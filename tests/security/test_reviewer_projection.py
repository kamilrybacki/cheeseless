"""ADR 0015 on the real path: the reviewer reads the tests and reaches no implementation.

The reviewer is the one session that must see the author's tests. That makes it the one
session where a leak of the candidate's code would matter most: its verdict decides whether
the suite freezes, so its input must be the tests and nothing else.
"""

import inspect
import os
import subprocess
from pathlib import Path

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.app.reference_view import reviewer_tree
from cheeseless.domain.session_role import SessionRole, acceptance_mount
from tests.requirements import needs
from tests.toy import make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/git", os.X_OK),
    "bwrap and git are required",
)

CODE_CANARY = "CANARY-CODE-5a2f"
HIDDEN = "CANARY-HIDDEN-b7e1"
CONTRACT = "CANARY-CONTRACT-3c90"
SPEC = "SPEC-TEXT-visible"
FILES = {
    "src/app.py": f"print('{CODE_CANARY}')\n",
    "acceptance/contract/spec.md": SPEC,
    "acceptance/contract/criteria.yaml": "CRITERIA",
    "acceptance/contract/test_smoke.py": CONTRACT,
    "acceptance/hidden/test_hidden.py": HIDDEN,
}


def _projection(tmp_path: Path) -> Path:
    root = make_repo(tmp_path / "r", FILES)
    repo = GitRepository.discover(root)
    tree = reviewer_tree(repo, repo.tree_of(repo.head_commit()))
    proj = tmp_path / "proj"
    repo.create_projection(tree, proj)
    return proj


def _inside(proj: Path, tmp_path: Path, script: str) -> tuple[str, int]:
    home = tmp_path / "home"
    home.mkdir()
    fd = profiles.open_acceptance(proj)
    try:
        argv = profiles.reviewer(
            bwrap="/usr/bin/bwrap",
            projection=proj,
            home=home,
            env=[],
            command=["/bin/sh", "-c", script],
            acceptance_fd=fd,
        )
        out = subprocess.run(  # noqa: S603
            argv, capture_output=True, pass_fds=(fd,), timeout=60
        )
    finally:
        os.close(fd)
    return out.stdout.decode(errors="replace"), out.returncode


def test_the_reviewer_reads_every_test_and_no_code(tmp_path: Path) -> None:
    proj = _projection(tmp_path)
    out, code = _inside(
        proj,
        tmp_path,
        "cat acceptance/hidden/test_hidden.py "
        "acceptance/contract/test_smoke.py acceptance/contract/spec.md",
    )
    assert code == 0
    # It must see the tests: that is what it was launched to judge.
    assert HIDDEN in out and CONTRACT in out and SPEC in out
    # And it must not see the implementation, even by walking the tree.
    assert CODE_CANARY not in out


def test_the_candidates_code_is_not_in_the_projection_at_all(tmp_path: Path) -> None:
    """Not merely unmounted: never written, so its .git holds no such blob either."""
    proj = _projection(tmp_path)
    for path in proj.rglob("*"):
        if path.is_file():
            assert CODE_CANARY.encode() not in path.read_bytes(), path


def test_the_candidates_code_is_absent_from_the_git_objects(tmp_path: Path) -> None:
    proj = _projection(tmp_path)
    out = subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(proj), "cat-file", "--batch-all-objects", "--batch"],
        capture_output=True,
        timeout=60,
    )
    assert CODE_CANARY.encode() not in out.stdout


def test_the_reviewer_cannot_write_the_tests_it_judges(tmp_path: Path) -> None:
    """The whole point: a reviewer that could edit the suite could make its own review pass."""
    proj = _projection(tmp_path)
    out, code = _inside(
        proj, tmp_path, "echo broken > acceptance/hidden/test_hidden.py 2>&1; echo rc=$?"
    )
    assert "rc=0" not in out
    assert HIDDEN in (proj / "acceptance/hidden/test_hidden.py").read_text()


def test_the_reviewer_cannot_write_the_projection_root(tmp_path: Path) -> None:
    proj = _projection(tmp_path)
    out, _ = _inside(proj, tmp_path, "echo x > newfile 2>&1; echo rc=$?")
    assert "rc=0" not in out
    assert not (proj / "newfile").exists()


def test_the_reviewer_can_write_its_own_home(tmp_path: Path) -> None:
    """It still has to run a toolchain, so HOME is writable even though the tree is not."""
    proj = _projection(tmp_path)
    out, code = _inside(proj, tmp_path, 'echo keep > "$HOME/scratch"; echo rc=$?')
    assert "rc=0" in out and code == 0
    assert (tmp_path / "home" / "scratch").read_text().strip() == "keep"


def test_the_reviewer_mount_is_read_only() -> None:
    assert acceptance_mount(SessionRole.REVIEWER) == "ro"


def test_the_reviewer_projection_is_built_from_the_reviewer_tree(tmp_path: Path) -> None:
    """`_projection_tree` must cut for a reviewer too, not only for a reference."""
    root = make_repo(tmp_path / "r2", FILES)
    repo = GitRepository.discover(root)
    reference = bootstrap._projection_tree(repo, None, SessionRole.REFERENCE)
    reviewer = bootstrap._projection_tree(repo, None, SessionRole.REVIEWER)
    implementer = bootstrap._projection_tree(repo, None, SessionRole.IMPLEMENTER)
    assert reference != reviewer, "a reviewer must see more than a reference"
    assert reviewer != implementer, "a reviewer must see less than an implementer"


