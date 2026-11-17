"""ADR 0014: ``dev-reference/<attempt_id>.json`` with compare-and-set transitions."""

import threading
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
    ReferenceTransitionError,
    TransitionRefused,
)
from cheeseless.ports.store import StoreError

S = ReferenceState
A = ReferenceAttempt(
    "att-1", "0123456789abcdef0123456789abcdef", 0, "a" * 64, ReferenceMode.TUI, False, S.RUNNING
)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def test_load_of_an_unknown_attempt_is_none(store):
    assert store.reference_attempt("nope") is None


def test_save_then_load_round_trips_at_the_documented_path(store):
    store.save_reference_attempt(A)
    assert store.reference_attempt("att-1") == A
    assert (store._root / "dev-reference" / "att-1.json").is_file()


def test_save_never_overwrites_an_existing_attempt(store):
    store.save_reference_attempt(A)
    with pytest.raises(StoreError):
        store.save_reference_attempt(A)


def test_an_invalid_id_is_a_store_error(store):
    with pytest.raises(StoreError):
        store.reference_attempt("../state")


def test_a_corrupt_record_is_a_store_error(store):
    store.save_reference_attempt(A)
    (store._root / "dev-reference" / "att-1.json").write_text("{}")
    with pytest.raises(StoreError):
        store.reference_attempt("att-1")


def test_cas_moves_the_state_when_it_matches(store):
    store.save_reference_attempt(A)
    out = store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING)
    assert isinstance(out, ReferenceAttempt) and out.state is S.STOPPING
    assert store.reference_attempt("att-1") == out


def test_cas_refuses_a_stale_expected_state_and_changes_nothing(store):
    store.save_reference_attempt(A)
    store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING)
    path = store._root / "dev-reference" / "att-1.json"
    before = path.read_bytes()
    out = store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING)
    assert out == TransitionRefused("att-1", S.RUNNING, S.STOPPING)
    assert path.read_bytes() == before


def test_cas_on_an_unknown_attempt_is_refused_with_no_actual_state(store):
    out = store.transition_reference_attempt("nope", S.RUNNING, S.STOPPING)
    assert out == TransitionRefused("nope", S.RUNNING, None)


def test_cas_rejects_a_transition_the_machine_forbids(store):
    store.save_reference_attempt(A)
    with pytest.raises(ReferenceTransitionError):
        store.transition_reference_attempt("att-1", S.RUNNING, S.DONE)
    assert store.reference_attempt("att-1") == A


def test_exactly_one_of_two_concurrent_cas_calls_wins(store):
    store.save_reference_attempt(A)
    barrier = threading.Barrier(2)
    results: list[object] = []

    def race() -> None:
        barrier.wait()
        results.append(store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING))

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [r for r in results if isinstance(r, ReferenceAttempt)]
    losses = [r for r in results if isinstance(r, TransitionRefused)]
    assert len(wins) == 1 and len(losses) == 1


@pytest.mark.parametrize(
    "owner",
    [
        AttemptOwner("f" * 32, 0, ReferenceMode.TUI),
        AttemptOwner(A.session_id, 1, ReferenceMode.TUI),
        AttemptOwner(A.session_id, 0, ReferenceMode.HEADLESS),
    ],
)
def test_cas_refuses_another_owner_under_the_lock_and_changes_nothing(store, owner):
    store.save_reference_attempt(A)
    path = store._root / "dev-reference" / "att-1.json"
    before = path.read_bytes()
    out = store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING, owner=owner)
    assert out == TransitionRefused("att-1", S.RUNNING, S.RUNNING)
    assert path.read_bytes() == before


def test_cas_with_the_matching_owner_moves_the_state(store):
    store.save_reference_attempt(A)
    owner = AttemptOwner(A.session_id, A.generation, A.mode)
    out = store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING, owner=owner)
    assert isinstance(out, ReferenceAttempt) and out.state is S.STOPPING
