"""Ledger transport: git objects and refs in the ledger repository via the GitHub API.

Transport only. It moves bytes and maps HTTP statuses; every trust decision (MACs,
chain shape, limits) is made by the domain codec and the counter. Error messages
never include the token or a response body.
"""

from __future__ import annotations

import base64
import binascii
import enum
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Protocol

from cheeseless.domain.budget_ledger import LedgerCommit, ledger_repo

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "cheeseless-ci-judge"
MAX_RESPONSE = 1 << 20
MAX_TREE_ENTRIES = 4
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9_.\-]{1,512}")

Send = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class TransportError(Exception):
    """Transient or ambiguous: the operation may or may not have been applied."""


class LedgerMalformed(TransportError):
    """The server answered with an unexpected shape. Not retried; the caller fails closed."""


class UpdateResult(enum.Enum):
    UPDATED = "updated"
    REJECTED = "rejected"


class LedgerTransport(Protocol):
    def get_ref(self, branch: str) -> str | None:
        """Tip of ``branch``, or ``None`` when the branch does not exist."""
        ...

    def get_commit(self, sha: str) -> LedgerCommit: ...

    def write_commit(self, *, parent: str, message: bytes, files: Mapping[str, bytes]) -> str:
        """Create blob, tree and commit objects (no ref change). Returns the commit SHA."""
        ...

    def update_ref(self, branch: str, sha: str) -> UpdateResult:
        """Non-force update. ``REJECTED`` when not a fast-forward; ``TransportError`` if unknown."""
        ...


class LedgerSeeder(LedgerTransport, Protocol):
    """The operator's transport: it may also create a ledger branch. The judge never gets one."""

    def write_root(self, *, message: bytes, files: Mapping[str, bytes]) -> str:
        """Create a parentless commit (no ref change). Returns the commit SHA."""
        ...

    def create_ref(self, branch: str, sha: str) -> UpdateResult:
        """Create ``branch`` at ``sha``. ``REJECTED`` when refused (exists, or a ruleset)."""
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _read_capped(stream: object) -> bytes:
    data = stream.read(MAX_RESPONSE + 1)  # type: ignore[attr-defined]
    if not isinstance(data, bytes) or len(data) > MAX_RESPONSE:
        raise TransportError("ledger response too large")
    return data


