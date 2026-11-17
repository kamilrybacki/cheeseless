"""G13: a CI bundle exported before a later dispute is stale and must be re-exported."""

import json
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.bootstrap import _state_home as state_home
from cheeseless.bootstrap import main
from cheeseless.domain.dispute import DisputeCode, DisputeStatus
from tests.ci_support import prepare_world


def _store(world) -> FileStore:
    from cheeseless.adapters.outbound.git.repository import GitRepository

    repo = GitRepository.discover(world.toy)
    return FileStore(store_root(repo.common_dir(), state_home(world.state)))


def _check(world, capsys) -> tuple[int, str]:
    code = main(
        ["--state-dir", str(world.state), "ci-bundle-check", "--bundle-file", str(world.bundle)]
    )
    return code, capsys.readouterr().err


@pytest.fixture
def world(toy_repo: Path, tmp_path: Path, monkeypatch):
    return prepare_world(toy_repo, tmp_path, monkeypatch)


def test_a_fresh_bundle_passes_the_check(world, capsys):
    code, _ = _check(world, capsys)
    assert code == 0


def test_a_later_dispute_makes_the_bundle_stale(world, capsys):
    store = _store(world)
    task = store.active_task_id()
    frozen, _ = store.load_task(task)
    store.set_dispute_policy(_policy())
    store.file_dispute(task, frozen.task.lineage_id, "c", DisputeCode.OTHER, "x")
    code, err = _check(world, capsys)
    assert code != 0 and "stale" in err


def test_a_rejected_dispute_still_makes_the_old_bundle_stale(world, capsys):
    store = _store(world)
    task = store.active_task_id()
    frozen, _ = store.load_task(task)
    store.set_dispute_policy(_policy())
    store.file_dispute(task, frozen.task.lineage_id, "c", DisputeCode.OTHER, "x")
    store.resolve_dispute(store.disputes()[0].dispute_id, DisputeStatus.REJECTED, "op")
    code, err = _check(world, capsys)
    assert code != 0 and "stale" in err


def test_re_export_after_a_rejection_is_fresh(world, capsys):
    store = _store(world)
    task = store.active_task_id()
    frozen, _ = store.load_task(task)
    store.set_dispute_policy(_policy())
    store.file_dispute(task, frozen.task.lineage_id, "c", DisputeCode.OTHER, "x")
    store.resolve_dispute(store.disputes()[0].dispute_id, DisputeStatus.REJECTED, "op")
    from tests.ci_support import export_argv

    fresh = world.bundle.parent / "fresh.json"
    assert main(export_argv(world.state, fresh, world.root)) == 0
    assert json.loads(fresh.read_bytes())["disputes"] == 1
    world.bundle.unlink()
    fresh.rename(world.bundle)
    code, _ = _check(world, capsys)
    assert code == 0


def test_a_same_lineage_re_freeze_without_a_dispute_makes_the_bundle_stale(world, capsys):
    import dataclasses

    store = _store(world)
    frozen, seed = store.load_task(store.active_task_id())
    refrozen = dataclasses.replace(frozen, task=dataclasses.replace(frozen.task, task_id="c" * 64))
    store.save_task(refrozen, seed)
    store.set_active(refrozen.task.task_id)
    assert _lineage_count(store, frozen) == 0  # no dispute was filed: the old check passes
    code, err = _check(world, capsys)
    assert code != 0 and "task" in err


def _lineage_count(store, frozen) -> int:
    from cheeseless.bootstrap_ci import _lineage_disputes

    return _lineage_disputes(store, frozen.task.lineage_id)


def test_a_missing_or_unreadable_bundle_fails_closed(world, capsys):
    world.bundle.write_bytes(b"not a bundle")
    code, _ = _check(world, capsys)
    assert code != 0


def _policy():
    from cheeseless.domain.dispute import DisputePolicy

    return DisputePolicy(enabled=True, cap=5)
