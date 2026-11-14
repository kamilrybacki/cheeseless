"""Opt-in wiring for the live GitHub release gate (``live_github`` marker)."""

from __future__ import annotations

import os

import pytest

from tests.live.config import ENABLE


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"live_github: runs against real GitHub canary repositories; skipped unless {ENABLE}=1",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(ENABLE) == "1":
        return
    skip = pytest.mark.skip(reason=f"set {ENABLE}=1 to run the live GitHub release gate")
    for item in items:
        if "live_github" in item.keywords:
            item.add_marker(skip)
