"""A UDP SMPTransport for Network connections like Wi-Fi or Ethernet."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from socket import AF_INET6
from typing import TYPE_CHECKING, Final, TypeAlias

from smp import header as smphdr
from typing_extensions import Self, assert_never, override

from smpclient import _request
from smpclient.exceptions import SMPClientException
from smpclient.transport import Auto, BufferSize, _ConnectableTransport
from smpclient.transport._udp_client import Addr, UDPClient

if TYPE_CHECKING:
    from types_bits import u8

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


UDPFragmentationStrategy: TypeAlias = Auto | BufferSize
"""How `SMPUDPTransport` sizes SMP messages: `Auto` or `BufferSize`.

Either way a message never exceeds one datagram's payload (the MSS): the server receives
each request as a single datagram into a single buffer.
"""


class SMPUDPTransport(_ConnectableTransport[UDPFragmentationStrategy]):
    def __init__(
        self,
        mtu: int = 1500,
        *,
        fragmentation_strategy: UDPFragmentationStrategy = Auto(),
        connect_timeout_s: float = 2.5,
        sequence: Callable[[], Iterator[u8]] = _request.wrapping_sequence,
    ) -> None:
        """Initialize the SMP UDP transport.

        Args:
            mtu: The Maximum Transmission Unit (MTU) of the link layer in bytes.
                IP and UDP header overhead will be subtracted to calculate the maximum
                UDP payload size (MSS) to avoid fragmentation per RFC 8085 section 3.2.
            fragmentation_strategy: How to size SMP messages: `Auto` or `BufferSize`.
            connect_timeout_s: Bounds connecting, and reading the server's MCUmgr
                parameters.
            sequence: The SMP sequence space the MCUmgr parameters read draws from.
        """
        super().__init__(fragmentation_strategy, connect_timeout_s, sequence)
        self._mtu: Final = mtu
        self._is_ipv6 = False

        self._client: Final = UDPClient()

    async def connect(self, address: str, port: int = 1337) -> None:
        """Connect to `address`:`port`, then `negotiate()`; prefer `connected()`."""
        logger.debug(f"Connecting to {address=} {port=}")
        await asyncio.wait_for(
            self._client.connect(Addr(host=address, port=port)), self._connect_timeout_s
        )

        if sock := self._client._transport.get_extra_info('socket'):
            self._is_ipv6 = sock.family == AF_INET6
            logger.debug(f"Detected {'IPv6' if self._is_ipv6 else 'IPv4'} connection")

        logger.info(f"Connected to {address=} {port=}")
        try:
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            await self.disconnect()
            raise

    @asynccontextmanager
    async def connected(self, address: str, port: int = 1337) -> AsyncIterator[Self]:
        """Connect to `address`:`port` for the duration of the `async with`, then disconnect."""
        await self.connect(address, port)
        async with self._released_on_exit():
            yield self

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
        return bytes(message)

    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @property
    @override
    def mtu(self) -> int:
        return self._mtu

    @override
    async def negotiate(self) -> None:
        match self._fragmentation_strategy:
            case Auto():
                match await self._read_buf_size():
                    case None:
                        self._sizing = Auto()
                    case buf_size:
                        self._sizing = BufferSize(buf_size)
            case BufferSize():
                pass
            case _ as unreachable:
                assert_never(unreachable)

    @property
    @override
    def max_unencoded_size(self) -> int:
        """Maximum UDP payload size (MSS) to avoid fragmentation, capped at the server's buffer.

        Subtracts IPv4/IPv6 and UDP header overhead from MTU per RFC 8085 section 3.2.
        The IP version is auto-detected after connection.
        """
        mss: Final = self._mtu - (IPV6_UDP_OVERHEAD if self._is_ipv6 else IPV4_UDP_OVERHEAD)
        match self._sizing:
            case Auto():
                return mss
            case BufferSize(buf_size=buf_size):
                return min(mss, buf_size)
            case _ as unreachable:
                assert_never(unreachable)
