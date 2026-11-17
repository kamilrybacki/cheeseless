"""ADR 0014 slice 3c: the host owns the whole reference attempt.

A headless attempt is ``done`` only on exit 0 and binds its snapshot; a non-zero exit is
``failed`` with no snapshot; a TUI attempt ends only on ``reference_done`` and a sandbox
that exits without it fails. Stale attempts a dead host left behind are reaped first.
"""

import json
import os
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.authoring import spec_of
from cheeseless.app.freeze import read_contract
from cheeseless.bootstrap_reference import run_reference_attempt
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from tests.requirements import needs
from tests.toy import make_repo

PYTHON = "/usr/bin/python3"
pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/git", os.X_OK),
    "bwrap and git are required",
)

LINEAGE = "toy"
TASK = "a" * 64
S = ReferenceState


class FakePanes:
    def __init__(self) -> None:
        self.opened: list[list[str]] = []
        self.closed: list[str] = []

    @staticmethod
    def available(env: dict[str, str]) -> bool:
        return bool(env.get("TMUX"))

    def open(self, argv: list[str]) -> str:
        self.opened.append(argv)
        return "%7"

    def close(self, pane_id: str) -> None:
        self.closed.append(pane_id)


@pytest.fixture
def repo(tmp_path) -> GitRepository:
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    store = _store(repo, tmp_path)
    digest = spec_of(read_contract(repo, repo.head_commit()))
    store.save_dev_state(DevState(LINEAGE).spec_done(digest))
    return repo


def _store(repo: GitRepository, tmp_path: Path) -> FileStore:
    return FileStore(store_root(repo.common_dir(), tmp_path / "state"))


def _digest(repo: GitRepository) -> str:
    return spec_of(read_contract(repo, repo.head_commit()))


def _run(repo: GitRepository, tmp_path: Path, command: list[str], **kwargs):
    return run_reference_attempt(
        lineage=LINEAGE,
        spec_digest=_digest(repo),
        agent_command=command,
        task_id=TASK,
        state_dir=tmp_path / "state",
        cwd=repo.root,
        **kwargs,
    )


def test_a_headless_exit_zero_is_done_and_binds_the_snapshot(repo, tmp_path):
    panes = FakePanes()
    result = _run(
        repo,
        tmp_path,
        ["/bin/sh", "-c", "echo reference-impl > answer.txt; echo log-canary; exit 0"],
        headless=True,
        panes=panes,
    )
    assert result.done and result.state is S.DONE
    assert result.mode is ReferenceMode.HEADLESS
    assert result.reference_tree is not None
    store = _store(repo, tmp_path)
    bound = store.load_reference(LINEAGE)
    assert bound == (_digest(repo), result.reference_tree)
    log = store.reference_log_path(result.attempt_id).read_text()
    assert "log-canary" in log
    exported = tmp_path / "exported"
    repo.export(result.reference_tree, exported)
    assert "reference-impl" in (exported / "answer.txt").read_text()
    assert panes.opened == []


def test_a_headless_non_zero_exit_is_failed_and_keeps_no_snapshot(repo, tmp_path):
    result = _run(
        repo,
        tmp_path,
        ["/bin/sh", "-c", "echo nope > answer.txt; exit 3"],
        headless=True,
        panes=FakePanes(),
    )
    assert result.state is S.FAILED
    assert result.exit_code == 3
    assert result.reference_tree is None
    assert _store(repo, tmp_path).load_reference(LINEAGE) is None


def test_a_tui_exit_without_reference_done_fails_and_closes_the_pane(repo, tmp_path):
    panes = FakePanes()
    result = _run(
        repo,
        tmp_path,
        ["/bin/true"],
        panes=panes,
        host_env={"TMUX": "canary"},
    )
    assert result.mode is ReferenceMode.TUI
    assert result.state is S.FAILED
    assert result.reference_tree is None
    assert len(panes.opened) == 1
    assert panes.closed == ["%7"]
    assert _store(repo, tmp_path).load_reference(LINEAGE) is None


def test_reference_done_stops_the_tui_and_snapshots_it(repo, tmp_path):
    script = (
        "import http.client, json, os, socket\n"
        "class Conn(http.client.HTTPConnection):\n"
        "    def connect(self):\n"
        "        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "        self.sock.connect(os.environ['CHEESELESS_SOCKET'])\n"
        "c = Conn('localhost')\n"
        "c.request('POST', '/reference-done',\n"
        "          json.dumps({'taskId': os.environ['CHEESELESS_TASK']}),\n"
        "          {'Content-Type': 'application/json',\n"
        "           'x-cheeseless-role': 'reference'})\n"
        "r = c.getresponse()\n"
        "print(r.status, r.read(), flush=True)\n"
        "import time; time.sleep(30)\n"
    )
    panes = FakePanes()
    result = _run(
        repo,
        tmp_path,
        [PYTHON, "-I", "-c", script],
        panes=panes,
        host_env={"TMUX": "canary"},
        timeout=30,
    )
    assert result.state is S.DONE, result.problem
    assert result.reference_tree is not None
    assert result.pane_id == "%7" and panes.closed == ["%7"]
    assert _store(repo, tmp_path).load_reference(LINEAGE) == (_digest(repo), result.reference_tree)


def test_a_stale_attempt_is_reaped_before_a_new_one_starts(repo, tmp_path):
    store = _store(repo, tmp_path)
    stale = ReferenceAttempt(
        "att-stale",
        "0123456789abcdef0123456789abcdef",
        0,
        _digest(repo),
        ReferenceMode.TUI,
        False,
        S.RUNNING,
        LINEAGE,
        "%9",
    )
    store.save_reference_attempt(stale)
    panes = FakePanes()
    result = _run(
        repo,
        tmp_path,
        ["/bin/sh", "-c", "exit 0"],
        headless=True,
        panes=panes,
    )
    assert result.done
    reaped = store.reference_attempt("att-stale")
    assert reaped is not None and reaped.state is S.FAILED
    assert panes.closed == ["%9"]


def test_an_attempt_outside_an_open_dev_session_is_refused(repo, tmp_path):
    store = _store(repo, tmp_path)
    store.save_dev_state(DevState("other"))
    with pytest.raises(Exception, match="dev session"):
        run_reference_attempt(
            lineage="other",
            spec_digest=_digest(repo),
            agent_command=["/bin/true"],
            task_id=TASK,
            state_dir=tmp_path / "state",
            cwd=repo.root,
            headless=True,
            panes=FakePanes(),
        )
