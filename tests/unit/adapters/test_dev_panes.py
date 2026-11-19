"""``cheeseless dev --panes``: the phase's role pane is announced and the agent runs on a pty.

``host_session`` is replaced by a fake: these tests are about what ``dev`` asks of it and
what it prints, not about a sandbox. The real wiring is in
``tests/security/test_pane_session_wiring.py``.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from cheeseless import bootstrap_dev
from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.app.dev_flow import PhaseStart


class _Proc:
    def __init__(self) -> None:
        self.waited = False

    def wait(self) -> int:
        self.waited = True
        return 0


class _NoState:
    def dev_state(self, _lineage: str) -> None:
        return None


@pytest.fixture
def fake_session(monkeypatch):
    calls: list[dict[str, Any]] = []
    procs: list[_Proc] = []

    @contextmanager
    def host_session(**kwargs: Any) -> Iterator[dict[str, object]]:
        calls.append(kwargs)
        proc = _Proc()
        procs.append(proc)
        yield {"agent_proc": proc}

    monkeypatch.setattr(bootstrap_dev, "host_session", host_session)
    return calls, procs


def _args(tmp_path: Path, *extra: str):
    return build_parser().parse_args(
        ["dev", "--lineage", "demo", "--state-dir", str(tmp_path), *extra]
    )


def _run(args, role: str):
    start = PhaseStart(
        role=role,
        dev_lineage="demo" if role == "author" else None,
        session=None,
        spec_digest=None,
        reference=False,
    )
    return bootstrap_dev._run_session(args, start, ["/bin/true"], "demo", _NoState())  # type: ignore[arg-type]


def test_the_parser_has_an_opt_in_panes_flag(tmp_path) -> None:
    assert _args(tmp_path).panes is False
    assert _args(tmp_path, "--panes").panes is True


def test_without_panes_nothing_changes(fake_session, tmp_path, capsys) -> None:
    calls, procs = fake_session
    assert _run(_args(tmp_path), "author") == 0
    assert "agent_pty" not in calls[0] and "pane_lineage" not in calls[0]
    assert not procs[0].waited
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("role", "pane", "mode"),
    [
        ("author", "test-writer", "interactive"),
        ("implementer", "coder", "view"),
    ],
)
def test_panes_runs_the_agent_on_a_pty_and_announces_the_role_pane(
    fake_session, tmp_path, capsys, role, pane, mode
) -> None:
    calls, procs = fake_session
    assert _run(_args(tmp_path, "--panes"), role) == 0
    assert calls[0]["pane_lineage"] == "demo"
    rows, cols = calls[0]["agent_pty"]
    assert rows > 0 and cols > 0
    assert procs[0].waited, "with a pane the host waits for the agent, it does not tear it down"
    lines = [x for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "panes" and event["lineage"] == "demo"
    assert event["panes"] == [
        {
            "role": pane,
            "mode": mode,
            "socket": str(tmp_path / "cheeseless" / "panes" / "demo" / f"{pane}.sock"),
        }
    ]


def test_the_panes_event_stays_off_stdout(fake_session, tmp_path, capsys) -> None:
    _run(_args(tmp_path, "--panes", "--json"), "author")
    assert "panes" not in capsys.readouterr().out
