"""A signalled ``cheeseless session`` must still tear down (P11-7).

SIGTERM or SIGHUP to the host process used to kill it without running the
``host_session`` teardown: the throwaway HOME (with seeded agent credentials),
the egress CA private key and the socket directories stayed behind in TMPDIR.
"""

import os
import signal
import subprocess  # noqa: S404 - the session runs as a real child process
import sys
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from tests.requirements import needs
from tests.toy import CANARY, make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)

SEED_SECRET = "seeded-credential-value-for-teardown-test"  # noqa: S105 - fake marker
CLI = "import sys; from cheeseless.bootstrap import main; raise SystemExit(main())"
AGENT = (
    "import os, sys, time\n"
    "open(os.path.join(os.getcwd(), 'agent-up'), 'w').close()\n"
    "time.sleep(120)\n"
)


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    root = tmp_path_factory.mktemp("signal-fixture")
    repo = GitRepository.discover(make_repo(root / "toy"))
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return repo, root / "state", store.active_task_id()


def _wait_for(path: Path, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise AssertionError(f"session exited early: {proc.communicate()}")
        time.sleep(0.1)
    raise AssertionError("agent never started")


def _processes_mentioning(marker: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() in cmdline:
            found.append(int(entry.name))
    return found


def _tree_contains(root: Path, needle: str) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        try:
            if path.is_file() and needle.encode() in path.read_bytes():
                hits.append(str(path))
        except OSError:
            continue
    return hits


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_signalled_session_removes_home_seed_ca_and_holder(frozen, tmp_path, signum):
    repo, state, task_id = frozen
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    projection = tmp_path / "projection"
    seed = tmp_path / "seed.json"
    seed.write_text(SEED_SECRET, encoding="ascii")
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    proc = subprocess.Popen(  # noqa: S603 - fixed test argv
        [
            sys.executable,
            "-c",
            CLI,
            "session",
            "--state-dir",
            str(state),
            "--task",
            task_id,
            "--projection",
            str(projection),
            "--home-seed",
            f".agent/auth.json={seed}",
            "--",
            "/usr/bin/python3",
            "-c",
            AGENT,
        ],
        cwd=repo.root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(projection / "agent-up", proc)
        homes = list(tmpdir.glob("cheeseless-home-*"))
        assert homes and _tree_contains(homes[0], SEED_SECRET), "seed was not placed"
        proc.send_signal(signum)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 128 + signum, (proc.returncode, out, err)
    assert "Traceback" not in err, err
    leftovers = sorted(p.name for p in tmpdir.iterdir() if p.name.startswith("cheeseless-"))
    assert leftovers == [], leftovers
    assert _tree_contains(tmpdir, SEED_SECRET) == []
    assert list(tmpdir.rglob("mitmproxy-ca.pem")) == []
    assert _processes_mentioning(str(projection)) == []
    assert _tree_contains(projection, CANARY) == []
