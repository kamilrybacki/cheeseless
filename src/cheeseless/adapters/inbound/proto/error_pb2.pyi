from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CODE_UNSPECIFIED: _ClassVar[ErrorCode]
    ERROR_CODE_RUN_BUDGET_EXHAUSTED: _ClassVar[ErrorCode]
    ERROR_CODE_CRITERION_FAILED: _ClassVar[ErrorCode]
    ERROR_CODE_BUILD_FAILED: _ClassVar[ErrorCode]
    ERROR_CODE_PROMOTE_PATH_REJECTED: _ClassVar[ErrorCode]
    ERROR_CODE_SENSITIVE_FILE_NEEDS_APPROVAL: _ClassVar[ErrorCode]
    ERROR_CODE_EGRESS_DOMAIN_DENIED: _ClassVar[ErrorCode]
    ERROR_CODE_EGRESS_SERVER_TOOL_DENIED: _ClassVar[ErrorCode]
    ERROR_CODE_DLP_BLOCKED: _ClassVar[ErrorCode]
    ERROR_CODE_INFRA_ERROR: _ClassVar[ErrorCode]
    ERROR_CODE_RATE_LIMITED: _ClassVar[ErrorCode]
    ERROR_CODE_IMPORT_FAILED: _ClassVar[ErrorCode]
    ERROR_CODE_EXECUTABLE_MISSING: _ClassVar[ErrorCode]
    ERROR_CODE_STARTUP_CRASH: _ClassVar[ErrorCode]
    ERROR_CODE_SMOKE_TEST_FAILED: _ClassVar[ErrorCode]
    ERROR_CODE_DISPUTE_DISABLED: _ClassVar[ErrorCode]
    ERROR_CODE_DISPUTE_CAP_REACHED: _ClassVar[ErrorCode]
    ERROR_CODE_DISPUTE_INVALID: _ClassVar[ErrorCode]
    ERROR_CODE_DISPUTE_NOT_ALLOWED: _ClassVar[ErrorCode]
    ERROR_CODE_SESSION_DISPUTED: _ClassVar[ErrorCode]
    ERROR_CODE_ROLE_NOT_ALLOWED: _ClassVar[ErrorCode]
    ERROR_CODE_REFERENCE_BLOCKED: _ClassVar[ErrorCode]
    ERROR_CODE_AUTHOR_REFUSED: _ClassVar[ErrorCode]
    ERROR_CODE_REFERENCE_REFUSED: _ClassVar[ErrorCode]
ERROR_CODE_UNSPECIFIED: ErrorCode
ERROR_CODE_RUN_BUDGET_EXHAUSTED: ErrorCode
ERROR_CODE_CRITERION_FAILED: ErrorCode
ERROR_CODE_BUILD_FAILED: ErrorCode
ERROR_CODE_PROMOTE_PATH_REJECTED: ErrorCode
ERROR_CODE_SENSITIVE_FILE_NEEDS_APPROVAL: ErrorCode
ERROR_CODE_EGRESS_DOMAIN_DENIED: ErrorCode
ERROR_CODE_EGRESS_SERVER_TOOL_DENIED: ErrorCode
ERROR_CODE_DLP_BLOCKED: ErrorCode
ERROR_CODE_INFRA_ERROR: ErrorCode
ERROR_CODE_RATE_LIMITED: ErrorCode
ERROR_CODE_IMPORT_FAILED: ErrorCode
ERROR_CODE_EXECUTABLE_MISSING: ErrorCode
ERROR_CODE_STARTUP_CRASH: ErrorCode
ERROR_CODE_SMOKE_TEST_FAILED: ErrorCode
ERROR_CODE_DISPUTE_DISABLED: ErrorCode
ERROR_CODE_DISPUTE_CAP_REACHED: ErrorCode
ERROR_CODE_DISPUTE_INVALID: ErrorCode
ERROR_CODE_DISPUTE_NOT_ALLOWED: ErrorCode
ERROR_CODE_SESSION_DISPUTED: ErrorCode
ERROR_CODE_ROLE_NOT_ALLOWED: ErrorCode
ERROR_CODE_REFERENCE_BLOCKED: ErrorCode
ERROR_CODE_AUTHOR_REFUSED: ErrorCode
ERROR_CODE_REFERENCE_REFUSED: ErrorCode

class BudgetState(_message.Message):
    __slots__ = ("remaining", "limit")
    REMAINING_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    remaining: int
    limit: int
    def __init__(self, remaining: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ErrorDetails(_message.Message):
    __slots__ = ("criterion_id", "agent_written_path", "budget", "host")
    CRITERION_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_WRITTEN_PATH_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    criterion_id: str
    agent_written_path: str
    budget: BudgetState
    host: str
    def __init__(self, criterion_id: _Optional[str] = ..., agent_written_path: _Optional[str] = ..., budget: _Optional[_Union[BudgetState, _Mapping]] = ..., host: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("code", "reason", "hint", "retryable", "details", "docs", "pre_hidden_stderr_tail")
    CODE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    DOCS_FIELD_NUMBER: _ClassVar[int]
    PRE_HIDDEN_STDERR_TAIL_FIELD_NUMBER: _ClassVar[int]
    code: ErrorCode
    reason: str
    hint: str
    retryable: bool
    details: ErrorDetails
    docs: str
    pre_hidden_stderr_tail: str
    def __init__(self, code: _Optional[_Union[ErrorCode, str]] = ..., reason: _Optional[str] = ..., hint: _Optional[str] = ..., retryable: _Optional[bool] = ..., details: _Optional[_Union[ErrorDetails, _Mapping]] = ..., docs: _Optional[str] = ..., pre_hidden_stderr_tail: _Optional[str] = ...) -> None: ...
