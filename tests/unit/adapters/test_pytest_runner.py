"""PytestRunner: a fresh pytest process per run, isolated from the candidate tree."""

import os
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.domain.evaluation import TestOutcome
from tests.toy import BASELINE_MAIN, FIXED_MAIN, HIDDEN_NORMALIZE, SMOKE_TEST, write_files


def _acceptance(root: Path) -> Path:
    write_files(
        root,
        {
            "contract/test_smoke.py": SMOKE_TEST,
            "hidden/test_normalize.py": HIDDEN_NORMALIZE,
        },
    )
    return root


def _main(tmp_path: Path, text: str) -> list[str]:
    main = tmp_path / "cand" / "main.py"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text(text)
    return [sys.executable, "-I", str(main)]


def test_baseline_fails_hidden_and_passes_smoke(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    out = PytestRunner().run(
        root,
        ["contract", "hidden"],
        selected=None,
        launcher=(),
        run=_main(tmp_path, BASELINE_MAIN),
        timeout=60.0,
    )
    suite = out.suite
    assert suite.infra_cause == ""
    ids = [t.node_id for t in suite.collected]
    assert ids == [
        "contract/test_smoke.py::test_starts",
        "hidden/test_normalize.py::test_lowercases",
        "hidden/test_normalize.py::test_collapses_whitespace",
        "hidden/test_normalize.py::test_keeps_lines",
    ]
    assert suite.collected[1].criteria == ("normalize",)
    assert suite.outcome_map["contract/test_smoke.py::test_starts"] == TestOutcome.PASSED
    assert suite.outcome_map["hidden/test_normalize.py::test_lowercases"] == TestOutcome.FAILED


def test_fixed_candidate_passes(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    out = PytestRunner().run(
        root,
        ["hidden"],
        selected=None,
        launcher=(),
        run=_main(tmp_path, FIXED_MAIN),
        timeout=60.0,
    )
    assert set(out.suite.outcome_map.values()) == {TestOutcome.PASSED}


def test_selection_deselects_the_rest(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    node = "hidden/test_normalize.py::test_keeps_lines"
    out = PytestRunner().run(
        root,
        ["contract", "hidden"],
        selected={node: "normalize"},
        launcher=(),
        run=_main(tmp_path, FIXED_MAIN),
        timeout=60.0,
    )
    assert out.suite.outcome_map == {node: TestOutcome.PASSED}
    assert len(out.suite.collected) == 4


def test_launcher_prefixes_every_call(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(
        "import subprocess, sys\n"
        "open(sys.argv[1], 'a').write('call\\n')\n"
        "raise SystemExit(subprocess.call(sys.argv[2:]))\n"
    )
    log = tmp_path / "calls.log"
    out = PytestRunner().run(
        root,
        ["contract"],
        selected=None,
        launcher=(sys.executable, "-I", str(wrapper), str(log)),
        run=_main(tmp_path, BASELINE_MAIN),
        timeout=60.0,
    )
    assert out.suite.infra_cause == ""
    assert log.read_text() == "call\n"


def test_collection_errors_are_reported(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    (root / "hidden" / "test_broken.py").write_text("def broken(:\n")
    out = PytestRunner().run(
        root,
        ["hidden"],
        selected=None,
        launcher=(),
        run=_main(tmp_path, BASELINE_MAIN),
        timeout=60.0,
    )
    assert out.suite.infra_cause == ""
    assert out.suite.collection_errors == ("hidden/test_broken.py",)


def test_suite_timeout_kills_the_whole_group(tmp_path: Path) -> None:
    root = tmp_path / "acc"
    token = f"cheeseless-kill-{os.getpid()}-{tmp_path.name}"
    write_files(
        root,
        {"hidden/test_hang.py": "def test_hang(candidate):\n    candidate.run([])\n"},
    )
    out = PytestRunner().run(
        root,
        ["hidden"],
        selected=None,
        launcher=(),
        run=["/bin/sh", "-c", f"sleep 3600; : {token}"],
        timeout=3.0,
    )
    assert "timed out" in out.suite.infra_cause
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        assert token.encode() not in cmdline


def test_runner_hardens_itself(tmp_path: Path) -> None:
    root = tmp_path / "acc"
    write_files(
        root,
        {
            "hidden/test_dump.py": (
                "def test_dumpable():\n"
                "    status = open('/proc/self/status').read()\n"
                "    assert 'TracerPid' in status\n"
                "    import ctypes\n"
                "    libc = ctypes.CDLL(None)\n"
                "    assert libc.prctl(3, 0, 0, 0, 0) == 0\n"
            )
        },
    )
    out = PytestRunner().run(
        root, ["hidden"], selected=None, launcher=(), run=["true"], timeout=60.0
    )
    assert out.suite.outcome_map == {"hidden/test_dump.py::test_dumpable": TestOutcome.PASSED}


def test_host_environment_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEESELESS_SECRET_PROBE", "leak")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p no:nonexistent --bogus")
    root = tmp_path / "acc"
    write_files(
        root,
        {
            "hidden/test_env.py": (
                "import os\n"
                "def test_env():\n"
                "    assert 'CHEESELESS_SECRET_PROBE' not in os.environ\n"
                "    assert 'PYTEST_ADDOPTS' not in os.environ\n"
            )
        },
    )
    out = PytestRunner().run(
        root, ["hidden"], selected=None, launcher=(), run=["true"], timeout=60.0
    )
    assert out.suite.outcome_map == {"hidden/test_env.py::test_env": TestOutcome.PASSED}


@pytest.mark.parametrize("bad", ["/abs", "../up", "a/../../b", ""])
def test_bad_paths_are_refused(tmp_path: Path, bad: str) -> None:
    root = _acceptance(tmp_path / "acc")
    with pytest.raises(ValueError):
        PytestRunner().run(root, [bad], selected=None, launcher=(), run=["true"], timeout=60.0)


def test_bad_timeouts_are_refused(tmp_path: Path) -> None:
    root = _acceptance(tmp_path / "acc")
    with pytest.raises(ValueError):
        PytestRunner().run(root, ["hidden"], selected=None, launcher=(), run=["true"], timeout=0)
    with pytest.raises(ValueError):
        PytestRunner(call_timeout=-1)


def test_output_is_capped(tmp_path: Path) -> None:
    root = tmp_path / "acc"
    write_files(
        root,
        {"hidden/test_loud.py": "def test_loud():\n    print('x' * 5_000_000)\n    assert 0\n"},
    )
    out = PytestRunner(max_output=10_000).run(
        root, ["hidden"], selected=None, launcher=(), run=["true"], timeout=60.0
    )
    assert len(out.output) <= 10_000
    assert out.suite.outcome_map == {"hidden/test_loud.py::test_loud": TestOutcome.FAILED}
