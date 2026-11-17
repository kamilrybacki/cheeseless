"""Pty helpers for launching the reference TUI behind bwrap (ADR 0014)."""

import fcntl
import os
import struct
import termios

from cheeseless.adapters.outbound.sandbox import pty_launch


def _winsize(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


def test_open_pty_applies_the_initial_size():
    master, slave = pty_launch.open_pty(31, 117)
    try:
        assert _winsize(slave) == (31, 117)
    finally:
        os.close(master)
        os.close(slave)


def test_open_pty_fds_are_not_inheritable():
    master, slave = pty_launch.open_pty(24, 80)
    try:
        assert not os.get_inheritable(master)
        assert not os.get_inheritable(slave)
    finally:
        os.close(master)
        os.close(slave)


def test_set_winsize_resizes_through_the_master():
    master, slave = pty_launch.open_pty(24, 80)
    try:
        pty_launch.set_winsize(master, 50, 200)
        assert _winsize(slave) == (50, 200)
    finally:
        os.close(master)
        os.close(slave)


def test_controlling_tty_command_prefixes_the_command():
    argv = pty_launch.with_controlling_tty("/usr/bin/python3", ["tool", "--flag"])
    assert argv[:3] == ["/usr/bin/python3", "-I", "-c"]
    assert argv[3] == pty_launch.CTTY_SCRIPT
    assert argv[4:] == ["tool", "--flag"]
    assert "TIOCSCTTY" in pty_launch.CTTY_SCRIPT
