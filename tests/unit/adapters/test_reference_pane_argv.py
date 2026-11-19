"""The reference tmux pane is a viewer: it never attaches an input path (ADR 0019)."""

import sys
from pathlib import Path

from cheeseless import bootstrap_reference


class _Panes:
    def __init__(self) -> None:
        self.argv: list[str] = []

    def open(self, argv: list[str]) -> str:
        self.argv = argv
        return "%1"


def test_the_reference_pane_runs_the_view_only_client() -> None:
    panes = _Panes()
    assert bootstrap_reference._open_pane(panes, Path("/x/relay.sock")) == "%1"  # type: ignore[arg-type]
    assert panes.argv == [
        sys.executable,
        "-m",
        "cheeseless",
        "pane-relay",
        "--socket",
        "/x/relay.sock",
        "--view",
    ]
