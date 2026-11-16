"""ADR 0009: the author runner must never see the trusted store, whatever the interpreter path."""

import shutil
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import bwrap
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.ports.sandbox import SandboxError

CANARY = "STORE-CANARY-5d0c"

needs_bwrap = pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)


def _store(tmp_path: Path) -> Path:
    store = tmp_path / "root" / "store"
    store.mkdir(parents=True)
    (store / "hmac.key").write_text(CANARY)
    return store


def _link_chain(tmp_path: Path) -> Path:
    """tmp/a/python -> tmp/root/bin/python -> the real interpreter."""
    (tmp_path / "root" / "bin").mkdir(parents=True, exist_ok=True)
    inner = tmp_path / "root" / "bin" / "python"
    inner.symlink_to(Path(sys.executable).resolve())
    (tmp_path / "a").mkdir()
    outer = tmp_path / "a" / "python"
    outer.symlink_to(inner)
    return outer


def test_a_hop_root_that_contains_the_store_is_refused(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(sys, "executable", str(_link_chain(tmp_path)))
    with pytest.raises(SandboxError, match="trusted"):
        bwrap._interpreter_roots(protected=[store])


def test_the_filesystem_root_and_home_are_never_mounted(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    link = home / "bin" / "python"
    link.symlink_to(Path(sys.executable).resolve())
    # a hop whose root is HOME itself
    outer = tmp_path / "py"
    outer.symlink_to(link)
    monkeypatch.setattr(sys, "executable", str(outer))
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(SandboxError):
        bwrap._interpreter_roots(protected=[])
    monkeypatch.setattr(bwrap, "_hop_roots", lambda: [Path("/")])
    with pytest.raises(SandboxError):
        bwrap._interpreter_roots(protected=[])


def test_the_interpreter_roots_include_their_real_paths_and_never_the_filesystem_root():
    roots = bwrap._interpreter_roots(protected=[])
    # An alias directory may stay (execvp follows it); its real target must be mounted too.
    assert all(r.resolve() in roots for r in roots)
    assert Path("/") not in roots


@needs_bwrap
def test_the_runner_cannot_read_a_canary_in_the_store(tmp_path):
    store = _store(tmp_path)
    acc = tmp_path / "acc"
    acc.mkdir()
    code = (
        "import sys\n"
        f"try:\n print(open({str(store / 'hmac.key')!r}).read())\n"
        "except OSError:\n print('denied')\n"
    )
    out = BwrapSandbox(protected=[store]).author_run(
        acc, None, [sys.executable, "-I", "-c", code], env=()
    )
    assert CANARY not in out.stdout and "denied" in out.stdout


@needs_bwrap
def test_a_symlinked_interpreter_does_not_expose_the_store(tmp_path, monkeypatch):
    store = _store(tmp_path)
    outer = _link_chain(tmp_path)
    monkeypatch.setattr(sys, "executable", str(outer))
    acc = tmp_path / "acc"
    acc.mkdir()
    with pytest.raises(SandboxError):
        BwrapSandbox(protected=[store]).author_run(acc, None, ["true"], env=())


@needs_bwrap
def test_the_baseline_tree_is_immutable_and_acceptance_stays_read_only(tmp_path):
    """The baseline cannot be modified in place; calls work on fresh copies (see the driver)."""
    store = _store(tmp_path)
    acc = tmp_path / "acc"
    acc.mkdir()
    (acc / "t.py").write_text("original")
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "keep.txt").write_text("keep")
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "ref = Path(os.environ['CHEESELESS_AUTHOR_REFERENCE'])\n"
        "for target in (ref / 'generated.txt', ref / 'keep.txt'):\n"
        "    try:\n        target.write_text('x'); print('ref=LEAK')\n"
        "    except OSError:\n        print('ref=blocked')\n"
        "for target in (Path(os.getcwd()) / 't.py', Path(os.getcwd()) / 'new.py'):\n"
        "    try:\n        target.write_text('x'); print('acc=LEAK')\n"
        "    except OSError:\n        print('acc=blocked')\n"
        f"try:\n print(open({str(store / 'hmac.key')!r}).read())\n"
        "except OSError:\n print('canary=denied')\n"
    )
    out = BwrapSandbox(protected=[store]).author_run(
        acc, ref, [sys.executable, "-I", "-c", code], env=()
    )
    assert "ref=LEAK" not in out.stdout and out.stdout.count("ref=blocked") == 2, out.stderr_tail
    assert "acc=LEAK" not in out.stdout and out.stdout.count("acc=blocked") == 2
    assert "canary=denied" in out.stdout and CANARY not in out.stdout
    assert not (ref / "generated.txt").exists()
    assert (ref / "keep.txt").read_text() == "keep"
    assert (acc / "t.py").read_text() == "original" and not (acc / "new.py").exists()
