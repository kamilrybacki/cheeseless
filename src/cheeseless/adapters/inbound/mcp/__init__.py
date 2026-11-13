"""MCP adapter exposing gate_run and gate_status."""

from .server import build_mcp_server, run_stdio

__all__ = ["build_mcp_server", "run_stdio"]
