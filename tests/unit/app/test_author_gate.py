import pytest

from cheeseless.app import author_gate
from cheeseless.app.authoring import AuthorCheckResult, AuthorError
from cheeseless.domain.authoring import AuthoringState, TaskPhase
from cheeseless.domain.errors import ErrorCode, GateError


class _Repo:
    def snapshot(self, worktree):
        return "a" * 40


def _boom(kind):
    def raiser(*args, **kwargs):
        raise AuthorError(kind, "x")

    return raiser


def test_check_returns_the_result(monkeypatch):
    result = AuthorCheckResult(True, (), 3)
    monkeypatch.setattr(author_gate, "author_check", lambda *a, **k: result)
    assert author_gate.gate_author_check(_Repo(), object(), object(), "wt") == result


@pytest.mark.parametrize("kind", ["frozen", "not_proposed", "no_reference"])
def test_refusals_map_to_author_refused(monkeypatch, kind):
    monkeypatch.setattr(author_gate, "freeze_propose", _boom(kind))
    out = author_gate.gate_freeze_propose(_Repo(), object(), "wt")
    assert isinstance(out, GateError) and out.code is ErrorCode.AUTHOR_REFUSED


def test_missing_reference_while_blocked_reports_reference_blocked(monkeypatch):
    class Store:
        def authoring_state(self, lineage, spec):
            return AuthoringState(lineage, "b" * 64, TaskPhase.REFERENCE_BLOCKED, 1, 0)

    monkeypatch.setattr(author_gate, "author_crosscheck", _boom("no_reference"))
    monkeypatch.setattr(
        author_gate, "read_contract", lambda *a: type("C", (), {"lineage_id": "l"})()
    )
    monkeypatch.setattr(author_gate, "spec_of", lambda c: "b" * 64)
    out = author_gate.gate_author_crosscheck(_Repo(), object(), Store(), "wt")
    assert isinstance(out, GateError) and out.code is ErrorCode.REFERENCE_BLOCKED


def test_unexpected_failures_are_infra_errors(monkeypatch):
    def raiser(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(author_gate, "author_check", raiser)
    out = author_gate.gate_author_check(_Repo(), object(), object(), "wt")
    assert isinstance(out, GateError) and out.code is ErrorCode.INFRA_ERROR
    assert "secret" not in repr(out)
