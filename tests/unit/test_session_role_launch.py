"""`cheeseless session --role` binds the role at launch (stage c, item 1)."""

import json
from pathlib import Path
from typing import Any

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(bootstrap, "launch_host_session", fake)
    return seen


@pytest.mark.parametrize("role", ["author", "reference", "implementer"])
def test_session_passes_the_role_to_the_launcher(monkeypatch, tmp_path, role):
    seen = _capture(monkeypatch)
    code = bootstrap.main(
        ["session", "--state-dir", str(tmp_path), "--task", "t", "--role", role, "--", "/bin/true"]
    )
    assert code == 0 and seen["role"] == role


def test_session_without_role_is_the_implementer(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    bootstrap.main(["session", "--state-dir", str(tmp_path), "--task", "t", "--", "/bin/true"])
    assert seen["role"] == "implementer"


def test_an_unknown_role_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        bootstrap.main(
            ["session", "--state-dir", str(tmp_path), "--role", "root", "--", "/bin/true"]
        )
    assert exc.value.code == 2


def _launch(toy_repo: Path, tmp_path: Path, role: str, task: str = "a" * 64) -> FileStore:
    repo = GitRepository.discover(toy_repo)
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    code = bootstrap.launch_host_session(
        state_dir=tmp_path / "state",
        cwd=repo.root,
        task_id=task,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=["/bin/true"],
        role=role,
    )
    assert code == 0
    return store


@pytest.mark.parametrize("role", ["author", "reference"])
def test_a_pre_freeze_role_session_launches_and_clears_its_record(toy_repo, tmp_path, role):
    store = _launch(toy_repo, tmp_path, role)
    # the record existed for the session's life and is gone with it
    assert not (store._root / "sessions.json").exists() or not json.loads(
        (store._root / "sessions.json").read_text()
    )


def test_an_implementer_session_still_needs_a_frozen_task(toy_repo, tmp_path):
    with pytest.raises(Exception):  # noqa: B017 - the exact refusal is the existing one
        _launch(toy_repo, tmp_path, "implementer")
