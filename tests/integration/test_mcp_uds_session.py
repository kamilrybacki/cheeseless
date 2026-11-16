"""End-to-end: MCP stdio inside a PID namespace talks to the authenticated UDS."""

from __future__ import annotations

import json
import os
import select
import threading

import pytest

from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.domain.budget import Pool
from tests.requirements import needs
from tests.support import finish, start_in_pid_namespace, wait_for_socket
from tests.toy import CANARY, make_repo

pytestmark = needs(os.path.exists("/usr/bin/unshare"), "needs unshare for a real PID namespace")

MCP = (
    "import sys\n"
    "from cheeseless.bootstrap import serve_mcp\n"
    "raise SystemExit(serve_mcp(sys.argv[1], task_id=sys.argv[2]))\n"
)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp-session")
    repo = GitRepository.discover(make_repo(root / "toy"))
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, runner, sandbox), store)
    return type(
        "Env",
        (),
        {
            "repo": repo,
            "runner": runner,
            "sandbox": sandbox,
            "store": store,
            "task_id": store.active_task_id(),
        },
    )()


class _Client:
    def __init__(self, proc) -> None:  # noqa: ANN001 - test helper
        self.proc = proc
        self._next = 1

    def send(self, method: str, params: dict | None = None) -> int:
        message_id = self._next
        self._next += 1
        message = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        return message_id

    def notify(self, method: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def response(self, message_id: int, timeout: float = 120.0) -> dict:
        assert self.proc.stdout is not None
        while True:
            ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
            if not ready:
                raise AssertionError("MCP server did not answer")
            line = self.proc.stdout.readline()
            if not line:
                raise AssertionError(f"MCP server exited with {self.proc.poll()}")
            message = json.loads(line)
            if message.get("id") == message_id:
                return message


def test_mcp_over_authenticated_uds_in_namespace(env, tmp_path):
    sockdir = tmp_path / "sockets"
    sockdir.mkdir(mode=0o700)
    sock = sockdir / "verifier.sock"

    proc, _child_pid, ns_fd = start_in_pid_namespace(MCP, str(sock), env.task_id, interactive=True)
    session = SessionConfig(
        task_id=env.task_id,
        worktree=env.repo.root,
        profile="local",
        pool=Pool.LOCAL,
        timeout=120.0,
        tool_version="0.1.0",
        repo=env.repo,
        store=env.store,
        runner=env.runner,
        sandbox=env.sandbox,
        expected_pidns_fd=os.dup(ns_fd),
    )
    server = UdsVerifierServer(sock, session)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        wait_for_socket(sock)
        client = _Client(proc)
        initialized = client.response(
            client.send(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            )
        )
        assert initialized["result"]["protocolVersion"] == "2025-06-18"
        client.notify("notifications/initialized")

        tools = client.response(client.send("tools/list"))
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert {"gate_run", "gate_status"} <= names

        call = client.response(client.send("tools/call", {"name": "gate_run", "arguments": {}}))
    finally:
        proc.terminate()
        finish(proc, timeout=10)
        server.shutdown()
        thread.join(timeout=10)
        try:
            os.close(ns_fd)
        except OSError:
            pass

    payload = call["result"]
    structured = payload["structuredContent"]
    text = payload["content"][0]["text"]
    assert structured["outcome"]["kind"] == "result", structured
    assert text == json.dumps(structured, sort_keys=True)
    assert CANARY not in text
    assert not thread.is_alive()


@needs(os.access("/usr/bin/bwrap", os.X_OK), "needs bubblewrap for the real agent profile")
def test_mcp_stdio_inside_bwrap_host_session(env, tmp_path):
    from cheeseless.bootstrap import host_session

    projection = tmp_path / "projection"
    socket_dir = tmp_path / "sockets"
    with host_session(
        state_dir=env.store.root.parent.parent,
        cwd=env.repo.root,
        task_id=env.task_id,
        projection_dir=projection,
        socket_dir=socket_dir,
        # The staged shim has the socket and task baked in. Strip the whole environment
        # and point the old variables somewhere else: the shim must still authenticate
        # to the real per-session UDS (P11-11).
        agent_command=[
            "/bin/sh",
            "-c",
            "exec /usr/bin/env -i PATH=/usr/bin:/bin "
            "CHEESELESS_SOCKET=/nonexistent/attacker.sock "
            'CHEESELESS_TASK=attacker-task "$CHEESELESS_MCP"',
        ],
        agent_stdio=True,
    ) as info:
        client = _Client(info["agent_proc"])
        initialized = client.response(
            client.send(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "bwrap-test", "version": "0"},
                },
            )
        )
        assert initialized["result"]["protocolVersion"] == "2025-06-18"
        client.notify("notifications/initialized")
        tools = client.response(client.send("tools/list"))
        assert {tool["name"] for tool in tools["result"]["tools"]} == {
            "gate_run",
            "gate_status",
            "gate_dispute",
        }
        call = client.response(client.send("tools/call", {"name": "gate_run", "arguments": {}}))
        status = client.response(
            client.send("tools/call", {"name": "gate_status", "arguments": {}})
        )
    payload = call["result"]
    structured = payload["structuredContent"]
    assert structured["outcome"]["kind"] == "result", structured
    assert CANARY not in payload["content"][0]["text"]
    assert status["result"]["structuredContent"]["outcome"]["kind"] in ("result", "error")
