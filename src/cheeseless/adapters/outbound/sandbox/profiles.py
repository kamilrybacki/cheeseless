"""bwrap argv for the three sandbox profiles (ADR 0003).

Every profile gets fresh user, PID, IPC, UTS and network namespaces, a new /proc, no
capabilities, a new session, a scrubbed environment and the seccomp filter. Only the
read-only system directories and the paths a profile names are visible.

* ``candidate``: the workdir is the lower layer of a disk-backed overlay mounted at a
  random path; writes land in a per-launch upper directory and are thrown away.
* ``build``: the workdir is bound writable at a random path; no network.
* ``author``: the unapproved acceptance tree and the reference snapshot, both read-only;
  nothing is writable and there is no trusted store (ADR 0009).
* ``agent``: the projection, the verifier and egress sockets and an ephemeral HOME.
"""

import os
import re
import secrets
import stat
from collections.abc import Sequence
from pathlib import Path

from cheeseless.domain.contract import check_env_entry
from cheeseless.ports.sandbox import SandboxError

SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
ETC_ALLOW = ("/etc/ld.so.cache", "/etc/alternatives", "/etc/localtime")
AGENT_ETC_ALLOW = ("/etc/ssl", "/etc/ca-certificates", "/etc/resolv.conf", "/etc/hosts")
# The tmpfs mounted inside the sandbox; it never refers to the host /tmp.
SANDBOX_TMP = "/tmp"  # noqa: S108
# Host pseudo-filesystems the sandbox replaces with its own; mounting the host's
# versions over them would leak the host kernel view.
RESERVED_MOUNTS = ("/proc", "/dev", "/sys")
BASE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": SANDBOX_TMP,
    "TMPDIR": SANDBOX_TMP,
    "LANG": "C.UTF-8",
}
_INSIDE = re.compile(r"/w/[0-9a-f]{4,64}")
_NAMESPACES = (
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-net",
    "--unshare-uts",
    "--unshare-cgroup-try",
    "--disable-userns",
    "--die-with-parent",
    "--new-session",
    "--cap-drop",
    "ALL",
    "--clearenv",
    "--hostname",
    "sandbox",
)


def inside_path() -> str:
    """A fresh random mount point, so candidates cannot hard-code the workdir path."""
    return f"/w/{secrets.token_hex(8)}"


def _inside(path: str) -> str:
    if _INSIDE.fullmatch(path) is None:
        raise SandboxError(f"invalid sandbox mount point: {path!r}")
    return path


def _system() -> list[str]:
    """Read-only system dirs; merged-/usr symlinks are recreated as symlinks."""
    args: list[str] = []
    for top in SYSTEM_DIRS:
        if top != "/usr" and os.path.islink(top):
            args += ["--symlink", os.readlink(top), top]
        elif top == "/usr" or os.path.isdir(top):
            args += ["--ro-bind", top, top]
    return args


def _try_binds(paths: Sequence[str]) -> list[str]:
    args: list[str] = []
    for path in paths:
        args += ["--ro-bind-try", path, path]
    return args


def check_env(env: Sequence[tuple[str, str]]) -> None:
    """Raise SandboxError unless every entry may be set for a candidate."""
    for key, value in env:
        try:
            check_env_entry(key, value)
        except ValueError as exc:
            raise SandboxError(str(exc)) from exc


def _candidate_env(env: Sequence[tuple[str, str]]) -> list[str]:
    check_env(env)
    args: list[str] = []
    for key, value in (*BASE_ENV.items(), *env):
        args += ["--setenv", key, value]
    return args


def _command(command: Sequence[str]) -> list[str]:
    if not command:
        raise SandboxError("no command to run in the sandbox")
    return ["--", *command]


def _common(bwrap: str, seccomp_fd: int) -> list[str]:
    return [
        bwrap,
        *_NAMESPACES,
        *_system(),
        *_try_binds(ETC_ALLOW),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        SANDBOX_TMP,
        "--seccomp",
        str(seccomp_fd),
    ]


