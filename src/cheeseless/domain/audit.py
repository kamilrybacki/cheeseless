"""Bounded, non-secret metadata for the per-request egress allow audit (P11-11).

An event describes *that* an allowed request passed policy and inspection, never what
it contained. There is deliberately no field for a URL path, query, fragment, userinfo,
header, body, token or arbitrary exception text, so none can be recorded by accident.
Every free-form value has a closed shape and an explicit length bound. The method is a
member of the canonical HTTP method set (anything else becomes ``OTHER``), and the rule
is a member of the policy's closed allow-rule set; request text is never trusted as is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cheeseless.domain import ids
from cheeseless.domain.egress_policy import ALLOW_RULES

AUDIT_SCHEMA = "cheeseless.egress.allow.v1"
REQUEST_ADMITTED = "request_admitted"
RESPONSE_CLEAN = "response_clean"
STAGES = frozenset({REQUEST_ADMITTED, RESPONSE_CLEAN})
ALLOW = "allow"
KNOWN_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
OTHER_METHOD = "OTHER"
METHODS = KNOWN_METHODS | {OTHER_METHOD}

_REQUEST_ID = re.compile(r"[0-9a-f]{16,64}")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One safe allow-audit record. Bounded fields only; no content can be passed in."""

    request_id: str
    stage: str
    host: str
    method: str
    rule: str
    decision: str = ALLOW

    def __post_init__(self) -> None:
        if _REQUEST_ID.fullmatch(self.request_id) is None:
            raise ValueError("invalid audit request id")
        if self.stage not in STAGES:
            raise ValueError("invalid audit stage")
        ids.host(self.host)
        if self.method not in METHODS:
            raise ValueError("invalid audit method")
        if self.rule not in ALLOW_RULES:
            raise ValueError("invalid audit rule")
        if self.decision != ALLOW:
            raise ValueError("invalid audit decision")

    def to_record(self) -> dict[str, object]:
        return {
            "schema": AUDIT_SCHEMA,
            "request_id": self.request_id,
            "stage": self.stage,
            "host": self.host,
            "method": self.method,
            "decision": self.decision,
            "rule": self.rule,
        }


__all__ = [
    "ALLOW",
    "AUDIT_SCHEMA",
    "KNOWN_METHODS",
    "METHODS",
    "OTHER_METHOD",
    "REQUEST_ADMITTED",
    "RESPONSE_CLEAN",
    "STAGES",
    "AuditEvent",
]
