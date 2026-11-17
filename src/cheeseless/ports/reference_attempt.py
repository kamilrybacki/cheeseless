"""Dev-mode reference attempts in the trusted store (ADR 0014)."""

from typing import Protocol

from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceState,
    TransitionRefused,
)


class ReferenceAttemptStore(Protocol):
    def reference_attempt(self, attempt_id: str) -> ReferenceAttempt | None:
        """The stored attempt, or ``None`` if there is none."""
        ...

    def save_reference_attempt(self, attempt: ReferenceAttempt) -> ReferenceAttempt:
        """Create the record; refused (``StoreError``) if the attempt already exists."""
        ...

    def transition_reference_attempt(
        self, attempt_id: str, expected: ReferenceState, new: ReferenceState
    ) -> ReferenceAttempt | TransitionRefused:
        """Compare-and-set under the store lock: move ``expected`` -> ``new`` or refuse.

        A current state other than ``expected`` (or no record) returns ``TransitionRefused``
        and writes nothing. A move the state machine forbids raises
        ``ReferenceTransitionError``."""
        ...
