"""CI branch classes and the per-class changed-path rule (ADR 0009, R8)."""

import pytest

from cheeseless.domain.ci_branch import BranchClass, classify_branch, decide_branch_paths


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("gate/abc123", BranchClass.GATE),
        ("refs/heads/gate/abc123", BranchClass.GATE),
        ("spec/lineage1", BranchClass.SPEC),
        ("refs/heads/spec/lineage1", BranchClass.SPEC),
        ("main", None),
        ("feat/x", None),
        ("gate/", None),
        ("spec/", None),
        ("gates/x", None),
        ("GATE/x", None),
        ("gate/a/../b", None),
        ("gate/a b", None),
        ("", None),
        ("xgate/x", None),
    ],
)
def test_branch_class(name, expected):
    assert classify_branch(name) is expected


def test_unknown_branch_class_is_rejected():
    verdict = decide_branch_paths(None, {"src/a.py": "100644"})
    assert not verdict.ok and verdict.reason == "unknown_branch_class"


def test_gate_pr_may_not_touch_acceptance():
    verdict = decide_branch_paths(
        BranchClass.GATE, {"src/a.py": "100644", "acceptance/hidden/t.py": "100644"}
    )
    assert not verdict.ok
    assert verdict.reason == "acceptance_in_gate_pr"
    assert verdict.rejected == ("acceptance/hidden/t.py",)


def test_gate_pr_with_only_source_changes_passes_this_check():
    assert decide_branch_paths(BranchClass.GATE, {"src/a.py": "100644"}).ok


def test_spec_pr_may_only_touch_acceptance():
    verdict = decide_branch_paths(
        BranchClass.SPEC,
        {"acceptance/contract/spec.md": "100644", "src/a.py": "100644", ".github/ci.yml": "100644"},
    )
    assert not verdict.ok
    assert verdict.reason == "outside_author_paths"
    assert verdict.rejected == (".github/ci.yml", "src/a.py")


def test_spec_pr_refuses_symlinks_even_under_acceptance():
    verdict = decide_branch_paths(BranchClass.SPEC, {"acceptance/l": "120000"})
    assert not verdict.ok and verdict.rejected == ("acceptance/l",)


def test_spec_pr_inside_acceptance_passes():
    ok = decide_branch_paths(
        BranchClass.SPEC, {"acceptance/hidden/t.py": "100644", "acceptance/old.py": "000000"}
    )
    assert ok.ok and ok.reason == "ok"


def test_an_empty_change_set_passes_for_known_classes():
    assert decide_branch_paths(BranchClass.SPEC, {}).ok
    assert decide_branch_paths(BranchClass.GATE, {}).ok
