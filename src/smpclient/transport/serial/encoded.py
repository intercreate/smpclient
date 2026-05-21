"""The base64-encoded serial `SMPTransport` for UART, USB CDC ACM, and CAN.

An SMP serial frame wraps the SMP message as `[uint16 length][message][uint16 CRC16]`,
base64-encodes it, and splits it into lines (<= 128 bytes by convention) on the wire.
The server base64-decodes each line as it arrives into one reassembly buffer, so the
buffer that bounds a transaction holds the *decoded* frame: the largest message is
`buf_size - 4` (the length and CRC16 share the buffer). That message becomes ~1.37x as
many bytes once base64-encoded and line-framed, so the transport puts more than
`buf_size` encoded bytes on the wire -- which the server decodes incrementally.

This is what Zephyr calls "SMP over console" -- the framing shared by
`CONFIG_MCUMGR_TRANSPORT_UART` and `CONFIG_MCUMGR_TRANSPORT_SHELL`, and the only
SMP-over-UART option that existed before Zephyr 4.4.  For
`CONFIG_MCUMGR_TRANSPORT_RAW_UART` servers, use `SMPSerialRawTransport` from
`smpclient.transport.serial.unencoded`.

The transport fills that decoded buffer for best throughput; how it learns the buffer
size is the `fragmentation_strategy` (`FragmentationStrategy`) -- see `Auto` (the
default), `BufferSize`, and `BufferParams`.
"""

import asyncio
import logging
import math
import warnings
from enum import IntEnum, unique
from typing import Final, NamedTuple, TypeAlias

from smp import packet as smppacket
from typing_extensions import assert_never, deprecated, overload, override

from smpclient.transport.serial.common import _SerialTransportBase

logger = logging.getLogger(__name__)


def _base64_cost(size: int) -> int:
    """The worst case size required to encode `size` `bytes`."""
    if size == 0:
        return 0

    return math.ceil(4 / 3 * size) + 2


def _base64_max(size: int) -> int:
    """Given a max `size`, return how many bytes can be encoded."""
    if size < 4:
        return 0

    return math.floor(3 / 4 * size) - 2


_DEFAULT_LINE_LENGTH: Final = 128
"""The SMP serial line length convention: base64 chars per line on the wire."""

_LEGACY_LINE_BUFFERS: Final = 2
"""The 7.1.0 default `line_buffers`, preserved for the deprecated constructor params."""

_LEGACY_FRAME_SIZE: Final = 256
"""The 7.1.0 default `max_smp_encoded_frame_size`, preserved for the deprecated params."""

_MIN_LINE_LENGTH: Final = 8
"""The smallest `line_length` that can carry a base64 payload.

`smppacket.encode` packs `((line_length - 4) // 4) * 4` base64 chars per line; below
8 that is `<= 0` and the encoder would emit empty continuation packets forever.
"""

_FRAME_OVERHEAD: Final = smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size
"""The 2-byte frame length + 2-byte CRC16 that share the server's reassembly buffer."""


def _encoded_budget(mtu: int, line_buffers: int) -> int:
    """Unencoded capacity within an encoded line-buffer budget of `mtu` bytes.

    Subtracts per-line framing (the base64-encoded frame length + CRC16 plus a
    start/continue delimiter, per line buffer) and the stop delimiter, then converts
    the remaining encoded budget to its unencoded capacity.  May be negative when
    `mtu` is too small to even hold the framing.
    """
    packet_framing_size: Final = (
        _base64_cost(_FRAME_OVERHEAD) + smppacket.DELIMITER_SIZE
    ) * line_buffers + len(smppacket.END_DELIMITER)
    return _base64_max(mtu) - packet_framing_size


_LEGACY_PARAMS_DEPRECATION: Final = (
    "max_smp_encoded_frame_size, line_length, and line_buffers are deprecated; pass a "
    "fragmentation_strategy (Auto, BufferSize, or BufferParams) instead."
)
"""The runtime `DeprecationWarning` message.

The `@deprecated` overload decorators must repeat this text as a string *literal* --
PEP 702 type checkers ignore a name reference -- so keep the two in sync.
"""


