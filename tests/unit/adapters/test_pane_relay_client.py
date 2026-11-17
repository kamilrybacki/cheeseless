"""`cheeseless pane-relay`: raw-mode terminal client for the host relay (ADR 0014)."""

import fcntl
import os
import socket
import struct
import termios
import threading
from pathlib import Path

import pytest

from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.adapters.inbound.cli.pane_client import run_pane_client
from cheeseless.domain.pane_frames import (
    Data,
    FrameDecoder,
    Resize,
    encode_data,
)


class FakeRelay:
    """One-shot UDS server that records frames and can push output."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(1)
        self.conn: socket.socket | None = None
        self.frames: list = []
        self._dec = FrameDecoder()

    def accept(self) -> None:
        self.conn, _ = self.srv.accept()
        self.conn.settimeout(3)

    def read_until(self, pred) -> None:
        assert self.conn is not None
        while not pred(self.frames):
            chunk = self.conn.recv(4096)
            assert chunk
            self.frames.extend(self._dec.feed(chunk))

    def close(self) -> None:
        if self.conn:
            self.conn.close()
        self.srv.close()


@pytest.fixture
def term():
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 33, 101, 0, 0))
    yield master, slave
    os.close(master)
    os.close(slave)


def _run(sock: Path, slave: int, result: list[int], resize_fd: int | None = None):
    if resize_fd is None:  # signal handlers need the main thread; tests run in a worker
        resize_fd = os.pipe()[0]
    t = threading.Thread(
        target=lambda: result.append(
            run_pane_client(sock, stdin_fd=slave, stdout_fd=slave, resize_fd=resize_fd)
        )
    )
    t.start()
    return t


def test_sends_the_initial_size_pumps_both_ways_and_restores_the_tty(term, tmp_path):
    master, slave = term
    before = termios.tcgetattr(slave)
    relay = FakeRelay(tmp_path / "r.sock")
    result: list[int] = []
    thread = _run(relay.path, slave, result)
    relay.accept()
    relay.read_until(lambda f: Resize(33, 101) in f)

    os.write(master, b"typed")
    relay.read_until(lambda f: any(isinstance(x, Data) and x.payload for x in f))
    assert relay.conn is not None
    relay.conn.sendall(encode_data(b"screen"))
    assert os.read(master, 100).endswith(b"screen")
    during = termios.tcgetattr(slave)
    assert not during[3] & (termios.ICANON | termios.ECHO), "tty was not in raw mode"

    relay.conn.close()  # relay closes: client must exit cleanly
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result == [0]
    assert termios.tcgetattr(slave) == before
    relay.srv.close()


def test_a_resize_trigger_sends_a_new_size(term, tmp_path):
    master, slave = term
    relay = FakeRelay(tmp_path / "r.sock")
    trigger_r, trigger_w = os.pipe()
    result: list[int] = []
    thread = _run(relay.path, slave, result, resize_fd=trigger_r)
    relay.accept()
    relay.read_until(lambda f: Resize(33, 101) in f)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 150, 0, 0))
    os.write(trigger_w, b"x")
    relay.read_until(lambda f: Resize(40, 150) in f)
    assert relay.conn is not None
    relay.conn.close()
    thread.join(timeout=5)
    relay.srv.close()
    os.close(trigger_r)
    os.close(trigger_w)


def test_sigwinch_is_wired_to_the_resize_trigger_on_the_main_thread(term, tmp_path):
    import signal

    from cheeseless.adapters.inbound.cli.pane_client import install_winch_pipe

    previous = signal.getsignal(signal.SIGWINCH)
    read_fd, restore = install_winch_pipe()
    try:
        signal.raise_signal(signal.SIGWINCH)
        assert os.read(read_fd, 1) == b"w"
    finally:
        restore()
    assert signal.getsignal(signal.SIGWINCH) == previous


def test_a_missing_socket_is_an_error_and_leaves_the_tty_alone(term, tmp_path):
    _, slave = term
    before = termios.tcgetattr(slave)
    assert run_pane_client(tmp_path / "nope.sock", stdin_fd=slave, stdout_fd=slave) == 1
    assert termios.tcgetattr(slave) == before


def test_the_tty_is_restored_when_the_relay_sends_garbage(term, tmp_path):
    _, slave = term
    before = termios.tcgetattr(slave)
    relay = FakeRelay(tmp_path / "r.sock")
    result: list[int] = []
    thread = _run(relay.path, slave, result)
    relay.accept()
    assert relay.conn is not None
    relay.conn.sendall(b"\x09\x00\x00\x00\x00")
    thread.join(timeout=5)
    assert result == [1]
    assert termios.tcgetattr(slave) == before
    relay.close()


def test_the_parser_has_pane_relay_with_a_required_socket():
    args = build_parser().parse_args(["pane-relay", "--socket", "/x/y"])
    assert args.command == "pane-relay"
    assert args.socket == Path("/x/y")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pane-relay"])


def test_write_all_survives_short_writes(monkeypatch):
    from cheeseless.adapters.inbound.cli import pane_client

    written: list[bytes] = []

    def short_write(_fd: int, data) -> int:
        chunk = bytes(data[:3])
        written.append(chunk)
        return len(chunk)

    monkeypatch.setattr(pane_client.os, "write", short_write)
    pane_client.write_all(1, b"0123456789")
    assert b"".join(written) == b"0123456789"


def test_sigterm_becomes_system_exit_on_the_main_thread():
    import signal

    from cheeseless.adapters.inbound.cli.pane_client import install_exit_handlers

    previous = signal.getsignal(signal.SIGTERM)
    restore = install_exit_handlers()
    try:
        with pytest.raises(SystemExit) as exc:
            signal.raise_signal(signal.SIGTERM)
        assert exc.value.code == 128 + signal.SIGTERM
    finally:
        restore()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_sigterm_restores_the_tty_of_a_running_client(term, tmp_path):
    import signal
    import subprocess
    import sys
    import time

    master, slave = term
    before = termios.tcgetattr(slave)
    relay = FakeRelay(tmp_path / "r.sock")
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from cheeseless.adapters.inbound.cli.pane_client import run_pane_client\n"
        "sys.exit(run_pane_client(Path(sys.argv[1])))\n"
    )
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-c", code, str(relay.path)], stdin=slave, stdout=slave
    )
    try:
        relay.accept()
        relay.read_until(lambda f: Resize(33, 101) in f)
        deadline = time.monotonic() + 5
        while termios.tcgetattr(slave)[3] & termios.ICANON:
            assert time.monotonic() < deadline, "client never entered raw mode"
            time.sleep(0.01)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 128 + signal.SIGTERM
        assert termios.tcgetattr(slave) == before
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        relay.close()
