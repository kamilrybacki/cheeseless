"""``cheeseless dev`` phase state in the trusted store (ADR 0013)."""

from typing import Protocol

from cheeseless.domain.dev_session import DevState


class DevSessionStore(Protocol):
    def dev_state(self, lineage_id: str) -> DevState | None:
        """The stored session for a lineage, or ``None`` if none was ever opened."""
        ...

    def save_dev_state(self, state: DevState) -> DevState:
        """Compare-and-set: refused (``DevSessionError``) unless ``state.version`` is current.

        A lineage with no record is at version 0. Returns the state with its new version."""
        ...
