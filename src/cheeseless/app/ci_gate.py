"""The one required check, "cheeseless gate" (H1).

GitHub scopes required status checks by the BASE branch, so a single required name has to
cover every PR class. This module folds the validated branch-policy payload and the judge
result into class-aware check bodies. Conclusions are only ever ``success`` or ``failure``:
GitHub counts ``neutral`` and ``skipped`` as satisfying a required check, so they are never
used to mean "not applicable".

The author check (ADR 0018) adds one guard, for spec/ only: a ``fail`` makes the gate fail. The
value comes from a job a pull request's own test code can influence, so it is only ever believed
in its exact closed form and can only *add* a failure: it never clears another guard, and
``unavailable`` is a fixed-text warning, not a block.
"""

from __future__ import annotations

import json
from typing import Any

from cheeseless.app.ci_branch import BRANCH_POLICY_CHECK
from cheeseless.domain.authoring import AuthorStatus, parse_author_status

GATE_CHECK = "cheeseless gate"
JUDGE_CHECK = "cheeseless judge"

_KEYS = {"name", "head_sha", "status", "conclusion", "output"}

# Fixed texts: nothing a pull request wrote is ever rendered into the gate.
_AUTHOR_FAIL_TEXT = " Author check: fail, see the evaluate job log."
_AUTHOR_UNAVAILABLE_TEXT = (
    " Warning: the author check could not run (no verdict from it); see the evaluate job log."
)
_AUTHOR_PASS_TEXT = " Author check: pass."  # noqa: S105 - status text, not a credential
_AUTHOR_TEXT = {
    AuthorStatus.FAIL: _AUTHOR_FAIL_TEXT,
    AuthorStatus.UNAVAILABLE: _AUTHOR_UNAVAILABLE_TEXT,
    AuthorStatus.PASS: _AUTHOR_PASS_TEXT,
}


def resolve_author(raw: object, evaluate_result: object) -> AuthorStatus:
    """The author status to act on: ``unavailable`` unless the evaluate job succeeded and its
    output is exactly one of the three wire values. Everything else (a skipped, cancelled or
    failed job, an empty, multi-line, oversized or oddly cased value) means "could not tell"."""
    if evaluate_result != "success":
        return AuthorStatus.UNAVAILABLE
    return parse_author_status(raw)


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


def _base_fresh(verdict: Any, current_base: str) -> bool:
    """The pass was judged against the default-branch tip that is current now (S19)."""
    if not isinstance(verdict, dict) or not current_base:
        return False
    judged = verdict.get("judged_base")
    return isinstance(judged, str) and judged == current_base


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
    *,
    current_base: str = "",
    guards_ok: bool,
    author: AuthorStatus = AuthorStatus.UNAVAILABLE,
) -> list[dict[str, Any]]:
    """The check bodies to publish: gate (required), policy and, for gate/, judge.

    ``current_base`` is the default-branch tip now; a gate/ pass counts only if the verdict was
    judged against it. ``guards_ok`` is False when an upstream job did not end as expected.
    ``author`` only matters for spec/: ``fail`` adds a failing guard on the gate check, and
    can never turn a failing gate into a passing one."""
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
        fresh = _base_fresh(verdict, current_base)
        ok = policy_ok and judge_ok and fresh
        summary = f"branch policy: {'pass' if policy_ok else 'fail'}; judge: "
        summary += "pass" if judge_ok else "not a pass"
        summary += f"; base: {'current' if fresh else 'stale or unknown, re-run the judge'}"
    elif branch_class == "spec":
        ok = policy_ok and author is not AuthorStatus.FAIL
        summary = (
            "Spec PR: path confinement only. This is NOT an implementation verdict; "
            f"branch policy: {'pass' if policy_ok else 'fail'}." + _AUTHOR_TEXT[author]
        )
    else:
        ok = False
        summary = "unsupported branch class; only gate/ and spec/ pull requests can pass"
    if not guards_ok:
        ok = False
        summary += " An upstream job did not end as expected."
    title = GATE_CHECK + (
        " (spec: NOT an implementation verdict)" if branch_class == "spec" else ""
    )
    return [_body(GATE_CHECK, head_sha, ok, title, summary), *checks]
