"""The judge workflow template: isolated secrets, budget token and App key handling.

The conclusion logic lives in ``ci-gate`` (tests/unit/app/test_ci_gate.py); the post job's
own layout is covered by tests/unit/test_ci_workflow.py."""

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "docs" / "ci" / "judge.yml"
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


def test_template_has_no_neutral_conclusion() -> None:
    # Only prose may mention it: a neutral/skipped conclusion satisfies a required check.
    assert not re.search(r"conclusion\W+(neutral|skipped)", WORKFLOW.read_text())


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


def _mints() -> list[dict[str, Any]]:
    return [s for s in _judge_steps() if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)]


def _mint_index(permission: str) -> int:
    return _step_index(
        lambda s: (
            str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)
            and permission in (s.get("with") or {})
        )
    )


def _token_ref(step_id: str) -> str:
    return "${{ steps." + step_id + ".outputs.token }}"


def test_checks_token_is_minted_only_in_the_post_job_after_the_gate_is_built() -> None:
    steps = _workflow()["jobs"]["post"]["steps"]
    mint = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)
        and "permission-checks" in (s.get("with") or {})
    )
    gate = next(i for i, s in enumerate(steps) if "ci-gate" in str(s.get("run", "")))
    post = next(i for i, s in enumerate(steps) if "check-runs" in str(s.get("run", "")))
    app = steps[mint]
    assert app["with"]["permission-checks"] == "write"
    assert {k for k in app["with"] if k.startswith("permission-")} == {"permission-checks"}
    assert gate < mint < post
    assert steps[post]["env"]["GH_TOKEN"] == "${{ steps." + app["id"] + ".outputs.token }}"


def test_the_judge_job_mints_exactly_one_token_the_budget_one() -> None:
    assert len(_mints()) == 1


def test_budget_token_is_contents_write_on_the_ledger_repository_only() -> None:
    steps = _judge_steps()
    mint = _mint_index("permission-contents")
    budget = steps[mint]["with"]
    assert budget["permission-contents"] == "write"
    assert {k for k in budget if k.startswith("permission-")} == {"permission-contents"}
    # Scoped to the dedicated ledger repository, never the judged one.
    assert budget["repositories"] == "${{ vars.CHEESELESS_BUDGET_REPO }}"
    assert "," not in budget["repositories"]
    assert mint < _step_index(lambda s: s.get("id") == "judge")


def test_judge_step_sees_only_the_budget_token() -> None:
    steps = _judge_steps()
    budget_id = steps[_mint_index("permission-contents")]["id"]
    judge = steps[_step_index(lambda s: s.get("id") == "judge")]
    assert judge["env"]["CHEESELESS_BUDGET_TOKEN"] == _token_ref(budget_id)
    dumped = yaml.safe_dump(judge)
    assert "APP_KEY" not in dumped
    assert "private-key" not in dumped
    assert "permission-checks" not in yaml.safe_dump(steps)
    assert dumped.count("outputs.token") == 1


def test_budget_token_reaches_no_other_candidate_facing_place() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["judge"]
    budget_id = _judge_steps()[_mint_index("permission-contents")]["id"]
    ref = f"steps.{budget_id}.outputs.token"
    assert ref not in yaml.safe_dump(job.get("env") or {})
    users = [s.get("id") or s.get("name") for s in _judge_steps() if ref in yaml.safe_dump(s)]
    assert users == ["judge", "revoke-budget"]


def test_budget_token_is_revoked_right_after_the_judge_whatever_happened() -> None:
    steps = _judge_steps()
    judge = _step_index(lambda s: s.get("id") == "judge")
    revoke = _step_index(lambda s: s.get("id") == "revoke-budget")
    assert revoke == judge + 1
    step = steps[revoke]
    budget_id = steps[_mint_index("permission-contents")]["id"]
    assert step["if"] == "always()"
    assert step["env"]["GH_TOKEN"] == _token_ref(budget_id)
    assert "installation/token" in step["run"]
    assert "DELETE" in step["run"]


def test_tokens_are_also_revoked_when_their_job_ends() -> None:
    # The action's post step revokes each token; nothing may opt out of it.
    every = [
        s
        for job in _workflow()["jobs"].values()
        for s in job["steps"]
        if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)
    ]
    assert len(every) == 2
    for mint in every:
        assert "skip-token-revoke" not in (mint.get("with") or {})
