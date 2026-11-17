"""ADR 0013 stage 2: ``spec_done`` exists only in a host-started dev author session.

The MCP tool list is only what the agent sees. The gate is the UDS route: an agent can
speak raw HTTP to the socket or build its own client from the staged runtime, so every
path form must be refused outside a dev author session, before anything is read or
written. The dev state of the lineage must be left exactly as it was.
"""

import io
import json
from pathlib import Path

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.bootstrap import _mcp_shim_source
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.session_role import SessionRole, dev_home_entries, tool_names
from tests.factories import frozen_task
from tests.support import bind_session

SEED = bytes(range(32))
LINEAGE = "toy"
SPEC_DONE_PATHS = ("/spec-done", "/SpecDone", "/cheeseless.v1.Verifier/SpecDone")


class Boom:
    """Any use means a refused call reached the verifier."""

    def __getattr__(self, name: str):
        raise AssertionError(f"refused call touched {name}")


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(LINEAGE), SEED)
    s.save_dev_state(DevState(LINEAGE))
    return s


def _tid() -> str:
    return frozen_task(LINEAGE).task.task_id


def _server(store, role: str, *, dev: bool, bound: str | None = None) -> UdsVerifierServer:
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
        role=role,
        author_sandbox=Boom() if role == "author" else None,  # type: ignore[arg-type]
        dev_lineage=LINEAGE if dev else None,
        **bind_session(store, _tid(), bound or role),
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _wire(server, path: str, claimed: str) -> bytes:
    out = io.BytesIO()
    server._route(out, path, json.dumps({"taskId": _tid()}).encode(), claimed_role=claimed)
    return out.getvalue()


def _refused(raw: bytes) -> bool:
    return b"ROLE_NOT_ALLOWED" in raw or b"role_not_allowed" in raw


_OUTSIDE_DEV_AUTHOR = [
    (role, dev)
    for role in ("implementer", "reference", "author")
    for dev in (False, True)
    if (role, dev) != ("author", True)
]


@pytest.mark.parametrize(("role", "dev"), _OUTSIDE_DEV_AUTHOR)
def test_spec_done_is_refused_outside_a_dev_author_session(store, role, dev):
    before = store.dev_state(LINEAGE)
    server = _server(store, role, dev=dev)
    for path in SPEC_DONE_PATHS:
        for claimed in ("implementer", "reference", "author"):
            assert _refused(_wire(server, path, claimed)), (role, dev, path, claimed)
    assert store.dev_state(LINEAGE) == before  # the canary: nothing moved


def test_a_forged_author_claim_on_a_dev_implementer_session_is_refused(store):
    before = store.dev_state(LINEAGE)
    server = _server(store, "implementer", dev=True)
    for path in SPEC_DONE_PATHS:
        assert _refused(_wire(server, path, "author")), path
    assert store.dev_state(LINEAGE) == before


def test_a_dev_author_whose_store_record_says_implementer_fails_closed(store):
    before = store.dev_state(LINEAGE)
    server = _server(store, "author", dev=True, bound="implementer")
    for path in SPEC_DONE_PATHS:
        assert _refused(_wire(server, path, "author")), path
    assert store.dev_state(LINEAGE) == before


def test_a_stale_generation_refuses_spec_done_in_dev_mode(store):
    before = store.dev_state(LINEAGE)
    server = _server(store, "author", dev=True)
    # a rejected dispute starts a new generation: this session is stale from then on
    (store._root / "session-generations.json").write_text(json.dumps({_tid(): 1}))
    for path in SPEC_DONE_PATHS:
        assert _refused(_wire(server, path, "author")), path
    assert store.dev_state(LINEAGE) == before


class _Client:
    def __getattr__(self, name: str):
        raise AssertionError(name)


def _listed(role: SessionRole, *, disputes: bool, dev: bool) -> set[str]:
    server = build_mcp_server(_Client(), role=role, disputes=disputes, dev=dev)
    return {t.name for t in server._tool_manager.list_tools()}


@pytest.mark.parametrize("disputes", [False, True])
def test_spec_done_is_listed_only_for_a_dev_author(disputes):
    for role in SessionRole:
        for dev in (False, True):
            listed = _listed(role, disputes=disputes, dev=dev)
            assert listed == set(tool_names(role, disputes=disputes, dev=dev))
            wanted = role is SessionRole.AUTHOR and dev
            assert ("spec_done" in listed) is wanted, (role, dev)


def test_the_shim_bakes_dev_only_for_the_roles_that_have_a_dev_tool(tmp_path):
    def shim(role: SessionRole, dev: bool) -> str:
        return _mcp_shim_source("/py", tmp_path, tmp_path / "s.sock", "t", role=role, dev=dev)

    for role in SessionRole:
        assert "dev=" not in shim(role, False)
    # dev carries exactly the one dev-only tool of the role (ADR 0013 author, ADR 0014 reference)
    assert "dev=True" in shim(SessionRole.AUTHOR, True)
    assert "dev=True" in shim(SessionRole.REFERENCE, True)
    # dev is ignored for any other role: its shim stays byte-identical
    assert shim(SessionRole.IMPLEMENTER, True) == shim(SessionRole.IMPLEMENTER, False)


def test_the_spec_done_command_is_seeded_only_for_a_dev_author():
    for role in SessionRole:
        assert dev_home_entries(role, dev=False) == ()
    assert dev_home_entries(SessionRole.IMPLEMENTER, dev=True) == ()
    assert dev_home_entries(SessionRole.REFERENCE, dev=True) == ()
    entries = dict(dev_home_entries(SessionRole.AUTHOR, dev=True))
    assert set(entries) == {".claude/commands/spec-done.md"}
    assert b"spec_done" in entries[".claude/commands/spec-done.md"]
