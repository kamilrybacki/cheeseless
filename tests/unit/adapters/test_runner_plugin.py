"""The trusted pytest plugin: criteria, selection, outcomes and the candidate fixture."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.outbound.runner import plugin

PLUGIN = plugin.__name__

FAKE_CANDIDATE = """\
import sys, time
args = sys.argv[1:]
if args[:1] == ["sleep"]:
    time.sleep(60)
if args[:1] == ["flood"]:
    sys.stdout.write("x" * (3 * 1024 * 1024))
    raise SystemExit(0)
sys.stderr.write("err:" + " ".join(args))
sys.stdout.write(sys.stdin.read().upper())
raise SystemExit(3 if args[:1] == ["fail"] else 0)
"""

OUTCOMES = """\
import pytest

pytestmark = pytest.mark.criterion("c1")

@pytest.fixture
def broken_setup():
    raise RuntimeError("setup")

@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown")

def test_passes():
    pass

def test_fails():
    assert False

def test_skips():
    pytest.skip("no")

@pytest.mark.skip
def test_marked_skip():
    pass

@pytest.mark.xfail
def test_xfails():
    assert False

@pytest.mark.xfail
def test_xpasses():
    pass

def test_setup_error(broken_setup):
    pass

def test_teardown_error(broken_teardown):
    pass
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {"report": tmp_path / "_r" / "report.json", "select": tmp_path / "_r" / "select.json"}
    paths["report"].parent.mkdir()
    script = tmp_path / "_r" / "fake.py"
    script.write_text(FAKE_CANDIDATE)
    monkeypatch.setenv(plugin.ENV_REPORT, str(paths["report"]))
    monkeypatch.setenv(plugin.ENV_CANDIDATE, json.dumps([sys.executable, "-I", str(script)]))
    monkeypatch.delenv(plugin.ENV_SELECT, raising=False)
    monkeypatch.delenv(plugin.ENV_CALL_TIMEOUT, raising=False)
    return paths


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_inprocess(
        "-p", PLUGIN, "--strict-markers", "-p", "no:cacheprovider", *args
    )


