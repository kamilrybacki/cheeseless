"""Strict mapping between cheeseless.v1 protobuf and domain types."""

from __future__ import annotations

from typing import Any

from cheeseless.adapters.inbound.proto import error_pb2, result_pb2, verifier_pb2
from cheeseless.domain.disclosure import AgentView
from cheeseless.domain.dispute import ACK_TEXT, DisputeCode
from cheeseless.domain.errors import (
    CATALOGUE,
    ErrorCode,
    ErrorDetails,
    GateError,
    docs_anchor,
)
from cheeseless.domain.errors import (
    BudgetState as DomainBudgetState,
)
from cheeseless.domain.result import CriterionResult, Result
from cheeseless.domain.status import Status

_ERROR_CODE_TO_PROTO = {
    ErrorCode.RUN_BUDGET_EXHAUSTED: error_pb2.ERROR_CODE_RUN_BUDGET_EXHAUSTED,
    ErrorCode.CRITERION_FAILED: error_pb2.ERROR_CODE_CRITERION_FAILED,
    ErrorCode.BUILD_FAILED: error_pb2.ERROR_CODE_BUILD_FAILED,
    ErrorCode.PROMOTE_PATH_REJECTED: error_pb2.ERROR_CODE_PROMOTE_PATH_REJECTED,
    ErrorCode.SENSITIVE_FILE_NEEDS_APPROVAL: error_pb2.ERROR_CODE_SENSITIVE_FILE_NEEDS_APPROVAL,
    ErrorCode.EGRESS_DOMAIN_DENIED: error_pb2.ERROR_CODE_EGRESS_DOMAIN_DENIED,
    ErrorCode.EGRESS_SERVER_TOOL_DENIED: error_pb2.ERROR_CODE_EGRESS_SERVER_TOOL_DENIED,
    ErrorCode.DLP_BLOCKED: error_pb2.ERROR_CODE_DLP_BLOCKED,
    ErrorCode.INFRA_ERROR: error_pb2.ERROR_CODE_INFRA_ERROR,
    ErrorCode.RATE_LIMITED: error_pb2.ERROR_CODE_RATE_LIMITED,
    ErrorCode.IMPORT_FAILED: error_pb2.ERROR_CODE_IMPORT_FAILED,
    ErrorCode.EXECUTABLE_MISSING: error_pb2.ERROR_CODE_EXECUTABLE_MISSING,
    ErrorCode.STARTUP_CRASH: error_pb2.ERROR_CODE_STARTUP_CRASH,
    ErrorCode.SMOKE_TEST_FAILED: error_pb2.ERROR_CODE_SMOKE_TEST_FAILED,
    ErrorCode.DISPUTE_DISABLED: error_pb2.ERROR_CODE_DISPUTE_DISABLED,
    ErrorCode.DISPUTE_CAP_REACHED: error_pb2.ERROR_CODE_DISPUTE_CAP_REACHED,
    ErrorCode.DISPUTE_INVALID: error_pb2.ERROR_CODE_DISPUTE_INVALID,
    ErrorCode.DISPUTE_NOT_ALLOWED: error_pb2.ERROR_CODE_DISPUTE_NOT_ALLOWED,
    ErrorCode.SESSION_DISPUTED: error_pb2.ERROR_CODE_SESSION_DISPUTED,
    ErrorCode.ROLE_NOT_ALLOWED: error_pb2.ERROR_CODE_ROLE_NOT_ALLOWED,
    ErrorCode.REFERENCE_BLOCKED: error_pb2.ERROR_CODE_REFERENCE_BLOCKED,
    ErrorCode.AUTHOR_REFUSED: error_pb2.ERROR_CODE_AUTHOR_REFUSED,
    ErrorCode.REFERENCE_REFUSED: error_pb2.ERROR_CODE_REFERENCE_REFUSED,
}

_PROTO_TO_ERROR_CODE = {v: k for k, v in _ERROR_CODE_TO_PROTO.items()}

_STATUS_TO_PROTO = {
    Status.PASS: result_pb2.STATUS_PASS,
    Status.FAIL: result_pb2.STATUS_FAIL,
    Status.BLOCKED: result_pb2.STATUS_BLOCKED,
}
_PROTO_TO_STATUS = {v: k for k, v in _STATUS_TO_PROTO.items()}


