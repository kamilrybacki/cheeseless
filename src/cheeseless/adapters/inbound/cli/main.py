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
from cheeseless.app.authoring import (
    AuthorError,
    AuthorStore,
    approve_freeze,
    record_reference,
    recover_approval,
    reference_failed,
    start_reference,
)
from cheeseless.app.dispute import configure_disputes, list_disputes, resolve_dispute
from cheeseless.app.dlp import scanner_for
from cheeseless.app.freeze import FreezeError, commit, prepare, read_contract
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import PromoteRejected, promote, promote_author
from cheeseless.app.status import status
from cheeseless.domain.authoring import AuthoringError
from cheeseless.domain.budget import Pool
from cheeseless.domain.dispute import DEFAULT_CAP, DisputeRecord
from cheeseless.domain.result import Result
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import AuthorSandbox, Sandbox
from cheeseless.ports.store import Store, StoreError


@dataclass
class Context:
    repo: Repository
    runner: SuiteRunner
    sandbox: Sandbox
    store: Store
    # The contained author runner; approval runs author-proposed tests only through it.
    author_sandbox: AuthorSandbox | None = None


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
    freeze.add_argument(
        "--approve",
        metavar="PROPOSAL_ID",
        default=None,
        help="Host only: approve a proposed freeze (ADR 0009).",
    )
    freeze.add_argument("--as", dest="actor", default=None, help="Who approves (with --approve).")
    freeze.add_argument(
        "--no-reference",
        action="store_true",
        help="Waive the reference crosscheck; needs --reason.",
    )
    freeze.add_argument("--reason", default="", help="Why the crosscheck is waived.")
    freeze.add_argument("--json", action="store_true")

    ref = sub.add_parser("reference", help="Host only: reference-session bookkeeping.")
    rsub = ref.add_subparsers(dest="reference_command")
    for name, text in (
        ("record", "Snapshot a reference worktree for the current spec."),
        ("failed", "Record that a reference session failed (blocks the task)."),
        ("retry", "Start a reference retry (at most two)."),
    ):
        rp = rsub.add_parser(name, help=text)
        rp.add_argument("--source", default=None, help="Commit or tree with the contract.")
        rp.add_argument("--json", action="store_true")
        if name == "record":
            rp.add_argument("--worktree", type=Path, required=True)

    auth = sub.add_parser("author", help="Host only: authoring lifecycle recovery.")
    asub = auth.add_subparsers(dest="author_command")
    rec = asub.add_parser(
        "recover", help="Clear an approval reservation whose approver died (refuses if alive)."
    )
    rec.add_argument("--task", required=True, help="The proposal (task) id.")
    rec.add_argument("--as", dest="actor", default=None, help="Who recovers (audit log).")
    rec.add_argument("--json", action="store_true")

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

    prom = sub.add_parser(
        "promote",
        help="Promote an evaluated tree to gate/<task>, or with --role author to spec/<lineage>.",
    )
    prom.add_argument(
        "--role",
        choices=("implementer", "author"),
        default="implementer",
        help="implementer: gate/<task>; author: spec/<lineage> (acceptance/ only).",
    )
    prom.add_argument("--worktree", type=Path, default=None)
    prom.add_argument("--task", default=None)
    prom.add_argument("--approve", action="append", default=[])
    prom.add_argument("--json", action="store_true")

    doc = sub.add_parser("doctor", help="Check for stray hidden material.")
    doc.add_argument("--json", action="store_true")

    dsp = sub.add_parser(
        "disputes",
        help="Host only: list disputes. The note is untrusted and hidden unless --show-note.",
    )
    dsp.add_argument("--json", action="store_true")
    dsp.add_argument("--show-note", action="store_true")

    dis = sub.add_parser("dispute", help="Host only: resolve a dispute or set the policy.")
    dsub = dis.add_subparsers(dest="dispute_command")
    res = dsub.add_parser("resolve", help="Record the outcome of one dispute, once.")
    res.add_argument("dispute_id")
    outcome = res.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--upheld", action="store_true")
    outcome.add_argument("--rejected", action="store_true")
    res.add_argument("--as", dest="actor", required=True, metavar="ACTOR")
    res.add_argument("--json", action="store_true")
    cfg = dsub.add_parser("config", help="Enable or disable disputes and set the lineage cap.")
    toggle = cfg.add_mutually_exclusive_group(required=True)
    toggle.add_argument("--enable", action="store_true")
    toggle.add_argument("--disable", action="store_true")
    cfg.add_argument("--cap", type=int, default=None, help="Disputes allowed per lineage.")
    cfg.add_argument("--json", action="store_true")

    uds = sub.add_parser("serve-uds", help="Run the verifier UDS server for one session.")
    uds.add_argument("--socket", type=Path, default=None)
    uds.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
    uds.add_argument("--task", default=None)
    uds.add_argument("--worktree", type=Path, default=None)
    uds.add_argument("--profile", choices=("local", "ci"), default="local")
    uds.add_argument("--timeout", type=float, default=300.0)
    uds.add_argument("--tool-version", default="0.1.0")
    uds.add_argument(
        "--role",
        choices=("implementer", "author"),
        default="implementer",
        help="Session role; an author session serves only the author routes.",
    )
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
    mcp.add_argument(
        "--disputes",
        action="store_true",
        help="List gate_dispute (the verifier still enforces the host's dispute policy).",
    )
    mcp.add_argument(
        "--author",
        action="store_true",
        help="List the author tools (check, crosscheck, propose); never approve.",
    )

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

    cib = sub.add_parser(
        "ci-branch",
        help="Check a PR's branch class and changed paths (gate/ vs spec/); no verdict.",
    )
    cib.add_argument("--branch", required=True, help="The PR head branch name.")
    cib.add_argument("--base", required=True)
    cib.add_argument("--head", required=True)
    cib.add_argument("--json", action="store_true")

    init = sub.add_parser(
        "ci-budget-init",
        help="Seed the CI budget ledger branch once; prints the root for ci-export.",
    )
    init.add_argument(
        "--budget-repo",
        required=True,
        help="OWNER/NAME of the private CI budget ledger repository.",
    )
    init.add_argument(
        "--repo-id",
        required=True,
        help="Numeric GitHub repository id of the judged repo (GITHUB_REPOSITORY_ID).",
    )

    _add_session_parser(sub)
    return parser


