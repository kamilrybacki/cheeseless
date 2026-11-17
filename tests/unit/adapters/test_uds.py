import base64
import io
import os
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.uds import server as uds_server
from cheeseless.adapters.inbound.uds.client import UdsClientError, UdsVerifierClient
from cheeseless.adapters.inbound.uds.server import (
    SessionConfig,
    UdsVerifierServer,
    _peer_pidns_inode,
)
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.domain.budget import Pool
from tests.requirements import needs
from tests.support import bind_session, finish, start_in_pid_namespace, wait_for_socket
from tests.toy import make_repo

pytestmark = needs(os.path.exists("/usr/bin/unshare"), "needs unshare for a real PID namespace")

CLIENT_SCRIPT = r"""
import os, sys, time
ready, sock, task = sys.argv[1:4]
while not os.path.exists(ready):
    time.sleep(0.02)
from cheeseless.adapters.inbound.uds.client import UdsVerifierClient
client = UdsVerifierClient(sock, task_id=task)
view = client.run()
print("RUN", type(view.outcome).__name__)
status = client.status()
print("STATUS", type(status.outcome).__name__)
"""

RAW_SCRIPT = r"""
import base64, os, socket, sys, time
ready, sock, encoded = sys.argv[1:4]
while not os.path.exists(ready):
    time.sleep(0.02)
raw = base64.b64decode(encoded)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(10)
s.connect(sock)
s.sendall(raw)
data = b""
try:
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
except OSError:
    pass
s.close()
print(data.split(b"\r\n", 1)[0].decode("iso-8859-1"))
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("uds-fixture")
    repo = GitRepository.discover(make_repo(root / "toy"))
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    return SimpleNamespace(
        repo=repo,
        runner=runner,
        sandbox=sandbox,
        store=store,
        task_id=store.active_task_id(),
    )


def _session(env, worktree, **kwargs) -> SessionConfig:
    return SessionConfig(
        **bind_session(env.store, env.task_id),
        task_id=env.task_id,
        worktree=Path(worktree),
        profile="local",
        pool=Pool.LOCAL,
        timeout=120.0,
        tool_version="0.1.0",
        repo=env.repo,
        store=env.store,
        runner=env.runner,
        sandbox=env.sandbox,
        **kwargs,
    )


@contextmanager
def _running(env, sock: Path, *, expected_fd: int | None = None, **kwargs):
    session = _session(env, env.repo.root, expected_pidns_fd=expected_fd)
    server = UdsVerifierServer(sock, session, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_socket(sock)
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)
        assert not thread.is_alive()


def _socket_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uds"
    directory.mkdir(mode=0o700)
    return directory


def test_uds_round_trip_authenticated_client(env, tmp_path):
    sockdir = _socket_dir(tmp_path)
    sock = sockdir / "verifier.sock"
    ready = tmp_path / "ready"
    proc, _child_pid, ns_fd = start_in_pid_namespace(
        CLIENT_SCRIPT, str(ready), str(sock), env.task_id
    )
    try:
        with _running(env, sock, expected_fd=os.dup(ns_fd)):
            ready.write_text("go")
            out, err = finish(proc, timeout=120)
    finally:
        try:
            os.close(ns_fd)
        except OSError:
            pass
    assert "RUN Result" in out, (out, err)
    assert "STATUS Result" in out, (out, err)


def test_peer_probe_reports_the_host_namespace(env, tmp_path):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert _peer_pidns_inode(left) == os.stat("/proc/self/ns/pid").st_ino
    finally:
        left.close()
        right.close()


def test_authenticate_rejects_missing_host_and_unsupported(env, tmp_path, monkeypatch):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server = UdsVerifierServer(tmp_path / "unused.sock", _session(env, env.repo.root))
        assert server._authenticate(left) is False

        host_inode = os.stat("/proc/self/ns/pid").st_ino
        inode_only = _session(env, env.repo.root, expected_pidns_inode=host_inode)
        assert inode_only.pinned_inode() is None
        server = UdsVerifierServer(tmp_path / "unused2.sock", inode_only)
        assert server._authenticate(left) is False

        host_fd = os.open("/proc/self/ns/pid", os.O_RDONLY)
        try:
            server = UdsVerifierServer(
                tmp_path / "unused3.sock",
                _session(env, env.repo.root, expected_pidns_fd=host_fd),
            )
            assert server._authenticate(left) is False
        finally:
            os.close(host_fd)

        server = UdsVerifierServer(
            tmp_path / "unused4.sock",
            _session(env, env.repo.root, expected_pidns_inode=999_999_999),
        )
        assert server._authenticate(left) is False

        monkeypatch.setattr(uds_server, "_peer_pidns_inode", lambda _conn: None)
        assert server._authenticate(left) is False
    finally:
        left.close()
        right.close()


def test_server_rejects_public_socket_directory(env, tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    sock = directory / "verifier.sock"
    server = UdsVerifierServer(sock, _session(env, env.repo.root))
    with pytest.raises(RuntimeError, match="0700"):
        server.serve_forever()


def test_server_rejects_symlinked_socket_directory(env, tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    server = UdsVerifierServer(link / "verifier.sock", _session(env, env.repo.root))
    with pytest.raises(RuntimeError):
        server.serve_forever()


def test_server_rejects_symlinked_socket_path(env, tmp_path):
    directory = _socket_dir(tmp_path)
    target = directory / "other"
    target.write_text("x")
    (directory / "verifier.sock").symlink_to(target)
    server = UdsVerifierServer(directory / "verifier.sock", _session(env, env.repo.root))
    with pytest.raises(RuntimeError, match="symlink"):
        server.serve_forever()


def test_server_rejects_non_socket_path(env, tmp_path):
    directory = _socket_dir(tmp_path)
    (directory / "verifier.sock").write_text("not a socket")
    server = UdsVerifierServer(directory / "verifier.sock", _session(env, env.repo.root))
    with pytest.raises(RuntimeError, match="not a socket"):
        server.serve_forever()


def test_server_rejects_live_socket(env, tmp_path):
    directory = _socket_dir(tmp_path)
    sock_path = directory / "verifier.sock"
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    live.bind(str(sock_path))
    live.listen(1)
    try:
        server = UdsVerifierServer(sock_path, _session(env, env.repo.root))
        with pytest.raises(RuntimeError, match="live socket"):
            server.serve_forever()
    finally:
        live.close()


def test_server_replaces_stale_socket_only(env, tmp_path):
    directory = _socket_dir(tmp_path)
    sock_path = directory / "verifier.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    server = UdsVerifierServer(sock_path, _session(env, env.repo.root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_socket(sock_path)
    server.shutdown()
    thread.join(timeout=10)
    assert directory.exists()


def test_server_cleanup_only_removes_owned_resources(env, tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    server = UdsVerifierServer(
        owned / "verifier.sock", _session(env, env.repo.root), socket_dir_owned=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_socket(owned / "verifier.sock")
    server.shutdown()
    thread.join(timeout=10)
    assert not owned.exists()

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    (shared / "keep.txt").write_text("keep")
    server = UdsVerifierServer(shared / "verifier.sock", _session(env, env.repo.root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_socket(shared / "verifier.sock")
    server.shutdown()
    thread.join(timeout=10)
    assert (shared / "keep.txt").read_text() == "keep"
    assert shared.exists()


def _raw_request(path: str, body: bytes = b"{}", headers: bytes = b"") -> bytes:
    return (
        f"POST {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{headers.decode('iso-8859-1')}"
        "Connection: close\r\n\r\n"
    ).encode("iso-8859-1") + body


def _send_raw(env, tmp_path, raw: bytes) -> str:
    sockdir = _socket_dir(tmp_path)
    sock = sockdir / "verifier.sock"
    ready = tmp_path / "ready"
    script_proc, _child, ns_fd = start_in_pid_namespace(
        RAW_SCRIPT, str(ready), str(sock), base64.b64encode(raw).decode("ascii")
    )
    try:
        with _running(env, sock, expected_fd=os.dup(ns_fd)):
            ready.write_text("go")
            out, err = finish(script_proc, timeout=60)
    finally:
        try:
            os.close(ns_fd)
        except OSError:
            pass
    assert err == "", err
    return out.strip()


def test_server_rejects_oversized_headers(env, tmp_path):
    headers = b"".join(b"X-Pad-%d: 1\r\n" % i for i in range(2000))
    raw = _raw_request("/run", b"{}", headers)
    assert "431" in _send_raw(env, tmp_path, raw)


def test_server_rejects_oversized_body(env, tmp_path):
    raw = _raw_request("/run", b"{" + b" " * 70000 + b"}")
    assert "413" in _send_raw(env, tmp_path, raw)


def test_server_rejects_transfer_encoding(env, tmp_path):
    raw = (
        b"POST /run HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
        b"Content-Length: 2\r\n\r\n{}"
    )
    assert "400" in _send_raw(env, tmp_path, raw)


def test_server_rejects_duplicate_headers(env, tmp_path):
    raw = b"POST /run HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    assert "400" in _send_raw(env, tmp_path, raw)


def test_server_run_serialization_returns_429(env, tmp_path):
    server = UdsVerifierServer(
        _socket_dir(tmp_path) / "verifier.sock", _session(env, env.repo.root)
    )
    out = io.BytesIO()
    assert server._run_lock.acquire(blocking=False)
    try:
        server._run(out, b"{}")
    finally:
        server._run_lock.release()
    assert b"429" in out.getvalue()


def test_server_read_timeout(env, tmp_path, monkeypatch):
    monkeypatch.setattr(uds_server, "READ_TIMEOUT", 0.2)
    sockdir = _socket_dir(tmp_path)
    sock = sockdir / "verifier.sock"
    ready = tmp_path / "ready"
    script = r"""
