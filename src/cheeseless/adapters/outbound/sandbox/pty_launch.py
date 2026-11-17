"""Pty plumbing for the reference TUI pane (ADR 0014).

The pty pair is created by the host and dedicated to one sandbox. The sandbox gets the
slave as stdin, stdout and stderr; the host keeps the master. The profile still passes
``--new-session``, so the child is in a new session with no controlling terminal: it can
never reach the host terminal through ``TIOCSTI`` or the tty job-control ioctls, and the
only terminal it can name is this private pty. ``--new-session`` also means the child gets
no ``SIGWINCH`` when the host resizes the pty (verified empirically: no controlling tty,
no signal). :data:`CTTY_SCRIPT` is the minimal trusted step that restores resize: inside
the sandbox it makes fd 0, the dedicated slave, the controlling terminal of a fresh
session before ``exec``.
"""

import fcntl
import os
import struct
import termios

CTTY_SCRIPT = (
    "import os, sys, fcntl, termios\n"
    "os.setsid()\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def set_winsize(fd: int, rows: int, cols: int) -> None:
    """Apply a window size to the pty that ``fd`` belongs to (master or slave)."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def open_pty(rows: int, cols: int) -> tuple[int, int]:
    """Return ``(master, slave)`` with the window size set and both fds non-inheritable."""
    master, slave = os.openpty()
    try:
        os.set_inheritable(master, False)
        os.set_inheritable(slave, False)
        set_winsize(master, rows, cols)
    except BaseException:
        os.close(master)
        os.close(slave)
        raise
    return master, slave


def with_controlling_tty(python: str, command: list[str]) -> list[str]:
    """Wrap ``command`` so it runs as a session leader with fd 0 as its controlling tty."""
    return [python, "-I", "-c", CTTY_SCRIPT, *command]
