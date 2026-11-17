"""The ``reference_done`` gate of a dev-mode TUI reference session (ADR 0014).

Admission is decided by the caller (it needs the session record); this function only does the
compare-and-set running -> stopping and tells the host, through a seam, to stop the attempt.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.domain.reference_attempt import AttemptOwner, ReferenceState, TransitionRefused
from cheeseless.ports.reference_attempt import ReferenceAttemptStore


@dataclass(frozen=True, slots=True)
class ReferenceStopping:
    attempt_id: str


def gate_reference_done(
    store: ReferenceAttemptStore,
    attempt_id: str,
    owner: AttemptOwner,
    on_stop: Callable[[str], None] | None = None,
) -> ReferenceStopping | GateError:
    """Move the attempt running -> stopping once; a duplicate or stale call changes nothing.

    ``owner`` is re-checked under the store lock, so a record that changed hands between
    admission and this call is refused rather than stopped.
    """
    try:
        outcome = store.transition_reference_attempt(
            attempt_id, ReferenceState.RUNNING, ReferenceState.STOPPING, owner=owner
        )
    except Exception:  # noqa: BLE001 - raw text never leaves the gate
        return gate_error(ErrorCode.INFRA_ERROR)
    if isinstance(outcome, TransitionRefused):
        return gate_error(ErrorCode.REFERENCE_REFUSED)
    if on_stop is not None:
        # the state already moved; a failing host seam must not leak or undo it
        with contextlib.suppress(Exception):
            on_stop(outcome.attempt_id)
    return ReferenceStopping(outcome.attempt_id)


__all__ = ["ReferenceStopping", "gate_reference_done"]
