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


class _Boom:
    def __getattr__(self, name):
        raise AssertionError(f"freeze should refuse before using {name}")


def test_prepare_refuses_a_suite_with_too_few_sentinels(tmp_path):
    from tests.toy import TOY_FILES, make_repo

    files = dict(TOY_FILES)
    files["acceptance/hidden/test_normalize.py"] = "def test_x(candidate):\n    pass\n"
    files["acceptance/hidden/test_score.py"] = "def test_y(candidate):\n    pass\n"
    repo = GitRepository.discover(make_repo(tmp_path / "toy", files))
    from cheeseless.app.freeze import FreezeError

    with pytest.raises(FreezeError, match="sentinel"):
        prepare(repo, _Boom(), _Boom())


MARKER = "CHEESELESS-SENTINEL-diag01abc"


def test_freeze_error_redacts_markers_in_every_problem():
    from cheeseless.app.freeze import FreezeError
    from cheeseless.domain.dlp import digest

    error = FreezeError([f"t::x[{MARKER}] is skipped", f"collection error: {MARKER}"])
    placeholder = f"<sentinel digest {digest(MARKER.encode())}>"
    assert all(MARKER not in problem for problem in error.problems)
    assert all(placeholder in problem for problem in error.problems)
    assert MARKER not in str(error)
    assert MARKER not in repr(error.args)


class _FailingBuildSandbox(_FakeSandbox):
    def build(self, *args, **kwargs):
        from cheeseless.ports.sandbox import StepOutcome

        return StepOutcome(False, 1, f"Traceback: assert x == {MARKER!r}")


def test_prepare_redacts_markers_in_build_stderr(toy_repo, tmp_path):
    from cheeseless.app.freeze import FreezeError
    from cheeseless.domain.dlp import digest

    repo = GitRepository.discover(toy_repo)
    with pytest.raises(FreezeError) as caught:
        prepare(repo, _FakeRunner([]), _FailingBuildSandbox(tmp_path / "sb"))
    message = str(caught.value)
    assert "build failed" in message
    assert MARKER not in message
    assert f"<sentinel digest {digest(MARKER.encode())}>" in message


def test_prepare_reads_from_a_tree_source_and_digests_the_baseline(toy_repo, tmp_path):
    from cheeseless.domain.evaluation import TestOutcome

    repo = GitRepository.discover(toy_repo)
    head = repo.head_commit()
    plain = prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))
    from_tree = prepare(
        repo,
        _FakeRunner([_suite(TestOutcome.FAILED)]),
        _FakeSandbox(tmp_path),
        baseline_commit=head,
        source=repo.tree_of(head),
    )
    assert from_tree.task_id == plain.task_id
    assert from_tree.acceptance_tree == plain.acceptance_tree
    assert from_tree.baseline_commit == head


class _CandidateSpy(_FakeSandbox):
    """Records the candidate tree handed to build() (what the RED run would execute)."""

    def __init__(self, root):
        super().__init__(root)
        self.seen: dict[str, str] = {}

    def build(self, cand_root, *args, **kwargs):
        for path in Path(cand_root).rglob("*"):
            if path.is_file():
                self.seen[str(path.relative_to(cand_root))] = path.read_text()
        return super().build(cand_root, *args, **kwargs)


def test_candidate_projection_comes_from_the_baseline_not_the_source(toy_repo, tmp_path):
    """The task digest names ``baseline_commit``; the RED check must run that code."""
    from cheeseless.domain.evaluation import TestOutcome
    from tests.toy import commit_all, write_files

    repo = GitRepository.discover(toy_repo)
    baseline = repo.head_commit()
    write_files(toy_repo, {"src/only_in_head.py": "HEAD_ONLY = 1\n"})
    head = commit_all(toy_repo, "head moves outside acceptance/")
    spy = _CandidateSpy(tmp_path / "sb")
    prepare(
        repo,
        _FakeRunner([_suite(TestOutcome.FAILED)]),
        spy,
        baseline_commit=baseline,
        source=head,
    )
    assert "src/only_in_head.py" not in spy.seen
    assert any(p.startswith("src/") for p in spy.seen)
    assert any(p.startswith("acceptance/contract/") for p in spy.seen)
