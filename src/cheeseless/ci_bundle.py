"""CI bundle: the secret material the CI judge needs, in one strictly validated blob.

The bundle carries the frozen task record (including the hidden-test selection), the
lineage id, the HMAC key, the CI run limit and (since v2) the pinned budget ledger
``{repo, branch, root}``. A bundle without a ledger is refused. It is a secret: it is
written 0600 outside the repository and never printed. Decoding fails closed on any
schema, version, key-length or integrity problem. The task record reuses the store codec,
so the freeze digest is re-verified against the task id.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from cheeseless.adapters.outbound.store import codec
from cheeseless.domain.budget_ledger import LedgerInvalid, check_branch, ledger_repo
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.ids import git_oid
from cheeseless.domain.result import MIN_KEY_BYTES
from cheeseless.ports.store import StoreError

BUNDLE_VERSION = 2
MAX_CI_LIMIT = 1000
MAX_BUNDLE_BYTES = 1 << 20
_FIELDS = frozenset({"version", "task", "lineage", "key", "ci_limit", "budget", "mac"})
_BUDGET_FIELDS = frozenset({"repo", "branch", "root"})


class BundleError(Exception):
    """The bundle is malformed, tampered with or unusable. Callers fail closed."""


@dataclass(frozen=True, slots=True)
class BudgetLedgerRef:
    """The pinned CI budget ledger: repository, branch and seed (root) commit."""

    repo: str
    branch: str
    root: str


@dataclass(frozen=True, slots=True)
class Bundle:
    frozen: FrozenTask
    seed: bytes
    key: bytes
    ci_limit: int
    budget: BudgetLedgerRef


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _mac(key: bytes, body: dict[str, Any]) -> str:
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def _check_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CI_LIMIT:
        raise BundleError("ci limit is out of range")
    return limit


def _check_budget(budget: object, lineage: str) -> BudgetLedgerRef:
    """Strict ``{repo, branch, root}``; the branch must belong to the task's lineage."""
    if isinstance(budget, BudgetLedgerRef):
        fields: dict[str, object] = {
            "repo": budget.repo,
            "branch": budget.branch,
            "root": budget.root,
        }
    elif isinstance(budget, dict) and set(budget) == _BUDGET_FIELDS:
        fields = budget
    else:
        raise BundleError("bundle budget ledger is missing or has the wrong shape")
    repo, branch, root = fields["repo"], fields["branch"], fields["root"]
    if not isinstance(branch, str) or not isinstance(root, str):
        raise BundleError("bundle budget ledger is invalid")
    try:
        ref = BudgetLedgerRef(repo=ledger_repo(repo), branch=branch, root=git_oid(root))
        check_branch(ref.branch, lineage)
    except (LedgerInvalid, ValueError) as exc:
        raise BundleError("bundle budget ledger is invalid") from exc
    return ref


def _budget_body(ref: BudgetLedgerRef) -> dict[str, str]:
    return {"repo": ref.repo, "branch": ref.branch, "root": ref.root}


def encode_bundle(
    frozen: FrozenTask, seed: bytes, key: bytes, *, ci_limit: int, budget: BudgetLedgerRef
) -> bytes:
    if len(key) < MIN_KEY_BYTES:
        raise BundleError(f"HMAC key must be at least {MIN_KEY_BYTES} bytes")
    body: dict[str, Any] = {
        "version": BUNDLE_VERSION,
        "task": json.loads(codec.encode_task(frozen, seed)),
        "lineage": frozen.task.lineage_id,
        "key": base64.b64encode(key).decode("ascii"),
        "ci_limit": _check_limit(ci_limit),
        "budget": _budget_body(_check_budget(budget, frozen.task.lineage_id)),
    }
    return _canonical({**body, "mac": _mac(key, body)})


def _key_of(data: dict[str, Any]) -> bytes:
    raw = data.get("key")
    if not isinstance(raw, str):
        raise BundleError("bundle key is missing")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BundleError("bundle key is not base64") from exc
    if len(key) < MIN_KEY_BYTES:
        raise BundleError(f"bundle key must be at least {MIN_KEY_BYTES} bytes")
    return key


def decode_bundle(raw: bytes) -> Bundle:
    if len(raw) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle is too large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError("bundle is not valid JSON") from exc
    if not isinstance(data, dict):
        raise BundleError("bundle has the wrong shape")
    version = data.get("version")
    if version != BUNDLE_VERSION or isinstance(version, bool):
        # v1 bundles carry no budget ledger; they are refused, never upgraded.
        raise BundleError("unsupported bundle version (re-export with this tool)")
    if set(data) != _FIELDS:
        raise BundleError("bundle has the wrong shape")
    key = _key_of(data)
    limit = _check_limit(data["ci_limit"])
    body = {k: v for k, v in data.items() if k != "mac"}
    tag = data["mac"]
    if not isinstance(tag, str) or not hmac.compare_digest(tag, _mac(key, body)):
        raise BundleError("bundle integrity check failed")
    task = data["task"]
    if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
        raise BundleError("bundle task record is invalid")
    try:
        frozen, seed = codec.decode_task(_canonical(task), task["task_id"])
    except StoreError as exc:
        raise BundleError(f"bundle task record is invalid: {exc}") from exc
    if data["lineage"] != frozen.task.lineage_id:
        raise BundleError("bundle lineage does not match its task")
    budget = _check_budget(data["budget"], frozen.task.lineage_id)
    return Bundle(frozen=frozen, seed=seed, key=key, ci_limit=limit, budget=budget)


def decode_bundle_b64(text: str) -> Bundle:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BundleError("bundle is not base64") from exc
    return decode_bundle(raw)


__all__ = [
    "BUNDLE_VERSION",
    "BudgetLedgerRef",
    "Bundle",
    "BundleError",
    "decode_bundle",
    "decode_bundle_b64",
    "encode_bundle",
]
