"""`cheeseless pane-relay`: terminal client for the host pane relay (ADR 0014).

Runs on the host inside the tmux pane. It puts the local tty in raw mode, pumps stdin to
the relay and relay output to stdout, sends the pane size at start and on every
``SIGWINCH``, and always restores the tty. It exits when the relay closes the socket. It
holds no secrets and never touches the sandbox; the socket path is the only input.

A *viewer* (``view_only``, ADR 0019) is the client of a view-only pane. It sends no frame at
all: no initial size, no resize, no stdin. It leaves the tty cooked, so Ctrl-C ends the
viewer and the session behind the pane keeps running. The relay enforces the same rule from
its side; this is the polite half.
"""

import contextlib
import fcntl
import os
import selectors
import signal
import socket
import struct
import sys
import termios
import threading
import time
import tty
from collections.abc import Callable
from pathlib import Path

from cheeseless.domain.pane_frames import Data, FrameDecoder, FrameError, encode_data, encode_resize

_READ_SIZE = 16 * 1024
_TERMINATING = (signal.SIGTERM, signal.SIGHUP)

EXIT_NO_SOCKET = 3
"""`pane-relay --wait` gave up: the socket never appeared (the host has not started yet)."""
NO_SOCKET_MESSAGE = "pane-relay: the relay socket did not appear in time"
_WAIT_INTERVAL = 0.1


def _socket_present(path: Path) -> bool:
    """True once ``path`` exists. Any error other than "absent" counts as present.

    A path we cannot stat (no permission, say) is left to ``connect``, which reports the
    real error instead of a misleading timeout.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def wait_for_socket(
    path: Path,
    seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = _WAIT_INTERVAL,
) -> bool:
    """Poll until ``path`` exists or ``seconds`` pass; True if it appeared.

    It checks first, so an existing socket costs no sleep, and checks once more at the
    deadline. It only waits for the path to exist: a socket that exists but refuses the
    connection (the host is between ``bind`` and ``listen``, or it is stale) is left to
    ``connect``, which exits 1.
    """
    deadline = clock() + seconds
    while True:
        if _socket_present(path):
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(interval, remaining))


def write_all(fd: int, data: bytes) -> None:
    """Write every byte of ``data``; ``os.write`` may write only part of it."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _exit_on_signal(signo: int, _frame: object) -> None:
    # Raising unwinds through run_pane_client's finally, which restores the tty. The
    # default action would kill the process with the pane still in raw mode.
    raise SystemExit(128 + signo)


def install_exit_handlers() -> Callable[[], None]:
    """Turn SIGTERM/SIGHUP into SystemExit; return a function that undoes it.

    Signal handlers can only be set on the main thread; elsewhere this is a no-op.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = {signo: signal.signal(signo, _exit_on_signal) for signo in _TERMINATING}

    def restore() -> None:
        for signo, handler in previous.items():
            signal.signal(signo, handler)

    return restore


def install_winch_pipe() -> tuple[int, Callable[[], None]]:
    """Route SIGWINCH to a pipe; return its read end and a function that undoes it."""
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)

    def handler(_signo: int, _frame: object) -> None:
        with contextlib.suppress(OSError):
            os.write(write_fd, b"w")

    previous = signal.signal(signal.SIGWINCH, handler)

    def restore() -> None:
        signal.signal(signal.SIGWINCH, previous)
        os.close(read_fd)
        os.close(write_fd)

    return read_fd, restore


def _window_size(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
    except OSError:
        sock.close()
        raise
    return sock


def _view(sock: socket.socket, stdout_fd: int) -> int:
    """Copy relay output to stdout and send nothing, ever."""
    decoder = FrameDecoder()
    while True:
        chunk = sock.recv(_READ_SIZE)
        if not chunk:
            return 0
        for frame in decoder.feed(chunk):
            if isinstance(frame, Data):
                write_all(stdout_fd, frame.payload)


def _pump(sock: socket.socket, stdin_fd: int, stdout_fd: int, resize_fd: int) -> int:
    sel = selectors.DefaultSelector()
    for fd in (stdin_fd, sock, resize_fd):
        sel.register(fd, selectors.EVENT_READ)
    decoder = FrameDecoder()
    sock.sendall(encode_resize(*_window_size(stdin_fd)))
    while True:
        for key, _ in sel.select():
            if key.fileobj == resize_fd:
                os.read(resize_fd, 64)
                sock.sendall(encode_resize(*_window_size(stdin_fd)))
            elif key.fileobj == stdin_fd:
                data = os.read(stdin_fd, _READ_SIZE)
                if data:
                    sock.sendall(encode_data(data))
            else:
                chunk = sock.recv(_READ_SIZE)
                if not chunk:
                    return 0
                for frame in decoder.feed(chunk):
                    if isinstance(frame, Data):
                        write_all(stdout_fd, frame.payload)


def run_pane_client(
    socket_path: Path,
    *,
    stdin_fd: int | None = None,
    stdout_fd: int | None = None,
    resize_fd: int | None = None,
    view_only: bool = False,
) -> int:
    """Attach the local terminal to the relay. Returns 0 on a clean close, 1 on error."""
    stdin = sys.stdin.fileno() if stdin_fd is None else stdin_fd
    stdout = sys.stdout.fileno() if stdout_fd is None else stdout_fd
    try:
        sock = _connect(socket_path)
    except OSError as exc:
        print(f"pane-relay: cannot connect to the relay: {exc}", file=sys.stderr)
        return 1
    if view_only:
        return _run_viewer(sock, stdout)
    restore_winch: Callable[[], None] | None = None
    if resize_fd is None:
        resize_fd, restore_winch = install_winch_pipe()
    saved = termios.tcgetattr(stdin)
    restore_exit = install_exit_handlers()
    try:
        tty.setraw(stdin)
        return _pump(sock, stdin, stdout, resize_fd)
    except (OSError, FrameError) as exc:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        print(f"pane-relay: {exc}", file=sys.stderr)
        return 1
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        restore_exit()
        sock.close()
        if restore_winch is not None:
            restore_winch()


def _run_viewer(sock: socket.socket, stdout_fd: int) -> int:
    restore_exit = install_exit_handlers()
    try:
        return _view(sock, stdout_fd)
    except (OSError, FrameError) as exc:
        print(f"pane-relay: {exc}", file=sys.stderr)
        return 1
    finally:
        restore_exit()
        sock.close()
