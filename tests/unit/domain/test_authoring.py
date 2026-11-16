import hashlib

import pytest

from cheeseless.domain.authoring import (
    MAX_REFERENCE_RETRIES,
    AuthoringError,
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    TaskPhase,
    crosscheck_binds,
    spec_digest,
)

OID_A = "a" * 40
OID_B = "b" * 40
OID_C = "c" * 40
SPEC = "1" * 64


def _state(**kw: object) -> AuthoringState:
    return AuthoringState(lineage_id="demo", spec_digest=SPEC, **kw)  # type: ignore[arg-type]


def test_spec_digest_is_sha256_of_contract_bytes() -> None:
    assert spec_digest(b"x") == hashlib.sha256(b"x").hexdigest()


def test_new_state_is_authoring_with_no_failures() -> None:
    s = _state()
    assert s.phase is TaskPhase.AUTHORING
    assert s.reference_failures == 0


def test_first_reference_failure_blocks_and_allows_two_retries() -> None:
    s = _state().reference_failed(SPEC)
    assert s.phase is TaskPhase.REFERENCE_BLOCKED
    assert s.retries_left == MAX_REFERENCE_RETRIES - 0
    s = s.retry_started().reference_failed(SPEC)
    assert s.retries_left == 1
    s = s.retry_started().reference_failed(SPEC)
    assert s.retries_left == 0
    with pytest.raises(AuthoringError):
        s.retry_started()


def test_spec_edit_resets_retries_and_returns_to_authoring() -> None:
    s = _state().reference_failed(SPEC).retry_started().reference_failed(SPEC)
    edited = s.spec_edited("2" * 64)
    assert edited.phase is TaskPhase.AUTHORING
    assert edited.reference_failures == 0
    assert edited.spec_digest == "2" * 64


def test_same_spec_keeps_count() -> None:
    s = _state().reference_failed(SPEC)
    assert s.spec_edited(SPEC).reference_failures == 1


def test_failure_under_new_spec_counts_from_one() -> None:
    s = _state().reference_failed(SPEC).reference_failed("3" * 64)
    assert s.reference_failures == 1


def test_propose_from_authoring_and_blocked_only() -> None:
    assert _state().proposed().phase is TaskPhase.PROPOSED
    assert _state().reference_failed(SPEC).proposed().phase is TaskPhase.PROPOSED
    with pytest.raises(AuthoringError):
        _state().proposed().proposed()


def test_stale_returns_to_authoring_and_frozen_is_terminal() -> None:
    assert _state().proposed().stale().phase is TaskPhase.AUTHORING
    frozen = _state().proposed().frozen()
    assert frozen.phase is TaskPhase.FROZEN
    for op in (frozen.proposed, frozen.stale, frozen.frozen, lambda: frozen.reference_failed(SPEC)):
        with pytest.raises(AuthoringError):
            op()
    assert frozen.closed


def test_frozen_requires_proposed() -> None:
    with pytest.raises(AuthoringError):
        _state().frozen()


def _record(**kw: object) -> CrosscheckRecord:
    base: dict[str, object] = {
        "acceptance_tree": OID_A,
        "spec_digest": SPEC,
        "baseline_commit": OID_B,
        "reference_tree": OID_C,
        "green": True,
        "red_criteria": (),
    }
    base.update(kw)
    return CrosscheckRecord(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("acceptance_tree", OID_B),
        ("spec_digest", "9" * 64),
        ("baseline_commit", OID_A),
        ("reference_tree", OID_A),
    ],
)
def test_crosscheck_binds_all_four(field: str, value: str) -> None:
    kw = {
        "acceptance_tree": OID_A,
        "spec_digest": SPEC,
        "baseline_commit": OID_B,
        "reference_tree": OID_C,
    }
    assert crosscheck_binds(_record(), **kw)
    assert not crosscheck_binds(_record(), **{**kw, field: value})


def test_green_record_cannot_list_red_criteria() -> None:
    with pytest.raises(ValueError):
        _record(green=True, red_criteria=("c1",))


def test_proposal_validates_shapes() -> None:
    p = Proposal(
        proposal_id="d" * 64,
        lineage_id="demo",
        baseline_commit=OID_B,
        acceptance_tree=OID_A,
        spec_digest=SPEC,
    )
    assert p.proposal_id == "d" * 64
    with pytest.raises(ValueError):
        Proposal(
            proposal_id="zz",
            lineage_id="demo",
            baseline_commit=OID_B,
            acceptance_tree=OID_A,
            spec_digest=SPEC,
        )
