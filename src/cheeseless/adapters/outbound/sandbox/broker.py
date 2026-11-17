"""Host-side candidate broker for the author runner (ADR 0010).

The unapproved acceptance suite runs in the author sandbox, which has no candidate tree and
no way to start one. To call the candidate it speaks a closed request protocol to this
broker over a Unix socket that only that sandbox can reach. The broker runs every call
through the host-pinned *prefix* (the normal candidate launcher plus the contract run
argv), so an author call and a normal call share one code path. The client supplies only
extra ``argv`` entries, ``stdin`` and a ``timeout``: never a path, env, cwd or profile.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

SOCKET_NAME = "broker.sock"
MAX_FRAME = 1 << 20
MAX_OUTPUT = 1 << 20
KILL_GRACE = 2.0
READ_TIMEOUT = 10.0
ALLOWED_FIELDS = frozenset({"argv", "stdin", "timeout"})


@dataclass(frozen=True, slots=True)
class BrokerLimits:
    call_seconds: float = 30.0
    total_seconds: float = 300.0
    max_calls: int = 1000
    concurrency: int = 4


class _Refused(Exception):
    pass


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode()
    return struct.pack(">I", len(body)) + body


def _read_exact(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise _Refused("incomplete request")
        data += chunk
    return data


def _parse(raw: bytes) -> tuple[list[str], str | None, float | None]:
    try:
        request = json.loads(raw)
    except ValueError as exc:
        raise _Refused("request is not valid JSON") from exc
    if not isinstance(request, dict):
        raise _Refused("request must be an object")
    extra = sorted(set(request) - ALLOWED_FIELDS)
    if extra:
        raise _Refused(f"unknown request field: {', '.join(map(str, extra))}")
    argv = request.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise _Refused("argv must be a list of strings")
    stdin = request.get("stdin")
    if stdin is not None and not isinstance(stdin, str):
        raise _Refused("stdin must be a string")
    timeout = request.get("timeout")
    if timeout is not None:
        bad = isinstance(timeout, bool) or not isinstance(timeout, int | float)
        if bad or not math.isfinite(timeout) or timeout <= 0:
            raise _Refused("timeout must be a positive finite number")
    return argv, stdin, None if timeout is None else float(timeout)


def _client_gone(conn: socket.socket) -> bool:
    ready, _, _ = select.select([conn], [], [], 0)
    if not ready:
        return False
    try:
        return conn.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)
    proc.wait()


def _read_capped(handle: IO[bytes]) -> str:
    handle.seek(0)
    return handle.read(MAX_OUTPUT).decode("utf-8", "replace")


class CandidateBroker:
    """One broker per author-sandbox session; every call is a fresh launcher process."""

    def __init__(self, prefix: Sequence[str], limits: BrokerLimits | None = None) -> None:
        self._prefix = tuple(prefix)
        self._limits = limits or BrokerLimits()
        self._directory: Path | None = None
        self._listener: socket.socket | None = None
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(self._limits.concurrency)
        self._stop = threading.Event()
        self._procs: set[subprocess.Popen[bytes]] = set()
        self._threads: list[threading.Thread] = []
        self._calls = 0
        self._spent = 0.0

    @property
    def directory(self) -> Path:
        if self._directory is None:
            raise RuntimeError("broker not started")
        return self._directory

    @property
    def socket(self) -> Path:
        return self.directory / SOCKET_NAME

    def start(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="cheeseless-broker-"))
        os.chmod(directory, 0o700)
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(directory / SOCKET_NAME))
        os.chmod(directory / SOCKET_NAME, 0o600)
        listener.listen(16)
        listener.settimeout(0.1)
        self._directory, self._listener = directory, listener
        acceptor = threading.Thread(target=self._accept, daemon=True)
        self._threads.append(acceptor)
        acceptor.start()

    def __enter__(self) -> CandidateBroker:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _signal_group(proc, signal.SIGKILL)
        for thread in list(self._threads):
            thread.join(timeout=KILL_GRACE * 3)
        if self._listener is not None:
            self._listener.close()
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    def _accept(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            worker = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            with self._lock:
                self._threads.append(worker)
            worker.start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(READ_TIMEOUT)
                (size,) = struct.unpack(">I", _read_exact(conn, 4))
                if size > MAX_FRAME:
                    raise _Refused("request too large")
                argv, stdin, timeout = _parse(_read_exact(conn, size))
                conn.settimeout(None)
                reply = self._call(conn, argv, stdin, timeout)
            except (_Refused, OSError) as exc:
                reply = {"error": str(exc) or "refused"}
            with contextlib.suppress(OSError):
                conn.sendall(_frame(reply))

    def _admit(self) -> float:
        """Reserve a call; return the seconds left in the total budget."""
        with self._lock:
            remaining = self._limits.total_seconds - self._spent
            if self._calls >= self._limits.max_calls or remaining <= 0:
                raise _Refused("candidate call budget exhausted")
            self._calls += 1
            return remaining

    def _call(
        self, conn: socket.socket, argv: list[str], stdin: str | None, timeout: float | None
    ) -> dict[str, Any]:
        if not self._slots.acquire(blocking=False):
            raise _Refused("broker busy: too many concurrent candidate calls")
        try:
            remaining = self._admit()
            limit = min(timeout or self._limits.call_seconds, self._limits.call_seconds, remaining)
            started = time.monotonic()
            try:
                return self._run(conn, argv, stdin, limit, started)
            finally:
                with self._lock:
                    self._spent += time.monotonic() - started
        finally:
            self._slots.release()

    def _run(
        self, conn: socket.socket, argv: list[str], stdin: str | None, limit: float, started: float
    ) -> dict[str, Any]:
        with (
            tempfile.TemporaryFile() as fin,
            tempfile.TemporaryFile() as fout,
            tempfile.TemporaryFile() as ferr,
        ):
            fin.write((stdin or "").encode())
            fin.seek(0)
            proc = subprocess.Popen(  # noqa: S603
                [*self._prefix, *argv],
                stdin=fin,
                stdout=fout,
                stderr=ferr,
                start_new_session=True,
            )
            with self._lock:
                self._procs.add(proc)
            try:
                while proc.poll() is None:
                    if self._stop.is_set() or _client_gone(conn):
                        _kill_tree(proc)
                        return {"error": "call cancelled"}
                    if time.monotonic() - started >= limit:
                        _kill_tree(proc)
                        return {"error": "timed out"}
                    time.sleep(0.02)
                _signal_group(proc, signal.SIGKILL)
                return {
                    "returncode": proc.returncode,
                    "stdout": _read_capped(fout),
                    "stderr": _read_capped(ferr),
                }
            finally:
                _signal_group(proc, signal.SIGKILL)
                with self._lock:
                    self._procs.discard(proc)


__all__ = ["SOCKET_NAME", "BrokerLimits", "CandidateBroker"]
