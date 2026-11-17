"""Dev-mode reference attempts in the trusted store (ADR 0014)."""

from pathlib import Path
from typing import Protocol

from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceState,
    TransitionRefused,
)


class ReferenceAttemptStore(Protocol):
    def reference_attempt(self, attempt_id: str) -> ReferenceAttempt | None:
        """The stored attempt, or ``None`` if there is none."""
        ...

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        """Every stored attempt, or only those of one lineage."""
        ...

    def save_reference_attempt(self, attempt: ReferenceAttempt) -> ReferenceAttempt:
        """Create the record; refused (``StoreError``) if the attempt already exists."""
        ...

    def transition_reference_attempt(
        self,
        attempt_id: str,
        expected: ReferenceState,
        new: ReferenceState,
        *,
        owner: AttemptOwner | None = None,
        result_tree: str | None = None,
    ) -> ReferenceAttempt | TransitionRefused:
        """Compare-and-set under the store lock: move ``expected`` -> ``new`` or refuse.

        A current state other than ``expected``, no record, or (given ``owner``) a record of
        another session, generation or mode returns ``TransitionRefused`` and writes nothing.
        A move the state machine forbids raises ``ReferenceTransitionError``. ``result_tree``
        is stored atomically with the move and is allowed only when ``new`` is ``done``."""
        ...

    def bind_reference_attempt(
        self,
        attempt_id: str,
        *,
        owner: AttemptOwner,
        attached: bool | None = None,
        pane_id: str | None = None,
    ) -> ReferenceAttempt | None:
        """Record the relay attach flag and/or the pane id; never changes the state.

        ``None`` when the attempt is missing or belongs to another owner."""
        ...

    def reference_log_path(self, attempt_id: str) -> Path:
        """A host-only log path for a headless attempt; the caller creates it 0600."""
        ...
