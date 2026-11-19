"""The dev-events composition root: usage errors exit 2 with a fixed message."""

import argparse
from pathlib import Path

import pytest

from cheeseless.bootstrap_dev_events import MAX_INTERVAL_SECONDS, run_dev_events_command


def _args(interval: float = 1.0, lineage: str = "demo") -> argparse.Namespace:
    return argparse.Namespace(lineage=lineage, interval=interval, follow=False)


@pytest.mark.parametrize(
    "interval", [0.0, -1.0, float("nan"), float("inf"), MAX_INTERVAL_SECONDS + 1]
)
def test_an_interval_outside_the_bounds_is_a_usage_error(
    interval: float, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_dev_events_command(_args(interval)) == 2
    assert "--interval" in capsys.readouterr().err


def test_outside_a_git_repository_prints_a_fixed_message_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert run_dev_events_command(_args()) == 2

    err = capsys.readouterr().err
    assert "not inside a git repository" in err
    assert str(tmp_path) not in err
