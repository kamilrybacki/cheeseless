"""End-to-end egress DLP: real TLS upstream, real mitmproxy, UDS bridge (Q48/Q49)."""

from __future__ import annotations

import gzip
import http.server
import io
import ipaddress
import json
import socket
import ssl
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from cheeseless.adapters.outbound.egress.proxy import EgressProxy, ProxyConfig
from cheeseless.domain.dlp import build_fingerprints
from cheeseless.domain.egress_policy import EgressPolicy
from tests.requirements import needs

TASK = "b" * 64
SENTINEL = b"CHEESELESS-CANARY-7f3a9c"

pytestmark = needs(hasattr(ssl, "SSLContext"), "needs TLS")


def _certificate(directory: Path) -> tuple[Path, Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cheeseless-test-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        .not_valid_after(
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(days=1)
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_subject)
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        .not_valid_after(
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(days=1)
        )
        .add_extension(san, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    cert_path = directory / "server.pem"
    key_path = directory / "server.key"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


def _zip(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", payload)
    return buffer.getvalue()


class Upstream:
    def __init__(self, port: int, tls: ssl.SSLContext, server: object) -> None:
        self.port = port
        self.tls = tls
        self._server = server

    @classmethod
    def start(cls, cert: Path, key: Path) -> Upstream:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                if self.path == "/clean":
                    self._send(200, b"public clean body", "text/plain")
                elif self.path == "/leak":
                    self._send(200, b"public prefix " + SENTINEL + b" suffix", "text/plain")
                elif self.path == "/clean-archive":
                    self._send(200, _zip(b"public clean archive"), "application/zip")
                elif self.path == "/leak-archive":
                    self._send(200, _zip(b"public prefix " + SENTINEL), "application/zip")
                elif self.path == "/encoded":
                    self._send(
                        200,
                        gzip.compress(b"public prefix " + SENTINEL),
                        "text/plain",
                        {"Content-Encoding": "gzip"},
                    )
                elif self.path == "/accept-encoding":
                    value = self.headers.get("Accept-Encoding", "")
                    self._send(200, value.encode(), "text/plain")
                elif self.path == "/sse":
                    self._sse()
                elif self.path == "/sse-chunked":
                    self._sse_chunked(clean=True)
                elif self.path == "/sse-chunked-leak":
                    self._sse_chunked(clean=False)
                elif self.path == "/sse-chunked-split":
                    self._sse_chunked(clean=False, split=True)
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                self._send(200, b"echo " + body[:16], "text/plain")

            def _send(self, status: int, body: bytes, content_type: str, extra=None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def _sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                chunks = (
                    b"data: public one\n\n",
                    b"data: ",
                    SENTINEL[:12],
                    SENTINEL[12:],
                    b"\n\ndata: done\n\n",
                )
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                self.close_connection = True

            def _sse_chunked(self, *, clean: bool, split: bool = False) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                head = (
                    b"event: ping\n\n",
                    b"data: " + b"x" * 300 + b"\n\n",
                    b"data: " + b"y" * 300 + b"\n\n",
                )
                if split:
                    # The marker straddles three HTTP chunk boundaries.
                    cut = len(SENTINEL) // 3
                    tail: tuple[bytes, ...] = (
                        b"data: " + SENTINEL[:cut],
                        SENTINEL[cut : 2 * cut],
                        SENTINEL[2 * cut :] + b"\n\n",
                    )
                elif clean:
                    tail = (b"data: public tail\n\n",)
                else:
                    tail = (b"data: " + SENTINEL + b"\n\n",)
                chunks = head + tail
                for chunk in chunks:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return cls(port=server.server_address[1], tls=context, server=server)

    def close(self) -> None:
        self._server.shutdown()  # type: ignore[attr-defined]
        self._server.server_close()  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tls")
    ca, cert, key = _certificate(directory)
    upstream = Upstream.start(cert, key)
    yield type("Tls", (), {"ca": ca, "upstream": upstream})()
    upstream.close()


@pytest.fixture()
def fingerprints():
    return build_fingerprints(TASK, [("acceptance/hidden/test_x.py", b"prefix " + SENTINEL)], [])


@dataclass
class ProxyFixture:
    proxy: EgressProxy
    hits: list[tuple[str, str, str]]
    upstream: Upstream


def _start_proxy(
    tmp_path: Path, tls, fingerprints, policy: EgressPolicy | None = None
) -> ProxyFixture:
    hits: list[tuple[str, str, str]] = []
    proxy = EgressProxy(
        ProxyConfig(
            policy=policy or EgressPolicy(frozenset({"127.0.0.1"})),
            confdir=tmp_path / "ca",
            socket_path=tmp_path / "egress.sock",
            upstream_trusted_ca=tls.ca,
        ),
        fingerprints,
        on_hit=lambda host, rule, fid: hits.append((host, rule, fid)),
    )
    proxy.start()
    return ProxyFixture(proxy, hits, tls.upstream)


def _get_through(
    sock: socket.socket,
    proxy: EgressProxy,
    port: int,
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    host: str = "127.0.0.1",
) -> bytes:
    sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    answer = sock.recv(4096)
    assert answer.startswith(b"HTTP/1.1 200"), answer
    context = ssl.create_default_context(cafile=str(proxy.ca_cert_path))
    tls_sock = context.wrap_socket(sock, server_hostname=host)
    payload = body or b""
    headers = (
        f"{method} {path} HTTP/1.1\r\nHost: {host}\r\n"
        f"Connection: close\r\nContent-Length: {len(payload)}\r\n\r\n"
    )
    tls_sock.sendall(headers.encode() + payload)
    out = b""
    while True:
        chunk = tls_sock.recv(65536)
        if not chunk:
            break
        out += chunk
    tls_sock.close()
    return out


def _tcp_get(fixture: ProxyFixture, path: str, **kwargs) -> bytes:
    sock = socket.create_connection(("127.0.0.1", fixture.proxy.tcp_port), timeout=10)
    return _get_through(sock, fixture.proxy, fixture.upstream.port, path, **kwargs)


def _uds_get(fixture: ProxyFixture, path: str, **kwargs) -> bytes:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(str(fixture.proxy.socket_path))
    return _get_through(sock, fixture.proxy, fixture.upstream.port, path, **kwargs)


def _body(response: bytes) -> bytes:
    return response.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in response else b""


def test_clean_traffic_flows_through_the_uds_bridge(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _uds_get(fixture, "/clean")
    finally:
        fixture.proxy.stop()
    assert b" 200 " in response.split(b"\r\n", 1)[0]
    assert b"public clean body" in _body(response)
    assert fixture.hits == []


def test_accept_encoding_is_forced_to_identity(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/accept-encoding")
    finally:
        fixture.proxy.stop()
    assert _body(response) == b"identity"


def test_canary_in_response_is_not_forwarded(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/leak")
    finally:
        fixture.proxy.stop()
    assert b"public prefix " not in _body(response)
    assert fixture.hits
    host, rule, fingerprint_id = fixture.hits[0]
    assert host == "127.0.0.1"
    assert rule in ("literal", "kgram")
    assert fingerprint_id and SENTINEL.decode() not in fingerprint_id


def test_cross_chunk_sse_canary_is_detected(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/sse")
    finally:
        fixture.proxy.stop()
    assert SENTINEL not in response
    assert fixture.hits


def _dechunk(raw: bytes) -> tuple[bytes, bool]:
    """Decode a chunked body; the flag is True only for a fully framed message.

    Every chunk must be followed by CRLF and the body must end with ``0\\r\\n\\r\\n``.
    """
    out = b""
    while raw:
        size_line, sep, raw = raw.partition(b"\r\n")
        if not sep:
            return out, False
        try:
            size = int(size_line, 16)
        except ValueError:
            return out, False
        if size == 0:
            return out, raw == b"\r\n"
        if raw[size : size + 2] != b"\r\n":
            return out, False
        out, raw = out + raw[:size], raw[size + 2 :]
    return out, False


def test_chunked_stream_with_small_first_chunk_is_delivered_in_full(tmp_path, tls, fingerprints):
    """Hold-back must never emit an empty chunk: that is the HTTP/1.1 body terminator."""
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/sse-chunked")
    finally:
        fixture.proxy.stop()
    body, terminated = _dechunk(_body(response))
    assert body == (
        b"event: ping\n\n"
        + b"data: "
        + b"x" * 300
        + b"\n\n"
        + b"data: "
        + b"y" * 300
        + b"\n\n"
        + b"data: public tail\n\n"
    )
    assert terminated
    assert fixture.hits == []


def test_chunked_stream_canary_is_still_blocked(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/sse-chunked-leak")
    finally:
        fixture.proxy.stop()
    assert SENTINEL not in response
    assert fixture.hits


def test_chunked_stream_canary_split_across_chunks_leaks_no_prefix(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/sse-chunked-split")
    finally:
        fixture.proxy.stop()
    assert fixture.hits
    for length in range(8, len(SENTINEL) + 1):
        assert SENTINEL[:length] not in response
    body, _ = _dechunk(_body(response))
    assert SENTINEL[:8] not in body


def test_archives_forward_only_after_a_clean_recursive_scan(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        dirty = _tcp_get(fixture, "/leak-archive")
        clean = _tcp_get(fixture, "/clean-archive")
    finally:
        fixture.proxy.stop()
    assert SENTINEL not in dirty
    assert fixture.hits
    body = _body(clean)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.read("payload.txt") == b"public clean archive"


def test_encoded_response_is_blocked_with_a_catalogue_error(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/encoded")
    finally:
        fixture.proxy.stop()
    head, body = response.split(b"\r\n\r\n", 1)
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: dlp_blocked" in head.lower()
    payload = json.loads(body)
    assert payload["code"] == "ERROR_CODE_DLP_BLOCKED"
    assert payload["details"]["host"] == "127.0.0.1"
    assert SENTINEL.decode() not in body.decode()
    assert fixture.hits and fixture.hits[0][1] == "encoded"


def test_request_body_canary_is_blocked(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        response = _tcp_get(fixture, "/clean", method="POST", body=b"leak " + SENTINEL)
    finally:
        fixture.proxy.stop()
    head = response.split(b"\r\n\r\n", 1)[0]
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: dlp_blocked" in head.lower()
    assert fixture.hits


def test_server_side_tool_schema_is_rejected(tmp_path, tls, fingerprints):
    policy = EgressPolicy(
        allowed_hosts=frozenset({"127.0.0.1"}), model_hosts=frozenset({"127.0.0.1"})
    )
    fixture = _start_proxy(tmp_path, tls, fingerprints, policy)
    payload = json.dumps({"tools": [{"type": "web_search"}]}).encode()
    try:
        response = _tcp_get(fixture, "/clean", method="POST", body=payload)
    finally:
        fixture.proxy.stop()
    head, body = response.split(b"\r\n\r\n", 1)
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: egress_server_tool_denied" in head.lower()
    assert json.loads(body)["code"] == "ERROR_CODE_EGRESS_SERVER_TOOL_DENIED"
    assert fixture.hits == []


def test_disallowed_host_is_refused_before_any_upstream_connection(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        sock = socket.create_connection(("127.0.0.1", fixture.proxy.tcp_port), timeout=10)
        sock.sendall(b"CONNECT evil.test:443 HTTP/1.1\r\nHost: evil.test:443\r\n\r\n")
        response = sock.recv(8192)
        sock.close()
    finally:
        fixture.proxy.stop()
    assert b" 403 " in response.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: egress_domain_denied" in response.lower()


def test_archive_in_a_request_body_is_inspected(tmp_path, tls, fingerprints):
    fixture = _start_proxy(tmp_path, tls, fingerprints)
    try:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hidden.txt", b"prefix " + SENTINEL)
        response = _tcp_get(fixture, "/clean", method="POST", body=buffer.getvalue())
    finally:
        fixture.proxy.stop()
    head = response.split(b"\r\n\r\n", 1)[0]
    assert b" 403 " in head.split(b"\r\n", 1)[0]
    assert b"x-cheeseless-code: dlp_blocked" in head.lower()
    assert fixture.hits
