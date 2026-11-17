"""The single trusted CI workflow: route first, then judge (gate/) or branch policy (R8, S2/S3)."""

import re
from pathlib import Path
from typing import Any

import yaml

from cheeseless.app.ci_branch import BRANCH_POLICY_CHECK

DOCS = Path(__file__).resolve().parents[2] / "docs" / "ci"
WORKFLOW = DOCS / "judge.yml"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _wf() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _jobs() -> dict[str, Any]:
    return _wf()["jobs"]


def _runs(job: str) -> str:
    return "\n".join(s.get("run", "") for s in _jobs()[job]["steps"])


def test_there_is_one_workflow_and_no_second_route_around_it():
    assert not (DOCS / "branch.yml").exists()
    assert set(_jobs()) == {"route", "judge", "branch-policy"}
    assert _wf()["permissions"] == {}


def test_the_route_job_holds_no_secrets_and_decides_through_python():
    route = _jobs()["route"]
    assert "environment" not in route
    runs = _runs("route")
    assert "cheeseless ci-route" in runs and "commits/${HEAD_SHA}/pulls" in runs
    assert "secrets." not in yaml.safe_dump(route)
    assert "create-github-app-token" not in yaml.safe_dump(route)


def test_only_a_gate_route_reaches_the_implementation_judge():
    judge = _jobs()["judge"]
    assert judge["needs"] == "route" or "route" in judge["needs"]
    assert "run_judge" in judge["if"] and "'true'" in judge["if"]
    assert "ci-judge" in _runs("judge")
    assert "ci-judge" not in _runs("branch-policy")
    assert "ci-judge" not in _runs("route")


def test_branch_policy_runs_for_every_validated_pr_and_publishes_the_policy_check():
    policy = _jobs()["branch-policy"]
    assert "route" in policy["needs"] and "if" not in policy
    runs = _runs("branch-policy")
    assert "cheeseless ci-branch" in runs and "--check-payload" in runs
    assert "repos/${REPO}/check-runs" in runs and "--input" in runs
    assert BRANCH_POLICY_CHECK in runs
    assert "cheeseless judge" not in runs
    assert policy["environment"] == "cheeseless-judge"
    assert any("create-github-app-token" in s.get("uses", "") for s in policy["steps"])


def test_the_published_check_is_bound_to_the_validated_head_and_pr():
    runs = _runs("branch-policy")
    assert "pulls/${PR_NUMBER}" in runs and ".head.sha" in runs
    assert "head_sha" in runs and '"$HEAD_SHA"' in runs


def test_the_branch_name_reaches_a_shell_only_through_an_env_var():
    for job in _jobs().values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            assert "head_branch" not in run and "outputs.branch" not in run, step
    lines = [ln.strip() for ln in WORKFLOW.read_text().splitlines() if "outputs.branch" in ln]
    assert sorted(lines) == [
        "BRANCH: ${{ needs.route.outputs.branch }}",
        "branch: ${{ steps.route.outputs.branch }}",
    ]


def test_the_pr_head_is_fetched_as_objects_only_and_actions_are_pinned():
    assert "refs/cheeseless/pr-head" in WORKFLOW.read_text()
    for job in _jobs().values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert PINNED.match(step["uses"]), step["uses"]