def candidate(
    *,
    bwrap: str,
    workdir: Path,
    upper: Path,
    work: Path,
    inside: str,
    env: Sequence[tuple[str, str]],
    seccomp_fd: int,
    command: Sequence[str],
) -> list[str]:
    mount = _inside(inside)
    return [
        *_common(bwrap, seccomp_fd),
        "--overlay-src",
        str(workdir),
        "--overlay",
        str(upper),
        str(work),
        mount,
        "--chdir",
        mount,
        *_candidate_env(env),
        *_command(command),
    ]


def build(
    *,
    bwrap: str,
    workdir: Path,
    inside: str,
    env: Sequence[tuple[str, str]],
    seccomp_fd: int,
    command: Sequence[str],
) -> list[str]:
    mount = _inside(inside)
    return [
        *_common(bwrap, seccomp_fd),
        "--bind",
        str(workdir),
        mount,
        "--chdir",
        mount,
        *_candidate_env(env),
        *_command(command),
    ]


def author(
    *,
    bwrap: str,
    acceptance: Path,
    inside: str,
    reference: Path | None,
    reference_inside: str,
    extra_read_only: Sequence[Path],
    env: Sequence[tuple[str, str]],
    seccomp_fd: int,
    command: Sequence[str],
    trusted_env: Sequence[tuple[str, str]] = (),
) -> list[str]:
    """The author runner: unapproved tests, read-only trees, no store, no network."""
    mount = _inside(inside)
    args = _common(bwrap, seccomp_fd)
    args += ["--ro-bind", str(acceptance), mount]
    if reference is not None:
        args += ["--ro-bind", str(reference), _inside(reference_inside)]
    for path in extra_read_only:
        if str(path) in RESERVED_MOUNTS or not path.is_absolute():
            raise SandboxError(f"cannot mount {path} into the author runner")
        args += ["--ro-bind-try", str(path), str(path)]
    # ``trusted_env`` is set by the runner itself, so it skips the reserved-name check.
    trusted = [a for key, value in trusted_env for a in ("--setenv", key, value)]
    return [*args, "--chdir", mount, *_candidate_env(env), *trusted, *_command(command)]


def open_acceptance(projection: Path) -> int:
    """An O_PATH fd on the projected ``acceptance/`` directory, or SandboxError (fail closed).

    The caller owns the fd and closes it. Mounting through the fd, not the path, closes the
    window in which a path could be swapped for a symlink between the check and the mount.
    """
    acceptance = projection / "acceptance"
    message = f"the projection has no acceptance/ directory to mount read-only: {acceptance}"
    try:
        fd = os.open(acceptance, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:  # missing, a symlink (ELOOP) or not a directory (ENOTDIR)
        raise SandboxError(message) from exc
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise SandboxError(message)
    except BaseException:
        os.close(fd)
        raise
    return fd


def agent(
    *,
    bwrap: str,
    projection: Path,
    sockets: Sequence[Path],
    home: Path,
    read_only: Sequence[Path],
    env: Sequence[tuple[str, str]],
    command: Sequence[str],
    acceptance_fd: int,
    pinned_read_only: Sequence[tuple[int, str]] = (),
    seccomp_fd: int | None = None,
    info_fd: int | None = None,
) -> list[str]:
    """The coding agent: it sees the projection and talks only through the sockets."""
    acceptance = projection / "acceptance"
    args = [bwrap, *_NAMESPACES, *_system(), *_try_binds((*ETC_ALLOW, *AGENT_ETC_ALLOW))]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", SANDBOX_TMP]
    for path in (projection, home, *sockets):
        args += ["--bind", str(path), str(path)]
    # Applied after the writable projection bind, so it lands on top of it. Bound by the fd
    # opened with O_NOFOLLOW (see open_acceptance), so a swapped path cannot redirect it.
    args += ["--ro-bind-fd", str(acceptance_fd), str(acceptance)]
    for path in read_only:
        args += ["--ro-bind", str(path), str(path)]
    for fd, destination in pinned_read_only:
        args += ["--ro-bind-fd", str(fd), destination]
    if info_fd is not None:
        args += ["--info-fd", str(info_fd)]
    if seccomp_fd is not None:
        args += ["--seccomp", str(seccomp_fd)]
    args += ["--chdir", str(projection)]
    agent_env = {**BASE_ENV, **dict(env), "TMPDIR": SANDBOX_TMP, "HOME": str(home)}
    for key, value in agent_env.items():
        args += ["--setenv", key, value]
    return [*args, *_command(command)]
