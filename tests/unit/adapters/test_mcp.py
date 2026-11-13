import json
import os
import socket
import sys
import threading
from pathlib import Path

import anyio
from google.protobuf import json_format  # type: ignore[import-untyped]
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.proto import verifier_pb2
from cheeseless.adapters.inbound.uds.mapping import result_to_proto
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.result import CriterionResult, Result
from cheeseless.domain.status import Status
from tests.support import src_root, wait_for_socket


def _result() -> Result:
    return Result(
        task_id="a" * 64,
        tree_hash="a" * 40,
        baseline_digest="b" * 64,
        profile="local",
        tool_version="0.1.0",
        status=Status.PASS,
        criteria=(CriterionResult("criterion", Status.PASS),),
    )


class FakeClient:
    def __init__(self, view: AgentView) -> None:
        self.view = view

    def run(self) -> AgentView:
        return self.view

    def status(self) -> AgentView:
        return self.view


def test_mcp_tools_registered() -> None:
    server = build_mcp_server(FakeClient(AgentView(_result(), ())))
    names = [tool.name for tool in server._tool_manager.list_tools()]
    assert "gate_run" in names
    assert "gate_status" in names


def test_mcp_structured_matches_text() -> None:
    view = AgentView(_result(), ())
    server = build_mcp_server(FakeClient(view))
    for name in ("gate_run", "gate_status"):
        result: CallToolResult = server._tool_manager.get_tool(name).fn()
        assert result.structured_content == json.loads(result.content[0].text)
        assert result.content[0].text == json.dumps(result.structured_content, sort_keys=True)


class _FakeUds:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeUds":
        self._thread.start()
        wait_for_socket(self.socket_path)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        listener.listen(8)
        listener.settimeout(0.1)
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = listener.accept()
                except TimeoutError:
                    continue
                try:
                    self._answer(conn)
                finally:
                    conn.close()
        finally:
            listener.close()

    def _answer(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            key, _, value = line.partition(b":")
            if key.strip().lower() == b"content-length":
                length = int(value.strip())
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                return
            body += chunk
        path = head.split(b" ")[1]
        if path == b"/run":
            message = verifier_pb2.RunResponse()
            message.result.CopyFrom(result_to_proto(_result()))
        else:
            message = verifier_pb2.StatusResponse()
            message.result.CopyFrom(result_to_proto(_result()))
        payload = json_format.MessageToJson(message).encode("utf-8")
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + payload
        )


def test_mcp_stdio_from_non_git_directory_without_store(tmp_path: Path) -> None:
    sockdir = tmp_path / "sock"
    sockdir.mkdir(mode=0o700)
    sock = sockdir / "verifier.sock"
    non_git = tmp_path / "plain"
    non_git.mkdir()
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    code = (
        "import sys\n"
        "from cheeseless.bootstrap import serve_mcp\n"
        "raise SystemExit(serve_mcp(sys.argv[1], task_id=sys.argv[2]))\n"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", code, str(sock), "task"],
        cwd=non_git,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(src_root()),
            "HOME": str(home),
            "XDG_STATE_HOME": str(state),
            "LANG": "C.UTF-8",
        },
    )

    async def exercise() -> tuple[object, list[str], CallToolResult, CallToolResult]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                run_result = await session.call_tool("gate_run", {})
                status_result = await session.call_tool("gate_status", {})
                return initialized, [tool.name for tool in tools.tools], run_result, status_result

    with _FakeUds(sock):
        _initialized, names, run_result, status_result = anyio.run(exercise)

    assert not (non_git / ".git").exists()
    assert not state.exists(), "the MCP client must never touch the trusted store"
    assert {"gate_run", "gate_status"} <= set(names)
    for result in (run_result, status_result):
        assert result.structured_content is not None
        assert result.content[0].text == json.dumps(result.structured_content, sort_keys=True)
        assert result.structured_content["outcome"]["kind"] == "result"


class FailingClient:
    def run(self) -> AgentView:
        raise RuntimeError("CHEESELESS-CANARY-LEAK")

    def status(self) -> AgentView:
        raise RuntimeError("CHEESELESS-CANARY-LEAK")


def test_mcp_does_not_expose_exception_text() -> None:
    server = build_mcp_server(FailingClient())
    result: CallToolResult = server._tool_manager.get_tool("gate_run").fn()
    text = result.content[0].text
    assert "CHEESELESS-CANARY-LEAK" not in text
    assert "RuntimeError" not in text
    assert result.structured_content is not None
    assert result.structured_content["outcome"]["error"]["code"] == "infra_error"
