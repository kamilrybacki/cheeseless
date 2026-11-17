"""Client for the verifier UDS (used by the MCP adapter)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from google.protobuf import json_format  # type: ignore[import-untyped]

from cheeseless.adapters.inbound.proto import verifier_pb2
from cheeseless.adapters.inbound.uds.mapping import (
    MappingError,
    author_response_to_outcome,
    dispute_code_to_proto,
    dispute_response_to_outcome,
    run_response_to_agent_view,
    status_response_to_agent_view,
)
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.dispute import DisputeCode
from cheeseless.domain.errors import ErrorCode, GateError, gate_error

MAX_RESPONSE_BYTES = 1 << 20
MAX_HEADER_BYTES = 16 * 1024
MAX_LINE_BYTES = 8 * 1024


class UdsClientError(RuntimeError):
    """The verifier answered with something this client refuses to trust."""


class UdsVerifierClient:
    def __init__(
        self,
        socket_path: str | Path,
        task_id: str | None = None,
        timeout: float = 10.0,
        role: str = "implementer",
    ) -> None:
        self.role = role
        self.socket_path = Path(socket_path)
        self.task_id = task_id
        self.timeout = timeout

    def run(self) -> AgentView:
        data = self._post("/run", self._request_body())
        try:
            msg = verifier_pb2.RunResponse()
            json_format.Parse(data.decode("utf-8"), msg, ignore_unknown_fields=False)
            return run_response_to_agent_view(msg)
        except (MappingError, UnicodeDecodeError, ValueError, json_format.ParseError):
            return AgentView(gate_error(ErrorCode.INFRA_ERROR), ())

    def status(self) -> AgentView:
        data = self._post("/status", self._request_body())
        try:
            msg = verifier_pb2.StatusResponse()
            json_format.Parse(data.decode("utf-8"), msg, ignore_unknown_fields=False)
            return status_response_to_agent_view(msg)
        except (MappingError, UnicodeDecodeError, ValueError, json_format.ParseError):
            return AgentView(gate_error(ErrorCode.INFRA_ERROR), ())

    def dispute(self, criterion_id: str, code: DisputeCode, note: str) -> str | GateError:
        request = verifier_pb2.DisputeRequest(
            task_id=self.task_id or "",
            criterion_id=criterion_id,
            code=dispute_code_to_proto(code),
            note=note,
        )
        data = self._post("/dispute", json_format.MessageToJson(request).encode("utf-8"))
        try:
            msg = verifier_pb2.DisputeResponse()
            json_format.Parse(data.decode("utf-8"), msg, ignore_unknown_fields=False)
            return dispute_response_to_outcome(msg)
        except (MappingError, UnicodeDecodeError, ValueError, json_format.ParseError):
            return gate_error(ErrorCode.INFRA_ERROR)

    def author_check(self) -> dict[str, object] | GateError:
        return self._author("check", "/author-check", verifier_pb2.AuthorCheckResponse)

    def author_crosscheck(self) -> dict[str, object] | GateError:
        return self._author(
            "crosscheck", "/author-crosscheck", verifier_pb2.AuthorCrosscheckResponse
        )

    def freeze_propose(self) -> dict[str, object] | GateError:
        return self._author("propose", "/freeze-propose", verifier_pb2.FreezeProposeResponse)

    def _author(self, kind: str, path: str, response_type: Any) -> dict[str, object] | GateError:
        data = self._post(path, self._request_body())
        try:
            msg = response_type()
            json_format.Parse(data.decode("utf-8"), msg, ignore_unknown_fields=False)
            outcome = author_response_to_outcome(kind, msg)
        except (MappingError, UnicodeDecodeError, ValueError, json_format.ParseError):
            return gate_error(ErrorCode.INFRA_ERROR)
        return outcome  # type: ignore[return-value]

    def _request_body(self) -> bytes:
        if not self.task_id:
            return b"{}"
        return ('{"task_id":' + _json_string(self.task_id) + "}").encode("ascii")

    def _post(self, path: str, body: bytes) -> bytes:
        # The implementer's wire bytes stay unchanged; other roles claim theirs. The server
        # still checks the claim against its bound role and the trusted store.
        claim = "" if self.role == "implementer" else f"X-Cheeseless-Role: {self.role}\r\n"
        request = (
            f"POST {path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Content-Type: application/json\r\n"
            f"{claim}"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(str(self.socket_path))
                sock.sendall(request)
                raw = self._receive(sock)
        except OSError as exc:
            raise UdsClientError(str(exc)) from exc
        head, body_out = self._split_response(raw)
        headers = self._parse_headers(head)
        content_length = _content_length(headers)
        if content_length is None:
            raise UdsClientError("missing content-length")
        if len(body_out) != content_length:
            raise UdsClientError("truncated or oversized response")
        return body_out

    def _receive(self, sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES + MAX_HEADER_BYTES:
                raise UdsClientError("response too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _split_response(self, raw: bytes) -> tuple[str, bytes]:
        marker = b"\r\n\r\n"
        idx = raw.find(marker)
        if idx < 0:
            raise UdsClientError("malformed response")
        try:
            head = raw[:idx].decode("iso-8859-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 never fails
            raise UdsClientError("malformed response") from exc
        return head, raw[idx + len(marker) :]

    def _parse_headers(self, head: str) -> dict[str, str]:
        lines = head.split("\r\n")
        if not lines or len(lines[0].encode("iso-8859-1")) > MAX_LINE_BYTES:
            raise UdsClientError("malformed response")
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or status_parts[0] != "HTTP/1.1":
            raise UdsClientError("malformed status line")
        try:
            status_code = int(status_parts[1])
        except ValueError as exc:
            raise UdsClientError("malformed status code") from exc
        if status_code != 200:
            raise UdsClientError(lines[0])
        headers: dict[str, str] = {}
        total = len(lines[0])
        for line in lines[1:]:
            total += len(line.encode("iso-8859-1"))
            if total > MAX_HEADER_BYTES:
                raise UdsClientError("response headers too large")
            if not line:
                continue
            key, sep, value = line.partition(":")
            if not sep or not key.strip():
                raise UdsClientError("malformed header")
            name = key.strip().lower()
            if name in headers:
                raise UdsClientError(f"duplicate header: {name}")
            headers[name] = value.strip()
        return headers


def _content_length(headers: dict[str, str]) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise UdsClientError("bad content-length") from exc
    if value < 0 or value > MAX_RESPONSE_BYTES:
        raise UdsClientError("bad content-length")
    return value


def _json_string(value: str) -> str:
    return json.dumps(value)
