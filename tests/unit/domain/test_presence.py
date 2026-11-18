"""Presence-challenge request types and display escaping (ADR 0017)."""

from __future__ import annotations

import dataclasses

import pytest

from cheeseless.domain.presence import (
    PRESENCE_SIGNAL,
    PresenceConfirmation,
    PresenceOperation,
    PresenceRefused,
    PresenceRequest,
    escape_field,
    render_prompt,
)


def _request(**changes: str) -> PresenceRequest:
    base = PresenceRequest(
        operation=PresenceOperation.FREEZE_APPROVE,
        lineage_id="lin",
        subject="a" * 64,
        actor="operator",
        outcome="freeze",
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def test_request_and_confirmation_are_frozen() -> None:
    request = _request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.actor = "other"  # type: ignore[misc]
    confirmation = PresenceConfirmation(request, "n" * 32)
    with pytest.raises(dataclasses.FrozenInstanceError):
        confirmation.nonce = "x"  # type: ignore[misc]
    assert confirmation.signal == PRESENCE_SIGNAL == "tty-challenge"


def test_confirmation_needs_a_nonce() -> None:
    with pytest.raises(ValueError, match="nonce"):
        PresenceConfirmation(_request(), "")


def test_refused_carries_a_reason() -> None:
    refused = PresenceRefused("no controlling terminal")
    assert refused.reason == "no controlling terminal"
    assert "no controlling terminal" in str(refused)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("plain text", "plain text"),
        ("esc\x1b[2Jhere", "esc\\x1b[2Jhere"),
        ("bell\x07", "bell\\x07"),
        ("del\x7f", "del\\x7f"),
        ("c1\x9bX", "c1\\x9bX"),
        ("new\nline\r", "new\\x0aline\\x0d"),
        ("tab\there", "tab\\x09here"),
        ("bidi‮evil", "bidi\\u202eevil"),
    ],
)
def test_escape_field_neutralises_terminal_control(raw: str, shown: str) -> None:
    assert escape_field(raw) == shown


def test_escape_field_caps_length() -> None:
    shown = escape_field("x" * 1000, limit=40)
    assert len(shown) <= 40
    assert shown.endswith("...")


def test_prompt_shows_every_bound_field_escaped() -> None:
    request = _request(outcome="freeze no-reference", waiver="ref\x1b]0;pwn\x07")
    prompt = render_prompt(request)
    assert "freeze-approve" in prompt
    assert "lin" in prompt
    assert "a" * 64 in prompt
    assert "operator" in prompt
    assert "freeze no-reference" in prompt
    assert "\x1b" not in prompt
    assert "\x07" not in prompt
    assert "ref\\x1b]0;pwn\\x07" in prompt


def test_prompt_omits_an_empty_waiver() -> None:
    assert "waiver" not in render_prompt(_request())