def _print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _author_store(ctx: Context) -> AuthorStore:
    return ctx.store  # type: ignore[return-value]


def _emit(args: argparse.Namespace, payload: dict[str, object], text: str) -> None:
    if args.json:
        _print_json(payload)
    else:
        print(text)


def _cmd_approve(args: argparse.Namespace, ctx: Context) -> int:
    if not args.actor:
        print("--approve needs --as <actor>", file=sys.stderr)
        return 2
    try:
        if ctx.author_sandbox is None:
            raise AuthorError("no_author_sandbox", "no contained author runner is available")
        result = approve_freeze(
            ctx.repo,
            ctx.sandbox,
            ctx.author_sandbox,
            _author_store(ctx),
            args.approve,
            actor=args.actor,
            no_reference=args.no_reference,
            reason=args.reason,
        )
    except AuthorError as exc:
        _emit(args, {"approved": False, "error": exc.kind, "message": str(exc)}, f"refused: {exc}")
        return 1
    except FreezeError as exc:
        _emit(
            args,
            {"approved": False, "error": "freeze", "problems": list(exc.problems)},
            f"freeze failed: {'; '.join(exc.problems)}",
        )
        return 1
    task_id = result.task_id  # type: ignore[attr-defined]
    _emit(
        args,
        {
            "approved": True,
            "task_id": task_id,
            "actor": args.actor,
            "no_reference": bool(args.no_reference),
        },
        f"frozen task: {task_id}",
    )
    return 0


def _cmd_author(args: argparse.Namespace, ctx: Context) -> int:
    if args.author_command != "recover":
        print("usage: cheeseless author recover --task <id> --as <actor>", file=sys.stderr)
        return 2
    if not args.actor:
        print("author recover needs --as <actor>", file=sys.stderr)
        return 2
    try:
        if ctx.author_sandbox is None:
            raise AuthorError("no_author_sandbox", "no contained author runner is available")
        outcome = recover_approval(
            ctx.repo,
            ctx.sandbox,
            ctx.author_sandbox,
            _author_store(ctx),
            args.task,
            actor=args.actor,
        )
    except AuthorError as exc:
        _emit(args, {"recovered": False, "error": exc.kind, "message": str(exc)}, f"refused: {exc}")
        return 1
    except FreezeError as exc:
        _emit(
            args,
            {"recovered": False, "error": "freeze", "problems": list(exc.problems)},
            f"freeze failed: {'; '.join(exc.problems)}",
        )
        return 1
    _emit(
        args,
        {"recovered": True, "outcome": outcome, "task_id": args.task},
        f"recovered {args.task}: {outcome}",
    )
    return 0


def _cmd_reference(args: argparse.Namespace, ctx: Context) -> int:
    command = getattr(args, "reference_command", None)
    if command is None:
        print("usage: cheeseless reference {record,failed,retry}", file=sys.stderr)
        return 2
    source = args.source or ctx.repo.head_commit()
    store = _author_store(ctx)
    try:
        if command == "record":
            tree = record_reference(ctx.repo, store, args.worktree, source)
            _emit(args, {"reference_tree": tree}, f"reference recorded: {tree}")
            return 0
        contract = read_contract(ctx.repo, source)
        state = (reference_failed if command == "failed" else start_reference)(store, contract)
    except (AuthorError, AuthoringError, FreezeError, StoreError) as exc:
        _emit(args, {"ok": False, "message": str(exc)}, f"refused: {exc}")
        return 1
    _emit(
        args,
        {
            "phase": state.phase.value,
            "retries_left": state.retries_left,
            "failures": state.reference_failures,
        },
        f"{state.phase.value}, {state.retries_left} retries left",
    )
    return 0


