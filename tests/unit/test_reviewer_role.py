"""The reviewer role: it reads the tests and can change nothing (ADR 0015).

The reviewer is the only role that sees the author's tests *and* must not be able to
influence them. Everything here is about keeping those two facts true at the same time.
"""

from __future__ import annotations

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.bootstrap import _mcp_shim_source
from cheeseless.domain.session_role import (
    SessionRole,
    acceptance_mount,
    parse_role,
    tool_names,
)


class _C:
    pass


def test_the_role_parses_from_its_name() -> None:
    assert parse_role("reviewer") is SessionRole.REVIEWER


def test_the_reviewer_reads_acceptance_read_only() -> None:
    """It must see the tests to review them, and must not be able to edit them."""
    assert acceptance_mount(SessionRole.REVIEWER) == "ro"


def test_the_reviewer_gets_no_tools_at_all() -> None:
    """A reviewer that could call any host operation would not be a reader."""
    assert tool_names(SessionRole.REVIEWER) == ()


def test_the_reviewer_gets_no_tools_even_with_disputes_or_dev() -> None:
    assert tool_names(SessionRole.REVIEWER, disputes=True) == ()
    assert tool_names(SessionRole.REVIEWER, dev=True) == ()


def test_the_reviewer_mcp_server_exposes_nothing() -> None:
    server = build_mcp_server(_C(), role=SessionRole.REVIEWER)
    assert [t.name for t in server._tool_manager.list_tools()] == []


def test_the_reviewer_shim_bakes_the_role() -> None:
    src = _mcp_shim_source("/py", object(), object(), "t1", role=SessionRole.REVIEWER)  # type: ignore[arg-type]
    assert "role='reviewer'" in src
    assert "dev=True" not in src


def test_the_reviewer_has_no_dev_command_files() -> None:
    from cheeseless.domain.session_role import dev_home_entries

    assert dev_home_entries(SessionRole.REVIEWER, dev=True) == ()
    assert dev_home_entries(SessionRole.REVIEWER, dev=False) == ()


@pytest.mark.parametrize("role", list(SessionRole))
def test_every_role_has_an_acceptance_mount(role: SessionRole) -> None:
    """A role added later must decide its mount, not inherit the implementer's by accident."""
    assert acceptance_mount(role) in ("ro", "rw", "contract")


@pytest.mark.parametrize("role", list(SessionRole))
def test_every_role_answers_the_tool_question(role: SessionRole) -> None:
    assert isinstance(tool_names(role), tuple)
