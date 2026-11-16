"""The role-gated author surface over UDS, the client port and MCP."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds import server as uds_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.app.authoring import AuthorCheckResult
from cheeseless.domain.authoring import CrosscheckRecord, Proposal
from cheeseless.domain.errors import ErrorCode, gate_error

OID = "a" * 40
H = "b" * 64


def _server(role: str, **extra) -> UdsVerifierServer:
    cfg = SessionConfig(
        task_id=None,
        worktree=Path("."),
        profile="local",
        pool=None,  # type: ignore[arg-type]
        timeout=1.0,
        tool_version="0",
        repo=SimpleNamespace(),  # type: ignore[arg-type]
        store=SimpleNamespace(),  # type: ignore[arg-type]
        runner=SimpleNamespace(),  # type: ignore[arg-type]
        sandbox=SimpleNamespace(),  # type: ignore[arg-type]
        role=role,
        **extra,
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _post(server, path, payload=None):
    out = io.BytesIO()
    server._route(out, path, json.dumps(payload or {}).encode())
    head, _, body = out.getvalue().partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0].decode(), body


AUTHOR_PATHS = ["/author-check", "/author-crosscheck", "/freeze-propose"]
RPC_PATHS = [
    "/cheeseless.v1.Verifier/AuthorCheck",
    "/cheeseless.v1.Verifier/AuthorCrosscheck",
    "/cheeseless.v1.Verifier/FreezePropose",
]


@pytest.mark.parametrize("path", AUTHOR_PATHS + RPC_PATHS)
def test_implementer_sessions_refuse_author_routes(path):
    status, body = _post(_server("implementer"), path)
    assert status.startswith("HTTP/1.1 200")
    assert b"ROLE_NOT_ALLOWED" in body


@pytest.mark.parametrize("path", ["/run", "/status", "/dispute"])
def test_author_sessions_refuse_implementer_routes(path):
    status, body = _post(_server("author"), path)
    assert status.startswith("HTTP/1.1 200")
    assert b"ROLE_NOT_ALLOWED" in body


def test_unknown_and_operator_paths_do_not_exist():
    for path in ("/approve", "/freeze-approve", "/record-reference", "/promote", "/x/run"):
        for role in ("author", "implementer"):
            status, _ = _post(_server(role), path)
            assert status.startswith("HTTP/1.1 404")


def test_author_routes_call_the_gate(monkeypatch):
    monkeypatch.setattr(
        uds_server,
        "gate_author_check",
        lambda repo, sandbox, store, wt: AuthorCheckResult(False, ("bad",), 2),
    )
    monkeypatch.setattr(
        uds_server,
        "gate_author_crosscheck",
        lambda repo, sandbox, store, wt: CrosscheckRecord(OID, H, OID, OID, False, ("a",)),
    )
    monkeypatch.setattr(
        uds_server,
        "gate_freeze_propose",
        lambda r, s, wt: Proposal(H, "l" * 0 + "lin", OID, OID, H),
    )
    srv = _server("author", author_sandbox=SimpleNamespace())
    _, body = _post(srv, "/author-check")
    assert json.loads(body)["check"] == {"problems": ["bad"], "tests": 2}
    _, body = _post(srv, "/author-crosscheck")
    assert json.loads(body)["crosscheck"]["redCriteria"] == ["a"]


def test_author_gate_errors_pass_through(monkeypatch):
    monkeypatch.setattr(
        uds_server, "gate_freeze_propose", lambda r, s, wt: gate_error(ErrorCode.AUTHOR_REFUSED)
    )
    _, body = _post(_server("author"), "/freeze-propose")
    assert b"AUTHOR_REFUSED" in body


def test_missing_author_sandbox_is_an_infra_error():
    _, body = _post(_server("author"), "/author-check")
    assert b"INFRA_ERROR" in body


def test_unknown_request_fields_are_rejected():
    status, _ = _post(_server("author"), "/author-check", {"approve": True})
    assert status.startswith("HTTP/1.1 400")


class FakeClient:
    def author_check(self):
        return {"ok": True, "problems": [], "tests": 4}

    def author_crosscheck(self):
        return {"green": True, "red_criteria": []}

    def freeze_propose(self):
        return {"proposal_id": H}

    def run(self):
        raise AssertionError

    status = dispute = run


def _names(**kw):
    server = build_mcp_server(FakeClient(), **kw)
    return {t.name for t in server._tool_manager.list_tools()}


def test_mcp_author_tools_only_when_enabled_and_never_approve():
    assert _names() == {"gate_run", "gate_status"}
    names = _names(author=True)
    assert names == {
        "gate_run",
        "gate_status",
        "author_check",
        "author_crosscheck",
        "freeze_propose",
    }


def test_mcp_author_tool_results_and_failures():
    server = build_mcp_server(FakeClient(), author=True)
    res = server._tool_manager.get_tool("author_check").fn()
    assert res.structured_content["outcome"]["kind"] == "check"

    class Boom(FakeClient):
        def freeze_propose(self):
            raise RuntimeError("x")

    boom = build_mcp_server(Boom(), author=True)
    res = boom._tool_manager.get_tool("freeze_propose").fn()
    assert res.structured_content["outcome"]["kind"] == "error"
