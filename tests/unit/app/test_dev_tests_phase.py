"""Whether the dev tests phase may close (ADR 0013 stage 5, slice 2).

The predicate only *reads*: a reference attempt and a crosscheck already in the store.
It must tell a red or missing reference apart from a stale crosscheck, because the loop
reports the reason to the user and a wrong reason sends them to the wrong fix.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dev_flow import DevTestsPhaseError, reference_ready
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)

SPEC = "1" * 64
OTHER = "2" * 64
ATTEMPT = "att-1"
LINEAGE = "demo"
S = ReferenceState


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def _attempt(state: ReferenceState, digest: str = SPEC) -> ReferenceAttempt:
    return ReferenceAttempt(
        ATTEMPT,
        "0b7d4c1e2f3a4b5c8d9e0f1a2b3c4d5e",
        1,
        digest,
        ReferenceMode.TUI,
        False,
        state,
        lineage_id=LINEAGE,
    )


def _save(store: FileStore, state: ReferenceState, digest: str = SPEC) -> None:
    store.save_reference_attempt(_attempt(state, digest))


def test_a_missing_attempt_is_refused(store: FileStore) -> None:
    with pytest.raises(DevTestsPhaseError, match="no reference"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_running_attempt_is_not_a_pass(store: FileStore) -> None:
    _save(store, S.RUNNING)
    with pytest.raises(DevTestsPhaseError, match="running"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_failed_attempt_is_refused_with_its_state(store: FileStore) -> None:
    _save(store, S.FAILED)
    with pytest.raises(DevTestsPhaseError, match="failed"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_cancelled_attempt_is_refused(store: FileStore) -> None:
    _save(store, S.CANCELLED)
    with pytest.raises(DevTestsPhaseError, match="cancelled"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_done_attempt_for_another_digest_is_stale(store: FileStore) -> None:
    _save(store, S.DONE, OTHER)
    with pytest.raises(DevTestsPhaseError, match="stale|another spec"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_done_attempt_without_a_snapshot_is_refused(store: FileStore) -> None:
    _save(store, S.DONE)
    with pytest.raises(DevTestsPhaseError, match="snapshot"):
        reference_ready(store, LINEAGE, SPEC)


def test_a_done_bound_attempt_passes(store: FileStore) -> None:
    owner = AttemptOwner("0b7d4c1e2f3a4b5c8d9e0f1a2b3c4d5e", 1, ReferenceMode.TUI)
    store.save_reference_attempt(replace(_attempt(S.STOPPING), pane_id="%3"))
    store.transition_reference_attempt(
        ATTEMPT, S.STOPPING, S.DONE, owner=owner, result_tree="a" * 40
    )
    assert reference_ready(store, LINEAGE, SPEC) is True


def test_the_predicate_writes_nothing(store: FileStore, tmp_path: Path) -> None:
    _save(store, S.DONE)
    root = tmp_path / "state"
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(DevTestsPhaseError):
        reference_ready(store, LINEAGE, SPEC)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
