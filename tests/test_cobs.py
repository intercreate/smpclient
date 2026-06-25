"""Tests for the COBS+CRC16 serial framing (`smpclient.transport.serial.framing.cobs`)."""

from __future__ import annotations

import doctest
import random

import pytest
from smp.packet import CRC16_STRUCT, crc16_func

from smpclient.transport.serial.framing import cobs
from smpclient.transport.serial.framing.cobs import (
    _MAX_BUFFER_BYTES,
    Cobs,
    cobs_decode,
    cobs_encode,
)


def test_doctests() -> None:
    """Run the module's doctests under pytest (CLAUDE.md: specific docstrings must be tested)."""
    results = doctest.testmod(cobs)
    assert results.attempted > 0
    assert results.failed == 0


def _frame(message: bytes) -> bytes:
    """The full COBS wire frame (delimiter included) for `message`."""
    (wire,) = Cobs().encode(message)
    return wire


def _corrupt(message: bytes) -> bytes:
    """A full COBS frame that decodes cleanly but whose CRC16 is wrong."""
    return cobs_encode(message + CRC16_STRUCT.pack(crc16_func(message) ^ 0xFFFF)) + b"\x00"


_M1 = b"\x0a\x00\x00\x01msg-one"
_M2 = b"second\x00message"
_M3 = b"\xff\xff third\x00"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes([0]),
        bytes(10),
        bytes([0xFF]) * 253,  # the COBS off-by-one lives at a 254-byte non-zero run
        bytes([0xFF]) * 254,
        bytes([0xFF]) * 255,
        bytes([0xFF]) * 508,  # two maximal runs back to back
        bytes(range(256)),
        b"\x00leading\x00and\x00trailing\x00",
    ],
)
def test_cobs_roundtrip(data: bytes) -> None:
    encoded = cobs_encode(data)
    assert 0 not in encoded, "COBS output must contain no 0x00"
    assert cobs_decode(encoded) == data


def test_cobs_roundtrip_fuzz() -> None:
    """Deterministic fuzz over the payload shapes that stress COBS (mirrors mcuboot's ztest)."""
    rng = random.Random(0x1234567)
    for _ in range(2000):
        n = rng.randrange(600)
        mode = rng.randrange(4)
        if mode == 0:
            data = bytes(rng.randrange(256) for _ in range(n))
        elif mode == 1:
            data = bytes(n)
        elif mode == 2:
            data = bytes([0xFF]) * n
        else:
            data = bytes(rng.randrange(256) if rng.randrange(4) else 0 for _ in range(n))
        encoded = cobs_encode(data)
        assert 0 not in encoded
        assert cobs_decode(encoded) == data


def test_cobs_decode_zero_code_raises() -> None:
    with pytest.raises(ValueError):
        cobs_decode(b"\x00")


def test_cobs_decode_truncated_raises() -> None:
    with pytest.raises(ValueError):
        cobs_decode(b"\x05\x11")  # code promises 4 more bytes, only 1 present


def test_cobs_framing_one_terminated_frame() -> None:
    frames = list(Cobs().encode(b"\x00message\xff" * 8))
    assert len(frames) == 1
    assert frames[0][-1] == 0, "frame is 0x00-terminated"
    assert 0 not in frames[0][:-1], "only the trailing delimiter is 0x00"


def test_cobs_framing_roundtrip() -> None:
    decoder = Cobs()
    decoder.feed(_frame(bytes(range(256))))
    assert decoder.take() == bytes(range(256))


def test_cobs_framing_split_across_feeds() -> None:
    decoder = Cobs()
    wire = _frame(_M1)
    decoder.feed(wire[:4])
    assert decoder.take() is None  # no delimiter yet
    decoder.feed(wire[4:])
    assert decoder.take() == _M1


