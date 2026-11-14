"""CI budget ledger codec: canonical records, MACs and chain validation (P10-27)."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from cheeseless.domain import budget_ledger as bl
from cheeseless.domain.budget_ledger import LedgerCommit, LedgerIdentity, LedgerInvalid

STORE_KEY = b"k" * 32
KB = bl.derive_key(STORE_KEY)
IDENT = LedgerIdentity(repo_id="123456789", lineage="toy-task")
CLAIM = "0123456789abcdef0123456789abcdef"
OTHER_CLAIM = "fedcba9876543210fedcba9876543210"


def _sha(parents: tuple[str, ...], message: bytes) -> str:
    return hashlib.sha256(b"|".join(p.encode() for p in parents) + b"#" + message).hexdigest()[:40]


def _commit(
    message: bytes, parents: tuple[str, ...] = (), files: dict[str, bytes] | None = None
) -> LedgerCommit:
    return LedgerCommit(
        sha=_sha(parents, message),
        parents=parents,
        message=message,
        files={bl.RECORD_FILE: message} if files is None else files,
    )


def _seed(ident: LedgerIdentity = IDENT, key: bytes = KB) -> LedgerCommit:
    return _commit(bl.seed_record(key, ident))


def _chain(runs: int, *, ident: LedgerIdentity = IDENT) -> list[LedgerCommit]:
    """Tip-first chain: ``runs`` run records on top of a seed."""
    commits = [_seed(ident)]
    for seq in range(1, runs + 1):
        parent = commits[0].sha
        raw = bl.run_record(
            KB, ident, seq=seq, parent=parent, run_key=f"{1000 + seq}/1", claim=CLAIM
        )
        commits.insert(0, _commit(raw, (parent,)))
    return commits


def _validate(
    commits: list[LedgerCommit], *, root: str | None = None, limit: int = 5
) -> bl.ChainState:
    root = commits[-1].sha if root is None and commits else (root or "0" * 40)
    return bl.validate_chain(commits, key=KB, identity=IDENT, root=root, limit=limit)


def _canon(obj: dict[str, object]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _run_fields(
    seq: int, parent: str, run_key: str = "1001/1", claim: str = CLAIM
) -> dict[str, object]:
    return json.loads(
        bl.run_record(KB, IDENT, seq=seq, parent=parent, run_key=run_key, claim=claim)
    )


# --- key, MAC formulas and canonical encoding -------------------------------------------------


def test_derive_key_is_the_documented_hmac() -> None:
    assert KB == hmac.new(STORE_KEY, b"cheeseless-budget-v1", hashlib.sha256).digest()
    assert KB != STORE_KEY


def test_derive_key_rejects_a_short_store_key() -> None:
    with pytest.raises(ValueError):
        bl.derive_key(b"short")


def test_seed_record_is_canonical_with_the_documented_mac() -> None:
    raw = bl.seed_record(KB, IDENT)
    mac = hmac.new(KB, b"budget-seed-v1|123456789|toy-task|ci", hashlib.sha256).hexdigest()
    expected = {
        "v": 1,
        "kind": "seed",
        "repo_id": "123456789",
        "lineage": "toy-task",
        "pool": "ci",
        "mac": mac,
    }
    assert raw == _canon(expected)


def test_run_record_is_canonical_with_the_documented_mac() -> None:
    parent = "a" * 40
    raw = bl.run_record(KB, IDENT, seq=3, parent=parent, run_key="1001/2", claim=CLAIM)
    msg = f"budget-run-v1|123456789|toy-task|ci|3|{parent}|1001/2|{CLAIM}".encode()
    mac = hmac.new(KB, msg, hashlib.sha256).hexdigest()
    expected = {
        "v": 1,
        "kind": "run",
        "repo_id": "123456789",
        "lineage": "toy-task",
        "pool": "ci",
        "seq": 3,
        "parent": parent,
        "run_key": "1001/2",
        "claim": CLAIM,
        "mac": mac,
    }
    assert raw == _canon(expected)


@pytest.mark.parametrize(
    ("kwargs", "what"),
    [
        ({"seq": 0}, "seq"),
        ({"parent": "nothex"}, "parent"),
        ({"run_key": "1001"}, "run key"),
        ({"run_key": "1001/1/2"}, "run key"),
        ({"claim": "short"}, "claim"),
    ],
)
def test_run_record_rejects_malformed_inputs(kwargs: dict[str, object], what: str) -> None:
    args: dict[str, object] = {
        "seq": 1,
        "parent": "a" * 40,
        "run_key": "1001/1",
        "claim": CLAIM,
        **kwargs,
    }
    with pytest.raises(ValueError, match=what):
        bl.run_record(KB, IDENT, **args)  # type: ignore[arg-type]


@pytest.mark.parametrize("repo_id", ["", "0", "12a", "-1", "1" * 21])
def test_identity_rejects_a_bad_repo_id(repo_id: str) -> None:
    with pytest.raises(ValueError):
        LedgerIdentity(repo_id=repo_id, lineage="toy-task")


def test_identity_rejects_a_bad_lineage() -> None:
    with pytest.raises(ValueError):
        LedgerIdentity(repo_id="1", lineage="Bad Lineage")


def test_run_key_and_claim_helpers() -> None:
    assert bl.run_key("1001", "2") == "1001/2"
    with pytest.raises(ValueError):
        bl.run_key("x", "1")
    assert len(bl.new_claim(bytes(16))) == 32
    with pytest.raises(ValueError):
        bl.new_claim(b"short")


# --- branch binding and ledger repository -----------------------------------------------------


def test_branch_is_bound_to_the_lineage() -> None:
    assert bl.branch_for("toy-task") == "cheeseless/budget/toy-task"
    bl.check_branch("cheeseless/budget/toy-task", "toy-task")
    for bad in (
        "cheeseless/budget/other",
        "cheeseless/budget/toy-task/x",
        "refs/heads/cheeseless/budget/toy-task",
    ):
        with pytest.raises(LedgerInvalid):
            bl.check_branch(bad, "toy-task")


@pytest.mark.parametrize("lineage", ["toy..task", "toy-task.", "toy-task.lock", "toy.lock"])
def test_a_lineage_that_is_not_a_valid_git_ref_has_no_branch(lineage: str) -> None:
    with pytest.raises(LedgerInvalid, match="git ref"):
        bl.branch_for(lineage)


@pytest.mark.parametrize("repo", ["example-owner/cheeseless-ledger", "a/b", "Owner.1/repo_name.x"])
def test_ledger_repo_accepts_owner_slash_name(repo: str) -> None:
    assert bl.ledger_repo(repo) == repo


@pytest.mark.parametrize(
    "repo", ["", "noslash", "a/b/c", "../x", "a/..", "a/.", "-a/b", "a b/c", "a/b?x=1", 5]
)
def test_ledger_repo_rejects_anything_else(repo: object) -> None:
    with pytest.raises(ValueError):
        bl.ledger_repo(repo)


# --- valid chains -----------------------------------------------------------------------------


def test_seed_only_chain_counts_zero() -> None:
    chain = _chain(0)
    state = _validate(chain)
    assert state.count == 0
    assert state.tip == chain[0].sha
    assert state.runs == ()
    assert state.find("1001/1") is None


def test_chain_counts_run_records_in_seq_order() -> None:
    chain = _chain(3)
    state = _validate(chain)
    assert state.count == 3
    assert state.tip == chain[0].sha
    assert [r.seq for r in state.runs] == [1, 2, 3]
    entry = state.find("1002/1")
    assert (
        entry is not None and entry.seq == 2 and entry.claim == CLAIM and entry.sha == chain[1].sha
    )


def test_a_full_chain_of_limit_runs_is_valid() -> None:
    assert _validate(_chain(5), limit=5).count == 5


# --- tampering --------------------------------------------------------------------------------


def _resign_tip(chain: list[LedgerCommit], **fields: object) -> list[LedgerCommit]:
    """Replace the tip with a record built from ``fields`` (raw, unsigned unless mac given)."""
    obj = {**json.loads(chain[0].message), **fields}
    raw = _canon(obj)
    return [_commit(raw, chain[0].parents), *chain[1:]]


def _tip_with(raw: bytes, chain: list[LedgerCommit]) -> list[LedgerCommit]:
    return [_commit(raw, chain[0].parents), *chain[1:]]


def _signed_tip(
    chain: list[LedgerCommit], ident: LedgerIdentity, **kw: object
) -> list[LedgerCommit]:
    args: dict[str, object] = {
        "seq": 1,
        "parent": chain[1].sha,
        "run_key": "1001/1",
        "claim": CLAIM,
        **kw,
    }
    return _tip_with(bl.run_record(KB, ident, **args), chain)  # type: ignore[arg-type]


def _mut_wrong_root(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    return c, "b" * 40


def _mut_seed_with_parent(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    seed = _commit(c[-1].message, ("c" * 40,))
    run = bl.run_record(KB, IDENT, seq=1, parent=seed.sha, run_key="1001/1", claim=CLAIM)
    return [_commit(run, (seed.sha,)), seed], seed.sha


def _mut_bad_seed_mac(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    seed = _commit(bl.seed_record(b"x" * 32, IDENT))
    return [seed], seed.sha


def _mut_seed_for_another_lineage(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    seed = _seed(LedgerIdentity(repo_id="123456789", lineage="other-task"))
    return [seed], seed.sha


def _mut_seed_for_another_repo(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    seed = _seed(LedgerIdentity(repo_id="987654321", lineage="toy-task"))
    return [seed], seed.sha


def _run_mut(fn: Callable[[list[LedgerCommit]], list[LedgerCommit]], runs: int = 1) -> object:
    def apply(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
        chain = _chain(runs)
        return fn(chain), chain[-1].sha

    return apply


def _bad_run_mac(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, mac="0" * 64)


def _copied_onto_other_parent(c: list[LedgerCommit]) -> list[LedgerCommit]:
    # A valid record for seq 2 replayed at seq 2 on top of a different seq-1 commit.
    extra = bl.run_record(KB, IDENT, seq=1, parent=c[-1].sha, run_key="2001/1", claim=CLAIM)
    other = _commit(extra, (c[-1].sha,))
    return [_commit(c[0].message, (other.sha,)), other, c[-1]]


def _wrong_repo(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _signed_tip(c, LedgerIdentity(repo_id="987654321", lineage="toy-task"))


def _wrong_lineage(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _signed_tip(c, LedgerIdentity(repo_id="123456789", lineage="other-task"))


def _wrong_pool(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, pool="local")


def _non_contiguous_seq(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _signed_tip(c, IDENT, seq=2)


def _merge_commit(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return [_commit(c[0].message, (c[1].sha, "d" * 40)), *c[1:]]


def _duplicate_run_key(c: list[LedgerCommit]) -> list[LedgerCommit]:
    raw = bl.run_record(KB, IDENT, seq=3, parent=c[0].sha, run_key="1001/1", claim=OTHER_CLAIM)
    return [_commit(raw, (c[0].sha,)), *c]


def _unsigned_append(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return [_commit(b"leaked token was here", (c[0].sha,)), *c]


def _unknown_v(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, v=2)


def _bool_v(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, v=True)


def _float_seq(c: list[LedgerCommit]) -> list[LedgerCommit]:
    obj = _run_fields(1, c[1].sha)
    raw = _canon(obj).replace(b'"seq":1', b'"seq":1.0')
    return _tip_with(raw, c)


def _unknown_kind(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, kind="refund")


def _seed_at_non_root(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _tip_with(bl.seed_record(KB, IDENT), c)


def _extra_field(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _resign_tip(c, note="hi")


def _missing_field(c: list[LedgerCommit]) -> list[LedgerCommit]:
    obj = json.loads(c[0].message)
    del obj["claim"]
    return _tip_with(_canon(obj), c)


def _non_canonical(c: list[LedgerCommit]) -> list[LedgerCommit]:
    obj = json.loads(c[0].message)
    return _tip_with(json.dumps(obj, sort_keys=True).encode(), c)


def _unsorted_keys(c: list[LedgerCommit]) -> list[LedgerCommit]:
    obj = json.loads(c[0].message)
    raw = json.dumps(dict(reversed(list(obj.items()))), separators=(",", ":")).encode()
    return _tip_with(raw, c)


def _duplicate_key(c: list[LedgerCommit]) -> list[LedgerCommit]:
    raw = c[0].message[:-1] + b',"v":1}'
    return _tip_with(raw, c)


def _trailing_newline(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _tip_with(c[0].message + b"\n", c)


def _not_utf8(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _tip_with(b"\xff\xfe", c)


def _json_array(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return _tip_with(b"[1]", c)


def _extra_file(c: list[LedgerCommit]) -> list[LedgerCommit]:
    tip = c[0]
    return [replace(tip, files={**tip.files, "extra.txt": b"x"}), *c[1:]]


def _missing_record_file(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return [replace(c[0], files={"other.json": c[0].message}), *c[1:]]


def _message_differs(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return [replace(c[0], message=c[0].message + b" "), *c[1:]]


def _parent_mismatch(c: list[LedgerCommit]) -> list[LedgerCommit]:
    # The record claims a parent that is not the commit's actual parent.
    return _signed_tip(c, IDENT, parent="e" * 40)


def _broken_link(c: list[LedgerCommit]) -> list[LedgerCommit]:
    return [replace(c[0], parents=("f" * 40,)), *c[1:]]


def _run_at_root(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    raw = bl.run_record(KB, IDENT, seq=1, parent="a" * 40, run_key="1001/1", claim=CLAIM)
    root = _commit(raw)
    return [root], root.sha


def _over_long(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    chain = _chain(6)
    return chain, chain[-1].sha


def _empty(c: list[LedgerCommit]) -> tuple[list[LedgerCommit], str]:
    return [], "a" * 40


_MUTATIONS = {
    "wrong_root": _mut_wrong_root,
    "seed_with_parent": _mut_seed_with_parent,
    "bad_seed_mac": _mut_bad_seed_mac,
    "seed_for_another_lineage": _mut_seed_for_another_lineage,
    "seed_for_another_repo": _mut_seed_for_another_repo,
    "run_at_root": _run_at_root,
    "over_long_chain": _over_long,
    "empty": _empty,
    "bad_run_mac": _run_mut(_bad_run_mac),
    "copied_onto_other_parent": _run_mut(_copied_onto_other_parent, runs=2),
    "wrong_repo": _run_mut(_wrong_repo),
    "wrong_lineage": _run_mut(_wrong_lineage),
    "wrong_pool": _run_mut(_wrong_pool),
    "non_contiguous_seq": _run_mut(_non_contiguous_seq),
    "merge_commit": _run_mut(_merge_commit),
    "duplicate_run_key": _run_mut(_duplicate_run_key, runs=2),
    "unsigned_append": _run_mut(_unsigned_append),
    "unknown_v": _run_mut(_unknown_v),
    "bool_v": _run_mut(_bool_v),
    "float_seq": _run_mut(_float_seq),
    "unknown_kind": _run_mut(_unknown_kind),
    "seed_at_non_root": _run_mut(_seed_at_non_root),
    "extra_field": _run_mut(_extra_field),
    "missing_field": _run_mut(_missing_field),
    "non_canonical_json": _run_mut(_non_canonical),
    "unsorted_keys": _run_mut(_unsorted_keys),
    "duplicate_json_key": _run_mut(_duplicate_key),
    "trailing_newline": _run_mut(_trailing_newline),
    "not_utf8": _run_mut(_not_utf8),
    "json_array": _run_mut(_json_array),
    "extra_file": _run_mut(_extra_file),
    "missing_record_file": _run_mut(_missing_record_file),
    "message_differs_from_blob": _run_mut(_message_differs),
    "parent_field_mismatch": _run_mut(_parent_mismatch),
    "broken_link": _run_mut(_broken_link),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_tampered_chain_is_invalid(name: str) -> None:
    chain, root = _MUTATIONS[name](_chain(1))  # type: ignore[operator]
    with pytest.raises(LedgerInvalid):
        bl.validate_chain(chain, key=KB, identity=IDENT, root=root, limit=5)


def test_ledger_invalid_is_a_value_error() -> None:
    assert issubclass(LedgerInvalid, ValueError)


def test_limit_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        _validate(_chain(0), limit=-1)
