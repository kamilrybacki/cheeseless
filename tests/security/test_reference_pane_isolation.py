"""ADR 0014: the reference pane never hands the sandbox a tmux handle or a host terminal.

The sandbox is started the way a session starts it (``profiles.agent`` through
``_launch_agent`` with seccomp), but on a dedicated pty. The canaries are host tmux
variables: none may be visible inside, and the pty master and the relay socket must be
unreachable from the sandbox. The relay admits only the host uid and one client.
"""

import contextlib
import os
import select
import socket
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.pane.relay import PaneRelay
from cheeseless.adapters.outbound.sandbox import profiles, pty_launch, seccomp
from cheeseless.bootstrap import _launch_agent, _terminate
from cheeseless.domain.pane_frames import encode_data, encode_resize
from cheeseless.session_options import SessionOptionError, check_extra_env, within
from tests.requirements import needs

PYTHON = "/usr/bin/python3"
pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access(PYTHON, os.X_OK),
    "bwrap and a system python are required",
)

CANARIES = {
    "TMUX": "canary-tmux-socket,4242,0",
    "TMUX_PANE": "%canary99",
    "TMUX_TMPDIR": "canary-tmux-tmpdir",
}


class Sandbox:
    def __init__(self, tmp_path: Path, command: list[str], *, ctty: bool = True) -> None:
        self.proj = tmp_path / "proj"
        (self.proj / "acceptance").mkdir(parents=True)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.master, slave = pty_launch.open_pty(24, 80)
        argv = pty_launch.with_controlling_tty(PYTHON, command) if ctty else command
        acceptance_fd = profiles.open_acceptance(self.proj)
        seccomp_fd = seccomp.program_fd()

        def build(info_fd: int) -> list[str]:
            return profiles.agent(
                bwrap="/usr/bin/bwrap",
                projection=self.proj,
                sockets=[],
                home=self.home,
                read_only=[],
                env=(),
                command=argv,
                acceptance_fd=acceptance_fd,
                seccomp_fd=seccomp_fd,
                info_fd=info_fd,
            )

        try:
            self.proc, self.ns_fd = _launch_agent(
                build, (seccomp_fd, acceptance_fd), pty_slave=slave
            )
        finally:
            os.close(slave)
            os.close(seccomp_fd)
            os.close(acceptance_fd)
        self.master_open = True

    def read_until(self, needle: bytes, timeout: float = 8.0) -> bytes:
        got, end = b"", time.monotonic() + timeout
        while needle not in got and time.monotonic() < end:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            got += chunk
        return got

    def drain(self, timeout: float = 8.0) -> bytes:
        return self.read_until(b"\0never", timeout)

    def close(self) -> None:
        _terminate(self.proc)
        self.proc.wait(timeout=10)
        os.close(self.ns_fd)
        if self.master_open:
            os.close(self.master)


@pytest.fixture
def start(tmp_path, monkeypatch):
    for key, value in CANARIES.items():
        monkeypatch.setenv(key, value)
    made: list[Sandbox] = []

    def make(command: list[str], **kwargs) -> Sandbox:
        sandbox = Sandbox(tmp_path, command, **kwargs)
        made.append(sandbox)
        return sandbox

    yield make
    for sandbox in made:
        sandbox.close()


def test_no_tmux_variable_or_canary_reaches_the_sandbox(start):
    out = start(["/usr/bin/env"]).drain().decode()
    assert "TMUX" not in out
    assert "canary" not in out
    assert "HOME=" in out  # the environment was captured at all


def test_only_the_pty_slave_is_inherited(start):
    script = (
        "import os\n"
        "for fd in sorted(os.listdir('/proc/self/fd'), key=int):\n"
        "    t = os.readlink('/proc/self/fd/' + fd)\n"
        "    if not t.endswith('/fd'):\n"
        "        print('FD', fd, t)\n"
    )
    out = start([PYTHON, "-I", "-c", script]).drain().decode()
    lines = [line.split() for line in out.splitlines() if line.startswith("FD")]
    assert {int(line[1]) for line in lines} <= {0, 1, 2}
    assert all("ptmx" not in line[2] for line in lines)
    assert {int(line[1]) for line in lines} == {0, 1, 2}


def test_the_relay_socket_is_not_visible_inside_the_sandbox(start):
    master, slave = os.openpty()
    relay = PaneRelay(master)
    relay.start()
    try:
        sandbox = start(["/usr/bin/test", "-e", str(relay.socket_path.parent)])
        sandbox.drain()
        assert sandbox.proc.wait(timeout=10) != 0
        for protected in (sandbox.proj, sandbox.home):
            assert not within(relay.socket_path, protected)
    finally:
        relay.close()
        os.close(slave)