import os, socket, sys, time
ready, sock = sys.argv[1:3]
while not os.path.exists(ready):
    time.sleep(0.02)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(sock)
s.sendall(b"POST /run HTTP/1.1\r\nHost: x\r\n")
time.sleep(0.5)
data = b""
try:
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
except OSError:
    pass
print(data.split(b"\r\n", 1)[0].decode("iso-8859-1"))
"""
    proc, _child, ns_fd = start_in_pid_namespace(script, str(ready), str(sock))
    try:
        with _running(env, sock, expected_fd=os.dup(ns_fd)):
            ready.write_text("go")
            out, err = finish(proc, timeout=30)
    finally:
        try:
            os.close(ns_fd)
        except OSError:
            pass
    assert "408" in out or "400" in out, (out, err)


def _fake_server(tmp_path: Path, response: bytes) -> Path:
    directory = _socket_dir(tmp_path)
    path = directory / "fake.sock"

    def serve() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        conn, _ = listener.accept()
        try:
            conn.recv(65536)
            conn.sendall(response)
        finally:
            conn.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    wait_for_socket(path)
    return path


@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 500 Error\r\nContent-Length: 2\r\n\r\n{}",
        b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}",
        b"garbage\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}extra",
    ],
)
def test_client_rejects_malformed_responses(tmp_path, response):
    path = _fake_server(tmp_path, response)
    client = UdsVerifierClient(path, timeout=2)
    with pytest.raises(UdsClientError):
        client.run()


def test_client_accepts_exact_response(tmp_path):
    response = b'HTTP/1.1 200 OK\r\nContent-Length: 14\r\n\r\n{"error":"no"}'
    path = _fake_server(tmp_path, response)
    client = UdsVerifierClient(path, timeout=2)
    view = client.run()
    assert view.outcome is not None


def test_client_rejects_oversized_response(tmp_path, monkeypatch):
    monkeypatch.setattr(uds_server, "MAX_BODY_BYTES", 65536)
    from cheeseless.adapters.inbound.uds import client as client_module

    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 10)
    response = b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"x":"1234"}'
    path = _fake_server(tmp_path, response)
    client = UdsVerifierClient(path, timeout=2)
    with pytest.raises(UdsClientError):
        client.run()


def test_server_does_not_expose_exception_text(env, tmp_path, monkeypatch):
    canary = "CHEESELESS-CANARY-LEAK"

    def boom(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(uds_server, "gate_run", boom)
    server = UdsVerifierServer(
        _socket_dir(tmp_path) / "verifier.sock", _session(env, env.repo.root)
    )
    out = io.BytesIO()
    server._run(out, b"{}")
    raw = out.getvalue()
    assert canary.encode() not in raw
    assert b"INFRA_ERROR" in raw or b"infra_error" in raw

    def boom(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(uds_server, "app_status", boom)
    out = io.BytesIO()
    server._status(out, b"{}")
    assert canary.encode() not in out.getvalue()
