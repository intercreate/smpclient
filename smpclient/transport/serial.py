"""A serial SMPTransport.

In addition to UART, this transport can be used with USB CDC ACM and CAN.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from enum import IntEnum, unique
from functools import cached_property
from typing import Final

from serial import Serial, SerialException
from smp import packet as smppacket
from typing_extensions import override

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

    def __init__(  # noqa: DOC301
        self,
        max_smp_encoded_frame_size: int = 256,
        line_length: int = 128,
        line_buffers: int = 2,
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
            max_smp_encoded_frame_size: The maximum size of an encoded SMP
                frame.  The SMP server needs to have a buffer large enough to
                receive the encoded frame packets and to store the decoded frame.
            line_length: The maximum SMP packet size.
            line_buffers: The number of line buffers in the serial buffer.
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
        if max_smp_encoded_frame_size < line_length * line_buffers:
            logger.error(
                f"{max_smp_encoded_frame_size=} is less than {line_length=} * {line_buffers=}!"
            )
        elif max_smp_encoded_frame_size != line_length * line_buffers:
            logger.warning(
                f"{max_smp_encoded_frame_size=} is not equal to {line_length=} * {line_buffers=}!"
            )

        self._max_smp_encoded_frame_size: Final = max_smp_encoded_frame_size
        self._line_length: Final = line_length
        self._line_buffers: Final = line_buffers
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
        """Contains full SMP packets; filled by reader loop and read by .receive()."""
        self._serial_buffer = bytearray()
        """Contains any non-SMP serial data."""
        self._buffer: bytearray = bytearray([])
        """Buffer for all incoming data (serial + SMP intertwined)."""
        self._buffer_read_state = SMPSerialTransport.BufferState.SERIAL
        """The state of the read buffer."""

        logger.debug(f"Initialized {self.__class__.__name__}")

    def _reset_state(self) -> None:
        """Reset internal state and queues for a fresh connection."""

        self._smp_packet_queue = asyncio.Queue()
        self._serial_buffer.clear()
        self._buffer = bytearray([])
        self._buffer_read_state = SMPSerialTransport.BufferState.SERIAL

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        self._reset_state()
        self._conn.port = address
        logger.debug(f"Connecting to {self._conn.port=}")
        start_time: Final = time.time()
        while time.time() - start_time <= timeout_s:
            try:
                self._conn.open()
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
        """Returns one received SMP packet from the queue, or raises exception if disconnected."""
        await self._read_and_process(read_until_one_smp_packet=True)
        return await self._smp_packet_queue.get()

    async def read_serial(self, delimiter: bytes | None = None) -> bytes:
        """Reads regular serial traffic (non-SMP bytes) until given delimiter.
        Returns all available bytes if no delimiter is given.
        May return empty bytes if nothing is available."""
        await self._read_and_process(read_until_one_smp_packet=False)
        if delimiter is None:
            res = bytes(self._serial_buffer)
            self._serial_buffer.clear()
            return res
        else:
            try:
                first_line, remaining_data = self._serial_buffer.split(delimiter, 1)
            except ValueError:
                return bytes()
            self._serial_buffer = remaining_data
            return bytes(first_line)

    async def _read_and_process(self, read_until_one_smp_packet: bool) -> None:
        """Reads raw data from serial and processes it into raw serial data and SMP packets."""
        try:
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

                if read_until_one_smp_packet and self._smp_packet_queue.empty():
                    continue
                else:
                    break

        except asyncio.CancelledError:
            raise

    async def _process_buffer(self) -> None:
        """Process buffered data until more bytes are needed."""

        while True:
            if self._buffer_read_state == SMPSerialTransport.BufferState.SERIAL:
                should_continue = await self._process_buffer_as_serial_data()
            else:
                should_continue = await self._process_buffer_as_smp_data()

            if not should_continue:
                break

    async def _process_buffer_as_serial_data(self) -> bool:
        """Handle non-SMP data and transition to SMP state when finding SMP frame-start delimiters.
        Return True if further data remains to process in the buffer; return False otherwise."""

        smp_packet_start: int = self._find_smp_packet_start(self._buffer)
        if smp_packet_start >= 0:
            serial_data, remaining_data = (
                self._buffer[:smp_packet_start],
                self._buffer[smp_packet_start:],
            )
            self._serial_buffer.extend(serial_data)

            self._buffer = remaining_data
            self._buffer_read_state = SMPSerialTransport.BufferState.SMP
            return True

        # Everything is serial data:
        self._serial_buffer.extend(self._buffer)
        self._buffer.clear()
        return False

    async def _process_buffer_as_smp_data(self) -> bool:
        """Handle SMP data and transition to SERIAL state when finding SMP frame-end delimiter.
        Return True if further data remains to process in the buffer; return False otherwise."""

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
        self._buffer_read_state = SMPSerialTransport.BufferState.SERIAL

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

    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @override
    @property
    def mtu(self) -> int:
        return self._max_smp_encoded_frame_size

    @override
    @cached_property
    def max_unencoded_size(self) -> int:
        """The serial transport encodes each packet instead of sending SMP messages as raw bytes."""

        # For each packet, AKA line_buffer, include the cost of the base64
        # encoded frame_length and CRC16 and the start/continue delimiter.
        # Add to that the cost of the stop delimiter.
        packet_framing_size: Final = (
            _base64_cost(smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size)
            + smppacket.DELIMITER_SIZE
        ) * self._line_buffers + len(smppacket.END_DELIMITER)

        # Get the number of unencoded bytes that can fit in self.mtu and
        # subtract the cost of framing the separate packets.
        # This is the maximum number of unencoded bytes that can be received by
        # the SMP server with this transport configuration.
        return _base64_max(self.mtu) - packet_framing_size
