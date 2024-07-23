import asyncio
import logging
from typing import Any, Final, NamedTuple, Tuple, override

from smp import header as smphdr

from smpclient.transport import SMPTransport, SMPTransportDisconnected

logger = logging.getLogger(__name__)


class SMPUDPTransport(SMPTransport):
    def __init__(self) -> None:
        self._client: Final = UDPClient()

    @override
    async def connect(self, address: str, timeout_s: float, port: int = 1337) -> None:
        logger.debug(f"Connecting to {address=} {port=}")
        await asyncio.wait_for(self._client.connect(RemoteAddr(host=address, port=port)), timeout_s)
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
        logger.debug(f"Sending {len(data)} B, {self.mtu=}")
        for offset in range(0, len(data), self.mtu):
            self._client.send(data[offset : offset + self.mtu])
        logger.debug(f"Sent {len(data)} B")

    @override
    async def receive(self) -> bytes:
        logger.debug("Receiving data")

        first_packet: Final = await self._client.receive()
        logger.debug(f"Received {len(first_packet)} B")

        header: Final = smphdr.Header.loads(first_packet[: smphdr.Header.SIZE])
        logger.debug(f"Received {header=}")

        message_length: Final = header.length + smphdr.Header.SIZE
        message: Final = bytearray(first_packet)

        if len(first_packet) != message_length:
            logger.debug(f"Waiting for the rest of the {message_length} byte response")
            while len(message) < message_length:
                packet = await self._client.receive()
                logger.debug(f"Received {len(packet)} B")
                message.extend(packet)
            if len(message) > message_length:
                logger.error(
                    f"Received more data than expected: {len(message)} B > {message_length} B"
                )
                raise Exception

        logger.debug(f"Finished receiving message of length {message_length}")
        return message

    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @override
    @property
    def mtu(self) -> int:
        return 1500


class RemoteAddr(NamedTuple):
    host: str
    port: int


class UDPClient:
    """Implementation of a UDP client."""

    async def connect(self, remote_addr: RemoteAddr) -> None:
        """Create a UDP connection to the given `RemoteAddr`."""

        self._transport, self._protocol = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: UDPProtocol(),
            remote_addr=remote_addr,
        )

    def send(self, data: bytes) -> None:
        """Send data to the transport.

        This does not block; it buffers the data and arranges for it to be sent
        out asynchronously.
        """

        self._transport.sendto(data)

    async def receive(self) -> bytes:
        """Receive data from the transport."""

        return await self._protocol.receive_queue.get()

    def disconnect(self) -> None:
        self._transport.close()


class UDPProtocol(asyncio.DatagramProtocol):
    """Implementation of a UDP protocol."""

    @override
    def __init__(self) -> None:
        self._receive_queue: Final[asyncio.Queue[bytes]] = asyncio.Queue()
        self._error_queue: Final[asyncio.Queue[Exception]] = asyncio.Queue()

    @override
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        logger.debug(f"Connection made, {transport=}")

    @override
    def datagram_received(self, data: bytes, addr: Tuple[str | Any, int]) -> None:
        logger.debug(f"{len(data)} B datagram received from {addr}")
        self._receive_queue.put_nowait(data)

    @override
    def error_received(self, exc: Exception) -> None:
        logger.warning(f"Error received: {exc=}")
        self._error_queue.put_nowait(exc)

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        logger.error(f"Connection lost: {exc=}")
        if exc is not None:
            self._error_queue.put_nowait(exc)

    @property
    def receive_queue(self) -> asyncio.Queue[bytes]:
        return self._receive_queue

    @property
    def error_queue(self) -> asyncio.Queue[Exception]:
        return self._error_queue
