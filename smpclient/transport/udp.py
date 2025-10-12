"""A UDP SMPTransport for Network connections like Wi-Fi or Ethernet."""

import asyncio
import logging
from socket import AF_INET6
from typing import Final

from smp import header as smphdr
from typing_extensions import override

from smpclient.exceptions import SMPClientException
from smpclient.transport import SMPTransport
from smpclient.transport._udp_client import Addr, UDPClient

logger = logging.getLogger(__name__)

IPV4_HEADER_SIZE: Final = 20
"""Minimum IPv4 header size in bytes."""

IPV6_HEADER_SIZE: Final = 40
"""IPv6 header size in bytes."""

UDP_HEADER_SIZE: Final = 8
"""UDP header size in bytes."""

IPV4_UDP_OVERHEAD: Final = IPV4_HEADER_SIZE + UDP_HEADER_SIZE
"""Total overhead (28 bytes) to subtract from MTU to get maximum UDP payload (MSS) for IPv4.

Per RFC 8085 section 3.2, applications must subtract IP and UDP header sizes from the
PMTU to avoid fragmentation."""

IPV6_UDP_OVERHEAD: Final = IPV6_HEADER_SIZE + UDP_HEADER_SIZE
"""Total overhead (48 bytes) to subtract from MTU to get maximum UDP payload (MSS) for IPv6.

Per RFC 8085 section 3.2, applications must subtract IP and UDP header sizes from the
PMTU to avoid fragmentation."""


class SMPUDPTransport(SMPTransport):
    def __init__(self, mtu: int = 1500) -> None:
        """Initialize the SMP UDP transport.

        Args:
            mtu: The Maximum Transmission Unit (MTU) of the link layer in bytes.
                IP and UDP header overhead will be subtracted to calculate the maximum
                UDP payload size (MSS) to avoid fragmentation per RFC 8085 section 3.2.
        """
        self._mtu = mtu
        self._is_ipv6 = False

        self._client: Final = UDPClient()

    @override
    async def connect(self, address: str, timeout_s: float, port: int = 1337) -> None:
        logger.debug(f"Connecting to {address=} {port=}")
        await asyncio.wait_for(self._client.connect(Addr(host=address, port=port)), timeout_s)

        if sock := self._client._transport.get_extra_info('socket'):
            self._is_ipv6 = sock.family == AF_INET6
            logger.debug(f"Detected {'IPv6' if self._is_ipv6 else 'IPv4'} connection")

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
        """Maximum UDP payload size (MSS) to avoid fragmentation.

        Subtracts IPv4/IPv6 and UDP header overhead from MTU per RFC 8085 section 3.2.
        The IP version is auto-detected after connection.
        """
        overhead = IPV6_UDP_OVERHEAD if self._is_ipv6 else IPV4_UDP_OVERHEAD
        return self._mtu - overhead
