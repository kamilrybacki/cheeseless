"""Composition root for the CI judge commands (``ci-export`` and ``ci-judge``).

Kept apart from ``bootstrap`` so that module stays small. Exit codes: 0 PASS,
1 FAIL or BLOCKED, 2 usage or infrastructure error (including an invalid bundle),
3 FREEZE_DRIFT. Only the public verdict form goes to stdout; the closed ``reason`` and
all diagnostics go to stderr and never carry hidden material.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.ci_judge import FreezeDrift, judge
from cheeseless.bootstrap import _ensure_outside, _private_dir, _state_home
from cheeseless.ci_bundle import (
    Bundle,
    BundleError,
    decode_bundle,
    decode_bundle_b64,
    encode_bundle,
)
from cheeseless.domain.budget import Pool
from cheeseless.domain.status import Status

BUNDLE_ENV = "CHEESELESS_CI_BUNDLE"
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
    frozen, seed = store.load_task(task_id)
    limit = store.budget(frozen.task.lineage_id, Pool.CI).limit
    raw = encode_bundle(frozen, seed, store.hmac_key(), ci_limit=limit)
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


def ci_judge(args: argparse.Namespace) -> int:
    for name, value in (("--base", args.base), ("--head", args.head)):
        if not _SHA.fullmatch(value):
            _err(f"ci-judge: {name} must be a full 40-character lowercase commit SHA")
            return EXIT_USAGE
    try:
        bundle = _load_bundle(args.bundle_file)
    except BundleError as exc:
        _err(f"ci-judge refused: {exc}")
        return EXIT_USAGE
    state = _private_dir("cheeseless-ci-")
    try:
        repo = GitRepository.discover(Path.cwd())
        store = FileStore(state / "store")
        verdict = judge(
            repo,
            store,
            PytestRunner(),
            BwrapSandbox(),
            bundle.frozen,
            bundle.key,
            base_commit=args.base,
            head_commit=args.head,
            timeout=args.timeout,
        )
    except FreezeDrift:
        _err("FREEZE_DRIFT: the default branch acceptance/ tree differs from the frozen task")
        return EXIT_FREEZE_DRIFT
    except Exception as exc:  # noqa: BLE001 - never leak details; fail closed
        _err(f"ci-judge failed: {type(exc).__name__}")
        return EXIT_USAGE
    finally:
        shutil.rmtree(state, ignore_errors=True)
    _err(f"reason: {verdict.reason}")
    _emit(verdict.public(), args.json)
    return EXIT_PASS if verdict.status is Status.PASS else EXIT_FAIL


def run(args: argparse.Namespace) -> int:
    return ci_export(args) if args.command == "ci-export" else ci_judge(args)


__all__ = ["BUNDLE_ENV", "ci_export", "ci_judge", "run"]
