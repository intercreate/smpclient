"""COBS+CRC16 framing for MCUboot raw serial recovery (intercreate/mcuboot#5).

The COBS codec matches MCUboot's ``boot/boot_serial/src/cobs.c``; the CRC is
reused from `smp.packet`.
"""

import logging
from dataclasses import dataclass, field
from typing import Final, Iterator

from smp.packet import CRC16_STRUCT, crc16_func

logger = logging.getLogger(__name__)

_DELIMITER: Final = 0
_CODE_FULL: Final = 0xFF

_MAX_BUFFER_BYTES: Final = 1 << 16
"""Resync ceiling: bytes with no `0x00` delimiter past this are noise, not a frame, so the
buffer is dropped.  64 KiB is far above any SMP-over-serial frame (recovery's
`BOOT_SERIAL_MAX_RECEIVE_SIZE` is a few KiB), and bounds memory on a delimiter-free stream
(e.g. a wrong-baud or wrong-protocol peer) the way the length-prefixed transport already does."""


def cobs_encode(data: bytes) -> bytes:
    r"""COBS-encode `data`, with no frame delimiter appended.

    >>> cobs_encode(b"").hex()
    '01'
    >>> cobs_encode(b"\x01\x02\x03").hex()
    '04010203'
    >>> cobs_encode(b"\x00").hex()
    '0101'
    >>> cobs_encode(b"\x11\x00\x22").hex()
    '02110222'
    >>> cobs_decode(cobs_encode(bytes(range(256)))) == bytes(range(256))
    True
    """
    out = bytearray([_DELIMITER])
    code_index = 0
    code = 1
    for byte in data:
        if byte != _DELIMITER:
            out.append(byte)
            code += 1
            if code != _CODE_FULL:
                continue
        out[code_index] = code
        code_index = len(out)
        out.append(_DELIMITER)
        code = 1
    out[code_index] = code
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    r"""Decode a COBS frame whose delimiter has been stripped; inverse of `cobs_encode`.

    >>> cobs_decode(bytes.fromhex("04010203")).hex()
    '010203'
    >>> cobs_decode(bytes.fromhex("0101"))
    b'\x00'
    >>> cobs_decode(bytes.fromhex("00"))
    Traceback (most recent call last):
        ...
    ValueError: 0x00 code byte in COBS frame
    >>> cobs_decode(bytes.fromhex("0511"))
    Traceback (most recent call last):
        ...
    ValueError: truncated COBS frame
    """
    out = bytearray()
    src = 0
    while src < len(encoded):
        code = encoded[src]
        src += 1
        if code == _DELIMITER:
            raise ValueError("0x00 code byte in COBS frame")
        for _ in range(1, code):
            if src >= len(encoded):
                raise ValueError("truncated COBS frame")
            out.append(encoded[src])
            src += 1
        if code != _CODE_FULL and src < len(encoded):
            out.append(_DELIMITER)
    return bytes(out)


def _decode_frame(frame: bytes) -> bytes | None:
    r"""Decode and CRC-verify one COBS frame; return its SMP message, or `None` if damaged.

    >>> message = b"\x0a\x00\x00\x01\x00\x01\x00\x05"
    >>> _decode_frame(cobs_encode(message + CRC16_STRUCT.pack(crc16_func(message)))) == message
    True
    >>> _decode_frame(cobs_encode(message + b"\x00\x00")) is None  # wrong CRC
    True
    >>> _decode_frame(b"") is None  # empty frame (a stray delimiter)
    True
    >>> _decode_frame(cobs_encode(b"\x00\x00")) is None  # decodes to an empty message + CRC
    True
    """
    try:
        decoded = cobs_decode(frame)
    except ValueError:
        return None
    if len(decoded) <= CRC16_STRUCT.size:  # nothing but (at most) a CRC: no message to carry
        return None
    message, crc = decoded[: -CRC16_STRUCT.size], decoded[-CRC16_STRUCT.size :]
    if crc16_func(message) != CRC16_STRUCT.unpack(crc)[0]:
        return None
    return message


@dataclass(frozen=True, slots=True)
class Cobs:
    """`SerialFraming` for MCUboot COBS+CRC16 raw serial recovery.

    A value that also owns its connection's reassembly buffer, mutated in place (frozen
    forbids rebinding the field, not mutating the `bytearray`); `reset` clears it.  Self-
    synchronising: a corrupt or truncated frame is dropped and decoding resumes at the next
    `0x00` delimiter.  Stateful, so use one instance per transport.
    """

    _buffer: bytearray = field(default_factory=bytearray, repr=False, compare=False)

    def encode(self, data: bytes) -> Iterator[bytes]:
        """Yield the one COBS frame for the SMP message `data`."""
        yield cobs_encode(data + CRC16_STRUCT.pack(crc16_func(data))) + bytes([_DELIMITER])

    def feed(self, data: bytes) -> None:
        """Buffer received bytes for decoding."""
        self._buffer.extend(data)

    def take(self) -> bytes | None:
        """Pop the next CRC-valid SMP message from the buffer, resyncing past bad frames."""
        while (end := self._buffer.find(_DELIMITER)) != -1:
            frame = bytes(self._buffer[:end])
            del self._buffer[: end + 1]
            if (message := _decode_frame(frame)) is not None:
                return message
            if frame:  # a stray delimiter gives an empty frame; only log real drops
                logger.warning(f"COBS: dropped a {len(frame)} B frame, resyncing")
        if len(self._buffer) > _MAX_BUFFER_BYTES:  # no delimiter in this much: noise, not a frame
            logger.warning(f"COBS: discarding {len(self._buffer)} delimiter-less bytes, resyncing")
            self._buffer.clear()
        return None

    def reset(self) -> None:
        """Discard buffered bytes so a new connection starts clean."""
        self._buffer.clear()
