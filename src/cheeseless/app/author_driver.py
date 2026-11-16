"""The in-sandbox pytest driver and the host-side parsing of its result line.

Shared by the author commands (``author_check``/``author_crosscheck``) and by approval, which
must also run the author's unapproved acceptance code only inside the author-runner sandbox.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cheeseless.domain.evaluation import CollectedTest, SuiteRun, TestOutcome
from cheeseless.ports.sandbox import AuthorOutcome, AuthorSandbox

RESULT_MARK = "CHEESELESS-AUTHOR "
CHECK_TIMEOUT = 300.0

# Runs inside the author-runner sandbox as ``python -I -B -c DRIVER collect|run <argv-json>``.
# It collects and (in run mode) executes the hidden suite against the mounted reference and
# prints one result line. It never imports the host package.
DRIVER = r"""
import json, os, shutil, subprocess, sys, tempfile, types
import pytest

MARK = "CHEESELESS-AUTHOR "
mode = sys.argv[1]
argv = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []


class Rec:
    def __init__(self):
        self.collected, self.outcomes, self.errors = [], {}, []

    def pytest_configure(self, config):
        config.addinivalue_line("markers", "criterion(id): the acceptance criterion tested")

    def pytest_collection_modifyitems(self, items):
        for item in items:
            crit = []
            for m in item.iter_markers("criterion"):
                ok = len(m.args) == 1 and not m.kwargs and isinstance(m.args[0], str)
                crit.append(m.args[0] if ok else "<invalid criterion marker>")
            self.collected.append({"node_id": item.nodeid, "criteria": crit})

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(report.nodeid or "<collection>")

    def pytest_runtest_logreport(self, report):
        nid, x = report.nodeid, hasattr(report, "wasxfail")
        if report.when == "teardown":
            if report.failed:
                self.outcomes[nid] = "error"
        elif report.when == "setup":
            if report.failed:
                self.outcomes[nid] = "error"
            elif report.skipped:
                self.outcomes[nid] = "xfailed" if x else "skipped"
        elif report.failed:
            self.outcomes[nid] = "failed"
        elif report.skipped:
            self.outcomes[nid] = "xfailed" if x else "skipped"
        else:
            self.outcomes[nid] = "xpassed" if x else "passed"


class Runner:
    @pytest.fixture
    def candidate(self):
        ref = os.environ["CHEESELESS_AUTHOR_REFERENCE"]

        class C:
            def run(self, args=(), stdin=None, timeout=None):
                data = stdin.encode() if isinstance(stdin, str) else stdin
                # Every call gets fresh writable state copied from the immutable baseline,
                # like a normal candidate call; nothing carries over between calls.
                work = tempfile.mkdtemp(prefix="cand-")
                tree = os.path.join(work, "tree")
                try:
                    shutil.copytree(ref, tree, symlinks=True)
                    p = subprocess.run([*argv, *args], input=data, capture_output=True,
                                       cwd=tree, timeout=timeout or 30.0)
                except subprocess.TimeoutExpired:
                    raise AssertionError("candidate call timed out")
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                dec = lambda b: b.decode("utf-8", "replace")
                return types.SimpleNamespace(returncode=p.returncode,
                                             stdout=dec(p.stdout), stderr=dec(p.stderr))
        return C()


rec = Rec()
args = ["hidden", "-p", "no:cacheprovider", "--import-mode=importlib", "-c", os.devnull,
        "--rootdir", os.getcwd(), "-q"]
if mode == "collect":
    args.append("--collect-only")
code = pytest.main(args, plugins=[rec] + ([Runner()] if mode == "run" else []))
print(MARK + json.dumps({"exitstatus": int(code), "collected": rec.collected,
                         "outcomes": rec.outcomes, "collection_errors": rec.errors}))
"""


def parse_outcome(outcome: AuthorOutcome) -> dict[str, object] | None:
    if outcome.timed_out:
        return None
    for line in reversed(outcome.stdout.splitlines()):
        if line.startswith(RESULT_MARK):
            try:
                data = json.loads(line[len(RESULT_MARK) :])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def suite_from(data: dict[str, object]) -> SuiteRun | None:
    try:
        collected = tuple(
            CollectedTest(str(t["node_id"]), tuple(str(c) for c in t["criteria"]))
            for t in data["collected"]  # type: ignore[attr-defined]
        )
        raw = data["outcomes"]
        outcomes = {str(k): TestOutcome(str(v)) for k, v in raw.items()}  # type: ignore[attr-defined]
        errors = tuple(str(e) for e in data["collection_errors"])  # type: ignore[attr-defined]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return SuiteRun(collected=collected, outcomes=outcomes, collection_errors=errors)


def run_driver_on(
    sandbox: AuthorSandbox,
    acceptance: Path,
    mode: str,
    reference: Path | None,
    run_argv: Sequence[str],
    env: Sequence[tuple[str, str]] = (),
) -> SuiteRun | None:
    """Run the driver over an exported acceptance tree inside the author sandbox."""
    command = (sys.executable, "-I", "-B", "-c", DRIVER, mode, json.dumps(list(run_argv)))
    outcome = sandbox.author_run(acceptance, reference, command, env=env, timeout=CHECK_TIMEOUT)
    data = parse_outcome(outcome)
    return None if data is None else suite_from(data)


__all__ = ["CHECK_TIMEOUT", "DRIVER", "RESULT_MARK", "parse_outcome", "run_driver_on", "suite_from"]
