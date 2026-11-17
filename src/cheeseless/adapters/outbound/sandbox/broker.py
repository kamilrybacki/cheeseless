"""Host-side candidate broker for the author runner (ADR 0010).

The unapproved acceptance suite runs in the author sandbox, which has no candidate tree and
no way to start one. To call the candidate it speaks a closed request protocol to this
broker over a Unix socket that only that sandbox can reach. The broker runs every call
through the host-pinned *prefix* (the normal candidate launcher plus the contract run
argv), so an author call and a normal call share one code path. The client supplies only
extra ``argv`` entries, ``stdin`` and a ``timeout``: never a path, env, cwd or profile.
"""

from __future__ import annotations

import base64
import binascii
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

_Sock = socket.socket
SOCKET_NAME = "broker.sock"
MAX_FRAME = 1 << 20
MAX_OUTPUT = 1 << 20
KILL_GRACE = 2.0
MAX_DEPTH = 8
MAX_ARGS = 1024
MAX_ARG_BYTES = 1 << 16
MAX_ARGV_BYTES = 1 << 17
ALLOWED_FIELDS = frozenset({"argv", "stdin", "timeout"})


@dataclass(frozen=True, slots=True)
class BrokerLimits:
    call_seconds: float = 30.0
    total_seconds: float = 300.0
    max_calls: int = 1000
    concurrency: int = 4
    connections: int = 8
    read_seconds: float = 10.0
    write_seconds: float = 10.0


class _Refused(Exception):
    pass


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode()
    return struct.pack(">I", len(body)) + body


def _read_exact(conn: _Sock, size: int, deadline: float) -> bytes:
    """Read exactly ``size`` bytes before the absolute ``deadline`` (a monotonic time)."""
    data = b""
    while len(data) < size:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Refused("request read timed out")
        conn.settimeout(left)
        try:
            chunk = conn.recv(size - len(data))
        except TimeoutError as exc:
            raise _Refused("request read timed out") from exc
        if not chunk:
            raise _Refused("incomplete request")
        data += chunk
    return data


def _too_deep(raw: bytes) -> bool:
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in b"[{":
            depth += 1
            if depth > MAX_DEPTH:
                return True
        elif byte in b"]}":
            depth -= 1
    return False


