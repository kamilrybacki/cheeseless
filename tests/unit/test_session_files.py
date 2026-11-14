import json
import os
import stat
from pathlib import Path

import pytest

from cheeseless.session_files import copy_evidence, seed_home, write_projection_list
from cheeseless.session_options import SessionOptionError


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_seed_home_copies_content_with_private_modes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    seed_home(home, ((".cfg/deep/c.json", b"payload"),))
    target = home / ".cfg" / "deep" / "c.json"
    assert target.read_bytes() == b"payload"
    assert not target.is_symlink()
    assert mode(target) == 0o600
    assert mode(home / ".cfg") == 0o700
    assert mode(home / ".cfg" / "deep") == 0o700


def test_seed_home_ignores_umask(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    old = os.umask(0)
    try:
        seed_home(home, (("d/f", b"x"),))
    finally:
        os.umask(old)
    assert mode(home / "d") == 0o700
    assert mode(home / "d" / "f") == 0o600


def test_seed_home_refuses_existing_destination(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "f").write_text("old")
    with pytest.raises(SessionOptionError):
        seed_home(home, (("f", b"x"),))


def test_seed_home_refuses_symlink_destination(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("keep")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "f").symlink_to(outside)
    with pytest.raises(SessionOptionError):
        seed_home(home, (("f", b"attack"),))
    assert outside.read_text() == "keep"


def test_copy_evidence_copies_files_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".cache" / "t").mkdir(parents=True)
    (home / ".cache" / "t" / "transcript.jsonl").write_text("hello")
    (home / "top.txt").write_text("top")
    (tmp_path / "outside").write_text("host-secret")
    (home / "link").symlink_to(tmp_path / "outside")
    (home / "dirlink").symlink_to(tmp_path)
    os.mkfifo(home / "pipe")
    dest = tmp_path / "evidence"
    result = copy_evidence(home, dest, limit=1 << 20)
    assert (dest / "home" / ".cache" / "t" / "transcript.jsonl").read_text() == "hello"
    assert (dest / "home" / "top.txt").read_text() == "top"
    assert not (dest / "home" / "link").exists()
    assert not (dest / "home" / "dirlink").exists()
    assert not (dest / "home" / "pipe").exists()
    assert result.files == 2
    assert not result.truncated
    assert mode(dest / "home" / "top.txt") == 0o600
    assert mode(dest) == 0o700


def test_copy_evidence_enforces_total_cap(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for index in range(5):
        (home / f"f{index}").write_bytes(b"x" * 100)
    dest = tmp_path / "evidence"
    result = copy_evidence(home, dest, limit=250)
    copied = sum(p.stat().st_size for p in (dest / "home").iterdir())
    assert copied <= 250
    assert result.truncated
    assert "size cap" in (dest / "TRUNCATED").read_text()


def test_copy_evidence_refuses_existing_destination(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "f").write_text("x")
    dest = tmp_path / "evidence"
    dest.mkdir()
    with pytest.raises(SessionOptionError):
        copy_evidence(home, dest)
    assert not (dest / "home").exists()


def test_copy_evidence_refuses_symlink_destination(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "f").write_text("x")
    target = tmp_path / "target"
    target.mkdir()
    dest = tmp_path / "evidence"
    dest.symlink_to(target)
    with pytest.raises(SessionOptionError):
        copy_evidence(home, dest)
    assert list(target.iterdir()) == []


def test_copy_evidence_copies_hard_link_content_without_touching_original(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    original = tmp_path / "original"
    original.write_text("agent content")
    os.link(original, home / "hard")
    dest = tmp_path / "evidence"
    copy_evidence(home, dest)
    copied = dest / "home" / "hard"
    assert copied.read_text() == "agent content"
    assert copied.stat().st_ino != original.stat().st_ino
    copied.write_text("changed")
    assert original.read_text() == "agent content"


def test_copy_evidence_deep_tree_is_truncated_without_recursion_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    current = home
    for _ in range(500):
        current = current / "d"
        current.mkdir(parents=True)
    (current / "deep.txt").write_text("x")
    dest = tmp_path / "evidence"
    result = copy_evidence(home, dest)
    assert result.truncated
    assert "depth cap" in (dest / "TRUNCATED").read_text()


def test_copy_evidence_entry_cap_is_truncated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("cheeseless.session_files.EVIDENCE_ENTRIES", 5)
    home = tmp_path / "home"
    home.mkdir()
    for index in range(10):
        (home / f"f{index}").write_text("x")
    dest = tmp_path / "evidence"
    result = copy_evidence(home, dest)
    assert result.truncated
    assert "entry cap" in (dest / "TRUNCATED").read_text()


def test_write_projection_list_round_trips_newline_filename(tmp_path: Path) -> None:
    projection = tmp_path / "proj"
    (projection / "pkg").mkdir(parents=True)
    (projection / "pkg" / "a.py").write_text("")
    (projection / "b.py").write_text("")
    (projection / "line\nbreak.py").write_text("")
    (projection / "l").symlink_to(projection / "b.py")
    dest = tmp_path / "evidence"
    dest.mkdir()
    write_projection_list(projection, dest)
    found = json.loads((dest / "projection-files.json").read_text())
    assert found == ["b.py", "line\nbreak.py", "pkg/a.py"]


def test_write_projection_list_deep_tree_is_truncated(tmp_path: Path) -> None:
    projection = tmp_path / "proj"
    current = projection
    for _ in range(500):
        current = current / "d"
        current.mkdir(parents=True)
    dest = tmp_path / "evidence"
    dest.mkdir()
    write_projection_list(projection, dest)
    assert (dest / "TRUNCATED").exists()
    assert "depth cap" in (dest / "TRUNCATED").read_text()
