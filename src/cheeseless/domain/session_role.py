"""Session roles: the host binds one at launch; mounts and tools follow from it (stage (c))."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SessionRole(StrEnum):
    AUTHOR = "author"
    REFERENCE = "reference"
    IMPLEMENTER = "implementer"


def parse_role(value: str) -> SessionRole:
    try:
        return SessionRole(value)
    except ValueError:
        raise ValueError(f"unknown session role: {value!r}") from None


@dataclass(frozen=True)
class SessionBinding:
    """What the host recorded for one launched session; the agent cannot write it."""

    task_id: str
    role: SessionRole
    generation: int


# The only files of acceptance/ a reference session may see (ADR 0009: the spec and the criteria
# examples, never a test). Paths are relative to ``acceptance/``.
REFERENCE_ALLOWLIST: tuple[str, ...] = ("contract/spec.md", "contract/criteria.yaml")


def acceptance_mount(role: SessionRole) -> str:
    """How ``acceptance/`` is mounted: read-only, writable, or tests masked (contract kept)."""
    if role is SessionRole.AUTHOR:
        return "rw"
    if role is SessionRole.REFERENCE:
        return "contract"
    return "ro"


def tool_names(role: SessionRole, *, disputes: bool = False) -> tuple[str, ...]:
    """The agent-visible MCP tool names for a role."""
    if role is SessionRole.AUTHOR:
        return ("author_check", "author_crosscheck", "freeze_propose")
    if role is SessionRole.REFERENCE:
        return ()
    base = ("gate_run", "gate_status")
    return (*base, "gate_dispute") if disputes else base
