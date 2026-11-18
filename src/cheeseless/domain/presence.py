"""Attended-decision presence challenge: what is asked and what counts as an answer (ADR 0017).

Five operator decisions need a fresh, single-use confirmation from a person at the
controlling terminal. The request binds the exact decision (operation, lineage, subject,
actor, outcome and any waiver), so an answer to one decision is not an answer to another.
What the audit records is a declared signal, ``presence: tty-challenge``: it says which
check ran, not that a particular human was there.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

PRESENCE_SIGNAL = "tty-challenge"
FIELD_LIMIT = 200


class PresenceOperation(enum.Enum):
    FREEZE_APPROVE = "freeze-approve"
    FREEZE_RECOVER = "freeze-recover"
    DISPUTE_RESOLVE = "dispute-resolve"
    PROMOTE_APPROVE = "promote-approve"
    AUTO_FREEZE_ENABLE = "auto-freeze-enable"


@dataclass(frozen=True, slots=True)
class PresenceRequest:
    """One operator decision, exactly as it will be recorded."""

    operation: PresenceOperation
    lineage_id: str
    subject: str
    actor: str
    outcome: str
    waiver: str = ""


@dataclass(frozen=True, slots=True)
class PresenceConfirmation:
    """A single-use answer to one :class:`PresenceRequest`, never a bare boolean."""

    request: PresenceRequest
    nonce: str
    signal: str = PRESENCE_SIGNAL

    def __post_init__(self) -> None:
        if not self.nonce:
            raise ValueError("a presence confirmation needs a nonce")


class PresenceRefused(Exception):
    """No valid presence confirmation was obtained. The decision must not be recorded."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"presence confirmation refused: {reason}")
        self.reason = reason


def _escape_char(ch: str) -> str:
    if ch.isprintable():
        return ch
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def escape_field(text: str, *, limit: int = FIELD_LIMIT) -> str:
    """Render untrusted text inert on a terminal: every non-printable becomes an escape.

    C0, DEL, C1, line breaks, tabs and format characters (bidi overrides included) are all
    shown as escapes, so a field cannot move the cursor, retitle the window or hide text."""
    shown = "".join(_escape_char(ch) for ch in text)
    if len(shown) > limit:
        return shown[: max(0, limit - 3)] + "..."
    return shown


def render_prompt(request: PresenceRequest) -> str:
    """The decision as the operator will see it. Never includes hidden-test content."""
    lines = [
        "cheeseless: confirm an operator decision at this terminal",
        f"  operation: {escape_field(request.operation.value)}",
        f"  lineage:   {escape_field(request.lineage_id)}",
        f"  subject:   {escape_field(request.subject)}",
        f"  actor:     {escape_field(request.actor)}",
        f"  outcome:   {escape_field(request.outcome)}",
    ]
    if request.waiver:
        lines.append(f"  waiver:    {escape_field(request.waiver)}")
    return "\n".join(lines) + "\n"


__all__ = [
    "FIELD_LIMIT",
    "PRESENCE_SIGNAL",
    "PresenceConfirmation",
    "PresenceOperation",
    "PresenceRefused",
    "PresenceRequest",
    "escape_field",
    "render_prompt",
]
