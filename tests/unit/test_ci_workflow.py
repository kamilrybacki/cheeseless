"""The single trusted CI workflow: route, judge (gate/), evaluate, post (R8, S2/S3, H1, M3)."""

import re
from pathlib import Path
from typing import Any

import yaml

from cheeseless.app.ci_branch import BRANCH_POLICY_CHECK
from cheeseless.app.ci_gate import GATE_CHECK, JUDGE_CHECK

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


def _dump(job: str) -> str:
    return yaml.safe_dump(_jobs()[job])


def _fetches_or_runs_pr_code(job: str) -> bool:
    runs = _runs(job)
    return "refs/pull/" in runs or "ci-judge" in runs or "ci-branch" in runs


def test_there_is_one_workflow_and_no_second_route_around_it():
    assert not (DOCS / "branch.yml").exists()
    assert set(_jobs()) == {"route", "policy", "judge", "evaluate", "post"}
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
    assert "route" in judge["needs"]
    assert "run_judge" in judge["if"] and "'true'" in judge["if"]
    for other in ("route", "policy", "evaluate", "post"):
        assert "ci-judge" not in _runs(other)


def test_evaluate_is_unprivileged_advisory_and_feeds_nothing_to_the_gate():
    """S2/S18: evaluate runs PR content in bwrap, so nothing it writes can reach the gate."""
    evaluate = _jobs()["evaluate"]
    assert "route" in evaluate["needs"] and "if" not in evaluate
    assert "environment" not in evaluate and "outputs" not in evaluate
    text = _dump("evaluate")
    for forbidden in ("secrets.", "vars.", "create-github-app-token", "check-runs"):
        assert forbidden not in text
    runs = _runs("evaluate")
    assert "cheeseless ci-branch" in runs and "--check-payload" not in runs
    assert not any("upload-artifact" in s.get("uses", "") for s in evaluate["steps"])


def test_the_path_policy_is_decided_in_a_job_that_runs_no_pr_code():
    """S18: branch class and path confinement come from `policy`, never from evaluate."""
    policy = _jobs()["policy"]
    assert "route" in policy["needs"] and "environment" not in policy
    text = _dump("policy")
    for forbidden in (
        "secrets.",
        "vars.",
        "create-github-app-token",
        "check-runs",
        "upload-artifact",
    ):
        assert forbidden not in text
    runs = _runs("policy")
    assert "--check-payload" in runs and "--no-author-check" in runs
    assert "bwrap" not in runs and "bubblewrap" not in runs and "ci-judge" not in runs
    assert "payload" in policy["outputs"]


def test_post_takes_the_policy_from_the_policy_job_and_never_from_an_artifact():
    post = _jobs()["post"]
    assert "policy" in post["needs"]
    assert post["env"]["POLICY_PAYLOAD"] == "${{ needs.policy.outputs.payload }}"
    downloads = [s for s in post["steps"] if "download-artifact" in s.get("uses", "")]
    assert [d["with"]["name"] for d in downloads] == ["verdict"]


def test_no_job_that_fetches_or_runs_pr_code_can_see_either_app_or_post_checks():
    """M3/S17: the checks credential and any PR code never share a job."""
    for name in _jobs():
        if _fetches_or_runs_pr_code(name):
            text = _dump(name)
            assert "CHECKS_APP" not in text, name
            assert "permission-checks" not in text, name
            assert "check-runs" not in text, name
            if name != "judge":
                assert "LEDGER_APP" not in text, name


def test_only_the_post_job_references_the_checks_app():
    for name in _jobs():
        assert ("CHEESELESS_CHECKS_APP" in _dump(name)) == (name == "post"), name


def test_the_judge_job_references_only_the_ledger_app():
    text = _dump("judge")
    assert "CHEESELESS_LEDGER_APP_KEY" in text and "CHEESELESS_LEDGER_APP_ID" in text
    assert "CHEESELESS_CHECKS_APP" not in text and "CHEESELESS_JUDGE_APP" not in text
    assert "CHEESELESS_JUDGE_APP" not in WORKFLOW.read_text().replace("cheeseless-judge", "")


def test_the_judge_job_uses_the_ledger_app_only_for_the_budget_token():
    judge = _jobs()["judge"]
    mints = [s for s in judge["steps"] if "create-github-app-token" in s.get("uses", "")]
    assert len(mints) == 1
    assert mints[0]["with"]["permission-contents"] == "write"
    assert "permission-checks" not in mints[0]["with"]
    assert "check-runs" not in _dump("judge")


