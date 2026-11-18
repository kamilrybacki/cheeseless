"""A typed challenge on the controlling terminal proves an operator is there (ADR 0017).

The code is read from ``/dev/tty`` and never from stdin, so an agent that pipes input
into the command cannot answer it. Anything typed before the code is shown is flushed.
There is one attempt, one deadline and a bounded read. Every failure (no terminal, a
background job, a wrong, late, empty or overlong answer, end of input, a hangup, an
interrupt or an I/O error) refuses. The terminal settings are always restored.
"""

from __future__ import annotations

import hmac
import os
import secrets
import select
import termios
import time
from typing import Any

from cheeseless.domain.presence import (
    PresenceConfirmation,
    PresenceRefused,
    PresenceRequest,
    render_prompt,
)

TTY_PATH = "/dev/tty"
TIMEOUT_SECONDS = 120.0
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
CODE_LENGTH = 8
MAX_ANSWER = 64
_QUIET = termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ECHONL
_HANGUP = select.POLLHUP | select.POLLERR | select.POLLNVAL


class TtyPresence:
    """The production :class:`~cheeseless.ports.presence.PresenceProof`."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("the presence timeout must be positive")
        self._timeout = timeout

    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        deadline = time.monotonic() + self._timeout
        fd = _open_controlling_tty()
        try:
            _require_foreground(fd)
            return challenge(fd, request, deadline)
        finally:
            os.close(fd)


def challenge(fd: int, request: PresenceRequest, deadline: float) -> PresenceConfirmation:
    """Show ``request`` and a fresh code on terminal ``fd``; accept only that code, once."""
    if not os.isatty(fd):
        raise PresenceRefused("the presence check needs a terminal")
    try:
        saved = termios.tcgetattr(fd)
    except termios.error as exc:
        raise PresenceRefused("could not read the terminal settings") from exc
    try:
        return _ask(fd, request, deadline, saved)
    except KeyboardInterrupt as exc:
        raise PresenceRefused("cancelled") from exc
    except (OSError, termios.error) as exc:
        raise PresenceRefused("terminal I/O failed") from exc
    finally:
        _restore(fd, saved)


def _ask(
    fd: int, request: PresenceRequest, deadline: float, saved: list[Any]
) -> PresenceConfirmation:
    quiet = list(saved)
    quiet[3] = saved[3] & ~_QUIET
    termios.tcsetattr(fd, termios.TCSANOW, quiet)
    termios.tcflush(fd, termios.TCIFLUSH)  # typed-ahead input never answers the code
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    _write_all(fd, f"{render_prompt(request)}type this code to confirm: {code}\n", deadline)
    answer = _read_answer(fd, deadline).strip().upper()
    if not hmac.compare_digest(answer, code.encode("ascii")):
        raise PresenceRefused("the code did not match")
    _write_all(fd, "\n", deadline)
    return PresenceConfirmation(request, secrets.token_hex(16))


def _restore(fd: int, saved: list[Any]) -> None:
    try:
        termios.tcsetattr(fd, termios.TCSANOW, saved)
    except (OSError, termios.error) as exc:
        raise PresenceRefused("could not restore the terminal settings") from exc


def _open_controlling_tty() -> int:
    try:
        return os.open(TTY_PATH, os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError as exc:  # ENXIO without a controlling terminal
        raise PresenceRefused("no controlling terminal") from exc


def _require_foreground(fd: int) -> None:
    try:
        foreground = os.tcgetpgrp(fd) == os.getpgrp()
    except OSError as exc:
        raise PresenceRefused("no controlling terminal") from exc
    if not foreground:
        raise PresenceRefused("not the terminal's foreground job")


def _wait(fd: int, event: int, deadline: float) -> None:
    """Block until ``fd`` is ready for ``event`` or the single deadline passes."""
    poller = select.poll()
    poller.register(fd, event | _HANGUP)
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise PresenceRefused("timed out")
        for _fd, happened in poller.poll(max(1, int(left * 1000))):
            if happened & _HANGUP:  # checked first: data that arrives with a hangup is refused
                raise PresenceRefused("the terminal hung up")
            if happened & event:
                return


def _read(fd: int, size: int) -> bytes:
    return os.read(fd, size)


def _read_answer(fd: int, deadline: float) -> bytes:
    """One line, at most ``MAX_ANSWER`` bytes, read without ever trusting its length."""
    buffer = b""
    while True:
        _wait(fd, select.POLLIN, deadline)
        chunk = _read(fd, MAX_ANSWER + 1 - len(buffer))
        if not chunk:
            raise PresenceRefused("end of input")
        buffer += chunk
        if len(buffer) > MAX_ANSWER:
            raise PresenceRefused("the answer was too long")
        if b"\n" in buffer:
            line, rest = buffer.split(b"\n", 1)
            if rest:
                raise PresenceRefused("more than one answer was typed")
            return line


def _write_all(fd: int, text: str, deadline: float) -> None:
    data = text.encode("utf-8", errors="replace")
    while data:
        _wait(fd, select.POLLOUT, deadline)
        data = data[os.write(fd, data) :]


__all__ = ["MAX_ANSWER", "TIMEOUT_SECONDS", "TtyPresence", "challenge"]
