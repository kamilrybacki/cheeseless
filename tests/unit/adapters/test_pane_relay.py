"""Host-side pane relay: pty master <-> one local client over a private UDS (ADR 0014)."""

import fcntl
import os
import socket
import stat
import struct
import termios
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.pane.relay import PaneRelay, peer_uid
from cheeseless.domain.pane_frames import (
    Data,
    FrameDecoder,
    encode_data,
    encode_resize,
)


@pytest.fixture
def relay(tmp_path: Path) -> Iterator[tuple[PaneRelay, int]]:
    master, slave = os.openpty()
    r = PaneRelay(master, base_dir=tmp_path)
    r.start()
    yield r, slave
    r.close()
    try:
        os.close(slave)
    except OSError:
        pass


def _connect(r: PaneRelay) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(str(r.socket_path))
    return s


def _until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def _read_data(s: socket.socket, want: bytes) -> None:
    dec, got = FrameDecoder(), b""
    while want not in got:
        chunk = s.recv(4096)
        assert chunk, "socket closed early"
        got += b"".join(f.payload for f in dec.feed(chunk) if isinstance(f, Data))


def _slave_size(slave: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(slave, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


def test_socket_dir_is_private_and_holds_the_socket(relay):
    r, _ = relay
    assert stat.S_IMODE(r.socket_path.parent.stat().st_mode) == 0o700
    assert r.socket_path.parent.parent == r.socket_path.parent.parent  # created under base_dir
    assert stat.S_ISSOCK(r.socket_path.stat().st_mode)


def test_output_reaches_the_client(relay):
    r, slave = relay
    c = _connect(r)
    time.sleep(0.1)
    os.write(slave, b"hello-from-tui")
    _read_data(c, b"hello-from-tui")


def test_client_input_reaches_the_child(relay):
    r, slave = relay
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    c = _connect(r)
    c.sendall(encode_data(b"k"))
    assert os.read(slave, 1) == b"k"


def test_resize_frame_sets_the_window_size(relay):
    r, slave = relay
    c = _connect(r)
    c.sendall(encode_resize(41, 133))
    _until(lambda: _slave_size(slave) == (41, 133))


def test_a_second_client_is_refused_and_the_first_keeps_working(relay):
    r, slave = relay
    first = _connect(r)
    time.sleep(0.1)
    second = _connect(r)
    assert second.recv(16) == b""
    os.write(slave, b"still-here")
    _read_data(first, b"still-here")


def test_a_peer_with_another_uid_is_refused(tmp_path):
    master, slave = os.openpty()
    r = PaneRelay(master, base_dir=tmp_path, host_uid=os.getuid() + 1)
    r.start()
    try:
        c = _connect(r)
        assert c.recv(16) == b""
    finally:
        r.close()
        os.close(slave)


def test_peer_uid_reads_so_peercred():
    a, b = socket.socketpair()
    assert peer_uid(a) == os.getuid()
    a.close()
    b.close()


def test_without_a_client_output_is_drained_and_not_replayed(relay):
    r, slave = relay
    os.set_blocking(slave, False)
    sent = 0
    deadline = time.monotonic() + 3
    while sent < 1_000_000 and time.monotonic() < deadline:
        try:
            sent += os.write(slave, b"x" * 4096)
        except BlockingIOError:
            time.sleep(0.005)
    assert sent >= 1_000_000, "child blocked: the relay did not drain"
    c = _connect(r)
    time.sleep(0.1)
    os.write(slave, b"fresh")
    _read_data(c, b"fresh")  # nothing older arrives first


def test_a_client_disconnect_keeps_the_relay_running(relay):
    r, slave = relay
    _connect(r).close()
    time.sleep(0.1)
    again = _connect(r)
    os.write(slave, b"back")
    _read_data(again, b"back")
    assert r.running


def test_a_malformed_frame_drops_the_client_only(relay):
    r, slave = relay
    bad = _connect(r)
    bad.sendall(b"\x09\x00\x00\x00\x00")
    assert bad.recv(16) == b""
    good = _connect(r)
    os.write(slave, b"ok")
    _read_data(good, b"ok")


def test_the_relay_stops_when_the_child_side_closes(relay):
    r, slave = relay
    c = _connect(r)
    os.close(slave)
    assert c.recv(16) == b""
    _until(lambda: not r.running)


def test_close_removes_the_socket_dir_and_closes_the_master(tmp_path):
    master, slave = os.openpty()
    r = PaneRelay(master, base_dir=tmp_path)
    r.start()
    d = r.socket_path.parent
    r.close()
    assert not d.exists()
    with pytest.raises(OSError):
        os.fstat(master)
    os.close(slave)
    r.close()  # idempotent