class MappingError(ValueError):
    """Raised when a protobuf message cannot be mapped safely."""


def budget_state_to_proto(budget: DomainBudgetState | None) -> error_pb2.BudgetState:
    msg = error_pb2.BudgetState()
    if budget is None:
        return msg
    if budget.remaining is not None:
        msg.remaining = budget.remaining
    if budget.limit is not None:
        msg.limit = budget.limit
    return msg


def budget_state_from_proto(msg: error_pb2.BudgetState) -> DomainBudgetState | None:
    remaining = msg.remaining if msg.HasField("remaining") else None
    limit = msg.limit if msg.HasField("limit") else None
    return DomainBudgetState(remaining=remaining, limit=limit)


def error_details_to_proto(details: ErrorDetails) -> error_pb2.ErrorDetails:
    msg = error_pb2.ErrorDetails()
    msg.criterion_id = details.criterion_id
    msg.agent_written_path = details.agent_written_path
    if details.budget is not None:
        msg.budget.CopyFrom(budget_state_to_proto(details.budget))
    msg.host = details.host
    return msg


def error_details_from_proto(msg: error_pb2.ErrorDetails) -> ErrorDetails:
    budget = budget_state_from_proto(msg.budget) if msg.HasField("budget") else None
    return ErrorDetails(
        criterion_id=msg.criterion_id,
        agent_written_path=msg.agent_written_path,
        budget=budget,
        host=msg.host,
    )


def gate_error_to_proto(err: GateError) -> error_pb2.Error:
    msg = error_pb2.Error()
    if err.code not in _ERROR_CODE_TO_PROTO:
        raise MappingError(f"unknown error code {err.code}")
    msg.code = _ERROR_CODE_TO_PROTO[err.code]
    if msg.code == error_pb2.ERROR_CODE_UNSPECIFIED:
        raise MappingError("UNSPECIFIED error code is not allowed")
    msg.reason = err.reason
    msg.hint = err.hint
    msg.retryable = err.retryable
    msg.details.CopyFrom(error_details_to_proto(err.details))
    msg.docs = err.docs
    msg.pre_hidden_stderr_tail = err.pre_hidden_stderr_tail
    return msg


def gate_error_from_proto(msg: error_pb2.Error) -> GateError:
    if msg.code == error_pb2.ERROR_CODE_UNSPECIFIED or msg.code not in _PROTO_TO_ERROR_CODE:
        raise MappingError("unknown or unspecified error code")
    code = _PROTO_TO_ERROR_CODE[msg.code]
    details = error_details_from_proto(msg.details)
    # Validate catalogue text by enforcing the same invariants GateError enforces.

    entry = CATALOGUE[code]
    if msg.hint != entry.hint:
        raise MappingError("hint does not match catalogue")
    if msg.retryable is not entry.retryable:
        raise MappingError("retryable does not match catalogue")
    if msg.docs != docs_anchor(code):
        raise MappingError("docs anchor does not match catalogue")
    reason = msg.reason
    if reason != entry.reason and not (
        code is ErrorCode.CRITERION_FAILED and reason.startswith(entry.reason + " Criterion: ")
    ):
        raise MappingError("reason does not match catalogue")
    if msg.pre_hidden_stderr_tail and code not in {
        ErrorCode.BUILD_FAILED,
        ErrorCode.IMPORT_FAILED,
        ErrorCode.EXECUTABLE_MISSING,
        ErrorCode.STARTUP_CRASH,
        ErrorCode.SMOKE_TEST_FAILED,
    }:
        raise MappingError("stderr tail on a non-pre-hidden code")
    # Reconstruct via GateError with validation by using dataclass replace trick is not possible;
    # validate by constructing directly (GateError.__post_init__ enforces the same rules).
    return GateError(
        code=code,
        reason=reason,
        hint=msg.hint,
        retryable=msg.retryable,
        details=details,
        docs=msg.docs,
        pre_hidden_stderr_tail=msg.pre_hidden_stderr_tail,
    )


