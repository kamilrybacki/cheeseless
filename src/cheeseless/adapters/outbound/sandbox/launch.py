"""Trusted launch helper: run one candidate command on a throwaway overlay.

Invoked as ``python -I -m cheeseless.adapters.outbound.sandbox.launch SPEC -- CMD...``
where SPEC is the JSON written by :meth:`BwrapSandbox.launcher`. Each call creates a
fresh overlay upper/work pair under the scratch directory, starts bwrap with the
``candidate`` profile and the seccomp filter, forwards stdio, and removes the overlay
afterwards. The exit status is the candidate's (128+N when it died by signal N).
"""

import json
import os
import resource
import shutil
import signal
import stat
import subprocess  # noqa: S404 - the sandbox is a process boundary by design
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from cheeseless.adapters.outbound.sandbox import profiles, seccomp
from cheeseless.ports.sandbox import SandboxError

LAUNCH_FAILED = 125
RLIMITS = (
    (resource.RLIMIT_CORE, 0),
    (resource.RLIMIT_NOFILE, 1024),
    (resource.RLIMIT_FSIZE, 256 * 1024 * 1024),
    (resource.RLIMIT_CPU, 300),
)


@dataclass(frozen=True, slots=True)
class Spec:
    bwrap: str
    workdir: Path
    scratch: Path
    env: tuple[tuple[str, str], ...]
    command: tuple[str, ...]


def parse(argv: Sequence[str]) -> Spec:
    if len(argv) < 3 or argv[1] != "--":
        raise SandboxError("usage: launch SPEC -- COMMAND...")
    try:
        raw: Any = json.loads(argv[0])
        spec = Spec(
            bwrap=_str(raw["bwrap"]),
            workdir=Path(_str(raw["workdir"])),
            scratch=Path(_str(raw["scratch"])),
            env=tuple((_str(k), _str(v)) for k, v in raw["env"]),
            command=tuple(argv[2:]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise SandboxError(f"invalid launch spec: {exc}") from exc
    for path in (spec.workdir, spec.scratch):
        if not path.is_absolute() or not path.is_dir():
            raise SandboxError(f"not an existing absolute directory: {path}")
    profiles.check_env(spec.env)
    return spec


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _limit_resources() -> None:
    for which, value in RLIMITS:
        _soft, hard = resource.getrlimit(which)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(which, (cap, cap))


def force_remove(path: Path) -> None:
    """Remove a tree whose overlay work dir is left with mode 000."""

    def retry(func: Callable[..., Any], name: str, _exc: BaseException) -> None:
        parent = os.path.dirname(name)
        os.chmod(parent, stat.S_IRWXU)
        if os.path.isdir(name) and not os.path.islink(name):
            os.chmod(name, stat.S_IRWXU)
            shutil.rmtree(name, onexc=retry)
        else:
            func(name)

    if path.exists():
        shutil.rmtree(path, onexc=retry)


def run(spec: Spec) -> int:
    overlay = Path(tempfile.mkdtemp(prefix="ov-", dir=spec.scratch))
    fd = -1
    try:
        (overlay / "up").mkdir()
        (overlay / "wk").mkdir()
        fd = seccomp.program_fd()
        argv = profiles.candidate(
            bwrap=spec.bwrap,
            workdir=spec.workdir,
            upper=overlay / "up",
            work=overlay / "wk",
            inside=profiles.inside_path(),
            env=spec.env,
            seccomp_fd=fd,
            command=spec.command,
        )
        child = subprocess.Popen(argv, pass_fds=(fd,))  # noqa: S603 - validated argv
        os.close(fd)
        fd = -1

        def forward(signum: int, _frame: FrameType | None) -> None:
            child.send_signal(signum)

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, forward)
        code = child.wait()
        return 128 - code if code < 0 else code
    finally:
        if fd >= 0:
            os.close(fd)
        force_remove(overlay)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        spec = parse(sys.argv[1:] if argv is None else argv)
        _limit_resources()
        return run(spec)
    except (SandboxError, OSError) as exc:
        sys.stderr.write(f"cheeseless sandbox: {exc}\n")
        return LAUNCH_FAILED


if __name__ == "__main__":
    sys.exit(main())
