"""The runner plugin's JSON report and its mapping to :class:`SuiteRun`.

The report is read before the exit status is interpreted: pytest exits 2 both for an
interrupt and for collection errors, and only the report tells them apart. Anything the
report does not explain, or a report that is missing or malformed, becomes an
``infra_cause`` so that every criterion fails closed.
"""

import json
from typing import Any

from cheeseless.domain.evaluation import CollectedTest, SuiteRun, TestOutcome

REPORT_VERSION = 1
# pytest.ExitCode: OK, TESTS_FAILED, NO_TESTS_COLLECTED.
_ORDINARY_EXITS = frozenset({0, 1, 5})
_INTERRUPTED = 2
_OUTCOMES = {o.value: o for o in TestOutcome}


class _Malformed(ValueError):
    pass


def _strs(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Malformed("expected a list of strings")
    return tuple(value)


def _collected(value: object) -> tuple[CollectedTest, ...]:
    if not isinstance(value, list):
        raise _Malformed("collected must be a list")
    tests = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("node_id"), str):
            raise _Malformed("bad collected entry")
        tests.append(CollectedTest(item["node_id"], _strs(item.get("criteria"))))
    return tuple(tests)


def _outcomes(value: object) -> dict[str, TestOutcome]:
    if not isinstance(value, dict):
        raise _Malformed("outcomes must be an object")
    out: dict[str, TestOutcome] = {}
    for node_id, raw in value.items():
        if not isinstance(raw, str) or raw not in _OUTCOMES:
            raise _Malformed(f"bad outcome for {node_id!r}")
        out[node_id] = _OUTCOMES[raw]
    return out


def _parse(raw: bytes) -> tuple[int, SuiteRun]:
    try:
        data: Any = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _Malformed("not JSON") from exc
    if not isinstance(data, dict) or data.get("version") != REPORT_VERSION:
        raise _Malformed("unknown report version")
    status = data.get("exitstatus")
    if not isinstance(status, int) or isinstance(status, bool):
        raise _Malformed("bad exit status")
    run = SuiteRun(
        collected=_collected(data.get("collected")),
        outcomes=_outcomes(data.get("outcomes")),
        collection_errors=_strs(data.get("collection_errors")),
    )
    return status, run


def _with_cause(run: SuiteRun, cause: str) -> SuiteRun:
    return SuiteRun(run.collected, run.outcomes, run.collection_errors, cause)


def suite_from_report(raw: bytes | None, exit_code: int, *, timed_out: bool = False) -> SuiteRun:
    """Map one pytest session (its report and process exit status) to a SuiteRun."""
    if timed_out:
        return SuiteRun(infra_cause="the test suite timed out and was killed")
    if raw is None:
        return SuiteRun(infra_cause=f"the runner wrote no report (exit status {exit_code})")
    try:
        reported, run = _parse(raw)
    except _Malformed as exc:
        return SuiteRun(infra_cause=f"malformed runner report: {exc}")
    if reported != exit_code:
        return _with_cause(run, f"exit status mismatch: report {reported}, process {exit_code}")
    if exit_code in _ORDINARY_EXITS:
        return run
    if exit_code == _INTERRUPTED and run.collection_errors:
        return run
    if exit_code == _INTERRUPTED:
        return _with_cause(run, "pytest was interrupted (exit status 2)")
    return _with_cause(run, f"pytest failed with exit status {exit_code}")
