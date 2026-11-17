"""Role-aware promote is confined per role and never carries hidden content (ADR 0009, R2/R15/R17).

Author promote may carry acceptance/ only and only onto spec/<lineage>; implementer promote may
carry anything except acceptance/ and only onto gate/<task>. A stale tree is refused, and neither
side ever publishes hidden-test or holdout content.
"""

import os
import subprocess
from dataclasses import replace

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
from cheeseless.domain.authoring import TaskPhase
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


def _host(host, *args: str, data: bytes | None = None) -> bytes:
    """Run git against the host repository; any failure is a test error, never an empty result."""
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(host), *args],  # noqa: S607
        input=data,
        capture_output=True,
        check=True,
    ).stdout


def _newly_added(host, ref: str) -> dict[str, bytes]:
    """Path -> content of every file ``ref`` adds or changes relative to the default branch.

    Baseline hidden tests legitimately live in host branches, so only content the promote itself
    introduced is inspected. Binary blobs are read too."""
    _host(host, "rev-parse", "--verify", f"{ref}^{{commit}}")
    raw = _host(host, "diff-tree", "-r", "-z", "--no-renames", "--diff-filter=AM", "main", ref)
    fields = raw.split(b"\0")
    added: dict[str, bytes] = {}
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta:
            continue
        new_oid = meta.split()[3].decode()
        added[path.decode()] = _host(host, "cat-file", "blob", new_oid)
    return added


def _leaks(host, ref: str) -> list[str]:
    return [
        path
        for path, content in _newly_added(host, ref).items()
        if CANARY.encode() in content or SENTINEL.encode() in content
    ]


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
    added = _newly_added(repo.root, out.ref)
    assert added  # the control below is meaningless if the scan saw nothing
    assert _leaks(repo.root, out.ref) == []
    assert not [p for p in added if p == "acceptance" or p.startswith("acceptance/")]


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


class _ApprovalLands:
    """Store wrapper: approval takes the lineage between promote's checks and its publication."""

    def __init__(self, inner, lineage):
        self._inner, self._lineage = inner, lineage

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def load_author_check(self, lineage_id):
        saved = self._inner.load_author_check(lineage_id)
        state = self._inner.authoring_state(lineage_id, saved[0])
        self._inner.save_authoring_state(replace(state, phase=TaskPhase.APPROVING))
        return saved


def test_author_promote_loses_a_race_with_approval_and_publishes_nothing(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    racing = _ApprovalLands(store, _lineage(repo))
    with pytest.raises(AuthorError) as err:
        promote_author(repo, racing, worktree=wt)
    assert err.value.kind == "approval_conflict"
    ref = f"refs/heads/spec/{_lineage(repo)}"
    probe = subprocess.run(  # noqa: S603
        ["git", "-C", str(wt), "rev-parse", "--verify", "-q", ref],  # noqa: S607
        capture_output=True,
    )
    assert probe.returncode != 0


def test_author_promote_adds_only_the_checked_acceptance_tree_and_no_hidden_markers(author):
    repo, store, wt = author
    _edit_acceptance(wt)
    assert _check(repo, store, wt).ok
    out = promote_author(repo, store, worktree=wt)
    added = _newly_added(repo.root, out.ref)
    assert set(added) == {"acceptance/hidden/test_extra.py"}
    assert _leaks(repo.root, out.ref) == []


def _contaminated_ref(host, tmp_path, ref: str, content: bytes) -> None:
    """Commit main plus a src/toy/leak.bin carrying ``content`` onto ``ref`` with plumbing."""
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "leak-index")}

    def plumbing(*args: str, data: bytes | None = None) -> str:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(host), *args],  # noqa: S607
            input=data,
            capture_output=True,
            check=True,
            env=env,
        )
        return out.stdout.decode().strip()

    blob = plumbing("hash-object", "-w", "--stdin", data=content)
    plumbing("read-tree", "main")
    plumbing("update-index", "--add", "--cacheinfo", f"100644,{blob},src/toy/leak.bin")
    tree = plumbing("write-tree")
    commit = plumbing(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit-tree",
        tree,
        "-p",
        "main",
        "-m",
        "contaminated",
    )
    plumbing("update-ref", ref, commit)


@pytest.mark.parametrize("marker", [CANARY, SENTINEL])
def test_the_leak_scan_detects_a_contaminated_ref_binary_included(author, tmp_path, marker):
    """Control: hidden content on a ref must be flagged, so a clean result above is evidence
    and not an empty scan."""
    repo, _store, _wt = author
    ref = "refs/heads/gate/contaminated"
    _contaminated_ref(repo.root, tmp_path, ref, b"\0\xff" + marker.encode() + b"\0")
    assert _leaks(repo.root, ref) == ["src/toy/leak.bin"]


def test_the_leak_scan_fails_loudly_on_a_missing_ref(author):
    repo, _store, _wt = author
    with pytest.raises(subprocess.CalledProcessError):
        _leaks(repo.root, "refs/heads/gate/does-not-exist")
