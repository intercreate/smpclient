"""A serial SMPTransport."""

import asyncio

from serial import Serial  # type: ignore


class SMPSerialTransport:
    _POLLING_INTERVAL_S = 0.005

    def __init__(self) -> None:
        self._conn = Serial()
        self._buffer = bytearray([])

    async def connect(self, address: str) -> None:
        self._conn.port = address
        self._conn.open()

    def write(self, data: bytes) -> None:
        self._conn.write(data)

    async def send(self, data: bytes) -> None:
        self.write(data)

        # fake async until I get around to replacing pyserial
        while self._conn.out_waiting > 0:
            await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)

    async def readuntil(self, delimiter: bytes = b"\n") -> bytes:
        # fake async until I get around to replacing pyserial
        while True:
            # read the entire OS buffer
            os_buffer = self._conn.read_all()

            # iterate the whole buffer to check for the delimiter
            for i in range(0, len(os_buffer) - (len(delimiter) - 1)):
                if os_buffer[i : i + len(delimiter)] == delimiter:
                    # out is whatever was previously in the buffer plus the
                    # just read OS buffer including the delimiter
                    out = self._buffer + os_buffer[: i + len(delimiter)]

                    # there may be some leftover to save for the next read
                    self._buffer = os_buffer[i + len(delimiter) :]

                    return out

            # delimiter was not reached, save the buffer and wait
            self._buffer.extend(os_buffer)
            await asyncio.sleep(SMPSerialTransport._POLLING_INTERVAL_S)
