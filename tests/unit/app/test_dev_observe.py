"""The read-only dev observation use case (S2): one event per call, no write path."""

from dataclasses import dataclass, field

import pytest

from cheeseless.app.dev_observe import read_event
from cheeseless.domain.dev_events import (
    Actor,
    Awaiting,
    EventKind,
    Hint,
    ReferenceView,
    Stage,
)
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.dispute import DisputeCode, DisputeRecord, DisputeStatus
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState
from cheeseless.ports import dev_observe
from cheeseless.ports.store import StoreError

LINEAGE = "demo"
DIGEST = "a" * 64
OBSERVED = "2026-11-19T10:00:00Z"


def _state(**fields: object) -> DevState:
    return DevState(lineage_id=LINEAGE, **fields)  # type: ignore[arg-type]


def _attempt(
    attempt_id: str,
    generation: int,
    state: ReferenceState,
    *,
    digest: str = DIGEST,
    mode: ReferenceMode = ReferenceMode.TUI,
) -> ReferenceAttempt:
    return ReferenceAttempt(
        attempt_id,
        "1" * 32,
        generation,
        digest,
        mode,
        False,
        state,
        LINEAGE,
        None,
        None,
    )


def _dispute(dispute_id: str, status: DisputeStatus, lineage: str = LINEAGE) -> DisputeRecord:
    return DisputeRecord(
        dispute_id=dispute_id,
        task_id="task-1",
        lineage_id=lineage,
        criterion_id="criterion-1",
        code=DisputeCode.OTHER,
        note="",
        status=status,
    )


@dataclass
class FakeObservation:
    """A read-only port double. It has no write methods, by design."""

    state: DevState | None = None
    attempts: tuple[ReferenceAttempt, ...] = ()
    records: tuple[DisputeRecord, ...] = ()
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def dev_state(self, lineage_id: str) -> DevState | None:
        self.calls.append("dev_state")
        if self.error is not None:
            raise self.error
        return self.state

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        self.calls.append("reference_attempts")
        return self.attempts

    def disputes(self) -> tuple[DisputeRecord, ...]:
        self.calls.append("disputes")
        return self.records


def test_port_exposes_read_methods_only() -> None:
    members = {name for name in dir(dev_observe.DevObservation) if not name.startswith("_")}

    assert members == {"dev_state", "reference_attempts", "disputes"}


def test_no_state_yields_no_event() -> None:
    port = FakeObservation(state=None)

    assert read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED) is None


def test_snapshot_projects_the_persisted_state() -> None:
    port = FakeObservation(
        state=_state(phase=DevPhase.TESTS, spec_digest=DIGEST, version=4),
    )

    made = read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)

    assert made is not None
    assert made.kind is EventKind.SNAPSHOT
    assert made.lineage == LINEAGE
    assert made.view is not None
    assert made.view.stage is Stage.TESTS
    assert made.view.actor is Actor.TEST_WRITER
    assert made.view.awaiting is Awaiting.REFERENCE_DONE
    assert made.view.state_version == 4


def test_uses_the_latest_reference_attempt_for_the_same_spec() -> None:
    port = FakeObservation(
        state=_state(phase=DevPhase.TESTS, spec_digest=DIGEST),
        attempts=(
            _attempt("a" * 32, 1, ReferenceState.DONE),
            _attempt("b" * 32, 2, ReferenceState.RUNNING),
            _attempt("c" * 32, 3, ReferenceState.FAILED, digest="d" * 64),
        ),
    )

    made = read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)

    assert made is not None and made.view is not None
    assert made.view.reference is ReferenceView.RUNNING_TUI
    assert made.view.stage is Stage.REFERENCE


def test_an_attempt_for_another_spec_is_ignored() -> None:
    port = FakeObservation(
        state=_state(phase=DevPhase.TESTS, spec_digest=DIGEST),
        attempts=(_attempt("a" * 32, 1, ReferenceState.DONE, digest="e" * 64),),
    )

    made = read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)

    assert made is not None and made.view is not None
    assert made.view.reference is ReferenceView.NONE
    assert made.view.awaiting is Awaiting.REFERENCE_DONE


def test_counts_only_open_disputes_for_this_lineage() -> None:
    port = FakeObservation(
        state=_state(phase=DevPhase.DISPUTED, spec_digest=DIGEST, proposal_id="c" * 64),
        records=(
            _dispute("d1", DisputeStatus.OPEN),
            _dispute("d2", DisputeStatus.REJECTED),
            _dispute("d3", DisputeStatus.OPEN, lineage="other"),
            _dispute("d4", DisputeStatus.OPEN),
        ),
    )

    made = read_event(port, LINEAGE, EventKind.CHANGE, OBSERVED)

    assert made is not None and made.view is not None
    assert made.view.open_disputes == 2
    assert made.view.stage is Stage.DISPUTE
    assert made.view.hint is Hint.RUN_DISPUTE_RESOLVE


def test_corrupt_store_yields_the_fixed_error_line() -> None:
    port = FakeObservation(error=StoreError("corrupt dev session state for demo: boom"))

    made = read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)

    assert made is not None
    assert made.kind is EventKind.ERROR
    assert made.view is None
    assert made.observed_at == OBSERVED
    assert made.lineage == LINEAGE


def test_store_error_message_never_reaches_the_event() -> None:
    port = FakeObservation(error=StoreError("secret path /home/x/store"))

    made = read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)

    assert made is not None
    assert "secret" not in repr(made)


def test_unexpected_errors_are_not_swallowed() -> None:
    port = FakeObservation(error=RuntimeError("bug"))

    with pytest.raises(RuntimeError):
        read_event(port, LINEAGE, EventKind.SNAPSHOT, OBSERVED)
