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


def test_host_bookkeeping_round_trips(store):
    attempt = ReferenceAttempt(
        "att-2",
        A.session_id,
        0,
        A.spec_digest,
        ReferenceMode.TUI,
        False,
        S.RUNNING,
        "toy",
        "%7",
    )
    store.save_reference_attempt(attempt)
    assert store.reference_attempt("att-2") == attempt


def test_list_filters_by_lineage_and_ignores_unknown_records(store):
    assert store.reference_attempts("toy") == ()
    toy = ReferenceAttempt(
        "att-2", A.session_id, 0, A.spec_digest, ReferenceMode.TUI, False, S.RUNNING, "toy"
    )
    store.save_reference_attempt(A)  # no lineage: never selected by a lineage query
    store.save_reference_attempt(toy)
    other = ReferenceAttempt(
        "att-3",
        A.session_id,
        0,
        A.spec_digest,
        ReferenceMode.HEADLESS,
        False,
        S.RUNNING,
        "other",
    )
    store.save_reference_attempt(other)
    assert {a.attempt_id for a in store.reference_attempts("toy")} == {"att-2"}
    assert {a.attempt_id for a in store.reference_attempts("other")} == {"att-3"}
    assert len(store.reference_attempts()) == 3


def test_bind_records_attach_and_pane_without_changing_state(store):
    store.save_reference_attempt(A)
    owner = AttemptOwner(A.session_id, A.generation, A.mode)
    out = store.bind_reference_attempt("att-1", owner=owner, attached=True, pane_id="%9")
    assert isinstance(out, ReferenceAttempt)
    assert out.state is S.RUNNING and out.attached is True and out.pane_id == "%9"
    assert store.reference_attempt("att-1") == out


def test_bind_refuses_another_owner_and_writes_nothing(store):
    store.save_reference_attempt(A)
    path = store._root / "dev-reference" / "att-1.json"
    before = path.read_bytes()
    out = store.bind_reference_attempt(
        "att-1",
        owner=AttemptOwner(A.session_id, 1, A.mode),
        attached=True,
        pane_id="%9",
    )
    assert out is None
    assert path.read_bytes() == before


def test_bind_with_nothing_to_change_returns_the_same_record(store):
    store.save_reference_attempt(A)
    owner = AttemptOwner(A.session_id, A.generation, A.mode)
    assert store.bind_reference_attempt("att-1", owner=owner) == A


def test_done_stores_the_result_tree_atomically(store):
    store.save_reference_attempt(A)
    store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING)
    tree = "b" * 40
    out = store.transition_reference_attempt("att-1", S.STOPPING, S.DONE, result_tree=tree)
    assert isinstance(out, ReferenceAttempt)
    assert out.state is S.DONE and out.result_tree == tree
    assert store.reference_attempt("att-1") == out


def test_a_result_tree_on_another_move_is_refused(store):
    store.save_reference_attempt(A)
    with pytest.raises(ValueError):
        store.transition_reference_attempt("att-1", S.RUNNING, S.STOPPING, result_tree="b" * 40)


def test_the_log_path_lives_in_a_private_directory(store):
    path = store.reference_log_path("att-1")
    assert path.parent.name == "logs" and path.parent.is_dir()
    assert path.parent.stat().st_mode & 0o777 == 0o700
