"""MCP adapter exposing ``gate_run`` and ``gate_status`` over a verifier client."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from cheeseless.adapters.inbound.mapping import agent_view_to_dict, gate_error_to_dict
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.dispute import ACK_TEXT, NOTE_LIMIT, DisputeCode
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.ports.verifier import VerifierClient


def build_mcp_server(client: VerifierClient, *, name: str = "cheeseless") -> MCPServer:
    server = MCPServer(name)

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

    @server.tool(
        name="gate_dispute",
        description=(
            "Dispute a public criterion as contradicting the spec, untestable, ambiguous or "
            "other. This ends the session: no further runs are possible. "
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

    return server


def run_stdio(server: MCPServer) -> None:
    server.run("stdio")
