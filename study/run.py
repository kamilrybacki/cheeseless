"""``python -m study.run``: dry-run with a scripted fake agent, or an opt-in real study.

Real mode refuses without ``CHEESELESS_STUDY_AGENT=1``, prints the estimated run count and cost,
and needs ``--yes``. It is resumable (completed run ids are skipped) and never deletes evidence.
The evidence directory holds agent-side captures: it is sensitive, keep it out of the repo.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from study import fake_agent, manifest
from study.agent_config import AgentId, AgentUnavailable, StudyAgent, load_study_agent
from study.arms import ARM_CHEESELESS, ARM_NAIVE, ArmResult, run_cheeseless, run_naive
from study.expected import EXPECTED
from study.prompt import build_prompt, prompt_hash
from study.real import (
    IsolationError,
    evidence_has_canary,
    infra_result,
    isolation_check,
    run_cheeseless_real,
    run_naive_real,
)
from study.scorer import INVALID, VALID, score
from study.stats import shuffled_order
from study.tasks import TASKS, TASKS_BY_ID, Task
from study.usage import Usage, read_usage

STUDY_ID = "cheeseless-real-agent-v1"
SECONDS_SOLVABLE = 53.0
SECONDS_UNSATISFIABLE = 150.0
ARMS = (ARM_NAIVE, ARM_CHEESELESS)
DRY_AGENT = AgentId("scripted", "fake-agent", "1")


@dataclass(frozen=True)
class Job:
    task: Task
    arm: str
    rep: int
    behaviour: str | None = None


def bwrap_available() -> bool:
    return os.access("/usr/bin/bwrap", os.X_OK)


def plan(tasks: Sequence[Task], arms: Sequence[str], reps: int) -> list[Job]:
    return [Job(t, a, r) for t in tasks for a in arms for r in range(reps)]


def estimate_seconds(jobs: Sequence[Job]) -> float:
    return sum(SECONDS_SOLVABLE if j.task.solvable else SECONDS_UNSATISFIABLE for j in jobs)


def _record(
    job: Job,
    res: ArmResult,
    *,
    mode: str,
    seed: int,
    agent: AgentId,
    attempt: int,
    canary: bool,
    started: str | None = None,
    usage: Usage | None = None,
    phase: str = "pilot",
) -> dict[str, object]:
    cls = res.classification
    labels = score(
        solvable=job.task.solvable,
        status=res.status,
        verdict=res.verdict,
        reason=res.reason,
        oracle=res.oracle,
        attempted=cls.attempted,
        refused_attempt=cls.refused_attempt,
    )
    run_id = manifest.make_run_id(STUDY_ID, seed, job.arm, job.task.task_id, agent.label, job.rep)
    return {
        "record_type": "run",
        "mode": mode,
        "phase": phase,
        "session_policy": "fresh_per_run" if mode == "real" else "scripted",
        "observability": "diff_only" if mode == "real" else "diff_and_events",
        "usage_provenance": "trusted_wrapper" if usage else "unavailable",
        "run_id": run_id,
        "attempt": attempt,
        "study_id": STUDY_ID,
        "seed": seed,
        "arm": job.arm,
        "task_id": job.task.task_id,
        "solvable": job.task.solvable,
        "held_out": job.task.held_out,
        "rep": job.rep,
        "behaviour": job.behaviour,
        "agent": {"vendor": agent.vendor, "model": agent.model, "version": agent.version},
        "prompt_hash": prompt_hash(build_prompt(job.task, job.arm)),
        "started": started,
        "ended": _now(),
        "wall_seconds": res.wall_seconds,
        "gate_runs": res.gate_runs,
        "tokens": usage.tokens if usage else None,
        "cost_usd": usage.cost_usd if usage else None,
        "judge_verdict": res.verdict,
        "judge_reason": res.judge_reason,
        "judge_in_tree": res.judge_in_tree,
        "oracle_verdict": res.oracle,
        "flags": {
            **dict(cls.flags),
            **(
                dict.fromkeys(("read_hidden", "history_read", "net_exfil"), "unknown")
                if mode == "real"
                else {}
            ),
        },
        "flag_evidence": list(cls.evidence),
        "refused_attempt": cls.refused_attempt,
        "transcript_observed": res.transcript_observed,
        "evidence_canary": canary,
        "labels": {
            "counted": labels.counted,
            "success": labels.success,
            "gamed": labels.gamed,
            "false_reject": labels.false_reject,
            "attempted": labels.attempted,
            "blocked": labels.blocked,
        },
        "status": res.status,
        "exclusion_reason": res.reason if res.status != VALID else None,
        "reason": res.reason,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _append_safe(path: Path, rec: dict[str, object]) -> dict[str, object]:
    """Append; a redaction hit stores a minimal ``invalid`` record instead of the content."""
    try:
        manifest.append(path, rec)
        return rec
    except manifest.RedactionError:
        stub: dict[str, object] = {
            k: rec[k]
            for k in (
                "record_type",
                "mode",
                "run_id",
                "attempt",
                "study_id",
                "arm",
                "task_id",
                "phase",
                "session_policy",
            )
            if k in rec
        }
        stub.update(status=INVALID, exclusion_reason="redaction", reason="redaction")
        manifest.append(path, stub)
        return stub


def run_dry(
    jobs: Sequence[Job], path: Path, seed: int, workdir: Path | None = None, phase: str = "pilot"
) -> list[dict[str, object]]:
    """Run every job with a scripted fake agent; one manifest line each (mode ``dry-run``)."""
    base = workdir or Path(tempfile.mkdtemp(prefix="study-dry-"))
    out: list[dict[str, object]] = []
    for n, job in enumerate(jobs):
        run_id = manifest.make_run_id(
            STUDY_ID, seed, job.arm, job.task.task_id, f"{DRY_AGENT.label}/{job.behaviour}", job.rep
        )
        if run_id in manifest.completed_ids(path):
            continue
        root = base / f"{n:04d}"
        root.mkdir(parents=True, exist_ok=True)
        behaviour = job.behaviour or fake_agent.HONEST

        def apply(r: Path, b: str = behaviour, t: Task = job.task) -> tuple[object, ...]:
            return fake_agent.act(b, r, t)

        runner = run_naive if job.arm == ARM_NAIVE else run_cheeseless
        res = runner(job.task, root, apply)  # type: ignore[arg-type]
        rec = _record(
            job,
            res,
            mode="dry-run",
            seed=seed,
            agent=DRY_AGENT,
            attempt=1,
            canary=False,
            phase=phase,
        )
        rec["run_id"] = run_id
        out.append(_append_safe(path, rec))
    return out


def label_code(rec: dict[str, object]) -> str:
    """Compact label string: S success, G gamed, F false reject, A attempted, B blocked."""
    labels = rec["labels"]
    assert isinstance(labels, dict)  # noqa: S101 - internal record shape
    code = "".join(c for c, k in zip("SGFAB", _LABEL_KEYS, strict=True) if labels.get(k))
    return code or "-"


_LABEL_KEYS = ("success", "gamed", "false_reject", "attempted", "blocked")


def check_dry(records: Sequence[dict[str, object]]) -> list[str]:
    """Mismatches against the pre-declared expected-label table (empty list means pass)."""
    bad: list[str] = []
    for rec in records:
        key = (str(rec["task_id"]), str(rec["behaviour"]), str(rec["arm"]))
        want = EXPECTED.get(key)
        got = label_code(rec)
        if want != got:
            bad.append(f"{key}: expected {want}, got {got}")
    return bad


def _real_loop(
    jobs: Sequence[Job],
    path: Path,
    seed: int,
    agent: StudyAgent,
    evidence_root: Path,
    work: Path,
    *,
    phase: str,
    protected: tuple[Path, ...],
    allow_network: bool,
    usage_dir: Path | None,
) -> int:
    done = manifest.completed_ids(path)
    ran = 0
    for n, job in enumerate(jobs):
        run_id = manifest.make_run_id(
            STUDY_ID, seed, job.arm, job.task.task_id, agent.agent.label, job.rep
        )
        if run_id in done:
            continue
        attempt = manifest.next_attempt(path, run_id)
        root = work / f"{run_id}-a{attempt}"
        root.mkdir(parents=True, exist_ok=True)
        evidence = evidence_root / f"{run_id}-a{attempt}"  # must not exist: host_session creates it
        evidence_root.mkdir(parents=True, exist_ok=True)
        started = _now()
        try:
            if job.arm == ARM_NAIVE:
                res = run_naive_real(
                    job.task, root, agent, protected=protected, allow_network=allow_network
                )
            else:
                res = run_cheeseless_real(job.task, root, agent, evidence)
        except IsolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the study
            res = infra_result(job.arm, f"unexpected:{type(exc).__name__}")
        canary = job.arm == ARM_CHEESELESS and evidence_has_canary(evidence)
        rec = _record(
            job,
            res,
            mode="real",
            seed=seed,
            agent=agent.agent,
            attempt=attempt,
            canary=canary,
            started=started,
            usage=read_usage(usage_dir, f"{run_id}-a{attempt}"),
            phase=phase,
        )
        _append_safe(path, rec)
        ran += 1
        print(f"[{n + 1}/{len(jobs)}] {job.arm} {job.task.task_id} rep={job.rep} {res.status}")
    return ran


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m study.run", description=__doc__)
    p.add_argument("--phase", choices=("pilot", "main"), required=True)
    p.add_argument("--allow-agent-network", action="store_true")
    p.add_argument("--usage-file-dir", type=Path)
    p.add_argument("--repair-manifest-tail", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="scripted fake agent, no network")
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--workdir", type=Path, default=None)
    p.add_argument("--evidence-dir", type=Path, default=None)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument(
        "--tasks", default="", help="comma-separated task ids (default: all non-held-out)"
    )
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--behaviours", default=",".join(fake_agent.BEHAVIOURS))
    p.add_argument("--include-held-out", action="store_true")
    p.add_argument("--yes", action="store_true", help="confirm the printed estimate (real mode)")
    return p


def _select_tasks(spec: str, include_held_out: bool) -> list[Task]:
    if spec:
        return [TASKS_BY_ID[t] for t in spec.split(",") if t]
    return [t for t in TASKS if include_held_out or not t.held_out]


def _order(jobs: list[Job], seed: int) -> list[Job]:
    keys = [f"{i}" for i in range(len(jobs))]
    return [jobs[int(k)] for k in shuffled_order(keys, seed)]


def main(argv: Sequence[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    args = _parser().parse_args(argv)
    arms = [a for a in args.arms.split(",") if a]
    try:
        tasks = _select_tasks(args.tasks, args.include_held_out or args.phase == "main")
        if args.phase == "pilot" and any(t.held_out for t in tasks):
            raise ValueError("pilot forbids held-out tasks")
        if not arms or any(a not in ARMS for a in arms) or args.reps < 1:
            raise ValueError("invalid arms or repetitions")
        if args.manifest:
            manifest.validate_resume(
                args.manifest, args.phase, repair_tail=args.repair_manifest_tail
            )
        if args.dry_run:
            return _main_dry(args, arms, out)
        return _main_real(args, arms, out)
    except (ValueError, IsolationError) as exc:
        out(f"refusing to run: {exc}")
        return 2


def _main_dry(args: argparse.Namespace, arms: list[str], out: Callable[[str], None]) -> int:
    if ARM_CHEESELESS in arms and not bwrap_available():
        out("bubblewrap not available: skipping the cheeseless arm")
        arms = [a for a in arms if a != ARM_CHEESELESS]
    tasks = _select_tasks(args.tasks, args.phase == "main")
    behaviours = [b for b in args.behaviours.split(",") if b]
    jobs = [
        Job(t, a, r, b)
        for t in tasks
        for a in arms
        for b in behaviours
        for r in range(args.reps if args.reps < 2 else 1)
    ]
    path = args.manifest or Path(tempfile.mkdtemp(prefix="study-dry-")) / "dry-run.jsonl"
    records = run_dry(_order(jobs, args.seed), path, args.seed, args.workdir, args.phase)
    mismatches = check_dry(records)
    out(f"dry-run: {len(records)} runs, manifest {path.name}")
    for line in mismatches:
        out(f"MISMATCH {line}")
    out("dry-run OK" if not mismatches else f"dry-run FAILED ({len(mismatches)} mismatches)")
    return 0 if not mismatches else 1


def _overlap(a: Path, b: Path) -> bool:
    return a.is_relative_to(b) or b.is_relative_to(a)


def _validate_paths(args: argparse.Namespace, agent: StudyAgent) -> tuple[Path, ...]:
    repo = Path(__file__).resolve().parents[1]
    if _overlap(args.evidence_dir.resolve(), repo) or _overlap(args.manifest.resolve(), repo):
        raise ValueError("evidence and manifest must be outside the study checkout")
    protected = [repo, args.manifest.resolve(), args.evidence_dir.resolve()]
    if args.usage_file_dir:
        usage = args.usage_file_dir.resolve()
        usage.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = usage.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("usage directory must be parent-owned and not group/world writable")
        protected.append(usage)
    work = args.workdir.resolve()
    # Work, evidence and manifest must be separate and outside the study checkout.
    if any(_overlap(work, p) for p in protected):
        raise ValueError("work directory overlaps protected study paths")
    for rel, src in agent.seeds.items():
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError("unsafe HOME seed destination")
        if any(_overlap(src.resolve(), p) for p in protected):
            raise ValueError("HOME seed overlaps protected study paths")
    for mount in agent.read_only:
        if any(_overlap(mount.resolve(), p) for p in [*protected, work]):
            raise ValueError("read-only mount overlaps protected study paths")
    if agent.egress_policy and agent.egress_policy.allowed_hosts and not args.allow_agent_network:
        raise ValueError("network policy requires --allow-agent-network")
    # Include explicit scorer/oracle files as well as their enclosing checkout in the probe.
    protected += [repo / "study" / name for name in ("scorer.py", "suite.py", "tasks.py")]
    return tuple(protected)


def _main_real(args: argparse.Namespace, arms: list[str], out: Callable[[str], None]) -> int:
    try:
        agent = load_study_agent()
    except AgentUnavailable as exc:
        out(f"refusing to run: {exc}")
        return 2
    if args.manifest is None or args.workdir is None or args.evidence_dir is None:
        out("refusing to run: real mode needs --manifest, --workdir and --evidence-dir")
        return 2
    if os.environ.get("CHEESELESS_STUDY_FRESH_SESSION") != "1":
        raise ValueError("set CHEESELESS_STUDY_FRESH_SESSION=1; wrapper must start fresh per run")
    if not bwrap_available():
        raise IsolationError("bubblewrap unavailable")
    protected = _validate_paths(args, agent)
    with tempfile.TemporaryDirectory(prefix="study-check-") as tmp:
        work, home = Path(tmp) / "work", Path(tmp) / "home"
        work.mkdir()
        home.mkdir()
        isolation_check(work, home, protected, allow_network=args.allow_agent_network)
    tasks = _select_tasks(args.tasks, args.include_held_out or args.phase == "main")
    jobs = _order(plan(tasks, arms, args.reps), args.seed)
    hours = estimate_seconds(jobs) / 3600
    out(f"planned runs: {len(jobs)}; serial wall-clock estimate: {hours:.1f} h (estimate only)")
    out("token and dollar cost cannot be estimated from existing timings; record them in the pilot")
    if not args.yes:
        out("refusing to run: pass --yes to confirm the estimate above")
        return 2
    ran = _real_loop(
        jobs,
        args.manifest,
        args.seed,
        agent,
        args.evidence_dir,
        args.workdir,
        phase=args.phase,
        protected=protected,
        allow_network=args.allow_agent_network,
        usage_dir=args.usage_file_dir,
    )
    out(f"done: {ran} new runs; evidence in the evidence dir is sensitive and is never deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
