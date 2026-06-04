"""A serial `SMPTransport` for UART, USB CDC ACM, and CAN.

An SMP serial frame wraps the SMP message as `[uint16 length][message][uint16 CRC16]`,
base64-encodes it, and splits it into lines (<= 128 bytes by convention) on the wire.
The server base64-decodes each line as it arrives into one reassembly buffer, so the
buffer that bounds a transaction holds the *decoded* frame: the largest message is
`buf_size - 4` (the length and CRC16 share the buffer). That message becomes ~1.37x as
many bytes once base64-encoded and line-framed, so the transport puts more than
`buf_size` encoded bytes on the wire -- which the server decodes incrementally.

The transport fills that decoded buffer for best throughput; how it learns the buffer
size is the `fragmentation_strategy` (`FragmentationStrategy`) -- see `Auto` (the
default), `BufferSize`, and `BufferParams`.
"""

import asyncio
import logging
import math
import time
import warnings
from enum import IntEnum, unique
from typing import Final, NamedTuple, TypeAlias

try:
    from serial import Serial, SerialException
except ModuleNotFoundError as e:
    if e.name == "serial":
        raise ImportError(
            "Serial transport requires the 'serial' extra. Use smpclient[serial]"
        ) from e
    raise
from smp import packet as smppacket
from typing_extensions import assert_never, deprecated, overload, override

from smpclient.transport import SMPTransport, SMPTransportDisconnected

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

_FRAME_OVERHEAD: Final = smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size
"""The 2-byte frame length + 2-byte CRC16 that share the server's reassembly buffer."""

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