def result_to_proto(result: Result) -> result_pb2.Result:
    msg = result_pb2.Result()
    msg.task_id = result.task_id
    msg.tree_hash = result.tree_hash
    msg.baseline_digest = result.baseline_digest
    msg.profile = result.profile
    msg.tool_version = result.tool_version
    if result.status not in _STATUS_TO_PROTO:
        raise MappingError(f"unknown status {result.status}")
    msg.status = _STATUS_TO_PROTO[result.status]
    if msg.status == result_pb2.STATUS_UNSPECIFIED:
        raise MappingError("UNSPECIFIED status is not allowed")
    for c in result.criteria:
        cr = msg.criteria.add()
        cr.criterion_id = c.criterion_id
        if c.status not in _STATUS_TO_PROTO:
            raise MappingError(f"unknown criterion status {c.status}")
        cr.status = _STATUS_TO_PROTO[c.status]
        if cr.status == result_pb2.STATUS_UNSPECIFIED:
            raise MappingError("UNSPECIFIED criterion status is not allowed")
    msg.hmac_tag = result.hmac_tag
    return msg


def result_from_proto(msg: result_pb2.Result) -> Result:
    if msg.status == result_pb2.STATUS_UNSPECIFIED or msg.status not in _PROTO_TO_STATUS:
        raise MappingError("unknown or unspecified status")
    status = _PROTO_TO_STATUS[msg.status]
    if not msg.task_id:
        raise MappingError("missing task_id")
    if not msg.tree_hash:
        raise MappingError("missing tree_hash")
    if not msg.baseline_digest:
        raise MappingError("missing baseline_digest")
    criteria = []
    for c in msg.criteria:
        if not c.criterion_id:
            raise MappingError("missing criterion_id")
        if c.status == result_pb2.STATUS_UNSPECIFIED or c.status not in _PROTO_TO_STATUS:
            raise MappingError("unknown or unspecified criterion status")
        criteria.append(CriterionResult(c.criterion_id, _PROTO_TO_STATUS[c.status]))

    return Result(
        task_id=msg.task_id,
        tree_hash=msg.tree_hash,
        baseline_digest=msg.baseline_digest,
        profile=msg.profile,
        tool_version=msg.tool_version,
        status=status,
        criteria=tuple(criteria),
        hmac_tag=bytes(msg.hmac_tag),
    )


def agent_view_to_run_response(view: AgentView) -> verifier_pb2.RunResponse:
    resp = verifier_pb2.RunResponse()
    outcome = view.outcome
    if isinstance(outcome, Result):
        resp.result.CopyFrom(result_to_proto(outcome))
    else:
        resp.error.CopyFrom(gate_error_to_proto(outcome))
    for failure in view.failures:
        resp.criterion_failures.append(gate_error_to_proto(failure))
    return resp


def agent_view_to_status_response(view: AgentView) -> verifier_pb2.StatusResponse:
    resp = verifier_pb2.StatusResponse()
    outcome = view.outcome
    if isinstance(outcome, Result):
        resp.result.CopyFrom(result_to_proto(outcome))
    else:
        resp.error.CopyFrom(gate_error_to_proto(outcome))
    for failure in view.failures:
        resp.criterion_failures.append(gate_error_to_proto(failure))
    return resp


def _validate_criterion_failures(view_outcome: object, failures: tuple[GateError, ...]) -> None:
    if isinstance(view_outcome, Result):
        failed_ids = {c.criterion_id for c in view_outcome.criteria if c.status is Status.FAIL}
        for f in failures:
            if f.code is not ErrorCode.CRITERION_FAILED:
                raise MappingError("criterion_failures must be CRITERION_FAILED")
            if f.details.criterion_id not in failed_ids:
                raise MappingError("criterion_failures must name a FAIL criterion")
    else:
        if failures:
            raise MappingError("criterion_failures require a Result outcome")


def run_response_to_agent_view(msg: verifier_pb2.RunResponse) -> AgentView:
    which = msg.WhichOneof("outcome")
    if which not in ("result", "error"):
        raise MappingError("missing outcome oneof")
    failures = tuple(gate_error_from_proto(e) for e in msg.criterion_failures)
    if which == "result":
        result = result_from_proto(msg.result)
        _validate_criterion_failures(result, failures)
        return AgentView(outcome=result, failures=failures)
    err = gate_error_from_proto(msg.error)
    if failures:
        raise MappingError("criterion_failures require a Result outcome")
    return AgentView(outcome=err, failures=())


