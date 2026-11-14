"""Command-line adapter (host CLI and agent-facing ``--json``)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cheeseless import __version__
from cheeseless.adapters.inbound.mapping import agent_view_to_dict, result_to_dict
from cheeseless.app.dlp import scanner_for
from cheeseless.app.freeze import FreezeError, commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import PromoteRejected, promote
from cheeseless.app.status import status
from cheeseless.domain.budget import Pool
from cheeseless.domain.result import Result
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import Sandbox
from cheeseless.ports.store import Store


@dataclass
class Context:
    repo: Repository
    runner: SuiteRunner
    sandbox: Sandbox
    store: Store


def split_agent_argv(argv: Sequence[str]) -> tuple[list[str], list[str] | None]:
    """Split ``argv`` at the first ``--``: (options, agent command or ``None``)."""
    items = list(argv)
    if "--" not in items:
        return items, None
    cut = items.index("--")
    return items[:cut], items[cut + 1 :]


def _add_session_parser(sub: Any) -> None:
    ses = sub.add_parser(
        "session",
        help="Run a real coding-agent CLI in the sandbox: session [options] -- AGENT [ARGS...]",
    )
    ses.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
    ses.add_argument("--task", default=None)
    ses.add_argument("--profile", choices=("local", "ci"), default="local")
    ses.add_argument("--timeout", type=float, default=300.0)
    ses.add_argument("--egress-policy", type=Path, default=None, metavar="FILE")
    ses.add_argument("--ro", type=Path, action="append", default=[], metavar="PATH")
    ses.add_argument("--home-seed", action="append", default=[], metavar="REL=SRC")
    ses.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    ses.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="New directory outside the repo; HOME and the projection list are copied into it.",
    )
    ses.add_argument(
        "--projection",
        type=Path,
        default=None,
        metavar="DIR",
        help="Empty or absent directory outside the repo; kept after the session for promote.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cheeseless", description="Black-box quality gate for coding agents."
    )
    parser.add_argument("--version", action="version", version=f"cheeseless {__version__}")
    parser.add_argument("--state-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command")

    freeze = sub.add_parser("freeze", help="Freeze acceptance tests for the current repo.")
    freeze.add_argument("--baseline", default=None)
    freeze.add_argument("--timeout", type=float, default=300.0)
    freeze.add_argument("--yes", action="store_true", help="Skip the approval prompt.")
    freeze.add_argument("--tool-version", default="0.1.0")

    run = sub.add_parser("run", help="Run the gate against a candidate worktree.")
    run.add_argument("--worktree", type=Path, default=None)
    run.add_argument("--task", default=None)
    run.add_argument("--profile", choices=("local", "ci"), default="local")
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--json", action="store_true")

    st = sub.add_parser("status", help="Show the latest verified result.")
    st.add_argument("--task", default=None)
    st.add_argument("--worktree", type=Path, default=None)
    st.add_argument("--json", action="store_true")

    prom = sub.add_parser("promote", help="Promote an evaluated tree to gate/<task>.")
    prom.add_argument("--worktree", type=Path, default=None)
    prom.add_argument("--task", default=None)
    prom.add_argument("--approve", action="append", default=[])
    prom.add_argument("--json", action="store_true")

    doc = sub.add_parser("doctor", help="Check for stray hidden material.")
    doc.add_argument("--json", action="store_true")

    uds = sub.add_parser("serve-uds", help="Run the verifier UDS server for one session.")
    uds.add_argument("--socket", type=Path, default=None)
    uds.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
    uds.add_argument("--task", default=None)
    uds.add_argument("--worktree", type=Path, default=None)
    uds.add_argument("--profile", choices=("local", "ci"), default="local")
    uds.add_argument("--timeout", type=float, default=300.0)
    uds.add_argument("--tool-version", default="0.1.0")
    uds.add_argument(
        "--pidns-fd",
        type=int,
        default=None,
        help="Inherited FD for the agent sandbox PID namespace (required to accept peers).",
    )

    mcp = sub.add_parser("serve-mcp", help="Run the cheeseless MCP server over stdio.")
    mcp.add_argument("--uds-socket", type=Path, required=True)
    mcp.add_argument("--task", default=None)
    mcp.add_argument("--timeout", type=float, default=10.0)

    export = sub.add_parser("ci-export", help="Write the secret CI bundle (0600, never printed).")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument(
        "--budget-repo",
        required=True,
        help="OWNER/NAME of the private CI budget ledger repository.",
    )
    export.add_argument(
        "--budget-root",
        required=True,
        help="Commit SHA of the seed commit on cheeseless/budget/<lineage>.",
    )

    cij = sub.add_parser("ci-judge", help="Judge a PR head in CI; prints only the public verdict.")
    cij.add_argument("--base", required=True)
    cij.add_argument("--head", required=True)
    cij.add_argument("--bundle-file", type=Path, default=None)
    cij.add_argument("--timeout", type=float, default=300.0)
    cij.add_argument("--json", action="store_true")

    _add_session_parser(sub)
    return parser


def _print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cmd_freeze(args: argparse.Namespace, ctx: Context) -> int:
    try:
        prepared = prepare(
            ctx.repo,
            ctx.runner,
            ctx.sandbox,
            baseline_commit=args.baseline,
            timeout=args.timeout,
            tool_version=args.tool_version,
        )
    except FreezeError as exc:
        print(f"freeze failed: {'; '.join(exc.problems)}", file=sys.stderr)
        return 1
    print(f"prepared freeze: {len(prepared.tests)} tests")
    if not args.yes:
        answer = input("Approve freeze? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted", file=sys.stderr)
            return 1
    result = commit(prepared, ctx.store)
    print(f"frozen task: {result.task_id}")
    return 0


def _cmd_run(args: argparse.Namespace, ctx: Context) -> int:
    worktree = args.worktree if args.worktree is not None else ctx.repo.root
    pool = Pool.CI if args.profile == "ci" else Pool.LOCAL
    try:
        verdict = gate_run(
            ctx.repo,
            ctx.store,
            ctx.runner,
            ctx.sandbox,
            worktree=worktree,
            task_id=args.task,
            profile=args.profile,
            timeout=args.timeout,
            pool=pool,
            scanner=scanner_for(ctx.store, args.task or ctx.store.active_task_id()),
        )
    except Exception:  # noqa: BLE001
        print("run failed: internal error", file=sys.stderr)
        return 1
    task_id = args.task if args.task is not None else ctx.store.active_task_id()
    task = None
    if task_id is not None:
        task = ctx.store.load_task(task_id)[0].task
    view = verdict.agent_view(task) if task is not None else None
    if args.json:
        if view is None:
            _print_json({"error": "no task"})
            return 1
        _print_json(agent_view_to_dict(view))
    else:
        outcome = verdict.outcome
        if isinstance(outcome, Result):
            print(outcome.status.value)
            for c in outcome.criteria:
                print(f"  {c.criterion_id}: {c.status.value}")
        else:
            print(f"{outcome.code.value}: {outcome.reason}")
            print(outcome.hint)
    passed = isinstance(verdict.outcome, Result) and verdict.outcome.status.value == "pass"
    return 0 if passed else 1


def _cmd_status(args: argparse.Namespace, ctx: Context) -> int:
    worktree = args.worktree
    repo = ctx.repo if worktree is not None else None
    res = status(ctx.store, task_id=args.task, repo=repo, worktree=worktree)
    if res is None:
        print("no result")
        return 1
    if args.json:
        _print_json(result_to_dict(res))
    else:
        print(res.status.value)
        for c in res.criteria:
            print(f"  {c.criterion_id}: {c.status.value}")
    return 0


def _cmd_promote(args: argparse.Namespace, ctx: Context) -> int:
    worktree = args.worktree if args.worktree is not None else ctx.repo.root
    try:
        outcome = promote(
            ctx.repo,
            ctx.store,
            task_id=args.task,
            worktree=worktree,
            approved=tuple(args.approve),
        )
    except PromoteRejected as exc:
        if args.json:
            _print_json(
                {
                    "rejected": list(exc.rejected),
                    "needs_approval": list(exc.needs_approval),
                }
            )
        else:
            print(f"rejected: {exc.rejected}", file=sys.stderr)
            if exc.needs_approval:
                print(f"needs approval: {exc.needs_approval}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001
        print("promote failed: internal error", file=sys.stderr)
        return 1
    if args.json:
        _print_json({"ref": outcome.ref, "commit": outcome.commit})
    else:
        print(f"promoted {outcome.ref} {outcome.commit}")
    return 0


def _cmd_doctor(args: argparse.Namespace, ctx: Context) -> int:
    task_id = ctx.store.active_task_id()
    strays: list[str] = []
    if task_id is not None:
        try:
            frozen, _seed = ctx.store.load_task(task_id)
            strays = ctx.repo.stray_hidden(ctx.repo.root, frozen.hidden_blobs)
        except Exception:  # noqa: BLE001
            strays = []
    if args.json:
        _print_json({"task_id": task_id, "strays": strays})
    else:
        if strays:
            print("stray hidden material found:")
            for s in strays:
                print(f"  {s}")
        else:
            print("no stray hidden material")
    return 1 if strays else 0


def dispatch(args: argparse.Namespace, ctx: Context) -> int:
    if args.command == "freeze":
        return _cmd_freeze(args, ctx)
    if args.command == "run":
        return _cmd_run(args, ctx)
    if args.command == "status":
        return _cmd_status(args, ctx)
    if args.command == "promote":
        return _cmd_promote(args, ctx)
    if args.command == "doctor":
        return _cmd_doctor(args, ctx)
    return 2


def main(argv: list[str] | None = None, *, context: Context | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args([] if argv is None else argv)
    if args.command is None:
        parser.print_help()
        return 2
    if context is None:
        print("cheeseless must be invoked through its bootstrap", file=sys.stderr)
        return 2
    return dispatch(args, context)


__all__ = ["Context", "build_parser", "dispatch", "main"]
