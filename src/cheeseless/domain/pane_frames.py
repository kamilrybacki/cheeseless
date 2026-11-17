"""Wire format of the reference-pane relay (ADR 0014).

A frame is one type byte, a 4-byte big-endian payload length and the payload. There are
two types: ``DATA`` (raw terminal bytes, either direction) and ``RESIZE`` (client to relay:
rows and columns as two unsigned 16-bit integers). The decoder is incremental, bounds the
length before it buffers a payload, and never guesses: an unknown type or a bad length is a
:class:`FrameError`, after which the peer must be dropped.
"""

import struct
from dataclasses import dataclass

DATA = 0
RESIZE = 1
MAX_PAYLOAD = 64 * 1024
_HEADER = struct.Struct(">BI")
_SIZE = struct.Struct(">HH")
_MAX_DIM = 0xFFFF


class FrameError(ValueError):
    """The byte stream is not a valid relay stream."""


@dataclass(frozen=True, slots=True)
class Data:
    payload: bytes


@dataclass(frozen=True, slots=True)
class Resize:
    rows: int
    cols: int


Frame = Data | Resize


def encode_data(payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise FrameError("data payload too large")
    return _HEADER.pack(DATA, len(payload)) + payload


def encode_resize(rows: int, cols: int) -> bytes:
    if not (0 <= rows <= _MAX_DIM and 0 <= cols <= _MAX_DIM):
        raise FrameError("resize out of range")
    body = _SIZE.pack(rows, cols)
    return _HEADER.pack(RESIZE, len(body)) + body


class FrameDecoder:
    """Feed received bytes; get back the complete frames they finish."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf.extend(chunk)
        frames: list[Frame] = []
        while len(self._buf) >= _HEADER.size:
            kind, length = _HEADER.unpack_from(self._buf)
            if kind not in (DATA, RESIZE):
                raise FrameError(f"unknown frame type {kind}")
            if length > MAX_PAYLOAD:
                raise FrameError("frame too large")
            if len(self._buf) < _HEADER.size + length:
                break
            body = bytes(self._buf[_HEADER.size : _HEADER.size + length])
            del self._buf[: _HEADER.size + length]
            frames.append(self._frame(kind, body))
        return frames

    @staticmethod
    def _frame(kind: int, body: bytes) -> Frame:
        if kind == DATA:
            return Data(body)
        if len(body) != _SIZE.size:
            raise FrameError("resize frame has the wrong length")
        rows, cols = _SIZE.unpack(body)
        return Resize(rows, cols)
