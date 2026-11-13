"""The two UDS bridges around the host proxy (ADR 0007, R3).

* Host side: a private Unix socket is bind-mounted into the sandbox. mitmproxy
  itself only listens on host loopback TCP, so :class:`UnixToTcpBridge` accepts on
  the UDS and relays to that TCP port.
* Sandbox side: :func:`serve_tcp_to_uds` exposes the bind-mounted UDS as
  ``127.0.0.1:<port>`` inside the otherwise empty network namespace, so ordinary
  HTTP clients can use ``HTTPS_PROXY``. It never opens an outbound internet socket.

Neither bridge parses or logs traffic.
"""

from __future__ import annotations

import os
import socket
import socketserver
import threading
from pathlib import Path

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18080
EGRESS_SOCKET_ENV = "CHEESELESS_EGRESS_SOCKET"
EGRESS_PORT_ENV = "CHEESELESS_EGRESS_PORT"
EGRESS_READY_ENV = "CHEESELESS_EGRESS_READY"
RELAY_CHUNK = 65536


def _relay(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(RELAY_CHUNK)
            if not chunk:
                break
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _pump(left: socket.socket, right: socket.socket) -> None:
    forward = threading.Thread(target=_relay, args=(left, right), daemon=True)
    forward.start()
    _relay(right, left)
    forward.join(timeout=5)
    for sock in (left, right):
        try:
            sock.close()
        except OSError:
            pass


def serve_tcp_to_uds(
    uds_path: str | Path, host: str = BRIDGE_HOST, port: int = BRIDGE_PORT
) -> None:
    """Run inside the sandbox: loopback TCP in, bind-mounted UDS out."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(64)
    ready = os.environ.get(EGRESS_READY_ENV)
    if ready:
        try:
            with open(ready, "w", encoding="ascii") as handle:
                handle.write("ready\n")
        except OSError:
            pass
    try:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                upstream.connect(str(uds_path))
            except OSError:
                client.close()
                continue
            threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    finally:
        listener.close()


def client_main() -> int:
    """Entry point for the in-sandbox forwarder (stdlib only, no host imports)."""
    uds_path = os.environ.get(EGRESS_SOCKET_ENV)
    port_text = os.environ.get(EGRESS_PORT_ENV, str(BRIDGE_PORT))
    if not uds_path:
        return 2
    try:
        port = int(port_text)
    except ValueError:
        return 2
    if not 1 <= port <= 65535:
        return 2
    serve_tcp_to_uds(uds_path, BRIDGE_HOST, port)
    return 0


class _UnixHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:  # pragma: no cover - exercised through the bridge
        target = self.server.target  # type: ignore[attr-defined]
        upstream = socket.create_connection(target, timeout=10)
        upstream.settimeout(None)
        _pump(self.request, upstream)


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 64

    def __init__(self, path: str, target: tuple[str, int]) -> None:
        self.target = target
        super().__init__(path, _UnixHandler)


class UnixToTcpBridge:
    """Host side of the egress socket: Unix in, loopback TCP out."""

    def __init__(self, socket_path: str | Path, target: tuple[str, int]) -> None:
        self.socket_path = Path(socket_path)
        self._target = target
        self._server: _UnixServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("bridge already started")
        path = self.socket_path
        if not path.parent.is_dir():
            raise RuntimeError("bridge socket directory does not exist")
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self._server = _UnixServer(str(path), self._target)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass


__all__ = [
    "BRIDGE_HOST",
    "BRIDGE_PORT",
    "EGRESS_PORT_ENV",
    "EGRESS_READY_ENV",
    "EGRESS_SOCKET_ENV",
    "UnixToTcpBridge",
    "client_main",
    "serve_tcp_to_uds",
]