class SMPSerialTransport(SMPTransport):
    _POLLING_INTERVAL_S = 0.005
    _CONNECTION_RETRY_INTERVAL_S = 0.500

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
            line_length: Deprecated; pass `BufferParams(line_length=...)`.
            line_buffers: Deprecated; pass `BufferParams(line_buffers=...)`.
            max_smp_encoded_frame_size: Deprecated; the strategy now derives the
                encoded frame size from `buf_size` (`Auto`/`BufferSize`) or
                `line_length * line_buffers` (`BufferParams`).
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
        self._fragmentation_strategy: Final = self._resolve_fragmentation_strategy(
            fragmentation_strategy, max_smp_encoded_frame_size, line_length, line_buffers
        )
        self._conn: Final = Serial(
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
    ) -> FragmentationStrategy:
        """Normalize the constructor inputs into a fragmentation strategy.

        An explicit `fragmentation_strategy` always wins.  Otherwise the deprecated
        7.1.0 params -- `max_smp_encoded_frame_size`, `line_length`, `line_buffers`,
        or a legacy positional `int` frame size -- map onto the equivalent
        `BufferParams` (whose encoded budget is `line_length * line_buffers`) and
        emit a `DeprecationWarning`.  A `max_smp_encoded_frame_size` that disagrees
        with that product is logged and ignored, as it was in 7.1.0.
        """
        if not isinstance(fragmentation_strategy, int) and fragmentation_strategy is not None:
            return fragmentation_strategy  # explicit modern strategy; ignore stray legacy args

        legacy_frame: Final = (
            max_smp_encoded_frame_size
            if max_smp_encoded_frame_size is not None
            else (fragmentation_strategy if isinstance(fragmentation_strategy, int) else None)
        )
        if legacy_frame is None and line_length is None and line_buffers is None:
            return Auto()

        warnings.warn(_LEGACY_PARAMS_DEPRECATION, DeprecationWarning, stacklevel=3)
        resolved_line_length: Final = _DEFAULT_LINE_LENGTH if line_length is None else line_length
        resolved_line_buffers: Final = (
            _LEGACY_LINE_BUFFERS if line_buffers is None else line_buffers
        )
        budget: Final = resolved_line_length * resolved_line_buffers
        if legacy_frame is not None and legacy_frame != budget:
            logger.warning(
                f"max_smp_encoded_frame_size={legacy_frame} is not equal to "
                f"line_length={resolved_line_length} * line_buffers={resolved_line_buffers}; "
                f"using {budget}"
            )
        return BufferParams(line_length=resolved_line_length, line_buffers=resolved_line_buffers)

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
            case _ as unreachable:
                assert_never(unreachable)

    @property
    def _line_buffers(self) -> int:
        """The number of encoded line buffers spanned by the configured budget."""
        match self._fragmentation_strategy:
            case Auto():
                if self._smp_server_transport_buffer_size is not None:
                    return self._smp_server_transport_buffer_size // self._line_length
                return 1
            case BufferSize(buf_size=buf_size):
                return buf_size // self._line_length
            case BufferParams(line_buffers=line_buffers):
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
            case _ as unreachable:
                assert_never(unreachable)

    @override
    def initialize(self, smp_server_transport_buffer_size: int) -> None:
        """Initialize with the server's buffer size from MCUMGR_PARAM.

        Args:
            smp_server_transport_buffer_size: The server's CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE
        """
        super().initialize(smp_server_transport_buffer_size)

        match self._fragmentation_strategy:
            case Auto():
                logger.info(
                    f"Auto-configured from server: {self._line_length=}, "
                    f"{self._line_buffers=}, mtu={self._max_smp_encoded_frame_size}"
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
            case _ as unreachable:
                assert_never(unreachable)

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        self._reset_state()
        self._conn.port = address
        logger.debug(f"Connecting to {self._conn.port=}")
        start_time: Final = time.time()
        while time.time() - start_time <= timeout_s:
            try:
                self._conn.open()
                self._conn.reset_input_buffer()
                logger.debug(f"Connected to {self._conn.port=}")
                return
            except SerialException as e:
                logger.debug(
                    f"Failed to connect to {self._conn.port=}: {e}, "
                    f"retrying in {SMPSerialTransport._CONNECTION_RETRY_INTERVAL_S} seconds"
                )
                await asyncio.sleep(SMPSerialTransport._CONNECTION_RETRY_INTERVAL_S)

        raise TimeoutError(f"Failed to connect to {address=}")

    @override
    async def disconnect(self) -> None:
        logger.debug(f"Disconnecting from {self._conn.port=}")
        self._conn.close()
        logger.debug(f"Disconnected from {self._conn.port=}")

    @override
    async def send(self, data: bytes) -> None:
        if len(data) > self.max_unencoded_size:
            raise ValueError(
                f"Data size {len(data)} exceeds maximum unencoded size {self.max_unencoded_size}"
            )
        logger.debug(f"Sending {len(data)} bytes")
        try:
            for packet in smppacket.encode(data, line_length=self._line_length):
                self._conn.write(packet)
                logger.debug(f"Writing encoded packet of size {len(packet)}B; {self._line_length=}")

            # fake async until I get around to replacing pyserial
            while self._conn.out_waiting > 0:
                await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)
        except SerialException as e:
            logger.error(f"Failed to send {len(data)} bytes: {e}")
            raise SMPTransportDisconnected(
                f"{self.__class__.__name__} disconnected from {self._conn.port}"
            )

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
            try:
                data = self._conn.read_all() or b""
            except StopIteration:
                data = b""
            except SerialException as exc:
                raise SMPTransportDisconnected(f"Failed to read from {self._conn.port}: {exc}")

            if data:
                self._buffer.extend(data)
                await self._process_buffer()
            else:
                await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)

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
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

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

        `BufferParams` (and `Auto` before initialization) instead bound the message
        by an *encoded* line-buffer budget (`line_length * line_buffers`): how many
        unencoded bytes survive base64 expansion and per-line framing.

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
            case _ as unreachable:
                assert_never(unreachable)

    def _encoded_budget_max_unencoded_size(self) -> int:
        """Unencoded capacity within the encoded line-buffer budget (`mtu`).

        Subtracts per-line framing (the base64-encoded frame length + CRC16 plus a
        start/continue delimiter, per line buffer) and the stop delimiter, then
        converts the remaining encoded budget to its unencoded capacity.
        """
        packet_framing_size: Final = (
            _base64_cost(_FRAME_OVERHEAD) + smppacket.DELIMITER_SIZE
        ) * self._line_buffers + len(smppacket.END_DELIMITER)
        return _base64_max(self.mtu) - packet_framing_size