def test_the_post_job_is_the_only_publisher_and_never_touches_pr_code():
    post = _jobs()["post"]
    assert post["environment"] == "cheeseless-judge"
    assert {"route", "policy", "judge", "evaluate"} <= set(post["needs"])
    assert "always()" in post["if"]
    runs = _runs("post")
    assert not _fetches_or_runs_pr_code("post") and "git fetch" not in runs
    assert "cheeseless ci-gate" in runs
    assert "pulls/${PR_NUMBER}" in runs and ".head.sha" in runs
    assert "repos/${REPO}/check-runs" in runs
    checkout = [s for s in post["steps"] if "checkout" in s.get("uses", "")]
    assert len(checkout) == 1
    assert checkout[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    for name in ("route", "judge", "evaluate"):
        assert "check-runs" not in _dump(name)


def test_the_required_gate_check_name_is_the_one_ci_gate_publishes():
    assert GATE_CHECK == "cheeseless gate"
    assert BRANCH_POLICY_CHECK == "cheeseless branch policy"
    assert JUDGE_CHECK == "cheeseless judge"


def test_the_published_check_is_bound_to_the_validated_head_and_pr():
    runs = _runs("post")
    assert '"$HEAD_SHA"' in runs and "check-runs" in runs
    assert "--head" in runs and "--branch-class" in runs


def test_the_branch_name_reaches_a_shell_only_through_an_env_var():
    for job in _jobs().values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            assert "head_branch" not in run and "outputs.branch" not in run, step
    lines = [ln.strip() for ln in WORKFLOW.read_text().splitlines() if "outputs.branch }}" in ln]
    assert sorted(lines) == [
        "BRANCH: ${{ needs.route.outputs.branch }}",  # policy
        "BRANCH: ${{ needs.route.outputs.branch }}",  # evaluate
        "branch: ${{ steps.route.outputs.branch }}",
    ]
    assert "BRANCH_CLASS: ${{ needs.route.outputs.branch_class }}" in WORKFLOW.read_text()


def test_the_pr_head_is_fetched_as_objects_only_and_actions_are_pinned():
    assert "refs/cheeseless/pr-head" in WORKFLOW.read_text()
    for job in _jobs().values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert PINNED.match(step["uses"]), step["uses"]


def test_every_downloaded_or_uploaded_artifact_action_is_pinned():
    text = WORKFLOW.read_text()
    assert "upload-artifact@" in text and "download-artifact@" in text


def test_ci_workflow_reruns_routing_when_the_base_is_retargeted():
    """L1: a retarget fires `edited`, so the route is recomputed against the new base."""
    ci = yaml.safe_load((DOCS.parents[1] / ".github" / "workflows" / "ci.yml").read_text())
    triggers = ci.get("on", ci.get(True))
    types = set(triggers["pull_request"]["types"])
    assert {"opened", "synchronize", "reopened", "edited"} <= types


def _ci() -> dict[str, Any]:
    loaded = yaml.safe_load((DOCS.parents[1] / ".github" / "workflows" / "ci.yml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_title_and_body_edits_do_not_rerun_the_judge():
    """S20: only an edit that changes the base re-runs routing; the rule is in run-name."""
    run_name = _ci()["run-name"]
    assert "github.event.action != 'edited'" in run_name
    assert "github.event.changes.base" in run_name
    route_if = _jobs()["route"]["if"]
    assert "display_title" in route_if and "judge=yes" in route_if


def test_post_asserts_the_upstream_job_results_as_a_second_guard():
    post = _jobs()["post"]
    env = post["env"]
    assert env["JUDGE_RESULT"] == "${{ needs.judge.result }}"
    assert env["EVALUATE_RESULT"] == "${{ needs.evaluate.result }}"
    assert env["POLICY_RESULT"] == "${{ needs.policy.result }}"
    runs = _runs("post")
    assert "--guards-ok" in runs and "success" in runs and "skipped" in runs


def test_post_binds_the_verdict_to_the_current_default_branch_tip():
    runs = _runs("post")
    assert '--current-base "$CURRENT_BASE"' in runs
    judge_runs = _runs("judge")
    assert "judged_base" in judge_runs
