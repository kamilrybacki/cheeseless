"""gate_run use case: run the hidden suite against a candidate tree."""

from __future__ import annotations

import hashlib
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from cheeseless.domain.budget import Pool
from cheeseless.domain.disclosure import RunReport, Verdict, decide
from cheeseless.domain.dlp import DlpScanner
from cheeseless.domain.errors import ErrorCode, ErrorDetails, gate_error
from cheeseless.domain.evaluation import criteria_statuses, smoke_passed
from cheeseless.domain.result import Result, sign
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import Sandbox
from cheeseless.ports.store import Store

PRE_HIDDEN_ATTEMPT_LIMIT = 10
PRE_HIDDEN_ATTEMPT_WINDOW = 60.0


def _baseline_digest(frozen: Any) -> str:
    return hashlib.sha256(frozen.baseline_commit.encode()).hexdigest()


def _safe_tail(scanner: DlpScanner | None, text: str) -> str:
    """Only a clean, scanned tail may reach the agent; omission otherwise (I16)."""
    if not text or scanner is None:
        return ""
    try:
        if scanner.scan(text.encode("utf-8", "replace")) is not None:
            return ""
    except Exception:  # noqa: BLE001 - scan failure must omit, never leak
        return ""
    return text


def gate_run(
    repo: Repository,
    store: Store,
    runner: SuiteRunner,
    sandbox: Sandbox,
    *,
    worktree: str | Path,
    task_id: str | None = None,
    profile: str = "local",
    tool_version: str = "0.1.0",
    timeout: float = 300.0,
    pool: Pool = Pool.LOCAL,
    attempt_limit: int | None = None,
    attempt_window: float | None = None,
    scanner: DlpScanner | None = None,
) -> Verdict:
    if task_id is None:
        task_id = store.active_task_id()
    if task_id is None:
        raise ValueError("no active task")
    frozen, _seed = store.load_task(task_id)
    task = frozen.task

    if attempt_limit is None:
        attempt_limit = PRE_HIDDEN_ATTEMPT_LIMIT
    if attempt_window is None:
        attempt_window = PRE_HIDDEN_ATTEMPT_WINDOW
    attempts = store.record_attempt(task_id, time.time(), attempt_window)
    if attempts > attempt_limit:
        err = gate_error(ErrorCode.RATE_LIMITED)
        verdict = Verdict(err, charged=False)
        store.log_event(task_id, "gate_run", "rate_limited")
        return verdict

    candidate_tree = repo.snapshot(Path(worktree))
    baseline_digest = _baseline_digest(frozen)

    selected = dict(frozen.holdout if pool is Pool.CI else frozen.visible)

    with sandbox.trusted_scratch() as hidden_scratch:
        acc_root = hidden_scratch / "acceptance"
        repo.export(frozen.acceptance_tree, acc_root)
        with tempfile.TemporaryDirectory(prefix="cheeseless-run-candidate-") as cand_tmp:
            cand_root = Path(cand_tmp) / "candidate"
            repo.export(candidate_tree, cand_root)

            env = list(frozen.contract.env)
            try:
                build = sandbox.build(cand_root, frozen.contract.build, env)
            except Exception:  # noqa: BLE001
                report = RunReport(
                    pre_hidden_failure=ErrorCode.STARTUP_CRASH,
                    stderr_tail="",
                )
                verdict = decide(
                    task,
                    report,
                    tree_hash=candidate_tree,
                    baseline_digest=baseline_digest,
                    profile=profile,
                    tool_version=tool_version,
                )
                store.log_event(task_id, "gate_run", "startup_crash")
                return verdict
            if not build.ok:
                report = RunReport(
                    pre_hidden_failure=ErrorCode.BUILD_FAILED,
                    stderr_tail=_safe_tail(scanner, build.stderr_tail),
                )
                verdict = decide(
                    task,
                    report,
                    tree_hash=candidate_tree,
                    baseline_digest=baseline_digest,
                    profile=profile,
                    tool_version=tool_version,
                )
                store.log_event(task_id, "gate_run", "build_failed")
                return verdict

            # Public smoke tests (pre-hidden).
            try:
                with sandbox.scratch() as scratch:
                    launcher = sandbox.launcher(cand_root, env, scratch)
                    smoke_out = runner.run(
                        root=acc_root,
                        paths=["contract"],
                        selected=None,
                        launcher=launcher,
                        run=frozen.contract.run,
                        timeout=timeout,
                    )
            except Exception:  # noqa: BLE001
                report = RunReport(
                    pre_hidden_failure=ErrorCode.STARTUP_CRASH,
                    stderr_tail="",
                )
                verdict = decide(
                    task,
                    report,
                    tree_hash=candidate_tree,
                    baseline_digest=baseline_digest,
                    profile=profile,
                    tool_version=tool_version,
                )
                store.log_event(task_id, "gate_run", "startup_crash")
                return verdict
            if not smoke_passed(smoke_out.suite):
                report = RunReport(
                    pre_hidden_failure=ErrorCode.SMOKE_TEST_FAILED,
                    stderr_tail=_safe_tail(scanner, smoke_out.output),
                )
                verdict = decide(
                    task,
                    report,
                    tree_hash=candidate_tree,
                    baseline_digest=baseline_digest,
                    profile=profile,
                    tool_version=tool_version,
                )
                store.log_event(task_id, "gate_run", "smoke_failed")
                return verdict

            budget = store.reserve_run(frozen.task.lineage_id, pool)
            if budget is None:
                err = gate_error(
                    ErrorCode.RUN_BUDGET_EXHAUSTED,
                    details=ErrorDetails(budget=store.budget(frozen.task.lineage_id, pool).state()),
                )
                verdict = Verdict(err, charged=False, operator_cause="run budget exhausted")
                store.log_event(task_id, "gate_run", "budget_exhausted")
                return verdict

            try:
                with sandbox.scratch() as scratch:
                    launcher = sandbox.launcher(cand_root, env, scratch)
                    out = runner.run(
                        root=acc_root,
                        paths=["hidden"],
                        selected=selected,
                        launcher=launcher,
                        run=frozen.contract.run,
                        timeout=timeout,
                    )
            except Exception as exc:  # noqa: BLE001
                report = RunReport(
                    hidden_input_read=True,
                    criteria={},
                    infra_cause=str(exc),
                )
                verdict = decide(
                    task,
                    report,
                    tree_hash=candidate_tree,
                    baseline_digest=baseline_digest,
                    profile=profile,
                    tool_version=tool_version,
                )
                if isinstance(verdict.outcome, Result):
                    key = store.hmac_key()
                    signed = sign(verdict.outcome, key)
                    verdict = replace(verdict, outcome=signed)
                    store.save_result(signed, operator_cause=verdict.operator_cause)
                store.log_event(task_id, "gate_run", verdict.operator_cause or "exception")
                return verdict
            suite = out.suite

    statuses, notes = criteria_statuses(selected, suite)
    infra_cause = "; ".join(part for part in (suite.infra_cause, notes) if part)
    report = RunReport(
        hidden_input_read=True,
        criteria=statuses,
        infra_cause=infra_cause,
    )
    verdict = decide(
        task,
        report,
        tree_hash=candidate_tree,
        baseline_digest=baseline_digest,
        profile=profile,
        tool_version=tool_version,
    )
    if isinstance(verdict.outcome, Result):
        key = store.hmac_key()
        signed = sign(verdict.outcome, key)
        verdict = replace(verdict, outcome=signed)
        store.save_result(signed, operator_cause=verdict.operator_cause)
    store.log_event(task_id, "gate_run", verdict.operator_cause or "ok")
    return verdict


__all__ = ["gate_run"]
