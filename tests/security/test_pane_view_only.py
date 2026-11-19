"""ADR 0019: a view-only role relay reads no client frame into the pty, on any connection.

The coder and reference panes are view-only. The relay may *read* bytes from the client
socket (it has to notice a close), but a ``Data`` or ``Resize`` frame must reach no pty:
not on the first connection, not after a reconnect. The frame closes that connection only;
the session keeps running, and closing the pane stops nothing. The host alone sets the
pty size. The canary is a marker the "client" types: it must never reach the slave.
"""

import fcntl
import os
import select
import socket
import struct
import termios
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.pane.relay import PaneRelay
from cheeseless.domain.pane_frames import Data, FrameDecoder, encode_data, encode_resize

CANARY = b"CANARY-TYPED-INTO-A-VIEW-ONLY-PANE"
HOST_SIZE = (24, 80)


def _set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _size(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


@pytest.fixture
def view_relay(tmp_path: Path) -> Iterator[tuple[PaneRelay, int]]:
    master, slave = os.openpty()
    _set_size(master, *HOST_SIZE)
    # Raw slave: no echo and no line discipline, so anything written to the master is
    # visible on the slave exactly as sent.
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    relay = PaneRelay(master, base_dir=tmp_path, view_only=True)
    relay.start()
    yield relay, slave
    relay.close()
    try:
        os.close(slave)
    except OSError:
        pass


def _connect(relay: PaneRelay) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    sock.connect(str(relay.socket_path))
    return sock


def _until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def _slave_bytes(slave: int, wait: float = 0.3) -> bytes:
    got = b""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        ready, _, _ = select.select([slave], [], [], 0.05)
        if ready:
            got += os.read(slave, 4096)
    return got


def _closed_by_relay(sock: socket.socket) -> bool:
    try:
        return sock.recv(4096) == b""
    except (ConnectionResetError, BrokenPipeError):
        return True
    except TimeoutError:
        return False


def test_a_data_frame_reaches_no_pty_and_closes_the_connection(view_relay):
    relay, slave = view_relay
    client = _connect(relay)
    client.sendall(encode_data(CANARY))
    assert _closed_by_relay(client)
    assert CANARY not in _slave_bytes(slave)


def test_a_resize_frame_changes_no_size_and_closes_the_connection(view_relay):
    relay, slave = view_relay
    client = _connect(relay)
    client.sendall(encode_resize(50, 200))
    assert _closed_by_relay(client)
    assert _size(slave) == HOST_SIZE


def test_every_reconnect_is_view_only(view_relay):
    relay, slave = view_relay
    for frame in (encode_data(CANARY), encode_resize(7, 9), encode_data(CANARY)):
        client = _connect(relay)
        client.sendall(frame)
        assert _closed_by_relay(client)
        client.close()
    assert CANARY not in _slave_bytes(slave)
    assert _size(slave) == HOST_SIZE


def test_a_frame_split_across_sends_still_closes_and_writes_nothing(view_relay):
    relay, slave = view_relay
    client = _connect(relay)
    frame = encode_data(CANARY)
    client.sendall(frame[:3])
    client.sendall(frame[3:])
    assert _closed_by_relay(client)
    assert CANARY not in _slave_bytes(slave)


def test_malformed_bytes_close_the_connection_too(view_relay):
    relay, slave = view_relay
    client = _connect(relay)
    client.sendall(b"\xff" * 16)
    assert _closed_by_relay(client)
    assert relay.running


def test_the_session_survives_and_output_still_flows_after_a_frame(view_relay):
    relay, slave = view_relay
    bad = _connect(relay)
    bad.sendall(encode_data(CANARY))
    assert _closed_by_relay(bad)
    _until(lambda: not relay.attached or relay.running)
    assert relay.running
    good = _connect(relay)
    time.sleep(0.1)
    os.write(slave, b"still-here")
    decoder, got = FrameDecoder(), b""
    while b"still-here" not in got:
        chunk = good.recv(4096)
        assert chunk
        got += b"".join(f.payload for f in decoder.feed(chunk) if isinstance(f, Data))


def test_closing_the_pane_stops_nothing(view_relay):
    relay, slave = view_relay
    client = _connect(relay)
    time.sleep(0.1)
    client.close()
    time.sleep(0.2)
    assert relay.running
    os.write(slave, b"after-pane-closed")  # the child can still write; the relay drains it
    time.sleep(0.1)
    assert relay.running


def test_an_interactive_relay_still_forwards_input(tmp_path):
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    relay = PaneRelay(master, base_dir=tmp_path)
    relay.start()
    try:
        client = _connect(relay)
        client.sendall(encode_data(b"typed"))
        assert b"typed" in _slave_bytes(slave)
    finally:
        relay.close()
        os.close(slave)
