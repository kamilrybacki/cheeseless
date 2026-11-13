"""MCP adapter exposing ``gate_run`` and ``gate_status`` over a verifier client."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from cheeseless.adapters.inbound.mapping import agent_view_to_dict
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.errors import ErrorCode, gate_error
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

    return server


def run_stdio(server: MCPServer) -> None:
    server.run("stdio")
