"""Host-side candidate broker: closed schema, caps, teardown (ADR 0010)."""

import json
import os
import socket
import stat
import struct
import sys
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox.broker import (
    SOCKET_NAME,
    BrokerLimits,
    CandidateBroker,
)

# prefix + argv: the broker's argv is appended, so this prefix just executes it.
EXEC = ("/bin/sh", "-c", 'exec "$@"', "sh")


def _call(path: Path, request: object) -> dict:
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(30)
        s.connect(str(path))
        body = json.dumps(request).encode()
        s.sendall(struct.pack(">I", len(body)) + body)
        head = s.recv(4, socket.MSG_WAITALL)
        (size,) = struct.unpack(">I", head)
        return json.loads(s.recv(size, socket.MSG_WAITALL))


def _alive(marker: str) -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if marker in cmd:
            return True
    return False


@pytest.fixture
def broker():
    with CandidateBroker(EXEC, BrokerLimits(call_seconds=10, total_seconds=60)) as b:
        yield b


def test_the_socket_lives_in_a_private_directory(broker) -> None:
    directory = broker.directory
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / SOCKET_NAME).is_socket()
    assert [p.name for p in directory.iterdir()] == [SOCKET_NAME]


def test_a_call_returns_exit_code_and_output(broker) -> None:
    out = _call(broker.socket, {"argv": ["sh", "-c", "cat; echo err >&2; exit 3"], "stdin": "aGk="})
    assert out == {"returncode": 3, "stdout": "hi", "stderr": "err\n"}


@pytest.mark.parametrize(
    "field",
    ["path", "env", "cwd", "profile", "tree", "workdir", "scratch", "launcher", "extra"],
)
def test_any_field_outside_the_closed_schema_is_refused(broker, field, tmp_path) -> None:
    marker = tmp_path / "ran"
    request = {"argv": ["touch", str(marker)], field: "/etc" if field != "env" else {"A": "b"}}
    out = _call(broker.socket, request)
    assert "error" in out and field in out["error"]
    assert not marker.exists()


@pytest.mark.parametrize(
    "request_",
    [
        [],
        {"argv": "ls"},
        {"argv": [1]},
        {"argv": ["true"], "stdin": 5},
        {"argv": ["true"], "timeout": "x"},
        {"argv": ["true"], "timeout": True},
        {"argv": ["true"], "timeout": -1},
        {"argv": ["true"], "timeout": float("inf")},
        {"stdin": "x"},
    ],
)
def test_malformed_requests_are_refused(broker, request_) -> None:
    assert "error" in _call(broker.socket, request_)


def test_a_non_json_or_oversized_frame_is_refused(broker) -> None:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(broker.socket))
        s.sendall(struct.pack(">I", 1 << 30))
        (size,) = struct.unpack(">I", s.recv(4, socket.MSG_WAITALL))
        assert "error" in json.loads(s.recv(size, socket.MSG_WAITALL))
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(broker.socket))
        s.sendall(struct.pack(">I", 3) + b"{{{")
        (size,) = struct.unpack(">I", s.recv(4, socket.MSG_WAITALL))
        assert "error" in json.loads(s.recv(size, socket.MSG_WAITALL))


def test_the_deadline_is_host_enforced_and_capped(tmp_path) -> None:
    limits = BrokerLimits(call_seconds=0.5, total_seconds=60)
    with CandidateBroker(EXEC, limits) as b:
        start = time.monotonic()
        out = _call(b.socket, {"argv": ["sleep", "30"], "timeout": 600})
        assert time.monotonic() - start < 10
        assert out == {"error": "timed out"}


def test_the_total_call_count_is_capped() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=60, max_calls=2)
    with CandidateBroker(EXEC, limits) as b:
        assert _call(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert _call(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert "budget" in _call(b.socket, {"argv": ["true"]})["error"]


def test_the_total_time_is_capped() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=1.0)
    with CandidateBroker(EXEC, limits) as b:
        assert _call(b.socket, {"argv": ["sleep", "30"], "timeout": 30}) == {"error": "timed out"}
        assert "budget" in _call(b.socket, {"argv": ["true"]})["error"]


def test_concurrency_is_capped() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=60, concurrency=1)
    with CandidateBroker(EXEC, limits) as b:
        busy = socket.socket(socket.AF_UNIX)
        busy.connect(str(b.socket))
        body = json.dumps({"argv": ["sleep", "3"]}).encode()
        busy.sendall(struct.pack(">I", len(body)) + body)
        time.sleep(0.5)
        assert "busy" in _call(b.socket, {"argv": ["true"]})["error"]
        busy.close()


def test_a_client_that_disappears_mid_call_kills_the_call() -> None:
    marker = f"sleep {os.getpid()}.4242"
    with CandidateBroker(EXEC, BrokerLimits(call_seconds=60, total_seconds=120)) as b:
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(b.socket))
        body = json.dumps({"argv": ["sleep", f"{os.getpid()}.4242"]}).encode()
        s.sendall(struct.pack(">I", len(body)) + body)
        time.sleep(0.5)
        assert _alive(marker)
        s.close()
        deadline = time.monotonic() + 10
        while _alive(marker) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(marker)


def test_closing_the_broker_kills_in_flight_calls_and_removes_the_directory() -> None:
    marker = f"sleep {os.getpid()}.5151"
    b = CandidateBroker(EXEC, BrokerLimits(call_seconds=60, total_seconds=120))
    b.start()
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(b.socket))
    body = json.dumps({"argv": ["sleep", f"{os.getpid()}.5151"]}).encode()
    s.sendall(struct.pack(">I", len(body)) + body)
    time.sleep(0.5)
    assert _alive(marker)
    directory = b.directory
    b.close()
    assert not _alive(marker)
    assert not directory.exists()
    s.close()


def test_the_prefix_is_pinned_by_the_host(tmp_path) -> None:
    with CandidateBroker(
        (sys.executable, "-c", "import sys;print(sys.argv[1:])"), BrokerLimits()
    ) as b:
        out = _call(b.socket, {"argv": ["a", "b"]})
    assert out["stdout"] == "['a', 'b']\n"
