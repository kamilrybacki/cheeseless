"""Test-wide isolation: no test touches the real home, state or runtime directories."""

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


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "toy")
