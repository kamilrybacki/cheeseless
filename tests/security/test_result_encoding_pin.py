"""Pin the v1 Result encoding, HMAC and proto bytes (ADR 0009 stage (g)).

A dispute is session state stored beside results, never a Result status, so these
golden values must not change when the dispute channel is added.
"""

import hashlib
from pathlib import Path

from cheeseless.adapters.inbound.proto import result_pb2
from cheeseless.adapters.outbound.store.codec import encode_result
from cheeseless.domain.result import CriterionResult, Result, canonical_bytes, sign
from cheeseless.domain.status import Status

KEY = bytes(range(32))
GOLDEN_TAG = "b596462218fbd177e5439defc7b22aaeb66f72cb5f24291b7465f39e81a9d990"
GOLDEN_JSON = (
    '{"baseline_digest":"' + "c" * 64 + '","criteria":[["normalize","pass"],["score","pass"]],'
    '"hmac":"' + GOLDEN_TAG + '","profile":"py","status":"pass","task_id":"' + "a" * 64 + '",'
    '"tool_version":"0.1","tree_hash":"' + "b" * 40 + '","version":1}'
)
GOLDEN_PROTO_SHA = "f790c08c5068e91c0851abc4f8c278eba1be4b7386a393b73fa7b4d22003704d"
GOLDEN_PB2_SHA = "fe1e43244f81b1fce14efe345260cc2f316ba370db6c938048bfe5a540d4db7a"


def _result() -> Result:
    crit = (CriterionResult("normalize", Status.PASS), CriterionResult("score", Status.PASS))
    return Result("a" * 64, "b" * 40, "c" * 64, "py", "0.1", Status.PASS, crit)


def test_hmac_tag_is_pinned() -> None:
    assert sign(_result(), KEY).hmac_tag.hex() == GOLDEN_TAG
    assert canonical_bytes(_result()).startswith(b"cheeseless.v1.Result\x00")


def test_stored_encoding_is_pinned() -> None:
    assert encode_result(sign(_result(), KEY)).decode() == GOLDEN_JSON


def test_result_status_has_no_disputed_member() -> None:
    assert "DISPUTED" not in Status.__members__
    assert "STATUS_DISPUTED" not in result_pb2.Status.keys()


def test_result_proto_bytes_are_pinned() -> None:
    proto = Path(__file__).parents[2] / "proto" / "cheeseless" / "v1" / "result.proto"
    assert hashlib.sha256(proto.read_bytes()).hexdigest() == GOLDEN_PROTO_SHA
    assert hashlib.sha256(result_pb2.DESCRIPTOR.serialized_pb).hexdigest() == GOLDEN_PB2_SHA
