"""Small reusable mapping from domain objects to JSON-friendly dicts.

Inbound adapters (CLI, MCP, UDS) use this instead of importing protobuf into app.
"""

from __future__ import annotations

from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.errors import GateError
from cheeseless.domain.result import Result


def result_to_dict(result: Result) -> dict[str, object]:
    return {
        "task_id": result.task_id,
        "tree_hash": result.tree_hash,
        "baseline_digest": result.baseline_digest,
        "profile": result.profile,
        "tool_version": result.tool_version,
        "status": result.status.value,
        "criteria": [
            {"criterion_id": c.criterion_id, "status": c.status.value} for c in result.criteria
        ],
    }


def gate_error_to_dict(error: GateError) -> dict[str, object]:
    details = error.details
    return {
        "code": error.code.value,
        "reason": error.reason,
        "hint": error.hint,
        "retryable": error.retryable,
        "details": {
            "criterion_id": details.criterion_id,
            "agent_written_path": details.agent_written_path,
            "budget": (
                None
                if details.budget is None
                else {"remaining": details.budget.remaining, "limit": details.budget.limit}
            ),
            "host": details.host,
        },
        "docs": error.docs,
    }


def agent_view_to_dict(view: AgentView) -> dict[str, object]:
    outcome = view.outcome
    if isinstance(outcome, Result):
        outcome_dict: object = {"kind": "result", "result": result_to_dict(outcome)}
    else:
        outcome_dict = {"kind": "error", "error": gate_error_to_dict(outcome)}
    return {
        "outcome": outcome_dict,
        "failures": [gate_error_to_dict(e) for e in view.failures],
    }