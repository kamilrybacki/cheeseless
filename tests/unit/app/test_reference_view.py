"""The reference projection carries no blob of acceptance/ beyond the spec and criteria."""

from pathlib import Path

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.app.reference_view import reference_tree
from cheeseless.domain.session_role import REFERENCE_ALLOWLIST
from tests.toy import make_repo

FILES = {
    "src/app.py": "print('app')\n",
    "acceptance/contract/spec.md": "SPEC\n",
    "acceptance/contract/criteria.yaml": "CRITERIA\n",
    "acceptance/contract/test_smoke.py": "PUBLIC\n",
    "acceptance/hidden/test_hidden.py": "HIDDEN\n",
    "acceptance/notes.txt": "NOTES\n",
}


def _repo(tmp_path: Path) -> GitRepository:
    root = make_repo(tmp_path / "r", FILES)
    return GitRepository.discover(root)


def test_only_the_allowlist_survives_under_acceptance(tmp_path):
    repo = _repo(tmp_path)
    tree = reference_tree(repo, repo.tree_of(repo.head_commit()))
    paths = {b.path for b in repo.blobs_under(tree, "acceptance")}
    assert paths == {f"acceptance/{rel}" for rel in REFERENCE_ALLOWLIST}


def test_the_rest_of_the_repo_is_kept(tmp_path):
    repo = _repo(tmp_path)
    tree = reference_tree(repo, repo.tree_of(repo.head_commit()))
    assert [b.path for b in repo.blobs_under(tree, "src")] == ["src/app.py"]


def test_a_directory_named_like_an_allowlisted_file_is_dropped_whole(tmp_path):
    files = dict(FILES)
    del files["acceptance/contract/spec.md"]
    files["acceptance/contract/spec.md/test_inside.py"] = "HIDDEN-IN-DIR\n"
    root = make_repo(tmp_path / "r", files)
    repo = GitRepository.discover(root)
    tree = reference_tree(repo, repo.tree_of(repo.head_commit()))
    paths = {b.path for b in repo.blobs_under(tree, "acceptance")}
    assert paths == {"acceptance/contract/criteria.yaml"}


def test_a_repo_without_acceptance_is_returned_without_it(tmp_path):
    root = make_repo(tmp_path / "r", {"src/app.py": "print('app')\n"})
    repo = GitRepository.discover(root)
    tree = reference_tree(repo, repo.tree_of(repo.head_commit()))
    assert repo.blobs_under(tree, "acceptance") == ()
    assert [b.path for b in repo.blobs_under(tree, "src")] == ["src/app.py"]
