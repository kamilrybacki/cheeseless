"""ADR 0014: ``gate_reference_done`` is a single compare-and-set running -> stopping."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.reference_gate import ReferenceStopping, gate_reference_done
from cheeseless.domain.errors import ErrorCode, GateError
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)

S = ReferenceState
SID = "0123456789abcdef0123456789abcdef"
OWNER = AttemptOwner(SID, 0, ReferenceMode.TUI)


def _attempt(state: ReferenceState) -> ReferenceAttempt:
    return ReferenceAttempt("att-1", SID, 0, "a" * 64, ReferenceMode.TUI, False, state)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def _path(store: FileStore) -> Path:
    return store._root / "dev-reference" / "att-1.json"


def test_a_running_attempt_moves_to_stopping_and_calls_the_seam_once(store):
    store.save_reference_attempt(_attempt(S.RUNNING))
    calls: list[str] = []
    out = gate_reference_done(store, "att-1", OWNER, calls.append)
    assert out == ReferenceStopping("att-1")
    assert store.reference_attempt("att-1").state is S.STOPPING
    assert calls == ["att-1"]


@pytest.mark.parametrize("state", [S.STOPPING, S.DONE, S.FAILED, S.CANCELLED])
def test_a_non_running_attempt_is_refused_and_left_unchanged(store, state):
    store.save_reference_attempt(_attempt(state))
    before = _path(store).read_bytes()
    calls: list[str] = []
    out = gate_reference_done(store, "att-1", OWNER, calls.append)
    assert isinstance(out, GateError) and out.code is ErrorCode.REFERENCE_REFUSED
    assert _path(store).read_bytes() == before
    assert calls == []


def test_another_owner_is_refused_and_left_unchanged(store):
    store.save_reference_attempt(_attempt(S.RUNNING))
    before = _path(store).read_bytes()
    out = gate_reference_done(store, "att-1", AttemptOwner(SID, 1, ReferenceMode.TUI))
    assert isinstance(out, GateError) and out.code is ErrorCode.REFERENCE_REFUSED
    assert _path(store).read_bytes() == before


def test_a_store_failure_is_an_infra_error_without_raw_text(store):
    class Broken:
        def transition_reference_attempt(self, *a, **k):
            raise RuntimeError("CANARY-HIDDEN-9c1e44")

    out = gate_reference_done(Broken(), "att-1", OWNER)  # type: ignore[arg-type]
    assert isinstance(out, GateError) and out.code is ErrorCode.INFRA_ERROR
    assert "CANARY" not in repr(out)


def test_a_failing_seam_does_not_undo_the_move(store):
    store.save_reference_attempt(_attempt(S.RUNNING))

    def boom(_: str) -> None:
        raise RuntimeError("seam")

    assert gate_reference_done(store, "att-1", OWNER, boom) == ReferenceStopping("att-1")
    assert store.reference_attempt("att-1").state is S.STOPPING