def test_the_sandbox_cannot_connect_to_the_relay_but_the_host_can(start):
    """Negative: a connect from inside fails. Positive control: the host then attaches
    and receives a canary written to the reference pty, so the relay was live and free."""
    master, slave = os.openpty()
    relay = PaneRelay(master)
    relay.start()
    probe = (
        "import socket, sys\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "try:\n"
        "    s.connect(sys.argv[1])\n"
        "except OSError:\n"
        "    print('BLOCKED', flush=True)\n"
        "else:\n"
        "    s.settimeout(3)\n"
        "    print('CONNECTED', s.recv(4096), flush=True)\n"
    )
    try:
        out = start([PYTHON, "-I", "-c", probe, str(relay.socket_path)]).drain().decode()
        assert "BLOCKED" in out
        assert "CONNECTED" not in out
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as host:
            host.connect(str(relay.socket_path))
            host.settimeout(5)
            got, end = b"", time.monotonic() + 5
            while b"canary-pane-output" not in got and time.monotonic() < end:
                os.write(slave, b"canary-pane-output\r\n")
                with contextlib.suppress(TimeoutError):
                    got += host.recv(4096)
            assert b"canary-pane-output" in got
    finally:
        relay.close()
        os.close(slave)


def test_the_relay_dir_is_private(tmp_path):
    master, slave = os.openpty()
    relay = PaneRelay(master)
    relay.start()
    try:
        assert relay.socket_path.parent.stat().st_mode & 0o777 == 0o700
        assert relay.socket_path.stat().st_mode & 0o077 == 0
        assert not within(relay.socket_path, tmp_path)
    finally:
        relay.close()
        os.close(slave)


def test_new_session_alone_gives_no_resize_signal(start):
    script = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGWINCH, lambda *a: print('WINCH', flush=True))\n"
        "print('ready', flush=True)\n"
        "time.sleep(2)\n"
    )
    sandbox = start([PYTHON, "-I", "-c", script], ctty=False)
    assert b"ready" in sandbox.read_until(b"ready")
    pty_launch.set_winsize(sandbox.master, 50, 200)
    assert b"WINCH" not in sandbox.drain(timeout=3)


def test_the_controlling_tty_step_restores_resize(start):
    script = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGWINCH, lambda *a: print('WINCH', flush=True))\n"
        "print('ready', flush=True)\n"
        "time.sleep(5)\n"
    )
    sandbox = start([PYTHON, "-I", "-c", script])
    assert b"ready" in sandbox.read_until(b"ready")
    pty_launch.set_winsize(sandbox.master, 50, 200)
    assert b"WINCH" in sandbox.read_until(b"WINCH")


def test_a_client_disconnect_leaves_the_child_running(start):
    sandbox = start([PYTHON, "-I", "-c", "import time; print('up', flush=True); time.sleep(20)"])
    relay = PaneRelay(sandbox.master)
    sandbox.master_open = False  # the relay owns the master now
    relay.start()
    try:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(3)
        client.connect(str(relay.socket_path))
        client.sendall(encode_resize(30, 100) + encode_data(b"x"))
        client.close()
        time.sleep(0.5)
        assert sandbox.proc.poll() is None
        assert relay.running
    finally:
        relay.close()


def test_a_second_client_and_a_foreign_uid_are_refused(tmp_path):
    master, slave = os.openpty()
    relay = PaneRelay(master, base_dir=tmp_path)
    relay.start()
    try:
        first = socket.socket(socket.AF_UNIX)
        first.settimeout(3)
        first.connect(str(relay.socket_path))
        time.sleep(0.1)
        second = socket.socket(socket.AF_UNIX)
        second.settimeout(3)
        second.connect(str(relay.socket_path))
        assert second.recv(8) == b""
    finally:
        relay.close()
    master2, slave2 = os.openpty()
    foreign = PaneRelay(master2, base_dir=tmp_path, host_uid=os.getuid() + 1)
    foreign.start()
    try:
        peer = socket.socket(socket.AF_UNIX)
        peer.settimeout(3)
        peer.connect(str(foreign.socket_path))
        assert peer.recv(8) == b""
    finally:
        foreign.close()
        os.close(slave)
        os.close(slave2)


@pytest.mark.parametrize("name", ["TMUX", "TMUX_PANE", "TMUX_TMPDIR", "TMUX_ANYTHING"])
def test_extra_env_with_a_tmux_name_is_refused(name):
    with pytest.raises(SessionOptionError, match="set by the session"):
        check_extra_env({name: "x"}, ())


def test_terminate_stops_a_pty_child(start):
    sandbox = start([PYTHON, "-I", "-c", "import time; time.sleep(30)"])
    _terminate(sandbox.proc)
    sandbox.proc.wait(timeout=10)
    assert sandbox.proc.poll() is not None
