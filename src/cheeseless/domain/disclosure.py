"""Turn an internal run report into what the agent may see (Q51, Q52, D2, D17).

Fail closed: a criterion that did not report is FAIL; an unknown criterion makes
the whole run FAIL; nothing post-hidden ever carries free text.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from cheeseless.domain.errors import (
    PRE_HIDDEN_STAGE_CODES,
    ErrorCode,
    ErrorDetails,
    GateError,
    gate_error,
)
from cheeseless.domain.result import CriterionResult, Result, overall_status
from cheeseless.domain.status import Status
from cheeseless.domain.task import Task


@dataclass(frozen=True, slots=True)
class RunReport:
    """What the runner observed. Internal only; never sent to the agent as is.

    ``stderr_tail`` must already be DLP-scanned; it is dropped after hidden input.
    ``infra_cause`` is operator-only text.
    """

    hidden_input_read: bool = False
    pre_hidden_failure: ErrorCode | None = None
    stderr_tail: str = ""
    criteria: Mapping[str, Status] = field(default_factory=dict)
    infra_cause: str = ""

    def __post_init__(self) -> None:
        failure = self.pre_hidden_failure
        if failure is not None and failure not in PRE_HIDDEN_STAGE_CODES:
            raise ValueError(f"{failure!r} is not a pre-hidden stage failure")
        if failure is not None and self.hidden_input_read:
            raise ValueError("a pre-hidden failure cannot follow hidden input")
        if self.criteria and not self.hidden_input_read:
            raise ValueError("criteria results require hidden input")


@dataclass(frozen=True, slots=True)
class AgentView:
    """Everything the agent may see about one run. Adapters serialise only this."""

    outcome: Result | GateError
    failures: tuple[GateError, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    """Internal decision. ``operator_cause`` and ``charged`` never reach the agent."""

    outcome: Result | GateError
    charged: bool
    operator_cause: str = ""

    def agent_view(self, task: Task) -> AgentView:
        return AgentView(self.outcome, self.criterion_failures(task))

    def criterion_failures(self, task: Task) -> tuple[GateError, ...]:
        """One ``CRITERION_FAILED`` per failed criterion, with its public text."""
        if not isinstance(self.outcome, Result):
            return ()
        return tuple(
            gate_error(
                ErrorCode.CRITERION_FAILED,
                details=ErrorDetails(criterion_id=c.criterion_id),
                public_description=task.criterion(c.criterion_id).description,
            )
            for c in self.outcome.criteria
            if c.status is Status.FAIL
        )


def _pre_hidden(report: RunReport) -> Verdict:
    if report.pre_hidden_failure is not None and not report.infra_cause:
        error = gate_error(report.pre_hidden_failure, stderr_tail=report.stderr_tail)
        return Verdict(error, charged=False)
    cause = report.infra_cause or "run ended before hidden input without a verdict"
    return Verdict(gate_error(ErrorCode.INFRA_ERROR), charged=False, operator_cause=cause)


def _post_hidden_criteria(task: Task, report: RunReport) -> tuple[tuple[CriterionResult, ...], str]:
    notes: list[str] = []
    unknown = sorted(set(report.criteria) - set(task.criterion_ids))
    if unknown:
        notes.append(f"unknown criteria reported: {', '.join(unknown)}")
    missing = [cid for cid in task.criterion_ids if cid not in report.criteria]
    if missing:
        notes.append(f"criteria without a result: {', '.join(missing)}")
    results = []
    for cid in task.criterion_ids:
        status = report.criteria.get(cid, Status.FAIL)
        if status is not Status.PASS or unknown or report.infra_cause:
            status = Status.FAIL
        results.append(CriterionResult(cid, status))
    return tuple(results), "; ".join(notes)


def decide(
    task: Task,
    report: RunReport,
    *,
    tree_hash: str,
    baseline_digest: str,
    profile: str,
    tool_version: str,
) -> Verdict:
    """Map a report to the agent view. The caller signs the Result."""
    if not report.hidden_input_read:
        return _pre_hidden(report)
    criteria, notes = _post_hidden_criteria(task, report)
    cause = "; ".join(part for part in (report.infra_cause, notes) if part)
    result = Result(
        task_id=task.task_id,
        tree_hash=tree_hash,
        baseline_digest=baseline_digest,
        profile=profile,
        tool_version=tool_version,
        status=overall_status(criteria),
        criteria=criteria,
    )
    return Verdict(result, charged=True, operator_cause=cause)
