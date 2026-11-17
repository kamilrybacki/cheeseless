"""The pane relay wire format: one type byte, a 4-byte length, then the payload."""

import pytest

from cheeseless.domain.pane_frames import (
    MAX_PAYLOAD,
    Data,
    FrameDecoder,
    FrameError,
    Resize,
    encode_data,
    encode_resize,
)


def test_data_round_trips():
    assert FrameDecoder().feed(encode_data(b"hello")) == [Data(b"hello")]


def test_resize_round_trips():
    assert FrameDecoder().feed(encode_resize(40, 100)) == [Resize(40, 100)]


def test_frames_split_at_any_byte_decode_the_same():
    wire = encode_data(b"abc") + encode_resize(24, 80) + encode_data(b"")
    decoder = FrameDecoder()
    frames = [f for i in range(len(wire)) for f in decoder.feed(wire[i : i + 1])]
    assert frames == [Data(b"abc"), Resize(24, 80), Data(b"")]


def test_several_frames_in_one_chunk():
    wire = encode_data(b"a") + encode_data(b"b")
    assert FrameDecoder().feed(wire) == [Data(b"a"), Data(b"b")]


def test_unknown_type_is_refused():
    with pytest.raises(FrameError, match="unknown"):
        FrameDecoder().feed(b"\x09\x00\x00\x00\x00")


def test_oversized_length_is_refused_before_the_payload_arrives():
    header = b"\x00" + (MAX_PAYLOAD + 1).to_bytes(4, "big")
    with pytest.raises(FrameError, match="too large"):
        FrameDecoder().feed(header)


def test_a_resize_with_the_wrong_length_is_refused():
    with pytest.raises(FrameError, match="resize"):
        FrameDecoder().feed(b"\x01\x00\x00\x00\x03abc")


def test_encode_data_refuses_an_oversized_payload():
    with pytest.raises(FrameError):
        encode_data(b"x" * (MAX_PAYLOAD + 1))


@pytest.mark.parametrize("rows,cols", [(-1, 80), (24, 70000), (0, 0)])
def test_encode_resize_refuses_out_of_range_sizes(rows, cols):
    if (rows, cols) == (0, 0):
        assert FrameDecoder().feed(encode_resize(0, 0)) == [Resize(0, 0)]
    else:
        with pytest.raises(FrameError):
            encode_resize(rows, cols)
