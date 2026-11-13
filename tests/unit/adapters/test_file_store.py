"""Trusted file store (Q38, Q55, D7, D14)."""

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.domain.budget import DEFAULT_LIMITS, Pool
from cheeseless.domain.result import CriterionResult, Result, sign
from cheeseless.domain.status import Status
from cheeseless.ports.store import StoreError
from tests.factories import frozen_task

SEED = bytes(range(32))


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _result(task_id: str, status: Status = Status.FAIL) -> Result:
    crit = (CriterionResult("normalize", status), CriterionResult("score", status))
    return Result(task_id, "5" * 40, "6" * 64, "local", "0.1.0", status, crit)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


def test_root_is_created_private(tmp_path: Path) -> None:
    FileStore(tmp_path / "state")
    assert _mode(tmp_path / "state") == 0o700


def test_wider_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(StoreError):
        FileStore(root)


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(StoreError):
        FileStore(tmp_path / "link")


def test_key_is_stable_and_private(store: FileStore, tmp_path: Path) -> None:
    key = store.hmac_key()
    assert len(key) == 32
    assert FileStore(tmp_path / "state").hmac_key() == key
    assert _mode(tmp_path / "state" / "hmac.key") == 0o600


def test_truncated_key_is_refused(store: FileStore, tmp_path: Path) -> None:
    store.hmac_key()
    (tmp_path / "state" / "hmac.key").write_bytes(b"short")
    with pytest.raises(StoreError):
        store.hmac_key()


def test_task_round_trip(store: FileStore) -> None:
    frozen = frozen_task()
    store.save_task(frozen, SEED)
    loaded, seed = store.load_task(frozen.task.task_id)
    assert loaded == frozen
    assert seed == SEED


def test_tampered_task_is_refused(store: FileStore, tmp_path: Path) -> None:
    frozen = frozen_task()
    store.save_task(frozen, SEED)
    path = tmp_path / "state" / "tasks" / f"{frozen.task.task_id}.json"
    data = json.loads(path.read_text())
    data["acceptance_tree"] = "9" * 40
    path.write_text(json.dumps(data))
    with pytest.raises(StoreError):
        store.load_task(frozen.task.task_id)


def test_unknown_or_malformed_task_id(store: FileStore) -> None:
    with pytest.raises(StoreError):
        store.load_task("a" * 64)
    with pytest.raises(StoreError):
        store.load_task("../../etc/passwd")


def test_bad_seed_is_refused(store: FileStore) -> None:
    with pytest.raises(StoreError):
        store.save_task(frozen_task(), b"short")


def test_active_task(store: FileStore) -> None:
    assert store.active_task_id() is None
    frozen = frozen_task()
    store.save_task(frozen, SEED)
    store.set_active(frozen.task.task_id)
    assert store.active_task_id() == frozen.task.task_id


def test_set_active_requires_a_stored_task(store: FileStore) -> None:
    with pytest.raises(StoreError):
        store.set_active("a" * 64)


def test_budget_defaults_and_reservation(store: FileStore) -> None:
    assert store.budget("toy", Pool.CI).limit == DEFAULT_LIMITS[Pool.CI]
    for _ in range(DEFAULT_LIMITS[Pool.CI]):
        assert store.reserve_run("toy", Pool.CI) is not None
    assert store.reserve_run("toy", Pool.CI) is None
    assert store.budget("toy", Pool.CI).remaining == 0
    assert store.budget("toy", Pool.LOCAL).remaining == DEFAULT_LIMITS[Pool.LOCAL]


def test_budget_is_keyed_by_lineage(store: FileStore) -> None:
    store.reserve_run("toy", Pool.LOCAL)
    assert store.budget("other", Pool.LOCAL).used == 0


def test_grant_reopens_an_exhausted_budget(store: FileStore) -> None:
    for _ in range(DEFAULT_LIMITS[Pool.CI]):
        store.reserve_run("toy", Pool.CI)
    granted = store.grant("toy", Pool.CI, 2)
    assert granted.remaining == 2
    assert store.reserve_run("toy", Pool.CI) is not None


def test_concurrent_reservations_never_overspend(tmp_path: Path) -> None:
    root = tmp_path / "state"
    FileStore(root)
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        ok = FileStore(root).reserve_run("toy", Pool.CI) is not None
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert granted.count(True) == DEFAULT_LIMITS[Pool.CI]


def test_corrupt_budget_fails_closed(store: FileStore, tmp_path: Path) -> None:
    store.reserve_run("toy", Pool.LOCAL)
    (tmp_path / "state" / "budget.json").write_text("{not json")
    with pytest.raises(StoreError):
        store.reserve_run("toy", Pool.LOCAL)


def test_result_history_and_latest(store: FileStore, tmp_path: Path) -> None:
    task_id = frozen_task().task.task_id
    assert store.latest_result(task_id) is None
    key = store.hmac_key()
    first = sign(_result(task_id), key)
    second = sign(_result(task_id, Status.PASS), key)
    store.save_result(first, "cause one")
    store.save_result(second, "")
    assert store.latest_result(task_id) == second
    history = (tmp_path / "state" / "results" / f"{task_id}.jsonl").read_text().splitlines()
    assert len(history) == 2
    assert json.loads(history[0])["operator_cause"] == "cause one"


def test_attempt_window(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "state")
    task_id = "a" * 64
    assert store.record_attempt(task_id, 100.0, 10.0) == 1
    assert store.record_attempt(task_id, 105.0, 10.0) == 2
    assert store.record_attempt(task_id, 120.0, 10.0) == 1


def test_events_are_appended(store: FileStore, tmp_path: Path) -> None:
    store.log_event("toy", "grant", "operator granted 2 runs")
    store.log_event("toy", "grant", "again")
    lines = (tmp_path / "state" / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["cause"] for line in lines] == ["operator granted 2 runs", "again"]
    assert _mode(tmp_path / "state" / "events.jsonl") == 0o600


def test_store_root_is_stable_per_repository(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    first = store_root(common, tmp_path / "xdg")
    assert first == store_root(common, tmp_path / "xdg")
    assert first.parent == tmp_path / "xdg" / "cheeseless"
    other = tmp_path / "other" / ".git"
    other.mkdir(parents=True)
    assert store_root(other, tmp_path / "xdg") != first


def test_files_never_follow_symlinks(store: FileStore, tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_text("{}")
    os.symlink(target, tmp_path / "state" / "budget.json")
    with pytest.raises(StoreError):
        store.reserve_run("toy", Pool.LOCAL)
