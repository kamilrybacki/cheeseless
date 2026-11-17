"""Host-side relay between a sandbox pty master and one local client (ADR 0014).

The relay owns the pty master. It listens on a unix socket inside a fresh 0700 directory
that the caller places outside every sandbox mount, accepts only peers whose ``SO_PEERCRED``
uid is the host uid, and serves one client at a time (a second one is closed at once).
The wire format is :mod:`cheeseless.domain.pane_frames`: data both ways, resize inbound.

With no client attached the relay still reads the master and discards the bytes, so the
child never blocks on a full pty buffer. Scrollback is not captured: a client sees output
from the moment it attaches. A client disconnect stops nothing; the relay ends when the
child side closes the pty or when the host calls :meth:`PaneRelay.close`.
"""

import contextlib
import errno
import fcntl
import logging
import os
import selectors
import shutil
import socket
import struct
import tempfile
import termios
import threading
from collections.abc import Callable
from pathlib import Path

from cheeseless.domain.pane_frames import (
    Data,
    FrameDecoder,
    FrameError,
    Resize,
    encode_data,
)

_LOG = logging.getLogger(__name__)
_READ_SIZE = 16 * 1024
_SEND_TIMEOUT = 2.0
_MAX_PENDING_INPUT = 1024 * 1024
_CRED = struct.Struct("3i")
_MASTER = "master"
_LISTENER = "listener"
_CLIENT = "client"
_WAKE = "wake"


def peer_uid(sock: socket.socket) -> int:
    """Return the uid of the process on the other end of a unix socket."""
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CRED.size)
    _pid, uid, _gid = _CRED.unpack(raw)
    return int(uid)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PaneRelay:
    """Serve one pty master to one local client over a private unix socket."""

    def __init__(
        self,
        master_fd: int,
        *,
        base_dir: Path | None = None,
        host_uid: int | None = None,
        peer_uid_of: Callable[[socket.socket], int] = peer_uid,
        on_attach: Callable[[], None] | None = None,
    ) -> None:
        self._master = master_fd
        self._base_dir = base_dir
        self._host_uid = os.getuid() if host_uid is None else host_uid
        self._peer_uid_of = peer_uid_of
        self._on_attach = on_attach
        self._attached = False
        self._dir: Path | None = None
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._decoder = FrameDecoder()
        self._pending = bytearray()
        self._sel = selectors.DefaultSelector()
        self._wake_r, self._wake_w = os.pipe()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._done = threading.Event()

    @property
    def socket_path(self) -> Path:
        if self._dir is None:
            raise RuntimeError("relay not started")
        return self._dir / "relay.sock"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def attached(self) -> bool:
        """True once an admitted client has connected (evidence only, not a guarantee)."""
        return self._attached

    def start(self) -> Path:
        self._dir = Path(tempfile.mkdtemp(prefix="cheeseless-pane-", dir=self._base_dir))
        os.chmod(self._dir, 0o700)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        listener.listen(1)
        listener.setblocking(False)
        self._listener = listener
        os.set_blocking(self._master, False)
        self._sel.register(self._master, selectors.EVENT_READ, _MASTER)
        self._sel.register(listener, selectors.EVENT_READ, _LISTENER)
        self._sel.register(self._wake_r, selectors.EVENT_READ, _WAKE)
        self._thread = threading.Thread(target=self._serve, name="pane-relay", daemon=True)
        self._thread.start()
        return self.socket_path

    def close(self) -> None:
        """Stop serving, close the master and the socket, and remove the socket dir."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError):
            os.write(self._wake_w, b"x")
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._teardown()

    def _serve(self) -> None:
        try:
            while self._dispatch_once():
                pass
        except Exception:
            _LOG.exception("pane relay stopped on an unexpected error")
        finally:
            self._drop_client()
            self._done.set()

    def _dispatch_once(self) -> bool:
        for key, _ in self._sel.select():
            if key.data == _WAKE:
                return False
            if key.data == _LISTENER:
                self._accept()
            elif key.data == _MASTER:
                if not self._pump_output():
                    return False
            elif key.data == _CLIENT:
                self._pump_input()
        return True

    def _accept(self) -> None:
        if self._listener is None:
            raise RuntimeError("relay listener is not open")
        try:
            conn, _ = self._listener.accept()
        except BlockingIOError:
            return
        if self._client is not None or not self._peer_allowed(conn):
            conn.close()
            return
        conn.settimeout(_SEND_TIMEOUT)
        self._client = conn
        self._decoder = FrameDecoder()
        self._sel.register(conn, selectors.EVENT_READ, _CLIENT)
        if not self._attached:
            self._attached = True
            if self._on_attach is not None:
                try:
                    self._on_attach()
                except Exception:  # noqa: BLE001 - evidence must never stop the relay
                    _LOG.warning("pane relay attach hook failed")

    def _peer_allowed(self, conn: socket.socket) -> bool:
        try:
            return self._peer_uid_of(conn) == self._host_uid
        except OSError:
            _LOG.warning("pane relay could not read peer credentials; refusing")
            return False

    def _pump_output(self) -> bool:
        try:
            chunk = os.read(self._master, _READ_SIZE)
        except BlockingIOError:
            return True
        except OSError as exc:
            if exc.errno == errno.EIO:
                return False
            raise
        if not chunk:
            return False
        if self._client is not None:
            try:
                self._client.sendall(encode_data(chunk))
            except OSError:
                self._drop_client()
        return True

    def _pump_input(self) -> None:
        if self._client is None:
            raise RuntimeError("no relay client attached")
        try:
            chunk = self._client.recv(_READ_SIZE)
            if not chunk:
                self._drop_client()
                return
            frames = self._decoder.feed(chunk)
        except FrameError:
            _LOG.warning("pane relay dropped a client that sent a malformed frame")
            self._drop_client()
            return
        except OSError:
            self._drop_client()
            return
        for frame in frames:
            if isinstance(frame, Resize):
                _set_winsize(self._master, frame.rows, frame.cols)
            elif isinstance(frame, Data):
                self._pending.extend(frame.payload)
        self._flush_pending()

    def _flush_pending(self) -> None:
        while self._pending:
            try:
                written = os.write(self._master, bytes(self._pending))
            except BlockingIOError:
                break
            del self._pending[:written]
        if len(self._pending) > _MAX_PENDING_INPUT:
            _LOG.warning("pane relay dropped a client whose input backed up")
            self._pending.clear()
            self._drop_client()

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        self._pending.clear()
        if client is None:
            return
        with contextlib.suppress(KeyError, ValueError):
            self._sel.unregister(client)
        client.close()

    def _teardown(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(KeyError, ValueError):
                self._sel.unregister(self._listener)
            self._listener.close()
        self._sel.close()
        for fd in (self._master, self._wake_r, self._wake_w):
            with contextlib.suppress(OSError):
                os.close(fd)
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
