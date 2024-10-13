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

    class _ReadBuffer:
        """The state of the read buffer."""

        @unique
        class State(IntEnum):
            SMP = 0
            """An SMP start or continue delimiter has been received and the
            `smp_buffer` is being filled with the remainder of the SMP packet.
            """

            SER = 1
            """The SMP start delimiter has not been received and the
            `ser_buffer` is being filled with data.
            """

        def __init__(self) -> None:
            self.smp = bytearray([])
            """The buffer for the SMP packet."""

            self.ser = bytearray([])
            """The buffer for serial data that is not part of an SMP packet."""

            self.state = SMPSerialTransport._ReadBuffer.State.SER
            """The state of the read buffer."""

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
        self._buffer = SMPSerialTransport._ReadBuffer()
        logger.debug(f"Initialized {self.__class__.__name__}")

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
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
            try:
                b = await self._readuntil()
                decoder.send(b)
            except StopIteration as e:
                logger.debug(f"Finished receiving {len(e.value)} byte response")
                return e.value
            except SerialException as e:
                logger.error(f"Failed to receive response: {e}")
                raise SMPTransportDisconnected(
                    f"{self.__class__.__name__} disconnected from {self._conn.port}"
                )

    async def _readuntil(self) -> bytes:
        """Read `bytes` until the `delimiter` then return the `bytes` including the `delimiter`."""

        START_DELIMITER: Final = smppacket.SIXTY_NINE
        CONTINUE_DELIMITER: Final = smppacket.FOUR_TWENTY
        END_DELIMITER: Final = b"\n"

        # fake async until I get around to replacing pyserial

        i_smp_start = 0
        i_smp_end = 0
        i_start: int | None = None
        i_continue: int | None = None
        while True:
            if self._buffer.state == SMPSerialTransport._ReadBuffer.State.SER:
                # read the entire OS buffer
                try:
                    self._buffer.ser.extend(self._conn.read_all() or [])
                except StopIteration:
                    pass

                try:  # search the buffer for the index of the start delimiter
                    i_start = self._buffer.ser.index(START_DELIMITER)
                except ValueError:
                    i_start = None

                try:  # search the buffer for the index of the continue delimiter
                    i_continue = self._buffer.ser.index(CONTINUE_DELIMITER)
                except ValueError:
                    i_continue = None

                if i_start is not None and i_continue is not None:
                    i_smp_start = min(i_start, i_continue)
                elif i_start is not None:
                    i_smp_start = i_start
                elif i_continue is not None:
                    i_smp_start = i_continue
                else:  # no delimiters found yet, clear non SMP data and wait
                    while True:
                        try:  # search the buffer for newline characters
                            i = self._buffer.ser.index(b"\n")
                            try:  # log as a string if possible
                                logger.warning(
                                    f"{self._conn.port}: {self._buffer.ser[:i].decode()}"
                                )
                            except UnicodeDecodeError:  # log as bytes if not
                                logger.warning(f"{self._conn.port}: {self._buffer.ser[:i].hex()}")
                            self._buffer.ser = self._buffer.ser[i + 1 :]
                        except ValueError:
                            break
                    await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)
                    continue

                if i_smp_start != 0:  # log the rest of the serial buffer
                    try:  # log as a string if possible
                        logger.warning(
                            f"{self._conn.port}: {self._buffer.ser[:i_smp_start].decode()}"
                        )
                    except UnicodeDecodeError:  # log as bytes if not
                        logger.warning(f"{self._conn.port}: {self._buffer.ser[:i_smp_start].hex()}")

                self._buffer.smp = self._buffer.ser[i_smp_start:]
                self._buffer.ser.clear()
                self._buffer.state = SMPSerialTransport._ReadBuffer.State.SMP
                i_smp_end = 0

                # don't await since the buffer may already contain the end delimiter

            elif self._buffer.state == SMPSerialTransport._ReadBuffer.State.SMP:
                # read the entire OS buffer
                try:
                    self._buffer.smp.extend(self._conn.read_all() or [])
                except StopIteration:
                    pass

                try:  # search the buffer for the index of the delimiter
                    i_smp_end = self._buffer.smp.index(END_DELIMITER, i_smp_end) + len(
                        END_DELIMITER
                    )
                except ValueError:  # delimiter not found yet, wait
                    await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)
                    continue

                # out is everything up to and including the delimiter
                out = self._buffer.smp[:i_smp_end]
                logger.debug(f"Received {len(out)} byte chunk")

                # there may be some leftover to save for the next read, but
                # it's not necessarily SMP data
                self._buffer.ser = self._buffer.smp[i_smp_end:]

                self._buffer.state = SMPSerialTransport._ReadBuffer.State.SER

                return out

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
