"""Dispute channel rules (ADR 0009, stage g).

A dispute is an implementer's claim that a public criterion cannot be met as written. It
ends the session in a ``DISPUTED`` session state kept beside results; it is never a
``Result`` status, so the v1 Result encoding is untouched. Nothing here may carry hidden
data, and the free-text note is untrusted: only host operators read it.
"""

from __future__ import annotations

import enum
import unicodedata
from dataclasses import dataclass

from cheeseless.domain.errors import ErrorCode

NOTE_LIMIT = 500
DEFAULT_CAP = 3
MAX_CAP = 100

# One fixed acknowledgement for every accepted dispute. It carries no id, count or time.
ACK_TEXT = "Dispute recorded. This session is closed."


class DisputeCode(enum.Enum):
    CONTRADICTS_SPEC = "contradicts_spec"
    UNTESTABLE = "untestable"
    AMBIGUOUS_SPEC = "ambiguous_spec"
    OTHER = "other"


class DisputeStatus(enum.Enum):
    OPEN = "open"
    UPHELD = "upheld"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DisputePolicy:
    """Trusted, host-set policy. The default is off, with a per-lineage cap of three."""

    enabled: bool = False
    cap: int = DEFAULT_CAP

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("dispute.enabled must be a boolean")
        if (
            isinstance(self.cap, bool)
            or not isinstance(self.cap, int)
            or not 1 <= self.cap <= MAX_CAP
        ):
            raise ValueError(f"dispute.cap must be an integer from 1 to {MAX_CAP}")


@dataclass(frozen=True, slots=True)
class DisputeRecord:
    dispute_id: str
    task_id: str
    lineage_id: str
    criterion_id: str
    code: DisputeCode
    note: str
    status: DisputeStatus = DisputeStatus.OPEN
    resolved_by: str = ""

    @property
    def blocks_session(self) -> bool:
        """Open and upheld disputes keep the task closed; a rejection reopens it."""
        return self.status is not DisputeStatus.REJECTED


def check_note(note: object) -> str:
    """Bounded text with no control, format, surrogate or private-use characters."""
    if not isinstance(note, str) or len(note) > NOTE_LIMIT:
        raise ValueError(f"note must be text of at most {NOTE_LIMIT} characters")
    if any(unicodedata.category(ch).startswith("C") for ch in note):
        raise ValueError("note may not contain control characters")
    return note


def refusal(
    policy: DisputePolicy, *, lineage_count: int, holds_pass: bool, blocked: bool
) -> ErrorCode | None:
    """First reason a dispute must be refused; ``None`` when it may be recorded."""
    if not policy.enabled:
        return ErrorCode.DISPUTE_DISABLED
    if lineage_count >= policy.cap:
        return ErrorCode.DISPUTE_CAP_REACHED
    if holds_pass:
        return ErrorCode.DISPUTE_NOT_ALLOWED
    if blocked:
        return ErrorCode.SESSION_DISPUTED
    return None


__all__ = [
    "ACK_TEXT",
    "DEFAULT_CAP",
    "MAX_CAP",
    "NOTE_LIMIT",
    "DisputeCode",
    "DisputePolicy",
    "DisputeRecord",
    "DisputeStatus",
    "check_note",
    "refusal",
]