def status_response_to_agent_view(msg: verifier_pb2.StatusResponse) -> AgentView:
    which = msg.WhichOneof("outcome")
    if which not in ("result", "error"):
        raise MappingError("missing outcome oneof")
    failures = tuple(gate_error_from_proto(e) for e in msg.criterion_failures)
    if which == "result":
        result = result_from_proto(msg.result)
        _validate_criterion_failures(result, failures)
        return AgentView(outcome=result, failures=failures)
    err = gate_error_from_proto(msg.error)
    if failures:
        raise MappingError("criterion_failures require a Result outcome")
    return AgentView(outcome=err, failures=())


def run_request_to_task_id(msg: verifier_pb2.RunRequest) -> str:
    # task_id is optional; empty means use session-configured task
    return msg.task_id


def status_request_to_task_id(msg: verifier_pb2.StatusRequest) -> str:
    return msg.task_id


def parse_run_request(body: bytes) -> verifier_pb2.RunRequest:
    from google.protobuf import json_format  # type: ignore[import-untyped]

    msg = verifier_pb2.RunRequest()
    text = body.decode("utf-8")
    if len(text) > 65536:
        raise MappingError("request too large")
    json_format.Parse(text, msg, ignore_unknown_fields=False)
    return msg


def parse_status_request(body: bytes) -> verifier_pb2.StatusRequest:
    from google.protobuf import json_format

    msg = verifier_pb2.StatusRequest()
    text = body.decode("utf-8")
    if len(text) > 65536:
        raise MappingError("request too large")
    json_format.Parse(text, msg, ignore_unknown_fields=False)
    return msg


def serialize_run_response(msg: verifier_pb2.RunResponse) -> bytes:
    from google.protobuf import json_format

    return json_format.MessageToJson(msg).encode("utf-8")  # type: ignore[no-any-return]


def serialize_status_response(msg: verifier_pb2.StatusResponse) -> bytes:
    from google.protobuf import json_format

    return json_format.MessageToJson(msg).encode("utf-8")  # type: ignore[no-any-return]


_DISPUTE_CODE_TO_PROTO: dict[DisputeCode, verifier_pb2.DisputeCode] = {
    DisputeCode.CONTRADICTS_SPEC: verifier_pb2.DISPUTE_CODE_CONTRADICTS_SPEC,
    DisputeCode.UNTESTABLE: verifier_pb2.DISPUTE_CODE_UNTESTABLE,
    DisputeCode.AMBIGUOUS_SPEC: verifier_pb2.DISPUTE_CODE_AMBIGUOUS_SPEC,
    DisputeCode.OTHER: verifier_pb2.DISPUTE_CODE_OTHER,
}
_DISPUTE_CODE_FROM_PROTO = {v: k for k, v in _DISPUTE_CODE_TO_PROTO.items()}


def dispute_code_to_proto(code: DisputeCode) -> verifier_pb2.DisputeCode:
    return _DISPUTE_CODE_TO_PROTO[code]


def parse_dispute_request(body: bytes) -> verifier_pb2.DisputeRequest:
    from google.protobuf import json_format

    msg = verifier_pb2.DisputeRequest()
    text = body.decode("utf-8")
    if len(text) > 65536:
        raise MappingError("request too large")
    try:
        json_format.Parse(text, msg, ignore_unknown_fields=False)
    except json_format.ParseError as exc:
        raise MappingError("malformed dispute request") from exc
    return msg


def dispute_request_fields(msg: verifier_pb2.DisputeRequest) -> tuple[str, str, str, str]:
    """``(task_id, criterion_id, code, note)`` as plain strings for the use case.

    An unspecified or unknown enum value maps to ``""``, which the use case rejects
    as invalid input like any other malformed code.
    """
    code = _DISPUTE_CODE_FROM_PROTO.get(msg.code)
    return msg.task_id, msg.criterion_id, code.value if code else "", msg.note


def dispute_outcome_to_response(outcome: str | GateError) -> verifier_pb2.DisputeResponse:
    resp = verifier_pb2.DisputeResponse()
    if isinstance(outcome, GateError):
        resp.error.CopyFrom(gate_error_to_proto(outcome))
    else:
        # Fixed text, never the caller's input and never per-dispute data.
        resp.ack.message = ACK_TEXT
    return resp


