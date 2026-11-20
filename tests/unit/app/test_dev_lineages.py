"""The dev-lineages use case: newest first, capped, stage from the shared observer code."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from cheeseless.app.dev_lineages import list_lineages
from cheeseless.domain.dev_events import Stage
from cheeseless.domain.dev_lineages import MAX_LINEAGES, LineageStamp
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.dispute import DisputeRecord
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState
from cheeseless.ports import dev_observe
from cheeseless.ports.store import StoreError

DIGEST = "a" * 64
BASE = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


@dataclass
class FakeListing:
    states: dict[str, DevState]
    stamps: tuple[LineageStamp, ...]
    attempts: tuple[ReferenceAttempt, ...] = ()
    error: Exception | None = None

    def lineage_stamps(self) -> tuple[LineageStamp, ...]:
        return self.stamps

    def dev_state(self, lineage_id: str) -> DevState | None:
        if self.error is not None:
            raise self.error
        return self.states.get(lineage_id)

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        return tuple(a for a in self.attempts if lineage_id in (None, a.lineage_id))

    def disputes(self) -> tuple[DisputeRecord, ...]:
        return ()


def _state(lineage: str, phase: DevPhase = DevPhase.SPEC) -> DevState:
    return DevState(lineage_id=lineage, phase=phase, spec_digest=DIGEST)


def _listing(*lineages: str, phase: DevPhase = DevPhase.SPEC) -> FakeListing:
    stamps = tuple(
        LineageStamp(name, BASE + timedelta(minutes=i)) for i, name in enumerate(lineages)
    )
    return FakeListing({name: _state(name, phase) for name in lineages}, stamps)


def test_the_listing_port_adds_one_read_method_and_nothing_else() -> None:
    names = {n for n in dir(dev_observe.LineageListing) if not n.startswith("_")}

    assert names == {"dev_state", "reference_attempts", "disputes", "lineage_stamps"}


def test_an_empty_store_lists_nothing() -> None:
    assert list_lineages(FakeListing({}, ())) == ()


def test_newest_change_comes_first() -> None:
    entries = list_lineages(_listing("old", "mid", "new"))

    assert [e.lineage_id for e in entries] == ["new", "mid", "old"]
    assert entries[0].updated_at == BASE + timedelta(minutes=2)
    assert all(e.stage is Stage.SPEC for e in entries)


def test_the_listing_is_capped_at_the_newest_hundred() -> None:
    names = [f"l{i:03d}" for i in range(MAX_LINEAGES + 5)]

    entries = list_lineages(_listing(*names))

    assert len(entries) == MAX_LINEAGES
    assert entries[0].lineage_id == names[-1]
    assert names[0] not in {e.lineage_id for e in entries}


def test_a_lineage_whose_state_vanished_is_skipped_and_does_not_use_a_slot() -> None:
    names = [f"l{i:03d}" for i in range(MAX_LINEAGES + 1)]
    port = _listing(*names)
    del port.states[names[-1]]

    entries = list_lineages(port)

    assert len(entries) == MAX_LINEAGES
    assert names[-1] not in {e.lineage_id for e in entries}
    assert entries[-1].lineage_id == names[0]


def test_stage_comes_from_the_shared_view_including_the_reference_attempt() -> None:
    port = _listing("demo", phase=DevPhase.TESTS)
    port.attempts = (
        ReferenceAttempt(
            "b" * 32,
            "1" * 32,
            1,
            DIGEST,
            ReferenceMode.TUI,
            False,
            ReferenceState.RUNNING,
            "demo",
            None,
            None,
        ),
    )

    (entry,) = list_lineages(port)

    assert entry.stage is Stage.REFERENCE


def test_a_store_error_propagates() -> None:
    port = _listing("demo")
    port.error = StoreError("corrupt")

    with pytest.raises(StoreError):
        list_lineages(port)


def test_a_state_outside_the_schema_bounds_is_a_store_error() -> None:
    port = _listing("demo")
    object.__setattr__(port.states["demo"], "version", -1)

    with pytest.raises(StoreError):
        list_lineages(port)
