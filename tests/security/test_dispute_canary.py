"""ADR 0009 stage g: the dispute channel must not become a side channel to, or from, the gate."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dispute import configure_disputes, gate_dispute
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import promote
from cheeseless.domain.dispute import ACK_TEXT
from cheeseless.domain.errors import ErrorCode, GateError
from tests.factories import frozen_task
from tests.presence_fakes import Approving
from tests.support import bind_session

SEED = bytes(range(32))
CANARY = "CANARY-NOTE-7f3a91"
HIDDEN_IDS = ("t::b", "t::d", "test_a.py")


class Boom:
    """Any use means the disputed session reached the verifier."""

    def __getattr__(self, name: str):
        raise AssertionError(f"disputed session touched {name}")


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    return s


def _tid() -> str:
    return frozen_task().task.task_id


def _dispute(store, criterion="normalize", code="untestable", note="n"):
    return gate_dispute(store, task_id=_tid(), criterion_id=criterion, code=code, note=note)


def _server(store: FileStore) -> UdsVerifierServer:
    cfg = SessionConfig(
        task_id=_tid(),
        worktree=Path("."),
        profile="local",
        pool=None,  # type: ignore[arg-type]
        timeout=1.0,
        tool_version="0",
        repo=Boom(),
        store=store,
        runner=Boom(),
        sandbox=Boom(),  # type: ignore[arg-type]
        **bind_session(store, _tid()),
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _wire(server: UdsVerifierServer, path: str, payload: dict) -> bytes:
    out = io.BytesIO()
    server._route(out, path, json.dumps(payload).encode())
    return out.getvalue()


def _req(**kw):
    return {
        "taskId": _tid(),
        "criterionId": "normalize",
        "code": "DISPUTE_CODE_OTHER",
        "note": "n",
        **kw,
    }


def test_dispute_is_off_by_default(store):
    out = _dispute(store)
    assert isinstance(out, GateError) and out.code is ErrorCode.DISPUTE_DISABLED
    assert store.disputes() == () and not store.session_disputed(_tid())
    assert b"DISPUTE_DISABLED" in _wire(_server(store), "/dispute", _req())
    assert store.disputes() == ()


def test_agent_writable_worktree_cannot_enable_disputes(store, tmp_path):
    wt = tmp_path / "worktree" / ".cheeseless"
    wt.mkdir(parents=True)
    (wt / "config.toml").write_text("[dispute]\nenabled = true\ncap = 99\n")
    (wt / "dispute-policy.json").write_text('{"enabled": true, "cap": 99}')
    assert not store.dispute_policy().enabled
    assert isinstance(_dispute(store), GateError)


def test_note_never_reaches_the_gated_session(store):
    configure_disputes(store, enabled=True, cap=3)
    server = _server(store)
    seen = [_wire(server, "/dispute", _req(note=CANARY))]
    seen.append(_wire(server, "/status", {"taskId": _tid()}))
    seen.append(_wire(server, "/run", {"taskId": _tid()}))
    seen.append(_wire(server, "/dispute", _req(note=CANARY)))
    assert CANARY.encode() not in b"".join(seen)
    assert store.disputes()[0].note == CANARY  # recorded for the host only


def test_mcp_never_echoes_the_note(store):
    class Client:
        def dispute(self, criterion_id, code, note):
            return ACK_TEXT

    res = (
        build_mcp_server(Client(), disputes=True)
        ._tool_manager.get_tool("gate_dispute")
        .fn("normalize", "other", CANARY)
    )
    assert CANARY not in res.content[0].text


def test_ack_is_uniform_and_carries_no_evaluation(tmp_path):
    bodies = set()
    for i, (crit, code, note) in enumerate(
        [("normalize", "DISPUTE_CODE_OTHER", "a"), ("score", "DISPUTE_CODE_UNTESTABLE", "b" * 300)]
    ):
        s = FileStore(tmp_path / f"s{i}")
        s.save_task(frozen_task(), SEED)
        configure_disputes(s, enabled=True, cap=3)
        bodies.add(_wire(_server(s), "/dispute", _req(criterionId=crit, code=code, note=note)))
    assert len(bodies) == 1
    body = bodies.pop()
    assert ACK_TEXT.encode() in body
    for word in (b"d-1", b"cap", b"pass", b"fail", b"hidden", b"holdout"):
        assert word not in body.lower().replace(b"dispute", b"")


def test_unknown_criterion_rejected_identically_and_no_hidden_echo(store):
    configure_disputes(store, enabled=True, cap=3)
    server = _server(store)
    a = _wire(server, "/dispute", _req(criterionId="nope"))
    b = _wire(server, "/dispute", _req(criterionId="t::b"))
    c = _wire(server, "/dispute", _req(criterionId="acceptance/hidden/test_a.py"))
    bodies = {x.partition(b"\r\n\r\n")[2] for x in (a, b, c)}
    assert len(bodies) == 1
    assert b"DISPUTE_INVALID" in bodies.pop()
    for h in HIDDEN_IDS:
        assert h.encode() not in a + b + c
    assert store.disputes() == ()


def test_no_list_or_resolve_on_uds_or_mcp(store):
    for path in ("/disputes", "/dispute/resolve", "/resolve", "/Resolve", "/List"):
        assert _wire(_server(store), path, {}).startswith(b"HTTP/1.1 404"), path
    for enabled in (False, True):
        server = build_mcp_server(SimpleNamespace(), disputes=enabled)
        names = {t.name for t in server._tool_manager.list_tools()}
        assert names <= {"gate_run", "gate_status", "gate_dispute"}
        assert ("gate_dispute" in names) is enabled


def test_disputed_session_cannot_run_publish_or_promote(store):
    configure_disputes(store, enabled=True, cap=3)
    assert _dispute(store) == ACK_TEXT
    verdict = gate_run(Boom(), store, Boom(), Boom(), worktree=".", task_id=_tid())
    assert isinstance(verdict.outcome, GateError)
    assert verdict.outcome.code is ErrorCode.SESSION_DISPUTED
    assert store.latest_result(_tid()) is None
    with pytest.raises(ValueError, match="dispute"):
        promote(Boom(), store, task_id=_tid(), worktree=".", presence=Approving())


def test_cap_is_enforced_and_refusal_records_nothing(store):
    configure_disputes(store, enabled=True, cap=1)
    assert _dispute(store) == ACK_TEXT
    store.resolve_dispute(
        store.disputes()[0].dispute_id,
        __import__("cheeseless.domain.dispute", fromlist=["DisputeStatus"]).DisputeStatus.REJECTED,
        "op",
    )
    out = _dispute(store)
    assert isinstance(out, GateError) and out.code is ErrorCode.DISPUTE_CAP_REACHED
    assert len(store.disputes()) == 1
