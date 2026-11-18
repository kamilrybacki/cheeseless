"""The controlling-terminal presence challenge (ADR 0017), driven through a real pty pair."""

from __future__ import annotations

import errno
import os
import pty
import re
import select
import termios
import threading
import time
from collections.abc import Callable

import pytest

from cheeseless.adapters.outbound.presence import tty as tty_mod
from cheeseless.adapters.outbound.presence.tty import MAX_ANSWER, TtyPresence, challenge
from cheeseless.domain.presence import (
    PRESENCE_SIGNAL,
    PresenceConfirmation,
    PresenceOperation,
    PresenceRefused,
    PresenceRequest,
)

REQUEST = PresenceRequest(
    PresenceOperation.FREEZE_APPROVE, "lineage-a", "p" * 64, "alice", "freeze"
)
CODE = re.compile(rb"type this code to confirm: ([A-Z0-9]+)\r?\n")


class _Pty:
    """A pty pair; the test plays the operator on the master side."""

    def __init__(self) -> None:
        self.master, self.slave = pty.openpty()
        self.seen = b""

    def close(self) -> None:
        for fd in (self.master, self.slave):
            if fd >= 0:
                os.close(fd)
        self.master = self.slave = -1

    def hang_up(self) -> None:
        os.close(self.master)
        self.master = -1

    def queued(self) -> None:
        """Wait until typed-ahead input has reached the slave's input queue."""
        assert select.select([self.slave], [], [], 5)[0], "typed-ahead input never arrived"

    def type(self, data: bytes) -> None:
        os.write(self.master, data)

    def read_until(self, pattern: re.Pattern[bytes], timeout: float = 10.0) -> re.Match[bytes]:
        end = time.monotonic() + timeout
        while (match := pattern.search(self.seen)) is None:
            left = end - time.monotonic()
            assert left > 0, f"prompt never appeared: {self.seen!r}"
            ready, _, _ = select.select([self.master], [], [], left)
            if ready:
                self.seen += os.read(self.master, 4096)
        return match

    def drain(self, wait: float = 0.2) -> bytes:
        while select.select([self.master], [], [], wait)[0]:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            self.seen += chunk
        return self.seen


@pytest.fixture
def term():
    pair = _Pty()
    yield pair
    pair.close()


def _run(
    term: _Pty, operator: Callable[[_Pty], None], *, seconds: float = 10.0
) -> PresenceConfirmation | BaseException:
    """Run the challenge on the slave while ``operator`` acts on the master."""
    box: list[PresenceConfirmation | BaseException] = []

    def target() -> None:
        try:
            box.append(challenge(term.slave, REQUEST, time.monotonic() + seconds))
        except BaseException as exc:  # noqa: BLE001 - the test inspects whatever happened
            box.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    try:
        operator(term)
    finally:
        worker.join(seconds + 5)
    assert not worker.is_alive(), "the challenge did not finish"
    return box[0]


def _answer_with(transform: Callable[[bytes], bytes]) -> Callable[[_Pty], None]:
    def operator(term: _Pty) -> None:
        code = term.read_until(CODE).group(1)
        term.type(transform(code))

    return operator


def test_the_shown_code_confirms_exactly_this_request(term) -> None:
    before = termios.tcgetattr(term.slave)
    out = _run(term, _answer_with(lambda code: code + b"\n"))
    assert isinstance(out, PresenceConfirmation)
    assert out.request == REQUEST and out.signal == PRESENCE_SIGNAL and out.nonce
    assert termios.tcgetattr(term.slave) == before


def test_each_confirmation_is_fresh(term) -> None:
    first = _run(term, _answer_with(lambda code: code + b"\n"))
    term.seen = b""
    second = _run(term, _answer_with(lambda code: code + b"\n"))
    assert isinstance(first, PresenceConfirmation) and isinstance(second, PresenceConfirmation)
    assert first.nonce != second.nonce


def test_the_answer_is_not_echoed(term) -> None:
    out = _run(term, _answer_with(lambda code: code + b"\n"))
    assert isinstance(out, PresenceConfirmation)
    code = CODE.search(term.seen).group(1)  # type: ignore[union-attr]
    assert term.drain().count(code) == 1  # shown once by the prompt, never echoed back


def test_the_prompt_names_the_decision(term) -> None:
    _run(term, _answer_with(lambda code: code + b"\n"))
    shown = term.seen
    for field in (b"freeze-approve", b"lineage-a", b"p" * 64, b"alice", b"freeze"):
        assert field in shown


def test_terminal_controls_in_fields_are_shown_inert(term) -> None:
    hostile = PresenceRequest(
        PresenceOperation.DISPUTE_RESOLVE, "lin", "\x1b[2J\x1b]0;owned\x07d1", "‮evil", "x"
    )
    box: list[object] = []

    def target() -> None:
        try:
            box.append(challenge(term.slave, hostile, time.monotonic() + 5))
        except PresenceRefused as exc:
            box.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    term.read_until(CODE)
    term.type(b"\n")
    worker.join(10)
    assert b"\x1b" not in term.seen and b"\x07" not in term.seen
    assert "‮".encode() not in term.seen
    assert b"\\x1b[2J" in term.seen and b"\\u202e" in term.seen


def test_a_wrong_code_is_refused_and_the_terminal_restored(term) -> None:
    before = termios.tcgetattr(term.slave)
    out = _run(term, _answer_with(lambda code: b"WRONG" + code[5:] + b"\n"))
    assert isinstance(out, PresenceRefused) and "did not match" in out.reason
    assert termios.tcgetattr(term.slave) == before


