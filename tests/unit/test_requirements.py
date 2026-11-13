"""CHEESELESS_REQUIRE_BWRAP turns every environment skip into a failure (P10-8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.requirements import REQUIRE_ENV, outcome_for

TEST_BODY = """
import pytest
from tests.requirements import needs

pytestmark = needs(False, "needs a thing that is missing")

def test_body():
    pass
"""


def test_outcome_runs_when_available() -> None:
    assert outcome_for(True, require=True) == "run"
    assert outcome_for(True, require=False) == "run"


def test_outcome_skips_when_missing_and_not_required() -> None:
    assert outcome_for(False, require=False) == "skip"


def test_outcome_fails_when_missing_and_required() -> None:
    assert outcome_for(False, require=True) == "fail"


def _run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, flag: str | None):
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    if flag is not None:
        monkeypatch.setenv(REQUIRE_ENV, flag)
    pytester.makeconftest('pytest_plugins = ["tests.requirements"]')
    pytester.makepyfile(test_probe=TEST_BODY)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--no-cov")


def test_missing_requirement_skips_without_flag(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, None).assert_outcomes(skipped=1)


def test_missing_requirement_fails_with_flag(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, "1").assert_outcomes(errors=1)


def test_flag_zero_does_not_require(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, "0").assert_outcomes(skipped=1)
