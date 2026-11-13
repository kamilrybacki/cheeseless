"""Freeze keeps every hidden blob and drops the public contract; strays are caught."""

from pathlib import Path

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare


def test_freeze_hidden_blobs_retain_hidden_and_exclude_contract(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    assert prepared.hidden_blobs
    for blob in prepared.hidden_blobs:
        assert blob.path.startswith("acceptance/")
        assert not blob.path.startswith("acceptance/contract/")
        assert "hidden" in blob.path.split("/")
    assert not any(b.path.startswith("acceptance/contract/") for b in prepared.hidden_blobs)
    commit(prepared, store)
    assert store.active_task_id() is not None


def test_freeze_stray_hidden_catches_copied_hidden(toy_repo: Path, tmp_path: Path) -> None:
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    dest = tmp_path / "proj"
    repo.create_projection(prepared.projection_tree, dest)
    assert repo.stray_hidden(dest, prepared.hidden_blobs) == []
    hidden_path = prepared.hidden_blobs[0].path
    src_hidden = toy_repo / hidden_path
    (dest / "leaked.py").write_bytes(src_hidden.read_bytes())
    assert "leaked.py" in repo.stray_hidden(dest, prepared.hidden_blobs)
