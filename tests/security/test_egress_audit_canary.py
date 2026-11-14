"""Allowed egress is audited without paths, bodies, tokens or hidden content.

The positive control drives real traffic through the real mitmproxy proxy, so an audit
event is never faked by a unit stub. Rejection and audit-failure paths must not copy
canary or exception text into the event or back to the client.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.egress.proxy import EgressProxy, ProxyConfig
from cheeseless.domain.audit import REQUEST_ADMITTED, RESPONSE_CLEAN, AuditEvent
from cheeseless.domain.dlp import build_fingerprints
from cheeseless.domain.egress_policy import EgressPolicy
from tests.egress_support import Upstream, certificate
from tests.requirements import needs

TASK = "c" * 64
SENTINEL = b"CHEESELESS-CANARY-audit-4d2e"
QUERY_LEAK = "SUPERSECRET-QUERY-VALUE"

pytestmark = needs(hasattr(__import__("ssl"), "SSLContext"), "needs TLS")


@pytest.fixture(scope="module")
def upstream(tmp_path_factory):
    directory = tmp_path_factory.mktemp("audit-tls")
    ca, cert, key = certificate(directory)
    server = Upstream(SENTINEL).start(cert, key, SENTINEL)
    yield ca, server
    server.close()


@pytest.fixture()
def fingerprints():
    return build_fingerprints(
        TASK,
        [("acceptance/hidden/test_x.py", b"prefix " + SENTINEL)],
        [],
    )


class ProxyFixture:
    def __init__(
        self,
        proxy: EgressProxy,
        events: list[AuditEvent],
        hits: list[tuple[str, str, str]],
        port: int,
    ) -> None:
        self.proxy = proxy
        self.events = events
        self.hits = hits
        self.port = port

    def request(self, path: str, method: str = "GET", body: bytes = b"") -> bytes:
        sock = socket.create_connection(("127.0.0.1", self.proxy.tcp_port), timeout=10)
        sock.sendall(
            f"CONNECT 127.0.0.1:{self.port} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n\r\n".encode()
        )
        assert sock.recv(4096).startswith(b"HTTP/1.1 200")
        import ssl

        context = ssl.create_default_context(cafile=str(self.proxy.ca_cert_path))
        tls = context.wrap_socket(sock, server_hostname="127.0.0.1")
        tls.sendall(
            (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                f"Connection: close\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        out = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            out += chunk
        tls.close()
        return out

    def get(self, path: str) -> bytes:
        return self.request(path)


def _start(tmp_path: Path, tls, fingerprints, sink=None) -> ProxyFixture:
    ca, server = tls
    events: list[AuditEvent] = []
    hits: list[tuple[str, str, str]] = []

    def audit(event: AuditEvent) -> None:
        if sink is not None:
            sink(event)
        events.append(event)

    proxy = EgressProxy(
        ProxyConfig(
            policy=EgressPolicy(frozenset({"127.0.0.1"})),
            confdir=tmp_path / "ca",
            socket_path=tmp_path / "egress.sock",
            upstream_trusted_ca=ca,
        ),
        fingerprints,
        on_hit=lambda host, rule, fid: hits.append((host, rule, fid)),
        on_audit=audit,
    )
    proxy.start()
    return ProxyFixture(proxy, events, hits, server.port)


def test_a_clean_allowed_request_logs_admission_and_a_clean_response(
    tmp_path, upstream, fingerprints
):
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        response = fixture.get(f"/clean?token={QUERY_LEAK}")
    finally:
        fixture.proxy.stop()

    assert b" 200 " in response.split(b"\r\n", 1)[0] or b" 404 " in response.split(b"\r\n", 1)[0]
    assert [event.stage for event in fixture.events] == [REQUEST_ADMITTED, RESPONSE_CLEAN]
    first, second = fixture.events
    assert first.request_id == second.request_id and first.request_id
    assert first.host == "127.0.0.1"
    assert first.method == "GET"
    assert first.decision == "allow"
    assert first.rule == "allowlisted-host"
    serialized = json.dumps([event.to_record() for event in fixture.events])
    for forbidden in (QUERY_LEAK, "token", "/clean", SENTINEL.decode()):
        assert forbidden not in serialized


def test_an_unknown_method_is_audited_as_other_and_never_leaks_its_text(
    tmp_path, upstream, fingerprints
):
    wire_method = "SECRETTOKEN"
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        fixture.request(f"/clean?token={QUERY_LEAK}", method=wire_method)
    finally:
        fixture.proxy.stop()

    assert [event.stage for event in fixture.events] == [REQUEST_ADMITTED, RESPONSE_CLEAN]
    assert fixture.events[0].method == "OTHER"
    serialized = json.dumps([event.to_record() for event in fixture.events])
    assert wire_method not in serialized
    assert QUERY_LEAK not in serialized


@pytest.mark.parametrize(
    ("wire_method", "audited_method"),
    [("get", "GET"), ("POST", "POST"), ("PATCH", "PATCH"), ("options", "OPTIONS")],
)
def test_known_http_methods_are_preserved_in_the_audit(
    tmp_path, upstream, fingerprints, wire_method, audited_method
):
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        fixture.request("/clean", method=wire_method)
    finally:
        fixture.proxy.stop()
    assert fixture.events[0].method == audited_method


def test_an_audit_failure_still_fails_an_unknown_method_closed(
    tmp_path, upstream, fingerprints
):
    def failing(_event: AuditEvent) -> None:
        raise RuntimeError("sink unavailable")

    fixture = _start(tmp_path, upstream, fingerprints, sink=failing)
    try:
        response = fixture.request("/clean", method="SECRETTOKEN")
    finally:
        fixture.proxy.stop()
    head = response.split(b"\r\n\r\n", 1)[0]
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: infra_error" in head.lower()
    assert fixture.hits == [("127.0.0.1", "audit", "audit-unavailable")]


def test_a_blocked_response_is_admitted_but_never_reported_clean(tmp_path, upstream, fingerprints):
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        response = fixture.get("/leak")
    finally:
        fixture.proxy.stop()
    assert SENTINEL not in response
    assert [event.stage for event in fixture.events] == [REQUEST_ADMITTED]
    serialized = json.dumps([event.to_record() for event in fixture.events])
    assert SENTINEL.decode() not in serialized


def test_a_request_canary_is_blocked_before_any_allow_event(tmp_path, upstream, fingerprints):
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        response = fixture.request("/clean", method="POST", body=b"leak " + SENTINEL)
    finally:
        fixture.proxy.stop()
    head = response.split(b"\r\n\r\n", 1)[0]
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert SENTINEL not in response
    assert fixture.events == []
    assert SENTINEL.decode() not in json.dumps([event.to_record() for event in fixture.events])


def test_a_denied_host_produces_no_allow_event(tmp_path, upstream, fingerprints):
    fixture = _start(tmp_path, upstream, fingerprints)
    try:
        sock = socket.create_connection(("127.0.0.1", fixture.proxy.tcp_port), timeout=10)
        sock.sendall(b"CONNECT evil.test:443 HTTP/1.1\r\nHost: evil.test:443\r\n\r\n")
        response = sock.recv(8192)
        sock.close()
    finally:
        fixture.proxy.stop()
    assert b" 403 " in response.split(b"\r\n", 1)[0]
    assert fixture.events == []


def test_an_audit_failure_fails_the_request_closed_without_leaking_text(
    tmp_path, upstream, fingerprints
):
    canary = "AUDIT-EXCEPTION-SECRET-9f"

    def failing(_event: AuditEvent) -> None:
        raise RuntimeError(f"sink exploded with {canary}")

    fixture = _start(tmp_path, upstream, fingerprints, sink=failing)
    try:
        response = fixture.get("/clean")
    finally:
        fixture.proxy.stop()
    head = response.split(b"\r\n\r\n", 1)[0]
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: infra_error" in head.lower()
    assert canary.encode() not in response
    assert fixture.hits == [("127.0.0.1", "audit", "audit-unavailable")]


def test_a_lost_clean_response_audit_tears_the_session_down(tmp_path, upstream, fingerprints):
    canary = "AUDIT-RESPONSE-SECRET-7a"

    def fail_on_clean(event: AuditEvent) -> None:
        if event.stage == RESPONSE_CLEAN:
            raise RuntimeError(f"response audit lost {canary}")

    fixture = _start(tmp_path, upstream, fingerprints, sink=fail_on_clean)
    try:
        response = fixture.get("/clean")
    finally:
        fixture.proxy.stop()
    assert b"public clean body" in response
    assert canary.encode() not in response
    assert fixture.hits == [("127.0.0.1", "audit", "audit-unavailable")]
