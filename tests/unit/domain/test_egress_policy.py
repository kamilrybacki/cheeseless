"""Egress policy: exact hosts, registries and server-side tools (Q48)."""

import json

import pytest

from cheeseless.domain.egress_policy import (
    ALLOW_RULES,
    Decision,
    EgressPolicy,
    PolicyError,
    normalize_host,
)
from cheeseless.domain.errors import ErrorCode

POLICY = EgressPolicy(
    allowed_hosts=frozenset({"api.example.test", "registry.example.test", "127.0.0.1"}),
    model_hosts=frozenset({"api.example.test"}),
    registry_hosts=frozenset({"registry.example.test"}),
    denied_markers=("cheeseless", "my-secret-repo"),
)


def request(method="POST", host="api.example.test", url=None, headers=None, body=b""):
    return POLICY.check_request(
        method,
        host,
        url or f"https://{host}/v1/messages",
        headers or {},
        body,
    )


def test_host_normalisation_is_exact():
    assert normalize_host("API.Example.TEST.") == "api.example.test"
    assert normalize_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ValueError):
        normalize_host("")
    with pytest.raises(ValueError):
        normalize_host("bad host")


def test_allowlist_has_no_suffix_or_wildcard_match():
    assert request(host="api.example.test") is None
    denied = request(host="evil.api.example.test")
    assert isinstance(denied, Decision)
    assert denied.code is ErrorCode.EGRESS_DOMAIN_DENIED
    assert request(host="api.example.test.evil") is not None
    own_url = "https://api.example.test/x?q=cheeseless"
    assert request(host="api.example.test", url=own_url) is not None


def test_registry_is_get_only_and_own_identity_is_denied():
    assert request(method="GET", host="registry.example.test") is None
    denied = request(method="POST", host="registry.example.test")
    assert isinstance(denied, Decision) and denied.code is ErrorCode.EGRESS_DOMAIN_DENIED
    assert denied.rule == "registry-method"
    own = request(
        method="GET",
        host="registry.example.test",
        url="https://registry.example.test/simple/cheeseless/",
    )
    assert isinstance(own, Decision) and own.rule == "own-identity"


def test_encoded_requests_and_upgrades_are_blocked():
    encoded = request(headers={"Content-Encoding": "gzip"})
    assert isinstance(encoded, Decision) and encoded.code is ErrorCode.DLP_BLOCKED
    upgrade = request(headers={"Upgrade": "websocket"})
    assert isinstance(upgrade, Decision) and upgrade.code is ErrorCode.DLP_BLOCKED


def test_server_side_tool_schemas_are_rejected():
    bodies = [
        {"tools": [{"type": "web_search"}]},
        {"tools": [{"type": "code_execution"}]},
        {"tools": [{"type": "function"}, {"type": "url_context"}]},
        {"mcp_servers": [{"url": "https://x"}]},
        {"tools": "not-a-list"},
        {"tools": [{"type": "future_unknown_tool"}]},
    ]
    for body in bodies:
        denied = request(body=json.dumps(body).encode())
        assert isinstance(denied, Decision), body
        assert denied.code is ErrorCode.EGRESS_SERVER_TOOL_DENIED, body


def test_plain_function_tools_and_unparseable_non_model_bodies():
    allowed = request(body=json.dumps({"tools": [{"type": "function", "name": "f"}]}).encode())
    assert allowed is None
    assert request(body=b"not json") is not None
    other = EgressPolicy(
        allowed_hosts=frozenset({"pypi.example.test"}),
        registry_hosts=frozenset({"pypi.example.test"}),
    )
    assert (
        other.check_request(
            "GET", "pypi.example.test", "https://pypi.example.test/simple/", {}, b""
        )
        is None
    )


def test_response_encoding_and_upgrade_are_blocked():
    assert POLICY.check_response("api.example.test", {"Content-Encoding": "identity"}) is None
    assert POLICY.check_response("api.example.test", {"Content-Encoding": "gzip"}) is not None
    assert POLICY.check_response("api.example.test", {"Upgrade": "websocket"}) is not None
    assert POLICY.check_response("evil.test", {}) is not None


def test_invalid_configuration_is_refused():
    with pytest.raises(PolicyError):
        EgressPolicy(allowed_hosts=frozenset({"a.test"}), model_hosts=frozenset({"b.test"}))
    with pytest.raises(PolicyError):
        EgressPolicy.from_config({"allowed_hosts": "a.test"})


def test_allow_rule_names_the_policy_branch_that_allowed_the_host():
    assert POLICY.allow_rule("api.example.test") == "model-request"
    registry = EgressPolicy(
        allowed_hosts=frozenset({"pypi.example.test"}),
        registry_hosts=frozenset({"pypi.example.test"}),
    )
    assert registry.allow_rule("pypi.example.test") == "registry-get"
    plain = EgressPolicy(allowed_hosts=frozenset({"cdn.example.test"}))
    assert plain.allow_rule("cdn.example.test") == "allowlisted-host"


def test_every_allow_rule_is_in_the_closed_audit_set():
    registry = EgressPolicy(
        allowed_hosts=frozenset({"api.example.test", "pypi.example.test", "cdn.example.test"}),
        model_hosts=frozenset({"api.example.test"}),
        registry_hosts=frozenset({"pypi.example.test"}),
    )
    for host in ("api.example.test", "pypi.example.test", "cdn.example.test"):
        assert registry.allow_rule(host) in ALLOW_RULES
