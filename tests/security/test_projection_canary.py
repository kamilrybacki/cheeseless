"""The agent projection never contains hidden tests, and untrusted repo config never runs.

ADR 0002: the projection is a fresh one-commit repository with no remote and no
protected blobs. The canary marks every hidden file; it must not appear in any object
of the projection or in its worktree. Git runs with hooks, filters, fsmonitor and
user config disabled, so files the agent can write never execute code in the verifier.
"""

import os
import subprocess
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.domain.promote_policy import decide_promote
from cheeseless.ports.repository import SnapshotRejected
from tests.toy import CANARY, FIXED_MAIN, commit_all, git, write_files


def _project(repo: GitRepository, dest: Path) -> str:
    tree = repo.tree_without(repo.tree_of(repo.head_commit()), "acceptance", "acceptance/contract")
    repo.create_projection(tree, dest)
    return tree


def _all_objects(root: Path) -> bytes:
    out = subprocess.run(  # noqa: S603
        ["git", "cat-file", "--batch-all-objects", "--batch"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    )
    return out.stdout


def _worktree_bytes(root: Path) -> bytes:
    data = b""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            if not path.is_symlink():
                data += path.read_bytes()
    return data


def test_canary_is_in_the_baseline(toy_repo: Path) -> None:
    # The checks below only mean something if the source really carries the canary.
    assert CANARY.encode() in _all_objects(toy_repo)


def test_projection_holds_no_hidden_object(toy_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    _project(GitRepository(toy_repo), dest)
    assert CANARY.encode() not in _all_objects(dest)
    assert CANARY.encode() not in _worktree_bytes(dest)
    assert git(dest, "remote") == ""
    assert git(dest, "rev-list", "--all", "--count") == "1"
    hooks = dest / ".git" / "hooks"
    assert not hooks.exists() or not any(hooks.iterdir())


def _plant(root: Path, marker: Path) -> None:
    """Every place where a repository can ask git to run a program."""
    payload = f"#!/bin/sh\ntouch {marker}\ncat\n"
    script = root / "evil.sh"
    script.write_text(payload)
    script.chmod(0o755)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for hook in ("post-checkout", "pre-commit", "post-index-change", "reference-transaction"):
        (hooks / hook).write_text(payload)
        (hooks / hook).chmod(0o755)
    for key, value in (
        ("filter.evil.clean", str(script)),
        ("filter.evil.smudge", str(script)),
        ("filter.evil.process", str(script)),
        ("core.fsmonitor", str(script)),
        ("diff.external", str(script)),
        ("core.sshCommand", str(script)),
        ("core.pager", str(script)),
        ("core.hooksPath", str(hooks)),
    ):
        git(root, "config", key, value)
    (root / ".gitattributes").write_text("* filter=evil diff=evil\n")


def test_planted_repo_config_never_runs(toy_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "PWNED"
    repo = GitRepository(toy_repo)
    baseline = repo.head_commit()
    base_tree = repo.tree_of(baseline)
    _plant(toy_repo, marker)
    write_files(toy_repo, {"src/toy/__main__.py": FIXED_MAIN})

    blobs = repo.blobs_under(base_tree, "acceptance/hidden")
    candidate = repo.snapshot(toy_repo)
    repo.dirty_paths("acceptance")
    repo.diff_modes(base_tree, candidate)
    projected = repo.tree_without(base_tree, "acceptance", "acceptance/contract")
    repo.create_projection(projected, tmp_path / "proj")
    exported = tmp_path / "exported"
    exported.mkdir()
    repo.export(candidate, exported)
    repo.stray_hidden(toy_repo, blobs)
    repo.promote(baseline, base_tree, candidate, "refs/heads/gate/toy")
    repo.sparse_hide(keep=["acceptance/contract"], hide=["acceptance"])

    assert not marker.exists()
    # The exported candidate is the raw content: no clean or smudge filter touched it.
    assert (exported / "src" / "toy" / "__main__.py").read_text() == FIXED_MAIN


def test_symlinked_directory_in_the_worktree_is_rejected(toy_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text(f"# {CANARY}\n")
    os.symlink(outside, toy_repo / "src" / "linked")
    with pytest.raises(SnapshotRejected):
        GitRepository(toy_repo).snapshot(toy_repo)


def test_dirty_hidden_file_survives_sparse_hide_and_is_caught(toy_repo: Path) -> None:
    repo = GitRepository(toy_repo)
    blobs = repo.blobs_under(repo.tree_of(repo.head_commit()), "acceptance/hidden")
    edited = toy_repo / "acceptance" / "hidden" / "test_score.py"
    edited.write_text(edited.read_text() + "# edited\n")
    repo.sparse_hide(keep=["acceptance/contract"], hide=["acceptance"])
    # Git keeps a modified file on disk; the stray check must catch it (fail closed).
    assert "acceptance/hidden/test_score.py" in repo.stray_hidden(toy_repo, blobs)


def test_copied_hidden_content_is_caught(toy_repo: Path, tmp_path: Path) -> None:
    repo = GitRepository(toy_repo)
    head = repo.head_commit()
    blobs = repo.blobs_under(repo.tree_of(head), "acceptance/hidden")
    dest = tmp_path / "proj"
    _project(repo, dest)
    assert repo.stray_hidden(dest, blobs) == []
    (dest / "notes.py").write_bytes(repo.show(head, "acceptance/hidden/test_normalize.py"))
    assert repo.stray_hidden(dest, blobs) == ["notes.py"]


def test_promote_policy_rejects_symlinks(toy_repo: Path) -> None:
    repo = GitRepository(toy_repo)
    base_tree = repo.tree_of(repo.head_commit())
    os.symlink("../README.md", toy_repo / "src" / "alias")
    commit_all(toy_repo)
    changes = repo.diff_modes(base_tree, repo.tree_of(repo.head_commit()))
    assert changes == {"src/alias": "120000"}
    assert decide_promote(changes).rejected == ("src/alias",)


def test_promote_refuses_non_regular_modes(toy_repo: Path) -> None:
    repo = GitRepository(toy_repo)
    baseline = repo.head_commit()
    base_tree = repo.tree_of(baseline)
    os.symlink("../README.md", toy_repo / "src" / "alias")
    candidate = commit_all(toy_repo)
    with pytest.raises(SnapshotRejected):
        repo.promote(baseline, base_tree, repo.tree_of(candidate), "refs/heads/gate/toy")


def test_promote_policy_rejects_acceptance_edits(toy_repo: Path) -> None:
    repo = GitRepository(toy_repo)
    base_tree = repo.tree_of(repo.head_commit())
    write_files(toy_repo, {"acceptance/hidden/test_score.py": "def test_x():\n    pass\n"})
    changes = repo.diff_modes(base_tree, repo.snapshot(toy_repo))
    assert decide_promote(changes).rejected == ("acceptance/hidden/test_score.py",)
