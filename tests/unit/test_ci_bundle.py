import base64
import json

import pytest

from cheeseless.ci_bundle import (
    BUNDLE_VERSION,
    BudgetLedgerRef,
    BundleError,
    decode_bundle,
    encode_bundle,
)
from tests.factories import frozen_task

KEY = bytes(range(32))
SEED = bytes(range(32))
ROOT = "a" * 40
BUDGET = BudgetLedgerRef(
    repo="example-owner/cheeseless-ledger", branch="cheeseless/budget/toy", root=ROOT
)


def _frozen():
    return frozen_task(), SEED


def _roundtrip_parts():
    frozen, seed = _frozen()
    return frozen, seed, encode_bundle(frozen, seed, KEY, ci_limit=5, budget=BUDGET, disputes=0)


def test_round_trip():
    frozen, seed, raw = _roundtrip_parts()
    bundle = decode_bundle(raw)
    assert bundle.frozen == frozen
    assert bundle.seed == seed
    assert bundle.key == KEY
    assert bundle.ci_limit == 5
    assert bundle.budget == BUDGET


def test_version_is_three():
    _, _, raw = _roundtrip_parts()
    assert BUNDLE_VERSION == 3
    assert json.loads(raw)["version"] == 3


def test_short_key_refused_on_encode():
    frozen, seed = _frozen()
    with pytest.raises(BundleError):
        encode_bundle(frozen, seed, b"short", ci_limit=5, budget=BUDGET, disputes=0)


@pytest.mark.parametrize(
    "budget",
    [
        BudgetLedgerRef("not a repo", BUDGET.branch, ROOT),
        BudgetLedgerRef(BUDGET.repo, "cheeseless/budget/other", ROOT),
        BudgetLedgerRef(BUDGET.repo, "main", ROOT),
        BudgetLedgerRef(BUDGET.repo, BUDGET.branch, "A" * 40),
        BudgetLedgerRef(BUDGET.repo, BUDGET.branch, "a" * 39),
    ],
)
def test_bad_budget_refused_on_encode(budget):
    frozen, seed = _frozen()
    with pytest.raises(BundleError):
        encode_bundle(frozen, seed, KEY, ci_limit=5, budget=budget, disputes=0)


def _mutate(raw, fn):
    data = json.loads(raw)
    fn(data)
    return json.dumps(data).encode()


def _resign(data):
    """Recompute the MAC so a check other than the MAC must catch the mutation."""
    import hashlib
    import hmac

    body = {k: v for k, v in data.items() if k != "mac"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    data["mac"] = hmac.new(KEY, canon, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(version=1),
        lambda d: d.update(version=2),
        lambda d: d.update(version=4),
        lambda d: d.update(disputes=-1),
        lambda d: d.update(disputes=True),
        lambda d: d.update(disputes="0"),
        lambda d: d.pop("disputes"),
        lambda d: d.update(key=base64.b64encode(b"x" * 8).decode()),
        lambda d: d.update(ci_limit=0),
        lambda d: d.update(ci_limit="5"),
        lambda d: d.update(extra=1),
        lambda d: d.pop("mac"),
        lambda d: d.update(lineage="other"),
        lambda d: d["task"]["visible"].update({"x::y": "c"}),
        lambda d: d.pop("task"),
        lambda d: d.pop("budget"),
        lambda d: d["budget"].update(repo="evil-owner/ledger"),
        lambda d: d["budget"].update(branch="cheeseless/budget/other"),
        lambda d: d["budget"].update(root="b" * 40),
    ],
)
def test_tampered_bundle_refused(mutation):
    _, _, raw = _roundtrip_parts()
    with pytest.raises(BundleError):
        decode_bundle(_mutate(raw, mutation))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.pop("budget"),
        lambda d: d.update(budget=None),
        lambda d: d.update(budget="example-owner/cheeseless-ledger"),
        lambda d: d["budget"].pop("root"),
        lambda d: d["budget"].update(extra="x"),
        lambda d: d["budget"].update(repo=""),
        lambda d: d["budget"].update(branch="cheeseless/budget/other"),
        lambda d: d["budget"].update(branch="main"),
        lambda d: d["budget"].update(root="ROOT"),
        lambda d: d["budget"].update(root=7),
    ],
)
def test_malformed_budget_refused_even_with_a_valid_mac(mutation):
    """A key holder cannot sign a bundle that points at a ledger of another lineage."""
    _, _, raw = _roundtrip_parts()

    def both(d):
        mutation(d)
        _resign(d)

    with pytest.raises(BundleError):
        decode_bundle(_mutate(raw, both))


def test_resign_helper_produces_an_accepted_bundle():
    _, _, raw = _roundtrip_parts()
    assert decode_bundle(_mutate(raw, _resign)).budget == BUDGET


def test_v1_bundle_without_a_ledger_refused():
    _, _, raw = _roundtrip_parts()

    def to_v1(d):
        d.pop("budget")
        d["version"] = 1
        _resign(d)

    with pytest.raises(BundleError, match="version"):
        decode_bundle(_mutate(raw, to_v1))


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b"\xff\xfe"])
def test_garbage_refused(raw):
    with pytest.raises(BundleError):
        decode_bundle(raw)


def test_bundle_records_the_dispute_watermark_it_was_exported_against():
    frozen, seed = _frozen()
    raw = encode_bundle(frozen, seed, KEY, ci_limit=5, budget=BUDGET, disputes=2)
    assert decode_bundle(raw).disputes == 2


def test_the_watermark_is_covered_by_the_mac():
    frozen, seed = _frozen()
    raw = encode_bundle(frozen, seed, KEY, ci_limit=5, budget=BUDGET, disputes=2)
    with pytest.raises(BundleError):
        decode_bundle(_mutate(raw, lambda d: d.update(disputes=0)))