def _report(env: dict[str, Path]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(env["report"].read_text())
    return data


def test_outcomes_cover_every_phase(pytester: pytest.Pytester, env: dict[str, Path]) -> None:
    pytester.makepyfile(test_outcomes=OUTCOMES)
    result = _run(pytester)
    report = _report(env)
    assert report["version"] == 1
    assert report["exitstatus"] == result.ret == 1
    assert report["collection_errors"] == []
    outcomes = {k.split("::")[1]: v for k, v in report["outcomes"].items()}
    assert outcomes == {
        "test_passes": "passed",
        "test_fails": "failed",
        "test_skips": "skipped",
        "test_marked_skip": "skipped",
        "test_xfails": "xfailed",
        "test_xpasses": "xpassed",
        "test_setup_error": "error",
        "test_teardown_error": "error",
    }
    assert {tuple(c["criteria"]) for c in report["collected"]} == {("c1",)}
    assert len(report["collected"]) == 8


def test_criteria_markers_are_recorded_verbatim(
    pytester: pytest.Pytester, env: dict[str, Path]
) -> None:
    pytester.makepyfile(
        test_marks="""
import pytest

def test_none():
    pass

@pytest.mark.criterion("a")
@pytest.mark.criterion("b")
def test_two():
    pass

@pytest.mark.criterion(7)
def test_bad_arg():
    pass

@pytest.mark.criterion()
def test_no_arg():
    pass
"""
    )
    _run(pytester)
    criteria = {c["node_id"].split("::")[1]: c["criteria"] for c in _report(env)["collected"]}
    assert criteria["test_none"] == []
    assert sorted(criteria["test_two"]) == ["a", "b"]
    assert criteria["test_bad_arg"] == [plugin.INVALID_CRITERION]
    assert criteria["test_no_arg"] == [plugin.INVALID_CRITERION]


def test_selection_deselects_but_records_everything(
    pytester: pytest.Pytester, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(test_sel="def test_a(): pass\ndef test_b(): assert False\n")
    env["select"].write_text(json.dumps(["test_sel.py::test_a"]))
    monkeypatch.setenv(plugin.ENV_SELECT, str(env["select"]))
    result = _run(pytester)
    result.assert_outcomes(passed=1, deselected=1)
    report = _report(env)
    assert report["exitstatus"] == 0
    assert report["outcomes"] == {"test_sel.py::test_a": "passed"}
    assert [c["node_id"] for c in report["collected"]] == [
        "test_sel.py::test_a",
        "test_sel.py::test_b",
    ]


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', "[1]"])
def test_malformed_selection_is_a_usage_error(
    pytester: pytest.Pytester, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    pytester.makepyfile(test_sel="def test_a(): pass\n")
    env["select"].write_text(raw)
    monkeypatch.setenv(plugin.ENV_SELECT, str(env["select"]))
    assert _run(pytester).ret == pytest.ExitCode.USAGE_ERROR


def test_collection_errors_are_reported(pytester: pytest.Pytester, env: dict[str, Path]) -> None:
    pytester.makepyfile(test_ok="def test_a(): pass\n", test_broken="def broken(:\n")
    result = _run(pytester)
    report = _report(env)
    assert result.ret == report["exitstatus"] == 2
    assert report["collection_errors"] == ["test_broken.py"]


def test_missing_report_path_is_a_usage_error(
    pytester: pytest.Pytester, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plugin.ENV_REPORT)
    pytester.makepyfile(test_a="def test_a(): pass\n")
    assert _run(pytester).ret == pytest.ExitCode.USAGE_ERROR


def test_candidate_fixture_runs_the_launcher(
    pytester: pytest.Pytester, env: dict[str, Path]
) -> None:
    pytester.makepyfile(
        test_cand="""
def test_run(candidate):
    r = candidate.run(["go", "now"], stdin="hello\\n")
    assert (r.returncode, r.stdout, r.stderr) == (0, "HELLO\\n", "err:go now")

def test_bytes_stdin_and_exit(candidate):
    r = candidate.run(["fail"], stdin=b"x")
    assert (r.returncode, r.stdout) == (3, "X")

def test_no_stdin(candidate):
    assert candidate.run([]).stdout == ""

def test_output_is_capped(candidate):
    r = candidate.run(["flood"])
    assert len(r.stdout) == candidate.max_output
"""
    )
    result = _run(pytester)
    result.assert_outcomes(passed=4)


def test_candidate_call_timeout_fails_the_test(
    pytester: pytest.Pytester, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plugin.ENV_CALL_TIMEOUT, "0.5")
    pytester.makepyfile(test_slow="def test_slow(candidate):\n    candidate.run(['sleep'])\n")
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*CandidateTimeout*"])


def test_explicit_timeout_wins(pytester: pytest.Pytester, env: dict[str, Path]) -> None:
    pytester.makepyfile(
        test_slow="def test_slow(candidate):\n    candidate.run(['sleep'], timeout=0.3)\n"
    )
    _run(pytester).assert_outcomes(failed=1)


@pytest.mark.parametrize("value", [None, "not json", "[]", '["a", 1]'])
def test_bad_candidate_argv_errors_the_test(
    pytester: pytest.Pytester,
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(plugin.ENV_CANDIDATE)
    else:
        monkeypatch.setenv(plugin.ENV_CANDIDATE, value)
    pytester.makepyfile(test_c="def test_c(candidate):\n    pass\n")
    _run(pytester).assert_outcomes(errors=1)
    assert _report(env)["outcomes"] == {"test_c.py::test_c": "error"}


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan"])
def test_bad_call_timeout_is_a_usage_error(
    pytester: pytest.Pytester, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(plugin.ENV_CALL_TIMEOUT, value)
    pytester.makepyfile(test_a="def test_a(): pass\n")
    assert _run(pytester).ret == pytest.ExitCode.USAGE_ERROR
