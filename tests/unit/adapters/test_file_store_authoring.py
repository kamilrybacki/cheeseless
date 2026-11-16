"""Authoring records in the trusted file store (ADR 0009, stages d and f)."""

import stat
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.authoring import AuthoringState, CrosscheckRecord, Proposal
from cheeseless.ports.store import StoreError

OID = "a" * 40
SPEC = "1" * 64
PID = "d" * 64


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def test_state_defaults_to_authoring_and_round_trips(store: FileStore) -> None:
    fresh = store.authoring_state("demo", SPEC)
    assert fresh == AuthoringState("demo", SPEC)
    blocked = fresh.reference_failed(SPEC)
    store.save_authoring_state(blocked)
    assert store.authoring_state("demo", SPEC) == blocked


def test_state_for_edited_spec_is_reset(store: FileStore) -> None:
    store.save_authoring_state(AuthoringState("demo", SPEC).reference_failed(SPEC))
    other = store.authoring_state("demo", "2" * 64)
    assert other.reference_failures == 0
    assert other.spec_digest == "2" * 64


def test_proposal_round_trip_and_unknown(store: FileStore) -> None:
    p = Proposal(PID, "demo", OID, OID, SPEC)
    assert store.load_proposal(PID) is None
    store.save_proposal(p)
    assert store.load_proposal(PID) == p


def test_reference_snapshot_round_trip(store: FileStore) -> None:
    assert store.load_reference("demo") is None
    store.save_reference("demo", SPEC, OID)
    assert store.load_reference("demo") == (SPEC, OID)


def test_crosscheck_round_trip(store: FileStore) -> None:
    rec = CrosscheckRecord(OID, SPEC, OID, OID, False, ("c1",))
    assert store.load_crosscheck(PID) is None
    store.save_crosscheck(PID, rec)
    assert store.load_crosscheck(PID) == rec


def test_files_are_private_and_lazy(store: FileStore, tmp_path: Path) -> None:
    assert not (store.root / "authoring").exists()
    store.save_authoring_state(AuthoringState("demo", SPEC))
    mode = stat.S_IMODE((store.root / "authoring").stat().st_mode)
    assert mode == 0o700


def test_corrupt_record_fails_closed(store: FileStore) -> None:
    store.save_proposal(Proposal(PID, "demo", OID, OID, SPEC))
    (store.root / "authoring" / f"proposal-{PID}.json").write_bytes(b"{nope")
    with pytest.raises(StoreError):
        store.load_proposal(PID)


def test_bad_ids_rejected(store: FileStore) -> None:
    with pytest.raises(StoreError):
        store.load_proposal("../x")
    with pytest.raises(StoreError):
        store.authoring_state("../x", SPEC)


def test_waiver_reason_round_trips_and_old_tasks_decode(store: FileStore) -> None:
    import dataclasses

    from tests.factories import frozen_task

    seed = bytes(range(32))
    plain = frozen_task()
    store.save_task(plain, seed)
    assert store.load_task(plain.task.task_id)[0].waiver == ""
    assert b"waiver" not in (store.root / "tasks" / f"{plain.task.task_id}.json").read_bytes()
    waived = dataclasses.replace(plain, waiver="reference agent unavailable")
    store.save_task(waived, seed)
    assert store.load_task(plain.task.task_id)[0].waiver == "reference agent unavailable"


def _waived(store: FileStore) -> tuple[str, Path]:
    import dataclasses

    from tests.factories import frozen_task

    plain = frozen_task()
    waived = dataclasses.replace(plain, waiver="reference agent unavailable")
    store.save_task(waived, bytes(range(32)))
    task_id = plain.task.task_id
    return task_id, store.root / "tasks" / f"{task_id}.json"


def test_editing_the_waiver_on_disk_is_detected(store: FileStore) -> None:
    task_id, path = _waived(store)
    path.write_bytes(path.read_bytes().replace(b"unavailable", b"inconvenient"))
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(task_id)


def test_removing_the_waiver_mac_or_adding_a_waiver_is_detected(store: FileStore) -> None:
    import json

    task_id, path = _waived(store)
    data = json.loads(path.read_bytes())
    stripped = {k: v for k, v in data.items() if k != "waiver_mac"}
    path.write_bytes(json.dumps(stripped).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(task_id)
    # A MAC-less waiver injected into a task that had none is refused too.
    from tests.factories import frozen_task

    other = frozen_task()
    store.save_task(other, bytes(range(32)))
    p2 = store.root / "tasks" / f"{other.task.task_id}.json"
    d2 = json.loads(p2.read_bytes())
    d2["waiver"] = "forged"
    p2.write_bytes(json.dumps(d2).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(other.task.task_id)
