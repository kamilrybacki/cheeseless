"""Helpers that drive the ci-export / ci-judge CLI against the toy repository."""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.ci.github_ledger import TransportError, UpdateResult
from cheeseless.adapters.outbound.ci.ledger_counter import LedgerCounter
from cheeseless.bootstrap import main
from cheeseless.domain.budget_ledger import (
    RECORD_FILE,
    LedgerCommit,
    LedgerIdentity,
    branch_for,
    derive_key,
    seed_record,
)
from cheeseless.domain.evaluation import SuiteRun, TestOutcome
from cheeseless.ports.runner import RunnerOutput
from cheeseless.ports.sandbox import StepOutcome
from tests.toy import commit_all, freeze_without_approval, git, write_files


@dataclass(frozen=True)
class CiWorld:
    toy: Path
    state: Path
    bundle: Path
    base: str
    ledger: FakeLedgerTransport
    root: str
    monkeypatch: pytest.MonkeyPatch


PROBE_ROOT = "0" * 40
_RUN_IDS = itertools.count(1000)


def export_argv(state: Path, out: Path, root: str = PROBE_ROOT) -> list[str]:
    return [
        "--state-dir",
        str(state),
        "ci-export",
        "--out",
        str(out),
        "--budget-repo",
        LEDGER_REPO,
        "--budget-root",
        root,
    ]


def prepare_world(toy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    """Freeze the toy, seed a fake ledger with the store key and export a bundle pinning it."""
    state = tmp_path / "state"
    monkeypatch.chdir(toy)
    assert freeze_without_approval(str(state)) == 0
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o700)
    # The seed MAC needs the store key, which only the exported bundle reveals.
    probe = secrets_dir / "probe.json"
    assert main(export_argv(state, probe)) == 0
    data = json.loads(probe.read_bytes())
    probe.unlink()
    ledger = FakeLedgerTransport()
    ident = LedgerIdentity(repo_id=JUDGED_REPO_ID, lineage=data["lineage"])
    root = ledger.seed(base64.b64decode(data["key"]), ident)
    bundle = secrets_dir / "bundle.json"
    assert main(export_argv(state, bundle, root)) == 0
    return CiWorld(toy, state, bundle, git(toy, "rev-parse", "HEAD"), ledger, root, monkeypatch)


def make_head(world: CiWorld, files: dict[str, str], branch: str = "pr") -> str:
    git(world.toy, "checkout", "-q", "-b", branch, world.base)
    write_files(world.toy, files)
    return commit_all(world.toy, "pr")


def judge_cli(
    world: CiWorld,
    head: str,
    *extra: str,
    bundle: Path | None = None,
    base: str | None = None,
) -> int:
    """Run ci-judge as one workflow run attempt: a fresh run id, the fake ledger, a fake token."""
    set_judge_event(world)
    argv = ["ci-judge", "--base", base or world.base, "--head", head, "--bundle-file"]
    argv.append(str(bundle or world.bundle))
    return main([*argv, *extra])


def set_judge_event(world: CiWorld, *, run_id: str | None = None, attempt: str = "1") -> None:
    """The judge step's env: event ids and the budget token; the transport is the fake."""
    from cheeseless import bootstrap_ci

    mp = world.monkeypatch
    mp.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    mp.setenv("GITHUB_REPOSITORY_ID", JUDGED_REPO_ID)
    mp.setenv("GITHUB_RUN_ID", run_id or str(next(_RUN_IDS)))
    mp.setenv("GITHUB_RUN_ATTEMPT", attempt)
    mp.setattr(bootstrap_ci, "make_ledger_transport", lambda repo, token: world.ledger)


def holdout_ids(world: CiWorld) -> list[str]:
    data = json.loads(world.bundle.read_bytes())
    return list(data["task"]["holdout"])


# -- fake ledger transport (CI budget counter) ---------------------------------

