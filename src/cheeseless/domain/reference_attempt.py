"""A dev-mode reference attempt and its state machine (ADR 0014). Pure: no I/O.

``running -> stopping -> done``; ``running | stopping -> failed | cancelled``. Every other
move is refused. ``transition`` returns a new record and never mutates its input.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from cheeseless.domain import ids


class ReferenceMode(StrEnum):
    TUI = "tui"
    HEADLESS = "headless"


class ReferenceState(StrEnum):
    RUNNING = "running"
    STOPPING = "stopping"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_S = ReferenceState
_ALLOWED: frozenset[tuple[ReferenceState, ReferenceState]] = frozenset(
    {
        (_S.RUNNING, _S.STOPPING),
        (_S.STOPPING, _S.DONE),
        (_S.RUNNING, _S.FAILED),
        (_S.STOPPING, _S.FAILED),
        (_S.RUNNING, _S.CANCELLED),
        (_S.STOPPING, _S.CANCELLED),
    }
)


class ReferenceTransitionError(ValueError):
    """The state machine does not allow this move."""


@dataclass(frozen=True, slots=True)
class ReferenceAttempt:
    attempt_id: str
    session_id: str
    generation: int
    spec_digest: str
    mode: ReferenceMode
    attached: bool
    state: ReferenceState

    def __post_init__(self) -> None:
        ids.attempt_id(self.attempt_id)
        ids.gate_session_id(self.session_id)
        ids.sha256_hex(self.spec_digest, "spec digest")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError(f"invalid generation: {self.generation!r}")
        if type(self.attached) is not bool:
            raise ValueError(f"invalid attached flag: {self.attached!r}")
        if not isinstance(self.mode, ReferenceMode):
            raise ValueError(f"invalid mode: {self.mode!r}")
        if not isinstance(self.state, ReferenceState):
            raise ValueError(f"invalid state: {self.state!r}")


@dataclass(frozen=True, slots=True)
class AttemptOwner:
    """Who may move an attempt: a CAS also refuses when any of these differ from the record."""

    session_id: str
    generation: int
    mode: ReferenceMode

    def owns(self, attempt: ReferenceAttempt) -> bool:
        return (
            attempt.session_id == self.session_id
            and attempt.generation == self.generation
            and attempt.mode is self.mode
        )


@dataclass(frozen=True, slots=True)
class TransitionRefused:
    """A compare-and-set lost: ``actual`` is the state found (``None``: no such attempt)."""

    attempt_id: str
    expected: ReferenceState
    actual: ReferenceState | None


def transition(attempt: ReferenceAttempt, new: ReferenceState) -> ReferenceAttempt:
    if (attempt.state, new) not in _ALLOWED:
        raise ReferenceTransitionError(f"{attempt.state.value} -> {new.value} is not allowed")
    return replace(attempt, state=new)
