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
from cheeseless.session_options import SessionOptionError
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


# -- ADR 0014 slice 3c: the attempt is bound by the host and the tool follows the mode --

from cheeseless.domain.dev_session import DevState  # noqa: E402
from cheeseless.domain.reference_attempt import (  # noqa: E402
    NewReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
DIGEST = "a" * 64
ATTEMPT = "att-wiring"
LINEAGE = "toy"
TASK = "a" * 64


@pytest.fixture
def repo(tmp_path) -> GitRepository:
    return GitRepository.discover(make_repo(tmp_path / "repo"))


def _store(repo: GitRepository, tmp_path: Path) -> FileStore:
    return FileStore(store_root(repo.common_dir(), tmp_path / "state"))


def _with_dev(repo: GitRepository, tmp_path: Path) -> FileStore:
    store = _store(repo, tmp_path)
    store.save_dev_state(DevState(LINEAGE).spec_done(DIGEST))
    return store


def _reference_session(repo, tmp_path, mode, **kwargs):
    attempt = NewReferenceAttempt(ATTEMPT, LINEAGE, DIGEST, mode)
    return host_session(
        state_dir=tmp_path / "state",
        cwd=repo.root,
        task_id=TASK,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=["/bin/true"],
        role="reference",
        new_reference=attempt,
        **kwargs,
    )


def test_a_tui_reference_session_gets_the_tool_the_pty_and_the_seam(repo, tmp_path, monkeypatch):
    seen: dict[str, object] = {}
    real_stage, real_server = bootstrap._stage_runtime, bootstrap.UdsVerifierServer

    def stage(*args, **kwargs):
        runtime = real_stage(*args, **kwargs)
        seen["shim"] = runtime.mcp.read_text()
        return runtime

    def server(path, session, **kwargs):
        seen["dev_reference"] = session.dev_reference
        seen["seam"] = session.on_reference_stop
        return real_server(path, session, **kwargs)

    def launch(*_args, **_kwargs):
        raise _Launched

    monkeypatch.setattr(bootstrap, "_stage_runtime", stage)
    monkeypatch.setattr(bootstrap, "UdsVerifierServer", server)
    monkeypatch.setattr(bootstrap, "_launch_agent", launch)
    _with_dev(repo, tmp_path)

    def seam(_attempt_id: str) -> None:
        seen["stopped"] = True

    with pytest.raises(_Launched):
        with _reference_session(
            repo, tmp_path, ReferenceMode.TUI, agent_pty=(24, 80), on_reference_stop=seam
        ):
            pass
    assert "role='reference'" in str(seen["shim"])
    assert "dev=True" in str(seen["shim"])
    assert seen["dev_reference"] == ATTEMPT
    assert seen["seam"] is seam
    stored = _store(repo, tmp_path).reference_attempt(ATTEMPT)
    assert stored is not None and stored.state is ReferenceState.RUNNING


def test_a_headless_reference_session_gets_no_tool_and_keeps_a_log(repo, tmp_path, monkeypatch):
    seen: dict[str, object] = {}
    real_stage = bootstrap._stage_runtime

    def stage(*args, **kwargs):
        runtime = real_stage(*args, **kwargs)
        seen["shim"] = runtime.mcp.read_text()
        return runtime

    def launch(*_args, **_kwargs):
        raise _Launched

    monkeypatch.setattr(bootstrap, "_stage_runtime", stage)
    monkeypatch.setattr(bootstrap, "_launch_agent", launch)
    store = _with_dev(repo, tmp_path)
    log = store.reference_log_path(ATTEMPT)
    with pytest.raises(_Launched):
        with _reference_session(repo, tmp_path, ReferenceMode.HEADLESS, log_file=log):
            pass
    assert "role='reference'" in str(seen["shim"])
    assert "dev=" not in str(seen["shim"])
    assert log.is_file()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agent_pty": (24, 80)},  # headless mode with a pty
        {},  # headless mode without a log
        {"agent_pty": (24, 80), "log_file": None},
    ],
)
def test_a_mismatched_mode_is_refused_before_anything_starts(repo, tmp_path, kwargs):
    _with_dev(repo, tmp_path)
    mode = ReferenceMode.HEADLESS
    with pytest.raises(SessionOptionError, match="pty|log"):
        with _reference_session(repo, tmp_path, mode, **kwargs):
            pass


def test_a_pty_and_a_log_together_are_refused(repo, tmp_path):
    _with_dev(repo, tmp_path)
    log = _store(repo, tmp_path).reference_log_path(ATTEMPT)
    with pytest.raises(SessionOptionError, match="log"):
        with _reference_session(
            repo, tmp_path, ReferenceMode.TUI, agent_pty=(24, 80), log_file=log
        ):
            pass


def test_an_attempt_is_only_for_a_reference_session(repo, tmp_path):
    _with_dev(repo, tmp_path)
    attempt = NewReferenceAttempt(ATTEMPT, LINEAGE, DIGEST, ReferenceMode.HEADLESS)
    with pytest.raises(SessionOptionError, match="reference session"):
        with host_session(
            state_dir=tmp_path / "state",
            cwd=repo.root,
            task_id=TASK,
            projection_dir=tmp_path / "projection",
            socket_dir=tmp_path / "sockets",
            agent_command=["/bin/true"],
            role="author",
            new_reference=attempt,
        ):
            pass
