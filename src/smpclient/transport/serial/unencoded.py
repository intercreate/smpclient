"""The unencoded (raw) serial SMPTransport.

This is the Zephyr "raw UART" SMP transport, enabled on the server by
`CONFIG_MCUMGR_TRANSPORT_RAW_UART` together with `CONFIG_UART_MCUMGR_RAW_PROTOCOL`.
Each SMP message is sent over the wire as the raw bytes
`[8-byte SMP header][header.length bytes of payload]` with no framing, encoding,
or CRC.  The receiver parses the SMP header to determine the message length.

This transport cannot coexist with shell or log output on the same UART.  If
you need shell interleaving, use `SMPSerialTransport` from
`smpclient.transport.serial.encoded`.
"""

import asyncio
import logging
from typing import Final

from smp import header as smphdr
from typing_extensions import override

from smpclient.exceptions import SMPClientException
from smpclient.transport.serial.common import _SerialTransportBase

logger = logging.getLogger(__name__)


class SMPSerialRawTransport(_SerialTransportBase):
    def __init__(
        self,
        mtu: int = 384,
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
        """Initialize the raw serial transport.

        Args:
            mtu: The maximum size of one SMP message (header + payload), in
                bytes.  A serial link has no MTU of its own, but the SMP
                server's receive buffer does -- this should match the server's
                `CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE` (Zephyr default 384).
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
            exclusive: Set exclusive access mode (POSIX only).  A port cannot be
                opened in exclusive access mode if it is already open in
                exclusive access mode.
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
        self._mtu: Final = mtu

        logger.debug(f"Initialized {self.__class__.__name__}")

    @override
    async def send(self, data: bytes) -> None:
        if len(data) > self.max_unencoded_size:
            raise ValueError(
                f"Data size {len(data)} exceeds maximum unencoded size {self.max_unencoded_size}"
            )
        logger.debug(f"Sending {len(data)} bytes")
        with self._serial_exception_to_disconnected():
            self._conn.write(data)
            await self._drain_tx()
        logger.debug(f"Sent {len(data)} bytes")

    @override
    async def receive(self) -> bytes:
        logger.debug("Waiting for response")
        message = bytearray()

        while len(message) < smphdr.Header.SIZE:
            await self._poll_read_into(message)

        header: Final = smphdr.Header.loads(bytes(message[: smphdr.Header.SIZE]))
        message_length: Final = header.length + smphdr.Header.SIZE
        logger.debug(f"Received {header=}; awaiting {message_length} B total")

        # The header's length field is attacker/noise-controlled - bound it before
        # we start waiting for that many bytes to arrive.
        if message_length > self.max_unencoded_size:
            error = (
                f"Header claims a {message_length} B message, "
                f"exceeding max_unencoded_size={self.max_unencoded_size}"
            )
            logger.error(error)
            raise SMPClientException(error)

        while len(message) < message_length:
            await self._poll_read_into(message)

        if len(message) > message_length:
            error = f"Received more data than expected: {len(message)} B > {message_length} B"
            logger.error(error)
            raise SMPClientException(error)

        logger.debug(f"Finished receiving {message_length} B response")
        return bytes(message)

    async def _poll_read_into(self, buf: bytearray) -> None:
        """Read available bytes into `buf`; if none, yield via a short sleep."""
        data = await self._read_all()
        if data:
            buf.extend(data)
        else:
            await asyncio.sleep(self._POLLING_INTERVAL_S)

    @override
    @property
    def mtu(self) -> int:
        return self._mtu
