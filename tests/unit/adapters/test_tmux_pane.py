"""tmux pane helper: runs on the host only, with an injected runner (no real tmux)."""

import subprocess

import pytest

from cheeseless.adapters.outbound.pane.tmux import PaneError, TmuxPanes


class FakeRunner:
    def __init__(self, stdout: str = "%7\n", returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._result = subprocess.CompletedProcess([], returncode, stdout, "boom")

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return self._result


def test_available_only_when_the_host_env_has_tmux():
    assert TmuxPanes.available({"TMUX": "/x,1,0"})
    assert not TmuxPanes.available({})
    assert not TmuxPanes.available({"TMUX": ""})


def test_open_splits_horizontally_without_stealing_focus():
    runner = FakeRunner("%12\n")
    pane = TmuxPanes(runner).open(["cheeseless", "pane-relay", "--socket", "/s"])
    assert pane == "%12"
    assert runner.calls == [
        [
            "tmux",
            "split-window",
            "-h",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "--",
            "cheeseless",
            "pane-relay",
            "--socket",
            "/s",
        ]
    ]


def test_close_kills_the_pane_by_id():
    runner = FakeRunner("")
    TmuxPanes(runner).close("%3")
    assert runner.calls == [["tmux", "kill-pane", "-t", "%3"]]


@pytest.mark.parametrize("bad", ["", "7", "%", "%x", "%1;rm", "%1\n%2", "-t"])
def test_close_refuses_a_malformed_pane_id(bad):
    runner = FakeRunner()
    with pytest.raises(PaneError, match="pane id"):
        TmuxPanes(runner).close(bad)
    assert runner.calls == []


def test_open_refuses_a_malformed_pane_id_from_tmux():
    with pytest.raises(PaneError, match="pane id"):
        TmuxPanes(FakeRunner("not-a-pane\n")).open(["x"])


def test_open_reports_a_failed_tmux():
    with pytest.raises(PaneError, match="split-window"):
        TmuxPanes(FakeRunner("", returncode=1)).open(["x"])


def test_open_refuses_an_empty_argv():
    with pytest.raises(PaneError):
        TmuxPanes(FakeRunner()).open([])