def _valid_argv(argv: object) -> list[str]:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise _Refused("argv must be a list of strings")
    if len(argv) > MAX_ARGS:
        raise _Refused("argv has too many entries")
    total = 0
    for arg in argv:
        try:
            size = len(arg.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise _Refused("argv entries must be valid UTF-8 text") from exc
        if "\0" in arg:
            raise _Refused("argv entries must not contain NUL")
        if size > MAX_ARG_BYTES:
            raise _Refused("an argv entry is too large")
        total += size + 1
    if total > MAX_ARGV_BYTES:
        raise _Refused("argv is too large")
    return list(argv)


def _valid_stdin(stdin: object) -> bytes | None:
    """Strict base64 (canonical, padded, ASCII) or ``None`` for /dev/null."""
    if stdin is None:
        return None
    if not isinstance(stdin, str):
        raise _Refused("stdin must be a base64 string")
    try:
        encoded = stdin.encode("ascii")
        data = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise _Refused("stdin must be strictly valid base64") from exc
    if base64.b64encode(data) != encoded:
        raise _Refused("stdin must be canonical base64")
    return data


def _parse(raw: bytes) -> tuple[list[str], bytes | None, float | None]:
    if _too_deep(raw):
        raise _Refused("request is nested too deeply")
    try:
        request = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise _Refused("request is not valid JSON") from exc
    if not isinstance(request, dict):
        raise _Refused("request must be an object")
    extra = sorted(set(request) - ALLOWED_FIELDS)
    if extra:
        raise _Refused(f"unknown request field: {', '.join(map(str, extra))}")
    argv = _valid_argv(request.get("argv"))
    stdin = _valid_stdin(request.get("stdin"))
    timeout = request.get("timeout")
    if timeout is not None:
        bad = isinstance(timeout, bool) or not isinstance(timeout, int | float)
        if bad or not math.isfinite(timeout) or timeout <= 0:
            raise _Refused("timeout must be a positive finite number")
    return argv, stdin, None if timeout is None else float(timeout)


def _client_gone(conn: _Sock) -> bool:
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
        self._connections = threading.Semaphore(self._limits.connections)
        self._stop = threading.Event()
        self._procs: set[subprocess.Popen[bytes]] = set()
        self._acceptor: threading.Thread | None = None
        self._workers: set[threading.Thread] = set()
        self._calls = 0
        self._spent = 0.0
        self._reserved = 0.0

    @property
    def directory(self) -> Path:
        if self._directory is None:
            raise RuntimeError("broker not started")
        return self._directory

    @property
    def live_workers(self) -> int:
        with self._lock:
            return len(self._workers)

    @property
    def spent_seconds(self) -> float:
        with self._lock:
            return self._spent

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
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

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
        with self._lock:
            threads = [*self._workers, *([self._acceptor] if self._acceptor else [])]
        for thread in threads:
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
            self._dispatch(conn)

    def _dispatch(self, conn: _Sock) -> None:
        """Admit a connection only while a worker slot is free; never read before that."""
        if not self._connections.acquire(blocking=False):
            conn.close()
            return
        worker = threading.Thread(
            target=self._work, args=(conn,), name="cheeseless-broker-worker", daemon=True
        )
        with self._lock:
            self._workers.add(worker)
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                self._workers.discard(worker)
            self._connections.release()
            conn.close()

    def _work(self, conn: _Sock) -> None:
        try:
            self._serve(conn)
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())
            self._connections.release()

    def _serve(self, conn: _Sock) -> None:
        with conn:
            try:
                deadline = time.monotonic() + self._limits.read_seconds
                (size,) = struct.unpack(">I", _read_exact(conn, 4, deadline))
                if size > MAX_FRAME:
                    raise _Refused("request too large")
                argv, stdin, timeout = _parse(_read_exact(conn, size, deadline))
                conn.settimeout(None)
                reply = self._call(conn, argv, stdin, timeout)
            except (_Refused, OSError) as exc:
                reply = {"error": str(exc) or "refused"}
            conn.settimeout(self._limits.write_seconds)
            with contextlib.suppress(OSError):
                conn.sendall(_frame(reply))

    def _admit(self, timeout: float | None) -> float:
        """Reserve a call and its time atomically; return the seconds it may run."""
        with self._lock:
            remaining = self._limits.total_seconds - self._spent - self._reserved
            if self._calls >= self._limits.max_calls or remaining <= 0:
                raise _Refused("candidate call budget exhausted")
            limit = min(timeout or self._limits.call_seconds, self._limits.call_seconds, remaining)
            self._calls += 1
            self._reserved += limit
            return limit

    def _call(
        self, conn: _Sock, argv: list[str], stdin: bytes | None, timeout: float | None
    ) -> dict[str, Any]:
        if not self._slots.acquire(blocking=False):
            raise _Refused("broker busy: too many concurrent candidate calls")
        try:
            limit = self._admit(timeout)
            started = time.monotonic()
            try:
                return self._run(conn, argv, stdin, limit, started)
            finally:
                elapsed = time.monotonic() - started
                with self._lock:
                    self._reserved -= limit
                    self._spent += elapsed
        finally:
            self._slots.release()

    def _run(
        self, conn: _Sock, argv: list[str], stdin: bytes | None, limit: float, started: float
    ) -> dict[str, Any]:
        with (
            tempfile.TemporaryFile() as fin,
            tempfile.TemporaryFile() as fout,
            tempfile.TemporaryFile() as ferr,
        ):
            if stdin is not None:
                fin.write(stdin)
                fin.seek(0)
            proc = subprocess.Popen(  # noqa: S603
                [*self._prefix, *argv],
                stdin=subprocess.DEVNULL if stdin is None else fin,
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
