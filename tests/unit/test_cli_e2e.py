from pathlib import Path

import pytest

from cheeseless.bootstrap import main
from tests.toy import FIXED_MAIN


def test_cli_freeze_run_status_promote(
    toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cwd = toy_repo
    import os as _os

    old = _os.getcwd()
    _os.chdir(cwd)
    try:
        rc = main(["--state-dir", str(state), "freeze", "--yes"])
        assert rc == 0
        # projection
        from cheeseless.adapters.outbound.git.repository import GitRepository

        repo = GitRepository.discover(cwd)
        # read active task hidden blobs from store
        from cheeseless.adapters.outbound.store.file_store import FileStore, store_root

        store = FileStore(store_root(repo.common_dir(), state))
        # projection tree from frozen task
        frozen, _ = store.load_task(store.active_task_id())
        proj = tmp_path / "proj"
        repo.create_projection(frozen.projection_tree, proj)
        (proj / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
        # run
        rc = main(["--state-dir", str(state), "run", "--worktree", str(proj), "--json"])
        assert rc == 0
        rc = main(["--state-dir", str(state), "status", "--json"])
        assert rc == 0
        rc = main(
            [
                "--state-dir",
                str(state),
                "promote",
                "--worktree",
                str(proj),
                "--json",
            ]
        )
        assert rc == 0
        repo.sparse_hide(keep=["acceptance/contract"], hide=["acceptance"])
        rc = main(["--state-dir", str(state), "doctor", "--json"])
        assert rc == 0
    finally:
        _os.chdir(old)
