"""Git adapter: plumbing-only, hardened access to the trusted repository (ADR 0002)."""

import os
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.domain.freeze import HiddenBlob
from cheeseless.ports.repository import RepositoryError, SnapshotRejected
from tests.toy import BASELINE_MAIN, FIXED_MAIN, commit_all, git, make_repo, write_files


@pytest.fixture
def repo(toy_repo: Path) -> GitRepository:
    return GitRepository(toy_repo)


def test_discover_finds_the_toplevel(toy_repo: Path) -> None:
    found = GitRepository.discover(toy_repo / "src" / "toy")
    assert found.root == toy_repo.resolve()
    assert found.common_dir() == (toy_repo / ".git").resolve()


def test_discover_outside_a_repo_fails(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(RepositoryError):
        GitRepository.discover(tmp_path / "plain")


def test_head_and_trees(repo: GitRepository, toy_repo: Path) -> None:
    head = repo.head_commit()
    assert head == git(toy_repo, "rev-parse", "HEAD")
    assert repo.tree_of(head) == git(toy_repo, "rev-parse", "HEAD^{tree}")
    assert repo.tree_of(head, "acceptance") == git(toy_repo, "rev-parse", "HEAD:acceptance")


def test_tree_of_a_file_or_missing_path_fails(repo: GitRepository) -> None:
    head = repo.head_commit()
    with pytest.raises(RepositoryError):
        repo.tree_of(head, "README.md")
    with pytest.raises(RepositoryError):
        repo.tree_of(head, "nope")


def test_invalid_oids_and_option_like_paths_are_refused(repo: GitRepository) -> None:
    with pytest.raises(RepositoryError):
        repo.tree_of("HEAD")
    with pytest.raises(RepositoryError):
        repo.show(repo.head_commit(), "--output=/tmp/x")


def test_blobs_under_and_show(repo: GitRepository) -> None:
    head = repo.head_commit()
    blobs = repo.blobs_under(repo.tree_of(head), "acceptance/hidden")
    assert [b.path for b in blobs] == [
        "acceptance/hidden/test_normalize.py",
        "acceptance/hidden/test_score.py",
    ]
    assert b"criterion" in repo.show(head, "acceptance/hidden/test_score.py")


def test_blobs_under_rejects_symlinks(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r", {"acceptance/hidden/test_a.py": "x = 1\n"})
    os.symlink("test_a.py", root / "acceptance" / "hidden" / "test_b.py")
    commit_all(root)
    repo = GitRepository(root)
    with pytest.raises(SnapshotRejected):
        repo.blobs_under(repo.tree_of(repo.head_commit()), "acceptance/hidden")


def test_tree_without_keeps_only_the_contract(repo: GitRepository, tmp_path: Path) -> None:
    tree = repo.tree_of(repo.head_commit())
    projected = repo.tree_without(tree, "acceptance", "acceptance/contract")
    dest = tmp_path / "out"
    dest.mkdir()
    repo.export(projected, dest)
    assert (dest / "acceptance" / "contract" / "criteria.yaml").is_file()
    assert not (dest / "acceptance" / "hidden").exists()
    assert (dest / "src" / "toy" / "__main__.py").read_text() == BASELINE_MAIN


def test_replace_subtree_takes_the_prefix_from_another_tree(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r2", {"src/a.py": "base\n", "acceptance/contract/s.md": "old\n"})
    repo = GitRepository(root)
    base = repo.tree_of(repo.head_commit())
    write_files(
        root,
        {
            "src/a.py": "moved\n",
            "acceptance/contract/s.md": "new\n",
            "acceptance/hidden/h.py": "h\n",
        },
    )
    other = repo.tree_of(commit_all(root))
    merged = repo.replace_subtree(base, "acceptance", other, "acceptance/contract")
    dest = tmp_path / "m"
    dest.mkdir()
    repo.export(merged, dest)
    assert (dest / "src" / "a.py").read_text() == "base\n"
    assert (dest / "acceptance" / "contract" / "s.md").read_text() == "new\n"
    assert not (dest / "acceptance" / "hidden").exists()


def test_export_only_and_exclude(repo: GitRepository, tmp_path: Path) -> None:
    tree = repo.tree_of(repo.head_commit())
    only = tmp_path / "only"
    only.mkdir()
    repo.export(tree, only, only=("acceptance/hidden",))
    assert sorted(p.name for p in (only / "acceptance" / "hidden").iterdir()) == [
        "test_normalize.py",
        "test_score.py",
    ]
    assert not (only / "src").exists()
    excl = tmp_path / "excl"
    excl.mkdir()
    repo.export(tree, excl, exclude=("acceptance",))
    assert not (excl / "acceptance").exists()
    assert (excl / "README.md").is_file()


def test_export_into_a_non_empty_dir_fails(repo: GitRepository, tmp_path: Path) -> None:
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "x").write_text("x")
    with pytest.raises(RepositoryError):
        repo.export(repo.tree_of(repo.head_commit()), dest)


def test_snapshot_includes_untracked_and_skips_ignored(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "w", {"a.txt": "a\n", ".gitignore": "*.log\n"})
    (root / "b.txt").write_text("b\n")
    (root / "noise.log").write_text("ignored\n")
    (root / "run.sh").write_text("#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    repo = GitRepository(root)
    tree = repo.snapshot(root)
    listing = git(root, "ls-tree", "-r", tree)
    assert "b.txt" in listing
    assert "noise.log" not in listing
    assert "100755 blob" in [line for line in listing.splitlines() if "run.sh" in line][0]
    assert git(root, "cat-file", "-p", f"{tree}:b.txt") == "b"


def test_snapshot_is_deterministic(repo: GitRepository, toy_repo: Path) -> None:
    assert repo.snapshot(toy_repo) == repo.snapshot(toy_repo)
    assert repo.snapshot(toy_repo) == repo.tree_of(repo.head_commit())


def test_snapshot_rejects_symlinks(repo: GitRepository, toy_repo: Path) -> None:
    os.symlink("/etc/passwd", toy_repo / "src" / "leak")
    with pytest.raises(SnapshotRejected) as err:
        repo.snapshot(toy_repo)
    assert err.value.path == "src/leak"


def test_snapshot_rejects_nested_repositories(repo: GitRepository, toy_repo: Path) -> None:
    make_repo(toy_repo / "vendor" / "inner", {"x.txt": "x\n"})
    with pytest.raises(SnapshotRejected):
        repo.snapshot(toy_repo)


def test_diff_modes(repo: GitRepository, toy_repo: Path) -> None:
    base = repo.tree_of(repo.head_commit())
    write_files(toy_repo, {"src/toy/__main__.py": FIXED_MAIN, "docs/n.md": "n\n"})
    (toy_repo / "README.md").unlink()
    changes = repo.diff_modes(base, repo.snapshot(toy_repo))
    assert changes == {
        "src/toy/__main__.py": "100644",
        "docs/n.md": "100644",
        "README.md": "000000",
    }


def test_merge_base_is_the_fork_point(repo: GitRepository, toy_repo: Path) -> None:
    fork = repo.head_commit()
    git(toy_repo, "checkout", "-q", "-b", "side")
    write_files(toy_repo, {"docs/a.md": "a\n"})
    side = commit_all(toy_repo, "side")
    git(toy_repo, "checkout", "-q", "main")
    write_files(toy_repo, {"docs/b.md": "b\n"})
    tip = commit_all(toy_repo, "main")
    assert repo.merge_base(tip, side) == fork


def test_merge_base_of_unrelated_histories_is_an_error(repo: GitRepository, toy_repo: Path) -> None:
    git(toy_repo, "checkout", "-q", "--orphan", "lonely")
    write_files(toy_repo, {"docs/c.md": "c\n"})
    lonely = commit_all(toy_repo, "lonely")
    with pytest.raises(RepositoryError):
        repo.merge_base(lonely, git(toy_repo, "rev-parse", "main").strip())


def test_promote_applies_the_candidate_on_the_baseline(repo: GitRepository, toy_repo: Path) -> None:
    baseline = repo.head_commit()
    base_tree = repo.tree_of(baseline)
    write_files(toy_repo, {"src/toy/__main__.py": FIXED_MAIN})
    candidate = repo.snapshot(toy_repo)
    commit = repo.promote(baseline, base_tree, candidate, "refs/heads/gate/toy")
    assert git(toy_repo, "rev-parse", "refs/heads/gate/toy") == commit
    assert git(toy_repo, "rev-parse", f"{commit}^") == baseline
    assert git(toy_repo, "show", f"{commit}:src/toy/__main__.py") == FIXED_MAIN.rstrip("\n")
    # Hidden tests stay exactly as they were at the baseline.
    assert git(toy_repo, "rev-parse", f"{commit}:acceptance") == git(
        toy_repo, "rev-parse", f"{baseline}:acceptance"
    )


def test_promote_refuses_refs_outside_the_gate_namespace(repo: GitRepository) -> None:
    head = repo.head_commit()
    tree = repo.tree_of(head)
    with pytest.raises(RepositoryError):
        repo.promote(head, tree, tree, "refs/heads/main")


def test_dirty_paths(repo: GitRepository, toy_repo: Path) -> None:
    assert repo.dirty_paths("acceptance") == []
    (toy_repo / "acceptance" / "hidden" / "test_score.py").write_text("changed\n")
    (toy_repo / "acceptance" / "new.py").write_text("new\n")
    (toy_repo / "src" / "other.py").write_text("x\n")
    assert repo.dirty_paths("acceptance") == [
        "acceptance/hidden/test_score.py",
        "acceptance/new.py",
    ]


def test_create_projection(repo: GitRepository, tmp_path: Path) -> None:
    tree = repo.tree_without(repo.tree_of(repo.head_commit()), "acceptance", "acceptance/contract")
    dest = tmp_path / "proj"
    repo.create_projection(tree, dest)
    assert git(dest, "rev-parse", "HEAD^{tree}") == tree
    assert git(dest, "rev-list", "--count", "HEAD") == "1"
    assert git(dest, "remote") == ""
    assert git(dest, "status", "--porcelain") == ""
    assert (dest / "src" / "toy" / "__main__.py").read_text() == BASELINE_MAIN


def test_stray_hidden_finds_paths_and_copies(repo: GitRepository, toy_repo: Path) -> None:
    blobs = repo.blobs_under(repo.tree_of(repo.head_commit()), "acceptance/hidden")
    assert sorted(repo.stray_hidden(toy_repo, blobs)) == [b.path for b in blobs]
    clean = toy_repo.parent / "clean"
    clean.mkdir()
    assert repo.stray_hidden(clean, blobs) == []
    copy = clean / "src" / "copied.py"
    copy.parent.mkdir()
    copy.write_bytes(repo.show(repo.head_commit(), "acceptance/hidden/test_score.py"))
    assert repo.stray_hidden(clean, blobs) == ["src/copied.py"]


def test_stray_hidden_counts_a_symlink_at_a_hidden_path(
    repo: GitRepository, tmp_path: Path
) -> None:
    clean = tmp_path / "clean"
    (clean / "acceptance" / "hidden").mkdir(parents=True)
    os.symlink("/nonexistent", clean / "acceptance" / "hidden" / "test_score.py")
    blob = HiddenBlob("acceptance/hidden/test_score.py", "0" * 40)
    assert repo.stray_hidden(clean, [blob]) == ["acceptance/hidden/test_score.py"]


def test_sparse_hide_removes_hidden_files(repo: GitRepository, toy_repo: Path) -> None:
    repo.sparse_hide(keep=["acceptance/contract"], hide=["acceptance"])
    assert not (toy_repo / "acceptance" / "hidden").exists()
    assert (toy_repo / "acceptance" / "contract" / "criteria.yaml").is_file()
    assert (toy_repo / "src" / "toy" / "__main__.py").is_file()
    assert repo.dirty_paths("acceptance") == []


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/heads/gates/x", "refs/tags/gate/x"])
def test_promote_refuses_refs_outside_gate_and_spec(
    repo: GitRepository, toy_repo: Path, ref: str
) -> None:
    baseline = repo.head_commit()
    tree = repo.tree_of(baseline)
    with pytest.raises(RepositoryError):
        repo.promote(baseline, tree, tree, ref)


def test_promote_accepts_a_spec_ref(repo: GitRepository, toy_repo: Path) -> None:
    baseline = repo.head_commit()
    tree = repo.tree_of(baseline)
    commit = repo.promote(baseline, tree, tree, "refs/heads/spec/abc")
    assert git(toy_repo, "rev-parse", "refs/heads/spec/abc") == commit
