from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare


def _sandbox(tmp_path):
    # Use default sandbox constructor if available
    return BwrapSandbox()


@pytest.mark.slow
def test_freeze_prepare_and_commit(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    assert prepared.tests
    result = commit(prepared, store)
    assert result.task_id
    assert store.active_task_id() == result.task_id


def _suite(outcomes):
    from cheeseless.domain.evaluation import CollectedTest, SuiteRun

    tests = (
        CollectedTest("t::a", ("normalize",)),
        CollectedTest("t::b", ("normalize",)),
        CollectedTest("t::c", ("score",)),
        CollectedTest("t::d", ("score",)),
    )
    return SuiteRun(collected=tests, outcomes={t.node_id: outcomes for t in tests})


class _FakeRunner:
    def __init__(self, suites):
        self.suites = list(suites)
        self.calls = 0

    def run(self, *args, **kwargs):
        from cheeseless.ports.runner import RunnerOutput

        suite = self.suites[min(self.calls, len(self.suites) - 1)]
        self.calls += 1
        return RunnerOutput(suite)


class _FakeSandbox:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.counter = 0

    def _dir(self, prefix):
        import tempfile
        from contextlib import contextmanager

        @contextmanager
        def scratch():
            self.counter += 1
            path = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=self.root))
            yield path

        return scratch()

    def trusted_scratch(self):
        return self._dir("trusted")

    def scratch(self):
        return self._dir("scratch")

    def build(self, *args, **kwargs):
        from cheeseless.ports.sandbox import StepOutcome

        return StepOutcome(True, 0)

    def launcher(self, *args, **kwargs):
        return ()


def test_prepare_needs_two_identical_runs_and_builds_fingerprints(toy_repo, tmp_path):
    from cheeseless.app.freeze import FreezeError
    from cheeseless.domain.dlp import DlpScanner
    from cheeseless.domain.evaluation import TestOutcome

    repo = GitRepository.discover(toy_repo)
    runner = _FakeRunner([_suite(TestOutcome.FAILED)])
    prepared = prepare(repo, runner, _FakeSandbox(tmp_path))
    assert runner.calls == 2
    assert prepared.fingerprints.task_id == prepared.task_id
    assert DlpScanner(prepared.fingerprints).scan(b"some plain text") is None

    flaky = _FakeRunner([_suite(TestOutcome.FAILED), _suite(TestOutcome.PASSED)])
    with pytest.raises(FreezeError, match="flaky"):
        prepare(repo, flaky, _FakeSandbox(tmp_path))
