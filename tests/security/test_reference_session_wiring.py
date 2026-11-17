"""ADR 0009 through ``host_session``: the projection a reference session gets holds no test.

The agent launcher is replaced by a probe that snapshots the projection (worktree and
``.git``) at the moment the agent would start, then aborts the session. This proves the
role reaches the projection builder on the real session path, pre-freeze and frozen.
"""

import os
import subprocess
from pathlib import Path

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from tests.requirements import needs
from tests.toy import CANARY, make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/git", os.X_OK),
    "bwrap and git are required",
)

CONTRACT_TEST = "def test_starts("  # the toy's public contract test body


class _Launched(Exception):
    """Raised by the probe once the projection has been captured."""


def _snapshot(projection: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["/usr/bin/git", "-C", str(projection), *args],
            capture_output=True,
            text=True,
            errors="replace",
            check=True,
        ).stdout

    parts = [git("ls-files"), git("cat-file", "--batch-all-objects", "--batch")]
    for path in sorted(projection.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(projection).parts:
            parts.append(f"{path.relative_to(projection)}\n{path.read_text(errors='replace')}")
    return "\n".join(parts)


@pytest.fixture
def probe(monkeypatch, tmp_path):
    seen: dict[str, str] = {}
    projection = tmp_path / "projection"

    def launch(*_args, **_kwargs):
        seen["snapshot"] = _snapshot(projection)
        raise _Launched

    monkeypatch.setattr(bootstrap, "_launch_agent", launch)
    return projection, seen


def _run_reference(repo: GitRepository, state: Path, task_id: str, tmp_path: Path, probe) -> str:
    projection, seen = probe
    with pytest.raises(_Launched):
        with host_session(
            state_dir=state,
            cwd=repo.root,
            task_id=task_id,
            projection_dir=projection,
            socket_dir=tmp_path / "sockets",
            agent_command=["/bin/true"],
            role="reference",
        ):
            pass
    return seen["snapshot"]


def _assert_no_test(snapshot: str) -> None:
    assert CANARY not in snapshot  # hidden tests
    assert CONTRACT_TEST not in snapshot  # public contract tests
    assert "test_smoke.py" not in snapshot and "acceptance/hidden" not in snapshot
    assert "acceptance/contract/criteria.yaml" in snapshot  # the allowlist survives


def test_a_pre_freeze_reference_session_gets_no_test(tmp_path, probe):
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    snapshot = _run_reference(repo, tmp_path / "state", "a" * 64, tmp_path, probe)
    _assert_no_test(snapshot)


def test_a_frozen_reference_session_gets_no_test(tmp_path, probe):
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    task_id = store.active_task_id()
    assert task_id is not None
    snapshot = _run_reference(repo, tmp_path / "state", task_id, tmp_path, probe)
    _assert_no_test(snapshot)
