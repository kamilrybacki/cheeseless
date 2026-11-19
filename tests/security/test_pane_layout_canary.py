"""The pane layout and the ``panes`` event leak nothing and stay inside the state dir (S6).

A store holds canaries where a leak could come from: a hidden test name and body, a spec
digest, a session id. ``cheeseless dev --panes`` runs against it with ``host_session``
replaced by a fake; its stderr (the ``panes`` event) and every socket path are scanned for
canaries, 64-hex digests and 32-hex session ids. Every path must stay inside the state dir.
The store must be byte-identical before and after. The MCP and UDS adapters must not
reference the layout, the relay or the pane client.
"""

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from cheeseless import bootstrap_dev, pane_layout
from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.app.dev_flow import PhaseStart
from cheeseless.pane_layout import PaneLayoutError, PaneRole

REPO_ROOT = Path(__file__).resolve().parents[2]
CANARIES = (
    "CANARY-HIDDEN-TEST-NAME-3c1d",
    "CANARY-HIDDEN-BODY-7e5a",
    "CANARY-SPEC-NOTE-2b9f",
)
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")
DIGEST = "a" * 64
SESSION = "e" * 32
LINEAGE = "demo"


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
def state() -> Path:
    # AF_UNIX paths are short; pytest's tmp_path would push the role socket over the limit.
    root = Path(tempfile.mkdtemp(prefix="cl-"))
    store = root / "cheeseless" / "0123456789abcdef"
    store.mkdir(parents=True)
    (store / "hidden.json").write_text(
        json.dumps({"name": CANARIES[0], "body": CANARIES[1], "note": CANARIES[2]}) + DIGEST,
        encoding="utf-8",
    )
    (store / f"session-{SESSION}.json").write_text(DIGEST, encoding="utf-8")
    return root


class _Proc:
    def wait(self) -> int:
        return 0


@pytest.fixture
def run_paned(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    @contextmanager
    def host_session(**_kwargs: Any) -> Iterator[dict[str, object]]:
        yield {"agent_proc": _Proc()}

    monkeypatch.setattr(bootstrap_dev, "host_session", host_session)

    class _NoState:
        def dev_state(self, _lineage: str) -> None:
            return None

    def run(state: Path, role: str) -> str:
        args: argparse.Namespace = build_parser().parse_args(
            ["dev", "--lineage", LINEAGE, "--state-dir", str(state), "--panes"]
        )
        start = PhaseStart(
            role=role,
            dev_lineage=LINEAGE if role == "author" else None,
            session=None,
            spec_digest=DIGEST,
            reference=False,
        )
        assert bootstrap_dev._run_session(args, start, ["/bin/true"], LINEAGE, _NoState()) == 0  # type: ignore[arg-type]
        captured = capsys.readouterr()
        return captured.err + captured.out

    return run


def _assert_clean(text: str) -> None:
    for canary in CANARIES:
        assert canary not in text
    assert not HEX64.search(text), "a 64-hex digest reached the output"
    assert not HEX32.search(text), "a 32-hex session id reached the output"


@pytest.mark.parametrize("role", ["author", "implementer"])
def test_the_panes_event_carries_no_canary_digest_or_session_id(run_paned, state, role) -> None:
    _assert_clean(run_paned(state, role))


_EVENT_KEYS = {"event", "schema", "lineage", "panes"}
_PANE_KEYS = {"role", "mode", "socket"}


def _panes_event(out: str) -> dict[str, Any]:
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert len(events) == 1
    return events[0]


def _assert_closed_schema(event: dict[str, Any]) -> None:
    assert set(event) == _EVENT_KEYS
    assert event["lineage"] == LINEAGE
    for pane in event["panes"]:
        assert set(pane) == _PANE_KEYS
        assert pane["role"] in {r.value for r in PaneRole}
        assert pane["mode"] in {m.value for m in pane_layout.PaneMode}


@pytest.mark.parametrize("role", ["author", "implementer"])
def test_the_panes_event_keys_and_values_come_from_a_closed_set(run_paned, state, role) -> None:
    _assert_closed_schema(_panes_event(run_paned(state, role)))


def test_negative_control_an_injected_digest_is_caught_by_the_scan(run_paned, state) -> None:
    """Prove the canary scan has teeth: the same checks must fail on a tainted event."""
    event = _panes_event(run_paned(state, "author"))
    for tainted in (
        {**event, "spec": DIGEST},  # a new key carrying a digest
        {**event, "lineage": DIGEST},  # a digest in a known value
        {**event, "panes": [{**event["panes"][0], "session": SESSION}]},
    ):
        with pytest.raises(AssertionError):
            _assert_closed_schema(tainted)
            _assert_clean(json.dumps(tainted))
    with pytest.raises(AssertionError):
        _assert_clean(json.dumps({**event, "lineage": DIGEST}))
    with pytest.raises(AssertionError):
        _assert_clean(json.dumps({**event, "note": CANARIES[0]}))


@pytest.mark.parametrize("role", ["author", "implementer"])
def test_the_panes_event_paths_stay_inside_the_state_dir(run_paned, state, role) -> None:
    out = run_paned(state, role)
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert len(events) == 1
    for pane in events[0]["panes"]:
        assert Path(pane["socket"]).resolve().is_relative_to(state.resolve())
        assert Path(pane["socket"]).name in {f"{r.value}.sock" for r in PaneRole}


def test_running_the_panes_command_never_writes_the_store(run_paned, state) -> None:
    before = _store_digest(state)
    run_paned(state, "author")
    assert _store_digest(state) == before


@pytest.mark.parametrize("role", list(PaneRole))
def test_every_socket_path_is_free_of_digests_and_session_ids(state, role) -> None:
    path = str(pane_layout.socket_path(state, LINEAGE, role))
    _assert_clean(path.removeprefix(str(state)))


@pytest.mark.parametrize("lineage", ["../escape", "a/b", "", ".", "..", "x" * 65, "A"])
def test_a_lineage_that_could_leave_the_state_dir_is_refused(state, lineage) -> None:
    with pytest.raises(ValueError):
        pane_layout.socket_path(state, lineage, PaneRole.CODER)


def test_a_symlinked_state_subdir_cannot_move_the_socket_outside(tmp_path: Path) -> None:
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "cheeseless").mkdir(parents=True)
    (state / "cheeseless" / "panes").symlink_to(outside)
    with pytest.raises(PaneLayoutError):
        pane_layout.socket_path(state, LINEAGE, PaneRole.CODER)


def test_the_mcp_and_uds_adapters_do_not_reference_the_panes() -> None:
    inbound = REPO_ROOT / "src" / "cheeseless" / "adapters" / "inbound"
    forbidden = ("pane_layout", "pane_client", "outbound.pane", "pane-relay", "panes_event")
    for sub in ("mcp", "uds"):
        for path in (inbound / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for word in forbidden:
                assert word not in text, (path, word)
