"""The one required check: "cheeseless gate" (H1). Class-aware, never neutral or skipped."""

from __future__ import annotations

from typing import Any

import pytest

from cheeseless.app.ci_branch import BranchOutcome, check_payload
from cheeseless.app.ci_gate import GATE_CHECK, JUDGE_CHECK
from cheeseless.app.ci_gate import build_checks as _build

SHA = "a" * 40
BASE = "b" * 40


def build_checks(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    kwargs.setdefault("current_base", BASE)
    return _build(*args, **kwargs)


def _policy(cls: str, ok: bool = True, head: str = SHA) -> dict[str, Any]:
    return check_payload(BranchOutcome(ok, "ok" if ok else "outside_author_paths", cls), head)


PASS = {"status": "pass", "criteria": [], "judged_base": BASE}


def _by_name(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in checks}


def test_gate_class_succeeds_only_when_judge_and_policy_both_pass() -> None:
    checks = _by_name(build_checks(SHA, "gate", _policy("gate"), "0", PASS))
    assert checks[GATE_CHECK]["conclusion"] == "success"
    assert checks[JUDGE_CHECK]["conclusion"] == "success"
    assert checks["cheeseless branch policy"]["conclusion"] == "success"


@pytest.mark.parametrize(
    ("policy_ok", "code", "verdict"),
    [
        (False, "0", PASS),
        (True, "1", PASS),
        (True, "0", {"status": "fail"}),
        (True, "0", None),
        (True, "", PASS),
        (True, "0", {"status": "PASS"}),
    ],
)
def test_gate_class_fails_when_either_half_fails(policy_ok, code, verdict) -> None:
    checks = _by_name(build_checks(SHA, "gate", _policy("gate", policy_ok), code, verdict))
    assert checks[GATE_CHECK]["conclusion"] == "failure"


def test_spec_class_succeeds_on_policy_and_says_it_is_not_an_implementation_verdict() -> None:
    gate = _by_name(build_checks(SHA, "spec", _policy("spec"), "", None))[GATE_CHECK]
    assert gate["conclusion"] == "success"
    text = gate["output"]["title"] + gate["output"]["summary"]
    assert "NOT an implementation verdict" in text


def test_spec_class_fails_when_the_policy_fails_and_has_no_judge_check() -> None:
    checks = _by_name(build_checks(SHA, "spec", _policy("spec", False), "", None))
    assert checks[GATE_CHECK]["conclusion"] == "failure"
    assert JUDGE_CHECK not in checks


@pytest.mark.parametrize("cls", ["unknown", "", "other", "GATE"])
def test_any_other_class_fails_even_with_a_forged_passing_policy(cls: str) -> None:
    checks = _by_name(build_checks(SHA, cls, _policy("gate"), "0", PASS))
    assert checks[GATE_CHECK]["conclusion"] == "failure"


@pytest.mark.parametrize(
    "policy",
    [
        None,
        "x",
        {},
        {**_policy("spec"), "head_sha": "c" * 40},
        {**_policy("spec"), "name": "cheeseless gate"},
        {**_policy("spec"), "conclusion": "neutral"},
        {**_policy("spec"), "status": "queued"},
        {**_policy("spec"), "extra": 1},
        {**_policy("spec"), "output": {"title": 1, "summary": "x"}},
        _policy("gate"),  # a payload for another class
    ],
)
def test_an_invalid_or_foreign_policy_payload_is_a_failure(policy: Any) -> None:
    checks = _by_name(build_checks(SHA, "spec", policy, "", None))
    assert checks[GATE_CHECK]["conclusion"] == "failure"
    assert checks["cheeseless branch policy"]["conclusion"] == "failure"


@pytest.mark.parametrize("cls", ["gate", "spec", "unknown", "bogus"])
@pytest.mark.parametrize("policy_ok", [True, False])
def test_no_check_is_ever_neutral_skipped_or_pending(cls: str, policy_ok: bool) -> None:
    for check in build_checks(SHA, cls, _policy(cls, policy_ok), "0", PASS):
        assert check["status"] == "completed"
        assert check["conclusion"] in {"success", "failure"}
        assert check["head_sha"] == SHA


def test_a_gate_pass_judged_against_an_older_base_is_stale_and_fails() -> None:
    """S19: re-running only post must not keep a pass judged against a moved default branch."""
    stale = build_checks(SHA, "gate", _policy("gate"), "0", PASS, current_base="c" * 40)
    assert _by_name(stale)[GATE_CHECK]["conclusion"] == "failure"
    assert "base" in _by_name(stale)[GATE_CHECK]["output"]["summary"]


@pytest.mark.parametrize("verdict", [{"status": "pass", "criteria": []}, {"judged_base": ""}])
def test_a_gate_pass_without_a_judged_base_fails(verdict: dict[str, Any]) -> None:
    checks = _by_name(build_checks(SHA, "gate", _policy("gate"), "0", verdict))
    assert checks[GATE_CHECK]["conclusion"] == "failure"


def test_an_unknown_current_base_fails_the_gate_class_closed() -> None:
    checks = _by_name(build_checks(SHA, "gate", _policy("gate"), "0", PASS, current_base=""))
    assert checks[GATE_CHECK]["conclusion"] == "failure"


@pytest.mark.parametrize("cls", ["gate", "spec"])
def test_a_failed_upstream_guard_fails_the_gate_whatever_the_payloads_say(cls: str) -> None:
    checks = _by_name(build_checks(SHA, cls, _policy(cls), "0", PASS, guards_ok=False))
    assert checks[GATE_CHECK]["conclusion"] == "failure"
