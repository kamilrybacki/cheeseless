"""Fingerprint persistence, holdout rotation and BLOCKED signalling (Q48, Q49)."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.egress_supervisor import EgressBlock, EgressSupervisor
from cheeseless.app.holdout import rotate_holdout
from cheeseless.domain.budget import Pool
from cheeseless.domain.dlp import build_fingerprints
from cheeseless.domain.result import verify
from cheeseless.domain.status import Status
from cheeseless.ports.store import StoreError
from tests.factories import frozen_task


def _store(tmp_path: Path) -> FileStore:
    return FileStore(store_root(tmp_path / "repo.git", tmp_path / "state"))


def _task(tmp_path: Path) -> tuple[FileStore, str, object]:
    store = _store(tmp_path)
    task = frozen_task()
    store.save_task(task, b"s" * 32)
    store.set_active(task.task.task_id)
    return store, task.task.task_id, task


def test_fingerprints_round_trip_and_stay_out_of_tasks(tmp_path):
    store, task_id, _task_value = _task(tmp_path)
    prints = build_fingerprints(
        task_id,
        [("acceptance/hidden/a.py", b"secret CHEESELESS-CANARY-abcdef")],
        [b"public"],
    )
    store.save_fingerprints(prints)
    loaded = store.load_fingerprints(task_id)
    assert loaded == prints
    assert b"CHEESELESS" not in (store.root / "fingerprints" / f"{task_id}.json").read_bytes()
    assert store.load_fingerprints("a" * 64) is None


def test_corrupt_fingerprint_file_fails_closed(tmp_path):
    store, task_id, _ = _task(tmp_path)
    path = store.root / "fingerprints" / f"{task_id}.json"
    path.write_bytes(b'{"version":1}')
    with pytest.raises(StoreError):
        store.load_fingerprints(task_id)


def test_rotation_keeps_task_id_and_budget(tmp_path):
    store, task_id, task = _task(tmp_path)
    before = store.budget(task.task.lineage_id, Pool.LOCAL)
    rotated = rotate_holdout(store, task_id, seed=b"r" * 32)
    assert rotated.task.task_id == task_id
    assert rotated.acceptance_tree == task.acceptance_tree
    after = store.budget(task.task.lineage_id, Pool.LOCAL)
    assert (after.limit, after.used) == (before.limit, before.used)
    loaded, seed = store.load_task(task_id)
    assert seed == b"r" * 32
    assert set(loaded.visible) | set(loaded.holdout) == set(task.visible) | set(task.holdout)


def test_supervisor_persists_blocked_and_alerts_without_content(tmp_path):
    store, task_id, task = _task(tmp_path)
    events: list[tuple[str, str, str]] = []
    torn: list[bool] = []
    supervisor = EgressSupervisor(
        store=store,
        repo=None,  # type: ignore[arg-type]
        worktree=Path("/nonexistent"),
        task_id=task_id,
        teardown=lambda: torn.append(True),
        alert=lambda host, rule, fid: events.append((host, rule, fid)),
    )
    block = EgressBlock("api.example.test", "kgram", "0123456789abcdef")
    supervisor.block(block)
    assert torn == [True]
    assert events == [("api.example.test", "kgram", "0123456789abcdef")]
    result = store.latest_result(task_id)
    assert result is not None
    assert result.status is Status.BLOCKED
    assert result.criteria == ()
    assert verify(result, store.hmac_key())
    assert "CHEESELESS" not in (store.root / "events.jsonl").read_text()


def test_supervisor_is_idempotent(tmp_path):
    store, task_id, _ = _task(tmp_path)
    calls: list[int] = []
    supervisor = EgressSupervisor(
        store=store,
        repo=None,  # type: ignore[arg-type]
        worktree=Path("/nonexistent"),
        task_id=task_id,
        teardown=lambda: calls.append(1),
        alert=lambda *_: None,
    )
    supervisor.block(EgressBlock("h.test", "literal", "abc"))
    supervisor.block(EgressBlock("h.test", "literal", "abc"))
    assert calls == [1]


def test_pre_hidden_tail_is_omitted_on_hit_or_missing_scanner(tmp_path):
    from cheeseless.app.dlp import scanner_for
    from cheeseless.app.gate_run import _safe_tail

    store, task_id, _ = _task(tmp_path)
    prints = build_fingerprints(
        task_id,
        [("acceptance/hidden/a.py", b"hidden stderr marker " + b"CHEESELESS-CANARY-abcdef")],
        [],
    )
    store.save_fingerprints(prints)
    scanner = scanner_for(store, task_id)
    assert scanner is not None
    assert _safe_tail(scanner, "ordinary build error") == "ordinary build error"
    assert _safe_tail(scanner, "leak CHEESELESS-CANARY-abcdef") == ""
    assert _safe_tail(None, "ordinary build error") == ""
    assert scanner_for(store, "a" * 64) is None

    class Broken:
        def scan(self, _data):
            raise RuntimeError("scanner failed")

    assert _safe_tail(Broken(), "ordinary build error") == ""  # type: ignore[arg-type]

    path = store.root / "fingerprints" / f"{task_id}.json"
    path.write_bytes(b"not json")
    assert scanner_for(store, task_id) is None
