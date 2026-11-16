"""Verifier client port used by the MCP adapter (no adapter-to-adapter deps)."""

from typing import Protocol

from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.dispute import DisputeCode
from cheeseless.domain.errors import GateError


class VerifierClient(Protocol):
    def run(self) -> AgentView: ...

    def status(self) -> AgentView: ...

    def dispute(self, criterion_id: str, code: DisputeCode, note: str) -> str | GateError:
        """The fixed acknowledgement text, or a catalogued refusal."""
        ...
