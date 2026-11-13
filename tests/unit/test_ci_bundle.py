import base64
import json

import pytest

from cheeseless.ci_bundle import BundleError, decode_bundle, encode_bundle
from tests.factories import frozen_task

KEY = bytes(range(32))
SEED = bytes(range(32))


def _frozen():
    return frozen_task(), SEED


def _roundtrip_parts():
    frozen, seed = _frozen()
    return frozen, seed, encode_bundle(frozen, seed, KEY, ci_limit=5)


def test_round_trip():
    frozen, seed, raw = _roundtrip_parts()
    bundle = decode_bundle(raw)
    assert bundle.frozen == frozen
    assert bundle.seed == seed
    assert bundle.key == KEY
    assert bundle.ci_limit == 5


def test_short_key_refused_on_encode():
    frozen, seed = _frozen()
    with pytest.raises(BundleError):
        encode_bundle(frozen, seed, b"short", ci_limit=5)


def _mutate(raw, fn):
    data = json.loads(raw)
    fn(data)
    return json.dumps(data).encode()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(key=base64.b64encode(b"x" * 8).decode()),
        lambda d: d.update(ci_limit=0),
        lambda d: d.update(ci_limit="5"),
        lambda d: d.update(extra=1),
        lambda d: d.pop("mac"),
        lambda d: d.update(lineage="other"),
        lambda d: d["task"]["visible"].update({"x::y": "c"}),
        lambda d: d.pop("task"),
    ],
)
def test_tampered_bundle_refused(mutation):
    _, _, raw = _roundtrip_parts()
    with pytest.raises(BundleError):
        decode_bundle(_mutate(raw, mutation))


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b"\xff\xfe"])
def test_garbage_refused(raw):
    with pytest.raises(BundleError):
        decode_bundle(raw)
