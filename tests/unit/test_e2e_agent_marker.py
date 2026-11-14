"""The e2e_agent marker is skipped unless the operator opts in with CHEESELESS_E2E_AGENT=1."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import E2E_AGENT_ENV

TEST_BODY = """
import pytest

@pytest.mark.e2e_agent
def test_real_agent():
    pass

def test_plain():
    pass
"""


def _run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, flag: str | None):
    monkeypatch.delenv(E2E_AGENT_ENV, raising=False)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    if flag is not None:
        monkeypatch.setenv(E2E_AGENT_ENV, flag)
    pytester.makeini("[pytest]\nmarkers =\n    e2e_agent: real agent\n")
    pytester.makeconftest(
        "from tests.conftest import pytest_collection_modifyitems  # noqa: F401\n"
    )
    pytester.makepyfile(test_probe=TEST_BODY)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--no-cov")


def test_e2e_agent_skipped_by_default(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, None).assert_outcomes(passed=1, skipped=1)


def test_e2e_agent_skipped_when_flag_is_not_one(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, "0").assert_outcomes(passed=1, skipped=1)


def test_e2e_agent_runs_with_flag(pytester, monkeypatch) -> None:
    _run(pytester, monkeypatch, "1").assert_outcomes(passed=2)
