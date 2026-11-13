"""Per-test outcomes to criterion statuses, and freeze preconditions (Q44, Q43)."""

from cheeseless.domain.evaluation import (
    CollectedTest,
    SuiteRun,
    TestOutcome,
    criteria_statuses,
    criterion_by_test,
    freeze_problems,
    smoke_passed,
)
from cheeseless.domain.status import Status

A1, A2, B1, B2 = "t.py::a1", "t.py::a2", "t.py::b1", "t.py::b2"
SELECTED = {A1: "alpha", B1: "beta"}
P, F = TestOutcome.PASSED, TestOutcome.FAILED


def test_all_selected_pass() -> None:
    statuses, notes = criteria_statuses(SELECTED, SuiteRun(outcomes={A1: P, B1: P}))
    assert statuses == {"alpha": Status.PASS, "beta": Status.PASS}
    assert notes == ""


def test_one_failure_fails_only_its_criterion() -> None:
    statuses, _ = criteria_statuses(SELECTED, SuiteRun(outcomes={A1: P, B1: F}))
    assert statuses == {"alpha": Status.PASS, "beta": Status.FAIL}


def test_fail_is_sticky_within_a_criterion() -> None:
    selected = {A1: "alpha", A2: "alpha"}
    statuses, _ = criteria_statuses(selected, SuiteRun(outcomes={A1: F, A2: P}))
    assert statuses == {"alpha": Status.FAIL}


def test_missing_outcome_fails_closed() -> None:
    statuses, notes = criteria_statuses(SELECTED, SuiteRun(outcomes={A1: P}))
    assert statuses["beta"] is Status.FAIL
    assert B1 in notes


def test_skip_xfail_xpass_error_count_as_failures() -> None:
    for outcome in (TestOutcome.SKIPPED, TestOutcome.XFAILED, TestOutcome.XPASSED):
        statuses, _ = criteria_statuses({A1: "alpha"}, SuiteRun(outcomes={A1: outcome}))
        assert statuses == {"alpha": Status.FAIL}
    statuses, _ = criteria_statuses({A1: "alpha"}, SuiteRun(outcomes={A1: TestOutcome.ERROR}))
    assert statuses == {"alpha": Status.FAIL}


def test_collection_errors_and_unexpected_tests_fail_everything() -> None:
    run = SuiteRun(outcomes={A1: P, B1: P}, collection_errors=("t.py",))
    statuses, notes = criteria_statuses(SELECTED, run)
    assert set(statuses.values()) == {Status.FAIL}
    assert "collection" in notes
    run = SuiteRun(outcomes={A1: P, B1: P, A2: P})
    statuses, notes = criteria_statuses(SELECTED, run)
    assert set(statuses.values()) == {Status.FAIL}
    assert "unselected" in notes


def test_infra_cause_is_noted() -> None:
    _, notes = criteria_statuses(SELECTED, SuiteRun(infra_cause="timeout"))
    assert "timeout" in notes


def test_smoke_passed() -> None:
    assert smoke_passed(SuiteRun(outcomes={A1: P}))
    assert not smoke_passed(SuiteRun(outcomes={}))
    assert not smoke_passed(SuiteRun(outcomes={A1: F}))
    assert not smoke_passed(SuiteRun(outcomes={A1: P}, collection_errors=("x",)))
    assert not smoke_passed(SuiteRun(outcomes={A1: P}, infra_cause="x"))


def _collected(*pairs: tuple[str, tuple[str, ...]]) -> tuple[CollectedTest, ...]:
    return tuple(CollectedTest(n, c) for n, c in pairs)


GOOD = SuiteRun(
    collected=_collected((A1, ("alpha",)), (A2, ("alpha",)), (B1, ("beta",)), (B2, ("beta",))),
    outcomes={A1: F, A2: P, B1: F, B2: F},
)


def test_good_suite_has_no_freeze_problems() -> None:
    assert freeze_problems(["alpha", "beta"], GOOD) == []
    assert criterion_by_test(GOOD) == {A1: "alpha", A2: "alpha", B1: "beta", B2: "beta"}


def test_freeze_problems_are_reported() -> None:
    run = SuiteRun(
        collected=_collected(
            (A1, ("alpha",)), (A2, ("alpha",)), (B1, ()), (B2, ("gamma",)), ("t.py::c", ("a", "b"))
        ),
        outcomes={A1: P, A2: P, B1: TestOutcome.SKIPPED, B2: TestOutcome.XFAILED},
        collection_errors=("broken.py",),
        infra_cause="crash",
    )
    problems = "\n".join(freeze_problems(["alpha", "beta"], run))
    for needle in (
        "runner failed",
        "collection error",
        "exactly one criterion",
        "unknown criterion",
        "skip and xfail",
        "criterion beta needs at least 2",
        "already passes",
    ):
        assert needle in problems


def test_empty_suite_cannot_be_frozen() -> None:
    assert "collected no tests" in "\n".join(freeze_problems(["alpha"], SuiteRun()))
