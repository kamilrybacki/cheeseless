"""CI routing: which checks a PR gets, decided from the live PR, not the event payload."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cheeseless.app.ci_branch import BRANCH_POLICY_CHECK, BranchOutcome, check_payload
from cheeseless.app.ci_route import resolve_route

SHA = "a" * 40
BASE = "b" * 40
REPO = "owner/repo"


def _pr(
    ref: str = "gate/t1",
    *,
    base: str = "main",
    sha: str = SHA,
    state: str = "open",
    head_repo: str = REPO,
) -> dict:
    return {
        "number": 7,
        "state": state,
        "base": {"ref": base, "sha": BASE, "repo": {"full_name": REPO}},
        "head": {"ref": ref, "sha": sha, "repo": {"full_name": head_repo}},
    }


def test_only_a_gate_pr_runs_the_implementation_judge() -> None:
    gate = resolve_route([_pr("gate/t1")], SHA, "main", REPO)
    spec = resolve_route([_pr("spec/L1")], SHA, "main", REPO)
    assert (gate.ok, gate.branch_class, gate.run_judge) == (True, "gate", True)
    assert (spec.ok, spec.branch_class, spec.run_judge) == (True, "spec", False)


def test_an_unknown_class_is_routed_to_the_policy_check_and_never_the_judge() -> None:
    route = resolve_route([_pr("feature/x")], SHA, "main", REPO)
    assert (route.ok, route.branch_class, route.run_judge) == (True, "unknown", False)


@pytest.mark.parametrize(
    "prs",
    [
        [],
        [_pr(), _pr()],
        [_pr(state="closed")],
        [_pr(base="release")],
        [_pr(sha="c" * 40)],
    ],
)
def test_an_unverifiable_pr_is_refused_and_publishes_nothing(prs: list[dict]) -> None:
    route = resolve_route(prs, SHA, "main", REPO)
    assert not route.ok and not route.run_judge and route.reason


def test_the_route_carries_the_validated_facts() -> None:
    route = resolve_route([_pr("spec/L1")], SHA, "main", REPO)
    assert (route.number, route.base_sha, route.branch) == (7, BASE, "spec/L1")


@pytest.mark.parametrize("bad", [None, "x", {"number": "7"}, {"head": 1}])
def test_malformed_pr_json_is_refused_not_raised(bad: Any) -> None:
    assert not resolve_route([bad], SHA, "main", REPO).ok


def test_a_spec_check_is_named_for_branch_policy_and_is_never_the_judge_check() -> None:
    ok = check_payload(BranchOutcome(True, "ok", "spec", (), ("note",)), SHA)
    assert ok["name"] == BRANCH_POLICY_CHECK != "cheeseless judge"
    assert ok["head_sha"] == SHA and ok["conclusion"] == "success"
    assert "not an implementation verdict" in ok["output"]["summary"]
    assert "note" in ok["output"]["summary"]


@pytest.mark.parametrize(
    "outcome",
    [
        BranchOutcome(False, "unknown_branch_class", "unknown"),
        BranchOutcome(False, "outside_author_paths", "spec", ("src/x.py",)),
        BranchOutcome(False, "acceptance_in_gate_pr", "gate", ("acceptance/a",)),
    ],
)
def test_a_refused_outcome_posts_a_failing_check(outcome: BranchOutcome) -> None:
    payload = check_payload(outcome, SHA)
    assert payload["conclusion"] == "failure" and payload["status"] == "completed"
    json.dumps(payload)


def test_a_fork_pr_named_like_a_gate_branch_is_refused_and_never_judged() -> None:
    route = resolve_route([_pr("gate/t1", head_repo="evil/repo")], SHA, "main", REPO)
    assert not route.ok and not route.run_judge
    assert route.reason == "head_repo_is_not_this_repository"


@pytest.mark.parametrize(
    "head", [{"ref": "gate/t1", "sha": SHA}, {"ref": "gate/t1", "sha": SHA, "repo": None}]
)
def test_a_pr_with_no_head_repo_is_refused(head: dict) -> None:
    pr = _pr()
    pr["head"] = head
    route = resolve_route([pr], SHA, "main", REPO)
    assert not route.ok and not route.run_judge
    assert route.reason == "head_repo_is_not_this_repository"


def test_a_base_repo_that_is_not_this_repository_is_refused() -> None:
    pr = _pr()
    pr["base"]["repo"] = {"full_name": "other/repo"}
    assert not resolve_route([pr], SHA, "main", REPO).ok
