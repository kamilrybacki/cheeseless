"""Broker hardening from the round-6 review: admission, deadlines, budget, validation.

Behaviour under test: the host broker (ADR 0010) bounds work *before* reading from a
connection, enforces an absolute read deadline and a bounded write, reaps finished workers,
survives a failed worker start, reserves aggregate call time atomically, and answers every
malformed request with a structured refusal. Existing broker tests: test_candidate_broker.py.
"""

import base64
import json
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import broker as broker_module
from cheeseless.adapters.outbound.sandbox.broker import BrokerLimits, CandidateBroker

EXEC = ("/bin/sh", "-c", 'exec "$@"', "sh")


def _send(path: Path, raw: bytes, timeout: float = 30) -> dict:
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(struct.pack(">I", len(raw)) + raw)
        head = s.recv(4, socket.MSG_WAITALL)
        (size,) = struct.unpack(">I", head)
        return json.loads(s.recv(size, socket.MSG_WAITALL))


def _ask(path: Path, request: object) -> dict:
    return _send(path, json.dumps(request).encode())


def _settle(predicate, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _workers() -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith("cheeseless-broker-worker"))


def test_incomplete_connections_are_capped_before_any_worker_is_started() -> None:
    limits = BrokerLimits(call_seconds=5, total_seconds=60, connections=4, read_seconds=30)
    with CandidateBroker(EXEC, limits) as b:
        socks = []
        for _ in range(80):
            s = socket.socket(socket.AF_UNIX)
            s.connect(str(b.socket))
            s.sendall(b"\x00")
            socks.append(s)
        time.sleep(0.5)
        assert _workers() <= 4
        for s in socks:
            s.close()
        assert _settle(lambda: _workers() == 0)
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


def test_the_read_deadline_is_absolute_not_per_recv() -> None:
    limits = BrokerLimits(call_seconds=5, total_seconds=60, read_seconds=1.0)
    with CandidateBroker(EXEC, limits) as b:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect(str(b.socket))
        start = time.monotonic()
        # Drip one byte every 0.4 s: a per-recv timeout would never fire.
        try:
            while time.monotonic() - start < 8:
                s.sendall(b"\x00")
                time.sleep(0.4)
        except OSError:
            pass
        s.close()
        assert _settle(lambda: _workers() == 0, 4)
        assert time.monotonic() - start < 6


def test_finished_workers_are_reaped() -> None:
    with CandidateBroker(EXEC, BrokerLimits(call_seconds=5, total_seconds=60)) as b:
        for _ in range(15):
            assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert _settle(lambda: _workers() == 0)
        assert _settle(lambda: b.live_workers == 0)


def test_a_worker_that_fails_to_start_does_not_kill_the_acceptor(monkeypatch) -> None:
    real = threading.Thread
    failed = []

    class Flaky(real):  # type: ignore[misc, valid-type]
        def start(self) -> None:
            if (self.name or "").startswith("cheeseless-broker-worker") and not failed:
                failed.append(True)
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(broker_module.threading, "Thread", Flaky)
    with CandidateBroker(EXEC, BrokerLimits(call_seconds=5, total_seconds=60)) as b:
        first = socket.socket(socket.AF_UNIX)
        first.settimeout(5)
        first.connect(str(b.socket))
        first.sendall(struct.pack(">I", 2) + b"{}")
        first.close()
        assert _settle(lambda: bool(failed))
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


def test_a_client_that_never_reads_cannot_hold_a_worker() -> None:
    limits = BrokerLimits(call_seconds=5, total_seconds=60, write_seconds=1.0)
    with CandidateBroker(EXEC, limits) as b:
        s = socket.socket(socket.AF_UNIX)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.connect(str(b.socket))
        body = json.dumps({"argv": ["sh", "-c", "head -c 900000 /dev/zero"]}).encode()
        s.sendall(struct.pack(">I", len(body)) + body)
        assert _settle(lambda: _workers() == 0, 10)
        s.close()


def test_concurrent_calls_cannot_exceed_the_aggregate_budget() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=0.4, concurrency=4)
    request = {"argv": ["sleep", "30"], "timeout": 30}
    with CandidateBroker(EXEC, limits) as b:
        with ThreadPoolExecutor(4) as pool:
            replies = list(pool.map(lambda _: _ask(b.socket, request), range(4)))
        timed_out = [r for r in replies if r == {"error": "timed out"}]
        assert len(timed_out) == 1
        assert all("budget" in r["error"] for r in replies if r not in timed_out)
        assert b.spent_seconds <= 0.4 + 0.25


def test_unused_reserved_time_is_refunded() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=1.5)
    with CandidateBroker(EXEC, limits) as b:
        for _ in range(5):
            assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert b.spent_seconds < 1.0


def test_deep_json_nesting_is_refused() -> None:
    with CandidateBroker(EXEC, BrokerLimits()) as b:
        raw = b'{"argv":' + b"[" * 20000 + b"]" * 20000 + b"}"
        assert "error" in _send(b.socket, raw)
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


@pytest.mark.parametrize(
    "request_",
    [
        {"argv": ["a\0b"]},
        {"argv": ["\ud800"]},
        {"argv": ["x" * 150000]},
        {"argv": ["x"] * 5000},
        {"argv": ["true"], "stdin": "\ud800"},
        {"argv": ["true"], "stdin": "not base64!"},
        {"argv": ["true"], "stdin": "/w"},
        {"argv": ["true"], "stdin": "é"},
    ],
)
def test_invalid_values_get_a_structured_refusal(request_) -> None:
    with CandidateBroker(EXEC, BrokerLimits()) as b:
        out = _send(b.socket, json.dumps(request_).encode())
        assert "error" in out and "returncode" not in out
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


def test_stdin_is_a_lossless_base64_byte_stream() -> None:
    script = "od -An -tx1 | tr -d ' \\n'"
    with CandidateBroker(EXEC, BrokerLimits()) as b:
        for data in (b"\xff", b"\x00\xfe\x80", b"", b"plain"):
            encoded = base64.b64encode(data).decode()
            out = _ask(b.socket, {"argv": ["sh", "-c", script], "stdin": encoded})
            assert out["stdout"] == data.hex()


def test_a_null_stdin_is_dev_null() -> None:
    with CandidateBroker(EXEC, BrokerLimits()) as b:
        out = _ask(b.socket, {"argv": ["sh", "-c", "readlink /proc/self/fd/0"]})
        assert out["stdout"].strip() == "/dev/null"
        out = _ask(b.socket, {"argv": ["sh", "-c", "readlink /proc/self/fd/0"], "stdin": ""})
        assert out["stdout"].strip() != "/dev/null"