def test_only_one_attempt_is_allowed(term) -> None:
    def twice(term: _Pty) -> None:
        code = term.read_until(CODE).group(1)
        term.type(b"nope\n")
        term.type(code + b"\n")

    out = _run(term, twice)
    assert isinstance(out, PresenceRefused)


def test_an_empty_answer_is_refused(term) -> None:
    out = _run(term, _answer_with(lambda _code: b"\n"))
    assert isinstance(out, PresenceRefused)


def test_silence_times_out_and_the_terminal_is_restored(term) -> None:
    before = termios.tcgetattr(term.slave)
    started = time.monotonic()
    out = _run(term, lambda term: term.read_until(CODE) and None, seconds=0.5)
    assert isinstance(out, PresenceRefused) and "timed out" in out.reason
    assert time.monotonic() - started < 5
    assert termios.tcgetattr(term.slave) == before


def test_end_of_input_is_refused(term) -> None:
    eof = termios.tcgetattr(term.slave)[6][termios.VEOF]
    out = _run(term, _answer_with(lambda _code: eof))
    assert isinstance(out, PresenceRefused) and "end of input" in out.reason


def test_a_hangup_is_refused(term) -> None:
    def hang_up(term: _Pty) -> None:
        term.read_until(CODE)
        term.hang_up()

    out = _run(term, hang_up)
    assert isinstance(out, PresenceRefused)


def test_overlong_input_is_refused_without_reading_it_all(term) -> None:
    out = _run(term, _answer_with(lambda code: code + b"A" * (MAX_ANSWER * 4) + b"\n"))
    assert isinstance(out, PresenceRefused) and "too long" in out.reason


def test_typed_ahead_input_is_flushed_before_the_code_is_shown(term) -> None:
    # A line queued before the prompt would be the answer if the queue were not flushed.
    term.type(b"PRELOADED\n")
    term.queued()
    out = _run(term, _answer_with(lambda code: code + b"\n"))
    assert isinstance(out, PresenceConfirmation)


def test_typed_ahead_input_alone_never_confirms(term) -> None:
    term.type(b"PRELOADED\n" * 4)
    term.queued()
    out = _run(term, lambda term: term.read_until(CODE) and None, seconds=0.5)
    assert isinstance(out, PresenceRefused) and "timed out" in out.reason


def test_an_interrupt_is_refused_and_the_terminal_restored(term, monkeypatch) -> None:
    before = termios.tcgetattr(term.slave)

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(tty_mod, "_wait", interrupted)
    with pytest.raises(PresenceRefused) as caught:
        challenge(term.slave, REQUEST, time.monotonic() + 5)
    assert "cancelled" in caught.value.reason
    assert termios.tcgetattr(term.slave) == before


def test_a_read_error_is_refused(term, monkeypatch) -> None:
    def broken(_fd, _n):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(tty_mod, "_read", broken)
    out = _run(term, lambda term: term.read_until(CODE) and term.type(b"x\n"))
    assert isinstance(out, PresenceRefused)


def test_a_descriptor_that_is_not_a_terminal_is_refused() -> None:
    read_end, write_end = os.pipe()
    try:
        with pytest.raises(PresenceRefused):
            challenge(read_end, REQUEST, time.monotonic() + 1)
    finally:
        os.close(read_end)
        os.close(write_end)


def test_a_failed_restore_is_refused_even_after_a_correct_code(term, monkeypatch) -> None:
    real = termios.tcsetattr
    calls = {"n": 0}

    def flaky(fd, when, attrs):
        calls["n"] += 1
        if calls["n"] > 1:  # the restore, not the quiet mode
            raise termios.error(errno.EIO, "gone")
        return real(fd, when, attrs)

    monkeypatch.setattr(tty_mod.termios, "tcsetattr", flaky)
    out = _run(term, _answer_with(lambda code: code + b"\n"))
    assert isinstance(out, PresenceRefused) and "restore" in out.reason


def test_no_controlling_terminal_is_refused(monkeypatch) -> None:
    def enxio(*_a, **_k):
        raise OSError(errno.ENXIO, "No such device or address")

    monkeypatch.setattr(tty_mod.os, "open", enxio)
    with pytest.raises(PresenceRefused) as caught:
        TtyPresence().confirm(REQUEST)
    assert "controlling terminal" in caught.value.reason


def test_a_background_job_is_refused(term, monkeypatch) -> None:
    monkeypatch.setattr(tty_mod.os, "open", lambda *_a, **_k: os.dup(term.slave))
    monkeypatch.setattr(tty_mod.os, "tcgetpgrp", lambda _fd: os.getpgrp() + 1)
    with pytest.raises(PresenceRefused) as caught:
        TtyPresence().confirm(REQUEST)
    assert "foreground" in caught.value.reason


def test_the_adapter_opens_the_controlling_terminal_and_never_stdin(term, monkeypatch) -> None:
    opened: list[str] = []

    def fake_open(path, flags, *_a):
        opened.append(path)
        assert flags & os.O_NOCTTY and flags & os.O_RDWR
        return os.dup(term.slave)

    monkeypatch.setattr(tty_mod.os, "open", fake_open)
    monkeypatch.setattr(tty_mod.os, "tcgetpgrp", lambda _fd: os.getpgrp())
    box: list[object] = []
    worker = threading.Thread(target=lambda: box.append(TtyPresence(timeout=10).confirm(REQUEST)))
    worker.start()
    term.type(term.read_until(CODE).group(1) + b"\n")
    worker.join(15)
    assert opened == ["/dev/tty"]
    assert isinstance(box[0], PresenceConfirmation)


def test_the_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TtyPresence(timeout=0)
