"""The branch-class workflow template: separate gate and author jobs, no secrets (R8)."""

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "docs" / "ci" / "branch.yml"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _wf() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _text() -> str:
    return WORKFLOW.read_text()


def test_it_is_a_default_branch_workflow_run_with_no_permissions_by_default():
    wf = _wf()
    assert wf[True]["workflow_run"]["types"] == ["completed"]  # `on` parses as True
    assert wf["permissions"] == {}


def test_three_separate_jobs_cover_gate_author_and_unknown():
    jobs = _wf()["jobs"]
    assert set(jobs) == {"gate", "author", "unknown-class"}
    assert "gate/" in jobs["gate"]["if"] and "spec/" not in jobs["gate"]["if"]
    assert "spec/" in jobs["author"]["if"] and "gate/" not in jobs["author"]["if"]
    unknown = jobs["unknown-class"]["if"]
    assert "gate/" in unknown and "spec/" in unknown and "!" in unknown


def test_the_branch_name_never_reaches_a_shell_by_interpolation():
    wf = _wf()
    assert "head_branch" in wf["env"]["BRANCH"]  # only via a workflow-level env var
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            assert "head_branch" not in step.get("run", ""), step


def test_each_job_runs_the_python_check_not_inline_logic():
    for name in ("gate", "author"):
        steps = _wf()["jobs"][name]["steps"]
        runs = " ".join(s.get("run", "") for s in steps)
        assert "cheeseless ci-branch" in runs and '--branch "$BRANCH"' in runs


def test_no_secret_environment_or_app_token_is_used():
    text = _text()
    assert "secrets." not in text.replace("# ", "")
    assert "environment:" not in text
    assert "create-github-app-token" not in text


def test_the_author_job_posts_no_implementation_verdict():
    steps = _wf()["jobs"]["author"]["steps"]
    assert "ci-judge" not in " ".join(s.get("run", "") for s in steps)
    assert "ci-judge" not in _text().replace("# ", "").split("jobs:")[1]


def test_the_pr_head_is_fetched_as_objects_only_and_actions_are_pinned():
    text = _text()
    assert "refs/cheeseless/pr-head" in text
    for job in _wf()["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert PINNED.match(step["uses"]), step["uses"]
