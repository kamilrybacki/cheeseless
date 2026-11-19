"""Read-only port for the dev-events observer. It has no write methods, by design."""

from __future__ import annotations

from typing import Protocol

from cheeseless.domain.dev_lineages import LineageStamp
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.dispute import DisputeRecord
from cheeseless.domain.reference_attempt import ReferenceAttempt


class DevObservation(Protocol):
    """Everything the dev-events use case may read, and nothing it may change."""

    def dev_state(self, lineage_id: str) -> DevState | None: ...

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]: ...

    def disputes(self) -> tuple[DisputeRecord, ...]: ...


class LineageListing(DevObservation, Protocol):
    """The observer port plus one more read: which lineages have state, and when it changed."""

    def lineage_stamps(self) -> tuple[LineageStamp, ...]: ...
