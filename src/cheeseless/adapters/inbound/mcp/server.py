"""MCP adapter exposing ``gate_run`` and ``gate_status`` over a verifier client.

``gate_dispute`` is listed only when the host enabled disputes for the session, so the
default agent-visible tool set stays ``gate_run`` and ``gate_status``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from cheeseless.adapters.inbound.mapping import agent_view_to_dict, gate_error_to_dict
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.dispute import ACK_TEXT, NOTE_LIMIT, DisputeCode
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.domain.session_role import SessionRole
from cheeseless.ports.verifier import VerifierClient


def build_mcp_server(
    client: VerifierClient,
    *,
    name: str = "cheeseless",
    disputes: bool = False,
    author: bool = False,
    role: SessionRole = SessionRole.IMPLEMENTER,
) -> MCPServer:
    server = MCPServer(name)
    if role is SessionRole.AUTHOR:
        _register_author(server, client)
        return server
    if role is SessionRole.REFERENCE:
        return server  # the reference snapshot is host-recorded: no tools

    def _view_payload(view: AgentView) -> dict[str, Any]:
        return agent_view_to_dict(view)

    def _call_result(view: AgentView) -> CallToolResult:
        payload = _view_payload(view)
        text = json.dumps(payload, sort_keys=True)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=payload,
            is_error=False,
        )

    @server.tool(
        name="gate_run",
        description="Run the cheeseless gate against the configured candidate.",
    )
    def gate_run_tool() -> CallToolResult:
        try:
            view = client.run()
        except Exception:  # noqa: BLE001
            view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
        return _call_result(view)

    @server.tool(name="gate_status", description="Read the latest verified cheeseless result.")
    def gate_status_tool() -> CallToolResult:
        try:
            view = client.status()
        except Exception:  # noqa: BLE001
            view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
        return _call_result(view)

    if disputes:
        _register_dispute(server, client)
    if author:  # legacy additive switch; role-bound author sessions use role=AUTHOR
        _register_author(server, client)
    return server


_CODE_LIST = ", ".join(c.value for c in DisputeCode)


def _register_dispute(server: MCPServer, client: VerifierClient) -> None:
    @server.tool(
        name="gate_dispute",
        description=(
            "Dispute a public criterion. `code` must be exactly one of: "
            f"{_CODE_LIST}. This ends the session: no further runs are possible. "
            f"The note is at most {NOTE_LIMIT} characters of plain text."
        ),
    )
    def gate_dispute_tool(criterion_id: str, code: str, note: str) -> CallToolResult:
        try:
            outcome: str | GateError = client.dispute(criterion_id, DisputeCode(code), note)
        except ValueError:
            outcome = gate_error(ErrorCode.DISPUTE_INVALID)
        except Exception:  # noqa: BLE001
            outcome = gate_error(ErrorCode.INFRA_ERROR)
        if isinstance(outcome, GateError):
            body: dict[str, Any] = {
                "outcome": {"kind": "error", "error": gate_error_to_dict(outcome)}
            }
        else:
            body = {"outcome": {"kind": "ack", "message": ACK_TEXT}}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(body, sort_keys=True))],
            structured_content=body,
            is_error=False,
        )


def _register_author(server: MCPServer, client: VerifierClient) -> None:
    """Author-session tools. There is deliberately no approve, reference or retry tool."""

    def _result(kind: str, call: Callable[[], object]) -> CallToolResult:
        try:
            outcome = call()
        except Exception:  # noqa: BLE001
            outcome = gate_error(ErrorCode.INFRA_ERROR)
        if isinstance(outcome, GateError):
            body: dict[str, Any] = {
                "outcome": {"kind": "error", "error": gate_error_to_dict(outcome)}
            }
        else:
            body = {"outcome": {"kind": kind, **dict(outcome)}}  # type: ignore[call-overload]
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(body, sort_keys=True))],
            structured_content=body,
            is_error=False,
        )

    @server.tool(
        name="author_check",
        description="Check the acceptance tree of this author session (deterministic).",
    )
    def author_check_tool() -> CallToolResult:
        return _result("check", client.author_check)

    @server.tool(
        name="author_crosscheck",
        description="Record the crosscheck of the acceptance tree against the reference snapshot.",
    )
    def author_crosscheck_tool() -> CallToolResult:
        return _result("crosscheck", client.author_crosscheck)

    @server.tool(
        name="freeze_propose",
        description="Propose the acceptance tree for freezing. A human or host approves it.",
    )
    def freeze_propose_tool() -> CallToolResult:
        return _result("propose", client.freeze_propose)


def run_stdio(server: MCPServer) -> None:
    server.run("stdio")
