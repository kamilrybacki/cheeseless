"""Turning an internal run report into what the agent may see (Q51, Q52, D2, D17)."""

import pytest

from cheeseless.domain.disclosure import RunReport, Verdict, decide
from cheeseless.domain.errors import ErrorCode, GateError
from cheeseless.domain.result import Result
from cheeseless.domain.status import Status
from cheeseless.domain.task import Criterion, Task

TASK = Task(
    task_id="a" * 64,
    lineage_id="toy",
    criteria=(Criterion("normalize", "CLI normalises"), Criterion("score", "score() ranks")),
)
BINDING = {
    "tree_hash": "b" * 40,
    "baseline_digest": "c" * 64,
    "profile": "local",
    "tool_version": "0.1.0",
}


def _decide(report: RunReport) -> Verdict:
    return decide(TASK, report, **BINDING)


def test_pre_hidden_failure_is_free_and_detailed() -> None:
    verdict = _decide(RunReport(pre_hidden_failure=ErrorCode.BUILD_FAILED, stderr_tail="E: x"))
    assert isinstance(verdict.outcome, GateError)
    assert verdict.outcome.code is ErrorCode.BUILD_FAILED
    assert verdict.outcome.pre_hidden_stderr_tail == "E: x"
    assert not verdict.charged


def test_pre_hidden_infra_error_is_visible_and_free() -> None:
    verdict = _decide(RunReport(infra_cause="sandbox setup failed"))
    assert isinstance(verdict.outcome, GateError)
    assert verdict.outcome.code is ErrorCode.INFRA_ERROR
    assert verdict.outcome.pre_hidden_stderr_tail == ""
    assert verdict.operator_cause == "sandbox setup failed"
    assert not verdict.charged


def test_all_criteria_pass() -> None:
    verdict = _decide(
        RunReport(hidden_input_read=True, criteria={"normalize": Status.PASS, "score": Status.PASS})
    )
    assert isinstance(verdict.outcome, Result)
    assert verdict.outcome.status is Status.PASS
    assert verdict.charged


def test_post_hidden_infra_error_is_charged_and_shown_as_fail() -> None:
    verdict = _decide(
        RunReport(hidden_input_read=True, criteria={"normalize": Status.PASS}, infra_cause="oom")
    )
    assert isinstance(verdict.outcome, Result)
    assert verdict.outcome.status is Status.FAIL
    assert [c.status for c in verdict.outcome.criteria] == [Status.FAIL, Status.FAIL]
    assert verdict.charged
    assert verdict.operator_cause.startswith("oom")


def test_missing_criterion_is_fail_never_pass() -> None:
    verdict = _decide(RunReport(hidden_input_read=True, criteria={"normalize": Status.PASS}))
    assert isinstance(verdict.outcome, Result)
    assert verdict.outcome.status is Status.FAIL
    assert verdict.operator_cause


def test_unknown_criterion_in_report_fails_closed() -> None:
    verdict = _decide(
        RunReport(
            hidden_input_read=True,
            criteria={"normalize": Status.PASS, "score": Status.PASS, "bogus": Status.PASS},
        )
    )
    assert isinstance(verdict.outcome, Result)
    assert verdict.outcome.status is Status.FAIL


def test_post_hidden_stderr_is_dropped() -> None:
    verdict = _decide(
        RunReport(hidden_input_read=True, criteria={"normalize": Status.FAIL}, stderr_tail="CANARY")
    )
    assert "CANARY" not in repr(verdict.outcome)


def test_failures_listed_with_public_descriptions() -> None:
    verdict = _decide(
        RunReport(hidden_input_read=True, criteria={"normalize": Status.PASS, "score": Status.FAIL})
    )
    [failure] = verdict.criterion_failures(TASK)
    assert failure.code is ErrorCode.CRITERION_FAILED
    assert failure.details.criterion_id == "score"
    assert "score() ranks" in failure.reason


def test_pre_hidden_report_cannot_name_a_post_hidden_code() -> None:
    with pytest.raises(ValueError):
        RunReport(pre_hidden_failure=ErrorCode.CRITERION_FAILED)


def test_report_after_hidden_input_cannot_carry_a_pre_hidden_failure() -> None:
    with pytest.raises(ValueError):
        RunReport(hidden_input_read=True, pre_hidden_failure=ErrorCode.BUILD_FAILED)


def test_empty_pre_hidden_report_is_infra_error() -> None:
    verdict = _decide(RunReport())
    assert isinstance(verdict.outcome, GateError)
    assert verdict.outcome.code is ErrorCode.INFRA_ERROR


def test_post_hidden_infra_failure_never_passes() -> None:
    """Codex review: an infra cause after hidden input fails closed (Q43, D17)."""
    report = RunReport(
        hidden_input_read=True,
        criteria={"normalize": Status.PASS, "score": Status.PASS},
        infra_cause="runner crashed after the last test",
    )
    verdict = decide(TASK, report, **BINDING)
    assert isinstance(verdict.outcome, Result)
    assert verdict.outcome.status is Status.FAIL
    assert all(c.status is Status.FAIL for c in verdict.outcome.criteria)
    assert verdict.charged
