"""ObserveStore.lineage_stamps: which lineages have state, and when they last changed."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.adapters.outbound.store.observe_store import ObserveStore
from cheeseless.domain.dev_lineages import LineageStamp
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState
from cheeseless.ports.dev_observe import LineageListing
from cheeseless.ports.store import StoreError

DIGEST = "a" * 64
T1 = 1_763_546_400  # 2025-11-19T10:00:00Z
T2 = T1 + 3600


def _attempt(attempt_id: str, lineage: str | None) -> ReferenceAttempt:
    return ReferenceAttempt(
        attempt_id,
        "1" * 32,
        1,
        DIGEST,
        ReferenceMode.HEADLESS,
        False,
        ReferenceState.RUNNING,
        lineage,
        None,
        None,
    )


def _seed(root: Path, *lineages: str) -> None:
    store = FileStore(root)
    for lineage in lineages:
        store.save_dev_state(DevState(lineage_id=lineage, phase=DevPhase.SPEC))


def _touch(path: Path, seconds: int) -> None:
    os.utime(path, ns=(seconds * 10**9, seconds * 10**9))


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def test_the_store_satisfies_the_listing_port(tmp_path: Path) -> None:
    store: LineageListing = ObserveStore(tmp_path)

    assert store.lineage_stamps() == ()


def test_a_missing_store_lists_nothing_and_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "absent"

    assert ObserveStore(root).lineage_stamps() == ()
    assert not root.exists()


def test_each_state_file_is_one_stamp_at_its_mtime(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha", "beta")
    _touch(tmp_path / "dev" / "state-alpha.json", T1)
    _touch(tmp_path / "dev" / "state-beta.json", T2)

    stamps = sorted(ObserveStore(tmp_path).lineage_stamps(), key=lambda s: s.lineage_id)

    assert stamps == [LineageStamp("alpha", _at(T1)), LineageStamp("beta", _at(T2))]


def test_a_newer_reference_attempt_moves_the_lineage_forward(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha", "beta")
    store = FileStore(tmp_path)
    store.save_reference_attempt(_attempt("attempt-a", "alpha"))
    store.save_reference_attempt(_attempt("attempt-b", "beta"))
    for name in ("state-alpha.json", "state-beta.json"):
        _touch(tmp_path / "dev" / name, T1)
    _touch(tmp_path / "dev-reference" / "attempt-a.json", T2)
    _touch(tmp_path / "dev-reference" / "attempt-b.json", T1 - 60)

    stamps = {s.lineage_id: s.updated_at for s in ObserveStore(tmp_path).lineage_stamps()}

    assert stamps == {"alpha": _at(T2), "beta": _at(T1)}


def test_an_attempt_without_a_state_file_lists_nothing(tmp_path: Path) -> None:
    FileStore(tmp_path).save_reference_attempt(_attempt("attempt-a", "orphan"))

    assert ObserveStore(tmp_path).lineage_stamps() == ()


def test_temp_files_and_foreign_names_are_skipped(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha")
    dev = tmp_path / "dev"
    (dev / ".state-beta.json.0123456789abcdef.tmp").write_bytes(b"{}")
    (dev / "state-Bad Name.json").write_bytes(b"{}")
    (dev / "state-.json").write_bytes(b"{}")
    (dev / "notes.txt").write_bytes(b"x")
    (dev / "sub").mkdir()

    assert [s.lineage_id for s in ObserveStore(tmp_path).lineage_stamps()] == ["alpha"]


def test_a_symlink_in_the_dev_directory_fails_closed(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha")
    (tmp_path / "dev" / "state-beta.json").symlink_to(tmp_path / "dev" / "state-alpha.json")

    with pytest.raises(StoreError):
        ObserveStore(tmp_path).lineage_stamps()


def test_a_dev_entry_that_is_not_a_directory_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "dev").write_bytes(b"not a directory")

    with pytest.raises(StoreError):
        ObserveStore(tmp_path).lineage_stamps()


def test_listing_writes_nothing(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha")
    FileStore(tmp_path).save_reference_attempt(_attempt("attempt-a", "alpha"))
    before = sorted(
        (p, p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()
    )

    ObserveStore(tmp_path).lineage_stamps()

    after = sorted(
        (p, p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()
    )
    assert before == after


def test_a_reference_attempt_without_a_lineage_is_ignored(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha")
    FileStore(tmp_path).save_reference_attempt(_attempt("attempt-a", None))

    stamps = ObserveStore(tmp_path).lineage_stamps()

    assert [stamp.lineage_id for stamp in stamps] == ["alpha"]
