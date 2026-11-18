"""Test-wide isolation: no test touches the real home, state or runtime directories."""

import os
from pathlib import Path

import pytest

from tests.toy import make_repo

pytest_plugins = ["pytester", "tests.requirements"]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var, sub in (("HOME", "home"), ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "run")):
        path = tmp_path / "_env" / sub
        path.mkdir(parents=True, mode=0o700)
        monkeypatch.setenv(var, str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


@pytest.fixture(autouse=True)
def _no_terminal_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process CLI calls get a refusing presence port (ADR 0017).

    Whoever runs the suite from a terminal is never asked to type a code; a test that needs
    the real adapter puts it back. Child processes are unaffected and use the real wiring.
    """
    from cheeseless import bootstrap
    from tests.presence_fakes import Refusing

    monkeypatch.setattr(bootstrap, "TtyPresence", Refusing)


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "toy")


E2E_AGENT_ENV = "CHEESELESS_E2E_AGENT"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests that drive a real coding-agent CLI unless the operator opts in."""
    if os.environ.get(E2E_AGENT_ENV) == "1":
        return
    skip = pytest.mark.skip(reason=f"set {E2E_AGENT_ENV}=1 to run real-agent E2E tests")
    for item in items:
        if "e2e_agent" in item.keywords:
            item.add_marker(skip)
