"""The judge workflow template: fail-closed conclusions, isolated secrets, App-posted verdict."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.requirements import needs

WORKFLOW = Path(__file__).resolve().parents[2] / "docs" / "ci" / "judge.yml"
JQ = shutil.which("jq") is not None
ENVIRONMENT = "cheeseless-judge"
APP_TOKEN_ACTION = "actions/create-github-app-token@"  # noqa: S105 - action name, not a secret
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _judge_steps() -> list[dict[str, Any]]:
    steps = _workflow()["jobs"]["judge"]["steps"]
    assert isinstance(steps, list)
    return steps


def _step_index(predicate: Any) -> int:
    return next(i for i, step in enumerate(_judge_steps()) if predicate(step))


def _environment_name(job: dict[str, Any]) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        return env.get("name")
    return env


def _conclusion_snippet() -> str:
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("status=$(jq"))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    body = "\n".join(ln.strip() for ln in lines[start : end + 1])
    return body.replace("${{ steps.judge.outputs.code }}", "$CODE") + '\necho "$conclusion"\n'


def _conclude(tmp_path: Path, code: str, verdict: str | None) -> str:
    if verdict is not None:
        (tmp_path / "verdict.json").write_text(verdict)
    out = subprocess.run(  # noqa: S603 - fixed argv, test-owned script
        ["bash", "-c", _conclusion_snippet()],  # noqa: S607
        cwd=tmp_path,
        env={"CODE": code, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_template_has_no_neutral_conclusion() -> None:
    assert "neutral" not in WORKFLOW.read_text()


@needs(JQ, "needs jq")
@pytest.mark.parametrize(
    ("code", "verdict", "expected"),
    [
        ("0", '{"status": "pass", "criteria": []}', "success"),
        ("0", '{"status": "fail", "criteria": []}', "failure"),
        ("0", "not json", "failure"),
        ("0", None, "failure"),
        ("1", '{"status": "pass", "criteria": []}', "failure"),
        ("1", '{"status": "blocked", "criteria": []}', "failure"),
        ("2", None, "failure"),
        ("3", None, "failure"),
        ("", None, "failure"),
    ],
)
def test_only_exit_zero_with_pass_status_succeeds(
    tmp_path: Path, code: str, verdict: str | None, expected: str
) -> None:
    assert _conclude(tmp_path, code, verdict) == expected


def test_judge_job_runs_in_the_restricted_environment() -> None:
    # Repository secrets are readable from any branch workflow a pusher writes; an
    # environment limited to the default branch is not (ci-budget-counter review #2).
    assert _environment_name(_workflow()["jobs"]["judge"]) == ENVIRONMENT


def test_secrets_appear_only_in_environment_jobs() -> None:
    for name, job in _workflow()["jobs"].items():
        if "secrets." in yaml.safe_dump(job):
            assert _environment_name(job) == ENVIRONMENT, name


def test_github_token_cannot_write_checks() -> None:
    # The shared github-actions app is forgeable by pushers; only the judge App posts.
    workflow = _workflow()
    assert workflow["permissions"] == {}
    for job in workflow["jobs"].values():
        assert "write" not in (job.get("permissions") or {}).values()


def test_every_action_is_pinned_by_commit_sha() -> None:
    for step in _judge_steps():
        if "uses" in step:
            assert PINNED.match(step["uses"]), step["uses"]


def test_verdict_is_posted_with_the_judge_app_token_after_the_judge() -> None:
    steps = _judge_steps()
    mint = _step_index(lambda s: str(s.get("uses", "")).startswith(APP_TOKEN_ACTION))
    judge = _step_index(lambda s: s.get("id") == "judge")
    post = _step_index(lambda s: "check-runs" in str(s.get("run", "")))
    app = steps[mint]
    assert app["with"]["permission-checks"] == "write"
    assert set(k for k in app["with"] if k.startswith("permission-")) == {"permission-checks"}
    assert judge < mint < post
    expected = "${{ steps." + app["id"] + ".outputs.token }}"
    assert steps[post]["env"]["GH_TOKEN"] == expected


def test_judge_step_never_sees_the_app_credentials() -> None:
    judge = _judge_steps()[_step_index(lambda s: s.get("id") == "judge")]
    dumped = yaml.safe_dump(judge)
    assert "APP_KEY" not in dumped
    assert "outputs.token" not in dumped
