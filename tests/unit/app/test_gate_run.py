from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.domain.errors import ErrorCode, GateError
from cheeseless.domain.result import Result
from cheeseless.domain.status import Status
from tests.toy import FIXED_MAIN


def test_gate_run_fail_then_pass(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)

    # Baseline projection fails.
    base_worktree = tmp_path / "base"
    repo.create_projection(prepared.projection_tree, base_worktree)
    v1 = gate_run(repo, store, runner, sandbox, worktree=base_worktree)
    assert v1.outcome.status is Status.FAIL

    # Fixed implementation passes.
    fixed_worktree = tmp_path / "fixed"
    repo.create_projection(prepared.projection_tree, fixed_worktree)
    (fixed_worktree / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    v2 = gate_run(repo, store, runner, sandbox, worktree=fixed_worktree)
    assert isinstance(v2.outcome, Result)
    assert v2.outcome.status is Status.PASS


def test_gate_run_rate_limited_blocks_candidate(toy_repo, tmp_path, monkeypatch):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    # Fill attempt window.
    for _ in range(100):
        import time

        store.record_attempt(store.active_task_id(), time.time(), 60.0)
    base_worktree = tmp_path / "base"
    repo.create_projection(prepared.projection_tree, base_worktree)
    called = {"build": 0}
    orig_build = sandbox.build

    def counting_build(*a, **k):
        called["build"] += 1
        return orig_build(*a, **k)

    monkeypatch.setattr(sandbox, "build", counting_build)
    v = gate_run(repo, store, runner, sandbox, worktree=base_worktree)
    assert isinstance(v.outcome, GateError)
    assert v.outcome.code is ErrorCode.RATE_LIMITED
    assert called["build"] == 0


def test_gate_run_ci_uses_holdout(toy_repo, tmp_path):
    from cheeseless.domain.budget import Pool

    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    fixed_worktree = tmp_path / "fixed"
    repo.create_projection(prepared.projection_tree, fixed_worktree)
    (fixed_worktree / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    local = gate_run(repo, store, runner, sandbox, worktree=fixed_worktree, pool=Pool.LOCAL)
    ci = gate_run(repo, store, runner, sandbox, worktree=fixed_worktree, pool=Pool.CI, profile="ci")
    assert isinstance(local.outcome, Result)
    assert isinstance(ci.outcome, Result)
    # Distinct selected sets yield potentially different criterion outcomes; both pass here.
    assert local.outcome.status is Status.PASS
    assert ci.outcome.status is Status.PASS


def test_pre_hidden_build_tail_is_dlp_scanned(tmp_path):
    from contextlib import contextmanager

    from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
    from cheeseless.domain.dlp import build_fingerprints
    from cheeseless.ports.sandbox import StepOutcome
    from tests.factories import frozen_task

    class Repo:
        def snapshot(self, worktree):
            return "1" * 40

        def export(self, tree, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)

    class Sandbox:
        def __init__(self, tail):
            self.tail = tail

        @contextmanager
        def trusted_scratch(self):
            yield tmp_path / "trusted"

        @contextmanager
        def scratch(self):
            yield tmp_path / "scratch"

        def build(self, *args, **kwargs):
            return StepOutcome(False, 1, self.tail)

        def launcher(self, *args, **kwargs):
            return ()

    task = frozen_task()
    store = FileStore(store_root(tmp_path / "repo.git", tmp_path / "state"))
    store.save_task(task, b"s" * 32)
    store.set_active(task.task.task_id)
    store.save_fingerprints(
        build_fingerprints(
            task.task.task_id,
            [("acceptance/hidden/a.py", b"CHEESELESS-CANARY-abcdef")],
            [],
        )
    )
    from cheeseless.app.dlp import scanner_for

    scanner = scanner_for(store, task.task.task_id)
    leaked = gate_run(
        Repo(),
        store,
        PytestRunner(),
        Sandbox("leak CHEESELESS-CANARY-abcdef"),
        worktree=tmp_path,
        scanner=scanner,
    )
    assert isinstance(leaked.outcome, GateError)
    assert leaked.outcome.code is ErrorCode.BUILD_FAILED
    assert leaked.outcome.pre_hidden_stderr_tail == ""

    clean = gate_run(
        Repo(),
        store,
        PytestRunner(),
        Sandbox("ordinary build failure"),
        worktree=tmp_path,
        scanner=scanner,
    )
    assert isinstance(clean.outcome, GateError)
    assert clean.outcome.code is ErrorCode.BUILD_FAILED
    assert "ordinary build failure" in clean.outcome.pre_hidden_stderr_tail


def test_gate_run_publishes_nothing_when_a_dispute_lands_mid_run(toy_repo, tmp_path):
    """A dispute filed while the suite runs wins: no result is published."""
    from cheeseless.app.dispute import configure_disputes
    from cheeseless.domain.dispute import DisputeCode

    repo = GitRepository.discover(toy_repo)
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, PytestRunner(), sandbox)
    commit(prepared, store)
    configure_disputes(store, enabled=True, cap=3)
    task_id = prepared.task_id

    class DisputingRunner(PytestRunner):
        def run(self, *args, **kwargs):
            out = super().run(*args, **kwargs)
            store.file_dispute(task_id, "toy", "normalize", DisputeCode.UNTESTABLE, "n")
            return out

    worktree = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, worktree)
    verdict = gate_run(repo, store, DisputingRunner(), sandbox, worktree=worktree)
    assert isinstance(verdict.outcome, GateError)
    assert verdict.outcome.code is ErrorCode.SESSION_DISPUTED
    assert store.latest_result(task_id) is None
