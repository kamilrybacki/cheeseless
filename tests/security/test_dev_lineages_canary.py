"""Security: the ``dev-lineages`` listing leaks nothing and writes nothing.

The listing reads the same store the dev-events observer reads, with hidden material
planted next to it: transcripts, hidden tests, seeds, a strength review and a dispute
note, each carrying a canary. The only values allowed out are lineage ids, stage enums
and UTC times: no canary, digest, session id, path or note text. The store must be
byte-identical afterwards, a missing store must stay missing, and neither MCP nor UDS
may expose the listing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.outbound.store.file_store import store_root
from cheeseless.bootstrap import main
from cheeseless.domain.dev_events import Stage
from cheeseless.domain.dev_lineages import STORE_ERROR_LINE
from tests.schema_check import violations
from tests.security.test_dev_events_canary import (
    STAGE_LINEAGES,
    TERMINAL_LINEAGES,
    _assert_clean,
    _common_dir,
    _git_repo,
    _seed_every_stage,
    _store_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "schemas" / "dev-lineages-v1.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
ALL_LINEAGES = STAGE_LINEAGES + TERMINAL_LINEAGES


def _run(
    capsys: pytest.CaptureFixture[str], repo: Path, command: str, state_home: Path, *extra: str
) -> tuple[int, str, str]:
    capsys.readouterr()
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        code = main([command, "--state-dir", str(state_home), *extra])
    finally:
        os.chdir(cwd)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    repo = _git_repo(tmp_path / "repo")
    state_home = tmp_path / "state"
    _seed_every_stage(state_home, repo)
    return repo, state_home


def test_the_listing_on_every_stage_leaks_no_canary_digest_session_or_path(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world

    code, out, err = _run(capsys, repo, "dev-lineages", state_home)

    assert code == 0
    assert out.count("\n") == 1
    assert err == ""
    _assert_clean(out, paths=(str(repo), str(state_home)))
    line = json.loads(out)
    assert violations(SCHEMA, line) == []
    assert sorted(row["lineage"] for row in line["lineages"]) == sorted(ALL_LINEAGES)
    for row in line["lineages"]:
        assert set(row) == {"lineage", "stage", "updated_at"}
        assert row["stage"] in {m.value for m in Stage}


def test_each_stage_matches_the_dev_events_view(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    _code, out, _err = _run(capsys, repo, "dev-lineages", state_home)
    listed = {row["lineage"]: row["stage"] for row in json.loads(out)["lineages"]}

    for lineage in ALL_LINEAGES:
        _c, event, _e = _run(capsys, repo, "dev-events", state_home, "--lineage", lineage)
        assert listed[lineage] == json.loads(event)["stage"], lineage


def test_the_store_directory_is_byte_identical_after_listing(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    root = store_root(_common_dir(repo), state_home)
    before = _store_digest(root)

    for _ in range(3):
        assert _run(capsys, repo, "dev-lineages", state_home)[0] == 0

    assert _store_digest(root) == before


def test_a_missing_store_lists_nothing_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    state_home = tmp_path / "state"

    code, out, _err = _run(capsys, repo, "dev-lineages", state_home)

    assert code == 0
    assert out == '{"schema":1,"lineages":[]}\n'
    assert not state_home.exists()


def test_a_corrupt_state_prints_only_the_fixed_line(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    root = store_root(_common_dir(repo), state_home)
    (root / "dev" / "state-l-spec.json").write_text(
        '{"note": "CANARY-NOTE-7f3a", "path": "/home/x"}', encoding="utf-8"
    )

    code, out, err = _run(capsys, repo, "dev-lineages", state_home)

    assert code == 1
    assert out == STORE_ERROR_LINE + "\n"
    assert violations(SCHEMA, json.loads(out)) == []
    _assert_clean(out + err, paths=(str(repo), str(state_home)))


@pytest.mark.parametrize(
    ("author", "disputes", "dev"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_no_mcp_server_configuration_exposes_dev_lineages(
    author: bool, disputes: bool, dev: bool
) -> None:
    server = build_mcp_server(
        object(),  # type: ignore[arg-type]  # the builder never calls the client
        author=author,
        disputes=disputes,
        dev=dev,
    )
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert not any("lineages" in name for name in names), names


def test_the_mcp_and_uds_adapters_do_not_reference_the_listing() -> None:
    inbound = REPO_ROOT / "src" / "cheeseless" / "adapters" / "inbound"
    for sub in ("mcp", "uds"):
        for path in (inbound / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "dev_lineages" not in text, path
            assert "dev-lineages" not in text, path
            assert "lineage_stamps" not in text, path
