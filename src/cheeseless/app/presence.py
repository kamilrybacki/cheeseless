"""The one place a use case obtains operator presence (ADR 0017).

Every attended decision calls :func:`require_presence` itself, so no inbound route (CLI
today, anything later) can reach the decision without it. A missing port refuses; there is
no flag, environment variable or configuration that turns the check off.
"""

from __future__ import annotations

import threading

from cheeseless.domain.presence import PresenceConfirmation, PresenceRefused, PresenceRequest
from cheeseless.ports.presence import PresenceProof

_SPENT: set[str] = set()
_SPENT_LOCK = threading.Lock()


def require_presence(proof: PresenceProof | None, request: PresenceRequest) -> PresenceConfirmation:
    """Return a fresh confirmation for exactly ``request`` or raise ``PresenceRefused``."""
    if proof is None:
        raise PresenceRefused("no presence check is available on this route")
    confirmation = proof.confirm(request)
    if not isinstance(confirmation, PresenceConfirmation):
        raise PresenceRefused("the presence check returned something that is not a confirmation")
    if confirmation.request != request:
        raise PresenceRefused("the confirmation answers a different decision")
    with _SPENT_LOCK:
        if confirmation.nonce in _SPENT:
            raise PresenceRefused("the confirmation was already used")
        _SPENT.add(confirmation.nonce)
    return confirmation


def presence_note(confirmation: PresenceConfirmation) -> str:
    """The audit marker: which check ran, as a declared signal (not proof of identity)."""
    return f"presence: {confirmation.signal}"


__all__ = ["presence_note", "require_presence"]
