"""The read-only observation store: it reads the trusted store and never changes it."""

import json
import os
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.adapters.outbound.store.observe_store import ObserveStore
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.dispute import DisputeCode, DisputeStatus
from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.ports.dev_observe import DevObservation
from cheeseless.ports.store import StoreError

LINEAGE = "demo"
DIGEST = "a" * 64


def _tree(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every path under ``root`` with its mode and, for files, its bytes."""
    found: dict[str, tuple[int, bytes | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            path = Path(dirpath, name)
            data = path.read_bytes() if path.is_file() else None
            found[str(path.relative_to(root))] = (path.lstat().st_mode, data)
    return found


def _attempt() -> ReferenceAttempt:
    return ReferenceAttempt(
        "attempt-1",
        "1" * 32,
        1,
        DIGEST,
        ReferenceMode.HEADLESS,
        False,
        ReferenceState.RUNNING,
        LINEAGE,
        None,
        None,
    )


def _seed(root: Path) -> None:
    """A realistic store: a dev state, a reference attempt and one open dispute."""
    store = FileStore(root)
    store.save_dev_state(DevState(lineage_id=LINEAGE, phase=DevPhase.TESTS, spec_digest=DIGEST))
    store.save_reference_attempt(_attempt())
    record = {
        "dispute_id": "d1",
        "task_id": "task-1",
        "lineage_id": LINEAGE,
        "criterion_id": "criterion-1",
        "code": DisputeCode.OTHER.value,
        "note": "",
        "status": DisputeStatus.OPEN.value,
        "resolved_by": "",
    }
    (root / "disputes.json").write_text(json.dumps({"records": [record]}))


def test_absent_store_is_read_without_being_created(tmp_path: Path) -> None:
    root = tmp_path / "absent"

    store = ObserveStore(root)

    assert store.dev_state(LINEAGE) is None
    assert store.reference_attempts(LINEAGE) == ()
    assert store.disputes() == ()
    assert not root.exists()


def test_reads_the_persisted_state_attempts_and_disputes(tmp_path: Path) -> None:
    _seed(tmp_path)
    store = ObserveStore(tmp_path)

    state = store.dev_state(LINEAGE)
    attempts = store.reference_attempts(LINEAGE)
    records = store.disputes()

    assert state is not None
    assert state.phase is DevPhase.TESTS
    assert [a.attempt_id for a in attempts] == ["attempt-1"]
    assert [(r.dispute_id, r.status) for r in records] == [("d1", DisputeStatus.OPEN)]


def test_reads_leave_every_byte_and_mode_unchanged(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = _tree(tmp_path)

    store = ObserveStore(tmp_path)
    store.dev_state(LINEAGE)
    store.reference_attempts()
    store.disputes()

    assert _tree(tmp_path) == before


def test_missing_disputes_file_reads_as_no_records(tmp_path: Path) -> None:
    assert ObserveStore(tmp_path).disputes() == ()


def test_invalid_disputes_shape_raises_the_store_error(tmp_path: Path) -> None:
    (tmp_path / "disputes.json").write_text('{"records": "nope"}')

    with pytest.raises(StoreError):
        ObserveStore(tmp_path).disputes()


def test_unparseable_disputes_raise_the_store_error(tmp_path: Path) -> None:
    (tmp_path / "disputes.json").write_text("not json")

    with pytest.raises(StoreError):
        ObserveStore(tmp_path).disputes()


def test_satisfies_the_read_only_port(tmp_path: Path) -> None:
    port: DevObservation = ObserveStore(tmp_path)

    assert port.disputes() == ()
