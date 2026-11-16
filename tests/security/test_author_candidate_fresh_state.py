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

# Where a candidate may keep state between calls: its cwd, the shared /tmp, or $HOME.
LOCATIONS = {"cwd": "marker", "tmp": "/tmp/cheeseless-marker", "home": "$HOME/marker"}  # noqa: S108


def _argv(location: str) -> list[str]:
    path = LOCATIONS[location]
    script = f"if [ -e {path} ]; then printf warm; else : > {path}; printf cold; fi"
    return ["/bin/sh", "-c", script]


TEST = (
    "import pytest\n"
    "@pytest.mark.criterion('C1')\n"
    "def test_every_call_is_cold(candidate):\n"
    "    first, second = candidate.run(), candidate.run()\n"
    "    assert (first.stdout, second.stdout) == ('cold', 'cold')\n"
)


def _author_side(tmp_path: Path, argv: list[str], test: str = TEST) -> str:
    acc, ref = tmp_path / "acc" / "hidden", tmp_path / "ref"
    acc.mkdir(parents=True)
    ref.mkdir()
    (acc / "test_fresh.py").write_text(test)
    command = (sys.executable, "-I", "-B", "-c", DRIVER, "run", json.dumps(argv))
    out = BwrapSandbox().author_run(acc.parent, ref, command, env=(), timeout=120.0)
    data = parse_outcome(out)
    assert data is not None, out.stderr_tail + out.stdout
    suite = suite_from(data)
    assert suite is not None
    (outcome,) = suite.outcomes.values()
    return outcome.value


def _normal_side(tmp_path: Path, argv: list[str]) -> list[str]:
    sandbox = BwrapSandbox()
    ref = tmp_path / "normal-ref"
    ref.mkdir()
    with sandbox.scratch() as scratch:
        launcher = sandbox.launcher(ref, (), scratch)
        runs = [subprocess.run([*launcher, *argv], capture_output=True, text=True) for _ in (1, 2)]  # noqa: S603
    return [r.stdout for r in runs]


@needs_bwrap
@pytest.mark.parametrize("location", sorted(LOCATIONS))
def test_author_side_calls_start_from_the_baseline_like_normal_calls(tmp_path, location):
    argv = _argv(location)
    assert _normal_side(tmp_path, argv) == ["cold", "cold"]
    assert _author_side(tmp_path, argv) == "passed"


@needs_bwrap
def test_a_candidate_cannot_modify_state_it_did_not_create(tmp_path):
    test = (
        "import pytest, pathlib\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_shared_state_is_refused(candidate):\n"
        "    shared = pathlib.Path('/tmp/shared-state')\n"  # noqa: S108
        "    shared.write_text('x')\n"
        "    with pytest.raises(AssertionError, match='shared'):\n"
        "        candidate.run()\n"
    )
    script = "printf y > /tmp/shared-state"  # noqa: S108
    assert _author_side(tmp_path, ["/bin/sh", "-c", script], test) == "passed"


BIG_TREE_TEST = (
    "import pytest\n"
    "@pytest.mark.criterion('C1')\n"
    "def test_deadline_covers_the_copy(candidate):\n"
    "    with pytest.raises(AssertionError, match='timed out'):\n"
    "        candidate.run(timeout=0.001)\n"
    "    assert candidate.run(timeout=60).stdout == 'ok\\n'\n"
)


@needs_bwrap
def test_the_per_call_deadline_covers_the_baseline_copy(tmp_path):
    ref = tmp_path / "ref"
    ref.mkdir()
    for i in range(4000):
        (ref / f"f{i}.txt").write_text("x" * 200)
    acc = tmp_path / "acc" / "hidden"
    acc.mkdir(parents=True)
    (acc / "test_big.py").write_text(BIG_TREE_TEST)
    command = (sys.executable, "-I", "-B", "-c", DRIVER, "run", json.dumps(["/bin/echo", "ok"]))
    out = BwrapSandbox().author_run(acc.parent, ref, command, env=(), timeout=120.0)
    data = parse_outcome(out)
    assert data is not None, out.stderr_tail + out.stdout
    suite = suite_from(data)
    assert suite is not None
    assert [o.value for o in suite.outcomes.values()] == ["passed"], out.stdout[-3000:]
