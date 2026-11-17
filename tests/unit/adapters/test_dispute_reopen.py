"""G12: a rejected dispute reopens the task for a NEW session with a fresh attempt budget."""

import json
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dispute import configure_disputes, gate_dispute, resolve_dispute
from cheeseless.domain.errors import ErrorCode, GateError
from tests.factories import frozen_task

SEED = bytes(range(32))
WINDOW = 3600.0


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    configure_disputes(s, enabled=True, cap=3)
    return s


def _tid() -> str:
    return frozen_task().task.task_id


def _file(store: FileStore) -> str:
    assert not isinstance(
        gate_dispute(store, task_id=_tid(), criterion_id="normalize", code="untestable", note="n"),
        GateError,
    )
    return store.disputes()[-1].dispute_id


def _events(store: FileStore, root: Path) -> list[dict]:
    path = root / "state" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_new_task_is_generation_zero(store: FileStore):
    assert store.session_generation(_tid()) == 0


def test_rejection_starts_a_new_generation_and_clears_the_attempt_window(
    store: FileStore, tmp_path: Path
):
    for _ in range(4):
        store.record_attempt(_tid(), 1000.0, WINDOW)
    did = _file(store)
    resolve_dispute(store, did, upheld=False, actor="alice")
    assert store.session_generation(_tid()) == 1
    # The old attempts no longer count: the first attempt of the new session is attempt 1.
    assert store.record_attempt(_tid(), 1001.0, WINDOW) == 1


def test_upheld_changes_neither_the_generation_nor_the_attempts(store: FileStore):
    for _ in range(4):
        store.record_attempt(_tid(), 1000.0, WINDOW)
    did = _file(store)
    resolve_dispute(store, did, upheld=True, actor="alice")
    assert store.session_generation(_tid()) == 0
    assert store.record_attempt(_tid(), 1001.0, WINDOW) == 5
    assert store.session_disputed(_tid())


def test_the_rejection_is_audited_with_who_when_and_the_new_budget(
    store: FileStore, tmp_path: Path
):
    did = _file(store)
    resolve_dispute(store, did, upheld=False, actor="alice")
    reopen = [e for e in _events(store, tmp_path) if e["kind"] == "dispute-reopen"]
    assert len(reopen) == 1
    event = reopen[0]
    assert event["subject"] == _tid() and event["time"] is not None
    assert did in event["cause"] and "alice" in event["cause"]
    assert "generation=1" in event["cause"]
    budget = [e for e in _events(store, tmp_path) if e["kind"] == "dispute-reopen-budget"]
    assert len(budget) == 1 and "attempts=" in budget[0]["cause"]
    assert "run-budget=unchanged" in budget[0]["cause"]


def test_a_rejection_cannot_be_applied_twice(store: FileStore):
    did = _file(store)
    resolve_dispute(store, did, upheld=False, actor="alice")
    with pytest.raises(Exception, match="already resolved"):
        resolve_dispute(store, did, upheld=False, actor="alice")
    assert store.session_generation(_tid()) == 1


def test_the_lineage_cap_still_counts_a_rejected_dispute(store: FileStore):
    configure_disputes(store, enabled=True, cap=1)
    resolve_dispute(store, _file(store), upheld=False, actor="alice")
    refused = gate_dispute(
        store, task_id=_tid(), criterion_id="normalize", code="untestable", note="n"
    )
    assert isinstance(refused, GateError) and refused.code is ErrorCode.DISPUTE_CAP_REACHED


def test_the_lineage_run_budget_is_not_reset(store: FileStore):
    """The run budget bounds hidden-test leakage, so a rejection must not refill it."""
    from cheeseless.domain.budget import Pool

    lineage = frozen_task().task.lineage_id
    store.reserve_run(lineage, Pool.LOCAL)
    before = store.budget(lineage, Pool.LOCAL)
    resolve_dispute(store, _file(store), upheld=False, actor="alice")
    assert store.budget(lineage, Pool.LOCAL) == before
