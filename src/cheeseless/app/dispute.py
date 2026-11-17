"""Dispute use cases (ADR 0009, stage g).

``gate_dispute`` is the only implementer-facing operation. Listing, resolving and policy
changes are host-only and are reachable from the CLI alone.
"""

from __future__ import annotations

from cheeseless.app.gate_run import PRE_HIDDEN_ATTEMPT_LIMIT, PRE_HIDDEN_ATTEMPT_WINDOW
from cheeseless.domain.dispute import (
    ACK_TEXT,
    DisputeCode,
    DisputePolicy,
    DisputeRecord,
    DisputeStatus,
    check_note,
)
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.ports.store import DisputeRefused, Store, StoreError


def gate_dispute(
    store: Store,
    *,
    task_id: str,
    criterion_id: object,
    code: object,
    note: object,
) -> str | GateError:
    """Record a dispute and end the session, or return a catalogued refusal.

    Success always returns the same fixed acknowledgement. Input that is not accepted
    gets one generic error that never echoes the input, so a hidden criterion id and an
    unknown id are indistinguishable.
    """
    if not store.dispute_policy().enabled:
        return gate_error(ErrorCode.DISPUTE_DISABLED)
    frozen, _seed = store.load_task(task_id)
    task = frozen.task
    try:
        if not isinstance(criterion_id, str) or criterion_id not in task.criterion_ids:
            raise ValueError
        parsed = DisputeCode(code)
        text = check_note(note)
    except ValueError:
        return gate_error(ErrorCode.DISPUTE_INVALID)
    try:
        store.file_dispute(task_id, task.lineage_id, criterion_id, parsed, text)
    except DisputeRefused as refused:
        return gate_error(refused.code)
    return ACK_TEXT


def list_disputes(store: Store) -> tuple[DisputeRecord, ...]:
    return store.disputes()


def resolve_dispute(store: Store, dispute_id: str, *, upheld: bool, actor: str) -> DisputeRecord:
    """Record the outcome once. Raises ``StoreError`` on a repeat or an unknown id."""
    outcome = DisputeStatus.UPHELD if upheld else DisputeStatus.REJECTED
    record = store.resolve_dispute(dispute_id, outcome, actor)
    if outcome is DisputeStatus.REJECTED:
        store.log_event(
            record.task_id,
            "dispute-reopen-budget",
            f"{dispute_id} attempts={PRE_HIDDEN_ATTEMPT_LIMIT}/{int(PRE_HIDDEN_ATTEMPT_WINDOW)}s"
            " run-budget=unchanged",
        )
    return record


def configure_disputes(store: Store, *, enabled: bool, cap: int) -> DisputePolicy:
    try:
        policy = DisputePolicy(enabled=enabled, cap=cap)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    store.set_dispute_policy(policy)
    return policy


__all__ = ["configure_disputes", "gate_dispute", "list_disputes", "resolve_dispute"]
