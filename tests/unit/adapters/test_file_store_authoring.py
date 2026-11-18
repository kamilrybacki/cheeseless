"""Authoring records in the trusted file store (ADR 0009, stages d and f)."""

import stat
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.authoring import (
    AuthoringError,
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    StrengthReview,
    TaskPhase,
)
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
    saved = store.save_authoring_state(blocked)
    assert store.authoring_state("demo", SPEC) == saved
    assert saved.phase is blocked.phase and saved.version == blocked.version + 1


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
    store.save_reference("demo", SPEC, OID, expected_version=0)
    assert store.load_reference("demo") == (SPEC, OID)


def test_crosscheck_round_trip(store: FileStore) -> None:
    rec = CrosscheckRecord(OID, SPEC, OID, OID, False, ("c1",))
    assert store.load_crosscheck(PID) is None
    store.save_crosscheck(PID, "demo", rec, expected_version=0)
    assert store.load_crosscheck(PID) == rec


def test_strength_review_round_trip(store: FileStore) -> None:
    rec = StrengthReview(PID, OID, SPEC, OID, True, "openai", "anthropic")
    assert store.load_strength_review(PID) is None
    store.save_strength_review(PID, "demo", rec, expected_version=0)
    assert store.load_strength_review(PID) == rec


def test_a_strength_review_is_conditional_on_the_state_version(store: FileStore) -> None:
    read = store.authoring_state("demo", SPEC)
    store.save_authoring_state(read.proposed())
    rec = StrengthReview(PID, OID, SPEC, OID, True, "openai", "anthropic")
    with pytest.raises(AuthoringError, match="changed"):
        store.save_strength_review(PID, "demo", rec, expected_version=read.version)
    assert store.load_strength_review(PID) is None


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
    import json

    on_disk = json.loads((store.root / "tasks" / f"{plain.task.task_id}.json").read_bytes())
    assert "waiver" not in on_disk and "waiver_mac" in on_disk
    waived = dataclasses.replace(plain, waiver="reference agent unavailable")
    other = FileStore(store.root.parent / "other-state")
    other.save_task(waived, seed)
    assert other.load_task(plain.task.task_id)[0].waiver == "reference agent unavailable"


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
    store = FileStore(store.root.parent / "other-state")  # same task id, separate store
    store.save_task(other, bytes(range(32)))
    p2 = store.root / "tasks" / f"{other.task.task_id}.json"
    d2 = json.loads(p2.read_bytes())
    d2["waiver"] = "forged"
    p2.write_bytes(json.dumps(d2).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(other.task.task_id)


def test_a_stale_state_cannot_be_saved(store: FileStore) -> None:
    first = store.authoring_state("demo", SPEC)
    second = store.authoring_state("demo", SPEC)
    store.save_authoring_state(first.proposed())
    with pytest.raises(AuthoringError, match="changed"):
        store.save_authoring_state(second.proposed())


def test_only_one_reader_wins_the_approval_reservation(store: FileStore) -> None:
    store.save_authoring_state(AuthoringState("demo", SPEC).proposed())
    a = store.authoring_state("demo", SPEC)
    b = store.authoring_state("demo", SPEC)
    reserved = store.save_authoring_state(a.approving())
    assert reserved.phase is TaskPhase.APPROVING
    with pytest.raises(AuthoringError):
        store.save_authoring_state(b.approving())
    assert store.authoring_state("demo", SPEC).phase is TaskPhase.APPROVING
    released = store.save_authoring_state(reserved.released())
    assert released.phase is TaskPhase.PROPOSED


def test_removing_the_waiver_text_keeps_a_mac_that_no_longer_fits(store: FileStore) -> None:
    import json

    task_id, path = _waived(store)
    data = json.loads(path.read_bytes())
    del data["waiver"]
    path.write_bytes(json.dumps(data).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(task_id)


def test_downgrading_a_waived_task_to_an_unwaived_record_is_refused(store: FileStore) -> None:
    import json

    task_id, path = _waived(store)
    data = json.loads(path.read_bytes())
    del data["waiver"], data["waiver_mac"]
    path.write_bytes(json.dumps(data).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(task_id)


def test_an_unwaived_task_without_the_waiver_field_is_refused(store: FileStore) -> None:
    import json

    from tests.factories import frozen_task

    plain = frozen_task()
    store.save_task(plain, bytes(range(32)))
    path = store.root / "tasks" / f"{plain.task.task_id}.json"
    data = json.loads(path.read_bytes())
    del data["waiver_mac"]
    path.write_bytes(json.dumps(data).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(plain.task.task_id)


def test_reference_and_crosscheck_publication_is_conditional_on_the_state_version(
    store: FileStore,
) -> None:
    read = store.authoring_state("demo", SPEC)
    store.save_authoring_state(read.proposed())  # the lifecycle moved on after the read
    with pytest.raises(AuthoringError, match="changed"):
        store.save_reference("demo", SPEC, OID, expected_version=read.version)
    rec = CrosscheckRecord(OID, SPEC, OID, OID, True, ())
    with pytest.raises(AuthoringError, match="changed"):
        store.save_crosscheck(PID, "demo", rec, expected_version=read.version)
    assert store.load_reference("demo") is None and store.load_crosscheck(PID) is None
    current = store.authoring_state("demo", SPEC)
    store.save_reference("demo", SPEC, OID, expected_version=current.version)
    assert store.load_reference("demo") == (SPEC, OID)


def test_a_waiver_reason_of_none_is_not_the_absence_of_a_waiver(store: FileStore) -> None:
    import dataclasses
    import json

    from tests.factories import frozen_task

    plain = frozen_task()
    store.save_task(dataclasses.replace(plain, waiver="none"), bytes(range(32)))
    path = store.root / "tasks" / f"{plain.task.task_id}.json"
    assert store.load_task(plain.task.task_id)[0].waiver == "none"
    data = json.loads(path.read_bytes())
    del data["waiver"]
    path.write_bytes(json.dumps(data).encode())
    with pytest.raises(StoreError, match="waiver"):
        store.load_task(plain.task.task_id)


def test_a_persisted_task_cannot_be_replaced(store: FileStore) -> None:
    from tests.factories import frozen_task

    plain = frozen_task()
    store.save_task(plain, bytes(range(32)))
    with pytest.raises(StoreError, match="already"):
        store.save_task(plain, bytes(reversed(range(32))))
    assert store.load_task(plain.task.task_id)[1] == bytes(range(32))
    assert store.has_task(plain.task.task_id) and not store.has_task("f" * 64)


def test_the_approval_ownership_lock_is_exclusive_and_released(store: FileStore) -> None:
    with store.approval_ownership("demo"):
        with pytest.raises(AuthoringError, match="held"):
            with store.approval_ownership("demo"):
                raise AssertionError("a second owner must not get in")
        with store.approval_ownership("other"):  # another lineage is independent
            pass
    with store.approval_ownership("demo"):  # released on exit, also after a failure
        pass
    with pytest.raises(RuntimeError):
        with store.approval_ownership("demo"):
            raise RuntimeError("boom")
    with store.approval_ownership("demo"):
        pass


def test_author_check_record_round_trip_and_conditional_publication(store: FileStore) -> None:
    assert store.load_author_check("demo") is None
    read = store.authoring_state("demo", SPEC)
    store.save_author_check("demo", SPEC, OID, expected_version=read.version)
    assert store.load_author_check("demo") == (SPEC, OID)
    store.save_authoring_state(read.proposed())
    with pytest.raises(AuthoringError, match="changed"):
        store.save_author_check("demo", SPEC, "b" * 40, expected_version=read.version)
    assert store.load_author_check("demo") == (SPEC, OID)


def test_a_corrupt_author_check_record_is_a_store_error(store: FileStore, tmp_path: Path) -> None:
    store.save_author_check("demo", SPEC, OID, expected_version=0)
    (tmp_path / "state" / "authoring" / "author-check-demo.json").write_text('{"tree": "x"}')
    with pytest.raises(StoreError):
        store.load_author_check("demo")


def test_auto_freeze_optin_is_off_until_recorded_and_survives_a_corrupt_record(
    store: FileStore,
) -> None:
    """Decision B lives in the store, so its default is what a missing or broken file does."""
    lineage = "demo-project"

    assert store.auto_freeze_enabled(lineage) is False, "an absent record is not consent"

    store.set_auto_freeze(lineage, True)
    assert store.auto_freeze_enabled(lineage) is True
    assert store.auto_freeze_enabled("other-project") is False, "the setting is per project"

    store.set_auto_freeze(lineage, False)
    assert store.auto_freeze_enabled(lineage) is False

    # A corrupt record must not open the gate. The store refuses it outright rather than
    # guessing `False`: an unreadable setting is a broken store, not a quiet "no".
    paths = list(Path(store.root).rglob("auto-freeze-*.json"))
    assert paths, "the opt-in record should exist before it is corrupted"
    for path in paths:
        path.write_text('{"enabled": "yes"}')
    with pytest.raises(StoreError):
        store.auto_freeze_enabled(lineage)


def test_a_session_binding_records_the_model_the_host_launched(store: FileStore) -> None:
    """The vendor of a review is read back from here, so this has to survive a round trip."""
    session_id, binding = store.register_session(PID, "reviewer", "gpt-6.2-sol")
    assert binding.model == "gpt-6.2-sol"

    stored = store.session_record(session_id)
    assert stored is not None
    assert stored.model == "gpt-6.2-sol"
    assert stored.role.value == "reviewer"

    # An older record without a model reads as empty, which the reviewer path refuses.
    assert store.register_session(SPEC, "author")[1].model == ""


def test_the_optin_cannot_change_while_an_approval_owns_the_lineage(store: FileStore) -> None:
    """The approval reads consent under ownership; a concurrent write must not slip in.

    A withdrawal that appeared to succeed while a freeze was already under way would tell
    the operator they had stopped it. Refusing it is the honest answer.
    """
    store.set_auto_freeze("demo", True)
    with store.approval_ownership("demo"):
        with pytest.raises(AuthoringError):
            store.set_auto_freeze("demo", False)
        store.set_auto_freeze("other", False)  # another lineage is not blocked
    assert store.auto_freeze_enabled("demo") is True, "the refused write changed nothing"
    store.set_auto_freeze("demo", False)  # canary: without a live approver it does change
    assert store.auto_freeze_enabled("demo") is False
