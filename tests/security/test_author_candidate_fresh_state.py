"""Author-side and normal-side candidate calls must agree: every call starts from the baseline.

A candidate that is cold on its first call and warm after leaving a marker must be cold on
every call in both paths. Otherwise author preparation accepts tests that normal preparation
rejects as already passing on the baseline.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.app.author_driver import DRIVER, parse_outcome, suite_from

needs_bwrap = pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)

SCRIPT = "if [ -e marker ]; then printf warm; else : > marker; printf cold; fi"
ARGV = ["/bin/sh", "-c", SCRIPT]
TEST = (
    "import pytest\n"
    "@pytest.mark.criterion('C1')\n"
    "def test_every_call_is_cold(candidate):\n"
    "    first, second = candidate.run(), candidate.run()\n"
    "    assert (first.stdout, second.stdout) == ('cold', 'cold')\n"
)


def _author_side(tmp_path: Path) -> str:
    acc, ref = tmp_path / "acc" / "hidden", tmp_path / "ref"
    acc.mkdir(parents=True)
    ref.mkdir()
    (acc / "test_fresh.py").write_text(TEST)
    command = (sys.executable, "-I", "-B", "-c", DRIVER, "run", json.dumps(ARGV))
    out = BwrapSandbox().author_run(acc.parent, ref, command, env=(), timeout=120.0)
    data = parse_outcome(out)
    assert data is not None, out.stderr_tail + out.stdout
    suite = suite_from(data)
    assert suite is not None
    (outcome,) = suite.outcomes.values()
    return outcome.value


def _normal_side(tmp_path: Path) -> list[str]:
    sandbox = BwrapSandbox()
    ref = tmp_path / "normal-ref"
    ref.mkdir()
    with sandbox.scratch() as scratch:
        launcher = sandbox.launcher(ref, (), scratch)
        runs = [subprocess.run([*launcher, *ARGV], capture_output=True, text=True) for _ in (1, 2)]  # noqa: S603
    return [r.stdout for r in runs]


@needs_bwrap
def test_author_side_calls_start_from_the_baseline_like_normal_calls(tmp_path):
    assert _normal_side(tmp_path) == ["cold", "cold"]
    assert _author_side(tmp_path) == "passed"
