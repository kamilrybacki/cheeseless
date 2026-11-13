"""Per-test outcomes → per-criterion statuses, and the freeze preconditions (Q44).

Fail closed: a criterion passes only if every one of its selected tests ran and
passed. Skips, xfails, xpasses, errors and missing tests all count as failures.
"""

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cheeseless.domain.holdout import MIN_TESTS_PER_CRITERION
from cheeseless.domain.status import Status


class TestOutcome(enum.Enum):
    __test__ = False  # not a pytest class

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"


@dataclass(frozen=True, slots=True)
class CollectedTest:
    node_id: str
    # Criterion ids named by ``@pytest.mark.criterion``; exactly one is valid.
    criteria: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """What the trusted runner observed for one pytest session."""

    collected: tuple[CollectedTest, ...] = ()
    outcomes: Mapping[str, TestOutcome] | None = None
    collection_errors: tuple[str, ...] = ()
    infra_cause: str = ""

    @property
    def outcome_map(self) -> Mapping[str, TestOutcome]:
        return self.outcomes or {}


def criteria_statuses(selected: Mapping[str, str], run: SuiteRun) -> tuple[dict[str, Status], str]:
    """Map a run of ``selected`` (node id → criterion) to statuses plus operator notes."""
    notes: list[str] = []
    if run.infra_cause:
        notes.append(run.infra_cause)
    if run.collection_errors:
        notes.append(f"collection errors in {len(run.collection_errors)} file(s)")
    outcomes = run.outcome_map
    unexpected = sorted(set(outcomes) - set(selected))
    if unexpected:
        notes.append(f"{len(unexpected)} unselected test(s) reported")
    statuses: dict[str, Status] = {}
    for node_id, cid in selected.items():
        outcome = outcomes.get(node_id)
        if outcome is None:
            notes.append(f"no outcome for {node_id}")
        ok = outcome is TestOutcome.PASSED and not run.collection_errors and not unexpected
        if not ok or statuses.get(cid) is Status.FAIL:
            statuses[cid] = Status.FAIL
        else:
            statuses[cid] = Status.PASS
    return statuses, "; ".join(notes)


def smoke_passed(run: SuiteRun) -> bool:
    """Public contract tests: non-empty, no collection errors, all passed."""
    outcomes = run.outcome_map
    return (
        not run.infra_cause
        and not run.collection_errors
        and bool(outcomes)
        and all(o is TestOutcome.PASSED for o in outcomes.values())
    )


def freeze_problems(criterion_ids: Sequence[str], run: SuiteRun) -> list[str]:
    """Preconditions for freezing the hidden suite against the baseline (Q44).

    Every test names exactly one known criterion; every criterion has enough tests
    for a holdout split and is RED on the baseline; nothing is skipped or xfailed.
    """
    problems: list[str] = []
    if run.infra_cause:
        problems.append(f"runner failed: {run.infra_cause}")
    problems.extend(f"collection error: {e}" for e in run.collection_errors)
    if not run.collected:
        problems.append("the hidden suite collected no tests")
    known = set(criterion_ids)
    per_criterion: dict[str, list[str]] = {cid: [] for cid in criterion_ids}
    for test in run.collected:
        if len(test.criteria) != 1:
            problems.append(f"{test.node_id} must name exactly one criterion")
        elif test.criteria[0] not in known:
            problems.append(f"{test.node_id} names unknown criterion {test.criteria[0]!r}")
        else:
            per_criterion[test.criteria[0]].append(test.node_id)
    outcomes = run.outcome_map
    for node_id, outcome in sorted(outcomes.items()):
        if outcome in (TestOutcome.SKIPPED, TestOutcome.XFAILED, TestOutcome.XPASSED):
            problems.append(f"{node_id} is {outcome.value}; skip and xfail are not allowed")
    for cid, nodes in per_criterion.items():
        if len(nodes) < MIN_TESTS_PER_CRITERION:
            problems.append(f"criterion {cid} needs at least {MIN_TESTS_PER_CRITERION} tests")
        elif all(outcomes.get(n) is TestOutcome.PASSED for n in nodes):
            problems.append(f"criterion {cid} already passes on the baseline (not RED)")
    return problems


def criterion_by_test(run: SuiteRun) -> dict[str, str]:
    """Node id → criterion for tests that name exactly one criterion."""
    return {t.node_id: t.criteria[0] for t in run.collected if len(t.criteria) == 1}