def default_send(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    """urllib sender that never follows redirects and caps the response size."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), _read_capped(resp)
    except urllib.error.HTTPError as err:
        with err:
            return int(err.code), _read_capped(err)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
        raise TransportError(f"ledger request failed: {type(err).__name__}") from None


def _oid(value: object, what: str) -> str:
    if not isinstance(value, str) or _OID.fullmatch(value) is None:
        raise LedgerMalformed(f"ledger {what} is not a git object id")
    return value


def _dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LedgerMalformed(f"ledger {what} has an unexpected shape")
    return value


class UrllibLedgerTransport:
    def __init__(
        self,
        repo: str,
        token: str,
        *,
        api_url: str = API_URL,
        send: Send | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._repo = ledger_repo(repo)
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise ValueError("invalid ledger token")
        parts = urllib.parse.urlsplit(api_url)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise ValueError("the ledger API URL must be https")
        self._api = api_url.rstrip("/")
        self._token = token
        self._send: Send = send if send is not None else default_send
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"UrllibLedgerTransport(repo={self._repo!r}, api_url={self._api!r})"

    # -- HTTP -------------------------------------------------------------------

    def _call(self, method: str, path: str, body: object = None) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(  # noqa: S310 - https enforced in __init__
            f"{self._api}/repos/{self._repo}/{path}", data=data, method=method
        )
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            return self._send(req, self._timeout)
        except TransportError:
            raise
        except (http.client.HTTPException, OSError, ValueError) as err:
            raise TransportError(f"ledger request failed: {type(err).__name__}") from None

    def _json(
        self, method: str, path: str, ok: tuple[int, ...], what: str, body: object = None
    ) -> dict[str, object]:
        status, raw = self._call(method, path, body)
        if status == 409 and method == "POST":
            # GitHub's Git data API refuses every write to a repository with no commits.
            raise TransportError(f"ledger {what} failed: the ledger repository is empty")
        if status not in ok:
            raise TransportError(f"ledger {what} failed: HTTP {status}")
        return _dict(_decode(raw, what), what)

    # -- LedgerTransport --------------------------------------------------------

    def get_ref(self, branch: str) -> str | None:
        status, raw = self._call("GET", f"git/ref/heads/{_quote(branch)}")
        if status == 404:
            return None
        if status != 200:
            raise TransportError(f"ledger ref read failed: HTTP {status}")
        obj = _dict(_decode(raw, "ref"), "ref")
        return _oid(_dict(obj.get("object"), "ref").get("sha"), "ref")

    def get_commit(self, sha: str) -> LedgerCommit:
        _oid(sha, "commit")
        obj = self._json("GET", f"git/commits/{sha}", (200,), "commit read")
        if obj.get("sha") != sha:
            raise LedgerMalformed("ledger commit was served under another id")
        message = obj.get("message")
        if not isinstance(message, str):
            raise LedgerMalformed("ledger commit message has an unexpected shape")
        tree = _oid(_dict(obj.get("tree"), "commit").get("sha"), "tree")
        raw_parents = obj.get("parents")
        if not isinstance(raw_parents, list):
            raise LedgerMalformed("ledger commit parents have an unexpected shape")
        parents = tuple(_oid(_dict(p, "parent").get("sha"), "parent") for p in raw_parents)
        files = self._tree(tree)
        return LedgerCommit(sha=sha, parents=parents, message=message.encode(), files=files)

    def _tree(self, sha: str) -> dict[str, bytes]:
        obj = self._json("GET", f"git/trees/{sha}", (200,), "tree read")
        entries = obj.get("tree")
        if obj.get("sha") != sha or obj.get("truncated") is not False:
            raise LedgerMalformed("ledger tree has an unexpected shape")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TREE_ENTRIES:
            raise LedgerMalformed("ledger tree has an unexpected number of entries")
        files: dict[str, bytes] = {}
        for raw in entries:
            entry = _dict(raw, "tree entry")
            path = entry.get("path")
            if entry.get("type") != "blob" or not isinstance(path, str) or path in files:
                raise LedgerMalformed("ledger tree entry has an unexpected shape")
            files[path] = self._blob(_oid(entry.get("sha"), "blob"))
        return files

    def _blob(self, sha: str) -> bytes:
        obj = self._json("GET", f"git/blobs/{sha}", (200,), "blob read")
        content = obj.get("content")
        if obj.get("sha") != sha or obj.get("encoding") != "base64":
            raise LedgerMalformed("ledger blob has an unexpected shape")
        if not isinstance(content, str):
            raise LedgerMalformed("ledger blob has an unexpected shape")
        try:
            return base64.b64decode(content.replace("\n", ""), validate=True)
        except (binascii.Error, ValueError):
            raise LedgerMalformed("ledger blob is not valid base64") from None

    def write_commit(self, *, parent: str, message: bytes, files: Mapping[str, bytes]) -> str:
        return self._write([_oid(parent, "parent")], message, files)

    def _write(self, parents: list[str], message: bytes, files: Mapping[str, bytes]) -> str:
        entries = []
        for path, content in sorted(files.items()):
            body = {"content": base64.b64encode(content).decode(), "encoding": "base64"}
            blob = self._json("POST", "git/blobs", (201,), "blob write", body)
            entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": _oid(blob.get("sha"), "blob"),
                }
            )
        tree = self._json("POST", "git/trees", (201,), "tree write", {"tree": entries})
        commit_body = {
            "message": message.decode(),
            "tree": _oid(tree.get("sha"), "tree"),
            "parents": parents,
        }
        commit = self._json("POST", "git/commits", (201,), "commit write", commit_body)
        return _oid(commit.get("sha"), "commit")

    def update_ref(self, branch: str, sha: str) -> UpdateResult:
        body = {"sha": _oid(sha, "commit"), "force": False}
        status, _ = self._call("PATCH", f"git/refs/heads/{_quote(branch)}", body)
        if status == 200:
            return UpdateResult.UPDATED
        if status == 422:
            return UpdateResult.REJECTED
        raise TransportError(f"ledger ref update failed: HTTP {status}")


class UrllibLedgerSeeder(UrllibLedgerTransport):
    """The operator's ``ci-budget-init`` transport: also writes roots and creates refs.

    A separate class so the judge's transport object has no way to create a branch or a
    parentless commit, even dynamically.
    """

    def __repr__(self) -> str:
        return f"UrllibLedgerSeeder(repo={self._repo!r}, api_url={self._api!r})"

    def write_root(self, *, message: bytes, files: Mapping[str, bytes]) -> str:
        return self._write([], message, files)

    def create_ref(self, branch: str, sha: str) -> UpdateResult:
        body = {"ref": f"refs/heads/{branch}", "sha": _oid(sha, "commit")}
        status, _ = self._call("POST", "git/refs", body)
        if status == 201:
            return UpdateResult.UPDATED
        # 422: ref exists or a ruleset refused it; 401/403: the token may not create it.
        # Both are definite refusals; the seeder re-reads the ref to tell them apart.
        if status in (401, 403, 422):
            return UpdateResult.REJECTED
        raise TransportError(f"ledger ref create failed: HTTP {status}")


def _quote(branch: str) -> str:
    return urllib.parse.quote(branch, safe="/")


def _decode(raw: bytes, what: str) -> object:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LedgerMalformed(f"ledger {what} is not JSON") from None


__all__ = [
    "LedgerMalformed",
    "LedgerSeeder",
    "LedgerTransport",
    "TransportError",
    "UpdateResult",
    "UrllibLedgerSeeder",
    "UrllibLedgerTransport",
    "default_send",
]
