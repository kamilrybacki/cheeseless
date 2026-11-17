"""Stage (c): a session's role is bound by the host at launch and re-checked on every call."""

import io
import json
from pathlib import Path

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.session_role import SessionRole, acceptance_mount, tool_names
from tests.factories import frozen_task

SEED = bytes(range(32))
ROUTES = ("/run", "/status", "/dispute", "/author-check", "/author-crosscheck", "/freeze-propose")


class Boom:
    """Any use means a refused call reached the verifier."""

    def __getattr__(self, name: str):
        raise AssertionError(f"refused call touched {name}")


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    return s


def _tid() -> str:
    return frozen_task().task.task_id


def _server(store, role: str) -> UdsVerifierServer:
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
    )
    return UdsVerifierServer("/nonexistent/sock", cfg)


def _wire(server, path: str, claimed: str = "implementer") -> bytes:
    out = io.BytesIO()
    server._route(out, path, json.dumps({"taskId": _tid()}).encode(), claimed_role=claimed)
    return out.getvalue()


def _refused(raw: bytes) -> bool:
    return (
        b"ROLE_NOT_ALLOWED" in raw
        or b"role_not_allowed" in raw
        or b"\x20\x14" in raw
        or b"\x08\x14" in raw
    )


def test_an_author_session_cannot_call_gate_run(store):
    store.set_session_role(_tid(), "author")
    server = _server(store, "author")
    for path in ("/run", "/status", "/dispute"):
        assert _refused(_wire(server, path, "author")), path


def test_an_implementer_session_cannot_call_the_author_tools(store):
    store.set_session_role(_tid(), "implementer")
    server = _server(store, "implementer")
    for path in ("/author-check", "/author-crosscheck", "/freeze-propose"):
        assert _refused(_wire(server, path)), path
        assert _refused(_wire(server, path, "author")), path  # a forged claim changes nothing


def test_a_forged_claim_on_an_implementer_session_is_refused_everywhere(store):
    server = _server(store, "implementer")
    for path in ROUTES:
        assert _refused(_wire(server, path, "author")), path


def test_a_role_mismatch_between_the_session_and_the_store_fails_closed(store):
    store.set_session_role(_tid(), "implementer")  # the host launched an implementer
    server = _server(store, "author")  # a stale or forged author server object
    for path in ("/author-check", "/freeze-propose", "/run"):
        assert _refused(_wire(server, path, "author")), path


def test_an_unreadable_role_record_fails_closed(store):
    (store._root / "session-roles.json").write_text("{not json")
    server = _server(store, "implementer")
    for path in ("/run", "/status"):
        assert _refused(_wire(server, path)), path


def test_a_reference_session_has_no_route_at_all(store):
    store.set_session_role(_tid(), "reference")
    server = _server(store, "reference")
    for path in ROUTES:
        for claimed in ("reference", "implementer", "author"):
            assert _refused(_wire(server, path, claimed)), (path, claimed)


def test_the_role_record_defaults_to_implementer_and_is_host_written(store):
    assert store.session_role(_tid()) == "implementer"
    store.set_session_role(_tid(), "author")
    assert store.session_role(_tid()) == "author"
    with pytest.raises(ValueError):
        store.set_session_role(_tid(), "root")


class _Client:
    def __getattr__(self, name: str):
        raise AssertionError(name)


def _listed(role: SessionRole, disputes: bool = False) -> set[str]:
    server = build_mcp_server(_Client(), role=role, disputes=disputes)
    return {t.name for t in server._tool_manager.list_tools()}


def test_the_mcp_tool_list_follows_the_baked_role():
    assert _listed(SessionRole.AUTHOR) == {"author_check", "author_crosscheck", "freeze_propose"}
    assert _listed(SessionRole.REFERENCE) == set()
    assert _listed(SessionRole.IMPLEMENTER) == {"gate_run", "gate_status"}
    assert _listed(SessionRole.IMPLEMENTER, disputes=True) == {
        "gate_run",
        "gate_status",
        "gate_dispute",
    }
    for role in SessionRole:
        assert _listed(role, disputes=True) == set(tool_names(role, disputes=True))


def test_an_author_never_lists_run_or_dispute_even_if_disputes_are_on():
    listed = _listed(SessionRole.AUTHOR, disputes=True)
    assert not listed & {"gate_run", "gate_status", "gate_dispute"}


def _argv(role: SessionRole, tmp_path: Path) -> list[str]:
    proj = tmp_path / "proj"
    (proj / "acceptance").mkdir(parents=True, exist_ok=True)
    fd = profiles.open_acceptance(proj)
    try:
        return profiles.agent(
            bwrap="bwrap",
            projection=proj,
            sockets=[],
            home=tmp_path / "home",
            read_only=[],
            env=[],
            command=["true"],
            acceptance_fd=fd,
            acceptance_mode=acceptance_mount(role),
        )
    finally:
        import os

        os.close(fd)


def test_mounts_follow_the_role(tmp_path):
    acc = str(tmp_path / "proj" / "acceptance")
    implementer = _argv(SessionRole.IMPLEMENTER, tmp_path)
    assert "--ro-bind-fd" in implementer and acc in implementer
    author = _argv(SessionRole.AUTHOR, tmp_path)
    assert "--ro-bind-fd" not in author and acc not in author  # writable via the projection bind
    reference = _argv(SessionRole.REFERENCE, tmp_path)
    assert "--ro-bind-fd" not in reference
    assert reference[reference.index("--tmpfs", reference.index(acc) - 2) + 1] == acc


def test_a_reference_session_cannot_read_hidden_tests_canary(tmp_path):
    """The reference mount masks acceptance/, so the canary is not visible in the built view."""
    proj = tmp_path / "proj"
    (proj / "acceptance").mkdir(parents=True, exist_ok=True)
    (proj / "acceptance" / "test_hidden.py").write_text("CANARY-REF-91c2")
    reference = _argv(SessionRole.REFERENCE, tmp_path)
    i = reference.index(str(proj / "acceptance"))
    assert reference[i - 1] == "--tmpfs"  # an empty tmpfs lands on top of the tree
    assert acceptance_mount(SessionRole.REFERENCE) == "hidden"
