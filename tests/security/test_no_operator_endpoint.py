"""ADR 0009: approval, reference bookkeeping and promotion exist only on the host CLI.

Neither the UDS surface nor the MCP surface may expose an operator action, whatever the
session role or flags, and failures on the author surface must not leak hidden content.
"""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds import server as uds
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.app import author_gate
from tests.factories import frozen_task

CANARY = "CANARY-HIDDEN-9c1e44"
OPERATOR_WORDS = (
    "approve",
    "reference",
    "retry",
    "freeze-approve",
    "freeze_approve",
    "promote",
    "disable",
    "waive",
    "no-reference",
    "no_reference",
    "recover",
    "author-recover",
)
ALLOWED_AUTHOR = {"author-check", "author-crosscheck", "freeze-propose"}


def _route_paths() -> set[str]:
    return set(uds._AUTHOR_ROUTES) | set(uds._IMPLEMENTER_ROUTES)


def test_route_table_has_no_operator_path() -> None:
    for path in _route_paths():
        assert not any(w in path.lower() for w in OPERATOR_WORDS), path
    slugs = {p.rsplit("/", 1)[-1] for p in uds._AUTHOR_ROUTES}
    assert {s for s in slugs if "-" in s} <= ALLOWED_AUTHOR


class _Boom:
    def __getattr__(self, name: str):
        raise AssertionError(f"operator path touched {name}")


def _server(role: str) -> UdsVerifierServer:
    cfg = SessionConfig(
        task_id=frozen_task().task.task_id,
        worktree=Path("."),
        profile="local",
        pool=None,  # type: ignore[arg-type]
        timeout=1.0,
        tool_version="0",
        repo=_Boom(),
        store=_Boom(),
        runner=_Boom(),
        sandbox=_Boom(),  # type: ignore[arg-type]
        role=role,
        author_sandbox=_Boom(),
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _call(server: UdsVerifierServer, path: str, body: bytes = b"{}") -> bytes:
    out = io.BytesIO()
    server._route(out, path, body)
    return out.getvalue()


@pytest.mark.parametrize("role", ["implementer", "author"])
@pytest.mark.parametrize(
    "name",
    [
        "approve",
        "freeze-approve",
        "FreezeApprove",
        "record-reference",
        "RecordReference",
        "reference-retry",
        "retry",
        "freeze",
        "promote",
        "disable",
        "no-reference",
        "recover",
        "author-recover",
        "AuthorRecover",
    ],
)
def test_operator_paths_are_404_for_every_role(role: str, name: str) -> None:
    server = _server(role)
    for path in (f"/{name}", f"/cheeseless.v1.Verifier/{name}"):
        assert b"404" in _call(server, path).split(b"\r\n", 1)[0]


@pytest.mark.parametrize("author", [False, True])
@pytest.mark.parametrize("disputes", [False, True])
def test_mcp_tools_never_include_operator_actions(author: bool, disputes: bool) -> None:
    server = build_mcp_server(SimpleNamespace(), disputes=disputes, author=author)
    names = {t.name for t in server._tool_manager.list_tools()}
    for name in names:
        assert not any(w in name for w in OPERATOR_WORDS), name


def test_author_failures_do_not_leak_hidden_content(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError(f"assertion failed: {CANARY}")

    monkeypatch.setattr(author_gate, "author_check", boom)
    monkeypatch.setattr(author_gate, "freeze_propose", boom)
    repo = SimpleNamespace(snapshot=lambda w: "a" * 40)
    for out in (
        author_gate.gate_author_check(repo, object(), object(), "wt"),
        author_gate.gate_freeze_propose(repo, object(), "wt"),
    ):
        assert CANARY not in repr(out)
        assert CANARY not in json.dumps(getattr(out, "__dict__", {}), default=str)
