"""A serial SMPTransport."""

import asyncio
import math
from functools import cached_property
from typing import Final

from serial import Serial
from smp import packet as smppacket


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


class SMPSerialTransport:
    _POLLING_INTERVAL_S = 0.005

    def __init__(
        self,
        mtu: int = 4096,
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
        exclusive: float | None = None,
    ) -> None:
        self._mtu: Final = mtu
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
        self._buffer = bytearray([])

    async def connect(self, address: str) -> None:
        self._conn.port = address
        self._conn.open()

    async def disconnect(self) -> None:
        self._conn.close()

    async def send(self, data: bytes) -> None:
        for packet in smppacket.encode(data, line_length=self.max_unencoded_size):
            if len(packet) > self.mtu:  # pragma: no cover
                raise Exception(
                    f"Encoded packet size {len(packet)} exceeds {self.mtu=}, this is a bug!"
                )
            self._conn.write(packet)

        # fake async until I get around to replacing pyserial
        while self._conn.out_waiting > 0:
            await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)

    async def receive(self) -> bytes:
        decoder = smppacket.decode()
        next(decoder)

        while True:
            try:
                b = await self._readuntil()
                decoder.send(b)
            except StopIteration as e:
                return e.value

    async def _readuntil(self, delimiter: bytes = b"\n") -> bytes:
        """Read `bytes` until the `delimiter` then return the `bytes` including the `delimiter`."""

        # fake async until I get around to replacing pyserial

        i = 0
        while True:
            # read the entire OS buffer
            self._buffer.extend(self._conn.read_all() or [])

            try:  # search the buffer for the index of the delimiter
                i = self._buffer.index(delimiter, i) + len(delimiter)

                # out is everything up to and including the delimiter
                out = self._buffer[:i]

                # there may be some leftover to save for the next read
                self._buffer = self._buffer[i:]

                return out

            except ValueError:  # delimiter not found yet, wait
                await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)

    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @property
    def mtu(self) -> int:
        return self._mtu

    @cached_property
    def max_unencoded_size(self) -> int:
        """The serial transport encodes each packet instead of sending SMP messages as raw bytes.

        The worst case size of an encoded SMP packet is:
        ```
        base64_cost(
            len(smp_message) + len(frame_length) + len(frame_crc16)
        ) + len(delimiter) + len(line_ending)
        ```
        This simplifies to:
        ```
        base64_cost(len(smp_message) + 4) + 3
        ```

        This property specifies the maximum size of an SMP message before it has been encoded for
        the serial transport.
        """

        packet_framing_size: Final = (
            _base64_cost(smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size)
            + smppacket.DELIMITER_SIZE
            + len(smppacket.CR)
        )

        return _base64_max(self.mtu) - packet_framing_size
