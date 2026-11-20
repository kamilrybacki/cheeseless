"""``cheeseless dev-lineages``: one JSON line, fixed error line, fixed exit codes."""

import argparse
import io
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.inbound.cli.dev_lineages import run_dev_lineages
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.bootstrap import main
from cheeseless.bootstrap_dev_lineages import run_dev_lineages_command
from cheeseless.domain.dev_lineages import STORE_ERROR_LINE, LineageStamp
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.dispute import DisputeRecord
from cheeseless.domain.reference_attempt import ReferenceAttempt
from cheeseless.ports.store import StoreError
from tests.schema_check import violations

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "dev-lineages-v1.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
WHEN = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


@dataclass
class FakeListing:
    stamps: tuple[LineageStamp, ...] = ()
    error: BaseException | None = None

    def lineage_stamps(self) -> tuple[LineageStamp, ...]:
        if self.error is not None:
            raise self.error
        return self.stamps

    def dev_state(self, lineage_id: str) -> DevState | None:
        return DevState(lineage_id=lineage_id, phase=DevPhase.SPEC)

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        return ()

    def disputes(self) -> tuple[DisputeRecord, ...]:
        return ()


class BrokenPipeOut(io.StringIO):
    def write(self, text: str) -> int:
        raise BrokenPipeError


def _run(port: FakeListing) -> tuple[int, str]:
    out = io.StringIO()
    return run_dev_lineages(port, out=out), out.getvalue()


def test_an_empty_listing_exits_zero_with_one_line() -> None:
    code, out = _run(FakeListing())

    assert code == 0
    assert out == '{"schema":1,"lineages":[]}\n'


def test_a_listing_prints_one_valid_line() -> None:
    code, out = _run(FakeListing((LineageStamp("demo", WHEN),)))

    assert code == 0
    assert out.count("\n") == 1
    line = json.loads(out)
    assert violations(SCHEMA, line) == []
    assert line["lineages"] == [
        {"lineage": "demo", "stage": "spec", "updated_at": "2026-11-19T10:00:00Z"}
    ]


@pytest.mark.parametrize(
    "error", [StoreError("corrupt /home/x/store"), OSError("disk"), ValueError("bad")]
)
def test_a_store_error_prints_the_fixed_line_and_exits_one(error: Exception) -> None:
    code, out = _run(FakeListing(error=error))

    assert code == 1
    assert out == STORE_ERROR_LINE + "\n"
    assert violations(SCHEMA, json.loads(out)) == []


def test_an_unexpected_error_is_not_swallowed() -> None:
    with pytest.raises(RuntimeError):
        _run(FakeListing(error=RuntimeError("bug")))


def test_a_closed_pipe_exits_zero() -> None:
    assert run_dev_lineages(FakeListing(), out=BrokenPipeOut()) == 0


def test_an_interrupt_exits_130() -> None:
    assert run_dev_lineages(FakeListing(error=KeyboardInterrupt()), out=io.StringIO()) == 130


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # noqa: S603, S607
    return path


def _main(repo: Path, *argv: str) -> int:
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(["dev-lineages", *argv])
    finally:
        os.chdir(cwd)


def test_main_lists_the_store_of_this_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    state = tmp_path / "state"
    root = store_root(GitRepository.discover(repo).common_dir(), state)
    FileStore(root).save_dev_state(DevState(lineage_id="demo", phase=DevPhase.SPEC))

    assert _main(repo, "--state-dir", str(state)) == 0

    line = json.loads(capsys.readouterr().out)
    assert violations(SCHEMA, line) == []
    assert [row["lineage"] for row in line["lineages"]] == ["demo"]


def test_main_on_a_missing_store_lists_nothing_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    state = tmp_path / "state"

    assert _main(repo, "--state-dir", str(state)) == 0

    assert capsys.readouterr().out == '{"schema":1,"lineages":[]}\n'
    assert not state.exists()


def test_main_on_a_corrupt_store_prints_the_fixed_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    state = tmp_path / "state"
    root = store_root(GitRepository.discover(repo).common_dir(), state)
    (root / "dev").mkdir(parents=True)
    (root / "dev" / "state-demo.json").write_bytes(b"not json")

    assert _main(repo, "--state-dir", str(state)) == 1

    captured = capsys.readouterr()
    assert captured.out == STORE_ERROR_LINE + "\n"
    assert str(tmp_path) not in captured.out + captured.err


def test_outside_a_git_repository_exits_two_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert run_dev_lineages_command(argparse.Namespace()) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not inside a git repository" in captured.err
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize("extra", [["--lineage", "demo"], ["--follow"], ["--bogus"], ["x"]])
def test_unknown_flags_are_a_usage_error(extra: list[str], tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    with pytest.raises(SystemExit) as info:
        _main(repo, *extra)

    assert info.value.code == 2
