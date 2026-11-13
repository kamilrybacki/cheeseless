"""Environment requirements for tests: skip locally, fail when CI demands them (P10-8).

``needs(ok, reason)`` marks a test (or module) as requiring an environment feature such
as bubblewrap, unprivileged user namespaces or mitmproxy. When the feature is missing the
test is skipped, unless ``CHEESELESS_REQUIRE_BWRAP=1`` is set: then it FAILS, so a CI
runner that silently lost its sandbox cannot turn the security suite into a green no-op.
"""

from __future__ import annotations

import os

import pytest

REQUIRE_ENV = "CHEESELESS_REQUIRE_BWRAP"
MARKER = "cheeseless_needs"


def required() -> bool:
    return os.environ.get(REQUIRE_ENV, "") == "1"


def outcome_for(available: bool, *, require: bool) -> str:
    """Return ``run``, ``skip`` or ``fail`` for a requirement."""
    if available:
        return "run"
    return "fail" if require else "skip"


def needs(available: bool, reason: str) -> pytest.MarkDecorator:
    return getattr(pytest.mark, MARKER)(available, reason)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", f"{MARKER}(available, reason): environment requirement (see tests/requirements)"
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    for mark in item.iter_markers(MARKER):
        available, reason = mark.args
        outcome = outcome_for(bool(available), require=required())
        if outcome == "fail":
            pytest.fail(f"{REQUIRE_ENV}=1 but requirement missing: {reason}", pytrace=False)
        if outcome == "skip":
            pytest.skip(reason)
