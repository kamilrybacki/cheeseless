"""``cheeseless dev-events`` leaks nothing it should not, in any stage (S4).

The store is built through every stage. Canaries sit where a leak could come from: a
dispute note, a hidden test name and body, an author transcript line, a seed, a strength
review reason. The command runs with ``--once`` and a bounded ``--follow``; the output bytes
are checked for canaries, 64-hex digests, 32-hex session ids and paths. Every line must
validate against the closed v1 schema. The store directory must be byte-identical before and
after. Write or conflicting flags must be refused. The MCP and UDS adapters must not expose
or reference the observer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.inbound.cli.dev_events import run_dev_events
from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.adapters.outbound.store.observe_store import ObserveStore
from cheeseless.bootstrap import main
from cheeseless.domain.dev_events import Actor, Awaiting, EventKind, Stage
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState
from tests.schema_check import violations

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "schemas" / "dev-event-v1.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

DIGEST = "a" * 64
PROPOSAL = "c" * 64
SESSION_AUTHOR = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
SESSION_IMPLEMENTER = "ffffffff-ffff-4fff-8fff-ffffffffffff"
START = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)

CANARIES = (
    "CANARY-NOTE-7f3a",
    "CANARY-TESTNAME-9b2c",
    "CANARY-BODY-4d1e",
    "CANARY-TRANSCRIPT-5a6b",
    "CANARY-SEED-8c9d",
    "CANARY-STRENGTH-0e1f",
)
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")
STAGE_LINEAGES = ("l-spec", "l-tests", "l-lock", "l-refreeze", "l-disputed", "l-blocked")
TERMINAL_LINEAGES = ("l-done", "l-aborted")


def _attempt(lineage: str, state: ReferenceState) -> ReferenceAttempt:
    return ReferenceAttempt(
        f"attempt-{lineage}",
        "1" * 32,
        1,
        DIGEST,
        ReferenceMode.HEADLESS,
        False,
        state,
        lineage,
        None,
        None,
    )


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # noqa: S603, S607
    return path


def _common_dir(repo: Path) -> Path:
    return GitRepository.discover(repo).common_dir()


def _seed_every_stage(state_home: Path, repo: Path) -> None:
    root = store_root(_common_dir(repo), state_home)
    store = FileStore(root)
    states = (
        DevState(lineage_id="l-spec", phase=DevPhase.SPEC, author_session=SESSION_AUTHOR),
        DevState(
            lineage_id="l-tests",
            phase=DevPhase.TESTS,
            spec_digest=DIGEST,
            author_session=SESSION_AUTHOR,
        ),
        DevState(
            lineage_id="l-lock",
            phase=DevPhase.IMPLEMENT,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION_IMPLEMENTER,
        ),
        DevState(
            lineage_id="l-refreeze",
            phase=DevPhase.IMPLEMENT,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION_IMPLEMENTER,
            holdout_failures=3,
        ),
        DevState(
            lineage_id="l-disputed",
            phase=DevPhase.DISPUTED,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION_IMPLEMENTER,
        ),
        DevState(
            lineage_id="l-blocked",
            phase=DevPhase.BLOCKED,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION_IMPLEMENTER,
        ),
        DevState(
            lineage_id="l-done",
            phase=DevPhase.DONE,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION_IMPLEMENTER,
        ),
        DevState(lineage_id="l-aborted", phase=DevPhase.ABORTED),
    )
    for state in states:
        store.save_dev_state(state)
    store.save_reference_attempt(_attempt("l-tests", ReferenceState.RUNNING))
    store.save_reference_attempt(_attempt("l-lock", ReferenceState.FAILED))
    _plant_hidden_material(root)


def _plant_hidden_material(root: Path) -> None:
    """Material the observer must never read. Each canary would show up if it leaked."""
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "transcripts" / "author.jsonl").write_text(
        json.dumps({"text": CANARIES[3]}) + "\n", encoding="utf-8"
    )
    (root / "hidden").mkdir(exist_ok=True)
    (root / "hidden" / "test_hidden.py").write_text(
        f"def {CANARIES[1]}():\n    assert '{CANARIES[2]}'\n", encoding="utf-8"
    )
    (root / "seeds.json").write_text(json.dumps({"seed": CANARIES[4]}), encoding="utf-8")
    (root / "strength-review.json").write_text(
        json.dumps({"reason": CANARIES[5]}), encoding="utf-8"
    )
    record = {
        "dispute_id": "d1",
        "task_id": "task-1",
        "lineage_id": "l-disputed",
        "criterion_id": "criterion-1",
        "code": "contradicts_spec",
        "note": f"{CANARIES[0]} the test expects the hidden body",
        "status": "open",
        "resolved_by": "",
    }
    (root / "disputes.json").write_text(json.dumps({"records": [record]}), encoding="utf-8")


def _run_main(
    capsys: pytest.CaptureFixture[str], repo: Path, state_home: Path, *extra: str
) -> tuple[int, str]:
    capsys.readouterr()
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        code = main(["dev-events", "--state-dir", str(state_home), *extra])
    finally:
        os.chdir(cwd)
    return code, capsys.readouterr().out


def _store_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        digest.update(dirpath.encode())
        for name in sorted(dirnames):
            digest.update(name.encode())
        for name in sorted(filenames):
            digest.update(name.encode())
            digest.update(Path(dirpath, name).read_bytes())
    return digest.hexdigest()


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    repo = _git_repo(tmp_path / "repo")
    state_home = tmp_path / "state"
    _seed_every_stage(state_home, repo)
    return repo, state_home


@pytest.fixture
def clock() -> Iterator[dict[str, Any]]:
    holder: dict[str, Any] = {"now": START, "slept": []}
    yield holder


def _assert_clean(text: str, *, paths: tuple[str, ...] = ()) -> None:
    for canary in CANARIES:
        assert canary not in text
    assert not HEX64.search(text), "a 64-hex digest leaked"
    assert not HEX32.search(text), "a 32-hex session id leaked"
    for session in (SESSION_AUTHOR, SESSION_IMPLEMENTER):
        assert session not in text, "a session id leaked"
    for path in paths:
        assert path not in text, "a filesystem path leaked"
    assert "/home/" not in text


def test_once_on_every_stage_leaks_no_canary_digest_session_or_path(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    outputs = []
    for lineage in STAGE_LINEAGES + TERMINAL_LINEAGES:
        code, out = _run_main(capsys, repo, state_home, "--lineage", lineage, "--once")
        assert code == 0, lineage
        outputs.append(out)
    everything = "".join(outputs)

    assert everything.count("\n") >= len(STAGE_LINEAGES) + len(TERMINAL_LINEAGES)
    _assert_clean(everything, paths=(str(repo), str(state_home)))


def test_bounded_follow_on_a_non_terminal_stage_leaks_nothing(
    world: tuple[Path, Path], clock: dict[str, Any]
) -> None:
    repo, state_home = world
    root = store_root(_common_dir(repo), state_home)
    out = StringIO()

    code = run_dev_events(
        ObserveStore(root),
        "l-disputed",
        follow=True,
        interval=1.0,
        out=out,
        now=lambda: clock["now"],
        sleep=lambda seconds: clock.update(now=clock["now"] + timedelta(seconds=seconds)),
        limit=20,
    )

    assert code == 0
    _assert_clean(out.getvalue(), paths=(str(repo), str(state_home)))


def test_follow_on_terminal_stages_leaks_nothing_and_ends(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    for lineage in TERMINAL_LINEAGES:
        code, out = _run_main(capsys, repo, state_home, "--lineage", lineage, "--follow")
        assert code == 0, lineage
        kinds = [json.loads(line)["kind"] for line in out.splitlines()]
        assert kinds[-1] == "end", lineage
        _assert_clean(out, paths=(str(repo), str(state_home)))


def test_every_output_line_validates_against_the_closed_schema(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    checked = 0
    for lineage in STAGE_LINEAGES + TERMINAL_LINEAGES:
        _code, out = _run_main(capsys, repo, state_home, "--lineage", lineage, "--once")
        for line in out.splitlines():
            assert violations(SCHEMA, json.loads(line)) == [], line
            checked += 1
    assert checked == len(STAGE_LINEAGES) + len(TERMINAL_LINEAGES)


def test_every_enum_field_is_a_domain_enum_member(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    members: dict[str, set[str]] = {
        "kind": {m.value for m in EventKind},
        "stage": {m.value for m in Stage},
        "actor": {m.value for m in Actor},
        "awaiting": {m.value for m in Awaiting},
    }
    for lineage in STAGE_LINEAGES + TERMINAL_LINEAGES:
        _code, out = _run_main(capsys, repo, state_home, "--lineage", lineage, "--once")
        line = json.loads(out)
        for field, allowed in members.items():
            if field in line:
                assert line[field] in allowed, (lineage, field, line[field])


def test_the_store_directory_is_byte_identical_after_observation(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    root = store_root(_common_dir(repo), state_home)
    before = _store_digest(root)

    for lineage in STAGE_LINEAGES + TERMINAL_LINEAGES:
        _run_main(capsys, repo, state_home, "--lineage", lineage, "--once")
    _run_main(capsys, repo, state_home, "--lineage", "l-done", "--follow")

    assert _store_digest(root) == before


def test_observing_an_unknown_lineage_creates_no_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    state_home = tmp_path / "state"

    code, out = _run_main(capsys, repo, state_home, "--lineage", "nobody", "--once")

    assert code == 3
    assert out == ""
    assert not state_home.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--as", "author"],
        ["--bogus"],
        ["--once", "--follow"],
    ],
)
def test_write_or_conflicting_flags_exit_two(
    extra: list[str], world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state_home = world
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        with pytest.raises(SystemExit) as info:
            main(["dev-events", "--lineage", "l-done", "--state-dir", str(state_home), *extra])
    finally:
        os.chdir(cwd)
    assert info.value.code == 2


@pytest.mark.parametrize(
    ("author", "disputes", "dev"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_no_mcp_server_configuration_exposes_dev_events(
    author: bool, disputes: bool, dev: bool
) -> None:
    server = build_mcp_server(
        object(),  # type: ignore[arg-type]  # the builder never calls the client
        author=author,
        disputes=disputes,
        dev=dev,
    )
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert not any("dev-events" in name or "dev_events" in name for name in names), names


def test_the_mcp_and_uds_adapters_do_not_reference_the_observer() -> None:
    inbound = REPO_ROOT / "src" / "cheeseless" / "adapters" / "inbound"
    for sub in ("mcp", "uds"):
        for path in (inbound / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "dev_events" not in text, path
            assert "dev-events" not in text, path
