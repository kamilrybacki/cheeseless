"""`pane-relay --wait`: poll for a relay socket that does not exist yet, then connect."""

import os
import tempfile
import threading
from pathlib import Path

import pytest

import cheeseless.adapters.inbound.cli.pane_client as pane_client
from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.adapters.inbound.cli.pane_client import (
    EXIT_NO_SOCKET,
    NO_SOCKET_MESSAGE,
    wait_for_socket,
)
from cheeseless.bootstrap import main


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _short_dir() -> Path:
    # AF_UNIX paths are short; pytest's tmp_path can be too long for a socket.
    return Path(tempfile.mkdtemp(prefix="cl-"))


def test_an_existing_socket_returns_at_once_without_sleeping(tmp_path: Path) -> None:
    path = tmp_path / "x.sock"
    path.touch()
    fake = FakeClock()

    assert wait_for_socket(path, 5.0, clock=fake.clock, sleep=fake.sleep)
    assert fake.slept == []


def test_a_socket_that_appears_later_is_found(tmp_path: Path) -> None:
    path = tmp_path / "x.sock"
    fake = FakeClock()

    def sleep(seconds: float) -> None:
        fake.sleep(seconds)
        if len(fake.slept) == 3:
            path.touch()

    assert wait_for_socket(path, 5.0, clock=fake.clock, sleep=sleep, interval=0.1)
    assert len(fake.slept) == 3


def test_a_socket_that_never_appears_times_out_after_a_final_check(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "x.sock"  # the parent does not exist either
    fake = FakeClock()

    assert not wait_for_socket(path, 1.0, clock=fake.clock, sleep=fake.sleep, interval=0.3)
    assert sum(fake.slept) == pytest.approx(1.0)
    assert max(fake.slept) <= 0.3


def test_a_socket_at_the_deadline_is_still_found(tmp_path: Path) -> None:
    path = tmp_path / "x.sock"
    fake = FakeClock()

    def sleep(seconds: float) -> None:
        fake.sleep(seconds)
        if fake.now >= 1.0:
            path.touch()

    assert wait_for_socket(path, 1.0, clock=fake.clock, sleep=sleep, interval=0.3)


def test_a_parent_that_is_a_file_counts_as_absent(tmp_path: Path) -> None:
    (tmp_path / "file").touch()
    fake = FakeClock()

    assert not wait_for_socket(
        tmp_path / "file" / "x.sock", 0.2, clock=fake.clock, sleep=fake.sleep
    )


def test_an_unreadable_path_counts_as_present_so_connect_reports_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def denied(_path: object) -> os.stat_result:
        raise PermissionError

    monkeypatch.setattr(pane_client.os, "lstat", denied)
    fake = FakeClock()

    assert wait_for_socket(tmp_path / "x.sock", 1.0, clock=fake.clock, sleep=fake.sleep)


@pytest.mark.parametrize("value", ["0", "-1", "600.5", "nan", "inf", "-inf", "soon", ""])
def test_the_parser_refuses_a_wait_outside_the_bounds(value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["pane-relay", "--socket", "/x", "--wait", value])
    assert exc.value.code == 2


@pytest.mark.parametrize(("value", "seconds"), [("0.1", 0.1), ("30", 30.0), ("600", 600.0)])
def test_the_parser_accepts_a_wait_within_the_bounds(value: str, seconds: float) -> None:
    args = build_parser().parse_args(["pane-relay", "--socket", "/x", "--wait", value])
    assert args.wait == seconds


def test_without_wait_the_parser_leaves_it_unset() -> None:
    assert build_parser().parse_args(["pane-relay", "--socket", "/x"]).wait is None


def _record_client(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, bool]]:
    seen: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        pane_client,
        "run_pane_client",
        lambda path, *, view_only=False, **_: seen.append((path, view_only)) or 0,
    )
    return seen


def test_a_timeout_exits_three_with_the_fixed_line_and_no_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _record_client(monkeypatch)
    path = _short_dir() / "x.sock"

    assert main(["pane-relay", "--socket", str(path), "--wait", "0.2"]) == EXIT_NO_SOCKET == 3

    captured = capsys.readouterr()
    assert captured.err == NO_SOCKET_MESSAGE + "\n"
    assert str(path.parent) not in captured.err
    assert captured.out == ""
    assert seen == []


def test_a_role_pane_waits_for_its_socket_then_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_client(monkeypatch)
    state = _short_dir()
    sock = state / "cheeseless" / "panes" / "lin-1" / "coder.sock"

    def create() -> None:
        sock.parent.mkdir(parents=True)
        sock.touch()

    timer = threading.Timer(0.2, create)
    timer.start()
    try:
        argv = ["pane-relay", "--role", "coder", "--lineage", "lin-1", "--state-dir", str(state)]
        assert main([*argv, "--wait", "5"]) == 0
    finally:
        timer.join()

    assert seen == [(sock, True)]


def test_a_plain_socket_with_wait_keeps_its_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_client(monkeypatch)
    path = _short_dir() / "x.sock"
    path.touch()

    assert main(["pane-relay", "--socket", str(path), "--view", "--wait", "1"]) == 0
    assert main(["pane-relay", "--socket", str(path), "--wait", "1"]) == 0

    assert seen == [(path, True), (path, False)]


def test_without_wait_a_missing_socket_goes_straight_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_client(monkeypatch)

    def never(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("waited without --wait")

    monkeypatch.setattr(pane_client, "wait_for_socket", never)
    path = _short_dir() / "x.sock"

    assert main(["pane-relay", "--socket", str(path)]) == 0
    assert seen == [(path, False)]


def test_an_interrupt_while_waiting_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_client(monkeypatch)

    def interrupted(*_args: object, **_kwargs: object) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(pane_client, "wait_for_socket", interrupted)

    assert main(["pane-relay", "--socket", "/x/y.sock", "--wait", "5"]) == 130
    assert seen == []
