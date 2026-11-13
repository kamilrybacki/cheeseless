"""``Sandbox`` port over bubblewrap (ADR 0003).

Build steps run directly under the ``build`` profile. Candidate commands run through a
trusted launch helper (``launch.py``) so that every call gets its own throwaway overlay:
the runner prepends :meth:`BwrapSandbox.launcher` to the contract's ``run`` argv.
"""

import json
import os
import secrets
import stat
import subprocess  # noqa: S404 - the sandbox is a process boundary by design
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from cheeseless.adapters.outbound.sandbox import profiles, seccomp
from cheeseless.adapters.outbound.sandbox.launch import force_remove
from cheeseless.ports.sandbox import SandboxError, StepOutcome

DEFAULT_BWRAP = "/usr/bin/bwrap"
BUILD_TIMEOUT = 600.0
STDERR_TAIL = 2000
LAUNCH_MODULE = "cheeseless.adapters.outbound.sandbox.launch"

TMPFS_MAGIC = 0x01021994


def _tmpfs_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in (Path("/dev/shm"), Path(f"/run/user/{os.geteuid()}"), Path("/tmp")):  # noqa: S108
        try:
            if candidate.is_dir():
                st = candidate.stat()
                if st.st_uid == os.geteuid() and not stat.S_IMODE(st.st_mode) & 0o022:
                    roots.append(candidate)
        except OSError:
            continue
    return roots


def _is_tmpfs(path: Path) -> bool:
    try:
        import ctypes

        buf = (ctypes.c_long * 32)()
        if ctypes.CDLL("libc.so.6").statfs(os.fsencode(path), buf) != 0:
            return False
        return buf[0] == TMPFS_MAGIC
    except (OSError, AttributeError):
        return False


def _trusted_tmpfs_scratch() -> Path:
    for root in _tmpfs_roots():
        if not _is_tmpfs(root):
            continue
        path = root / f"cheeseless-scratch-{secrets.token_hex(8)}"
        try:
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise SandboxError(f"cannot create trusted scratch: {exc}") from exc
        try:
            st = path.stat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                raise SandboxError("trusted scratch is not private")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise SandboxError("trusted scratch is not private")
            if not _is_tmpfs(path):
                raise SandboxError("trusted scratch is not backed by tmpfs")
        except Exception:
            force_remove(path)
            raise
        return path
    raise SandboxError("no verified tmpfs root available for trusted scratch")


class BwrapSandbox:
    def __init__(self, bwrap: str = DEFAULT_BWRAP, build_timeout: float = BUILD_TIMEOUT) -> None:
        self._bwrap = bwrap
        self._build_timeout = build_timeout

    def _check(self) -> str:
        if not os.path.isabs(self._bwrap) or not os.access(self._bwrap, os.X_OK):
            raise SandboxError(f"bubblewrap is not available at {self._bwrap}")
        return self._bwrap

    def binary(self) -> str:
        """Trusted bubblewrap executable path."""
        return self._check()

    @contextmanager
    def scratch(self) -> Iterator[Path]:
        path = _trusted_tmpfs_scratch()
        try:
            yield path
        finally:
            force_remove(path)

    @contextmanager
    def trusted_scratch(self) -> Iterator[Path]:
        path = _trusted_tmpfs_scratch()
        try:
            yield path
        finally:
            force_remove(path)

    def launcher(
        self, workdir: Path, env: Sequence[tuple[str, str]], scratch: Path
    ) -> tuple[str, ...]:
        bwrap = self._check()
        profiles.check_env(env)
        spec = {
            "bwrap": bwrap,
            "workdir": str(workdir.resolve()),
            "scratch": str(scratch.resolve()),
            "env": [[k, v] for k, v in env],
        }
        return (sys.executable, "-I", "-m", LAUNCH_MODULE, json.dumps(spec), "--")

    def build(
        self, workdir: Path, steps: Sequence[Sequence[str]], env: Sequence[tuple[str, str]]
    ) -> StepOutcome:
        bwrap = self._check()
        profiles.check_env(env)
        for step in steps:
            outcome = self._step(bwrap, workdir.resolve(), step, env)
            if not outcome.ok:
                return outcome
        return StepOutcome(True, 0)

    def _step(
        self, bwrap: str, workdir: Path, step: Sequence[str], env: Sequence[tuple[str, str]]
    ) -> StepOutcome:
        fd = seccomp.program_fd()
        try:
            argv = profiles.build(
                bwrap=bwrap,
                workdir=workdir,
                inside=profiles.inside_path(),
                env=env,
                seccomp_fd=fd,
                command=step,
            )
            done = subprocess.run(  # noqa: S603 - argv built from validated parts
                argv,
                pass_fds=(fd,),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self._build_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return StepOutcome(False, -1, _tail(exc.stderr) + "\n[build step timed out]")
        except OSError as exc:
            raise SandboxError(f"cannot start bubblewrap: {exc}") from exc
        finally:
            os.close(fd)
        if done.returncode != 0:
            return StepOutcome(False, done.returncode, _tail(done.stderr))
        return StepOutcome(True, 0)


def _tail(data: bytes | str | None) -> str:
    if not data:
        return ""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return text[-STDERR_TAIL:]
