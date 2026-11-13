"""Gate result, its canonical encoding and HMAC binding (Q43, P11).

The tag covers every field, including the verdict and the ordered criteria, so a
result cannot be flipped, trimmed or reordered without detection.
"""

import dataclasses
import hashlib
import hmac
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from cheeseless.domain import ids
from cheeseless.domain.status import Status

MIN_KEY_BYTES = 32
_DOMAIN = b"cheeseless.v1.Result\x00"
_ENCODING_VERSION = 1


@dataclass(frozen=True, slots=True)
class CriterionResult:
    criterion_id: str
    status: Status

    def __post_init__(self) -> None:
        ids.criterion_id(self.criterion_id)
        if self.status not in (Status.PASS, Status.FAIL):
            raise ValueError("criterion status must be PASS or FAIL")


@dataclass(frozen=True, slots=True)
class Result:
    task_id: str
    tree_hash: str
    baseline_digest: str
    profile: str
    tool_version: str
    status: Status
    criteria: tuple[CriterionResult, ...]
    hmac_tag: bytes = b""

    def __post_init__(self) -> None:
        ids.sha256_hex(self.task_id, "task id")
        ids.git_oid(self.tree_hash)
        ids.sha256_hex(self.baseline_digest, "baseline digest")
        ids.non_empty(self.profile, "profile")
        ids.non_empty(self.tool_version, "tool version")
        if not isinstance(self.status, Status):
            raise ValueError("status must be a Status")
        if not isinstance(self.hmac_tag, bytes):
            raise ValueError("hmac_tag must be bytes")
        if not isinstance(self.criteria, tuple) or not all(
            isinstance(c, CriterionResult) for c in self.criteria
        ):
            raise ValueError("criteria must be a tuple of CriterionResult")
        if len({c.criterion_id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate criterion id")
        self._check_verdict()

    def _check_verdict(self) -> None:
        if self.status is Status.BLOCKED:
            return
        if overall_status(self.criteria) is not self.status:
            raise ValueError(f"status {self.status.name} contradicts the criteria")


def overall_status(criteria: Iterable[CriterionResult]) -> Status:
    """PASS only if there is at least one criterion and every one passed."""
    items = tuple(criteria)
    if items and all(c.status is Status.PASS for c in items):
        return Status.PASS
    return Status.FAIL


def _field(value: str) -> bytes:
    raw = value.encode()
    return struct.pack(">I", len(raw)) + raw


def canonical_bytes(result: Result) -> bytes:
    """Unambiguous encoding of every field except the tag."""
    parts = [
        _DOMAIN,
        struct.pack(">I", _ENCODING_VERSION),
        _field(result.task_id),
        _field(result.tree_hash),
        _field(result.baseline_digest),
        _field(result.profile),
        _field(result.tool_version),
        _field(result.status.value),
        struct.pack(">I", len(result.criteria)),
    ]
    for c in result.criteria:
        parts.append(_field(c.criterion_id))
        parts.append(_field(c.status.value))
    return b"".join(parts)


def _tag(result: Result, key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
        raise ValueError(f"HMAC key must be at least {MIN_KEY_BYTES} bytes")
    return hmac.new(key, canonical_bytes(result), hashlib.sha256).digest()


def sign(result: Result, key: bytes) -> Result:
    return dataclasses.replace(result, hmac_tag=_tag(result, key))


def verify(result: Result, key: bytes) -> bool:
    if not result.hmac_tag:
        return False
    return hmac.compare_digest(result.hmac_tag, _tag(result, key))