class Auto(NamedTuple):
    """Discover the server's reassembly buffer from its MCUmgr params.

    On connect the client reads the server's `buf_size`
    (`CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE`) -- the decoded SMP frame reassembly
    buffer -- and sends messages up to `buf_size - 4` (the frame length and CRC16
    share that buffer), filling it for best throughput.  Before the params are read,
    or when the server does not support the params command, a single 128-byte line
    buffer is assumed.
    """


class BufferSize(NamedTuple):
    """Manually specify the server's decoded reassembly buffer size.

    For servers that do not advertise MCUmgr params (e.g. MCUboot serial recovery):
    behaves exactly like `Auto` once `buf_size` is known, sending messages up to
    `buf_size - 4`.  Lower `line_length` only for a server whose per-line input
    buffer is smaller than the 128-byte convention.
    """

    buf_size: int
    """The decoded SMP frame reassembly buffer
    (`CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE` / `BOOT_SERIAL_MAX_RECEIVE_SIZE`)."""

    line_length: int = _DEFAULT_LINE_LENGTH
    """The maximum length of one fragment (line) on the wire: a 2-byte
    start/continue delimiter, the base64 payload, and a newline (so the base64
    payload is ~3 bytes less).  128 by convention."""


class BufferParams(NamedTuple):
    """Model the server's *encoded* line-buffer budget.

    The message is bounded by how many unencoded bytes survive base64 expansion and
    per-line framing within `line_length * line_buffers` encoded bytes.  This
    deliberately under-fills a larger decoded reassembly buffer; prefer `Auto` or
    `BufferSize` unless the server's real constraint is a small pool of short line
    buffers.
    """

    line_length: int = _DEFAULT_LINE_LENGTH
    """The maximum length of one fragment (line) on the wire: a 2-byte
    start/continue delimiter, the base64 payload, and a newline (so the base64
    payload is ~3 bytes less).  128 by convention."""

    line_buffers: int = 1
    """The number of encoded line buffers the budget spans."""


FragmentationStrategy: TypeAlias = Auto | BufferSize | BufferParams
"""How `SMPSerialTransport` sizes SMP messages: `Auto`, `BufferSize`, or `BufferParams`."""


class _LegacyParams(NamedTuple):
    """The deprecated 7.1.0 `(max_smp_encoded_frame_size, line_length, line_buffers)` sizing.

    Constructed only by the deprecated constructor params; it reproduces 7.1.0
    byte-for-byte.  Unlike `BufferParams`, `mtu` is the *explicit*
    `max_smp_encoded_frame_size` (independent of `line_length * line_buffers`, exactly
    as 7.1.0 stored it), while the per-line framing still spans `line_buffers`.  Not part
    of the public `FragmentationStrategy` API -- prefer `Auto`, `BufferSize`, or
    `BufferParams`.
    """

    max_smp_encoded_frame_size: int
    """The encoded frame size that `mtu` reports verbatim (7.1.0 semantics)."""

    line_length: int
    """The maximum length of one fragment (line) on the wire."""

    line_buffers: int
    """The number of encoded line buffers the framing budget spans."""


_ResolvedStrategy: TypeAlias = Auto | BufferSize | BufferParams | _LegacyParams
"""The internal strategy a constructor call resolves to (adds the deprecated `_LegacyParams`)."""


