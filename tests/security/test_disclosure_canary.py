"""The disclosure filter never lets hidden-stage text reach the agent (Q51, D17, AC 23).

The canary stands in for anything a candidate could print after reading hidden
inputs, and for operator-only infra causes. It must not appear in any field of the
agent view.
"""

import dataclasses

import pytest

from cheeseless.domain.disclosure import AgentView, RunReport, decide
from cheeseless.domain.errors import ErrorCode, gate_error
from cheeseless.domain.status import Status
from cheeseless.domain.task import Criterion, Task

CANARY = "CHEESELESS-CANARY-7f3a9c"
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


def _flatten(value: object) -> str:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return " ".join(_flatten(getattr(value, f.name)) for f in dataclasses.fields(value))
    if isinstance(value, tuple | list):
        return " ".join(_flatten(v) for v in value)
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)


REPORTS = {
    "post-hidden-stderr": RunReport(
        hidden_input_read=True, criteria={"normalize": Status.FAIL}, stderr_tail=CANARY
    ),
    "post-hidden-infra-cause": RunReport(
        hidden_input_read=True, criteria={"normalize": Status.PASS}, infra_cause=CANARY
    ),
    "pre-hidden-infra-cause": RunReport(infra_cause=CANARY),
    "pre-hidden-infra-with-stage": RunReport(
        pre_hidden_failure=ErrorCode.BUILD_FAILED, stderr_tail="ok", infra_cause=CANARY
    ),
    "unknown-criterion-key": RunReport(
        hidden_input_read=True,
        criteria={"normalize": Status.PASS, "score": Status.PASS, "canary-7f3a9c": Status.PASS},
    ),
}


@pytest.mark.parametrize("report", REPORTS.values(), ids=REPORTS.keys())
def test_canary_never_reaches_the_agent_view(report: RunReport) -> None:
    view = decide(TASK, report, **BINDING).agent_view(TASK)
    assert isinstance(view, AgentView)
    text = _flatten(view)
    assert CANARY not in text
    assert "canary-7f3a9c" not in text


def test_agent_view_has_no_operator_fields() -> None:
    names = {f.name for f in dataclasses.fields(AgentView)}
    assert names == {"outcome", "failures"}


def test_pre_hidden_stderr_is_visible_as_control() -> None:
    """Control: the flatten helper does see a tail that is allowed through."""
    report = RunReport(pre_hidden_failure=ErrorCode.BUILD_FAILED, stderr_tail=CANARY)
    assert CANARY in _flatten(decide(TASK, report, **BINDING).agent_view(TASK))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reason": CANARY},
        {"hint": CANARY},
        {"docs": CANARY},
        {"pre_hidden_stderr_tail": CANARY},
        {"retryable": False},
    ],
)
def test_direct_construction_cannot_smuggle_text(kwargs: dict[str, object]) -> None:
    """Codex review: GateError refuses non-catalogue text even without gate_error()."""
    base = gate_error(ErrorCode.INFRA_ERROR)
    with pytest.raises(ValueError):
        dataclasses.replace(base, **kwargs)


def test_criterion_reason_only_accepts_the_template_prefix() -> None:
    base = gate_error(ErrorCode.CRITERION_FAILED, public_description="CLI normalises")
    assert dataclasses.replace(base) == base
    with pytest.raises(ValueError):
        dataclasses.replace(base, reason=CANARY + base.reason)
