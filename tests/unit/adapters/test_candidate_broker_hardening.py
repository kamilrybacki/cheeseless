"""Broker hardening from the round-6 review: admission, deadlines, budget, validation.

Behaviour under test: the host broker (ADR 0010) bounds work *before* reading from a
connection, enforces an absolute read deadline and a bounded write, reaps finished workers,
survives a failed worker start, reserves aggregate call time atomically, and answers every
malformed request with a structured refusal. Existing broker tests: test_candidate_broker.py.
"""

import base64
import contextlib
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


def test_incomplete_connections_are_capped_before_any_worker_is_started() -> None:
    limits = BrokerLimits(call_seconds=5, total_seconds=60, connections=4, read_seconds=30)
    with CandidateBroker(EXEC, limits) as b:
        socks = []
        try:
            for _ in range(80):
                s = socket.socket(socket.AF_UNIX)
                socks.append(s)
                s.connect(str(b.socket))
                with contextlib.suppress(OSError):  # an excess connection is closed on us
                    s.sendall(b"\x00")
            time.sleep(0.5)
            assert b.live_workers <= 4
        finally:
            for s in socks:
                s.close()
        assert _settle(lambda: b.live_workers == 0)
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


def test_closing_the_broker_wakes_workers_blocked_on_a_read() -> None:
    limits = BrokerLimits(call_seconds=5, total_seconds=60, connections=4, read_seconds=60)
    b = CandidateBroker(EXEC, limits)
    b.start()
    socks = []
    try:
        for _ in range(3):
            s = socket.socket(socket.AF_UNIX)
            socks.append(s)
            s.connect(str(b.socket))
            s.sendall(b"\x00")
        assert _settle(lambda: b.live_workers == 3)
        start = time.monotonic()
        b.close()
        assert time.monotonic() - start < 3
        assert b.live_workers == 0
    finally:
        for s in socks:
            s.close()


def _timeout_reply(b: CandidateBroker, chunks: list[bytes], gap: float) -> tuple[dict, float]:
    """Drip ``chunks`` ``gap`` seconds apart and return the broker's reply and the elapsed."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(15)
        s.connect(str(b.socket))
        start = time.monotonic()
        for chunk in chunks:
            with contextlib.suppress(OSError):
                s.sendall(chunk)
            time.sleep(gap)
        head = s.recv(4, socket.MSG_WAITALL)
        (size,) = struct.unpack(">I", head)
        return json.loads(s.recv(size, socket.MSG_WAITALL)), time.monotonic() - start


DRIP_LIMITS = BrokerLimits(call_seconds=5, total_seconds=60, read_seconds=1.0)


def test_the_read_deadline_is_absolute_for_a_body_dripped_after_a_valid_header() -> None:
    header = struct.pack(">I", 64)  # a nonzero frame whose body never completes
    with CandidateBroker(EXEC, DRIP_LIMITS) as b:
        reply, elapsed = _timeout_reply(b, [header, *([b"{"] * 5)], 0.4)
        assert reply == {"error": "request read timed out"}
        # Each byte arrives inside any per-recv timeout; only an absolute deadline fires.
        assert 0.9 <= elapsed < 3.0
        assert _settle(lambda: b.live_workers == 0)


def test_the_read_deadline_is_absolute_for_a_partial_header() -> None:
    with CandidateBroker(EXEC, DRIP_LIMITS) as b:
        reply, elapsed = _timeout_reply(b, [b"\x00", b"\x00", b"\x00"], 0.45)
        assert reply == {"error": "request read timed out"}
        assert 0.9 <= elapsed < 3.0


def test_a_per_recv_timeout_would_fail_the_absolute_deadline_test(monkeypatch) -> None:
    """The drip tests above bite: a per-recv variant lets the same drip run to completion."""

    def per_recv(conn, size, deadline):  # the broken variant: the timeout restarts each recv
        data = b""
        while len(data) < size:
            conn.settimeout(1.0)
            try:
                chunk = conn.recv(size - len(data))
            except TimeoutError as exc:
                raise broker_module._Refused("request read timed out") from exc
            if not chunk:
                raise broker_module._Refused("incomplete request")
            data += chunk
        return data

    monkeypatch.setattr(broker_module, "_read_exact", per_recv)
    with CandidateBroker(EXEC, DRIP_LIMITS) as b:
        reply, elapsed = _timeout_reply(b, [struct.pack(">I", 64), *([b"{"] * 5)], 0.4)
        assert elapsed >= 2.0  # never cut off at the 1 s deadline
        assert reply != {"error": "request read timed out"} or elapsed >= 2.0


def test_finished_workers_are_reaped() -> None:
    with CandidateBroker(EXEC, BrokerLimits(call_seconds=5, total_seconds=60)) as b:
        for _ in range(15):
            assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert _settle(lambda: b.live_workers == 0)
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
        assert _settle(lambda: b.live_workers == 0, 10)
        s.close()


# A prefix that ignores SIGTERM (the disposition survives exec): only SIGKILL stops the call.
STUBBORN = ("/bin/sh", "-c", 'trap "" TERM; exec "$@"', "sh")
HANG = {"argv": ["sleep", "30"], "timeout": 0.1}


def test_concurrent_calls_cannot_exceed_the_aggregate_budget_even_when_sigterm_is_ignored() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=4.0, concurrency=4)
    with CandidateBroker(STUBBORN, limits) as b:
        with ThreadPoolExecutor(4) as pool:
            replies = list(pool.map(lambda _: _ask(b.socket, HANG), range(4)))
        timed_out = [r for r in replies if r == {"error": "timed out"}]
        assert len(timed_out) == 3  # 3 x (0.1 s + 1.0 s allowance) fit; the 4th does not
        assert all("budget" in r["error"] for r in replies if r not in timed_out)
        assert b.spent_seconds <= 4.0


def test_sequential_sigterm_ignoring_calls_are_charged_within_the_budget() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=4.0)
    with CandidateBroker(STUBBORN, limits) as b:
        replies = [_ask(b.socket, HANG) for _ in range(8)]
        assert replies[0] == {"error": "timed out"}
        assert "budget" in replies[-1]["error"]
        assert b.spent_seconds <= 4.0
        # Each call is bounded by its limit plus the enforced grace, not the 2 s of old.
        per_call = b.spent_seconds / sum(r == {"error": "timed out"} for r in replies)
        assert per_call < 0.1 + broker_module.ALLOWANCE


def test_unused_reserved_time_is_refunded() -> None:
    limits = BrokerLimits(call_seconds=10, total_seconds=3.0)
    with CandidateBroker(EXEC, limits) as b:
        for _ in range(5):
            assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0
        assert b.spent_seconds < 1.0


@pytest.mark.parametrize("timeout", [10**1000, -(10**1000), 1e999, float("nan"), 0, "5", [1]])
def test_an_out_of_range_timeout_gets_a_structured_refusal(timeout) -> None:
    with CandidateBroker(EXEC, BrokerLimits()) as b:
        raw = json.dumps({"argv": [], "timeout": timeout}).encode()
        out = _send(b.socket, raw)
        assert "timeout" in out["error"] and "returncode" not in out
        assert _ask(b.socket, {"argv": ["true"]})["returncode"] == 0


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
