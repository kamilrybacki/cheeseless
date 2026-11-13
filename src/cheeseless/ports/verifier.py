"""Verifier client port used by the MCP adapter (no adapter-to-adapter deps)."""

from typing import Protocol

from cheeseless.domain.disclosure import AgentView


class VerifierClient(Protocol):
    def run(self) -> AgentView: ...

    def status(self) -> AgentView: ...
