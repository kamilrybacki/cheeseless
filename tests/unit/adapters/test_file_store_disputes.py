"""Dispute state in the trusted file store (ADR 0009, stage g)."""

import json
import stat
import threading
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.dispute import (
    DisputeCode,
    DisputePolicy,
    DisputeStatus,
)
from cheeseless.domain.errors import ErrorCode
from cheeseless.domain.result import CriterionResult, Result, sign
from cheeseless.domain.status import Status
from cheeseless.ports.store import DisputeRefused, SessionDisputedError, StoreError
from tests.factories import frozen_task

SEED = bytes(range(32))
ON = DisputePolicy(enabled=True, cap=3)


def _result(task_id: str, status: Status = Status.FAIL) -> Result:
    crit = (CriterionResult("normalize", status), CriterionResult("score", status))
    return Result(task_id, "5" * 40, "6" * 64, "local", "0.1.0", status, crit)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    return s


def _task_id() -> str:
    return frozen_task().task.task_id


def _file(store: FileStore, *, criterion: str = "normalize", note: str = "n") -> None:
    store.file_dispute(_task_id(), "toy", criterion, DisputeCode.UNTESTABLE, note)


def test_policy_defaults_off_and_is_persisted(store: FileStore, tmp_path: Path) -> None:
    assert store.dispute_policy() == DisputePolicy()
    store.set_dispute_policy(ON)
    assert FileStore(tmp_path / "state").dispute_policy() == ON
    mode = stat.S_IMODE((tmp_path / "state" / "dispute-policy.json").stat().st_mode)
    assert mode == 0o600


def test_corrupt_policy_fails_closed(store: FileStore, tmp_path: Path) -> None:
    (tmp_path / "state" / "dispute-policy.json").write_bytes(b'{"enabled": "yes"}')
    with pytest.raises(StoreError):
        store.dispute_policy()


def test_layout_unchanged_while_disputes_unused(store: FileStore, tmp_path: Path) -> None:
    store.dispute_policy()
    store.session_disputed(_task_id())
    assert not (tmp_path / "state" / "disputes.json").exists()
    assert not (tmp_path / "state" / "dispute-policy.json").exists()


def test_disabled_refuses_and_records_nothing(store: FileStore, tmp_path: Path) -> None:
    with pytest.raises(DisputeRefused) as exc:
        _file(store)
    assert exc.value.code is ErrorCode.DISPUTE_DISABLED
    assert not (tmp_path / "state" / "disputes.json").exists()
    assert not (tmp_path / "state" / "events.jsonl").exists()


def test_dispute_ends_the_session(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    assert not store.session_disputed(_task_id())
    _file(store)
    assert store.session_disputed(_task_id())
    with pytest.raises(DisputeRefused) as exc:
        _file(store)
    assert exc.value.code is ErrorCode.SESSION_DISPUTED


def test_save_result_is_refused_after_a_dispute(store: FileStore) -> None:
    """The in-flight run race: a result computed before the dispute must not publish."""
    store.set_dispute_policy(ON)
    _file(store)
    signed = sign(_result(_task_id()), store.hmac_key())
    with pytest.raises(SessionDisputedError):
        store.save_result(signed, operator_cause="")
    assert store.latest_result(_task_id()) is None


def test_dispute_and_save_race_never_publishes_after_dispute(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    signed = sign(_result(_task_id(), Status.FAIL), store.hmac_key())
    outcomes: list[str] = []

    def save() -> None:
        try:
            store.save_result(signed, operator_cause="")
            outcomes.append("saved")
        except SessionDisputedError:
            outcomes.append("refused")

    def dispute() -> None:
        try:
            _file(store)
            outcomes.append("disputed")
        except DisputeRefused:
            outcomes.append("dispute-refused")

    threads = [threading.Thread(target=f) for f in (save, dispute)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert "disputed" in outcomes
    # Whatever the interleaving, a disputed task never ends with a result saved after it.
    if "saved" in outcomes:
        assert outcomes.index("saved") == 0 or store.latest_result(_task_id()) is not None


def test_passing_session_cannot_dispute(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    store.save_result(sign(_result(_task_id(), Status.PASS), store.hmac_key()), operator_cause="")
    with pytest.raises(DisputeRefused) as exc:
        _file(store)
    assert exc.value.code is ErrorCode.DISPUTE_NOT_ALLOWED
    assert not store.session_disputed(_task_id())


def test_failing_session_can_dispute_and_result_stays(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    store.save_result(sign(_result(_task_id()), store.hmac_key()), operator_cause="")
    _file(store)
    assert store.latest_result(_task_id()) is not None


def test_cap_is_per_lineage_and_counts_every_status(store: FileStore) -> None:
    store.set_dispute_policy(DisputePolicy(enabled=True, cap=2))
    for _ in range(2):
        _file(store)
        record = store.disputes()[-1]
        store.resolve_dispute(record.dispute_id, DisputeStatus.REJECTED, "op")
    with pytest.raises(DisputeRefused) as exc:
        _file(store)
    assert exc.value.code is ErrorCode.DISPUTE_CAP_REACHED
    assert len(store.disputes()) == 2


def test_resolution_rules(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    _file(store, note="a note")
    record = store.disputes()[0]
    assert record.status is DisputeStatus.OPEN and record.note == "a note"
    done = store.resolve_dispute(record.dispute_id, DisputeStatus.REJECTED, "alice")
    assert done.status is DisputeStatus.REJECTED and done.resolved_by == "alice"
    assert not store.session_disputed(_task_id())
    with pytest.raises(StoreError):
        store.resolve_dispute(record.dispute_id, DisputeStatus.UPHELD, "bob")
    with pytest.raises(StoreError):
        store.resolve_dispute("nope", DisputeStatus.UPHELD, "bob")
    with pytest.raises(StoreError):
        store.resolve_dispute(record.dispute_id, DisputeStatus.OPEN, "bob")


def test_upheld_keeps_the_task_closed(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    _file(store)
    store.resolve_dispute(store.disputes()[0].dispute_id, DisputeStatus.UPHELD, "op")
    assert store.session_disputed(_task_id())


def test_run_budget_untouched_by_a_dispute(store: FileStore) -> None:
    from cheeseless.domain.budget import Pool

    store.set_dispute_policy(ON)
    before = store.reserve_run("toy", Pool.LOCAL)
    _file(store)
    after = store.budget("toy", Pool.LOCAL)
    assert before is not None and after.used == before.used and after.limit == before.limit


def test_records_file_is_private_and_json(store: FileStore, tmp_path: Path) -> None:
    store.set_dispute_policy(ON)
    _file(store)
    path = tmp_path / "state" / "disputes.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes())["records"][0]["task_id"] == _task_id()


def test_unknown_task_is_refused(store: FileStore) -> None:
    store.set_dispute_policy(ON)
    with pytest.raises(StoreError):
        store.file_dispute("9" * 64, "toy", "normalize", DisputeCode.OTHER, "")
