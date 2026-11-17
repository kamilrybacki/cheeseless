"""The CI branch check: classify, confine changed paths, advisory author check (R8)."""

from collections.abc import Mapping

from cheeseless.app.authoring import AuthorCheckResult
from cheeseless.app.ci_branch import BranchOutcome, check_branch


class StubRepo:
    def __init__(self, changes: Mapping[str, str]) -> None:
        self.changes = changes

    def merge_base(self, a: str, b: str) -> str:
        return "m" * 40

    def tree_of(self, commit: str, path: str | None = None) -> str:
        return f"tree-{commit[:4]}"

    def diff_modes(self, old: str, new: str) -> Mapping[str, str]:
        return self.changes


BASE, HEAD = "b" * 40, "h" * 40


def _check(branch, changes, author=None):
    return check_branch(StubRepo(changes), branch, BASE, HEAD, author_check=author)


def test_unknown_branch_class_fails_closed():
    out = _check("feat/x", {"src/a.py": "100644"})
    assert not out.ok and out.reason == "unknown_branch_class"


def test_gate_pr_touching_acceptance_fails():
    out = _check("gate/t1", {"acceptance/hidden/t.py": "100644"})
    assert not out.ok and out.reason == "acceptance_in_gate_pr"
    assert out.rejected == ("acceptance/hidden/t.py",)


def test_gate_pr_never_runs_the_author_check():
    def boom(_head):
        raise AssertionError("author check must not run for gate/")

    assert _check("gate/t1", {"src/a.py": "100644"}, boom).ok


def test_spec_pr_outside_acceptance_fails_without_running_the_check():
    def boom(_head):
        raise AssertionError("no check on a refused PR")

    out = _check("spec/l1", {"src/a.py": "100644"}, boom)
    assert not out.ok and out.reason == "outside_author_paths"


def test_spec_pr_runs_the_advisory_check_and_reports_problems_without_failing():
    seen = []

    def check(head):
        seen.append(head)
        return AuthorCheckResult(False, ("C1 has no test",), 0)

    out = _check("refs/heads/spec/l1", {"acceptance/contract/spec.md": "100644"}, check)
    assert seen == [HEAD]
    assert out.ok and out.reason == "ok"
    assert out.advisory == ("C1 has no test",)


def test_spec_pr_with_clean_check_has_no_advisory():
    out = _check(
        "spec/l1",
        {"acceptance/a": "100644"},
        lambda h: AuthorCheckResult(True, (), 3),
    )
    assert out.ok and out.advisory == ()


def test_a_crashing_author_check_is_advisory_not_a_verdict():
    def crash(_h):
        raise RuntimeError("sandbox missing")

    out = _check("spec/l1", {"acceptance/a": "100644"}, crash)
    assert out.ok and out.advisory == ("author check unavailable: RuntimeError",)


def test_outcome_public_form_has_no_implementation_verdict():
    out = BranchOutcome(True, "ok", "spec", (), ("x",))
    assert set(out.public()) == {"ok", "reason", "branch_class", "rejected", "advisory"}
