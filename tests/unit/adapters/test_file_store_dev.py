"""``cheeseless dev`` phase state in the trusted file store (ADR 0013)."""

import stat
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.dev_session import DevPhase, DevSessionError, DevState
from cheeseless.ports.store import StoreError

SPEC = "1" * 64
PID = "d" * 64
AUTHOR = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def test_no_state_until_saved(store: FileStore) -> None:
    assert store.dev_state("demo") is None


def test_state_round_trips_with_a_new_version(store: FileStore) -> None:
    state = DevState("demo").author_started(AUTHOR).spec_done(SPEC).proposed(PID)
    saved = store.save_dev_state(state)
    assert saved.version == state.version + 1
    assert store.dev_state("demo") == saved
    assert saved.phase is DevPhase.TESTS and saved.author_session == AUTHOR


def test_a_stale_save_is_refused(store: FileStore) -> None:
    first = store.save_dev_state(DevState("demo"))
    store.save_dev_state(first.author_started(AUTHOR))
    with pytest.raises(DevSessionError):
        store.save_dev_state(first.spec_done(SPEC))


def test_a_first_save_must_start_at_version_zero(store: FileStore) -> None:
    store.save_dev_state(DevState("demo"))
    with pytest.raises(DevSessionError):
        store.save_dev_state(DevState("demo"))


def test_the_state_file_is_private(store: FileStore) -> None:
    store.save_dev_state(DevState("demo"))
    path = store.root / "dev" / "state-demo.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_corrupt_record_is_a_store_error(store: FileStore) -> None:
    store.save_dev_state(DevState("demo"))
    path = store.root / "dev" / "state-demo.json"
    path.write_text('{"phase": "implement", "version": 1}')
    with pytest.raises(StoreError):
        store.dev_state("demo")


def test_an_invalid_lineage_is_refused(store: FileStore) -> None:
    with pytest.raises(StoreError):
        store.dev_state("../escape")
