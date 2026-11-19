"""Read one dev-event for a lineage through the read-only observation port."""

from __future__ import annotations

from cheeseless.domain.dev_events import (
    DevEvent,
    DevView,
    EventKind,
    derive,
    error_event,
    event,
)
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.dispute import DisputeStatus
from cheeseless.domain.reference_attempt import ReferenceAttempt
from cheeseless.ports.dev_observe import DevObservation
from cheeseless.ports.store import StoreError


def read_event(
    port: DevObservation,
    lineage: str,
    kind: EventKind,
    observed_at: str,
) -> DevEvent | None:
    """One event for ``lineage``, or None when the lineage has no state yet.

    A corrupt store yields the fixed error line; its message is never copied into the event.
    Any other exception is a bug and propagates.
    """
    try:
        state = port.dev_state(lineage)
        if state is None:
            return None
        attempt = _latest_attempt(port, lineage, state)
        open_disputes = _open_disputes(port, lineage)
    except StoreError:
        return error_event(observed_at, lineage)
    view: DevView = derive(state, attempt, open_disputes)
    return event(kind, observed_at, lineage, view)


def _latest_attempt(
    port: DevObservation,
    lineage: str,
    state: DevState,
) -> ReferenceAttempt | None:
    matching = [
        attempt
        for attempt in port.reference_attempts(lineage)
        if attempt.lineage_id == lineage and attempt.spec_digest == state.spec_digest
    ]
    if not matching:
        return None
    return max(matching, key=lambda attempt: attempt.generation)


def _open_disputes(port: DevObservation, lineage: str) -> int:
    return sum(
        1
        for record in port.disputes()
        if record.lineage_id == lineage and record.status is DisputeStatus.OPEN
    )
