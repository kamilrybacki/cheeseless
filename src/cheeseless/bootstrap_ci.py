"""Composition root for the CI judge commands (``ci-export`` and ``ci-judge``).

Kept apart from ``bootstrap`` so that module stays small. Exit codes: 0 PASS,
1 FAIL or BLOCKED, 2 usage or infrastructure error (including an invalid bundle),
3 FREEZE_DRIFT. Only the public verdict form goes to stdout; the closed ``reason`` and
all diagnostics go to stderr and never carry hidden material.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import secrets
import sys
from pathlib import Path

from cheeseless.adapters.outbound.ci.github_ledger import (
    LedgerMalformed,
    LedgerSeeder,
    LedgerTransport,
    TransportError,
    UrllibLedgerSeeder,
    UrllibLedgerTransport,
)
from cheeseless.adapters.outbound.ci.ledger_counter import LedgerCounter
from cheeseless.adapters.outbound.ci.ledger_seed import SeedError, seed_ledger
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.ci_judge import CiVerdict, FreezeDrift, JudgeReason, judge
from cheeseless.bootstrap import _ensure_outside, _state_home
from cheeseless.ci_bundle import (
    BudgetLedgerRef,
    Bundle,
    BundleError,
    decode_bundle,
    decode_bundle_b64,
    encode_bundle,
)
from cheeseless.domain.budget import Pool
from cheeseless.domain.budget_ledger import (
    LedgerIdentity,
    branch_for,
    ledger_repo,
    new_claim,
    run_key,
)
from cheeseless.domain.status import Status

BUNDLE_ENV = "CHEESELESS_CI_BUNDLE"
BUDGET_TOKEN_ENV = "CHEESELESS_BUDGET_TOKEN"  # noqa: S105 - an env var name
# Set by the Actions runner for the judged repository; never taken from the PR.
EVENT_ENV = ("GITHUB_REPOSITORY_ID", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_FREEZE_DRIFT = 3
_SHA = re.compile(r"[0-9a-f]{40}")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def ci_export(args: argparse.Namespace) -> int:
    out: Path = args.out
    repo = GitRepository.discover(Path.cwd())
    try:
        _ensure_outside(out.parent / out.name, repo, "the CI bundle")
    except RuntimeError as exc:
        _err(f"ci-export refused: {exc}")
        return EXIT_USAGE
    store = FileStore(store_root(repo.common_dir(), _state_home(args.state_dir)))
    task_id = store.active_task_id()
    if task_id is None:
        _err("ci-export failed: no frozen task")
        return EXIT_USAGE
    if store.session_disputed(task_id):
        _err("ci-export refused: the session ended in a dispute")
        return EXIT_USAGE
    frozen, seed = store.load_task(task_id)
    limit = store.budget(frozen.task.lineage_id, Pool.CI).limit
    ledger = BudgetLedgerRef(
        repo=args.budget_repo, branch=branch_for(frozen.task.lineage_id), root=args.budget_root
    )
    try:
        raw = encode_bundle(frozen, seed, store.hmac_key(), ci_limit=limit, budget=ledger)
    except BundleError as exc:
        _err(f"ci-export refused: {exc}")
        return EXIT_USAGE
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(out, flags, 0o600)
    except OSError as exc:
        _err(f"ci-export failed: cannot create the bundle file: {exc.strerror}")
        return EXIT_USAGE
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    print(f"wrote CI bundle for task {task_id} (not printed; keep it secret)")
    return EXIT_PASS


def _load_bundle(bundle_file: Path | None) -> Bundle:
    if bundle_file is not None:
        try:
            return decode_bundle(bundle_file.read_bytes())
        except OSError as exc:
            raise BundleError(f"cannot read the bundle file: {exc.strerror}") from exc
    text = os.environ.get(BUNDLE_ENV, "")
    if not text:
        raise BundleError(f"no bundle: set {BUNDLE_ENV} or pass --bundle-file")
    return decode_bundle_b64(text)


def _emit(verdict_public: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(verdict_public, indent=2, sort_keys=True))
        return
    print(verdict_public["status"])
    for crit in verdict_public["criteria"]:  # type: ignore[attr-defined]
        print(f"  {crit['id']}: {crit['status']}")


def make_ledger_transport(repo: str, token: str) -> LedgerTransport:
    """The real ledger transport (tests substitute an in-memory one)."""
    return UrllibLedgerTransport(repo, token)


def _make_counter(bundle: Bundle, token: str) -> LedgerCounter:
    """The CI budget counter pinned by the bundle. ``ValueError`` on any bad input."""
    if not token:
        raise ValueError(f"no budget token: set {BUDGET_TOKEN_ENV}")
    missing = [name for name in EVENT_ENV if not os.environ.get(name)]
    if missing:
        raise ValueError(f"missing workflow event variables: {', '.join(missing)}")
    repo_id, run_id, attempt = (os.environ[name] for name in EVENT_ENV)
    identity = LedgerIdentity(repo_id=repo_id, lineage=bundle.frozen.task.lineage_id)
    key = run_key(run_id, attempt)
    return LedgerCounter(
        make_ledger_transport(bundle.budget.repo, token),
        store_key=bundle.key,
        identity=identity,
        branch=bundle.budget.branch,
        root=bundle.budget.root,
        limit=bundle.ci_limit,
        run_key=key,
        claim=new_claim(secrets.token_bytes(16)),
    )


def _confirmed(verdict: CiVerdict, counter: LedgerCounter) -> CiVerdict:
    """Every charged verdict stands only while its charge is still on the chain (fail closed).

    A lost charge is an incident whatever the outcome, so a FAIL is re-checked too.
    """
    if not counter.charged and verdict.status is not Status.PASS:
        return verdict
    if counter.confirm_still_on_chain():
        return verdict
    return dataclasses.replace(verdict, status=Status.BLOCKED, reason=JudgeReason.INFRA_ERROR)


def ci_judge(args: argparse.Namespace) -> int:
    # The budget token leaves the environment first, before anything can spawn a process.
    token = os.environ.pop(BUDGET_TOKEN_ENV, "")
    for name, value in (("--base", args.base), ("--head", args.head)):
        if not _SHA.fullmatch(value):
            _err(f"ci-judge: {name} must be a full 40-character lowercase commit SHA")
            return EXIT_USAGE
    try:
        bundle = _load_bundle(args.bundle_file)
        counter = _make_counter(bundle, token)
    except BundleError as exc:
        _err(f"ci-judge refused: {exc}")
        return EXIT_USAGE
    except ValueError as exc:  # messages never include the token
        _err(f"ci-judge refused: invalid budget ledger configuration: {exc}")
        return EXIT_USAGE
    finally:
        del token
    try:
        repo = GitRepository.discover(Path.cwd())
        verdict = judge(
            repo,
            PytestRunner(),
            BwrapSandbox(),
            bundle.frozen,
            bundle.key,
            counter=counter,
            base_commit=args.base,
            head_commit=args.head,
            timeout=args.timeout,
        )
        verdict = _confirmed(verdict, counter)
    except FreezeDrift:
        _err("FREEZE_DRIFT: the default branch acceptance/ tree differs from the frozen task")
        return EXIT_FREEZE_DRIFT
    except Exception as exc:  # noqa: BLE001 - never leak details; fail closed
        _err(f"ci-judge failed: {type(exc).__name__}")
        return EXIT_USAGE
    finally:
        for alert in counter.alerts:
            _err(alert)
    if counter.observed is not None:
        _err(f"budget used {counter.observed} of {bundle.ci_limit}")
    _err(f"reason: {verdict.reason}")
    _emit(verdict.public(), args.json)
    return EXIT_PASS if verdict.status is Status.PASS else EXIT_FAIL


def make_ledger_seeder(repo: str, token: str) -> LedgerSeeder:
    return UrllibLedgerSeeder(repo, token)


def _seed(args: argparse.Namespace, token: str) -> int:
    if not token:
        _err(f"ci-budget-init failed: set {BUDGET_TOKEN_ENV} to a token for the ledger repo")
        return EXIT_USAGE
    repo = GitRepository.discover(Path.cwd())
    store = FileStore(store_root(repo.common_dir(), _state_home(args.state_dir)))
    task_id = store.active_task_id()
    if task_id is None:
        _err("ci-budget-init failed: no frozen task")
        return EXIT_USAGE
    lineage = store.load_task(task_id)[0].task.lineage_id
    try:
        # Both identifiers are checked here, before the seeder sends any request.
        repo_name = ledger_repo(args.budget_repo)
        identity = LedgerIdentity(repo_id=args.repo_id, lineage=lineage)
        result = seed_ledger(
            make_ledger_seeder(repo_name, token),
            store_key=store.hmac_key(),
            identity=identity,
            branch=branch_for(lineage),
        )
    except (SeedError, ValueError) as exc:  # messages never include the key or token
        _err(f"ci-budget-init refused: {exc}")
        return EXIT_USAGE
    except LedgerMalformed:
        _err("ci-budget-init failed: the ledger returned a malformed response")
        return EXIT_USAGE
    except TransportError as exc:  # fixed messages: an HTTP status or an exception name
        _err(f"ci-budget-init failed: cannot reach the ledger ({exc})")
        return EXIT_USAGE
    state = "seeded" if result.created else "already seeded"
    _err(f"{state} {branch_for(lineage)} in {repo_name}")
    _err(f"next: cheeseless ci-export --budget-repo {repo_name} --budget-root {result.root} ...")
    print(result.root)
    return EXIT_PASS


def ci_budget_init(args: argparse.Namespace) -> int:
    """Seed the CI budget ledger for the frozen task; prints only the root commit SHA."""
    # The budget token leaves the environment first, before anything can spawn a process.
    token = os.environ.pop(BUDGET_TOKEN_ENV, "")
    try:
        return _seed(args, token)
    except Exception as exc:  # noqa: BLE001 - local setup (repository, store); never a trace
        _err(f"ci-budget-init failed: {type(exc).__name__}")
        return EXIT_USAGE
    finally:
        del token


def run(args: argparse.Namespace) -> int:
    commands = {"ci-export": ci_export, "ci-judge": ci_judge, "ci-budget-init": ci_budget_init}
    return commands[args.command](args)


__all__ = [
    "BUDGET_TOKEN_ENV",
    "BUNDLE_ENV",
    "ci_budget_init",
    "ci_export",
    "ci_judge",
    "make_ledger_seeder",
    "make_ledger_transport",
    "run",
]