class SMPSerialTransport(_SerialTransportBase):
    @unique
    class BufferState(IntEnum):
        SMP = 0
        """An SMP start or continue delimiter has been received and
        `_buffer` is being parsed as an SMP packet.
        """

        SERIAL = 1
        """The SMP start delimiter has not been received and
        `_buffer` is being parsed as serial data.
        """

    @overload
    def __init__(
        self,
        fragmentation_strategy: FragmentationStrategy = ...,
        *,
        baudrate: int = ...,
        bytesize: int = ...,
        parity: str = ...,
        stopbits: float = ...,
        timeout: float | None = ...,
        xonxoff: bool = ...,
        rtscts: bool = ...,
        write_timeout: float | None = ...,
        dsrdtr: bool = ...,
        inter_byte_timeout: float | None = ...,
        exclusive: bool | None = ...,
    ) -> None: ...

    @overload
    @deprecated(
        "max_smp_encoded_frame_size, line_length, and line_buffers are deprecated; pass a "
        "fragmentation_strategy (Auto, BufferSize, or BufferParams) instead."
    )
    def __init__(
        self,
        *,
        max_smp_encoded_frame_size: int = ...,
        line_length: int = ...,
        line_buffers: int = ...,
        baudrate: int = ...,
        bytesize: int = ...,
        parity: str = ...,
        stopbits: float = ...,
        timeout: float | None = ...,
        xonxoff: bool = ...,
        rtscts: bool = ...,
        write_timeout: float | None = ...,
        dsrdtr: bool = ...,
        inter_byte_timeout: float | None = ...,
        exclusive: bool | None = ...,
    ) -> None: ...

    @overload
    @deprecated(
        "max_smp_encoded_frame_size, line_length, and line_buffers are deprecated; pass a "
        "fragmentation_strategy (Auto, BufferSize, or BufferParams) instead."
    )
    def __init__(
        self,
        max_smp_encoded_frame_size: int,
        line_length: int = ...,
        line_buffers: int = ...,
        /,
        *,
        baudrate: int = ...,
        bytesize: int = ...,
        parity: str = ...,
        stopbits: float = ...,
        timeout: float | None = ...,
        xonxoff: bool = ...,
        rtscts: bool = ...,
        write_timeout: float | None = ...,
        dsrdtr: bool = ...,
        inter_byte_timeout: float | None = ...,
        exclusive: bool | None = ...,
    ) -> None: ...

    def __init__(  # noqa: DOC301
        self,
        fragmentation_strategy: FragmentationStrategy | int | None = None,
        line_length: int | None = None,
        line_buffers: int | None = None,
        *,
        max_smp_encoded_frame_size: int | None = None,
        baudrate: int = 115200,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout: float | None = None,
        xonxoff: bool = False,
        rtscts: bool = False,
        write_timeout: float | None = None,
        dsrdtr: bool = False,
        inter_byte_timeout: float | None = None,
        exclusive: bool | None = None,
    ) -> None:
        """Initialize the serial transport.

        Args:
            fragmentation_strategy: how to size SMP messages; one of `Auto`
                (default), `BufferSize`, or `BufferParams`.
            line_length: Deprecated; pass `BufferParams(line_length=...)` (or `BufferSize`).
            line_buffers: Deprecated; pass `BufferParams(line_buffers=...)`.
            max_smp_encoded_frame_size: Deprecated, but still honored for backward
                compatibility -- it drives `mtu` exactly as in 7.1.0.  Prefer an explicit
                `BufferSize(buf_size=...)` (decoded netbuf) for new code.
            baudrate: The baudrate of the serial connection.  OK to ignore for
                USB CDC ACM.
            bytesize: The number of data bits.
            parity: The parity setting.
            stopbits: The number of stop bits.
            timeout: The read timeout.
            xonxoff: Enable software flow control.
            rtscts: Enable hardware (RTS/CTS) flow control.
            write_timeout: The write timeout.
            dsrdtr: Enable hardware (DSR/DTR) flow control.
            inter_byte_timeout: The inter-byte timeout.
            exclusive: The exclusive access timeout.

        """
        super().__init__(
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            xonxoff=xonxoff,
            rtscts=rtscts,
            write_timeout=write_timeout,
            dsrdtr=dsrdtr,
            inter_byte_timeout=inter_byte_timeout,
            exclusive=exclusive,
        )

        self._fragmentation_strategy: Final = self._resolve_fragmentation_strategy(
            fragmentation_strategy, max_smp_encoded_frame_size, line_length, line_buffers
        )

        self._smp_packet_queue: asyncio.Queue[bytes] = asyncio.Queue()
        """Contains full SMP packets."""
        self._serial_buffer = bytearray()
        """Contains any non-SMP serial data."""
        self._buffer: bytearray = bytearray([])
        """Contains all incoming data (serial + SMP intertwined, may be incomplete)."""
        self._buffer_state = SMPSerialTransport.BufferState.SERIAL
        """The state of the read buffer."""

        logger.debug(f"Initialized {self.__class__.__name__}")

    @staticmethod
    def _resolve_fragmentation_strategy(
        fragmentation_strategy: FragmentationStrategy | int | None,
        max_smp_encoded_frame_size: int | None,
        line_length: int | None,
        line_buffers: int | None,
    ) -> _ResolvedStrategy:
        """Normalize the constructor inputs into a fragmentation strategy.

        An explicit `fragmentation_strategy` always wins; it is validated, and any
        stray deprecated args passed alongside it are logged and ignored.  Otherwise
        the deprecated 7.1.0 params -- `max_smp_encoded_frame_size`, `line_length`,
        `line_buffers`, or a legacy positional `int` frame size -- reproduce 7.1.0
        exactly via `_LegacyParams` (`mtu == max_smp_encoded_frame_size`, defaulting to
        the 7.1.0 256/128/2) and emit a `DeprecationWarning`.  A frame size that
        disagrees with `line_length * line_buffers` is logged at the level 7.1.0 used,
        but -- as in 7.1.0 -- the explicit frame size still drives `mtu`.
        """
        if not isinstance(fragmentation_strategy, int) and fragmentation_strategy is not None:
            ignored: Final = {
                name: value
                for name, value in (
                    ("max_smp_encoded_frame_size", max_smp_encoded_frame_size),
                    ("line_length", line_length),
                    ("line_buffers", line_buffers),
                )
                if value is not None
            }
            if ignored:
                logger.warning(
                    f"explicit fragmentation_strategy={fragmentation_strategy!r} takes "
                    f"precedence; ignoring deprecated {ignored}"
                )
            SMPSerialTransport._validate_strategy(fragmentation_strategy)
            return fragmentation_strategy

        legacy_frame: Final = (
            max_smp_encoded_frame_size
            if max_smp_encoded_frame_size is not None
            else (fragmentation_strategy if isinstance(fragmentation_strategy, int) else None)
        )
        if legacy_frame is None and line_length is None and line_buffers is None:
            return Auto()

        warnings.warn(_LEGACY_PARAMS_DEPRECATION, DeprecationWarning, stacklevel=3)
        resolved_frame: Final = _LEGACY_FRAME_SIZE if legacy_frame is None else legacy_frame
        resolved_line_length: Final = _DEFAULT_LINE_LENGTH if line_length is None else line_length
        resolved_line_buffers: Final = (
            _LEGACY_LINE_BUFFERS if line_buffers is None else line_buffers
        )
        budget: Final = resolved_line_length * resolved_line_buffers
        if resolved_frame < budget:
            logger.error(
                f"max_smp_encoded_frame_size={resolved_frame} is less than "
                f"line_length={resolved_line_length} * line_buffers={resolved_line_buffers}!"
            )
        elif resolved_frame != budget:
            logger.warning(
                f"max_smp_encoded_frame_size={resolved_frame} is not equal to "
                f"line_length={resolved_line_length} * line_buffers={resolved_line_buffers}!"
            )
        return _LegacyParams(
            max_smp_encoded_frame_size=resolved_frame,
            line_length=resolved_line_length,
            line_buffers=resolved_line_buffers,
        )

    @staticmethod
    def _validate_strategy(strategy: FragmentationStrategy) -> None:
        """Raise `ValueError` for a modern strategy that cannot carry a message.

        Guards `BufferSize`/`BufferParams` against configs that would otherwise fail far
        downstream: a `line_length` too small for `smppacket.encode` to make progress (it
        would emit empty packets forever), a `buf_size` at or below the frame overhead, or
        an encoded budget too small for a single byte.  The deprecated 7.1.0 params are
        intentionally *not* validated -- `_LegacyParams` reproduces 7.1.0 behavior, latent
        edge cases and all.  `Auto` defers to `initialize`, where the server's advertised
        buffer size is known.
        """
        match strategy:
            case Auto():
                return
            case BufferSize(buf_size=buf_size, line_length=line_length):
                SMPSerialTransport._validate_line_length(line_length)
                if buf_size <= _FRAME_OVERHEAD:
                    raise ValueError(
                        f"BufferSize buf_size ({buf_size}) must exceed the "
                        f"{_FRAME_OVERHEAD}-byte frame overhead to carry a message"
                    )
            case BufferParams(line_length=line_length, line_buffers=line_buffers):
                SMPSerialTransport._validate_line_length(line_length)
                if line_buffers < 1:
                    raise ValueError(f"BufferParams line_buffers ({line_buffers}) must be >= 1")
                if _encoded_budget(line_length * line_buffers, line_buffers) <= 0:
                    raise ValueError(
                        f"BufferParams (line_length={line_length}, line_buffers={line_buffers}) "
                        f"is too small to carry a message"
                    )
            case _ as unreachable:
                assert_never(unreachable)

    @staticmethod
    def _validate_line_length(line_length: int) -> None:
        """Raise `ValueError` if `line_length` is too small to fragment a base64 payload."""
        if line_length < _MIN_LINE_LENGTH:
            raise ValueError(
                f"line_length ({line_length}) must be >= {_MIN_LINE_LENGTH}; smaller lines "
                f"cannot carry a base64 payload and would stall fragmentation"
            )

    @override
    def _reset_state(self) -> None:
        """Reset internal state and queues for a fresh connection."""
        self._smp_packet_queue = asyncio.Queue()
        self._serial_buffer.clear()
        self._buffer = bytearray([])
        self._buffer_state = SMPSerialTransport.BufferState.SERIAL

    @property
    def _line_length(self) -> int:
        """The base64 line length used to fragment outgoing frames."""
        match self._fragmentation_strategy:
            case Auto():
                return _DEFAULT_LINE_LENGTH
            case BufferSize(line_length=line_length):
                return line_length
            case BufferParams(line_length=line_length):
                return line_length
            case _LegacyParams(line_length=line_length):
                return line_length
            case _ as unreachable:
                assert_never(unreachable)

    @property
    def _line_buffers(self) -> int:
        """The number of encoded line buffers spanned by the configured budget.

        Meaningful for `BufferParams`/legacy params, where it sets the encoded budget.
        For the decoded-netbuf strategies (`Auto`/`BufferSize`) it is a diagnostic line
        count, clamped to at least 1 (never the misleading `0` of a sub-`line_length`
        buffer); `Auto` falls back to the conservative legacy default until the server's
        params are read.
        """
        match self._fragmentation_strategy:
            case Auto():
                if self._smp_server_transport_buffer_size is not None:
                    return max(1, self._smp_server_transport_buffer_size // self._line_length)
                return _LEGACY_LINE_BUFFERS
            case BufferSize(buf_size=buf_size):
                return max(1, buf_size // self._line_length)
            case BufferParams(line_buffers=line_buffers):
                return line_buffers
            case _LegacyParams(line_buffers=line_buffers):
                return line_buffers
            case _ as unreachable:
                assert_never(unreachable)

    @property
    def _max_smp_encoded_frame_size(self) -> int:
        """The configured buffer size that the MTU reports."""
        match self._fragmentation_strategy:
            case Auto():
                if self._smp_server_transport_buffer_size is not None:
                    return self._smp_server_transport_buffer_size
                return self._line_length * self._line_buffers
            case BufferSize(buf_size=buf_size):
                return buf_size
            case BufferParams(line_length=line_length, line_buffers=line_buffers):
                return line_length * line_buffers
            case _LegacyParams(max_smp_encoded_frame_size=frame_size):
                return frame_size
            case _ as unreachable:
                assert_never(unreachable)

    @override
    def initialize(self, smp_server_transport_buffer_size: int) -> None:
        """Initialize with the server's buffer size from MCUMGR_PARAM.

        Args:
            smp_server_transport_buffer_size: The server's CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE

        Raises:
            ValueError: in `Auto` mode, if the server's advertised buffer is too small to
                hold a framed message (`<= ` the frame overhead).
        """
        super().initialize(smp_server_transport_buffer_size)

        match self._fragmentation_strategy:
            case Auto():
                if smp_server_transport_buffer_size <= _FRAME_OVERHEAD:
                    raise ValueError(
                        f"server buffer size ({smp_server_transport_buffer_size}) must exceed "
                        f"the {_FRAME_OVERHEAD}-byte frame overhead to carry a message"
                    )
                logger.info(
                    f"Auto-configured from server buf_size={smp_server_transport_buffer_size}: "
                    f"mtu={self.mtu}, max_unencoded_size={self.max_unencoded_size}, "
                    f"line_length={self._line_length}"
                )
            case BufferSize(buf_size=buf_size):
                if buf_size > smp_server_transport_buffer_size:
                    logger.warning(
                        f"BufferSize buf_size ({buf_size}) exceeds the server's advertised "
                        f"buffer size ({smp_server_transport_buffer_size})"
                    )
            case BufferParams(line_length=line_length, line_buffers=line_buffers):
                calculated_size = line_length * line_buffers
                if calculated_size > smp_server_transport_buffer_size:
                    logger.warning(
                        f"BufferParams (line_length={line_length} * "
                        f"line_buffers={line_buffers} = {calculated_size}) "
                        f"exceeds server buffer size ({smp_server_transport_buffer_size})"
                    )
            case _LegacyParams(max_smp_encoded_frame_size=frame_size):
                if frame_size > smp_server_transport_buffer_size:
                    logger.warning(
                        f"deprecated max_smp_encoded_frame_size ({frame_size}) exceeds the "
                        f"server's advertised buffer size ({smp_server_transport_buffer_size})"
                    )
            case _ as unreachable:
                assert_never(unreachable)

    @override
    async def send(self, data: bytes) -> None:
        if len(data) > self.max_unencoded_size:
            raise ValueError(
                f"Data size {len(data)} exceeds maximum unencoded size {self.max_unencoded_size}"
            )
        logger.debug(f"Sending {len(data)} bytes")
        with self._serial_exception_to_disconnected():
            for packet in smppacket.encode(data, line_length=self._line_length):
                self._conn.write(packet)
                logger.debug(f"Writing encoded packet of size {len(packet)}B; {self._line_length=}")

            await self._drain_tx()

        logger.debug(f"Sent {len(data)} bytes")

    @override
    async def receive(self) -> bytes:
        decoder = smppacket.decode()
        next(decoder)

        logger.debug("Waiting for response")
        while True:
            b = await self._read_one_smp_packet()
            try:
                decoder.send(b)
            except StopIteration as e:
                logger.debug(f"Finished receiving {len(e.value)} byte response")
                return e.value

    async def _read_one_smp_packet(self) -> bytes:
        """Return one received SMP packet from the queue.

        Raises `SMPTransportDisconnected` if disconnected.
        """
        if not self._smp_packet_queue.empty():
            # There may already be a response in the queue, if for some reason we've received
            # multiple responses and haven't read them in-between. This is not standard but
            # it is possible, and easier to implement this way.
            return self._smp_packet_queue.get_nowait()

        await self._read_and_process(read_until_one_smp_packet=True)
        return self._smp_packet_queue.get_nowait()

    async def read_serial(self, delimiter: bytes | None = None) -> bytes:
        """Drain regular serial traffic (non-SMP bytes) until given delimiter.

        Returns all available bytes if no delimiter is given.
        May return empty bytes if nothing has been received.
        """
        await self._read_and_process(read_until_one_smp_packet=False)
        if delimiter is None:
            res = bytes(self._serial_buffer)
            self._serial_buffer.clear()
            return res
        else:
            try:
                first_match, remaining_data = self._serial_buffer.split(delimiter, 1)
            except ValueError:
                return b''
            self._serial_buffer = remaining_data
            return bytes(first_match)

    async def _read_and_process(self, read_until_one_smp_packet: bool) -> None:
        """Reads raw data from serial and processes it into SMP packets and regular serial data."""
        while True:
            data = await self._read_all()

            if data:
                self._buffer.extend(data)
                await self._process_buffer()
            else:
                await asyncio.sleep(self._POLLING_INTERVAL_S)

            if read_until_one_smp_packet:
                if self._smp_packet_queue.qsize():
                    break  # Packet found; exit early
            else:
                # Just polling serial data
                break

    async def _process_buffer(self) -> None:
        """Process buffered data until more bytes are needed."""
        while True:
            if self._buffer_state == SMPSerialTransport.BufferState.SERIAL:
                should_continue = await self._process_buffer_as_serial_data()
            else:
                should_continue = await self._process_buffer_as_smp_data()

            if not should_continue:
                break

    async def _process_buffer_as_serial_data(self) -> bool:
        """Handle non-SMP data and transition to SMP state when finding SMP frame-start delimiters.

        Return True if further data remains to process in the buffer; return False otherwise.
        """
        if not self._buffer:
            return False

        smp_packet_start: int = self._find_smp_packet_start(self._buffer)
        if smp_packet_start >= 0:
            serial_data, remaining_data = (
                self._buffer[:smp_packet_start],
                self._buffer[smp_packet_start:],
            )
            self._serial_buffer.extend(serial_data)

            self._buffer = remaining_data
            self._buffer_state = SMPSerialTransport.BufferState.SMP
            return True

        # No complete delimiter found - everything is serial data, with one rare edge
        # case: last byte of buffer could be an incomplete delimiter - must preserve it for now.
        if self._could_be_smp_packet_start(self._buffer[-1]):
            self._serial_buffer.extend(self._buffer[:-1])
            self._buffer = self._buffer[-1:]
        else:
            self._serial_buffer.extend(self._buffer)
            self._buffer.clear()
        return False

    async def _process_buffer_as_smp_data(self) -> bool:
        """Handle SMP data and transition to SERIAL state when finding SMP frame-end delimiter.

        Return True if further data remains to process in the buffer; return False otherwise.
        """
        smp_packet_end: int = self._buffer.find(smppacket.END_DELIMITER)
        if smp_packet_end == -1:
            return False
        smp_packet_end += len(smppacket.END_DELIMITER)

        smp_data, remaining_data = (
            self._buffer[:smp_packet_end],
            self._buffer[smp_packet_end:],
        )
        await self._smp_packet_queue.put(bytes(smp_data))

        self._buffer = remaining_data
        # Even if the remaining data is actually SMP data, then the next serial parse
        # will simply put us right back into SMP state - no need to check here.
        self._buffer_state = SMPSerialTransport.BufferState.SERIAL

        return bool(self._buffer)

    def _find_smp_packet_start(self, buf: bytearray) -> int:
        """Return index of the earliest SMP frame-start delimiter, if any; -1 if none found."""
        indices = [
            i
            for i in (
                buf.find(smppacket.START_DELIMITER),
                buf.find(smppacket.CONTINUE_DELIMITER),
            )
            if i != -1
        ]
        return min(indices) if indices else -1

    def _could_be_smp_packet_start(self, byte: int) -> bool:
        """Return True if the given byte value matches the start of any SMP packet delimiter."""
        return byte == smppacket.START_DELIMITER[0] or byte == smppacket.CONTINUE_DELIMITER[0]

    @override
    @property
    def mtu(self) -> int:
        return self._max_smp_encoded_frame_size

    @override
    @property
    def max_unencoded_size(self) -> int:
        """The maximum unencoded SMP message size, in bytes.

        `Auto` (once `buf_size` is known) and `BufferSize` fill the server's
        decoded reassembly buffer: the message is `buf_size - 4`, where `buf_size`
        is `CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE` and 4 is the SMP serial frame's
        2-byte length and 2-byte CRC16, which the server strips before the netbuf.
        (Verified against native_sim/QEMU/mps2: a `buf_size - 4` message
        round-trips; `buf_size - 3` is dropped.)

        `BufferParams`, the deprecated 7.1.0 params, and `Auto` before initialization
        instead bound the message by an *encoded* line-buffer budget: how many
        unencoded bytes survive base64 expansion and per-line framing within `mtu`.

        SMP serial framing (the 2-byte length + 2-byte CRC16):
        https://docs.zephyrproject.org/latest/services/device_mgmt/smp_transport.html
        """
        match self._fragmentation_strategy:
            case Auto():
                if self._smp_server_transport_buffer_size is not None:
                    return self._smp_server_transport_buffer_size - _FRAME_OVERHEAD
                return self._encoded_budget_max_unencoded_size()
            case BufferSize(buf_size=buf_size):
                return buf_size - _FRAME_OVERHEAD
            case BufferParams():
                return self._encoded_budget_max_unencoded_size()
            case _LegacyParams():
                return self._encoded_budget_max_unencoded_size()
            case _ as unreachable:
                assert_never(unreachable)

    def _encoded_budget_max_unencoded_size(self) -> int:
        """Unencoded capacity within the encoded line-buffer budget (`mtu`)."""
        return _encoded_budget(self.mtu, self._line_buffers)
