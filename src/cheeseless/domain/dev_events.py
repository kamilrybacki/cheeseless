"""The read-only dev-event projection: a closed, versioned view of one dev lineage.

Pure domain: no I/O. An event is derived from the persisted dev state, the latest
reference attempt for the same spec, and the open-dispute count. Every field is a closed
enum or a bounded integer, so no free text, path or secret can reach the wire.
Unknown or corrupt state is never guessed at; the adapter emits ``EventKind.ERROR``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.ids import lineage_id
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState

SCHEMA_VERSION = 1

_OBSERVED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class EventKind(StrEnum):
    SNAPSHOT = "snapshot"
    CHANGE = "change"
    HEARTBEAT = "heartbeat"
    END = "end"
    ERROR = "error"


class Stage(StrEnum):
    SPEC = "spec"
    TESTS = "tests"
    REFERENCE = "reference"
    LOCK = "lock"
    CODE = "code"
    DISPUTE = "dispute"
    BLOCKED = "blocked"
    DONE = "done"
    ABORTED = "aborted"


class Actor(StrEnum):
    TEST_WRITER = "test_writer"
    REFERENCE_CODER = "reference_coder"
    CODER = "coder"
    OPERATOR = "operator"
    HOST = "host"
    NONE = "none"


class Awaiting(StrEnum):
    NONE = "none"
    SPEC_DONE = "spec_done"
    REFERENCE_DONE = "reference_done"
    LOCK_APPROVAL = "lock_approval"
    DISPUTE_RESOLUTION = "dispute_resolution"
    RUN_GRANT = "run_grant"
    REFREEZE_OR_ABORT = "refreeze_or_abort"


class ReferenceView(StrEnum):
    NONE = "none"
    RUNNING_TUI = "running_tui"
    RUNNING_HEADLESS = "running_headless"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Hint(StrEnum):
    """Message keys only. A viewer displays them and never runs them."""

    NONE = "none"
    RUN_DEV_RESUME = "run_dev_resume"
    RUN_FREEZE_APPROVE = "run_freeze_approve"
    RUN_DISPUTE_RESOLVE = "run_dispute_resolve"
    GRANT_RUNS_OR_ABORT = "grant_runs_or_abort"
    REFREEZE_OR_ABORT = "refreeze_or_abort"


@dataclass(frozen=True, slots=True)
class DevView:
    stage: Stage
    actor: Actor
    awaiting: Awaiting
    reference: ReferenceView
    holdout_failures: int
    open_disputes: int
    state_version: int
    hint: Hint


@dataclass(frozen=True, slots=True)
class DevEvent:
    kind: EventKind
    observed_at: str
    lineage: str
    view: DevView | None


# (stage, actor, awaiting, hint) for every phase except TESTS, which depends on the reference.
_Row = tuple[Stage, Actor, Awaiting, Hint]

_PHASE_ROWS: dict[DevPhase, _Row] = {
    DevPhase.SPEC: (Stage.SPEC, Actor.TEST_WRITER, Awaiting.SPEC_DONE, Hint.RUN_DEV_RESUME),
    DevPhase.IMPLEMENT: (Stage.CODE, Actor.CODER, Awaiting.NONE, Hint.NONE),
    DevPhase.BLOCKED: (Stage.BLOCKED, Actor.OPERATOR, Awaiting.RUN_GRANT, Hint.GRANT_RUNS_OR_ABORT),
    DevPhase.DISPUTED: (
        Stage.DISPUTE,
        Actor.OPERATOR,
        Awaiting.DISPUTE_RESOLUTION,
        Hint.RUN_DISPUTE_RESOLVE,
    ),
    DevPhase.DONE: (Stage.DONE, Actor.NONE, Awaiting.NONE, Hint.NONE),
    DevPhase.ABORTED: (Stage.ABORTED, Actor.NONE, Awaiting.NONE, Hint.NONE),
}

_REFREEZE_ROW: _Row = (
    Stage.CODE,
    Actor.CODER,
    Awaiting.REFREEZE_OR_ABORT,
    Hint.REFREEZE_OR_ABORT,
)

_REFERENCE_RUNNING_ROW: _Row = (Stage.REFERENCE, Actor.REFERENCE_CODER, Awaiting.NONE, Hint.NONE)
_LOCK_ROW: _Row = (Stage.LOCK, Actor.OPERATOR, Awaiting.LOCK_APPROVAL, Hint.RUN_FREEZE_APPROVE)
_TESTS_DONE_ROW: _Row = (Stage.TESTS, Actor.TEST_WRITER, Awaiting.NONE, Hint.RUN_DEV_RESUME)
_TESTS_WAITING_ROW: _Row = (
    Stage.TESTS,
    Actor.TEST_WRITER,
    Awaiting.REFERENCE_DONE,
    Hint.RUN_DEV_RESUME,
)


def derive(state: DevState, attempt: ReferenceAttempt | None, open_disputes: int) -> DevView:
    """Project the persisted state onto the closed view. Pure and total over ``DevPhase``."""
    reference = _reference_view(state, attempt)
    stage, actor, awaiting, hint = _row(state, reference)
    return DevView(
        stage=stage,
        actor=actor,
        awaiting=awaiting,
        reference=reference,
        holdout_failures=state.holdout_failures,
        open_disputes=open_disputes,
        state_version=state.version,
        hint=hint,
    )


def _row(state: DevState, reference: ReferenceView) -> _Row:
    if state.phase is DevPhase.TESTS:
        return _tests_row(state, reference)
    if state.phase is DevPhase.IMPLEMENT and state.needs_refreeze:
        return _REFREEZE_ROW
    return _PHASE_ROWS[state.phase]


def _tests_row(state: DevState, reference: ReferenceView) -> _Row:
    if reference in (ReferenceView.RUNNING_TUI, ReferenceView.RUNNING_HEADLESS):
        return _REFERENCE_RUNNING_ROW
    if state.proposal_id is not None:
        return _LOCK_ROW
    if reference is ReferenceView.DONE:
        return _TESTS_DONE_ROW
    return _TESTS_WAITING_ROW


def _reference_view(state: DevState, attempt: ReferenceAttempt | None) -> ReferenceView:
    if state.phase is not DevPhase.TESTS or attempt is None:
        return ReferenceView.NONE
    if attempt.spec_digest != state.spec_digest:
        return ReferenceView.NONE
    if attempt.state in (ReferenceState.RUNNING, ReferenceState.STOPPING):
        if attempt.mode is ReferenceMode.HEADLESS:
            return ReferenceView.RUNNING_HEADLESS
        return ReferenceView.RUNNING_TUI
    return {
        ReferenceState.DONE: ReferenceView.DONE,
        ReferenceState.FAILED: ReferenceView.FAILED,
        ReferenceState.CANCELLED: ReferenceView.CANCELLED,
    }[attempt.state]


def event(kind: EventKind, observed_at: str, lineage: str, view: DevView) -> DevEvent:
    return DevEvent(kind, _checked_time(observed_at), lineage_id(lineage), view)


def error_event(observed_at: str, lineage: str) -> DevEvent:
    """A fixed error line: it carries no state, only the time and the lineage."""
    return DevEvent(EventKind.ERROR, _checked_time(observed_at), lineage_id(lineage), None)


def encode(made: DevEvent) -> str:
    """One compact JSON line, no trailing newline. Enum members are written as their values."""
    body: dict[str, object] = {
        "v": SCHEMA_VERSION,
        "kind": made.kind.value,
        "observed_at": made.observed_at,
        "lineage": made.lineage,
    }
    if made.view is not None:
        body.update(_view_fields(made.view))
    return json.dumps(body, separators=(",", ":"))


def _view_fields(view: DevView) -> dict[str, object]:
    return {
        "stage": view.stage.value,
        "actor": view.actor.value,
        "awaiting": view.awaiting.value,
        "reference": view.reference.value,
        "holdout_failures": view.holdout_failures,
        "open_disputes": view.open_disputes,
        "state_version": view.state_version,
        "hint": view.hint.value,
    }


def _checked_time(value: str) -> str:
    if _OBSERVED_AT.fullmatch(value) is None:
        raise ValueError("observed_at must be RFC 3339 UTC, e.g. 2026-11-19T10:00:00Z")
    return value
