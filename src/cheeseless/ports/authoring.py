"""Authoring records in the trusted store (ADR 0009, stages d and f)."""

from contextlib import AbstractContextManager
from typing import Protocol

from cheeseless.domain.authoring import (
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    StrengthReview,
)


class AuthoringStore(Protocol):
    def authoring_state(self, lineage_id: str, spec_digest: str) -> AuthoringState:
        """Stored state, reset when the spec changed since it was saved."""
        ...

    def save_authoring_state(self, state: AuthoringState) -> AuthoringState:
        """Compare-and-set: refused (``AuthoringError``) unless ``state.version`` is current.

        Returns the stored state with its new version."""
        ...

    def hold_open(self, lineage_id: str, expected_version: int) -> AbstractContextManager[None]:
        """Hold the store lock while the lifecycle is still at ``expected_version``.

        Every state transition (approval included) takes the same lock, so nothing can seal
        the lineage while the caller publishes. Raises ``AuthoringError`` if the version moved."""
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

    def save_author_check(
        self, lineage_id: str, spec_digest: str, tree: str, *, expected_version: int
    ) -> None:
        """Record an ``author_check`` PASS for an acceptance tree (ledger R15).

        Conditional on the lifecycle version, like ``save_reference``."""
        ...

    def load_author_check(self, lineage_id: str) -> tuple[str, str] | None:
        """``(spec_digest, acceptance_tree)`` of the latest PASS, or ``None``."""
        ...

    def save_crosscheck(
        self, proposal_id: str, lineage_id: str, record: CrosscheckRecord, *, expected_version: int
    ) -> None:
        """Conditional on the lifecycle version, like ``save_reference``."""
        ...

    def load_crosscheck(self, proposal_id: str) -> CrosscheckRecord | None: ...

    def save_strength_review(
        self, proposal_id: str, lineage_id: str, record: StrengthReview, *, expected_version: int
    ) -> None:
        """Record another vendor's review of a proposal's tests (ADR 0015).

        Written by the host only, after a reviewer session the host launched. Conditional on
        the lifecycle version, like ``save_reference``."""
        ...

    def load_strength_review(self, proposal_id: str) -> StrengthReview | None: ...

    def auto_freeze_enabled(self, lineage_id: str) -> bool:
        """Whether this project has opted in to unattended freeze (ledger: decision B).

        Off unless a record says otherwise: the default is the one an operator must change
        deliberately, and an absent record must never read as consent."""
        ...

    def set_auto_freeze(self, lineage_id: str, enabled: bool) -> None:
        """Record the project's choice. The host writes this; a proposal cannot."""
        ...
