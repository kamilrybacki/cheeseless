"""Role-aware promote is confined per role and never carries hidden content (ADR 0009, R2/R15/R17).

Author promote may carry acceptance/ only and only onto spec/<lineage>; implementer promote may
carry anything except acceptance/ and only onto gate/<task>. A stale tree is refused, and neither
side ever publishes hidden-test or holdout content.
"""

import subprocess

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.authoring import AuthorError
from cheeseless.app.freeze import commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import PromoteRejected, promote, promote_author
from tests.toy import CANARY, FIXED_MAIN, SENTINEL
from tests.unit.app.test_author_promote import _check, _edit_acceptance, _lineage, _ref_files


@pytest.fixture
def author(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    return repo, FileStore(store_root(repo.common_dir(), tmp_path / "state")), toy_repo


@pytest.fixture
def implementer(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner, sandbox = PytestRunner(), BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    frozen = commit(prepared, store)
    wt = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, wt)
    return repo, runner, sandbox, store, wt, frozen


def _all_blobs(worktree, ref) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree), "grep", "-I", "-l", "-e", CANARY, "-e", SENTINEL, ref],  # noqa: S607
        capture_output=True,
        text=True,
    )
    return out.stdout


@pytest.mark.parametrize(
    "path",
    ["src/toy/evil.py", ".github/workflows/x.yml", ".cheeseless/config.toml", "pyproject.toml"],
)
def test_author_promote_carries_acceptance_paths_only(author, path):
    repo, store, wt = author
    _edit_acceptance(wt)
    target = wt / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")
    assert _check(repo, store, wt).ok
    with pytest.raises(PromoteRejected) as err:
        promote_author(repo, store, worktree=wt)
    assert path in err.value.rejected


def test_author_promote_never_writes_outside_spec_refs(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    out = promote_author(repo, store, worktree=wt)
    assert out.ref == f"refs/heads/spec/{_lineage(repo)}"
    assert "acceptance/hidden/test_extra.py" in _ref_files(wt, out.ref)


def test_author_promote_on_a_stale_tree_is_refused(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    _edit_acceptance(wt, text="def test_e(candidate):\n    assert 1 == 1\n")
    with pytest.raises(AuthorError) as err:
        promote_author(repo, store, worktree=wt)
    assert err.value.kind == "stale_author_check"


def test_the_author_ref_differs_from_the_default_branch_only_in_the_authored_paths(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    out = promote_author(repo, store, worktree=wt)
    changed = repo.diff_modes(repo.tree_of(repo.default_branch_commit()), repo.tree_of(out.commit))
    assert set(changed) == {"acceptance/hidden/test_extra.py"}


def test_implementer_promote_refuses_any_acceptance_change(implementer):
    repo, runner, sandbox, store, wt, _ = implementer
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    (wt / "acceptance" / "hidden").mkdir(parents=True, exist_ok=True)
    (wt / "acceptance" / "hidden" / "x.py").write_text("x")
    gate_run(repo, store, runner, sandbox, worktree=wt)
    with pytest.raises(PromoteRejected) as err:
        promote(repo, store, worktree=wt)
    assert "acceptance/hidden/x.py" in err.value.rejected


def test_implementer_promote_lands_on_gate_and_never_carries_hidden_content(implementer):
    repo, runner, sandbox, store, wt, _ = implementer
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    gate_run(repo, store, runner, sandbox, worktree=wt)
    out = promote(repo, store, worktree=wt)
    assert out.ref.startswith("refs/heads/gate/")
    assert _all_blobs(wt, out.ref) == ""


def test_implementer_promote_cannot_be_pointed_at_a_spec_ref(implementer):
    repo, runner, sandbox, store, wt, _ = implementer
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    gate_run(repo, store, runner, sandbox, worktree=wt)
    with pytest.raises(ValueError, match="gate/"):
        promote(repo, store, worktree=wt, ref="refs/heads/spec/x")


def test_implementer_promote_on_a_stale_tree_is_refused(implementer):
    repo, runner, sandbox, store, wt, _ = implementer
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    gate_run(repo, store, runner, sandbox, worktree=wt)
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN + "\n# edited after the gate\n")
    with pytest.raises(ValueError, match="does not match"):
        promote(repo, store, worktree=wt)


def test_author_state_alone_cannot_authorise_an_implementer_promote(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    assert uc.read_contract(repo, repo.snapshot(wt)).lineage_id
    with pytest.raises(ValueError):
        promote(repo, store, worktree=wt)
