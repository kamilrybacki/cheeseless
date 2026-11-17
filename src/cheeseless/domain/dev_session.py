"""The ``cheeseless dev`` phase state machine (ADR 0013).

One record per lineage, owned by the host. The author interviews the user about the
spec (``SPEC``), writes and freezes the tests (``TESTS``), and a fresh implementer then
works against the frozen gate (``IMPLEMENT``). Only the author's session is ever resumed:
the implementer always starts clean, so it never inherits the author's context.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

from cheeseless.domain import ids

MAX_HOLDOUT_FAILURES = 3


class DevSessionError(Exception):
    """A transition the current phase does not allow, or a stale save."""


class DevPhase(Enum):
    SPEC = "spec"
    TESTS = "tests"
    IMPLEMENT = "implement"
    BLOCKED = "blocked"
    DISPUTED = "disputed"
    DONE = "done"
    ABORTED = "aborted"


_AUTHOR_PHASES = frozenset({DevPhase.SPEC, DevPhase.TESTS})
_FROZEN_PHASES = frozenset({DevPhase.IMPLEMENT, DevPhase.BLOCKED, DevPhase.DISPUTED, DevPhase.DONE})
_TERMINAL = frozenset({DevPhase.DONE, DevPhase.ABORTED})


def _optional(check: Callable[[object], str], value: str | None) -> None:
    if value is not None:
        check(value)


@dataclass(frozen=True, slots=True)
class DevState:
    lineage_id: str
    phase: DevPhase = DevPhase.SPEC
    spec_digest: str | None = None
    proposal_id: str | None = None
    author_session: str | None = None
    implementer_session: str | None = None
    holdout_failures: int = 0
    version: int = 0

    def __post_init__(self) -> None:
        ids.lineage_id(self.lineage_id)
        _optional(lambda v: ids.sha256_hex(v, "spec digest"), self.spec_digest)
        _optional(lambda v: ids.sha256_hex(v, "proposal id"), self.proposal_id)
        _optional(ids.session_id, self.author_session)
        _optional(ids.session_id, self.implementer_session)
        if not 0 <= self.holdout_failures <= MAX_HOLDOUT_FAILURES:
            raise ValueError(f"invalid holdout failure count: {self.holdout_failures}")
        if self.version < 0:
            raise ValueError(f"invalid version: {self.version}")
        past_spec = self.phase not in (DevPhase.SPEC, DevPhase.ABORTED)
        if past_spec and self.spec_digest is None:
            raise ValueError(f"the {self.phase.value} phase needs a spec digest")
        if self.phase in _FROZEN_PHASES and self.proposal_id is None:
            raise ValueError(f"the {self.phase.value} phase needs a frozen proposal")
        if self.phase in _AUTHOR_PHASES and self.implementer_session is not None:
            raise ValueError("an implementer session exists only after the freeze")

    # -- derived -------------------------------------------------------------

    @property
    def terminal(self) -> bool:
        return self.phase in _TERMINAL

    @property
    def needs_refreeze(self) -> bool:
        return self.phase is DevPhase.IMPLEMENT and self.holdout_failures >= MAX_HOLDOUT_FAILURES

    @property
    def judgment_allowed(self) -> bool:
        return self.phase is DevPhase.IMPLEMENT and self.holdout_failures < MAX_HOLDOUT_FAILURES

    def _require(self, *phases: DevPhase) -> None:
        if self.phase not in phases:
            allowed = ", ".join(p.value for p in phases)
            raise DevSessionError(f"not allowed in the {self.phase.value} phase (needs {allowed})")

    # -- author --------------------------------------------------------------

    def author_started(self, session: str) -> "DevState":
        self._require(DevPhase.SPEC, DevPhase.TESTS)
        return replace(self, author_session=session)

    def spec_done(self, digest: str) -> "DevState":
        self._require(DevPhase.SPEC)
        return replace(self, phase=DevPhase.TESTS, spec_digest=digest)

    def spec_changed(self, digest: str) -> "DevState":
        """The author edited the spec while writing tests: any proposal is stale."""
        self._require(DevPhase.TESTS)
        if digest == self.spec_digest:
            return self
        return replace(self, spec_digest=digest, proposal_id=None)

    def proposed(self, proposal_id: str) -> "DevState":
        self._require(DevPhase.TESTS)
        return replace(self, proposal_id=proposal_id)

    def frozen(self, proposal_id: str) -> "DevState":
        self._require(DevPhase.TESTS)
        if self.proposal_id is None or proposal_id != self.proposal_id:
            raise DevSessionError("only the recorded proposal can be frozen")
        return replace(self, phase=DevPhase.IMPLEMENT, holdout_failures=0)

    # -- implementer ---------------------------------------------------------

    def implementer_started(self, session: str) -> "DevState":
        self._require(DevPhase.IMPLEMENT)
        return replace(self, implementer_session=session)

    def holdout_failed(self) -> "DevState":
        self._require(DevPhase.IMPLEMENT)
        if self.needs_refreeze:
            raise DevSessionError("holdout failed too often: re-freeze or abort")
        return replace(self, holdout_failures=self.holdout_failures + 1)

    def passed(self) -> "DevState":
        self._require(DevPhase.IMPLEMENT)
        if not self.judgment_allowed:
            raise DevSessionError("holdout failed too often: re-freeze or abort")
        return replace(self, phase=DevPhase.DONE)

    def refreeze(self) -> "DevState":
        """Back to the author for a new split and seed after the holdout limit (ADR 0005)."""
        self._require(DevPhase.IMPLEMENT)
        if not self.needs_refreeze:
            raise DevSessionError("a re-freeze is only needed after the holdout limit")
        return self._back_to_author()

    def pool_empty(self) -> "DevState":
        self._require(DevPhase.IMPLEMENT)
        return replace(self, phase=DevPhase.BLOCKED)

    def runs_granted(self) -> "DevState":
        self._require(DevPhase.BLOCKED)
        return replace(self, phase=DevPhase.IMPLEMENT, implementer_session=None)

    # -- disputes (ADR 0009) ---------------------------------------------------

    def disputed(self) -> "DevState":
        self._require(DevPhase.IMPLEMENT)
        return replace(self, phase=DevPhase.DISPUTED)

    def dispute_rejected(self) -> "DevState":
        self._require(DevPhase.DISPUTED)
        return replace(self, phase=DevPhase.IMPLEMENT, implementer_session=None)

    def dispute_upheld(self) -> "DevState":
        self._require(DevPhase.DISPUTED)
        return self._back_to_author()

    def aborted(self) -> "DevState":
        if self.terminal:
            raise DevSessionError(f"the session is already {self.phase.value}")
        return replace(self, phase=DevPhase.ABORTED)

    def _back_to_author(self) -> "DevState":
        return replace(
            self,
            phase=DevPhase.TESTS,
            proposal_id=None,
            implementer_session=None,
            holdout_failures=0,
        )


@dataclass(frozen=True, slots=True)
class ResumeTarget:
    role: Literal["author", "implementer"]
    session: str | None


_REFUSALS = {
    DevPhase.BLOCKED: "the local run pool is empty: grant more runs or abort",
    DevPhase.DISPUTED: "a dispute is open: rule on it first",
    DevPhase.DONE: "the session is done: start a new one",
    DevPhase.ABORTED: "the session was aborted: start a new one",
}


def resume_target(state: DevState) -> ResumeTarget:
    """Who continues the session. Only the author's session is ever resumed."""
    if state.phase in _AUTHOR_PHASES:
        return ResumeTarget("author", state.author_session)
    if state.needs_refreeze:
        raise DevSessionError("holdout failed too often: re-freeze or abort")
    if state.phase is DevPhase.IMPLEMENT:
        return ResumeTarget("implementer", None)
    raise DevSessionError(_REFUSALS[state.phase])