def dispute_response_to_outcome(msg: verifier_pb2.DisputeResponse) -> str | GateError:
    which = msg.WhichOneof("outcome")
    if which == "ack":
        if msg.ack.message != ACK_TEXT:
            raise MappingError("unexpected acknowledgement")
        return ACK_TEXT
    if which == "error":
        return gate_error_from_proto(msg.error)
    raise MappingError("missing outcome oneof")


def serialize_dispute_response(msg: verifier_pb2.DisputeResponse) -> bytes:
    from google.protobuf import json_format

    return json_format.MessageToJson(msg).encode("utf-8")  # type: ignore[no-any-return]


# -- author role (ADR 0009, 0013): check, crosscheck, propose, spec_done --------------

_AUTHOR_REQUESTS = {
    "check": verifier_pb2.AuthorCheckRequest,
    "crosscheck": verifier_pb2.AuthorCrosscheckRequest,
    "propose": verifier_pb2.FreezeProposeRequest,
    "spec_done": verifier_pb2.SpecDoneRequest,
    "reference_done": verifier_pb2.ReferenceDoneRequest,
}
_AUTHOR_RESPONSES = {
    "check": verifier_pb2.AuthorCheckResponse,
    "crosscheck": verifier_pb2.AuthorCrosscheckResponse,
    "propose": verifier_pb2.FreezeProposeResponse,
    "spec_done": verifier_pb2.SpecDoneResponse,
    "reference_done": verifier_pb2.ReferenceDoneResponse,
}


def parse_author_request(kind: str, body: bytes) -> str:
    """The ``task_id`` of an author request; every other field is rejected."""
    from google.protobuf import json_format

    msg = _AUTHOR_REQUESTS[kind]()
    text = body.decode("utf-8")
    if len(text) > 65536:
        raise MappingError("request too large")
    try:
        json_format.Parse(text, msg, ignore_unknown_fields=False)
    except json_format.ParseError as exc:
        raise MappingError("malformed author request") from exc
    return str(msg.task_id)  # type: ignore[attr-defined]


def author_outcome_to_response(kind: str, outcome: object) -> Any:
    """Map an app outcome (or a ``GateError``) to the proto response for ``kind``."""
    resp = _AUTHOR_RESPONSES[kind]()
    if isinstance(outcome, GateError):
        resp.error.CopyFrom(gate_error_to_proto(outcome))  # type: ignore[attr-defined]
    elif kind == "check":
        resp.check.ok = outcome.ok  # type: ignore[attr-defined]
        resp.check.problems.extend(outcome.problems)  # type: ignore[attr-defined]
        resp.check.tests = outcome.tests  # type: ignore[attr-defined]
    elif kind == "crosscheck":
        resp.crosscheck.green = outcome.green  # type: ignore[attr-defined]
        resp.crosscheck.red_criteria.extend(outcome.red_criteria)  # type: ignore[attr-defined]
    elif kind == "spec_done":
        resp.ack.spec_digest = outcome.spec_digest  # type: ignore[attr-defined]
    elif kind == "reference_done":
        resp.ack.attempt_id = outcome.attempt_id  # type: ignore[attr-defined]
    else:
        resp.proposal.proposal_id = outcome.proposal_id  # type: ignore[attr-defined]
    return resp


def author_response_to_outcome(kind: str, msg: Any) -> object:
    which = msg.WhichOneof("outcome")
    if which == "error":
        return gate_error_from_proto(msg.error)
    if which == "check" and kind == "check":
        return {"ok": msg.check.ok, "problems": list(msg.check.problems), "tests": msg.check.tests}
    if which == "crosscheck" and kind == "crosscheck":
        return {"green": msg.crosscheck.green, "red_criteria": list(msg.crosscheck.red_criteria)}
    if which == "proposal" and kind == "propose":
        return {"proposal_id": msg.proposal.proposal_id}
    if which == "ack" and kind == "spec_done":
        return {"spec_digest": msg.ack.spec_digest}
    if which == "ack" and kind == "reference_done":
        return {"attempt_id": msg.ack.attempt_id}
    raise MappingError("missing or unexpected outcome oneof")


def serialize_author_response(msg: Any) -> bytes:
    from google.protobuf import json_format

    return json_format.MessageToJson(msg).encode("utf-8")  # type: ignore[no-any-return]