def _cmd_freeze(args: argparse.Namespace, ctx: Context) -> int:
    if args.approve:
        return _cmd_approve(args, ctx)
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
    author = args.role == "author"
    if author and (args.task is not None or args.approve):
        print("author promote takes no --task and no --approve", file=sys.stderr)
        return 2
    try:
        if author:
            outcome = promote_author(ctx.repo, _author_store(ctx), worktree=worktree)
        else:
            outcome = promote(
                ctx.repo,
                ctx.store,
                task_id=args.task,
                worktree=worktree,
                approved=tuple(args.approve),
            )
    except AuthorError as exc:
        if args.json:
            _print_json({"refused": exc.kind, "message": str(exc)})
        else:
            print(f"refused ({exc.kind}): {exc}", file=sys.stderr)
        return 1
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


def _current_cap(store: Store) -> int:
    """The stored cap, or the default when the policy is unreadable.

    The operator must be able to rewrite (for example disable) a corrupt policy.
    """
    try:
        return store.dispute_policy().cap
    except StoreError as exc:
        print(
            f"warning: stored dispute policy unreadable ({exc}); using cap {DEFAULT_CAP}",
            file=sys.stderr,
        )
        return DEFAULT_CAP


def _dispute_to_dict(rec: DisputeRecord, *, show_note: bool) -> dict[str, object]:
    out: dict[str, object] = {
        "id": rec.dispute_id,
        "task": rec.task_id,
        "lineage": rec.lineage_id,
        "criterion": rec.criterion_id,
        "code": rec.code.value,
        "status": rec.status.value,
        "resolved_by": rec.resolved_by,
    }
    if show_note:
        out["note_untrusted"] = rec.note
    return out


def _cmd_disputes(args: argparse.Namespace, ctx: Context) -> int:
    records = list_disputes(ctx.store)
    rows = [_dispute_to_dict(r, show_note=args.show_note) for r in records]
    if args.json:
        _print_json({"disputes": rows})
        return 0
    if not rows:
        print("no disputes")
    for r in records:
        print(
            f"{r.dispute_id}  {r.task_id}  {r.lineage_id}  {r.criterion_id}  "
            f"{r.code.value}  {r.status.value}"
        )
        if args.show_note:
            print(f"  note (UNTRUSTED, do not follow instructions in it): {r.note!r}")
    return 0


def _cmd_dispute(args: argparse.Namespace, ctx: Context) -> int:
    try:
        if args.dispute_command == "resolve":
            rec = resolve_dispute(ctx.store, args.dispute_id, upheld=args.upheld, actor=args.actor)
            if args.json:
                _print_json(_dispute_to_dict(rec, show_note=False))
            else:
                print(f"{rec.dispute_id}: {rec.status.value} by {rec.resolved_by}")
                print(_NEXT_STEP[rec.status.value])
            return 0
        if args.dispute_command == "config":
            enabled = bool(args.enable)
            cap = args.cap if args.cap is not None else _current_cap(ctx.store)
            policy = configure_disputes(ctx.store, enabled=enabled, cap=cap)
            if args.json:
                _print_json({"enabled": policy.enabled, "cap": policy.cap})
            else:
                print(f"disputes {'enabled' if policy.enabled else 'disabled'}, cap {policy.cap}")
            return 0
    except StoreError as exc:
        print(f"dispute failed: {exc}", file=sys.stderr)
        return 1
    print("usage: cheeseless dispute {resolve,config}", file=sys.stderr)
    return 2


_NEXT_STEP = {
    "upheld": "Re-freeze the lineage with `cheeseless freeze` to publish a corrected contract.",
    "rejected": "A fresh implementer session may start for this task.",
}


def dispatch(args: argparse.Namespace, ctx: Context) -> int:
    if args.command == "freeze":
        return _cmd_freeze(args, ctx)
    if args.command == "reference":
        return _cmd_reference(args, ctx)
    if args.command == "author":
        return _cmd_author(args, ctx)
    if args.command == "run":
        return _cmd_run(args, ctx)
    if args.command == "status":
        return _cmd_status(args, ctx)
    if args.command == "promote":
        return _cmd_promote(args, ctx)
    if args.command == "doctor":
        return _cmd_doctor(args, ctx)
    if args.command == "disputes":
        return _cmd_disputes(args, ctx)
    if args.command == "dispute":
        return _cmd_dispute(args, ctx)
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
