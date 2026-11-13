"""mitmproxy addon enforcing the egress rules and streaming DLP (ADR 0007).

The addon holds only the digest-only fingerprint set. It never logs or stores a
body, header or hidden match. Every response it generates itself is a catalogue
``cheeseless.v1.Error`` plus ``X-Cheeseless-Code``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mitmproxy import http

from cheeseless.domain.dlp import BodyScanner, DlpScanner, ScanHit, inspect_archive
from cheeseless.domain.egress_policy import Decision, EgressPolicy
from cheeseless.domain.errors import CATALOGUE, ErrorCode, ErrorDetails, docs_anchor

HitCallback = Callable[[str, str, str], None]


def _wire_code(code: ErrorCode) -> str:
    return f"ERROR_CODE_{code.name}"


def error_body(code: ErrorCode, host: str) -> bytes:
    entry = CATALOGUE[code]
    details: dict[str, str] = {}
    try:
        ErrorDetails(host=host)
    except ValueError:
        host = ""
    if host:
        details["host"] = host
    payload = {
        "code": _wire_code(code),
        "reason": entry.reason,
        "hint": entry.hint,
        "retryable": entry.retryable,
        "details": details,
        "docs": docs_anchor(code),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def error_response(code: ErrorCode, host: str) -> http.Response:
    headers = {
        "content-type": "application/json",
        "x-cheeseless-code": code.name,
        "cache-control": "no-store",
    }
    return http.Response.make(403, error_body(code, host), headers)


class EgressAddon:
    def __init__(
        self, policy: EgressPolicy, scanner: DlpScanner, on_hit: HitCallback | None = None
    ) -> None:
        self._policy = policy
        self._scanner = scanner
        self._on_hit = on_hit or (lambda host, rule, fingerprint: None)
        self._states: dict[str, BodyScanner] = {}

    # -- request side ---------------------------------------------------------

    def http_connect(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        try:
            decision = self._policy.check_connect(host)
        except Exception:  # noqa: BLE001 - fail closed
            flow.response = error_response(ErrorCode.DLP_BLOCKED, host)
            self._mark(flow)
            return
        if decision is not None:
            flow.response = error_response(decision.code, host)
            self._mark(flow)

    def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        try:
            self._request(flow)
        except Exception:  # noqa: BLE001 - a proxy bug must fail closed
            self._block(flow, host, ScanHit("protocol", "proxy-error"))

    def _request(self, flow: http.HTTPFlow) -> None:
        if self._already_blocked(flow):
            return
        request = flow.request
        host = request.pretty_host
        headers = dict(request.headers)
        try:
            body = request.get_content(strict=False) or b""
        except ValueError:
            self._deny(flow, host, ErrorCode.DLP_BLOCKED)
            return
        decision = self._policy.check_request(
            request.method, host, request.pretty_url, headers, body
        )
        if decision is not None:
            self._apply_decision(flow, host, decision)
            return
        hit = self._scan_parts((request.pretty_url, *headers.keys(), *headers.values()))
        if hit is not None:
            self._block(flow, host, hit)
            return
        if body:
            inspection = inspect_archive(body, self._scanner)
            if not inspection.clean:
                self._block(
                    flow,
                    host,
                    inspection.hit or ScanHit("archive", "archive-unsafe"),
                )
                return
        request.headers.pop("accept-encoding", None)
        request.headers["accept-encoding"] = "identity"

    # -- response side --------------------------------------------------------

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        try:
            self._responseheaders(flow)
        except Exception:  # noqa: BLE001 - a proxy bug must fail closed
            self._block(flow, host, ScanHit("protocol", "proxy-error"))

    def _responseheaders(self, flow: http.HTTPFlow) -> None:
        if self._already_blocked(flow) or flow.response is None:
            return
        response = flow.response
        host = flow.request.pretty_host
        decision = self._policy.check_response(host, dict(response.headers))
        if decision is not None:
            if decision.code is ErrorCode.EGRESS_DOMAIN_DENIED:
                self._set_error(flow, decision.code, host)
                return
            response.stream = False
            flow.metadata["cheeseless_pending"] = (ErrorCode.DLP_BLOCKED, decision.rule)
            return
        hit = self._scan_parts(
            (flow.request.pretty_url, *response.headers.keys(), *response.headers.values())
        )
        if hit is not None:
            self._block(flow, host, hit)
            return
        state = BodyScanner(self._scanner)
        self._states[flow.id] = state

        def stream(chunk: bytes) -> bytes:
            return self._stream(flow, state, chunk)

        response.stream = stream

    def response(self, flow: http.HTTPFlow) -> None:
        self._states.pop(flow.id, None)
        pending = flow.metadata.pop("cheeseless_pending", None)
        if pending is None:
            return
        code, rule = pending
        host = flow.request.pretty_host
        try:
            self._set_error(flow, code, host)
            self._on_hit(host, "encoded", rule)
        except Exception:  # noqa: BLE001 - never forward a pending block
            flow.kill()  # type: ignore[no-untyped-call]

    # -- helpers --------------------------------------------------------------

    def _stream(self, flow: http.HTTPFlow, state: BodyScanner, chunk: bytes) -> bytes:
        try:
            out = state.feed(chunk)
        except Exception:  # noqa: BLE001 - fail closed on a scanner bug
            state.fail()
            out = b""
        hit = state.hit
        if hit is not None:
            self._block(flow, flow.request.pretty_host, hit, streaming=True)
            return b""
        return out

    def _scan_parts(self, parts: tuple[Any, ...]) -> ScanHit | None:
        for part in parts:
            data = part if isinstance(part, bytes) else str(part).encode("utf-8", "replace")
            hit = self._scanner.scan(data)
            if hit is not None:
                return hit
        return None

    def _apply_decision(self, flow: http.HTTPFlow, host: str, decision: Decision) -> None:
        if decision.code is ErrorCode.DLP_BLOCKED:
            self._block(flow, host, ScanHit("protocol", decision.rule))
            return
        self._set_error(flow, decision.code, host)

    def _deny(self, flow: http.HTTPFlow, host: str, code: ErrorCode) -> None:
        self._set_error(flow, code, host)

    def _block(
        self, flow: http.HTTPFlow, host: str, hit: ScanHit, *, streaming: bool = False
    ) -> None:
        self._mark(flow)
        try:
            if streaming:
                flow.kill()  # type: ignore[no-untyped-call]
            else:
                self._set_error(flow, ErrorCode.DLP_BLOCKED, host)
        finally:
            self._on_hit(host, hit.rule, hit.fingerprint_id)

    def _set_error(self, flow: http.HTTPFlow, code: ErrorCode, host: str) -> None:
        body = error_body(code, host)
        response = flow.response
        if response is None:
            flow.response = error_response(code, host)
        else:
            response.status_code = 403
            response.reason = "Forbidden"
            response.headers.clear()
            response.content = body
            response.headers["content-type"] = "application/json"
            response.headers["content-length"] = str(len(body))
            response.headers["cache-control"] = "no-store"
            response.headers["x-cheeseless-code"] = code.name
            response.stream = False
        self._mark(flow)

    def _already_blocked(self, flow: http.HTTPFlow) -> bool:
        return bool(flow.metadata.get("cheeseless_blocked"))

    def _mark(self, flow: http.HTTPFlow) -> None:
        flow.metadata["cheeseless_blocked"] = True


__all__ = ["EgressAddon", "error_body", "error_response"]
