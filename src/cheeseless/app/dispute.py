"""Dispute use cases (ADR 0009, stage g).

``gate_dispute`` is the only implementer-facing operation. Listing, resolving and policy
changes are host-only and are reachable from the CLI alone.
"""

from __future__ import annotations

from cheeseless.app.gate_run import PRE_HIDDEN_ATTEMPT_LIMIT, PRE_HIDDEN_ATTEMPT_WINDOW
from cheeseless.app.presence import presence_note, require_presence
from cheeseless.domain.dispute import (
    ACK_TEXT,
    DisputeCode,
    DisputePolicy,
    DisputeRecord,
    DisputeStatus,
    check_note,
)
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.domain.presence import PresenceOperation, PresenceRequest
from cheeseless.ports.presence import PresenceProof
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


def resolve_dispute(
    store: Store,
    dispute_id: str,
    *,
    upheld: bool,
    actor: str,
    presence: PresenceProof | None,
) -> DisputeRecord:
    """Record the outcome once. Raises ``StoreError`` on a repeat or an unknown id.

    The decision needs a presence confirmation (ADR 0017), asked only for a dispute that is
    still open. Raises ``PresenceRefused`` without one. The store's resolve is itself a
    compare-and-set on OPEN, so a dispute resolved elsewhere while the operator answered
    is refused rather than resolved twice.
    """
    outcome = DisputeStatus.UPHELD if upheld else DisputeStatus.REJECTED
    pending = next((d for d in store.disputes() if d.dispute_id == dispute_id), None)
    if pending is None:
        raise StoreError("no such dispute")
    if pending.status is not DisputeStatus.OPEN:
        raise StoreError(f"dispute {dispute_id} is already resolved")
    confirmation = require_presence(
        presence,
        PresenceRequest(
            PresenceOperation.DISPUTE_RESOLVE,
            pending.lineage_id,
            dispute_id,
            actor,
            outcome.value,
        ),
    )
    record = store.resolve_dispute(dispute_id, outcome, actor)
    store.log_event(
        record.task_id, "dispute-presence", f"{dispute_id} {actor} {presence_note(confirmation)}"
    )
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