LEDGER_REPO = "example-owner/cheeseless-ledger"
JUDGED_REPO_ID = "123456789"
FAKE_BUDGET_TOKEN = "ghs_" + "FAKE0budget0token0for0tests"  # obviously fake


class FakeLedgerTransport:
    """In-memory ledger repository with a strict fast-forward check and scriptable faults.

    ``fail_reads`` makes the next N reads raise a 5xx. ``update_script`` pops one action per
    ``update_ref`` call: ``"ok"`` (normal), ``"lost"`` (applied, then the response is lost),
    ``"drop"`` (not applied, 5xx), ``"race"`` (pops a callable from ``racers`` and runs it
    first, typically another judge's reserve, so ours is no longer a fast-forward).
    ``fail_writes`` makes the next N ``write_commit`` calls raise.

    Seeding: ``create_script`` pops one action per ``create_ref`` call: ``"ok"``, ``"lost"``
    (created, then the response is lost), ``"drop"`` (not created, 5xx), ``"ruleset"``
    (refused by a ruleset, nothing created) or ``"race"`` (runs a racer first).
    ``rewrite_message`` lets a test model a server that normalises commit messages.
    """

    def __init__(self) -> None:
        self.commits: dict[str, LedgerCommit] = {}
        self.refs: dict[str, str] = {}
        self.fail_reads = 0
        self.fail_writes = 0
        self.update_script: list[str] = []
        self.racers: list[Callable[[], object]] = []
        self.updates_sent = 0
        self.create_script: list[str] = []
        self.creates_sent = 0
        self.rewrite_message: Callable[[bytes], bytes] | None = None
        self.reads = 0
        self._serial = 0

    # -- helpers for tests ---------------------------------------------------

    def add_commit(
        self, parents: tuple[str, ...], message: bytes, files: Mapping[str, bytes] | None = None
    ) -> str:
        self._serial += 1
        seed = b"%d|" % self._serial + b"|".join(p.encode() for p in parents) + message
        sha = hashlib.sha256(seed).hexdigest()[:40]
        tree = dict(files) if files is not None else {RECORD_FILE: message}
        self.commits[sha] = LedgerCommit(sha=sha, parents=parents, message=message, files=tree)
        return sha

    def seed(self, store_key: bytes, ident: LedgerIdentity) -> str:
        root = self.add_commit((), seed_record(derive_key(store_key), ident))
        self.refs[branch_for(ident.lineage)] = root
        return root

    def append(self, branch: str, message: bytes, files: Mapping[str, bytes] | None = None) -> str:
        """Append a commit directly, as another writer would (no fast-forward check needed)."""
        sha = self.add_commit((self.refs[branch],), message, files)
        self.refs[branch] = sha
        return sha

    def chain(self, branch: str) -> list[LedgerCommit]:
        out, sha = [], self.refs.get(branch)
        while sha is not None:
            commit = self.commits[sha]
            out.append(commit)
            sha = commit.parents[0] if commit.parents else None
        return out

    def _fault(self) -> None:
        if self.fail_reads > 0:
            self.fail_reads -= 1
            raise TransportError("ledger read failed: HTTP 502")

    # -- LedgerTransport -----------------------------------------------------

    def get_ref(self, branch: str) -> str | None:
        self.reads += 1
        self._fault()
        return self.refs.get(branch)

    def get_commit(self, sha: str) -> LedgerCommit:
        self.reads += 1
        self._fault()
        if sha not in self.commits:
            raise TransportError("ledger read failed: HTTP 404")
        return self.commits[sha]

    def write_commit(self, *, parent: str, message: bytes, files: Mapping[str, bytes]) -> str:
        if self.fail_writes > 0:
            self.fail_writes -= 1
            raise TransportError("ledger write failed: HTTP 500")
        return self.add_commit((parent,), message, files)

    def update_ref(self, branch: str, sha: str) -> UpdateResult:
        self.updates_sent += 1
        action = self.update_script.pop(0) if self.update_script else "ok"
        if action == "drop":
            raise TransportError("ledger update failed: HTTP 503")
        if action == "race":
            self.racers.pop(0)()
        if branch not in self.refs:
            raise TransportError("ledger update failed: HTTP 404")
        if not self._descends(sha, self.refs[branch]):
            return UpdateResult.REJECTED
        self.refs[branch] = sha
        if action == "lost":
            raise TransportError("ledger update timed out")
        return UpdateResult.UPDATED

    # -- LedgerSeeder --------------------------------------------------------

    def write_root(self, *, message: bytes, files: Mapping[str, bytes]) -> str:
        if self.rewrite_message is not None:
            message = self.rewrite_message(message)
        return self.add_commit((), message, files)

    def create_ref(self, branch: str, sha: str) -> UpdateResult:
        self.creates_sent += 1
        action = self.create_script.pop(0) if self.create_script else "ok"
        if action == "drop":
            raise TransportError("ledger ref create failed: HTTP 503")
        if action == "ruleset":
            return UpdateResult.REJECTED
        if action == "race":
            self.racers.pop(0)()
        if sha not in self.commits:
            raise TransportError("ledger ref create failed: HTTP 422")
        if branch in self.refs:
            return UpdateResult.REJECTED
        self.refs[branch] = sha
        if action == "lost":
            raise TransportError("ledger ref create timed out")
        return UpdateResult.UPDATED

    def _descends(self, sha: str, ancestor: str) -> bool:
        current: str | None = sha
        while current is not None:
            if current == ancestor:
                return True
            parents = self.commits[current].parents
            current = parents[0] if parents else None
        return False


