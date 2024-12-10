"""A UDP SMPTransport for Network connections like Wi-Fi or Ethernet."""

import asyncio
import logging
from typing import Final

from smp import header as smphdr
from typing_extensions import override

from smpclient.exceptions import SMPClientException
from smpclient.transport import SMPTransport
from smpclient.transport._udp_client import Addr, UDPClient

logger = logging.getLogger(__name__)


class SMPUDPTransport(SMPTransport):
    def __init__(self, mtu: int = 1500) -> None:
        """Initialize the SMP UDP transport.

        Args:
            mtu: The Maximum Transmission Unit (MTU) in 8-bit bytes.
        """
        self._mtu = mtu

        self._client: Final = UDPClient()

    @override
    async def connect(self, address: str, timeout_s: float, port: int = 1337) -> None:
        logger.debug(f"Connecting to {address=} {port=}")
        await asyncio.wait_for(self._client.connect(Addr(host=address, port=port)), timeout_s)
        logger.info(f"Connected to {address=} {port=}")

    @override
    async def disconnect(self) -> None:
        logger.debug("Disconnecting from transport")
        self._client.disconnect()

        if not self._client._protocol.error_queue.empty():
            logger.warning(
                f"{self._client._protocol.error_queue.qsize()} exceptions were uncollected before "
                "disconnecting, fetching them now"
            )
            while True:
                try:
                    logger.warning(f"{self._client._protocol.error_queue.get_nowait()}")
                except asyncio.QueueEmpty:
                    break

        logger.info("Disconnected from transport")

    @override
    async def send(self, data: bytes) -> None:
        if len(data) > self.max_unencoded_size:
            logger.warning(
                "Fragmenting UDP packets is not recommended: "
                f"{len(data)=} B > {self.max_unencoded_size=} B"
            )

        logger.debug(f"Sending {len(data)} B")
        for offset in range(0, len(data), self.max_unencoded_size):
            self._client.send(data[offset : offset + self.max_unencoded_size])
        logger.debug(f"Sent {len(data)} B")

    @override
    async def receive(self) -> bytes:
        logger.debug("Awaiting data")

        first_packet: Final = await self._client.receive()
        logger.debug(f"Received {len(first_packet)} B")

        header: Final = smphdr.Header.loads(first_packet[: smphdr.Header.SIZE])
        logger.debug(f"Received {header=}")

        message_length: Final = header.length + smphdr.Header.SIZE
        message: Final = bytearray(first_packet)

        if len(message) != message_length:
            logger.debug(f"Waiting for the rest of the {message_length} B response")
            while len(message) < message_length:
                packet = await self._client.receive()
                logger.debug(f"Received {len(packet)} B")
                message.extend(packet)
            if len(message) > message_length:
                error: Final = (
                    f"Received more data than expected: {len(message)} B > {message_length} B"
                )
                logger.error(error)
                raise SMPClientException(error)

        logger.debug(f"Finished receiving message of length {message_length} B")
        return message

    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @override
    @property
    def mtu(self) -> int:
        return self._mtu

    @override
    @property
    def max_unencoded_size(self) -> int:
        return self._mtu