def test_the_reviewer_is_shown_the_proposed_tree_not_head(tmp_path: Path) -> None:
    """Found in review twice: an earlier fixture made HEAD and the subject the same tree, so
    falling back to HEAD passed. Here the proposal differs from HEAD and from the public
    projection, and each carries its own canary."""
    import types

    STALE = "CANARY-STALE-IN-HEAD-1122"
    root = make_repo(
        tmp_path / "r3",
        {
            "src/app.py": f"{CODE_CANARY}\n",
            "acceptance/contract/spec.md": SPEC,
            "acceptance/contract/test_smoke.py": CONTRACT,
            "acceptance/hidden/test_old.py": f"{STALE}\n",
        },
    )
    repo = GitRepository.discover(root)
    head = repo.head_commit()
    head_acceptance = repo.tree_of(head, "acceptance")
    # The proposal dropped the stale test; HEAD still holds it. A HEAD fallback would show it.
    proposed = repo.tree_without(head_acceptance, "hidden/test_old.py", ())
    frozen = types.SimpleNamespace(
        acceptance_tree=proposed,
        projection_tree=repo.tree_without(repo.tree_of(head), "acceptance", ()),
    )
    reviewer = bootstrap._projection_tree(repo, frozen, SessionRole.REVIEWER)
    proj = tmp_path / "proj"
    repo.create_projection(reviewer, proj)
    files = [p for p in proj.rglob("*") if p.is_file() and ".git" not in p.parts]
    text = "".join(p.read_text(errors="replace") for p in files)
    # The tree the verdict binds, and not the one HEAD happens to hold.
    assert CONTRACT in text and SPEC in text
    assert STALE not in text, "the reviewer was shown HEAD, not the proposed acceptance tree"
    assert CODE_CANARY not in text
    paths = {str(p.relative_to(proj)) for p in files}
    assert paths and all(p.startswith("acceptance/") for p in paths), paths


def test_an_empty_acceptance_tree_is_refused(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r4", {"src/a.py": "x\n"})
    repo = GitRepository.discover(root)
    with pytest.raises(ValueError, match="acceptance"):
        reviewer_tree(repo, repo.tree_of(repo.head_commit()))
    # A well-formed but empty acceptance tree: nothing to judge is a refusal, not a pass.
    from cheeseless.app.reference_view import reviewer_tree_of

    empty = repo.tree_keeping(repo.tree_of(repo.head_commit()), "src")
    empty = repo.tree_without(empty, "src", ())
    with pytest.raises(ValueError, match="empty"):
        reviewer_tree_of(repo, empty)


def test_a_projection_holding_extra_material_is_refused(tmp_path: Path) -> None:
    """The reviewer is given the hidden tests, so `stray_hidden` cannot guard it.

    Found in review: the exact-tree check must catch a stray implementation blob, which is the
    thing `stray_hidden` exists to catch for every other role.
    """
    root = make_repo(tmp_path / "r5", FILES)
    repo = GitRepository.discover(root)
    tree = repo.tree_of(repo.head_commit(), "acceptance")
    proj = tmp_path / "proj"
    repo.create_projection(reviewer_tree(repo, repo.tree_of(repo.head_commit())), proj)
    # A blob that is not in the selected tree appears in the projection.
    (proj / "src").mkdir()
    (proj / "src" / "leak.py").write_text(CODE_CANARY)
    with pytest.raises(RuntimeError, match="not the selected tree"):
        bootstrap._refuse_wrong_reviewer_tree(repo, proj, tree)


def test_a_correct_projection_passes_the_exact_tree_check(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r6", FILES)
    repo = GitRepository.discover(root)
    tree = reviewer_tree(repo, repo.tree_of(repo.head_commit()))
    proj = tmp_path / "proj"
    repo.create_projection(tree, proj)
    bootstrap._refuse_wrong_reviewer_tree(repo, proj, tree)  # must not raise


def test_a_reviewer_session_refuses_operator_seeds(tmp_path: Path) -> None:
    """Seeds are an unfiltered route for candidate content, so the refusal is in host_session.

    Driving `host_session` needs a full context, so this asserts on the refusal branch being
    reached by role — and `tests/unit/test_reviewer_role.py` pins that the reviewer is the
    only role for which it fires.
    """
    from cheeseless import bootstrap

    source = inspect.getsource(bootstrap.host_session)
    marker = "bound is SessionRole.REVIEWER and (extras.seeds or extras.read_only)"
    assert marker in source
    after = source[source.index(marker) :]
    assert "SessionOptionError" in after.split("\n\n")[0]


def test_a_reviewer_session_cannot_inherit_operator_stdio() -> None:
    """Found in review: stdio can carry content the verdict does not bind."""
    import inspect

    from cheeseless import bootstrap

    source = inspect.getsource(bootstrap.host_session)
    assert "a reviewer session cannot inherit the operator's stdio" in source
