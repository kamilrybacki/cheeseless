"""Lifecycle moves of a dev-mode reference attempt (ADR 0014).

The pure state machine lives in :mod:`cheeseless.domain.reference_attempt`; this module
is the use-case layer: choosing the mode before launch, and the compare-and-set moves the
host makes around the sandbox (fail, cancel, stop and complete) plus stale reaping.
Every move is a compare-and-set on the stored record, so a second host or a late
``reference_done`` cannot move an attempt twice.
"""

from collections.abc import Callable

from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.ports.reference_attempt import ReferenceAttemptStore

S = ReferenceState


def choose_mode(*, headless: bool, in_tmux: bool) -> ReferenceMode:
    """TUI unless asked for headless or the host is not inside tmux (ADR 0014)."""
    return ReferenceMode.TUI if in_tmux and not headless else ReferenceMode.HEADLESS


def _owner(attempt: ReferenceAttempt) -> AttemptOwner:
    return AttemptOwner(attempt.session_id, attempt.generation, attempt.mode)


def _moved(
    store: ReferenceAttemptStore, *args: object, **kwargs: object
) -> ReferenceAttempt | None:
    outcome = store.transition_reference_attempt(*args, **kwargs)  # type: ignore[arg-type]
    return outcome if isinstance(outcome, ReferenceAttempt) else None


def fail_attempt(
    store: ReferenceAttemptStore, attempt: ReferenceAttempt
) -> ReferenceAttempt | None:
    """Move ``running`` or ``stopping`` to ``failed``; a lost race returns ``None``."""
    if attempt.state not in (S.RUNNING, S.STOPPING):
        return None
    return _moved(store, attempt.attempt_id, attempt.state, S.FAILED, owner=_owner(attempt))


def cancel_attempt(
    store: ReferenceAttemptStore, attempt: ReferenceAttempt
) -> ReferenceAttempt | None:
    """Move ``running`` or ``stopping`` to ``cancelled``; a lost race returns ``None``."""
    if attempt.state not in (S.RUNNING, S.STOPPING):
        return None
    return _moved(store, attempt.attempt_id, attempt.state, S.CANCELLED, owner=_owner(attempt))


def stop_attempt(
    store: ReferenceAttemptStore, attempt: ReferenceAttempt
) -> ReferenceAttempt | None:
    """Move ``running`` to ``stopping`` (headless exit 0); a lost race returns ``None``."""
    if attempt.state is not S.RUNNING:
        return None
    return _moved(store, attempt.attempt_id, S.RUNNING, S.STOPPING, owner=_owner(attempt))


def complete_attempt(
    store: ReferenceAttemptStore, attempt: ReferenceAttempt, result_tree: str
) -> ReferenceAttempt | None:
    """Move ``stopping`` to ``done`` and store the snapshot atomically with the move."""
    if attempt.state is not S.STOPPING:
        return None
    return _moved(
        store,
        attempt.attempt_id,
        S.STOPPING,
        S.DONE,
        owner=_owner(attempt),
        result_tree=result_tree,
    )


def reap_attempts(
    store: ReferenceAttemptStore,
    lineage_id: str,
    close_pane: Callable[[str], None],
) -> tuple[ReferenceAttempt, ...]:
    """Fail attempts a dead host left ``running``/``stopping`` and kill their panes.

    Terminal attempts are left alone: a tmux pane id can be reused after the tmux server
    restarts, so only a pane this host has just reaped is safe to kill.
    """
    reaped: list[ReferenceAttempt] = []
    for attempt in store.reference_attempts(lineage_id):
        if attempt.state not in (S.RUNNING, S.STOPPING):
            continue
        failed = fail_attempt(store, attempt)
        if failed is None:  # a live host won the race; never touch its pane
            continue
        reaped.append(failed)
        if failed.pane_id is not None:
            close_pane(failed.pane_id)
    return tuple(reaped)


__all__ = [
    "cancel_attempt",
    "choose_mode",
    "complete_attempt",
    "fail_attempt",
    "reap_attempts",
    "stop_attempt",
]
