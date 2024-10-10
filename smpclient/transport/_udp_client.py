"""A UDP Client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, NamedTuple, Tuple

from typing_extensions import override

logger = logging.getLogger(__name__)


class Addr(NamedTuple):
    host: str
    port: int


class UDPClient:
    """Implementation of a UDP client."""

    async def connect(self, remote_addr: Addr, _local_addr: Addr | None = None) -> None:
        """Create a UDP connection to the given `Addr`.

        Args:
            remote_addr: The remote address to connect to.
            _local_addr: For unit tests only!  The local address to connect from.

        Example:

        ```python

        c = UDPClient()
        await c.connect(Addr("192.168.55.55", 1337))
        ```
        """

        self._transport, self._protocol = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _UDPProtocol(),
            remote_addr=remote_addr,
            local_addr=_local_addr,
        )

    def send(self, data: bytes) -> None:
        """Send data to the transport.

        This does not block; it buffers the data and arranges for it to be sent
        out asynchronously.

        Args:
            data: The data to send.
        """

        self._transport.sendto(data)

    async def receive(self) -> bytes:
        """Receive data from the transport.

        Returns:
            bytes: The data received
        """

        return await self._protocol.receive_queue.get()

    def disconnect(self) -> None:
        self._transport.close()


class _UDPProtocol(asyncio.DatagramProtocol):
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
        logger.info("Connection lost")
        if exc is not None:
            logger.error(f"Connection lost {exc=}")
            self._error_queue.put_nowait(exc)

    @property
    def receive_queue(self) -> asyncio.Queue[bytes]:
        return self._receive_queue

    @property
    def error_queue(self) -> asyncio.Queue[Exception]:
        return self._error_queue
