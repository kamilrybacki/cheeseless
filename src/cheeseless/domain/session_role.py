"""Session roles: the host binds one at launch; mounts and tools follow from it (stage (c))."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SessionRole(StrEnum):
    AUTHOR = "author"
    REFERENCE = "reference"
    IMPLEMENTER = "implementer"
    # ADR 0015: reads the author's tests to judge them, and can change nothing. It has no
    # tools and no writable tree, because a reviewer that could act would not be a reader.
    REVIEWER = "reviewer"


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
    # The model the host told the agent to use. Recorded by the host, not reported by the
    # agent, so a verdict can name the vendor that actually ran (ledger: vendor identity).
    model: str = ""


# The only files of acceptance/ a reference session may see (ADR 0009: the spec and the criteria
# examples, never a test). Paths are relative to ``acceptance/``.
REFERENCE_ALLOWLIST: tuple[str, ...] = ("contract/spec.md", "contract/criteria.yaml")


def acceptance_mount(role: SessionRole) -> str:
    """How ``acceptance/`` is mounted: read-only, writable, or tests masked (contract kept)."""
    if role is SessionRole.AUTHOR:
        return "rw"
    if role is SessionRole.REFERENCE:
        return "contract"
    # The implementer and the reviewer both read it without writing it. They differ in
    # everything else, not in this.
    return "ro"


def tool_names(role: SessionRole, *, disputes: bool = False, dev: bool = False) -> tuple[str, ...]:
    """The agent-visible MCP tool names for a role.

    ``spec_done`` exists only in a host-started ``cheeseless dev`` author session and
    ``reference_done`` only in a dev TUI reference session (ADR 0014)."""
    if role is SessionRole.AUTHOR:
        author: tuple[str, ...] = ("author_check", "author_crosscheck", "freeze_propose")
        return (*author, "spec_done") if dev else author
    if role is SessionRole.REFERENCE:
        return ("reference_done",) if dev else ()
    if role is SessionRole.REVIEWER:
        # ADR 0015: never any tool, whatever the flags say. Its verdict is the host's to
        # record, after the session ends, from what this session produced.
        return ()
    base = ("gate_run", "gate_status")
    return (*base, "gate_dispute") if disputes else base


SPEC_DONE_COMMAND = ".claude/commands/spec-done.md"
_SPEC_DONE_TEXT = b"""---
description: Close the spec phase of this cheeseless dev session
---
Call this only once the user has agreed to the spec in spec.md. Then call the MCP tool
spec_done. It binds this session to the spec as it is now in the worktree and moves the
session to the tests phase. Write the acceptance tests only after it succeeds.
"""


def dev_home_entries(role: SessionRole, *, dev: bool) -> tuple[tuple[str, bytes], ...]:
    """Files seeded into the agent's HOME for a dev session: only a dev author gets any."""
    if role is SessionRole.AUTHOR and dev:
        return ((SPEC_DONE_COMMAND, _SPEC_DONE_TEXT),)
    return ()
