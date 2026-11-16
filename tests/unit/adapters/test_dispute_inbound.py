"""gate_dispute over MCP and the UDS handler, and the host-only dispute CLI (stage g)."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.cli.main import Context, build_parser, dispatch
from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dispute import configure_disputes
from cheeseless.domain.dispute import ACK_TEXT, DisputeCode
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from tests.factories import frozen_task

SEED = bytes(range(32))


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    return s


def _tid() -> str:
    return frozen_task().task.task_id


class FakeClient:
    def __init__(self, outcome: str | GateError | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, DisputeCode, str]] = []

    def dispute(self, criterion_id: str, code: DisputeCode, note: str) -> str | GateError:
        self.calls.append((criterion_id, code, note))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _tool(client: FakeClient):
    return build_mcp_server(client)._tool_manager.get_tool("gate_dispute").fn


def test_mcp_registers_gate_dispute_but_no_host_tools() -> None:
    names = {t.name for t in build_mcp_server(FakeClient(ACK_TEXT))._tool_manager.list_tools()}
    assert names == {"gate_run", "gate_status", "gate_dispute"}


def test_mcp_ack_is_fixed_text() -> None:
    client = FakeClient("anything the client says")
    res = _tool(client)("normalize", "untestable", "n")
    assert res.structured_content == {"outcome": {"kind": "ack", "message": ACK_TEXT}}
    assert client.calls == [("normalize", DisputeCode.UNTESTABLE, "n")]


def test_mcp_error_and_bad_code_and_infra() -> None:
    err = _tool(FakeClient(gate_error(ErrorCode.DISPUTE_DISABLED)))("a", "other", "n")
    assert err.structured_content["outcome"]["kind"] == "error"
    bad = _tool(FakeClient(ACK_TEXT))("a", "not-a-code", "n")
    assert bad.structured_content["outcome"]["error"]["code"] == "dispute_invalid"
    boom = _tool(FakeClient(RuntimeError("x")))("a", "other", "n")
    assert boom.structured_content["outcome"]["error"]["code"] == "infra_error"


def _server(store: FileStore, task_id: str | None = None) -> UdsVerifierServer:
    cfg = SessionConfig(
        task_id=task_id or _tid(),
        worktree=Path("."),
        profile="local",
        pool=None,  # type: ignore[arg-type]
        timeout=1.0,
        tool_version="0",
        repo=SimpleNamespace(),  # type: ignore[arg-type]
        store=store,
        runner=SimpleNamespace(),  # type: ignore[arg-type]
        sandbox=SimpleNamespace(),  # type: ignore[arg-type]
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _post(server: UdsVerifierServer, path: str, payload: dict) -> tuple[str, bytes]:
    out = io.BytesIO()
    server._route(out, path, json.dumps(payload).encode())
    head, _, body = out.getvalue().partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0].decode(), body


def _req(**kw):
    base = {
        "taskId": _tid(),
        "criterionId": "normalize",
        "code": "DISPUTE_CODE_UNTESTABLE",
        "note": "n",
    }
    return {**base, **kw}


def test_uds_dispute_disabled_by_default(store: FileStore) -> None:
    status, body = _post(_server(store), "/dispute", _req())
    assert status.startswith("HTTP/1.1 200")
    assert b"DISPUTE_DISABLED" in body
    assert store.disputes() == ()


def test_uds_dispute_ack_then_status_and_run_closed(store: FileStore) -> None:
    configure_disputes(store, enabled=True, cap=3)
    server = _server(store)
    status, body = _post(server, "/dispute", _req(note="secret-ish note"))
    assert ACK_TEXT.encode() in body and b"secret-ish" not in body
    _, again = _post(server, "/cheeseless.v1.Verifier/Dispute", _req())
    assert b"SESSION_DISPUTED" in again
    _, st = _post(server, "/status", {"taskId": _tid()})
    assert b"SESSION_DISPUTED" in st


def test_uds_dispute_ignores_client_task_and_rejects_mismatch(store: FileStore) -> None:
    configure_disputes(store, enabled=True, cap=3)
    status, _ = _post(_server(store), "/dispute", _req(taskId="other"))
    assert status.startswith("HTTP/1.1 403")
    assert store.disputes() == ()


def test_uds_dispute_bad_body_is_400(store: FileStore) -> None:
    out = io.BytesIO()
    _server(store)._route(out, "/dispute", b"{not json")
    assert out.getvalue().startswith(b"HTTP/1.1 400")


def test_uds_has_no_list_or_resolve_route(store: FileStore) -> None:
    for path in ("/disputes", "/dispute/resolve", "/Disputes", "/resolve"):
        status, _ = _post(_server(store), path, {})
        assert status.startswith("HTTP/1.1 404"), path


def _cli(store: FileStore, argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(repo=None, runner=None, sandbox=None, store=store)  # type: ignore[arg-type]
    return dispatch(args, ctx)


def _file(store: FileStore, note: str = "plain note") -> None:
    configure_disputes(store, enabled=True, cap=3)
    store.file_dispute(_tid(), "toy", "normalize", DisputeCode.UNTESTABLE, note)


def test_cli_list_hides_note_by_default(store: FileStore, capsys) -> None:
    _file(store)
    assert _cli(store, ["disputes", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    row = data["disputes"][0]
    assert row["code"] == "untestable" and row["status"] == "open"
    assert "note_untrusted" not in row


def test_cli_list_show_note_labels_untrusted(store: FileStore, capsys) -> None:
    _file(store)
    assert _cli(store, ["disputes", "--show-note"]) == 0
    out = capsys.readouterr().out
    assert "UNTRUSTED" in out and "plain note" in out
    assert _cli(store, ["disputes", "--json", "--show-note"]) == 0
    assert json.loads(capsys.readouterr().out)["disputes"][0]["note_untrusted"] == "plain note"


def test_cli_list_empty(store: FileStore, capsys) -> None:
    assert _cli(store, ["disputes"]) == 0
    assert "no disputes" in capsys.readouterr().out


def test_cli_resolve_once_and_records_actor(store: FileStore, capsys) -> None:
    _file(store)
    did = store.disputes()[0].dispute_id
    assert _cli(store, ["dispute", "resolve", did, "--rejected", "--as", "alice", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "rejected" and out["resolved_by"] == "alice"
    assert _cli(store, ["dispute", "resolve", did, "--upheld", "--as", "bob"]) == 1
    assert "dispute failed" in capsys.readouterr().err


def test_cli_resolve_upheld_text_points_at_freeze(store: FileStore, capsys) -> None:
    _file(store)
    did = store.disputes()[0].dispute_id
    assert _cli(store, ["dispute", "resolve", did, "--upheld", "--as", "op"]) == 0
    assert "freeze" in capsys.readouterr().out


def test_cli_resolve_requires_outcome_and_actor() -> None:
    parser = build_parser()
    for argv in (
        ["dispute", "resolve", "d-1", "--as", "x"],
        ["dispute", "resolve", "d-1", "--upheld"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_cli_config_enable_cap_and_bad_cap(store: FileStore, capsys) -> None:
    assert _cli(store, ["dispute", "config", "--enable", "--cap", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": True, "cap": 2}
    assert _cli(store, ["dispute", "config", "--enable", "--cap", "0"]) == 1
    assert _cli(store, ["dispute", "config", "--disable"]) == 0
    assert not store.dispute_policy().enabled
    assert "disabled" in capsys.readouterr().out
