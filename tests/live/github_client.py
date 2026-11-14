"""Small GitHub REST adapter for the live release-gate suite.

Tokens travel as :class:`Token`, whose ``repr``/``str`` never show the value, so a pytest
traceback that prints frame arguments cannot leak one. The value is unwrapped only on the
line that builds the ``Authorization`` header. Errors carry an HTTP status or an exception
class name, never a request header or a body.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
CANARY_PREFIX = "cheeseless-canary-"
MAX_BODY = 1_000_000
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ClientError(Exception):
    """A transport failure or a refused call. The message never holds a token or a body."""


class Token:
    """A credential that redacts itself everywhere except :meth:`reveal`."""

    __slots__ = ("_value", "label")

    def __init__(self, value: str, label: str) -> None:
        self._value = value
        self.label = label

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Token({self.label}, <redacted>)"

    __str__ = __repr__


@dataclass(frozen=True)
class Response:
    status: int
    body: object = None

    def meta(self) -> str:
        """HTTP status plus body metadata (message and key names) only, never values."""
        parts = [f"HTTP {self.status}"]
        if isinstance(self.body, dict):
            message = self.body.get("message")
            if isinstance(message, str):
                parts.append(f"message={message[:160]!r}")
            parts.append(f"keys={sorted(str(k) for k in self.body)[:12]}")
        elif isinstance(self.body, list):
            parts.append(f"items={len(self.body)}")
        return " ".join(parts)

    def get(self, key: str) -> object:
        return self.body.get(key) if isinstance(self.body, dict) else None


class GitHubClient(Protocol):
    def request(
        self, method: str, path: str, *, token: Token | None = None, json: object = None
    ) -> Response: ...


def guard_mutation(method: str, path: str) -> None:
    """Refuse a write to any repository that is not a disposable canary."""
    if method not in _MUTATING:
        return
    parts = path.strip("/").split("/")
    if parts[0] == "repos" and len(parts) >= 3 and parts[2].startswith(CANARY_PREFIX):
        return
    if parts[0] in {"app", "app-manifests"} or parts[:2] == ["installation", "token"]:
        return
    raise ClientError(f"refusing a {method} outside a {CANARY_PREFIX}* repository")


class UrllibGitHubClient:
    def __init__(self, *, api_url: str = API_URL, timeout: float = 30.0) -> None:
        if not api_url.startswith("https://"):
            raise ValueError("the GitHub API URL must be https")
        self._api = api_url.rstrip("/")
        self._timeout = timeout

    def request(
        self, method: str, path: str, *, token: Token | None = None, json: object = None
    ) -> Response:
        guard_mutation(method, path)
        data = None if json is None else _dumps(json)
        req = urllib.request.Request(  # noqa: S310 - https enforced in __init__
            f"{self._api}/{path.lstrip('/')}", data=data, method=method
        )
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("User-Agent", "cheeseless-live-gate")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token.reveal()}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            status, raw = _send(req, self._timeout)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            raise ClientError(f"request failed: {type(err).__name__}") from None
        return Response(status, _decode(raw))


def _dumps(value: object) -> bytes:
    return json.dumps(value).encode()


def _send(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), resp.read(MAX_BODY)
    except urllib.error.HTTPError as err:
        with err:
            return int(err.code), err.read(MAX_BODY)


def _decode(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
