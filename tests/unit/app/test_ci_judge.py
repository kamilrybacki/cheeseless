import os

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.ci_judge import FreezeDrift, JudgeReason, judge
from cheeseless.app.freeze import commit, prepare
from cheeseless.domain.budget import Pool
from cheeseless.domain.evaluation import SuiteRun
from cheeseless.domain.status import Status
from cheeseless.ports.run_counter import CounterError
from tests.ci_support import RED_SMOKE, FakeRunner, FakeSandbox
from tests.requirements import needs
from tests.toy import FIXED_MAIN, commit_all, git, write_files

BWRAP = os.access("/usr/bin/bwrap", os.X_OK)


class Spy:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if callable(attr):

            def counted(*a, **k):
                self.calls += 1
                return attr(*a, **k)

            return counted
        return attr


@pytest.fixture
def world(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner, sandbox = PytestRunner(), BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    frozen, _ = store.load_task(store.active_task_id())
    base = repo.head_commit()
    return repo, runner, sandbox, store, frozen, base, toy_repo


def _head(toy_repo, files):
    git(toy_repo, "checkout", "-q", "-b", "pr")
    write_files(toy_repo, files)
    commit_all(toy_repo, "pr")
    return git(toy_repo, "rev-parse", "HEAD").strip()


def test_protected_path_fails_without_running_or_charging(world):
    repo, runner, sandbox, store, frozen, base, toy = world
    head = _head(toy, {"acceptance/hidden/test_extra.py": "def test_x():\n    pass\n"})
    r, s = Spy(runner), Spy(sandbox)
    v = judge(
        repo, r, s, frozen, store.hmac_key(), counter=store, base_commit=base, head_commit=head
    )
    assert v.status is Status.FAIL and v.reason == JudgeReason.PROTECTED_PATH
    assert r.calls == 0 and s.calls == 0
    assert (
        store.budget(
            frozen.task.lineage_id,
            Pool.CI,
        ).used
        == 0
    )


def test_workflow_change_is_protected(world):
    repo, runner, sandbox, store, frozen, base, toy = world
    head = _head(toy, {".github/workflows/x.yml": "on: push\n"})
    v = judge(
        repo,
        runner,
        sandbox,
        frozen,
        store.hmac_key(),
        counter=store,
        base_commit=base,
        head_commit=head,
    )
    assert v.reason == JudgeReason.PROTECTED_PATH


def test_freeze_drift_raises(world):
    repo, runner, sandbox, store, frozen, base, toy = world
    write_files(toy, {"acceptance/hidden/test_drift.py": "def test_d():\n    pass\n"})
    drifted = commit_all(toy, "drift")
    git(toy, "checkout", "-q", "-b", "pr", base)
    head = _head_noop(toy)
    with pytest.raises(FreezeDrift):
        judge(
            repo,
            runner,
            sandbox,
            frozen,
            store.hmac_key(),
            counter=store,
            base_commit=drifted,
            head_commit=head,
        )


def _head_noop(toy):
    write_files(toy, {"src/toy/note.txt": "x\n"})
    commit_all(toy, "note")
    return git(toy, "rev-parse", "HEAD").strip()


@needs(BWRAP, "needs bubblewrap")
def test_unfixed_fails_fixed_passes_and_public_form_is_minimal(world):
    from cheeseless.domain.result import verify

    repo, runner, sandbox, store, frozen, base, toy = world
    key = store.hmac_key()
    bad = judge(
        repo,
        runner,
        sandbox,
        frozen,
        key,
        counter=store,
        base_commit=base,
        head_commit=_head_noop(toy),
    )
    assert bad.status is Status.FAIL
    good_head = _head(toy, {"src/toy/__main__.py": FIXED_MAIN})
    good = judge(
        repo, runner, sandbox, frozen, key, counter=store, base_commit=base, head_commit=good_head
    )
    assert good.status is Status.PASS
    assert good.result is not None and verify(good.result, key)
    assert good.result.tree_hash == repo.tree_of(good_head) == good.tree_hash
    pub = good.public()
    assert set(pub) == {"status", "criteria"}
    visible_ids = set(frozen.visible.values())
    assert {c["id"] for c in pub["criteria"]} == visible_ids
    text = str(pub)
    assert not any(t in text for t in frozen.holdout)


@needs(BWRAP, "needs bubblewrap")
def test_budget_exhausted_blocks(world):
    repo, runner, sandbox, store, frozen, base, toy = world
    while store.reserve_run(frozen.task.lineage_id, Pool.CI) is not None:
        pass
    head = _head(toy, {"src/toy/__main__.py": FIXED_MAIN})
    v = judge(
        repo,
        runner,
        sandbox,
        frozen,
        store.hmac_key(),
        counter=store,
        base_commit=base,
        head_commit=head,
    )
    assert v.status is Status.BLOCKED and v.reason == JudgeReason.BUDGET_EXHAUSTED


class _NoSandbox:
    def __getattr__(self, name):
        raise RuntimeError(f"sandbox used: {name}")


def test_protected_diff_uses_merge_base_not_default_tip(world):
    # main gains a workflow after the PR diverged; the PR does not touch it.
    repo, runner, _sandbox, store, frozen, base, toy = world
    head = _head(toy, {"src/toy/note.txt": "x\n"})
    git(toy, "checkout", "-q", "main")
    write_files(toy, {".github/workflows/late.yml": "on: push\n"})
    tip = commit_all(toy, "late workflow")
    v = judge(
        repo,
        runner,
        _NoSandbox(),
        frozen,
        store.hmac_key(),
        counter=store,
        base_commit=tip,
        head_commit=head,
    )
    assert v.reason == JudgeReason.INFRA_ERROR  # got past the protected check, then fails closed


def test_protected_change_still_caught_when_pr_matches_default_tip(world):
    # main changes a workflow; the PR makes the identical change: still a PR touch.
    repo, runner, _sandbox, store, frozen, base, toy = world
    head = _head(toy, {".github/workflows/same.yml": "on: push\n"})
    git(toy, "checkout", "-q", "main")
    write_files(toy, {".github/workflows/same.yml": "on: push\n"})
    tip = commit_all(toy, "same workflow")
    v = judge(
        repo,
        runner,
        _NoSandbox(),
        frozen,
        store.hmac_key(),
        counter=store,
        base_commit=tip,
        head_commit=head,
    )
    assert v.reason == JudgeReason.PROTECTED_PATH


def test_protected_touch_reverted_within_the_pr_is_judged_on_the_net_tree(world):
    # Policy (ledger P10-20): the judge protects the tree that is judged and merged, not history.
    repo, runner, _sandbox, store, frozen, base, toy = world
    _head(toy, {".github/workflows/tmp.yml": "on: push\n"})
    (toy / ".github" / "workflows" / "tmp.yml").unlink()
    head = commit_all(toy, "revert")
    v = judge(
        repo,
        runner,
        _NoSandbox(),
        frozen,
        store.hmac_key(),
        counter=store,
        base_commit=base,
        head_commit=head,
    )
    assert v.reason == JudgeReason.INFRA_ERROR  # past the protected check, then fails closed


# -- ordering: when the CI budget is charged (spec section 4) -------------------


class RecordingCounter:
    """``RunCounter`` that records every call; delegates to ``inner`` or raises ``error``."""

    def __init__(self, inner, error: Exception | None = None):
        self.inner = inner
        self.error = error
        self.calls: list[str] = []

    def reserve_run(self, lineage_id, pool):
        self.calls.append("reserve_run")
        assert pool is Pool.CI
        if self.error is not None:
            raise self.error
        return self.inner.reserve_run(lineage_id, pool)

    def budget(self, lineage_id, pool):
        self.calls.append("budget")
        return self.inner.budget(lineage_id, pool)


def _judge(world, runner, sandbox, counter, head):
    repo, _r, _s, store, frozen, base, _toy = world
    return judge(
        repo,
        runner,
        sandbox,
        frozen,
        store.hmac_key(),
        counter=counter,
        base_commit=base,
        head_commit=head,
    )


def _used(world) -> int:
    _repo, _r, _s, store, frozen, _base, _toy = world
    return store.budget(frozen.task.lineage_id, Pool.CI).used


@pytest.mark.parametrize(
    ("runner", "sandbox", "status", "reason"),
    [
        (FakeRunner(), FakeSandbox("raise"), Status.BLOCKED, JudgeReason.INFRA_ERROR),
        (FakeRunner(), FakeSandbox("fail"), Status.FAIL, JudgeReason.BUILD_FAILED),
        (
            FakeRunner(smoke=RuntimeError("x")),
            FakeSandbox(),
            Status.BLOCKED,
            JudgeReason.INFRA_ERROR,
        ),
        (FakeRunner(smoke=RED_SMOKE), FakeSandbox(), Status.FAIL, JudgeReason.SMOKE_FAILED),
        (FakeRunner(smoke=SuiteRun()), FakeSandbox(), Status.FAIL, JudgeReason.SMOKE_FAILED),
    ],
    ids=["build-raises", "build-fails", "smoke-raises", "smoke-fails", "smoke-empty"],
)
def test_pre_hidden_failures_never_touch_the_counter(world, runner, sandbox, status, reason):
    counter = RecordingCounter(world[3])
    head = _head(world[6], {"src/toy/note.txt": "x\n"})
    v = _judge(world, runner, sandbox, counter, head)
    assert (v.status, v.reason) == (status, reason)
    assert counter.calls == []
    assert runner.hidden_calls == 0
    assert _used(world) == 0


def test_protected_path_never_touches_the_counter(world):
    counter = RecordingCounter(world[3])
    head = _head(world[6], {"acceptance/hidden/test_extra.py": "def test_x():\n    pass\n"})
    runner = FakeRunner()
    v = _judge(world, runner, FakeSandbox(), counter, head)
    assert v.reason == JudgeReason.PROTECTED_PATH
    assert counter.calls == []


def test_exhausted_counter_blocks_before_hidden_execution(world):
    _repo, _r, _s, store, frozen, _base, toy = world
    while store.reserve_run(frozen.task.lineage_id, Pool.CI) is not None:
        pass
    counter = RecordingCounter(store)
    runner = FakeRunner(hidden=AssertionError("hidden execution reached"))
    v = _judge(world, runner, FakeSandbox(), counter, _head(toy, {"src/toy/note.txt": "x\n"}))
    assert (v.status, v.reason) == (Status.BLOCKED, JudgeReason.BUDGET_EXHAUSTED)
    assert counter.calls == ["reserve_run"]
    assert runner.hidden_calls == 0


def test_counter_error_at_reserve_blocks_before_hidden_execution(world):
    counter = RecordingCounter(world[3], error=CounterError("ledger unreadable"))
    runner = FakeRunner(hidden=AssertionError("hidden execution reached"))
    head = _head(world[6], {"src/toy/note.txt": "x\n"})
    v = _judge(world, runner, FakeSandbox(), counter, head)
    assert (v.status, v.reason) == (Status.BLOCKED, JudgeReason.INFRA_ERROR)
    assert counter.calls == ["reserve_run"]
    assert runner.hidden_calls == 0


@pytest.mark.parametrize(
    "hidden",
    [RuntimeError("hidden run broke"), SuiteRun(infra_cause="runner died")],
    ids=["raises", "infra-cause"],
)
def test_failure_after_reserve_keeps_the_charge(world, hidden):
    counter = RecordingCounter(world[3])
    runner = FakeRunner(hidden=hidden)
    head = _head(world[6], {"src/toy/note.txt": "x\n"})
    v = _judge(world, runner, FakeSandbox(), counter, head)
    assert v.status is not Status.PASS
    assert counter.calls == ["reserve_run"]
    assert runner.hidden_calls == 1
    assert _used(world) == 1  # never refunded


def test_hidden_execution_happens_at_most_once_per_judge_call(world):
    counter = RecordingCounter(world[3])
    runner = FakeRunner(hidden=SuiteRun())
    head = _head(world[6], {"src/toy/note.txt": "x\n"})
    _judge(world, runner, FakeSandbox(), counter, head)
    assert counter.calls == ["reserve_run"]
    assert runner.hidden_calls == 1
    assert _used(world) == 1


def test_judge_requires_the_counter_keyword(world):
    repo, runner, sandbox, store, frozen, base, _toy = world
    with pytest.raises(TypeError):
        judge(
            repo,
            store,
            runner,
            sandbox,
            frozen,
            store.hmac_key(),
            base_commit=base,
            head_commit=base,
        )  # type: ignore[call-arg]
