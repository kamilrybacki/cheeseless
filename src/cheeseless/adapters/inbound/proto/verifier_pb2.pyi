from . import error_pb2 as _error_pb2
from . import result_pb2 as _result_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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
