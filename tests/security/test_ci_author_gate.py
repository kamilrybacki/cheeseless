"""The CI author check is fail-only and closed-enum (ADR 0018).

`evaluate` runs PR acceptance content, so everything it emits is forgeable. The gate therefore
believes one value only: an exact ``pass|fail|unavailable`` from a successful job. Anything else
is ``unavailable`` (a warning), a ``fail`` can only block the forger's own spec/ PR, and no value
can clear another guard or put PR-controlled text into a published check body. The canary and
sentinel strings stand for hidden acceptance content: they must never reach a check body.
"""

import json
from typing import Any

import pytest

from cheeseless.app.ci_branch import BranchOutcome, check_payload
from cheeseless.app.ci_gate import GATE_CHECK, build_checks, resolve_author
from cheeseless.bootstrap import main
from cheeseless.domain.authoring import AuthorStatus, author_output_line, parse_author_status
from tests.toy import CANARY, SENTINEL

SHA = "a" * 40
BASE = "b" * 40
JUDGE_PASS = {"status": "pass", "criteria": [], "judged_base": BASE}
FORGED = [
    "",
    " pass",
    "pass ",
    "PASS",
    "Pass",
    "FAIL",
    "Unavailable",
    "pass\nfail",
    "fail\npass",
    "pass\r\n",
    "pass\x00",
    "pass,fail",
    "p" * 10_000,
    "pass" + " " * 10_000,
    "::error::pwn",
    "::set-output name=author::pass",
    "::add-mask::pass",
    "author=pass",
    "${{ secrets.TOKEN }}",
    "$(echo pass)",
    "`echo pass`",
    "--help",
    "-pass",
    f"pass {CANARY}",
    SENTINEL,
]


def _gate(cls: str, author: AuthorStatus, *, policy_ok: bool = True, guards_ok: bool = True):
    policy = check_payload(
        BranchOutcome(policy_ok, "ok" if policy_ok else "outside_author_paths", cls), SHA
    )
    checks = build_checks(
        SHA,
        cls,
        policy,
        "0",
        JUDGE_PASS,
        current_base=BASE,
        guards_ok=guards_ok,
        author=author,
    )
    return {c["name"]: c for c in checks}


@pytest.mark.parametrize("raw", FORGED)
def test_a_forged_value_never_parses_as_a_verdict(raw: str) -> None:
    assert parse_author_status(raw) is AuthorStatus.UNAVAILABLE
    assert resolve_author(raw, "success") is AuthorStatus.UNAVAILABLE


@pytest.mark.parametrize("raw", [None, 1, True, b"pass", ["pass"], {"a": "pass"}, object()])
def test_a_non_string_value_never_parses_as_a_verdict(raw: object) -> None:
    assert parse_author_status(raw) is AuthorStatus.UNAVAILABLE


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "", "SUCCESS", None, 0])
@pytest.mark.parametrize("raw", ["pass", "fail"])
def test_a_value_from_an_evaluate_job_that_did_not_succeed_is_not_believed(
    raw: str, result: object
) -> None:
    assert resolve_author(raw, result) is AuthorStatus.UNAVAILABLE


def test_only_the_exact_wire_values_are_believed() -> None:
    for status in AuthorStatus:
        assert resolve_author(status.value, "success") is status
    assert {parse_author_status(s.value) for s in AuthorStatus} == set(AuthorStatus)


def test_the_job_output_line_is_built_from_the_enum_alone() -> None:
    lines = {s: author_output_line(s) for s in AuthorStatus}
    assert lines == {
        AuthorStatus.PASS: "author=pass\n",
        AuthorStatus.FAIL: "author=fail\n",
        AuthorStatus.UNAVAILABLE: "author=unavailable\n",
    }


@pytest.mark.parametrize("raw", FORGED)
def test_no_forged_value_clears_or_blocks_a_spec_gate_on_its_own(raw: str) -> None:
    """A forged value reads as unavailable: a warning, exactly as if the check never ran."""
    author = resolve_author(raw, "success")
    gate = _gate("spec", author)[GATE_CHECK]
    assert gate["conclusion"] == "success"
    assert "Warning" in gate["output"]["summary"]


@pytest.mark.parametrize("author", list(AuthorStatus))
@pytest.mark.parametrize(("policy_ok", "guards_ok"), [(False, True), (True, False), (False, False)])
def test_no_author_status_clears_another_guard(
    author: AuthorStatus, policy_ok: bool, guards_ok: bool
) -> None:
    gate = _gate("spec", author, policy_ok=policy_ok, guards_ok=guards_ok)[GATE_CHECK]
    assert gate["conclusion"] == "failure"


@pytest.mark.parametrize("author", list(AuthorStatus))
def test_a_fail_adds_a_guard_only_on_a_spec_pr(author: AuthorStatus) -> None:
    for cls in ("gate", "other"):
        baseline = _gate(cls, AuthorStatus.PASS)
        assert _gate(cls, author) == baseline
    assert _gate("spec", AuthorStatus.FAIL)[GATE_CHECK]["conclusion"] == "failure"


@pytest.mark.parametrize("author", list(AuthorStatus))
def test_an_author_fail_never_changes_the_policy_or_judge_checks(author: AuthorStatus) -> None:
    base, other = _gate("spec", AuthorStatus.PASS), _gate("spec", author)
    for name in base:
        if name != GATE_CHECK:
            assert other[name] == base[name]


def _run_gate(tmp_path, capsys, author: str, evaluate_result: str = "success") -> dict[str, Any]:
    (tmp_path / "policy.json").write_text(
        json.dumps(check_payload(BranchOutcome(True, "ok", "spec"), SHA))
    )
    code = main(
        [
            "ci-gate",
            "--head",
            SHA,
            "--branch-class",
            "spec",
            "--policy",
            str(tmp_path / "policy.json"),
            "--guards-ok",
            "true",
            f"--author={author}",
            "--evaluate-result",
            evaluate_result,
        ]
    )
    assert code == 0
    return {c["name"]: c for c in json.loads(capsys.readouterr().out)}


@pytest.mark.parametrize("raw", FORGED)
def test_the_cli_does_not_clear_or_block_on_a_forged_value(tmp_path, capsys, raw: str) -> None:
    checks = _run_gate(tmp_path, capsys, raw)
    assert checks[GATE_CHECK]["conclusion"] == "success"
    assert "Warning" in checks[GATE_CHECK]["output"]["summary"]


def test_the_cli_blocks_only_on_an_exact_fail_from_a_successful_evaluate(tmp_path, capsys) -> None:
    assert _run_gate(tmp_path, capsys, "fail")[GATE_CHECK]["conclusion"] == "failure"
    assert _run_gate(tmp_path, capsys, "fail", "failure")[GATE_CHECK]["conclusion"] == "success"


@pytest.mark.parametrize("raw", [CANARY, SENTINEL, f"fail {CANARY} {SENTINEL}", "fail\n" + CANARY])
def test_canary_text_in_the_value_never_reaches_a_check_body(tmp_path, capsys, raw: str) -> None:
    """The check body is fixed text: nothing PR-controlled is rendered, so a canary cannot leak."""
    checks = _run_gate(tmp_path, capsys, raw)
    text = json.dumps(checks)
    assert CANARY not in text and SENTINEL not in text


def test_the_policy_payload_carries_no_author_text_or_canary() -> None:
    out = BranchOutcome(
        True, "ok", "spec", (), (f"{CANARY} {SENTINEL}", "::error::pwn"), AuthorStatus.FAIL
    )
    text = json.dumps(check_payload(out, SHA))
    assert CANARY not in text and SENTINEL not in text and "pwn" not in text
