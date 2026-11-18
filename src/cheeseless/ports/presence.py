"""Proof that an operator is present for an attended decision (ADR 0017)."""

from typing import Protocol

from cheeseless.domain.presence import PresenceConfirmation, PresenceRequest


class PresenceProof(Protocol):
    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        """Ask the operator to confirm ``request``; one attempt only.

        Returns a fresh confirmation bound to ``request``. Raises ``PresenceRefused`` on any
        failure: no terminal, a wrong or late answer, end of input, an interrupt or an
        I/O error. There is no configuration that makes this succeed without asking."""
        ...
