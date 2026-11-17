"""Author promote onto spec/<lineage> (ledger Q3): needs a fresh author_check PASS (R15, R17)."""

import subprocess

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.authoring import AuthorError
from cheeseless.app.promote import PromoteRejected, promote_author
from cheeseless.ports.repository import SnapshotRejected
from tests.unit.app.test_authoring import FakeAuthorSandbox, _line, _source


@pytest.fixture
def world(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    return repo, store, toy_repo


def _lineage(repo):
    return uc.read_contract(repo, _source(repo)).lineage_id


def _check(repo, store, worktree):
    return uc.author_check(repo, FakeAuthorSandbox(_line()), repo.snapshot(worktree), store=store)


def _edit_acceptance(
    worktree, name="hidden/test_extra.py", text="def test_e(candidate):\n    pass\n"
):
    path = worktree / "acceptance" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _ref_files(worktree, ref):
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree), "ls-tree", "-r", "--name-only", ref],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.split())


def test_a_passing_author_check_is_recorded_on_the_acceptance_tree(world):
    repo, store, wt = world
    assert _check(repo, store, wt).ok
    spec, tree = store.load_author_check(_lineage(repo))
    contract = uc.read_contract(repo, _source(repo))
    assert spec == uc.spec_of(contract)
    assert tree == repo.tree_of(_source(repo), "acceptance")


def test_a_failing_author_check_records_nothing(world):
    repo, store, wt = world
    bad = [{"node_id": "t::a", "criteria": ["nope"]}]
    result = uc.author_check(repo, FakeAuthorSandbox(_line(bad)), repo.snapshot(wt), store=store)
    assert not result.ok
    assert store.load_author_check(_lineage(repo)) is None


def test_author_promote_publishes_the_acceptance_change_on_spec_ref(world):
    repo, store, wt = world
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    out = promote_author(repo, store, worktree=wt)
    assert out.ref == f"refs/heads/spec/{_lineage(repo)}"
    assert "acceptance/hidden/test_extra.py" in _ref_files(wt, out.ref)
    assert repo.tree_of(out.commit, "acceptance") == repo.tree_of(repo.snapshot(wt), "acceptance")


def test_author_promote_without_a_check_is_refused_with_a_reason(world):
    repo, store, wt = world
    _edit_acceptance(wt)
    with pytest.raises(AuthorError) as err:
        promote_author(repo, store, worktree=wt)
    assert err.value.kind == "no_author_check"


def test_author_promote_after_the_tree_changed_is_refused_as_stale(world):
    repo, store, wt = world
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    _edit_acceptance(wt, text="def test_e(candidate):\n    assert True\n")
    with pytest.raises(AuthorError) as err:
        promote_author(repo, store, worktree=wt)
    assert err.value.kind == "stale_author_check"


def test_author_promote_refuses_a_non_acceptance_path(world):
    repo, store, wt = world
    _edit_acceptance(wt)
    (wt / "src" / "toy" / "evil.py").write_text("x = 1\n")
    assert _check(repo, store, wt).ok
    with pytest.raises(PromoteRejected) as err:
        promote_author(repo, store, worktree=wt)
    assert err.value.rejected == ("src/toy/evil.py",)


def test_author_promote_refuses_a_symlink_out_of_the_tree(world):
    repo, store, wt = world
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    (wt / "acceptance" / "hidden" / "link").symlink_to("/etc/passwd")
    with pytest.raises(SnapshotRejected):
        promote_author(repo, store, worktree=wt)
    assert not _ref_exists(wt, f"refs/heads/spec/{_lineage(repo)}")


@pytest.mark.parametrize("seal", ["approving", "frozen"])
def test_author_promote_is_refused_once_approval_began(world, seal):
    repo, store, wt = world
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    contract = uc.read_contract(repo, _source(repo))
    state = store.authoring_state(contract.lineage_id, uc.spec_of(contract))
    state = store.save_authoring_state(state.proposed())
    state = store.save_authoring_state(state.approving())
    if seal == "frozen":
        state = store.save_authoring_state(state.frozen())
    with pytest.raises(AuthorError) as err:
        promote_author(repo, store, worktree=wt)
    assert err.value.kind == "frozen"
    assert not _ref_exists(wt, f"refs/heads/spec/{contract.lineage_id}")


def _ref_exists(worktree, ref):
    return (
        subprocess.run(  # noqa: S603
            ["git", "-C", str(worktree), "rev-parse", "--verify", "-q", ref],  # noqa: S607
            capture_output=True,
        ).returncode
        == 0
    )
