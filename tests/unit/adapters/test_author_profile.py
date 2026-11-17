"""The author-runner sandbox profile (ADR 0009): unapproved tests run contained."""

import shutil
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.ports.sandbox import SandboxError

INSIDE_A = "/w/aaaa1111"
INSIDE_R = "/w/bbbb2222"


def _argv(tmp_path: Path, **over: object) -> list[str]:
    args: dict[str, object] = {
        "bwrap": "/usr/bin/bwrap",
        "acceptance": tmp_path / "acc",
        "inside": INSIDE_A,
        "broker_dir": tmp_path / "brk",
        "broker_inside": INSIDE_R,
        "extra_read_only": (),
        "env": (),
        "seccomp_fd": 9,
        "command": ["true"],
    }
    args.update(over)
    return profiles.author(**args)  # type: ignore[arg-type]


def test_author_profile_is_fully_unshared_with_a_read_only_acceptance_tree(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-net"):
        assert flag in argv
    assert "--overlay" not in argv
    ro = [argv[i + 1 : i + 3] for i, a in enumerate(argv) if a == "--ro-bind"]
    assert [str(tmp_path / "acc"), INSIDE_A] in ro
    # The broker socket directory is read-only too; nothing is writable and no tree is mounted.
    assert [str(tmp_path / "brk"), INSIDE_R] in ro
    assert "--bind" not in argv
    assert "--overlay-src" not in argv and "--tmp-overlay" not in argv
    assert argv[argv.index("--chdir") + 1] == INSIDE_A


def test_author_profile_without_a_broker_binds_nothing_writable(tmp_path: Path) -> None:
    argv = _argv(tmp_path, broker_dir=None)
    assert str(tmp_path / "brk") not in argv and "--bind" not in argv


def test_author_profile_rejects_bad_mount_points(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        _argv(tmp_path, inside="/etc")
    with pytest.raises(SandboxError):
        _argv(tmp_path, broker_inside="/srv/x")


def test_author_profile_rejects_reserved_extra_mounts(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        _argv(tmp_path, extra_read_only=(Path("/proc"),))


@pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)
def test_author_run_is_contained_and_cannot_write(tmp_path: Path) -> None:
    acc = tmp_path / "acc"
    acc.mkdir()
    (acc / "f.txt").write_text("x")
    outcome = BwrapSandbox().author_run(
        acc,
        None,
        [
            sys.executable,
            "-I",
            "-c",
            "import os,sys\n"
            "sys.stdout.write(os.listdir(os.getcwd())[0])\n"
            "try:\n open('new','w')\nexcept OSError:\n sys.stdout.write(' ro')\n",
        ],
        env=(),
    )
    assert outcome.exit_code == 0
    assert outcome.stdout.endswith("ro")


@pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)
def test_author_run_exposes_a_broker_socket_only_with_a_reference(tmp_path: Path) -> None:
    from cheeseless.ports.sandbox import AUTHOR_BROKER_ENV

    acc, ref = tmp_path / "acc", tmp_path / "ref"
    for d in (acc, ref):
        d.mkdir()
    code = (
        f"import os,stat\np=os.environ.get({AUTHOR_BROKER_ENV!r})\n"
        "print(bool(p) and stat.S_ISSOCK(os.stat(p).st_mode))\n"
        "print(os.path.exists('/w') and any('ref' in n for n in os.listdir('/w')))\n"
    )
    cmd = [sys.executable, "-I", "-c", code]
    with_ref = BwrapSandbox().author_run(acc, ref, cmd, env=())
    assert with_ref.stdout.split() == ["True", "False"]
    none = BwrapSandbox().author_run(acc, None, cmd, env=())
    assert none.stdout.split() == ["False", "False"]
