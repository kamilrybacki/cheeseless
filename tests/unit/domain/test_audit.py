"""The per-request allow audit carries only bounded, non-secret metadata."""

import pytest

from cheeseless.domain.audit import (
    ALLOW,
    AUDIT_SCHEMA,
    KNOWN_METHODS,
    OTHER_METHOD,
    REQUEST_ADMITTED,
    RESPONSE_CLEAN,
    AuditEvent,
)


def _event(**overrides):
    values = {
        "request_id": "0123456789abcdef",
        "stage": REQUEST_ADMITTED,
        "host": "api.example.test",
        "method": "POST",
        "rule": "allowlisted-host",
    }
    values.update(overrides)
    return AuditEvent(**values)


def test_record_has_exactly_the_documented_safe_fields():
    record = _event().to_record()
    assert record == {
        "schema": AUDIT_SCHEMA,
        "request_id": "0123456789abcdef",
        "stage": REQUEST_ADMITTED,
        "host": "api.example.test",
        "method": "POST",
        "decision": ALLOW,
        "rule": "allowlisted-host",
    }
    assert set(record) == {
        "schema",
        "request_id",
        "stage",
        "host",
        "method",
        "decision",
        "rule",
    }


@pytest.mark.parametrize("stage", [REQUEST_ADMITTED, RESPONSE_CLEAN])
def test_both_documented_stages_are_accepted(stage):
    assert _event(stage=stage).to_record()["stage"] == stage


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_id": ""},
        {"request_id": "not-hex"},
        {"request_id": "a" * 65},
        {"stage": "request"},
        {"stage": "response"},
        {"host": "API.Example.Test"},
        {"host": "host:443"},
        {"host": "user@host.test"},
        {"host": "h" * 254},
        {"method": "GET /secret"},
        {"method": "get"},
        {"method": ""},
        {"method": "SECRETTOKEN"},
        {"method": "FROBNICATE"},
        {"method": "OTHER "},
        {"rule": "allow listed"},
        {"rule": "attacker-controlled-text"},
        {"rule": "x" * 65},
    ],
)
def test_invalid_fields_are_refused(overrides):
    with pytest.raises(ValueError):
        _event(**overrides)


@pytest.mark.parametrize("method", sorted(KNOWN_METHODS))
def test_only_canonical_methods_and_other_are_accepted(method):
    assert _event(method=method).method == method


def test_other_is_the_only_escape_hatch_for_unknown_methods():
    assert _event(method=OTHER_METHOD).method == OTHER_METHOD
    assert OTHER_METHOD not in KNOWN_METHODS


def test_every_allowed_rule_is_in_the_policy_closed_set():
    for rule in ("allowlisted-host", "model-request", "registry-get"):
        assert _event(rule=rule).rule == rule


def test_a_path_query_or_fragment_has_no_field_to_go_into():
    with pytest.raises(TypeError):
        AuditEvent(
            request_id="0123456789abcdef",
            stage=REQUEST_ADMITTED,
            host="api.example.test",
            method="GET",
            rule="allowlisted-host",
            url="/private?token=secret",  # type: ignore[call-arg]
        )
