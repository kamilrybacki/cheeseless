"""Authoring records in the trusted store (ADR 0009, stages d and f)."""

from contextlib import AbstractContextManager
from typing import Protocol

from cheeseless.domain.authoring import AuthoringState, CrosscheckRecord, Proposal


class AuthoringStore(Protocol):
    def authoring_state(self, lineage_id: str, spec_digest: str) -> AuthoringState:
        """Stored state, reset when the spec changed since it was saved."""
        ...

    def save_authoring_state(self, state: AuthoringState) -> AuthoringState:
        """Compare-and-set: refused (``AuthoringError``) unless ``state.version`` is current.

        Returns the stored state with its new version."""
        ...

    def approval_ownership(self, lineage_id: str) -> AbstractContextManager[None]:
        """Exclusive, non-blocking ownership of a lineage's approval.

        Held by an approver for its whole run and released by the OS if it dies. Raises
        ``AuthoringError`` at once if another owner holds it."""
        ...

    def save_proposal(self, proposal: Proposal) -> None: ...

    def load_proposal(self, proposal_id: str) -> Proposal | None: ...

    def save_reference(
        self, lineage_id: str, spec_digest: str, tree: str, *, expected_version: int
    ) -> None:
        """Bind a reference snapshot tree to the spec it was built from.

        Conditional: refused (``AuthoringError``) unless the lifecycle is still at
        ``expected_version``, checked atomically with the write."""
        ...

    def load_reference(self, lineage_id: str) -> tuple[str, str] | None:
        """``(spec_digest, tree)`` of the latest snapshot, or ``None``."""
        ...

    def save_crosscheck(
        self, proposal_id: str, lineage_id: str, record: CrosscheckRecord, *, expected_version: int
    ) -> None:
        """Conditional on the lifecycle version, like ``save_reference``."""
        ...

    def load_crosscheck(self, proposal_id: str) -> CrosscheckRecord | None: ...
