"""The one required check, "cheeseless gate" (H1).

GitHub scopes required status checks by the BASE branch, so a single required name has to
cover every PR class. This module folds the validated branch-policy payload and the judge
result into class-aware check bodies. Conclusions are only ever ``success`` or ``failure``:
GitHub counts ``neutral`` and ``skipped`` as satisfying a required check, so they are never
used to mean "not applicable".
"""

from __future__ import annotations

import json
from typing import Any

from cheeseless.app.ci_branch import BRANCH_POLICY_CHECK

GATE_CHECK = "cheeseless gate"
JUDGE_CHECK = "cheeseless judge"

_KEYS = {"name", "head_sha", "status", "conclusion", "output"}


def _body(name: str, head_sha: str, ok: bool, title: str, summary: str) -> dict[str, Any]:
    conclusion = "success" if ok else "failure"
    return {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": f"{title}: {conclusion}", "summary": summary},
    }


def _policy_ok(policy: Any, head_sha: str, branch_class: str) -> bool:
    """A policy payload counts only if it is exactly our own, for this head and class."""
    if not isinstance(policy, dict) or set(policy) != _KEYS:
        return False
    out = policy["output"]
    if not (
        isinstance(out, dict)
        and set(out) == {"title", "summary"}
        and isinstance(out["title"], str)
        and isinstance(out["summary"], str)
    ):
        return False
    return (
        policy["name"] == BRANCH_POLICY_CHECK
        and policy["head_sha"] == head_sha
        and policy["status"] == "completed"
        and policy["conclusion"] == "success"
        and out["summary"].startswith(f"class: {branch_class};")
    )


def _judge_ok(code: str, verdict: Any) -> bool:
    return code == "0" and isinstance(verdict, dict) and verdict.get("status") == "pass"


def _judge_summary(verdict: Any) -> str:
    if not isinstance(verdict, dict):
        return "{}"
    return json.dumps({k: verdict.get(k) for k in ("status", "criteria")}, sort_keys=True)


def build_checks(
    head_sha: str,
    branch_class: str,
    policy: Any,
    judge_code: str,
    verdict: Any,
) -> list[dict[str, Any]]:
    """The check bodies to publish: gate (required), policy and, for gate/, judge."""
    policy_ok = _policy_ok(policy, head_sha, branch_class)
    policy_check = _body(
        BRANCH_POLICY_CHECK,
        head_sha,
        policy_ok,
        BRANCH_POLICY_CHECK,
        policy["output"]["summary"]
        if policy_ok
        else "the branch policy did not pass or its payload was invalid",
    )
    checks = [policy_check]
    if branch_class == "gate":
        judge_ok = _judge_ok(judge_code, verdict)
        checks.append(_body(JUDGE_CHECK, head_sha, judge_ok, JUDGE_CHECK, _judge_summary(verdict)))
        ok = policy_ok and judge_ok
        summary = f"branch policy: {'pass' if policy_ok else 'fail'}; judge: "
        summary += "pass" if judge_ok else "not a pass"
    elif branch_class == "spec":
        ok = policy_ok
        summary = (
            "Spec PR: path confinement only. This is NOT an implementation verdict; "
            f"branch policy: {'pass' if policy_ok else 'fail'}."
        )
    else:
        ok = False
        summary = "unsupported branch class; only gate/ and spec/ pull requests can pass"
    title = GATE_CHECK + (
        " (spec: NOT an implementation verdict)" if branch_class == "spec" else ""
    )
    return [_body(GATE_CHECK, head_sha, ok, title, summary), *checks]
