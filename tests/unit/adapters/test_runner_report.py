"""Runner report → SuiteRun: read the report before trusting the exit status."""

import json

import pytest

from cheeseless.adapters.outbound.runner.report import REPORT_VERSION, suite_from_report
from cheeseless.domain.evaluation import CollectedTest, TestOutcome


def _report(**overrides: object) -> bytes:
    data: dict[str, object] = {
        "version": REPORT_VERSION,
        "exitstatus": 1,
        "collected": [
            {"node_id": "a.py::t1", "criteria": ["c1"]},
            {"node_id": "a.py::t2", "criteria": []},
        ],
        "outcomes": {"a.py::t1": "passed", "a.py::t2": "failed"},
        "collection_errors": [],
    }
    data.update(overrides)
    return json.dumps(data).encode()


def test_a_normal_report_maps_to_a_suite_run() -> None:
    run = suite_from_report(_report(), 1)
    assert run.infra_cause == ""
    assert run.collected == (
        CollectedTest("a.py::t1", ("c1",)),
        CollectedTest("a.py::t2", ()),
    )
    assert run.outcome_map == {"a.py::t1": TestOutcome.PASSED, "a.py::t2": TestOutcome.FAILED}


@pytest.mark.parametrize("code", [0, 1, 5])
def test_ordinary_exit_codes_are_not_infra(code: int) -> None:
    assert suite_from_report(_report(exitstatus=code), code).infra_cause == ""


def test_collection_errors_explain_exit_two() -> None:
    run = suite_from_report(_report(exitstatus=2, collection_errors=["b.py"]), 2)
    assert run.infra_cause == ""
    assert run.collection_errors == ("b.py",)


def test_unexplained_interrupt_is_infra() -> None:
    assert "interrupted" in suite_from_report(_report(exitstatus=2), 2).infra_cause


@pytest.mark.parametrize("code", [3, 4, 7, -9])
def test_other_exit_codes_are_infra(code: int) -> None:
    run = suite_from_report(_report(exitstatus=code), code)
    assert str(code) in run.infra_cause


def test_missing_report_is_infra() -> None:
    run = suite_from_report(None, 1)
    assert "no report" in run.infra_cause
    assert run.outcomes is None


def test_timeout_wins_over_any_report() -> None:
    run = suite_from_report(_report(), 1, timed_out=True)
    assert "timed out" in run.infra_cause


def test_exit_status_mismatch_is_infra() -> None:
    assert "mismatch" in suite_from_report(_report(exitstatus=0), 1).infra_cause


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        _report(version=2),
        _report(outcomes={"a.py::t1": "winning"}),
        _report(outcomes={"a.py::t1": 1}),
        _report(outcomes=[]),
        _report(collected=[{"node_id": 1, "criteria": []}]),
        _report(collected=[{"node_id": "x", "criteria": "c1"}]),
        _report(collected={}),
        _report(collection_errors=[1]),
        _report(exitstatus="1"),
        _report(exitstatus=True),
    ],
)
def test_malformed_reports_are_infra(raw: bytes) -> None:
    run = suite_from_report(raw, 1)
    assert "malformed" in run.infra_cause
    assert run.outcomes is None
