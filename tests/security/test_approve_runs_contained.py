"""ADR 0009: approval executes the author's proposed tests only inside the author sandbox.

A hostile acceptance suite (conftest at import time plus a test body) tries to read the store's
hmac key, plant canary files in the store and in $HOME, and open a network socket. Approval must
leave no trace on the host. A second test proves the probes really ran inside the sandbox, so a
no-op suite cannot make the first one pass.
"""

import shutil
import socket
import threading
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.author_driver import DRIVER
from tests.toy import TOY_FILES, make_repo

needs_bwrap = pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)


def _conftest(key: Path, store: Path, home: Path, port: int) -> str:
    return f"""\
import socket
from pathlib import Path

LINES = []


def probe(tag):
    seen = []
    try:
        seen.append("read=LEAK:" + Path({str(key)!r}).read_bytes().decode("latin-1"))
    except OSError:
        seen.append("read=blocked")
    for target in ({str(store / "planted")!r}, {str(home / "planted")!r}):
        try:
            Path(target).write_text("planted")
            seen.append("write=LEAK")
        except OSError:
            seen.append("write=blocked")
    try:
        socket.create_connection(("127.0.0.1", {port}), timeout=2).close()
        seen.append("net=LEAK")
    except OSError:
        seen.append("net=blocked")
    LINES.append("PROBE-EXECUTED " + tag + " " + " ".join(seen))


probe("import")


def pytest_terminal_summary(terminalreporter):
    for line in LINES:
        terminalreporter.write_line(line)


import pytest


@pytest.fixture(autouse=True)
def _body_probe():
    probe("body")
    yield
"""


class _Host:
    """A world with a store, a hostile accepted suite and a host-side listener."""

    def __init__(self, tmp_path: Path) -> None:
        home = tmp_path / "_env" / "home"
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.accepted: list[object] = []
        threading.Thread(target=self._accept, daemon=True).start()
        repo_root = tmp_path / "hostile"
        make_repo(repo_root, TOY_FILES)  # placeholder commit so the store path exists
        repo = GitRepository.discover(repo_root)
        self.store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
        self.key = self.store.hmac_key()
        conftest = _conftest(
            self.store.root / "hmac.key",
            self.store.root,
            home,
            self.listener.getsockname()[1],
        )
        files = {**TOY_FILES, "acceptance/hidden/conftest.py": conftest}
        from tests.toy import commit_all, write_files

        write_files(repo_root, files)
        commit_all(repo_root, "hostile acceptance")
        self.repo = repo
        self.home = home
        self.tmp = tmp_path

    def _accept(self) -> None:
        while True:
            try:
                self.accepted.append(self.listener.accept())
            except OSError:
                return

    def sandbox(self) -> BwrapSandbox:
        return BwrapSandbox(protected=[self.store.root, self.repo.root])


@pytest.fixture
def host(tmp_path):
    h = _Host(tmp_path)
    yield h
    h.listener.close()


def _source(repo: GitRepository) -> str:
    return repo.tree_of(repo.head_commit())


@needs_bwrap
def test_approval_never_runs_proposed_code_in_the_host_context(host):
    proposal = uc.freeze_propose(host.repo, host.store, _source(host.repo))
    sandbox = host.sandbox()
    result = uc.approve_freeze(
        host.repo,
        sandbox,
        sandbox,
        host.store,
        proposal.proposal_id,
        actor="tester",
        no_reference=True,
        reason="contained-run security test",
    )
    assert result.task_id == proposal.proposal_id
    assert not (host.store.root / "planted").exists()
    assert not (host.home / "planted").exists()
    assert host.accepted == []
    haystack = repr(result) + "".join(
        p.read_text(errors="replace") for p in host.store.root.rglob("*") if p.is_file()
    )
    assert "LEAK" not in haystack
    assert host.key.decode("latin-1") not in haystack
    assert host.key.hex() not in haystack


@needs_bwrap
def test_the_hostile_probes_really_execute_inside_the_sandbox(host):
    """Canary: the same suite, driven directly, runs its probes and every one is blocked."""
    import json
    import sys
    import tempfile

    sandbox = host.sandbox()
    repo = host.repo
    with tempfile.TemporaryDirectory(prefix="cheeseless-probe-") as tmp:
        acceptance = Path(tmp) / "acceptance"
        repo.export(repo.tree_of(_source(repo), "acceptance"), acceptance)
        command = (sys.executable, "-I", "-B", "-c", DRIVER, "collect", json.dumps([]))
        outcome = sandbox.author_run(acceptance, None, command, env=(), timeout=120.0)
    text = outcome.stdout + outcome.stderr_tail
    assert "PROBE-EXECUTED import" in text
    assert "read=blocked" in text and "write=blocked" in text and "net=blocked" in text
    assert "LEAK" not in text and host.key.decode("latin-1") not in text
    assert host.accepted == []


@needs_bwrap
def test_the_test_body_probe_executes_contained_in_a_real_run(host):
    """A run with a reference executes test bodies; their probes are blocked too."""
    import json
    import sys
    import tempfile

    sandbox = host.sandbox()
    repo = host.repo
    with tempfile.TemporaryDirectory(prefix="cheeseless-probe-") as tmp:
        acceptance = Path(tmp) / "acceptance"
        reference = Path(tmp) / "reference"
        reference.mkdir()
        repo.export(repo.tree_of(_source(repo), "acceptance"), acceptance)
        command = (sys.executable, "-I", "-B", "-c", DRIVER, "run", json.dumps([sys.executable]))
        outcome = sandbox.author_run(acceptance, reference, command, env=(), timeout=120.0)
    text = outcome.stdout + outcome.stderr_tail
    assert "PROBE-EXECUTED import" in text and "PROBE-EXECUTED body" in text
    body = [line for line in text.splitlines() if line.startswith("PROBE-EXECUTED body")]
    assert body and all(
        "read=blocked" in ln and "write=blocked" in ln and "net=blocked" in ln for ln in body
    )
    assert "LEAK" not in text and host.key.decode("latin-1") not in text
    assert not (host.store.root / "planted").exists() and not (host.home / "planted").exists()
    assert host.accepted == []
