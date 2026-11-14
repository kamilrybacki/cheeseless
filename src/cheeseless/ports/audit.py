"""Port for the operator-only egress allow audit."""

from typing import Protocol

from cheeseless.domain.audit import AuditEvent


class EgressAuditSink(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Persist one event. A failure makes the caller fail the request closed."""
        ...


__all__ = ["EgressAuditSink"]
