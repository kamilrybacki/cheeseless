import dataclasses

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.app.status import status
from tests.toy import FIXED_MAIN


def test_status_returns_latest_result(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    wt = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, wt)
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    gate_run(repo, store, runner, sandbox, worktree=wt)
    res = status(store)
    assert res is not None
    assert res.status.value == "pass"


def test_status_rejects_result_for_another_task(toy_repo, tmp_path, monkeypatch):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    wt = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, wt)
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    gate_run(repo, store, runner, sandbox, worktree=wt)
    real = store.latest_result(store.active_task_id())
    assert real is not None
    forged = dataclasses.replace(real, task_id="0" * 64)
    # sign forged with same key so HMAC passes
    from cheeseless.domain.result import sign

    forged = sign(forged, store.hmac_key())
    monkeypatch.setattr(store, "latest_result", lambda _tid: forged)
    with pytest.raises(ValueError):
        status(store, task_id=store.active_task_id())
