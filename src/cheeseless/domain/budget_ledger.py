"""CI budget ledger codec (P10-27): canonical records, MACs and chain validation. Pure.

One ledger branch per lineage holds a seed commit, then one commit per charged run. The
transport hands over normalized commits; everything that decides whether the chain is
trustworthy lives here, so an adapter cannot weaken it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from cheeseless.domain.ids import git_oid, lineage_id, sha256_hex

RECORD_FILE = "record.json"
BRANCH_PREFIX = "cheeseless/budget/"
POOL = "ci"
VERSION = 1
SEED, RUN = "seed", "run"
MIN_STORE_KEY = 32
CLAIM_BYTES = 16

_KEY_LABEL = b"cheeseless-budget-v1"
_SEED_FIELDS = frozenset({"v", "kind", "repo_id", "lineage", "pool", "mac"})
_RUN_FIELDS = _SEED_FIELDS | {"seq", "parent", "run_key", "claim"}
_REPO_ID = re.compile(r"[1-9][0-9]{0,19}")
_RUN_KEY = re.compile(r"[1-9][0-9]{0,19}/[1-9][0-9]{0,9}")
_CLAIM = re.compile(r"[0-9a-f]{32}")
# owner/name, GitHub charset; neither part may start with "." or "-", so "." and ".." fail.
_LEDGER_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,38}/(?![.-])[A-Za-z0-9._-]{1,100}")


class LedgerInvalid(ValueError):
    """The ledger chain or a record is not trustworthy. Fail closed; never re-initialise."""


@dataclass(frozen=True, slots=True)
class LedgerIdentity:
    """Expected identity: judged repository id (from the event) and the authenticated lineage."""

    repo_id: str
    lineage: str

    def __post_init__(self) -> None:
        if not isinstance(self.repo_id, str) or _REPO_ID.fullmatch(self.repo_id) is None:
            raise ValueError(f"invalid repository id: {self.repo_id!r}")
        lineage_id(self.lineage)


@dataclass(frozen=True, slots=True)
class LedgerCommit:
    """A commit as the transport normalizes it. ``files`` is the full tree (name -> bytes)."""

    sha: str
    parents: tuple[str, ...]
    message: bytes
    files: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunEntry:
    sha: str
    seq: int
    run_key: str
    claim: str


@dataclass(frozen=True, slots=True)
class ChainState:
    """A validated chain: tip SHA and the run records in ``seq`` order."""

    tip: str
    runs: tuple[RunEntry, ...]

    @property
    def count(self) -> int:
        return len(self.runs)

    def find(self, key: str) -> RunEntry | None:
        return next((r for r in self.runs if r.run_key == key), None)

    def contains(self, sha: str) -> bool:
        return any(r.sha == sha for r in self.runs)


def derive_key(store_key: bytes) -> bytes:
    """``k_b = HMAC(store_key, "cheeseless-budget-v1")``: unchanged by a re-freeze."""
    if len(store_key) < MIN_STORE_KEY:
        raise ValueError("store key too short")
    return hmac.new(store_key, _KEY_LABEL, hashlib.sha256).digest()


def branch_for(lineage: str) -> str:
    return BRANCH_PREFIX + lineage_id(lineage)


def check_branch(branch: str, lineage: str) -> None:
    if branch != branch_for(lineage):
        raise LedgerInvalid("ledger branch does not match the lineage")


def ledger_repo(value: object) -> str:
    if not isinstance(value, str) or _LEDGER_REPO.fullmatch(value) is None:
        raise ValueError(f"invalid ledger repository: {value!r}")
    return value


def run_key(run_id: str, run_attempt: str) -> str:
    return _check(_RUN_KEY, f"{run_id}/{run_attempt}", "run key")


def new_claim(random_bytes: bytes) -> str:
    """128-bit claim from caller-supplied randomness (the domain draws none itself)."""
    if len(random_bytes) != CLAIM_BYTES:
        raise ValueError("a claim needs exactly 16 random bytes")
    return random_bytes.hex()


def _mac(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def _seed_mac(key: bytes, ident: LedgerIdentity) -> str:
    return _mac(key, f"budget-seed-v1|{ident.repo_id}|{ident.lineage}|{POOL}")


def _run_mac(
    key: bytes, ident: LedgerIdentity, seq: int, parent: str, rkey: str, claim: str
) -> str:
    return _mac(
        key, f"budget-run-v1|{ident.repo_id}|{ident.lineage}|{POOL}|{seq}|{parent}|{rkey}|{claim}"
    )


def _canonical(obj: Mapping[str, object]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seed_record(key: bytes, ident: LedgerIdentity) -> bytes:
    obj: dict[str, object] = {
        "v": VERSION,
        "kind": SEED,
        "repo_id": ident.repo_id,
        "lineage": ident.lineage,
    }
    return _canonical({**obj, "pool": POOL, "mac": _seed_mac(key, ident)})


def run_record(
    key: bytes, ident: LedgerIdentity, *, seq: int, parent: str, run_key: str, claim: str
) -> bytes:
    _check_int(seq, "seq", minimum=1)
    _check(_RUN_KEY, run_key, "run key")
    _check(_CLAIM, claim, "claim")
    try:
        git_oid(parent)
    except ValueError:
        raise ValueError("invalid parent") from None
    obj: dict[str, object] = {
        "v": VERSION,
        "kind": RUN,
        "repo_id": ident.repo_id,
        "lineage": ident.lineage,
        "pool": POOL,
        "seq": seq,
        "parent": parent,
        "run_key": run_key,
        "claim": claim,
        "mac": _run_mac(key, ident, seq, parent, run_key, claim),
    }
    return _canonical(obj)


def _check(pattern: re.Pattern[str], value: object, what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {what}")
    return value


def _check_int(value: object, what: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {what}")
    return value


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in pairs:
        if k in out:
            raise LedgerInvalid("duplicate key in ledger record")
        out[k] = v
    return out


def _reject_constant(token: str) -> object:
    raise LedgerInvalid(f"non-finite number in ledger record: {token}")


def _decode(commit: LedgerCommit) -> dict[str, object]:
    """Strict wire format: only ``record.json``, message == blob, canonical JSON object, v1."""
    if set(commit.files) != {RECORD_FILE}:
        raise LedgerInvalid("ledger commit tree must hold exactly record.json")
    raw = commit.files[RECORD_FILE]
    if commit.message != raw:
        raise LedgerInvalid("ledger commit message differs from record.json")
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LedgerInvalid("ledger record is not JSON") from None
    if not isinstance(obj, dict):
        raise LedgerInvalid("ledger record is not a JSON object")
    if type(obj.get("v")) is not int or obj["v"] != VERSION:
        raise LedgerInvalid("unsupported ledger record version")
    if _canonical(obj) != raw:
        raise LedgerInvalid("ledger record is not canonical JSON")
    return obj


def _check_identity(obj: Mapping[str, object], ident: LedgerIdentity) -> None:
    if (
        obj.get("repo_id") != ident.repo_id
        or obj.get("lineage") != ident.lineage
        or obj.get("pool") != POOL
    ):
        raise LedgerInvalid("ledger record names another repository, lineage or pool")


def _check_seed(commit: LedgerCommit, key: bytes, ident: LedgerIdentity) -> None:
    if commit.parents:
        raise LedgerInvalid("ledger seed must have no parent")
    obj = _decode(commit)
    if obj.get("kind") != SEED or set(obj) != _SEED_FIELDS:
        raise LedgerInvalid("ledger root is not a seed record")
    _check_identity(obj, ident)
    mac = obj["mac"]
    if not isinstance(mac, str) or not hmac.compare_digest(mac, _seed_mac(key, ident)):
        raise LedgerInvalid("bad ledger seed MAC")


def _check_run(
    commit: LedgerCommit, parent: str, seq: int, key: bytes, ident: LedgerIdentity
) -> RunEntry:
    if commit.parents != (parent,):
        raise LedgerInvalid("ledger run commit must have exactly the previous commit as parent")
    obj = _decode(commit)
    if obj.get("kind") != RUN or set(obj) != _RUN_FIELDS:
        raise LedgerInvalid("ledger commit is not a run record")
    _check_identity(obj, ident)
    try:
        got_seq = _check_int(obj["seq"], "seq", minimum=1)
        got_parent = git_oid(obj["parent"])
        got_key = _check(_RUN_KEY, obj["run_key"], "run key")
        claim = _check(_CLAIM, obj["claim"], "claim")
        mac = sha256_hex(obj["mac"], "mac")
    except ValueError:
        raise LedgerInvalid("malformed ledger run record") from None
    if got_seq != seq:
        raise LedgerInvalid("ledger seq is not contiguous")
    if got_parent != parent:
        raise LedgerInvalid("ledger record parent differs from the actual parent")
    if not hmac.compare_digest(mac, _run_mac(key, ident, got_seq, got_parent, got_key, claim)):
        raise LedgerInvalid("bad ledger run MAC")
    return RunEntry(sha=commit.sha, seq=got_seq, run_key=got_key, claim=claim)


def validate_chain(
    commits: Sequence[LedgerCommit],
    *,
    key: bytes,
    identity: LedgerIdentity,
    root: str,
    limit: int,
) -> ChainState:
    """Validate a tip-first chain down to the pinned root. Any defect raises ``LedgerInvalid``."""
    _check_int(limit, "limit", minimum=0)
    if not commits:
        raise LedgerInvalid("ledger branch is empty")
    if len(commits) > limit + 1:
        raise LedgerInvalid("ledger chain is longer than the limit allows")
    oldest_first = list(reversed(commits))
    if oldest_first[0].sha != root:
        raise LedgerInvalid("ledger root is not the pinned root")
    _check_seed(oldest_first[0], key, identity)
    runs: list[RunEntry] = []
    seen: set[str] = set()
    for seq, (prev, commit) in enumerate(
        zip(oldest_first, oldest_first[1:], strict=False), start=1
    ):
        entry = _check_run(commit, prev.sha, seq, key, identity)
        if entry.run_key in seen:
            raise LedgerInvalid("ledger run key appears twice")
        seen.add(entry.run_key)
        runs.append(entry)
    return ChainState(tip=commits[0].sha, runs=tuple(runs))


__all__ = [
    "BRANCH_PREFIX",
    "CLAIM_BYTES",
    "POOL",
    "RECORD_FILE",
    "ChainState",
    "LedgerCommit",
    "LedgerIdentity",
    "LedgerInvalid",
    "RunEntry",
    "branch_for",
    "check_branch",
    "derive_key",
    "ledger_repo",
    "new_claim",
    "run_key",
    "run_record",
    "seed_record",
    "validate_chain",
]
