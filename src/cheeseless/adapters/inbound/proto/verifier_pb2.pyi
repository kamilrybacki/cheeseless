from . import error_pb2 as _error_pb2
from . import result_pb2 as _result_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DisputeCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DISPUTE_CODE_UNSPECIFIED: _ClassVar[DisputeCode]
    DISPUTE_CODE_CONTRADICTS_SPEC: _ClassVar[DisputeCode]
    DISPUTE_CODE_UNTESTABLE: _ClassVar[DisputeCode]
    DISPUTE_CODE_AMBIGUOUS_SPEC: _ClassVar[DisputeCode]
    DISPUTE_CODE_OTHER: _ClassVar[DisputeCode]
DISPUTE_CODE_UNSPECIFIED: DisputeCode
DISPUTE_CODE_CONTRADICTS_SPEC: DisputeCode
DISPUTE_CODE_UNTESTABLE: DisputeCode
DISPUTE_CODE_AMBIGUOUS_SPEC: DisputeCode
DISPUTE_CODE_OTHER: DisputeCode

class RunRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class RunResponse(_message.Message):
    __slots__ = ("result", "error", "criterion_failures")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CRITERION_FAILURES_FIELD_NUMBER: _ClassVar[int]
    result: _result_pb2.Result
    error: _error_pb2.Error
    criterion_failures: _containers.RepeatedCompositeFieldContainer[_error_pb2.Error]
    def __init__(self, result: _Optional[_Union[_result_pb2.Result, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ..., criterion_failures: _Optional[_Iterable[_Union[_error_pb2.Error, _Mapping]]] = ...) -> None: ...

class StatusRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class StatusResponse(_message.Message):
    __slots__ = ("result", "error", "criterion_failures")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CRITERION_FAILURES_FIELD_NUMBER: _ClassVar[int]
    result: _result_pb2.Result
    error: _error_pb2.Error
    criterion_failures: _containers.RepeatedCompositeFieldContainer[_error_pb2.Error]
    def __init__(self, result: _Optional[_Union[_result_pb2.Result, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ..., criterion_failures: _Optional[_Iterable[_Union[_error_pb2.Error, _Mapping]]] = ...) -> None: ...

class DisputeRequest(_message.Message):
    __slots__ = ("task_id", "criterion_id", "code", "note")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CRITERION_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    criterion_id: str
    code: DisputeCode
    note: str
    def __init__(self, task_id: _Optional[str] = ..., criterion_id: _Optional[str] = ..., code: _Optional[_Union[DisputeCode, str]] = ..., note: _Optional[str] = ...) -> None: ...

class DisputeAck(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class DisputeResponse(_message.Message):
    __slots__ = ("ack", "error")
    ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ack: DisputeAck
    error: _error_pb2.Error
    def __init__(self, ack: _Optional[_Union[DisputeAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...

class AuthorCheckRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class AuthorCheckAck(_message.Message):
    __slots__ = ("ok", "problems", "tests")
    OK_FIELD_NUMBER: _ClassVar[int]
    PROBLEMS_FIELD_NUMBER: _ClassVar[int]
    TESTS_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    problems: _containers.RepeatedScalarFieldContainer[str]
    tests: int
    def __init__(self, ok: _Optional[bool] = ..., problems: _Optional[_Iterable[str]] = ..., tests: _Optional[int] = ...) -> None: ...

class AuthorCheckResponse(_message.Message):
    __slots__ = ("check", "error")
    CHECK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    check: AuthorCheckAck
    error: _error_pb2.Error
    def __init__(self, check: _Optional[_Union[AuthorCheckAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...

class AuthorCrosscheckRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class AuthorCrosscheckAck(_message.Message):
    __slots__ = ("green", "red_criteria")
    GREEN_FIELD_NUMBER: _ClassVar[int]
    RED_CRITERIA_FIELD_NUMBER: _ClassVar[int]
    green: bool
    red_criteria: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, green: _Optional[bool] = ..., red_criteria: _Optional[_Iterable[str]] = ...) -> None: ...

class AuthorCrosscheckResponse(_message.Message):
    __slots__ = ("crosscheck", "error")
    CROSSCHECK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    crosscheck: AuthorCrosscheckAck
    error: _error_pb2.Error
    def __init__(self, crosscheck: _Optional[_Union[AuthorCrosscheckAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...

class FreezeProposeRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class FreezeProposeAck(_message.Message):
    __slots__ = ("proposal_id",)
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    proposal_id: str
    def __init__(self, proposal_id: _Optional[str] = ...) -> None: ...

class FreezeProposeResponse(_message.Message):
    __slots__ = ("proposal", "error")
    PROPOSAL_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    proposal: FreezeProposeAck
    error: _error_pb2.Error
    def __init__(self, proposal: _Optional[_Union[FreezeProposeAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...

class SpecDoneRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class SpecDoneAck(_message.Message):
    __slots__ = ("spec_digest",)
    SPEC_DIGEST_FIELD_NUMBER: _ClassVar[int]
    spec_digest: str
    def __init__(self, spec_digest: _Optional[str] = ...) -> None: ...

class SpecDoneResponse(_message.Message):
    __slots__ = ("ack", "error")
    ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ack: SpecDoneAck
    error: _error_pb2.Error
    def __init__(self, ack: _Optional[_Union[SpecDoneAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...

class ReferenceDoneRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class ReferenceDoneAck(_message.Message):
    __slots__ = ("attempt_id",)
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    def __init__(self, attempt_id: _Optional[str] = ...) -> None: ...

class ReferenceDoneResponse(_message.Message):
    __slots__ = ("ack", "error")
    ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ack: ReferenceDoneAck
    error: _error_pb2.Error
    def __init__(self, ack: _Optional[_Union[ReferenceDoneAck, _Mapping]] = ..., error: _Optional[_Union[_error_pb2.Error, _Mapping]] = ...) -> None: ...
