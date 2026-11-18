"""The app-layer presence requirement (ADR 0017): fail closed, bound, single use."""

from __future__ import annotations

import secrets

import pytest

from cheeseless.app.presence import presence_note, require_presence
from cheeseless.domain.presence import (
    PresenceConfirmation,
    PresenceOperation,
    PresenceRefused,
    PresenceRequest,
)

REQUEST = PresenceRequest(PresenceOperation.DISPUTE_RESOLVE, "lin", "d1", "operator", "upheld")


class Approving:
    def __init__(self) -> None:
        self.asked: list[PresenceRequest] = []

    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        self.asked.append(request)
        return PresenceConfirmation(request, secrets.token_hex(16))


class Replaying:
    """Returns one confirmation forever: a reused proof must not count twice."""

    def __init__(self) -> None:
        self.once = PresenceConfirmation(REQUEST, secrets.token_hex(16))

    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        return self.once


class Mismatched:
    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        other = PresenceRequest(
            request.operation, request.lineage_id, "other", request.actor, request.outcome
        )
        return PresenceConfirmation(other, secrets.token_hex(16))


class Refusing:
    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        raise PresenceRefused("no controlling terminal")


def test_no_proof_port_fails_closed() -> None:
    with pytest.raises(PresenceRefused, match="no presence"):
        require_presence(None, REQUEST)


def test_an_approving_port_is_asked_the_exact_request() -> None:
    proof = Approving()
    confirmation = require_presence(proof, REQUEST)
    assert proof.asked == [REQUEST]
    assert confirmation.request == REQUEST


def test_a_refusal_propagates() -> None:
    with pytest.raises(PresenceRefused, match="no controlling terminal"):
        require_presence(Refusing(), REQUEST)


def test_a_confirmation_for_another_request_is_refused() -> None:
    with pytest.raises(PresenceRefused, match="different decision"):
        require_presence(Mismatched(), REQUEST)


def test_a_confirmation_is_single_use() -> None:
    proof = Replaying()
    require_presence(proof, REQUEST)
    with pytest.raises(PresenceRefused, match="already used"):
        require_presence(proof, REQUEST)


def test_a_non_confirmation_is_refused() -> None:
    class Truthy:
        def confirm(self, request: PresenceRequest) -> object:
            return True

    with pytest.raises(PresenceRefused, match="not a confirmation"):
        require_presence(Truthy(), REQUEST)  # type: ignore[arg-type]


def test_the_audit_note_names_the_signal() -> None:
    confirmation = require_presence(Approving(), REQUEST)
    assert presence_note(confirmation) == "presence: tty-challenge"
