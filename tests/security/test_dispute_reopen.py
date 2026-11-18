"""G12: nothing inside a session can reopen a disputed task or reuse an old session."""

from pathlib import Path
from types import SimpleNamespace

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dispute import configure_disputes, gate_dispute, resolve_dispute
from cheeseless.domain.errors import ErrorCode
from tests.factories import frozen_task
from tests.presence_fakes import Approving
from tests.support import bind_session

SEED = bytes(range(32))
SRC = Path(__file__).resolve().parents[2] / "src" / "cheeseless"


class Boom:
    def __getattr__(self, name: str):
        raise AssertionError(f"touched {name}")


def _server(store: FileStore, binding: dict) -> UdsVerifierServer:
    cfg = SessionConfig(
        task_id=frozen_task().task.task_id,
        worktree=Path("."),
        profile="local",
        pool=None,  # type: ignore[arg-type]
        timeout=1.0,
        tool_version="0",
        repo=Boom(),
        store=store,
        runner=Boom(),
        sandbox=Boom(),  # type: ignore[arg-type]
        **binding,
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    def flush(self) -> None: ...


def _status_after_reject(tmp_path: Path, *, bind_before: bool) -> bytes:
    store = FileStore(tmp_path / "state")
    store.save_task(frozen_task(), SEED)
    tid = frozen_task().task.task_id
    binding = bind_session(store, tid) if bind_before else None
    configure_disputes(store, enabled=True, cap=3)
    gate_dispute(
        store,
        task_id=frozen_task().task.task_id,
        criterion_id="normalize",
        code="untestable",
        note="n",
    )
    resolve_dispute(
        store, store.disputes()[-1].dispute_id, upheld=False, actor="op", presence=Approving()
    )
    server = _server(store, binding or bind_session(store, tid))
    out = _W()
    server._route(out, "/status", b"{}")  # noqa: SLF001
    return out.data


def test_a_session_from_before_the_rejection_is_refused(tmp_path: Path):
    stale = _status_after_reject(tmp_path / "a", bind_before=True)
    assert ErrorCode.ROLE_NOT_ALLOWED.value.encode() in stale


def test_a_session_launched_after_the_rejection_is_served_past_the_role_check(tmp_path: Path):
    fresh = _status_after_reject(tmp_path / "b", bind_before=False)
    assert ErrorCode.ROLE_NOT_ALLOWED.value.encode() not in fresh


def test_no_session_route_or_tool_can_resolve_or_reset(tmp_path: Path):
    store = FileStore(tmp_path / "state")
    store.save_task(frozen_task(), SEED)
    server = _server(store, bind_session(store, frozen_task().task.task_id))
    for path in ("/resolve", "/dispute/resolve", "/reopen", "/reset", "/grant"):
        out = _W()
        server._route(out, path, b"{}")  # noqa: SLF001
        assert b"404" in out.data or b"not found" in out.data
    client = SimpleNamespace()
    tools = {t.name for t in build_mcp_server(client, disputes=True)._tool_manager.list_tools()}  # noqa: SLF001
    assert not any(("resolve" in n or "reopen" in n or "reset" in n) for n in tools)


def test_inbound_adapters_never_import_the_resolution_use_case():
    for path in (SRC / "adapters" / "inbound" / "uds", SRC / "adapters" / "inbound" / "mcp"):
        for file in path.rglob("*.py"):
            text = file.read_text()
            assert "resolve_dispute" not in text and "set_session_generation" not in text, file
