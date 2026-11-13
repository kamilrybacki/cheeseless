"""SuiteRunner adapter: one fresh, isolated pytest process per suite run (D4, D17).

The root holds only the acceptance tree; the candidate is reached solely through the
plugin's ``candidate`` fixture, which runs ``launcher + run`` in a separate process. The
pytest process gets a scrubbed environment, a private temp area and an empty ini file,
so nothing from the host (``PYTEST_ADDOPTS``, plugins, caches) or from the candidate
tree can change how the suite is collected or reported.

pytest runs in its own session and process group. Candidate processes stay in that
group, so a suite timeout kills the whole group; the leader is not reaped until the
group has been killed, which keeps its process-group id from being reused.
"""

import json
import math
import os
import select
import signal
import subprocess  # noqa: S404 - pytest runs behind a process boundary
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from cheeseless.adapters.outbound.runner import plugin
from cheeseless.adapters.outbound.runner.report import suite_from_report
from cheeseless.ports.runner import RunnerOutput

DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
KILL_GRACE = 2.0
MAX_OUTPUT = 1024 * 1024
_PLUGIN = "cheeseless.adapters.outbound.runner.plugin"


def _positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number, not {value!r}")
    return value


def _check_paths(paths: Sequence[str]) -> list[str]:
    checked = []
    for raw in paths:
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"suite paths must be relative and inside the root: {raw!r}")
        checked.append(str(path))
    if not checked:
        raise ValueError("no suite paths to run")
    return checked


def _exited(pidfd: int, timeout: float) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return bool(poller.poll(timeout * 1000))


def _killpg(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _wait(proc: "subprocess.Popen[bytes]", timeout: float) -> bool:
    """Wait for pytest, then kill whatever is left of its group. True when it timed out."""
    pidfd = os.pidfd_open(proc.pid)
    try:
        timed_out = not _exited(pidfd, timeout)
        if timed_out:
            _killpg(proc.pid, signal.SIGTERM)
            _exited(pidfd, KILL_GRACE)
        # The leader is a zombie (or still alive) here, so its pgid cannot be reused yet.
        _killpg(proc.pid, signal.SIGKILL)
    finally:
        os.close(pidfd)
    proc.wait()
    return timed_out


class PytestRunner:
    """Runs an acceptance suite with the trusted recorder plugin."""

    def __init__(
        self, call_timeout: float = plugin.DEFAULT_CALL_TIMEOUT, max_output: int = MAX_OUTPUT
    ):
        self._call_timeout = _positive("call_timeout", call_timeout)
        if max_output <= 0:
            raise ValueError("max_output must be positive")
        self._max_output = max_output

    def run(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        selected: Mapping[str, str] | None,
        launcher: Sequence[str],
        run: Sequence[str],
        timeout: float,
    ) -> RunnerOutput:
        _positive("timeout", timeout)
        suite_paths = _check_paths(paths)
        root = Path(root).resolve(strict=True)
        if not run:
            raise ValueError("the contract has no run command")
        with tempfile.TemporaryDirectory(prefix="cheeseless-run-") as raw_private:
            private = Path(raw_private)
            private.chmod(0o700)
            return self._session(root, suite_paths, private, selected, [*launcher, *run], timeout)

    def _session(
        self,
        root: Path,
        paths: list[str],
        private: Path,
        selected: Mapping[str, str] | None,
        candidate: list[str],
        timeout: float,
    ) -> RunnerOutput:
        ini = private / "pytest.ini"
        ini.write_text("[pytest]\n", encoding="utf-8")
        report = private / "report.json"
        for name in ("basetemp", "tmp", "home"):
            (private / name).mkdir(mode=0o700)
        env = {
            "PATH": DEFAULT_PATH,
            "LANG": "C.UTF-8",
            "HOME": str(private / "home"),
            "TMPDIR": str(private / "tmp"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            plugin.ENV_REPORT: str(report),
            plugin.ENV_CANDIDATE: json.dumps(candidate),
            plugin.ENV_CALL_TIMEOUT: repr(self._call_timeout),
            plugin.ENV_HARDEN: "1",
        }
        if selected is not None:
            select_file = private / "select.json"
            select_file.write_text(json.dumps(list(selected)), encoding="utf-8")
            env[plugin.ENV_SELECT] = str(select_file)
        argv = [
            sys.executable, "-I", "-B", "-m", "pytest",
            "-c", str(ini),
            f"--rootdir={root}",
            f"--confcutdir={root}",
            f"--basetemp={private / 'basetemp'}",
            "--import-mode=importlib",
            "--strict-markers",
            "-p", "no:cacheprovider",
            "-p", _PLUGIN,
            "-q",
            "--",
            *paths,
        ]  # fmt: skip
        with tempfile.TemporaryFile(dir=private) as out:
            proc = subprocess.Popen(  # noqa: S603 - trusted argv, scrubbed env
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            timed_out = _wait(proc, timeout)
            out.seek(0)
            output = out.read(self._max_output).decode("utf-8", "replace")
        raw = report.read_bytes() if report.is_file() else None
        suite = suite_from_report(raw, proc.returncode, timed_out=timed_out)
        return RunnerOutput(suite, output[: self._max_output])
