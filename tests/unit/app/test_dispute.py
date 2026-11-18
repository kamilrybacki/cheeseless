"""gate_dispute use case against the real file store (ADR 0009, stage g)."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dispute import (
    configure_disputes,
    gate_dispute,
    list_disputes,
    resolve_dispute,
)
from cheeseless.domain.dispute import ACK_TEXT, DisputeStatus
from cheeseless.domain.errors import ErrorCode, GateError
from cheeseless.ports.store import StoreError
from tests.factories import frozen_task
from tests.presence_fakes import Approving

SEED = bytes(range(32))


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    s = FileStore(tmp_path / "state")
    s.save_task(frozen_task(), SEED)
    return s


def _tid() -> str:
    return frozen_task().task.task_id


def _dispute(store, criterion="normalize", code="untestable", note="why"):
    return gate_dispute(store, task_id=_tid(), criterion_id=criterion, code=code, note=note)


def _code(out):
    assert isinstance(out, GateError)
    return out.code


def test_disabled_by_default_records_nothing(store):
    assert _code(_dispute(store)) is ErrorCode.DISPUTE_DISABLED
    assert list_disputes(store) == ()


def test_accepted_dispute_returns_the_fixed_ack(store):
    configure_disputes(store, enabled=True, cap=3)
    assert _dispute(store) == ACK_TEXT
    assert store.session_disputed(_tid())


def test_ack_is_identical_for_every_accepted_dispute(tmp_path):
    acks = set()
    for i, (crit, code, note) in enumerate(
        [("normalize", "untestable", "a"), ("score", "other", "b" * 400)]
    ):
        s = FileStore(tmp_path / f"s{i}")
        s.save_task(frozen_task(), SEED)
        configure_disputes(s, enabled=True, cap=3)
        acks.add(_dispute(s, crit, code, note))
    assert acks == {ACK_TEXT}


@pytest.mark.parametrize(
    ("criterion", "code", "note"),
    [
        ("nope", "untestable", "x"),
        ("normalize", "bogus", "x"),
        ("normalize", "untestable", "x" * 501),
        ("normalize", "untestable", "bad\x00note"),
        ("normalize", "untestable", 5),
        (None, "untestable", "x"),
    ],
)
def test_invalid_input_is_one_generic_error(store, criterion, code, note):
    configure_disputes(store, enabled=True, cap=3)
    out = _dispute(store, criterion, code, note)
    assert _code(out) is ErrorCode.DISPUTE_INVALID
    assert "nope" not in out.reason + out.hint
    assert list_disputes(store) == ()


def test_resolve_is_once_only_and_uses_the_actor(store):
    configure_disputes(store, enabled=True, cap=3)
    _dispute(store)
    rec = list_disputes(store)[0]
    done = resolve_dispute(store, rec.dispute_id, upheld=True, actor="alice", presence=Approving())
    assert done.status is DisputeStatus.UPHELD and done.resolved_by == "alice"
    with pytest.raises(StoreError):
        resolve_dispute(store, rec.dispute_id, upheld=False, actor="bob", presence=Approving())


def test_configure_rejects_a_bad_cap(store):
    with pytest.raises(StoreError):
        configure_disputes(store, enabled=True, cap=0)
