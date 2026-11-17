"""Shared helpers for adapter and session tests."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess  # noqa: S404 - tests start isolated helpers
import sys
import time
from pathlib import Path


def src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src"


def find_child_pid(parent_pid: int, timeout: float = 10.0) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.05)
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                parts = (entry / "stat").read_text().split()
            except OSError:
                continue
            if len(parts) < 4:
                continue
            if int(parts[3]) == parent_pid:
                return int(parts[0])
    return None


def start_in_pid_namespace(
    script: str,
    *args: str,
    env: dict[str, str] | None = None,
    interactive: bool = False,
) -> tuple[subprocess.Popen[str], int, int]:
    """Run ``script`` in a fresh PID namespace and pin that namespace's fd."""
    unshare = shutil.which("unshare")
    if unshare is None:
        raise RuntimeError("unshare is not installed")
    child_env = {**os.environ, **(env or {}), "PYTHONPATH": str(src_root())}
    proc = subprocess.Popen(  # noqa: S603 - fixed helper argv
        [
            unshare,
            "--user",
            "--pid",
            "--mount-proc",
            "--fork",
            sys.executable,
            "-c",
            script,
            *args,
        ],
        stdin=subprocess.PIPE if interactive else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if interactive else subprocess.PIPE,
        text=True,
        env=child_env,
    )
    child_pid = find_child_pid(proc.pid)
    if child_pid is None:
        proc.kill()
        out, err = proc.communicate(timeout=5)
        raise AssertionError(f"no child PID namespace process: {out!r} {err!r}")
    ns_fd = os.open(f"/proc/{child_pid}/ns/pid", os.O_RDONLY)
    return proc, child_pid, ns_fd


def finish(proc: subprocess.Popen[str], timeout: float = 30.0) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
    return out, err


def raw_post(socket_path: Path, path: str, body: bytes = b"{}", *, timeout: float = 5.0) -> bytes:
    import socket

    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(request)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def wait_for_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            time.sleep(0.02)
            continue
        if not stat.S_ISSOCK(st.st_mode):
            raise AssertionError(f"not a socket: {path}")
        return
    raise AssertionError(f"socket did not appear: {path}")


__all__ = [
    "finish",
    "find_child_pid",
    "raw_post",
    "src_root",
    "start_in_pid_namespace",
    "wait_for_socket",
]


def bind_session(store, task_id: str, role: str = "implementer") -> dict:  # noqa: ANN001
    """What the host passes to a ``SessionConfig`` after ``register_session``."""
    session_id, binding = store.register_session(task_id, role)
    return {"session_id": session_id, "generation": binding.generation}
