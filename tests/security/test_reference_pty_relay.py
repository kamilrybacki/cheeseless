"""ADR 0014 slice 3c: ``host_session`` starts a reference on a pty and relays it.

The real bwrap sandbox runs a Python one-liner that prints a canary. The host connects to
the relay exactly as the pane client does and must receive that canary; the relay socket
must live outside the projection and HOME, and the attempt record must show the attach.
"""

import contextlib
import os
import socket
import time
from pathlib import Path

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.bootstrap import host_session
from cheeseless.domain.reference_attempt import (
    NewReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.session_options import within
from tests.requirements import needs
from tests.toy import make_repo

PYTHON = "/usr/bin/python3"
pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access(PYTHON, os.X_OK),
    "bwrap and a system python are required",
)

LINEAGE = "toy"
TASK = "a" * 64
DIGEST = "b" * 64
ATTEMPT = "att-pty"
CANARY = "canary-pane-output"


def _store(repo: GitRepository, tmp_path: Path) -> FileStore:
    return FileStore(store_root(repo.common_dir(), tmp_path / "state"))


def test_the_host_relay_carries_the_sandbox_pty_to_one_client(tmp_path):
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    store = _store(repo, tmp_path)
    script = f"import time; print({CANARY!r}, flush=True); time.sleep(30)"
    with host_session(
        state_dir=tmp_path / "state",
        cwd=repo.root,
        task_id=TASK,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=[PYTHON, "-I", "-c", script],
        role="reference",
        new_reference=NewReferenceAttempt(ATTEMPT, LINEAGE, DIGEST, ReferenceMode.TUI),
        agent_pty=(24, 80),
    ) as info:
        relay = info["relay"]
        assert relay is not None
        attempt = store.reference_attempt(ATTEMPT)
        assert attempt is not None and attempt.state is ReferenceState.RUNNING
        assert not within(relay.socket_path, info["projection_dir"])
        assert not within(relay.socket_path, info["home_dir"])

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        try:
            client.connect(str(relay.socket_path))
            # ADR 0019: a reference pane is view-only, so the viewer sends no frame at all.
            got, deadline = b"", time.monotonic() + 10
            while CANARY.encode() not in got and time.monotonic() < deadline:
                with contextlib.suppress(TimeoutError):
                    got += client.recv(4096)
            assert CANARY.encode() in got
        finally:
            client.close()
        attached = store.reference_attempt(ATTEMPT)
        assert attached is not None and attached.attached is True
