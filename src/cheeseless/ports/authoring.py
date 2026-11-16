"""Authoring records in the trusted store (ADR 0009, stages d and f)."""

from typing import Protocol

from cheeseless.domain.authoring import AuthoringState, CrosscheckRecord, Proposal


class AuthoringStore(Protocol):
    def authoring_state(self, lineage_id: str, spec_digest: str) -> AuthoringState:
        """Stored state, reset when the spec changed since it was saved."""
        ...

    def save_authoring_state(self, state: AuthoringState) -> None: ...

    def save_proposal(self, proposal: Proposal) -> None: ...

    def load_proposal(self, proposal_id: str) -> Proposal | None: ...

    def save_reference(self, lineage_id: str, spec_digest: str, tree: str) -> None:
        """Bind a reference snapshot tree to the spec it was built from."""
        ...

    def load_reference(self, lineage_id: str) -> tuple[str, str] | None:
        """``(spec_digest, tree)`` of the latest snapshot, or ``None``."""
        ...

    def save_crosscheck(self, proposal_id: str, record: CrosscheckRecord) -> None: ...

    def load_crosscheck(self, proposal_id: str) -> CrosscheckRecord | None: ...
