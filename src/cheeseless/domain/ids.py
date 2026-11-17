"""Shape checks shared by domain types. Values that reach the agent must be boring."""

import re

_CRITERION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_LINEAGE_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ATTEMPT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_GATE_SESSION_ID = re.compile(r"[0-9a-f]{32}")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


def _check(pattern: re.Pattern[str], value: object, what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def criterion_id(value: object) -> str:
    return _check(_CRITERION_ID, value, "criterion id")


def lineage_id(value: object) -> str:
    return _check(_LINEAGE_ID, value, "lineage id")


def sha256_hex(value: object, what: str = "digest") -> str:
    return _check(_SHA256, value, what)


def git_oid(value: object) -> str:
    return _check(_GIT_OID, value, "git object id")


def session_id(value: object) -> str:
    """An agent session id (a lowercase UUID, as Claude Code issues them)."""
    return _check(_SESSION_ID, value, "session id")


def attempt_id(value: object) -> str:
    """A dev reference attempt id; it names a file, so no dots or slashes."""
    return _check(_ATTEMPT_ID, value, "attempt id")


def gate_session_id(value: object) -> str:
    """A host-registered gate session id (``register_session``: 32 lowercase hex digits)."""
    return _check(_GATE_SESSION_ID, value, "gate session id")


def host(value: object) -> str:
    if isinstance(value, str) and len(value) > 253:
        raise ValueError("host too long")
    return _check(_HOST, value, "host")


def non_empty(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or not value.isprintable():
        raise ValueError(f"invalid {what}: {value!r}")
    return value
