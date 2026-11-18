"""The host loop of ``cheeseless dev``: preconditions and phase handover (ADR 0013).

The loop itself owns no trust decision of its own: it sequences the moves the domain
already forbids or allows, and refuses before anything is staged. Everything here is the
*host* side, so an agent can never reach these functions.
"""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dev_flow import (
    DevFlowError,
    require_clean_tree,
    require_token,
    start_phase,
)
from cheeseless.app.dev_session import open_session

SPEC = "1" * 64
PID = "d" * 64
AUTHOR = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"
IMPL = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


# -- preconditions -----------------------------------------------------------


def test_a_dirty_tree_is_refused() -> None:
    with pytest.raises(DevFlowError, match="clean"):
        require_clean_tree(lambda: False)


def test_a_clean_tree_passes() -> None:
    require_clean_tree(lambda: True)


def test_a_missing_token_names_the_command_that_makes_one() -> None:
    with pytest.raises(DevFlowError, match="setup-token"):
        require_token(None)


def test_an_empty_token_is_refused() -> None:
    with pytest.raises(DevFlowError):
        require_token("   ")


def test_a_credential_is_returned_unchanged() -> None:
    supplied = "placeholder-value"
    assert require_token(supplied) == supplied


# -- phase starts ------------------------------------------------------------


def test_the_spec_phase_starts_an_author_session(store: FileStore) -> None:
    open_session(store, "demo", resume=False)
    start = start_phase(store, "demo")
    assert start.role == "author"
    assert start.dev_lineage == "demo"
    assert start.reference is False


def test_the_implement_phase_starts_a_fresh_implementer(store: FileStore) -> None:
    state = open_session(store, "demo", resume=False)[0]
    saved = state.author_started(AUTHOR).spec_done(SPEC).proposed(PID).frozen(PID)
    store.save_dev_state(saved)
    start = start_phase(store, "demo")
    assert start.role == "implementer"
    assert start.dev_lineage is None
    assert start.session is None  # never a resume


def test_a_finished_phase_cannot_start_a_session(store: FileStore) -> None:
    state = open_session(store, "demo", resume=False)[0]
    store.save_dev_state(state.aborted())
    with pytest.raises(DevFlowError):
        start_phase(store, "demo")


def test_a_blocked_phase_is_refused_with_the_reason(store: FileStore) -> None:
    state = open_session(store, "demo", resume=False)[0]
    blocked = state.spec_done(SPEC).proposed(PID).frozen(PID).pool_empty()
    store.save_dev_state(blocked)
    with pytest.raises(DevFlowError, match="grant"):
        start_phase(store, "demo")


def test_starting_an_unknown_lineage_is_refused(store: FileStore) -> None:
    with pytest.raises(DevFlowError, match="no dev session"):
        start_phase(store, "demo")


def test_the_resumed_author_keeps_its_session(store: FileStore) -> None:
    state = open_session(store, "demo", resume=False)[0]
    store.save_dev_state(state.author_started(AUTHOR).spec_done(SPEC))
    start = start_phase(store, "demo")
    assert start.role == "author"
    assert start.session == AUTHOR


def test_a_tests_phase_start_asks_for_a_reference_attempt(store: FileStore) -> None:
    state = open_session(store, "demo", resume=False)[0]
    store.save_dev_state(state.author_started(AUTHOR).spec_done(SPEC))
    start = start_phase(store, "demo")
    assert start.reference is True
    assert start.spec_digest == SPEC


def test_the_spec_phase_never_asks_for_a_reference(store: FileStore) -> None:
    open_session(store, "demo", resume=False)
    assert start_phase(store, "demo").reference is False


def test_the_precondition_takes_the_clean_fact_not_a_dirty_flag() -> None:
    """A `dirty` parameter read through `not` is a sign error waiting to happen.

    The repository port exposes `is_clean()`, so requiring a `dirty` callable made every
    caller write `lambda: not repo.is_clean()` and one E2E test got it backwards.
    """
    import inspect

    from cheeseless.app import dev_flow

    params = inspect.signature(dev_flow.require_clean_tree).parameters
    assert list(params) == ["is_clean"]
    # The guard must refuse when the fact is false, not when it is true.
    docstring = dev_flow.require_clean_tree.__doc__ or ""
    source = inspect.getsource(dev_flow.require_clean_tree).replace(docstring, "")
    assert "if not clean:" in source
