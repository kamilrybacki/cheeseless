import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import PromoteRejected, promote
from tests.presence_fakes import Approving
from tests.toy import FIXED_MAIN


def test_promote_valid_change(toy_repo, tmp_path):
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
    out = promote(repo, store, worktree=wt, presence=Approving())
    assert out.commit
    assert out.ref.startswith("refs/heads/gate/")


def test_promote_rejects_protected_path(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    wt = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, wt)
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    # add a protected file
    (wt / "acceptance" / "hidden" / "x.py").parent.mkdir(parents=True, exist_ok=True)
    (wt / "acceptance" / "hidden" / "x.py").write_text("x")
    gate_run(repo, store, runner, sandbox, worktree=wt)
    with pytest.raises(PromoteRejected):
        promote(repo, store, worktree=wt, presence=Approving())
