"""Real bwrap bridge: no direct network, clean proxy traffic, DLP teardown (Q48/Q49)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from cheeseless.domain.egress_policy import EgressPolicy
from cheeseless.domain.status import Status
from tests.egress_support import Upstream, certificate
from tests.requirements import needs
from tests.toy import CANARY, make_repo

pytestmark = needs(os.access("/usr/bin/bwrap", os.X_OK), "needs bubblewrap")

PROBE = r"""
import json, os, socket, ssl, sys
result = {}
projection, port = sys.argv[1], int(sys.argv[2])

def probe(action):
    try:
        action()
        return "allowed"
    except OSError as exc:
        return type(exc).__name__

result["direct"] = probe(lambda: socket.create_connection(("1.1.1.1", 443), timeout=2))
result["direct_upstream"] = probe(lambda: socket.create_connection(("127.0.0.1", port), timeout=2))
def udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    sock.sendto(b"probe", ("8.8.8.8", 53))
result["udp"] = probe(udp)
result["codex_ca"] = os.path.isfile(os.environ.get("CODEX_CA_CERTIFICATE", ""))
result["node_ca"] = os.path.isfile(os.environ.get("NODE_EXTRA_CA_CERTS", ""))

def fetch(path):
    sock = socket.create_connection(("127.0.0.1", 18080), timeout=10)
    connect = "CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % (port, port)
    sock.sendall(connect.encode())
    assert sock.recv(4096).startswith(b"HTTP/1.1 200")
    context = ssl.create_default_context(cafile=os.environ["NODE_EXTRA_CA_CERTS"])
    tls = context.wrap_socket(sock, server_hostname="127.0.0.1")
    tls.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n" % path).encode())
    out = b""
    while True:
        chunk = tls.recv(65536)
        if not chunk:
            break
        out += chunk
    return out

result["clean"] = fetch("/clean").decode("latin-1")
with open(os.path.join(projection, "result.json"), "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""

LEAK = r"""
import os, socket, ssl, sys
port = int(sys.argv[2])
sock = socket.create_connection(("127.0.0.1", 18080), timeout=10)
sock.sendall(
    ("CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % (port, port)).encode()
)
sock.recv(4096)
context = ssl.create_default_context(cafile=os.environ["NODE_EXTRA_CA_CERTS"])
tls = context.wrap_socket(sock, server_hostname="127.0.0.1")
tls.sendall(b"GET /leak HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
while tls.recv(65536):
    pass
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("egress-bridge")
    repo = GitRepository.discover(make_repo(root / "toy"))
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    ca, cert, key = certificate(root)
    return type(
        "Env",
        (),
        {
            "repo": repo,
            "store": store,
            "state": root / "state",
            "task_id": store.active_task_id(),
            "ca": ca,
            "cert": cert,
            "key": key,
        },
    )()


def _wait(path: Path, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"probe did not finish: {path}")


def test_bwrap_bridge_blocks_direct_network_and_forwards_clean_traffic(env, tmp_path):
    upstream = Upstream(CANARY.encode()).start(env.cert, env.key, CANARY.encode())
    projection = tmp_path / "projection"
    try:
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=projection,
            socket_dir=tmp_path / "sockets",
            agent_command=["/usr/bin/python3", "-c", PROBE, str(projection), str(upstream.port)],
            egress_policy=EgressPolicy(frozenset({"127.0.0.1"})),
            egress_upstream_trusted_ca=env.ca,
        ):
            _wait(projection / "result.json")
    finally:
        upstream.close()
    data = json.loads((projection / "result.json").read_text())
    assert data["direct"] != "allowed"
    assert data["direct_upstream"] != "allowed"
    assert data["udp"] != "allowed"
    assert data["codex_ca"] is True
    assert data["node_ca"] is True
    assert "public clean body" in data["clean"]


def test_bwrap_dlp_hit_tears_down_and_persists_blocked(env, tmp_path, capsys):
    upstream = Upstream(CANARY.encode()).start(env.cert, env.key, CANARY.encode())
    projection = tmp_path / "projection"
    before_seed = env.store.load_task(env.task_id)[1]
    try:
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=projection,
            socket_dir=tmp_path / "sockets",
            agent_command=["/usr/bin/python3", "-c", LEAK, str(projection), str(upstream.port)],
            egress_policy=EgressPolicy(frozenset({"127.0.0.1"})),
            egress_upstream_trusted_ca=env.ca,
        ) as info:
            assert info["blocked"].wait(30), "DLP hit did not reach the supervisor"
            assert info["agent_proc"].poll() is not None
    finally:
        upstream.close()
    result = env.store.latest_result(env.task_id)
    assert result is not None and result.status is Status.BLOCKED
    after_seed = env.store.load_task(env.task_id)[1]
    assert after_seed != before_seed
    events = (env.store.root / "events.jsonl").read_text()
    assert "dlp_blocked" in events
    assert "host=127.0.0.1" in events
    assert CANARY not in events
    captured = capsys.readouterr()
    assert "egress blocked" in captured.err
    assert CANARY not in captured.err
