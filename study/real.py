"""Real-agent run paths (opt-in, operator-owned wrapper). Never exercised by ``--dry-run``.

The naive arm uses a minimal bubblewrap mount namespace.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess  # noqa: S404 - runs the operator-chosen agent command
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from cheeseless.bootstrap import host_session
from cheeseless.session_files import copy_evidence, write_projection_list
from study.agent_config import StudyAgent
from study.arms import (
    ARM_CHEESELESS,
    ARM_NAIVE,
    ArmResult,
    finish_cheeseless,
    prepare_cheeseless,
    run_naive,
)
from study.classifier import Classification, Event
from study.prompt import build_prompt
from study.scorer import INFRA, INVALID, TIMEOUT
from study.suite import copy_pytest_runtime
from study.tasks import CANARY, SENTINEL, Task, baseline_files
from study.transcripts import observe_writes

SANDBOX_WORK = "/work"
SEED_FILE_LIMIT = 1 << 20
_SCRUBBED_ENV = ("PATH", "LANG", "TERM")


class IsolationError(RuntimeError):
    """Isolation could not be established; stop real runs."""


def sandbox_argv(
    work: Path, home: Path, command: list[str], *, allow_network: bool = False
) -> list[str]:
    binary = shutil.which("bwrap")
    if binary is None:
        raise IsolationError("bubblewrap unavailable")
    args = [binary, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
    if allow_network:
        args += ["--share-net"]
    for top in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(top).is_symlink():
            args += ["--symlink", os.readlink(top), top]
        elif Path(top).is_dir():
            args += ["--ro-bind", top, top]
    if allow_network:
        for config in ("/etc/resolv.conf", "/etc/hosts", "/etc/ssl/certs"):
            if Path(config).exists():
                args += ["--ro-bind", config, config]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # noqa: S108
        "--bind",
        str(work.resolve()),
        "/work",
        "--bind",
        str(home.resolve()),
        "/agent-home",
        "--chdir",
        "/work",
        "--setenv",
        "HOME",
        "/agent-home",
        "--remount-ro",
        "/",
        "--",
        *command,
    ]
    return args


def isolation_check(
    work: Path, home: Path, protected: tuple[Path, ...], *, allow_network: bool = False
) -> None:
    """Probe the namespace before launching any operator command."""
    script = r"""for p do
        test ! -r "$p" && test ! -w "$p" && test ! -e "$p" || exit 31
    done
    if (echo probe > /outside-work) 2>/dev/null; then exit 32; fi
    touch /work/.isolation-probe || exit 33
    rm /work/.isolation-probe
    """
    argv = sandbox_argv(
        work,
        home,
        ["/bin/sh", "-c", script, "probe", *map(str, protected)],
        allow_network=allow_network,
    )
    try:
        result = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsolationError("bubblewrap isolation self-check could not execute") from exc
    if result.returncode:
        raise IsolationError("bubblewrap isolation self-check failed")


class AgentTimedOut(RuntimeError):
    """The agent exceeded the wall-clock bound."""


def _status(arm: str, status: str, reason: str, started: float) -> ArmResult:
    wall = round(time.monotonic() - started, 3)
    return ArmResult(
        arm, status, reason, None, None, None, Classification({}), None, None, False, False, wall
    )


def infra_result(arm: str, reason: str) -> ArmResult:
    """An ``infra-error`` result for an unexpected failure (reason is a class name, no paths)."""
    return _status(arm, INFRA, reason, time.monotonic())


def _copy_seed(src: Path, dest: Path) -> None:
    """Copy a seed without following symlinks; oversized files are skipped."""
    if src.is_symlink():
        return
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            _copy_seed(child, dest / child.name)
    elif src.is_file() and src.stat().st_size <= SEED_FILE_LIMIT:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(0o700 if os.access(src, os.X_OK) else 0o600)


def _scrubbed_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _SCRUBBED_ENV if k in os.environ}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = "/agent-home/.study-pytest"
    env["STUDY_IN_TREE"] = "1"
    return env


def _run_process(argv: list[str], cwd: Path, env: Mapping[str, str], timeout: float) -> int:
    proc = subprocess.Popen(  # noqa: S603 - operator-supplied argv, scrubbed env
        argv,
        cwd=cwd,
        env=dict(env),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(),
    )
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise AgentTimedOut(f"agent exceeded {timeout:g} s") from exc


def run_naive_real(
    task: Task,
    root: Path,
    agent: StudyAgent,
    *,
    protected: tuple[Path, ...] = (),
    allow_network: bool = False,
) -> ArmResult:
    """Run the operator command on the naive tree, then grade with the shared graders."""
    started = time.monotonic()
    prompt = build_prompt(task, ARM_NAIVE)
    exits: list[int] = []
    writes: list[tuple[Event, ...] | None] = []

    def apply(workdir: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="study-home-") as tmp:
            home = Path(tmp)
            copy_pytest_runtime(home / ".study-pytest")
            for rel, src in agent.seeds.items():
                _copy_seed(src, home / rel)
            isolation_check(workdir, home, protected, allow_network=allow_network)
            code = _run_process(
                sandbox_argv(
                    workdir, home, [*agent.naive_command, prompt], allow_network=allow_network
                ),
                workdir,
                _scrubbed_env(home),
                agent.timeout,
            )
            # parse before the temporary home (and the transcript in it) is discarded
            writes.append(observe_writes(agent.agent.vendor, home, (SANDBOX_WORK,)))
        exits.append(code)

    try:
        result = run_naive(task, root, apply, lambda: writes[0] if writes else None)
    except AgentTimedOut:
        return _status(ARM_NAIVE, TIMEOUT, "agent_timeout", started)
    if exits and exits[0] != 0:
        return _status(ARM_NAIVE, INFRA, "agent_nonzero_exit", started)
    return result


def evidence_has_canary(evidence: Path) -> bool:
    """True if any captured regular file contains a hidden marker (symlinks never followed)."""
    needles = (CANARY.encode(), SENTINEL.encode())
    for dirpath, _dirs, files in os.walk(evidence, followlinks=False):
        for name in files:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > SEED_FILE_LIMIT:
                continue
            data = path.read_bytes()
            if any(n in data for n in needles):
                return True
    return False


def run_cheeseless_real(task: Task, root: Path, agent: StudyAgent, evidence: Path) -> ArmResult:
    """Real cheeseless run through ``host_session``; the harness makes no gate_run itself."""
    started = time.monotonic()
    case = prepare_cheeseless(task, root)
    prompt = build_prompt(task, ARM_CHEESELESS)
    before = {
        k: v for k, v in baseline_files(task).items() if not k.startswith("acceptance/hidden")
    }
    sockets = root / "sockets"
    sockets.mkdir(mode=0o700, parents=True, exist_ok=True)
    sockets.chmod(0o700)
    home: Path | None = None
    write_events: tuple[Event, ...] | None = None
    try:
        with host_session(
            state_dir=case.state,
            cwd=case.repo.root,
            task_id=case.task_id,
            projection_dir=case.projection,
            socket_dir=sockets,
            agent_command=[*agent.command, prompt],
            agent_stdio=False,
            timeout=agent.timeout,
            home_seed=dict(agent.seeds),
            extra_read_only=agent.read_only,
            evidence_dir=evidence,
            egress_policy=agent.egress_policy,
        ) as info:
            home = Path(info["home_dir"])
            try:
                code = info["agent_proc"].wait(timeout=agent.timeout)
            except subprocess.TimeoutExpired:
                return _status(ARM_CHEESELESS, TIMEOUT, "agent_timeout", started)
            copy_evidence(home, evidence, protected=info["protected_roots"])
            write_events = observe_writes(
                agent.agent.vendor,
                home,
                (str(info["projection_dir"]), str(case.projection)),
            )
            write_projection_list(Path(info["projection_dir"]), evidence)
            blocked = info["blocked"].is_set()
    except (ValueError, OSError, RuntimeError) as exc:
        return _status(ARM_CHEESELESS, INFRA, f"session_error:{type(exc).__name__}", started)
    if blocked:
        return _status(ARM_CHEESELESS, INVALID, "session_blocked", started)
    if code != 0:
        return _status(ARM_CHEESELESS, INFRA, "agent_nonzero_exit", started)
    if not case.projection.is_dir():
        return _status(ARM_CHEESELESS, INFRA, "projection_missing", started)
    return finish_cheeseless(
        task, case, before, None, gate_runs=None, started=started, write_events=write_events
    )
