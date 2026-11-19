"""Composition root for one dev-mode reference attempt (ADR 0014).

The host owns the sandbox: it starts bwrap on a pty, keeps the master, relays the bytes
to one tmux pane, and owns the attempt record. A TUI attempt ends only on the agent's
``reference_done`` (or a timeout / a dead sandbox); a headless attempt ends on exit 0.
Only the winner of ``running -> stopping`` snapshots the tree.

Exit codes: 0 the attempt is ``done``, 1 it failed or was cancelled, 2 usage or an
infrastructure refusal before or during launch.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cheeseless.adapters.outbound.pane.tmux import PaneError, TmuxPanes
from cheeseless.app.authoring import record_reference_snapshot, spec_of
from cheeseless.app.freeze import read_contract
from cheeseless.app.reference_run import (
    choose_mode,
    complete_attempt,
    fail_attempt,
    reap_attempts,
    stop_attempt,
)
from cheeseless.bootstrap import (
    PRE_FREEZE_TASK,
    _terminate,
    build_context,
    host_session,
    resolve_cli_launch,
)
from cheeseless.domain import ids
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    NewReferenceAttempt,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.session_options import SessionOptionError

PLACEHOLDER_TASK = PRE_FREEZE_TASK
S = ReferenceState


@dataclass(frozen=True, slots=True)
class ReferenceRun:
    attempt_id: str
    mode: ReferenceMode
    state: ReferenceState
    reference_tree: str | None
    attached: bool
    pane_id: str | None
    exit_code: int | None
    warning: str | None = None
    problem: str | None = None

    @property
    def done(self) -> bool:
        return self.state is S.DONE


def _close_pane(panes: TmuxPanes, pane_id: str) -> None:
    try:
        panes.close(pane_id)
    except (PaneError, OSError) as exc:
        print(f"dev-reference: could not kill pane {pane_id}: {exc}", file=sys.stderr)


def _open_pane(panes: TmuxPanes, socket_path: Path) -> str:
    # The reference pane is a viewer (ADR 0019): it sends no frame and takes no input.
    argv = [
        sys.executable,
        "-m",
        "cheeseless",
        "pane-relay",
        "--socket",
        str(socket_path),
        "--view",
    ]
    return panes.open(argv)


def _current(store: object, attempt: ReferenceAttempt) -> ReferenceAttempt:
    found = store.reference_attempt(attempt.attempt_id)  # type: ignore[attr-defined]
    return found if isinstance(found, ReferenceAttempt) else attempt


def _finish(
    store: object,
    repo: object,
    source: str,
    attempt: ReferenceAttempt,
    projection: Path,
) -> tuple[ReferenceAttempt, str | None, str | None]:
    """Snapshot the stopped sandbox, move stopping -> done, then publish the snapshot."""
    try:
        tree = repo.snapshot(projection)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - a snapshot failure fails the attempt
        failed = fail_attempt(store, attempt)  # type: ignore[arg-type]
        return (failed or _current(store, attempt)), None, f"snapshot failed: {type(exc).__name__}"
    done = complete_attempt(store, attempt, tree)  # type: ignore[arg-type]
    if done is None:
        return _current(store, attempt), None, "the attempt moved before its snapshot was stored"
    problem: str | None = None
    try:
        record_reference_snapshot(repo, store, tree, source)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the snapshot is kept on the attempt either way
        problem = f"snapshot stored on the attempt but not published: {type(exc).__name__}"
    return done, tree, problem


def _supervise_tui(
    store: object,
    repo: object,
    source: str,
    attempt: ReferenceAttempt,
    proc: object,
    projection: Path,
    stop: threading.Event,
    deadline: float,
) -> tuple[ReferenceAttempt, str | None, int | None, str | None]:
    while True:
        code = proc.poll()  # type: ignore[attr-defined]
        if stop.is_set() or code is not None:
            _terminate(proc)  # type: ignore[arg-type]
            exit_code = proc.wait()  # type: ignore[attr-defined]
            current = _current(store, attempt)
            if current.state is S.STOPPING:
                done, tree, problem = _finish(store, repo, source, current, projection)
                return done, tree, exit_code, problem
            failed = fail_attempt(store, current)  # type: ignore[arg-type]
            problem = "the sandbox exited without reference_done"
            return (failed or current), None, exit_code, problem
        if time.monotonic() >= deadline:
            failed = fail_attempt(store, _current(store, attempt))  # type: ignore[arg-type]
            _terminate(proc)  # type: ignore[arg-type]
            return (failed or _current(store, attempt)), None, None, "the attempt timed out"
        stop.wait(0.05)


def _supervise_headless(
    store: object,
    repo: object,
    source: str,
    attempt: ReferenceAttempt,
    proc: object,
    projection: Path,
    deadline: float,
) -> tuple[ReferenceAttempt, str | None, int | None, str | None]:
    remaining = max(0.0, deadline - time.monotonic())
    try:
        code = proc.wait(timeout=remaining)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired without importing it here
        failed = fail_attempt(store, _current(store, attempt))  # type: ignore[arg-type]
        _terminate(proc)  # type: ignore[arg-type]
        return (failed or _current(store, attempt)), None, None, "the attempt timed out"
    if code != 0:
        failed = fail_attempt(store, _current(store, attempt))  # type: ignore[arg-type]
        return (failed or _current(store, attempt)), None, code, "the sandbox exited non-zero"
    stopped = stop_attempt(store, _current(store, attempt))  # type: ignore[arg-type]
    if stopped is None:
        return _current(store, attempt), None, code, "the attempt moved before its exit"
    done, tree, problem = _finish(store, repo, source, stopped, projection)
    return done, tree, code, problem


def run_reference_attempt(
    *,
    lineage: str,
    spec_digest: str,
    agent_command: Sequence[str],
    source: str | None = None,
    task_id: str | None = None,
    headless: bool = False,
    timeout: float = 1800.0,
    rows: int = 24,
    cols: int = 80,
    state_dir: Path | None = None,
    cwd: Path | None = None,
    attempt_id: str | None = None,
    panes: TmuxPanes | None = None,
    host_env: dict[str, str] | None = None,
    agent_model: str = "",
) -> ReferenceRun:
    """Run one reference attempt to a terminal state and return its host record."""
    if not agent_command:
        raise SessionOptionError("an agent command is required after --")
    if not 1 <= rows <= 0xFFFF or not 1 <= cols <= 0xFFFF:
        raise SessionOptionError("the pty size is out of range")
    lineage_id = ids.lineage_id(lineage)
    digest = ids.sha256_hex(spec_digest, "spec digest")
    task = ids.sha256_hex(task_id or PLACEHOLDER_TASK, "task id")
    panes = panes or TmuxPanes()
    env = dict(os.environ if host_env is None else host_env)
    mode = choose_mode(headless=headless, in_tmux=TmuxPanes.available(env))
    warning = None
    if mode is ReferenceMode.HEADLESS and not headless:
        warning = (
            "tmux is not available: running the reference headless (no pane, no reference_done)"
        )

    ctx = build_context(state_dir, cwd)
    store = ctx.store
    dev = store.dev_state(lineage_id)  # type: ignore[attr-defined]
    if dev is None or dev.spec_digest is None:
        raise SessionOptionError(f"no dev session with a spec is open for {lineage_id}")
    if dev.spec_digest != digest:
        raise SessionOptionError("the spec digest does not match the open dev session")
    source = source or ctx.repo.head_commit()
    contract = read_contract(ctx.repo, source)
    if contract.lineage_id != lineage_id or spec_of(contract) != digest:
        raise SessionOptionError("the contract at the source does not match the attempt's spec")

    reap_attempts(
        store,  # type: ignore[arg-type]
        lineage_id,
        lambda pane: _close_pane(panes, pane),
    )

    attempt = attempt_id or f"att-{secrets.token_hex(8)}"
    ids.attempt_id(attempt)
    if store.reference_attempt(attempt) is not None:  # type: ignore[attr-defined]
        raise SessionOptionError(f"reference attempt {attempt} already exists")

    projection = Path(tempfile.mkdtemp(prefix="cheeseless-reference-"))
    stop = threading.Event()
    log_file = store.reference_log_path(attempt) if mode is ReferenceMode.HEADLESS else None  # type: ignore[attr-defined]
    final: ReferenceAttempt | None = None
    tree: str | None = None
    problem: str | None = None
    exit_code: int | None = None
    pane_id: str | None = None
    try:
        with host_session(
            state_dir=state_dir,
            cwd=cwd,
            task_id=task,
            projection_dir=projection,
            agent_command=list(agent_command),
            role="reference",
            new_reference=NewReferenceAttempt(attempt, lineage_id, digest, mode),
            on_reference_stop=lambda _attempt: stop.set(),
            agent_pty=(rows, cols) if mode is ReferenceMode.TUI else None,
            pane_lineage=lineage_id if mode is ReferenceMode.TUI else None,
            log_file=log_file,
            agent_model=agent_model,
        ) as info:
            attempt_record = info["reference_attempt"]
            if not isinstance(attempt_record, ReferenceAttempt):
                raise SessionOptionError("the session did not create a reference attempt")
            owner = AttemptOwner(
                attempt_record.session_id, attempt_record.generation, attempt_record.mode
            )
            if mode is ReferenceMode.TUI:
                relay = info["relay"]
                if relay is None:
                    raise SessionOptionError("the TUI attempt has no relay")
                pane_id = _open_pane(panes, relay.socket_path)
                if store.bind_reference_attempt(attempt, owner=owner, pane_id=pane_id) is None:  # type: ignore[attr-defined]
                    raise SessionOptionError("the attempt record changed before its pane was bound")
            deadline = time.monotonic() + timeout
            proc = info["agent_proc"]
            if mode is ReferenceMode.TUI:
                final, tree, exit_code, problem = _supervise_tui(
                    store, ctx.repo, source, attempt_record, proc, projection, stop, deadline
                )
            else:
                final, tree, exit_code, problem = _supervise_headless(
                    store, ctx.repo, source, attempt_record, proc, projection, deadline
                )
    except BaseException:
        current = store.reference_attempt(attempt)  # type: ignore[attr-defined]
        if current is not None and current.state in (S.RUNNING, S.STOPPING):
            fail_attempt(store, current)  # type: ignore[arg-type]
        raise
    finally:
        if pane_id is not None:
            _close_pane(panes, pane_id)
        shutil.rmtree(projection, ignore_errors=True)

    final = final or store.reference_attempt(attempt)  # type: ignore[attr-defined]
    if final is None:
        raise RuntimeError("the reference attempt record disappeared")
    return ReferenceRun(
        attempt_id=attempt,
        mode=mode,
        state=final.state,
        reference_tree=tree,
        attached=final.attached,
        pane_id=pane_id,
        exit_code=exit_code,
        warning=warning,
        problem=problem,
    )


def _payload(result: ReferenceRun) -> dict[str, object]:
    return {
        "attempt_id": result.attempt_id,
        "mode": result.mode.value,
        "state": result.state.value,
        "reference_tree": result.reference_tree,
        "attached": result.attached,
        "pane_id": result.pane_id,
        "exit_code": result.exit_code,
        "warning": result.warning,
        "problem": result.problem,
    }


def run_reference_command(args: argparse.Namespace, agent: list[str] | None) -> int:
    try:
        agent, agent_model = resolve_cli_launch(getattr(args, "launcher", None), agent)
        result = run_reference_attempt(
            lineage=args.lineage,
            spec_digest=args.spec_digest,
            agent_command=agent or [],
            source=args.source,
            task_id=args.task,
            headless=bool(args.headless),
            timeout=args.timeout,
            rows=args.rows,
            cols=args.cols,
            state_dir=getattr(args, "state_dir", None),
            attempt_id=args.attempt,
            agent_model=agent_model,
        )
    except (SessionOptionError, ValueError, RuntimeError) as exc:
        print(f"dev-reference: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(_payload(result), indent=2, sort_keys=True))
    else:
        if result.warning:
            print(f"warning: {result.warning}", file=sys.stderr)
        line = f"{result.attempt_id}: {result.state.value} ({result.mode.value})"
        if result.reference_tree:
            line += f" tree {result.reference_tree}"
        print(line)
        if result.problem:
            print(f"problem: {result.problem}", file=sys.stderr)
    return 0 if result.done else 1


__all__ = ["ReferenceRun", "run_reference_attempt", "run_reference_command"]
