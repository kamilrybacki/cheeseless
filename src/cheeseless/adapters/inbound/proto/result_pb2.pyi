from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STATUS_UNSPECIFIED: _ClassVar[Status]
    STATUS_PASS: _ClassVar[Status]
    STATUS_FAIL: _ClassVar[Status]
    STATUS_BLOCKED: _ClassVar[Status]
STATUS_UNSPECIFIED: Status
STATUS_PASS: Status
STATUS_FAIL: Status
STATUS_BLOCKED: Status

class CriterionResult(_message.Message):
    __slots__ = ("criterion_id", "status")
    CRITERION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    criterion_id: str
    status: Status
    def __init__(self, criterion_id: _Optional[str] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...

class Result(_message.Message):
    __slots__ = ("task_id", "tree_hash", "baseline_digest", "profile", "tool_version", "status", "criteria", "hmac_tag")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TREE_HASH_FIELD_NUMBER: _ClassVar[int]
    BASELINE_DIGEST_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    TOOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CRITERIA_FIELD_NUMBER: _ClassVar[int]
    HMAC_TAG_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    tree_hash: str
    baseline_digest: str
    profile: str
    tool_version: str
    status: Status
    criteria: _containers.RepeatedCompositeFieldContainer[CriterionResult]
    hmac_tag: bytes
    def __init__(self, task_id: _Optional[str] = ..., tree_hash: _Optional[str] = ..., baseline_digest: _Optional[str] = ..., profile: _Optional[str] = ..., tool_version: _Optional[str] = ..., status: _Optional[_Union[Status, str]] = ..., criteria: _Optional[_Iterable[_Union[CriterionResult, _Mapping]]] = ..., hmac_tag: _Optional[bytes] = ...) -> None: ...
