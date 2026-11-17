"""Opening or resuming a ``cheeseless dev`` session (ADR 0013)."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dev_session import open_session
from cheeseless.domain.dev_session import DevPhase, DevSessionError, DevState, ResumeTarget

SPEC = "1" * 64
PID = "d" * 64
AUTHOR = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"
IMPL = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def test_a_new_session_is_saved_in_the_spec_phase(store: FileStore) -> None:
    state, target = open_session(store, "demo", resume=False)
    assert state.phase is DevPhase.SPEC and state.version == 1
    assert target == ResumeTarget("author", None)
    assert store.dev_state("demo") == state


def test_a_new_session_is_refused_while_one_is_open(store: FileStore) -> None:
    open_session(store, "demo", resume=False)
    with pytest.raises(DevSessionError, match="--resume"):
        open_session(store, "demo", resume=False)


def test_a_new_session_may_replace_a_finished_one(store: FileStore) -> None:
    first, _ = open_session(store, "demo", resume=False)
    store.save_dev_state(first.aborted())
    state, _ = open_session(store, "demo", resume=False)
    assert state.phase is DevPhase.SPEC and state.version == 3


def test_resume_without_a_session_is_refused(store: FileStore) -> None:
    with pytest.raises(DevSessionError, match="nothing to resume"):
        open_session(store, "demo", resume=True)


def test_resume_returns_the_author_session(store: FileStore) -> None:
    first, _ = open_session(store, "demo", resume=False)
    store.save_dev_state(first.author_started(AUTHOR).spec_done(SPEC))
    state, target = open_session(store, "demo", resume=True)
    assert state.phase is DevPhase.TESTS
    assert target == ResumeTarget("author", AUTHOR)


def test_resume_while_implementing_never_reuses_a_session(store: FileStore) -> None:
    first, _ = open_session(store, "demo", resume=False)
    implementing = (
        first.author_started(AUTHOR).spec_done(SPEC).proposed(PID).frozen(PID)
    ).implementer_started(IMPL)
    store.save_dev_state(implementing)
    _, target = open_session(store, "demo", resume=True)
    assert target == ResumeTarget("implementer", None)


def test_resume_of_a_blocked_session_is_refused(store: FileStore) -> None:
    first, _ = open_session(store, "demo", resume=False)
    blocked = first.spec_done(SPEC).proposed(PID).frozen(PID).pool_empty()
    store.save_dev_state(blocked)
    with pytest.raises(DevSessionError, match="grant"):
        open_session(store, "demo", resume=True)


def test_the_state_is_not_shared_between_lineages(store: FileStore) -> None:
    open_session(store, "demo", resume=False)
    assert store.dev_state("other") is None
    assert isinstance(store.dev_state("demo"), DevState)
