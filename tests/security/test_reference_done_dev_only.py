"""ADR 0014 slice 3a: ``reference_done`` exists only in a running TUI dev reference attempt.

The MCP tool list is only what the agent sees. The gate is the UDS route: every path
form must be refused for every role and claim outside a running TUI attempt, before
anything is read or written. The attempt record must stay byte-identical.
"""

import io
import json
from pathlib import Path

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.domain.session_role import SessionRole, tool_names
from tests.factories import frozen_task
from tests.support import bind_session

SEED = bytes(range(32))
LINEAGE = "toy"
ATTEMPT = "att-1"
PATHS = ("/reference-done", "/ReferenceDone", "/cheeseless.v1.Verifier/ReferenceDone")
ROLES = ("implementer", "reference", "author")
S = ReferenceState


class Boom:
    """Any use means a refused call reached the verifier."""

    def __getattr__(self, name: str):
        raise AssertionError(f"refused call touched {name}")


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(LINEAGE), SEED)
    return s


def _tid() -> str:
    return frozen_task(LINEAGE).task.task_id


def _server(store, role: str, *, dev_reference: str | None, bound: str | None = None):
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
        dev_reference=dev_reference,
        **bind_session(store, _tid(), bound or role),
    )
    return cfg, UdsVerifierServer("/nonexistent/sock", cfg)


def _seed(store, cfg, *, state=S.RUNNING, mode=ReferenceMode.TUI) -> ReferenceAttempt:
    attempt = ReferenceAttempt(
        ATTEMPT, cfg.session_id, cfg.generation, "a" * 64, mode, False, state
    )
    store.save_reference_attempt(attempt)
    return attempt


def _wire(server, path: str, claimed: str) -> bytes:
    out = io.BytesIO()
    server._route(out, path, json.dumps({"taskId": _tid()}).encode(), claimed_role=claimed)
    return out.getvalue()


def _refused(raw: bytes) -> bool:
    return b"ROLE_NOT_ALLOWED" in raw or b"role_not_allowed" in raw


def _record(store) -> bytes:
    return (store._root / "dev-reference" / f"{ATTEMPT}.json").read_bytes()


def _all_refused(server, store) -> None:
    before = _record(store)
    for path in PATHS:
        for claimed in ROLES:
            assert _refused(_wire(server, path, claimed)), (path, claimed)
    assert _record(store) == before  # the canary: nothing moved


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    ("state", "mode"),
    [
        (S.STOPPING, ReferenceMode.TUI),
        (S.DONE, ReferenceMode.TUI),
        (S.FAILED, ReferenceMode.TUI),
        (S.CANCELLED, ReferenceMode.TUI),
        (S.RUNNING, ReferenceMode.HEADLESS),
        (S.STOPPING, ReferenceMode.HEADLESS),
    ],
)
def test_refused_outside_a_running_tui_attempt(store, role, state, mode):
    cfg, server = _server(store, role, dev_reference=ATTEMPT)
    _seed(store, cfg, state=state, mode=mode)
    _all_refused(server, store)


@pytest.mark.parametrize("role", ["implementer", "author"])
def test_refused_for_any_non_reference_role_even_with_a_running_attempt(store, role):
    cfg, server = _server(store, role, dev_reference=ATTEMPT)
    _seed(store, cfg)
    _all_refused(server, store)


@pytest.mark.parametrize("role", ROLES)
def test_refused_without_dev_reference(store, role):
    cfg, server = _server(store, role, dev_reference=None)
    _seed(store, cfg)
    _all_refused(server, store)


def test_refused_for_an_unknown_attempt(store):
    cfg, server = _server(store, "reference", dev_reference="nope")
    _seed(store, cfg)
    _all_refused(server, store)


def test_refused_for_an_attempt_bound_to_another_session(store):
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT)
    store.save_reference_attempt(
        ReferenceAttempt(
            ATTEMPT, "f" * 32, cfg.generation, "a" * 64, ReferenceMode.TUI, False, S.RUNNING
        )
    )
    _all_refused(server, store)


def test_a_forged_claim_on_a_running_reference_session_is_refused(store):
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT)
    _seed(store, cfg)
    before = _record(store)
    for path in PATHS:
        for claimed in ("implementer", "author"):
            assert _refused(_wire(server, path, claimed)), (path, claimed)
    assert _record(store) == before


def test_a_reference_whose_store_record_says_implementer_fails_closed(store):
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT, bound="implementer")
    _seed(store, cfg)
    _all_refused(server, store)


def test_a_stale_generation_is_refused(store):
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT)
    _seed(store, cfg)
    (store._root / "session-generations.json").write_text(json.dumps({_tid(): 1}))
    _all_refused(server, store)


def test_a_running_tui_attempt_is_admitted_once_then_refused(store):
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT)
    _seed(store, cfg)
    first = _wire(server, PATHS[0], "reference")
    assert not _refused(first)
    assert store.reference_attempt(ATTEMPT).state is S.STOPPING
    _all_refused(server, store)  # second call: refused, record unchanged


def test_the_stop_seam_is_called_once_on_success(store):
    calls: list[str] = []
    cfg, server = _server(store, "reference", dev_reference=ATTEMPT)
    server.session.on_reference_stop = calls.append  # type: ignore[attr-defined]
    _seed(store, cfg)
    _wire(server, PATHS[0], "reference")
    _wire(server, PATHS[0], "reference")
    assert calls == [ATTEMPT]


class _Client:
    def __getattr__(self, name: str):
        raise AssertionError(name)


@pytest.mark.parametrize("disputes", [False, True])
def test_reference_done_is_listed_only_for_a_dev_reference(disputes):
    for role in SessionRole:
        for dev in (False, True):
            server = build_mcp_server(_Client(), role=role, disputes=disputes, dev=dev)
            listed = {t.name for t in server._tool_manager.list_tools()}
            assert listed == set(tool_names(role, disputes=disputes, dev=dev))
            wanted = role is SessionRole.REFERENCE and dev
            assert ("reference_done" in listed) is wanted, (role, dev)
