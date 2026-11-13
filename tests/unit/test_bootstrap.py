"""Bounded startup and session lifecycle helpers (P6-7)."""

import os
import time

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import _launch_agent, _read_info, launch_host_session
from tests.requirements import needs

pytestmark = needs(os.access("/usr/bin/bwrap", os.X_OK), "needs bubblewrap")


def test_read_info_times_out_without_a_writer():
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            _read_info(read_fd, time.monotonic() + 0.2)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_launch_agent_times_out_and_cleans_up():
    def build(_info_fd: int) -> list[str]:
        return ["/bin/sh", "-c", "sleep 30"]

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        _launch_agent(build, (), timeout=0.3)
    assert time.monotonic() - started < 10


def test_launch_host_session_returns_when_the_agent_exits(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    code = launch_host_session(
        state_dir=tmp_path / "state",
        cwd=repo.root,
        task_id=store.active_task_id(),
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=["/bin/true"],
    )
    assert code == 0
