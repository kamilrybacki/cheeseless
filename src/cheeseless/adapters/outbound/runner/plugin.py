"""Trusted pytest plugin loaded by the runner with ``-p`` (D4, D17).

It records what pytest collected (with each test's ``criterion`` markers), applies the
runner's selection by deselecting (never skipping), records one outcome per test and
writes a JSON report (see ``report.py``) when the session ends. The ``candidate``
fixture is the only way a test reaches the candidate: it runs the sandbox launcher plus
the contract's ``run`` argv in a separate process.

Configuration comes from the environment the runner sets; anything malformed is a
usage error so the session fails closed.
"""

import ctypes
import json
import math
import os
import subprocess  # noqa: S404 - the candidate runs behind a process boundary
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import pytest

from cheeseless.adapters.outbound.runner.report import REPORT_VERSION
from cheeseless.domain.evaluation import TestOutcome

ENV_REPORT = "CHEESELESS_RUNNER_REPORT"
ENV_SELECT = "CHEESELESS_RUNNER_SELECT"
ENV_CANDIDATE = "CHEESELESS_RUNNER_CANDIDATE"
ENV_CALL_TIMEOUT = "CHEESELESS_RUNNER_CALL_TIMEOUT"
ENV_HARDEN = "CHEESELESS_RUNNER_HARDEN"

DEFAULT_CALL_TIMEOUT = 60.0
MAX_OUTPUT = 1024 * 1024
KILL_GRACE = 5.0
INVALID_CRITERION = "<invalid criterion marker>"
_PR_SET_DUMPABLE = 4


class CandidateTimeout(AssertionError):
    """One candidate call ran past its timeout and was killed."""


@dataclass(frozen=True, slots=True)
class CandidateResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class Settings:
    report: Path
    selected: frozenset[str] | None
    call_timeout: float


def _selection(raw: str | None) -> frozenset[str] | None:
    if raw is None:
        return None
    try:
        data = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"cannot read the selection: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(n, str) for n in data):
        raise pytest.UsageError("the selection must be a JSON list of node ids")
    return frozenset(data)


def _timeout(raw: str | None) -> float:
    if raw is None:
        return DEFAULT_CALL_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise pytest.UsageError(f"bad {ENV_CALL_TIMEOUT}: {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise pytest.UsageError(f"bad {ENV_CALL_TIMEOUT}: {raw!r}")
    return value


def settings_from_env() -> Settings:
    report = os.environ.get(ENV_REPORT)
    if not report:
        raise pytest.UsageError(f"{ENV_REPORT} is not set")
    selected = _selection(os.environ.get(ENV_SELECT))
    return Settings(Path(report), selected, _timeout(os.environ.get(ENV_CALL_TIMEOUT)))


def _harden() -> None:
    """No core dumps and no same-uid ptrace or /proc/<pid>/mem reads of the runner."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise pytest.UsageError(f"prctl(PR_SET_DUMPABLE) failed: errno {ctypes.get_errno()}")


def _criteria(item: pytest.Item) -> list[str]:
    ids = []
    for mark in item.iter_markers("criterion"):
        valid = len(mark.args) == 1 and isinstance(mark.args[0], str) and not mark.kwargs
        ids.append(mark.args[0] if valid else INVALID_CRITERION)
    return ids


class Recorder:
    """Per-session state; lives on the config, never in module globals."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.collected: list[dict[str, Any]] = []
        self.outcomes: dict[str, TestOutcome] = {}
        self.collection_errors: list[str] = []

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        self.collected = [{"node_id": i.nodeid, "criteria": _criteria(i)} for i in items]
        selected = self.settings.selected
        if selected is None:
            return
        kept = [i for i in items if i.nodeid in selected]
        dropped = [i for i in items if i.nodeid not in selected]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = kept

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        outcome = _outcome(report)
        if outcome is not None:
            self.outcomes[report.nodeid] = outcome

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        data = {
            "version": REPORT_VERSION,
            "exitstatus": int(exitstatus),
            "collected": self.collected,
            "outcomes": {k: v.value for k, v in self.outcomes.items()},
            "collection_errors": self.collection_errors,
        }
        tmp = self.settings.report.with_name(self.settings.report.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.settings.report)


def _outcome(report: pytest.TestReport) -> TestOutcome | None:
    xfail = hasattr(report, "wasxfail")
    if report.when == "teardown":
        return TestOutcome.ERROR if report.failed else None
    if report.failed:
        return TestOutcome.ERROR if report.when == "setup" else TestOutcome.FAILED
    if report.skipped:
        return TestOutcome.XFAILED if xfail else TestOutcome.SKIPPED
    if report.when == "call":
        return TestOutcome.XPASSED if xfail else TestOutcome.PASSED
    return None


_RECORDER = pytest.StashKey[Recorder]()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "criterion(id): the acceptance criterion a test proves")
    recorder = Recorder(settings_from_env())
    if os.environ.get(ENV_HARDEN) == "1":  # pragma: no cover - exercised in a subprocess
        _harden()
    config.stash[_RECORDER] = recorder
    config.pluginmanager.register(recorder, "cheeseless-recorder")


def _read(stream: IO[bytes]) -> str:
    stream.seek(0)
    return stream.read(MAX_OUTPUT).decode("utf-8", "replace")


def _stop(proc: "subprocess.Popen[bytes]") -> None:
    """SIGTERM (the launcher forwards it to bwrap), then SIGKILL after a grace period.

    The candidate stays in the runner's process group, so a suite timeout that kills the
    group also kills every launcher; bwrap's ``--die-with-parent`` takes the sandbox down.
    """
    proc.terminate()
    try:
        proc.wait(KILL_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class Candidate:
    """Runs the candidate once per call through the sandbox launcher."""

    max_output = MAX_OUTPUT

    def __init__(self, argv: Sequence[str], call_timeout: float) -> None:
        self._argv = tuple(argv)
        self._timeout = call_timeout

    def run(
        self,
        args: Sequence[str] = (),
        stdin: str | bytes | None = None,
        timeout: float | None = None,
    ) -> CandidateResult:
        limit = self._timeout if timeout is None else timeout
        data = stdin.encode() if isinstance(stdin, str) else stdin
        with (
            tempfile.TemporaryFile() as fin,
            tempfile.TemporaryFile() as out,
            tempfile.TemporaryFile() as err,
        ):
            if data is not None:
                fin.write(data)
                fin.seek(0)
            proc = subprocess.Popen(  # noqa: S603 - trusted launcher argv
                [*self._argv, *args],
                stdin=fin if data is not None else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
            )
            try:
                code = proc.wait(limit)
            except subprocess.TimeoutExpired:
                _stop(proc)
                raise CandidateTimeout(f"the candidate ran longer than {limit}s") from None
            finally:
                if proc.returncode is None:  # pragma: no cover - interrupted wait
                    _stop(proc)
            return CandidateResult(code, _read(out), _read(err))


def _candidate_argv(raw: str | None) -> list[str]:
    if raw is None:
        raise RuntimeError(f"{ENV_CANDIDATE} is not set")
    try:
        argv = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"{ENV_CANDIDATE} is not JSON") from exc
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise RuntimeError(f"{ENV_CANDIDATE} must be a non-empty list of strings")
    return argv


@pytest.fixture
def candidate(request: pytest.FixtureRequest) -> Iterator[Candidate]:
    settings = request.config.stash[_RECORDER].settings
    yield Candidate(_candidate_argv(os.environ.get(ENV_CANDIDATE)), settings.call_timeout)
