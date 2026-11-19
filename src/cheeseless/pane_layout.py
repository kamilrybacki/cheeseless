"""Where the role pane sockets live and what ``cheeseless dev`` says about them (ADR 0019).

A pane is a host-side relay on a unix socket. T3 project scripts attach a terminal to it with
``cheeseless pane-relay --role R --lineage L``, so the path must be computable from the
lineage and the role alone. The layout is ``<state>/cheeseless/panes/<lineage>/<role>.sock``:
inside the state dir, never in a sandbox mount, and carrying no digest, session id or test
name, only the lineage id the operator typed and a role from a closed set.

This is composition-level code (it touches the filesystem to resolve paths), so it lives
outside ``domain``. Neither the MCP nor the UDS adapter may import it.
"""

import json
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from cheeseless.domain.ids import lineage_id

PANES_SCHEMA = 1
# ``sockaddr_un.sun_path`` is 108 bytes on Linux, one of them the NUL; fail with a clear error.
_MAX_SOCKET_PATH = 107


class PaneLayoutError(ValueError):
    """A role pane path cannot be built safely."""


class PaneRole(StrEnum):
    TEST_WRITER = "test-writer"
    REFERENCE = "reference"
    CODER = "coder"


class PaneMode(StrEnum):
    VIEW = "view"
    INTERACTIVE = "interactive"


_MODES = {
    PaneRole.TEST_WRITER: PaneMode.INTERACTIVE,
    PaneRole.REFERENCE: PaneMode.VIEW,
    PaneRole.CODER: PaneMode.VIEW,
}


def mode_of(role: PaneRole) -> PaneMode:
    """The one place that says which panes accept input (ADR 0019)."""
    return _MODES[role]


def socket_path(state_home: Path, lineage: str, role: PaneRole) -> Path:
    """The relay socket of ``role`` in ``lineage``, validated to stay inside ``state_home``."""
    lineage_id(lineage)
    role = PaneRole(role)
    path = state_home / "cheeseless" / "panes" / lineage / f"{role.value}.sock"
    if not path.resolve().is_relative_to(state_home.resolve()):
        raise PaneLayoutError("the pane socket would be outside the state dir")
    if len(str(path).encode()) > _MAX_SOCKET_PATH:
        raise PaneLayoutError(
            f"the pane socket path is too long for a unix socket ({len(str(path))} bytes); "
            "use a shorter --state-dir"
        )
    return path


def panes_event_line(state_home: Path, lineage: str, roles: Sequence[PaneRole]) -> str:
    """One closed JSON line: ``{event, schema, lineage, panes: [{role, mode, socket}]}``."""
    panes = [
        {
            "role": role.value,
            "mode": mode_of(role).value,
            "socket": str(socket_path(state_home, lineage, role)),
        }
        for role in roles
    ]
    event = {"event": "panes", "schema": PANES_SCHEMA, "lineage": lineage, "panes": panes}
    return json.dumps(event, sort_keys=True, separators=(",", ":"))
