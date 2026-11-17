"""Review items 2 and 3: a session's role and generation are bound per session, on the real path.

Every session goes through ``host_session`` (the path ``cheeseless session`` takes), never a
hand-built config.
"""

import io
import json
import os
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.uds import server as uds_server
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from cheeseless.domain.errors import ErrorCode, gate_error
from tests.requirements import needs
from tests.toy import make_repo

needs_bwrap = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)
pytestmark = needs_bwrap

TID = "a" * 64


def _wire(server, path: str, task_id: str, claimed: str | None = None) -> bytes:
    out = io.BytesIO()
    claim = claimed or server.session.role
    body = json.dumps({"taskId": task_id}).encode()
    server._route(out, path, body, claimed_role=claim)  # noqa: SLF001
    return out.getvalue()


def _refused(raw: bytes) -> bool:
    return ErrorCode.ROLE_NOT_ALLOWED.value.encode() in raw


def _open(stack: ExitStack, tmp: Path, repo: Path, name: str, role: str, task: str) -> dict:
    return stack.enter_context(
        host_session(
            state_dir=tmp / "state",
            cwd=repo,
            task_id=task,
            projection_dir=tmp / f"proj-{name}",
            socket_dir=tmp / f"sock-{name}",
            agent_command=["/bin/sleep", "60"],
            role=role,
        )
    )


@pytest.fixture
def frozen(tmp_path: Path):
    repo = GitRepository.discover(make_repo(tmp_path / "toy"))
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return SimpleNamespace(repo=repo, store=store, task=store.active_task_id())


def _reject(store: FileStore, task: str) -> None:
    store._reopen(  # noqa: SLF001 - what a rejected dispute does, minus the ledger
        SimpleNamespace(task_id=task, dispute_id="d1", resolved_by="op")
    )


def test_a_host_session_from_before_a_rejection_is_refused_after_it(frozen, tmp_path):
    """G12 on the real path: host_session binds the generation at launch."""
    with ExitStack() as stack:
        info = _open(stack, tmp_path, frozen.repo.root, "impl", "implementer", frozen.task)
        server = info["server"]
        assert not _refused(_wire(server, "/status", frozen.task))
        _reject(frozen.store, frozen.task)
        for path in ("/status", "/run", "/dispute"):
            assert _refused(_wire(server, path, frozen.task)), path


def test_a_session_launched_after_the_rejection_is_served(frozen, tmp_path):
    _reject(frozen.store, frozen.task)
    with ExitStack() as stack:
        info = _open(stack, tmp_path, frozen.repo.root, "impl", "implementer", frozen.task)
        assert not _refused(_wire(info["server"], "/status", frozen.task))


def test_a_config_without_a_generation_or_session_id_fails_closed(frozen, tmp_path):
    with ExitStack() as stack:
        server = _open(stack, tmp_path, frozen.repo.root, "impl", "implementer", frozen.task)[
            "server"
        ]
        whole = server.session
        for field in ("generation", "session_id"):
            server.session = replace(whole, **{field: None})
            assert _refused(_wire(server, "/status", frozen.task)), field
        server.session = whole
        assert not _refused(_wire(server, "/status", frozen.task))


def test_author_stays_live_across_a_reference_launch(tmp_path, monkeypatch):
    """The happy path: the author runs author_crosscheck after the reference has launched."""
    repo = make_repo(tmp_path / "toy")
    monkeypatch.setattr(
        uds_server, "gate_author_crosscheck", lambda *a, **k: gate_error(ErrorCode.INFRA_ERROR)
    )
    with ExitStack() as stack:
        author = _open(stack, tmp_path, repo, "author", "author", TID)["server"]
        assert not _refused(_wire(author, "/author-crosscheck", TID))
        reference = _open(stack, tmp_path, repo, "ref", "reference", TID)["server"]
        # the author keeps its own role: crosscheck allowed, gate_run still refused
        assert not _refused(_wire(author, "/author-crosscheck", TID))
        for path in ("/run", "/status", "/dispute"):
            assert _refused(_wire(author, path, TID)), path
        # the reference still has no route of its own
        for path in ("/run", "/status", "/dispute", "/author-check", "/author-crosscheck"):
            assert _refused(_wire(reference, path, TID)), path


def test_a_session_record_is_removed_when_the_session_ends(frozen, tmp_path):
    with ExitStack() as stack:
        info = _open(stack, tmp_path, frozen.repo.root, "impl", "implementer", frozen.task)
        sid = info["server"].session.session_id
        assert frozen.store.session_record(sid) is not None
    assert frozen.store.session_record(sid) is None
