"""Contract tests for cheeseless.v1 (tests of the inbound proto adapter, not of the domain)."""

from collections.abc import Iterator

from google.protobuf import json_format
from google.protobuf.descriptor import Descriptor

from cheeseless.adapters.inbound.proto import error_pb2, result_pb2

REQUIRED_CODES = {
    "RUN_BUDGET_EXHAUSTED",
    "CRITERION_FAILED",
    "BUILD_FAILED",
    "PROMOTE_PATH_REJECTED",
    "SENSITIVE_FILE_NEEDS_APPROVAL",
    "EGRESS_DOMAIN_DENIED",
    "EGRESS_SERVER_TOOL_DENIED",
    "DLP_BLOCKED",
    "INFRA_ERROR",
    "RATE_LIMITED",
}
DETAILS_WHITELIST = {"criterion_id", "agent_written_path", "budget", "host"}
BANNED_RESULT_FIELD_PARTS = ("stdout", "stderr", "duration", "time", "holdout")


def test_defaults_fail_closed() -> None:
    assert result_pb2.Result().status == result_pb2.STATUS_UNSPECIFIED
    assert result_pb2.Result().status != result_pb2.STATUS_PASS
    assert error_pb2.Error().code == error_pb2.ERROR_CODE_UNSPECIFIED
    assert result_pb2.CriterionResult().status != result_pb2.STATUS_PASS


def test_zero_values_are_unspecified() -> None:
    status_zero = result_pb2.Status.Name(0)
    code_zero = error_pb2.ErrorCode.Name(0)
    assert status_zero.endswith("UNSPECIFIED")
    assert code_zero.endswith("UNSPECIFIED")
    assert result_pb2.STATUS_PASS != 0


def test_status_values() -> None:
    names = set(result_pb2.Status.keys())
    assert names == {"STATUS_UNSPECIFIED", "STATUS_PASS", "STATUS_FAIL", "STATUS_BLOCKED"}


def test_error_code_enum_has_required_codes() -> None:
    names = {n.removeprefix("ERROR_CODE_") for n in error_pb2.ErrorCode.keys()}
    assert REQUIRED_CODES <= names


def _reachable(root: Descriptor) -> Iterator[Descriptor]:
    seen: set[str] = set()
    stack = [root]
    while stack:
        message = stack.pop()
        if message.full_name in seen:
            continue
        seen.add(message.full_name)
        yield message
        stack.extend(f.message_type for f in message.fields if f.message_type is not None)


def test_result_has_no_oracle_fields_transitively() -> None:
    for message in _reachable(result_pb2.Result.DESCRIPTOR):
        for field in message.fields:
            assert not any(p in field.name for p in BANNED_RESULT_FIELD_PARTS), (
                f"{message.full_name}.{field.name}"
            )


def test_result_reaches_only_criterion_results() -> None:
    names = {m.full_name for m in _reachable(result_pb2.Result.DESCRIPTOR)}
    assert names == {"cheeseless.v1.Result", "cheeseless.v1.CriterionResult"}


def test_budget_counts_have_explicit_presence() -> None:
    budget = error_pb2.BudgetState()
    assert not budget.HasField("remaining")
    assert not budget.HasField("limit")
    assert error_pb2.BudgetState(remaining=0).HasField("remaining")


def test_criterion_result_is_id_and_status_only() -> None:
    names = {f.name for f in result_pb2.CriterionResult.DESCRIPTOR.fields}
    assert names == {"criterion_id", "status"}


def test_error_details_is_exactly_the_whitelist() -> None:
    names = {f.name for f in error_pb2.ErrorDetails.DESCRIPTOR.fields}
    assert names == DETAILS_WHITELIST


def test_no_untyped_containers_in_contract() -> None:
    for message in (error_pb2.Error, error_pb2.ErrorDetails, result_pb2.Result):
        for field in message.DESCRIPTOR.fields:
            if field.message_type is not None:
                assert field.message_type.full_name.startswith("cheeseless.v1.")
                assert not field.message_type.GetOptions().map_entry


def test_stderr_tail_is_named_for_its_stage() -> None:
    names = {f.name for f in error_pb2.Error.DESCRIPTOR.fields}
    assert "pre_hidden_stderr_tail" in names
    assert {"code", "reason", "hint", "retryable", "details", "docs"} <= names


def _populated_error() -> error_pb2.Error:
    return error_pb2.Error(
        code=error_pb2.ERROR_CODE_BUILD_FAILED,
        reason="build failed",
        hint="fix the import",
        retryable=True,
        details=error_pb2.ErrorDetails(
            criterion_id="c1",
            agent_written_path="src/x.py",
            budget=error_pb2.BudgetState(remaining=3, limit=30),
            host="example.org",
        ),
        docs="errors.md#build_failed",
        pre_hidden_stderr_tail="ImportError: nope",
    )


def test_error_protojson_round_trip() -> None:
    original = _populated_error()
    text = json_format.MessageToJson(original)
    assert json_format.Parse(text, error_pb2.Error()) == original


def test_result_protojson_round_trip() -> None:
    original = result_pb2.Result(
        task_id="sha256:abc",
        tree_hash="deadbeef",
        baseline_digest="sha256:def",
        profile="default",
        tool_version="0.0.0",
        status=result_pb2.STATUS_BLOCKED,
        criteria=[
            result_pb2.CriterionResult(criterion_id="c1", status=result_pb2.STATUS_PASS),
            result_pb2.CriterionResult(criterion_id="c2", status=result_pb2.STATUS_FAIL),
        ],
        hmac_tag=b"\x00\x01\xfe\xff",
    )
    text = json_format.MessageToJson(original)
    assert json_format.Parse(text, result_pb2.Result()) == original
