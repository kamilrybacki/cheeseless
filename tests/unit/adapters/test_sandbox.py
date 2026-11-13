"""Sandbox adapter: the seccomp program and the bwrap profiles (ADR 0003, ledger I2)."""

import json
import struct
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import profiles, seccomp
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.ports.sandbox import SandboxError

ARCH_X86_64 = 0xC000003E


def _run_bpf(program: bytes, arch: int, nr: int) -> int:
    """A minimal classic-BPF interpreter for the instructions the program uses."""
    insns = [struct.unpack("<HBBI", program[i : i + 8]) for i in range(0, len(program), 8)]
    data = {0: nr, 4: arch}
    acc = 0
    pc = 0
    while True:
        code, jt, jf, k = insns[pc]
        if code == 0x20:
            acc = data[k]
            pc += 1
        elif code == 0x15:
            pc += 1 + (jt if acc == k else jf)
        elif code == 0x35:
            pc += 1 + (jt if acc >= k else jf)
        elif code == 0x06:
            return k
        else:
            raise AssertionError(f"unexpected opcode {code:#x}")


def test_seccomp_denies_ptrace_and_process_vm() -> None:
    program = seccomp.program("x86_64")
    assert len(program) % 8 == 0
    for nr in (101, 310, 311, 438):
        assert _run_bpf(program, ARCH_X86_64, nr) == seccomp.RET_EPERM
    for nr in (0, 1, 59, 57, 231):
        assert _run_bpf(program, ARCH_X86_64, nr) == seccomp.RET_ALLOW


def test_seccomp_kills_foreign_arch_and_x32() -> None:
    program = seccomp.program("x86_64")
    assert _run_bpf(program, 0x40000003, 101) == seccomp.RET_KILL
    assert _run_bpf(program, ARCH_X86_64, 0x40000000 + 101) == seccomp.RET_KILL


def test_seccomp_refuses_other_machines() -> None:
    with pytest.raises(SandboxError):
        seccomp.program("aarch64")


def _pairs(argv: list[str], flag: str, arity: int) -> list[tuple[str, ...]]:
    return [tuple(argv[i + 1 : i + 1 + arity]) for i, a in enumerate(argv) if a == flag]


def test_candidate_profile_isolates_everything(tmp_path: Path) -> None:
    argv = profiles.candidate(
        bwrap="/usr/bin/bwrap",
        workdir=tmp_path / "w",
        upper=tmp_path / "s" / "up",
        work=tmp_path / "s" / "wk",
        inside="/w/abcd",
        env=(("PYTHONPATH", "src"),),
        seccomp_fd=7,
        command=("python3", "-m", "toy"),
    )
    assert argv[0] == "/usr/bin/bwrap"
    for flag in (
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--unshare-uts",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ):
        assert flag in argv
    assert _pairs(argv, "--cap-drop", 1) == [("ALL",)]
    assert _pairs(argv, "--seccomp", 1) == [("7",)]
    assert _pairs(argv, "--overlay-src", 1) == [(str(tmp_path / "w"),)]
    assert _pairs(argv, "--overlay", 3) == [
        (str(tmp_path / "s" / "up"), str(tmp_path / "s" / "wk"), "/w/abcd")
    ]
    assert _pairs(argv, "--chdir", 1) == [("/w/abcd",)]
    assert ("PYTHONPATH", "src") in _pairs(argv, "--setenv", 2)
    assert {k for k, _ in _pairs(argv, "--setenv", 2)} == {"PYTHONPATH", *profiles.BASE_ENV}
    # Nothing writable from the host and no host path except the read-only system.
    assert "--bind" not in argv
    ro = _pairs(argv, "--ro-bind", 2) + _pairs(argv, "--ro-bind-try", 2)
    assert {src for src, _ in ro} <= {*profiles.SYSTEM_DIRS, *profiles.ETC_ALLOW}
    assert argv[argv.index("--") + 1 :] == ["python3", "-m", "toy"]


def test_build_profile_binds_only_the_workdir_writable(tmp_path: Path) -> None:
    argv = profiles.build(
        bwrap="/usr/bin/bwrap",
        workdir=tmp_path / "w",
        inside="/w/abcd",
        env=(),
        seccomp_fd=5,
        command=("make",),
    )
    assert _pairs(argv, "--bind", 2) == [(str(tmp_path / "w"), "/w/abcd")]
    assert "--unshare-net" in argv
    assert _pairs(argv, "--seccomp", 1) == [("5",)]
    assert argv[-2:] == ["--", "make"]


def test_agent_profile_binds_projection_and_sockets(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    sock = tmp_path / "run" / "verifier.sock"
    egress = tmp_path / "run" / "egress.sock"
    argv = profiles.agent(
        bwrap="/usr/bin/bwrap",
        projection=proj,
        sockets=(sock, egress),
        home=tmp_path / "home",
        read_only=(Path("/opt/agent"),),
        env=(("CHEESELESS_SOCKET", str(sock)),),
        command=("agent",),
    )
    binds = _pairs(argv, "--bind", 2)
    assert (str(proj), str(proj)) in binds
    assert (str(sock), str(sock)) in binds
    assert (str(egress), str(egress)) in binds
    assert (str(tmp_path / "home"), str(tmp_path / "home")) in binds
    assert ("/opt/agent", "/opt/agent") in _pairs(argv, "--ro-bind", 2)
    assert "--unshare-net" in argv
    assert ("HOME", str(tmp_path / "home")) in _pairs(argv, "--setenv", 2)


def test_profiles_refuse_reserved_env() -> None:
    with pytest.raises(SandboxError):
        profiles.build(
            bwrap="b",
            workdir=Path("/x"),
            inside="/w/ab",
            env=(("PATH", "/evil"),),
            seccomp_fd=3,
            command=("x",),
        )


def test_profiles_refuse_odd_inside_paths() -> None:
    with pytest.raises(SandboxError):
        profiles.build(
            bwrap="b", workdir=Path("/x"), inside="/proc", env=(), seccomp_fd=3, command=("x",)
        )


def test_inside_paths_are_random() -> None:
    first, second = profiles.inside_path(), profiles.inside_path()
    assert first != second
    assert first.startswith("/w/")


def test_launcher_prefix_runs_the_trusted_helper(tmp_path: Path) -> None:
    sandbox = BwrapSandbox(bwrap=sys.executable)
    prefix = sandbox.launcher(tmp_path / "w", (("A", "1"),), tmp_path / "s")
    assert prefix[:4] == (sys.executable, "-I", "-m", "cheeseless.adapters.outbound.sandbox.launch")
    assert prefix[-1] == "--"
    spec = json.loads(prefix[4])
    assert spec == {
        "bwrap": sys.executable,
        "workdir": str((tmp_path / "w").resolve()),
        "scratch": str((tmp_path / "s").resolve()),
        "env": [["A", "1"]],
    }


def test_missing_bwrap_fails_closed(tmp_path: Path) -> None:
    sandbox = BwrapSandbox(bwrap=str(tmp_path / "no-bwrap"))
    with pytest.raises(SandboxError):
        sandbox.launcher(tmp_path, (), tmp_path)
    with pytest.raises(SandboxError):
        sandbox.build(tmp_path, [("true",)], ())


def test_scratch_is_private_and_removed_even_when_locked() -> None:
    with BwrapSandbox().scratch() as scratch:
        assert scratch.is_dir()
        assert scratch.stat().st_mode & 0o777 == 0o700
        locked = scratch / "work" / "inner"
        locked.mkdir(parents=True)
        (locked / "f").write_text("x")
        (scratch / "work").chmod(0)
    assert not scratch.exists()