LEDGER_STORE_KEY = b"\x5a" * 32  # obviously fake store key
LEDGER_IDENT = LedgerIdentity(repo_id=JUDGED_REPO_ID, lineage="toy-task")
CLAIM_A = "a" * 32
CLAIM_B = "b" * 32


def ledger_counter(
    transport: FakeLedgerTransport,
    root: str,
    *,
    run_key: str = "1001/1",
    claim: str = CLAIM_A,
    limit: int = 5,
) -> LedgerCounter:
    return LedgerCounter(
        transport,
        store_key=LEDGER_STORE_KEY,
        identity=LEDGER_IDENT,
        branch=branch_for(LEDGER_IDENT.lineage),
        root=root,
        limit=limit,
        run_key=run_key,
        claim=claim,
    )


# -- sandbox and runner fakes (ordering tests only; never run candidate code) ----


class FakeSandbox:
    """No isolation at all: only for ordering tests, which never execute candidate code."""

    def __init__(self, build: str = "ok"):
        self.build_mode = build

    @contextmanager
    def _tmp(self):
        with tempfile.TemporaryDirectory(prefix="cheeseless-test-") as tmp:
            yield Path(tmp)

    def trusted_scratch(self):
        return self._tmp()

    def scratch(self):
        return self._tmp()

    def build(self, workdir, steps, env):
        if self.build_mode == "raise":
            raise RuntimeError("build sandbox broke")
        return StepOutcome(
            ok=self.build_mode == "ok", exit_code=0 if self.build_mode == "ok" else 1
        )

    def launcher(self, workdir, env, scratch):
        return ("false",)


GREEN_SMOKE = SuiteRun(outcomes={"contract/test_cli.py::test_runs": TestOutcome.PASSED})
RED_SMOKE = SuiteRun(outcomes={"contract/test_cli.py::test_runs": TestOutcome.FAILED})


class FakeRunner:
    """Scripted smoke and hidden results; an ``Exception`` value is raised instead."""

    def __init__(self, smoke=GREEN_SMOKE, hidden=None):
        self.smoke, self.hidden = smoke, hidden
        self.hidden_calls = 0

    def run(self, root, paths, *, selected, launcher, run, timeout):
        if list(paths) == ["hidden"]:
            self.hidden_calls += 1
            result = self.hidden
        else:
            result = self.smoke
        if isinstance(result, Exception):
            raise result
        return RunnerOutput(suite=result)
