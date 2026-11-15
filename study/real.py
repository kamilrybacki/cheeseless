"""Real-agent run paths (opt-in, operator-owned wrapper). Never exercised by ``--dry-run``.

The naive arm runs the operator command as a plain subprocess: it has a throwaway HOME and a
scrubbed environment but NO filesystem, read-only-mount or egress isolation. Use a disposable
container, VM or user for it. The cheeseless arm goes through ``host_session`` exactly like the
real-agent E2E. The evidence directory holds agent-side captures: treat it as sensitive.
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
from study.classifier import Classification
from study.prompt import build_prompt
from study.scorer import INFRA, INVALID, TIMEOUT
from study.tasks import CANARY, SENTINEL, Task, baseline_files

SEED_FILE_LIMIT = 1 << 20
_SCRUBBED_ENV = ("PATH", "LANG", "TERM")


class AgentTimedOut(RuntimeError):
    """The agent exceeded the wall-clock bound."""


def _status(arm: str, status: str, reason: str, started: float) -> ArmResult:
    wall = round(time.monotonic() - started, 3)
    return ArmResult(
        arm, status, reason, None, None, None, Classification({}), None, None, False, wall
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


def _scrubbed_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _SCRUBBED_ENV if k in os.environ}
    env["HOME"] = str(home)
    return env


def _run_process(argv: list[str], cwd: Path, env: Mapping[str, str], timeout: float) -> int:
    proc = subprocess.Popen(  # noqa: S603 - operator-supplied argv, scrubbed env
        argv, cwd=cwd, env=dict(env), start_new_session=True, stdin=subprocess.DEVNULL
    )
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise AgentTimedOut(f"agent exceeded {timeout:g} s") from exc


def run_naive_real(task: Task, root: Path, agent: StudyAgent) -> ArmResult:
    """Run the operator command on the naive tree, then grade with the shared graders."""
    started = time.monotonic()
    prompt = build_prompt(task, ARM_NAIVE)
    exits: list[int] = []

    def apply(workdir: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="study-home-") as tmp:
            home = Path(tmp)
            for rel, src in agent.seeds.items():
                _copy_seed(src, home / rel)
            code = _run_process(
                [*agent.naive_command, prompt], workdir, _scrubbed_env(home), agent.timeout
            )
        exits.append(code)

    try:
        result = run_naive(task, root, apply)
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
    return finish_cheeseless(task, case, before, None, gate_runs=None, started=started)
