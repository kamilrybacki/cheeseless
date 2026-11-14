"""Pure egress policy: hosts, methods, identities and server-side tools (Q48).

Everything here is exact and fail closed: the host must equal a normalised
allowlist entry (no wildcard, no suffix match), an unknown tool schema is
rejected, and an unparseable request to a model host is never forwarded.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cheeseless.domain import ids
from cheeseless.domain.errors import ErrorCode

SERVER_TOOL_MARKERS = (
    "web_search",
    "web_fetch",
    "websearch",
    "webfetch",
    "code_execution",
    "code_interpreter",
    "computer_use",
    "computer",
    "bash",
    "text_editor",
    "file_search",
    "mcp",
)
SAFE_TOOL_TYPES = frozenset({"", "function", "custom"})
SERVER_TOOL_KEYS = frozenset(
    {
        "mcp_servers",
        "mcpServers",
        "server_tools",
        "builtin_tools",
        "built_in_tools",
    }
)
UNINSPECTABLE_REQUEST_HEADERS = ("content-encoding", "transfer-encoding")
ALLOWLISTED_RULE = "allowlisted-host"
MODEL_RULE = "model-request"
REGISTRY_RULE = "registry-get"
ALLOW_RULES = frozenset({ALLOWLISTED_RULE, MODEL_RULE, REGISTRY_RULE})


class PolicyError(ValueError):
    """The policy configuration itself is invalid."""


@dataclass(frozen=True, slots=True)
class Decision:
    """``None`` means allowed; otherwise a catalogue error plus an audit rule."""

    code: ErrorCode
    rule: str


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allowed_hosts: frozenset[str]
    model_hosts: frozenset[str] = frozenset()
    registry_hosts: frozenset[str] = frozenset()
    denied_markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for host in (*self.allowed_hosts, *self.model_hosts, *self.registry_hosts):
            normalize_host(host)
        if not self.model_hosts <= self.allowed_hosts:
            raise PolicyError("model hosts must be on the allowlist")
        if not self.registry_hosts <= self.allowed_hosts:
            raise PolicyError("registry hosts must be on the allowlist")
        for marker in self.denied_markers:
            if not marker or not marker.isprintable() or len(marker) > 128:
                raise PolicyError(f"invalid denied marker: {marker!r}")

    @classmethod
    def from_config(cls, data: Mapping[str, Any]) -> "EgressPolicy":
        def host_set(key: str) -> frozenset[str]:
            raw = data.get(key, [])
            if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
                raise PolicyError(f"{key} must be a list of hosts")
            return frozenset(normalize_host(v) for v in raw)

        markers = data.get("denied_markers", [])
        if not isinstance(markers, list) or not all(isinstance(v, str) for v in markers):
            raise PolicyError("denied_markers must be a list of strings")
        return cls(
            allowed_hosts=host_set("allowed_hosts"),
            model_hosts=host_set("model_hosts"),
            registry_hosts=host_set("registry_hosts"),
            denied_markers=tuple(v.strip().lower() for v in markers if v.strip()),
        )

    def check_connect(self, host: str) -> Decision | None:
        try:
            normalized = normalize_host(host)
        except ValueError:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "invalid-host")
        if normalized not in self.allowed_hosts:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "host-not-allowed")
        for marker in self.denied_markers:
            if marker in normalized:
                return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "own-identity")
        return None

    def allow_rule(self, host: str) -> str:
        """The policy branch that admits an already-normalised host (audit rule)."""
        if host in self.model_hosts:
            return MODEL_RULE
        if host in self.registry_hosts:
            return REGISTRY_RULE
        return ALLOWLISTED_RULE

    def check_request(
        self, method: str, host: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> Decision | None:
        try:
            normalized = normalize_host(host)
        except ValueError:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "invalid-host")
        if normalized not in self.allowed_hosts:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "host-not-allowed")
        lowered_headers = {k.lower(): v for k, v in headers.items()}
        if "upgrade" in lowered_headers:
            return Decision(ErrorCode.DLP_BLOCKED, "protocol-upgrade")
        for name in UNINSPECTABLE_REQUEST_HEADERS:
            value = lowered_headers.get(name, "").strip().lower()
            if name == "transfer-encoding" and value in ("", "identity"):
                continue
            if name == "content-encoding" and value in ("", "identity"):
                continue
            return Decision(ErrorCode.DLP_BLOCKED, "encoded-request")
        target = url.lower()
        for marker in self.denied_markers:
            if marker in target or marker in normalized:
                return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "own-identity")
        if normalized in self.registry_hosts:
            if method != "GET":
                return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "registry-method")
            return None
        if normalized in self.model_hosts:
            return check_model_request(body)
        return None

    def check_response(
        self, host: str, headers: Mapping[str, str], body: bytes = b""
    ) -> Decision | None:
        del body
        try:
            normalized = normalize_host(host)
        except ValueError:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "invalid-host")
        if normalized not in self.allowed_hosts:
            return Decision(ErrorCode.EGRESS_DOMAIN_DENIED, "host-not-allowed")
        lowered = {k.lower(): v.strip().lower() for k, v in headers.items()}
        if lowered.get("upgrade"):
            return Decision(ErrorCode.DLP_BLOCKED, "protocol-upgrade")
        encoding = lowered.get("content-encoding", "")
        if encoding and encoding != "identity":
            return Decision(ErrorCode.DLP_BLOCKED, "encoded-response")
        return None


def normalize_host(raw: str) -> str:
    """Exact host normalisation: lower case, one trailing dot removed, IDNA."""
    if not isinstance(raw, str):
        raise ValueError("host must be a string")
    value = raw.strip().lower()
    if value.endswith("."):
        value = value[:-1]
    if not value:
        raise ValueError(f"invalid host: {raw!r}")
    try:
        ascii_host = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid host: {raw!r}") from exc
    return ids.host(ascii_host)


def _tool_denied(item: object) -> bool:
    if not isinstance(item, Mapping):
        return True
    kind = item.get("type", "")
    if not isinstance(kind, str):
        return True
    lowered = kind.lower()
    if lowered in SAFE_TOOL_TYPES:
        return False
    return True


def check_model_request(body: bytes) -> Decision | None:
    """Reject model requests that ask the provider to run a server-side tool."""
    if not body:
        return None
    try:
        data: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "unparseable-model-request")
    if not isinstance(data, Mapping):
        return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "unparseable-model-request")
    for key in data:
        if isinstance(key, str) and key in SERVER_TOOL_KEYS:
            value = data[key]
            if value not in (None, [], {}):
                return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
    tools = data.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
        for item in tools:
            if _tool_denied(item):
                return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
        for item in tools:
            if not isinstance(item, Mapping):
                return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
            kind = item.get("type", "")
            if isinstance(kind, str) and any(
                marker in kind.lower() for marker in SERVER_TOOL_MARKERS
            ):
                return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
    for key in ("tool_choice", "tools_choice"):
        choice = data.get(key)
        if isinstance(choice, Mapping):
            kind = choice.get("type", "")
            if not isinstance(kind, str) or kind.lower() not in SAFE_TOOL_TYPES:
                return Decision(ErrorCode.EGRESS_SERVER_TOOL_DENIED, "server-tool-schema")
    return None


__all__ = [
    "ALLOWLISTED_RULE",
    "ALLOW_RULES",
    "MODEL_RULE",
    "REGISTRY_RULE",
    "Decision",
    "EgressPolicy",
    "PolicyError",
    "check_model_request",
    "normalize_host",
]