def test_cobs_framing_byte_at_a_time() -> None:
    decoder = Cobs()
    wire = _frame(_M1)
    results = []
    for b in wire:
        decoder.feed(bytes([b]))
        results.append(decoder.take())
    assert results[-1] == _M1
    assert all(r is None for r in results[:-1])


def test_cobs_framing_multiple_frames_drained_one_per_take() -> None:
    decoder = Cobs()
    decoder.feed(_frame(_M1) + _frame(_M2) + _frame(_M3))  # three frames in one feed
    assert decoder.take() == _M1
    assert decoder.take() == _M2
    assert decoder.take() == _M3
    assert decoder.take() is None


def test_cobs_framing_leftover_preserved_across_feeds() -> None:
    decoder = Cobs()
    w1, w2 = _frame(_M1), _frame(_M2)
    decoder.feed(w1 + w2[: len(w2) // 2])  # feed ends partway into frame 2
    assert decoder.take() == _M1
    assert decoder.take() is None  # frame 2 incomplete; its prefix is retained
    decoder.feed(w2[len(w2) // 2 :])
    assert decoder.take() == _M2


def test_cobs_framing_resyncs_past_bad_crc() -> None:
    decoder = Cobs()
    decoder.feed(_corrupt(_M1) + _frame(_M2))
    assert decoder.take() == _M2


def test_cobs_framing_resyncs_past_leading_garbage() -> None:
    decoder = Cobs()
    decoder.feed(b"\x11\x22\x33\x44\x00" + _frame(_M1))  # truncated-frame tail + delimiter
    assert decoder.take() == _M1


def test_cobs_framing_skips_empty_frames() -> None:
    decoder = Cobs()
    decoder.feed(b"\x00\x00\x00" + _frame(_M1))
    assert decoder.take() == _M1


def test_cobs_framing_lone_corrupt_frame_yields_none() -> None:
    decoder = Cobs()
    decoder.feed(_corrupt(_M1))
    assert decoder.take() is None  # no false message; the caller times out and retransmits


def test_cobs_framing_drops_empty_message_frame() -> None:
    decoder = Cobs()
    (empty_frame,) = Cobs().encode(b"")  # a structurally valid frame carrying a 0-length message
    decoder.feed(empty_frame)
    assert (
        decoder.take() is None
    )  # not a real message (CRC of b"" is 0x0000); dropped, not surfaced


def test_cobs_framing_caps_delimiterless_buffer_and_resyncs() -> None:
    decoder = Cobs()
    decoder.feed(b"\x11" * (_MAX_BUFFER_BYTES + 1))  # a stream with no 0x00 delimiter: noise
    assert decoder.take() is None  # dropped at the ceiling rather than buffered without bound
    decoder.feed(_frame(_M1))
    assert decoder.take() == _M1  # and a real frame after the noise still decodes


def test_cobs_instances_have_independent_buffers() -> None:
    a, b = Cobs(), Cobs()  # default_factory gives each its own buffer (no shared-default trap)
    a.feed(_frame(_M1))
    assert b.take() is None
    assert a.take() == _M1


def test_cobs_reset_clears_buffer() -> None:
    decoder = Cobs()
    decoder.feed(_frame(_M1)[:4])  # a partial frame from a prior (aborted) connection
    decoder.reset()
    decoder.feed(_frame(_M2))
    assert decoder.take() == _M2  # the stale partial was discarded, not prepended


def test_cobs_framing_stream_fuzz() -> None:
    """Many frames as a randomly-chunked stream: every message round-trips, in order."""
    rng = random.Random(0xC0B5)
    messages = [bytes(rng.randrange(256) for _ in range(rng.randrange(8, 300))) for _ in range(50)]
    stream = b"".join(_frame(m) for m in messages)
    decoder = Cobs()
    received: list[bytes] = []
    i = 0
    while i < len(stream):
        step = rng.randrange(1, 64)
        decoder.feed(stream[i : i + step])
        i += step
        while (out := decoder.take()) is not None:  # drain every frame the chunk completed
            received.append(out)
    assert received == messages
