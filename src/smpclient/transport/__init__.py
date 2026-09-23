"""Simple Management Protocol (SMP) Client Transport Protocol."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final, Protocol
from uuid import UUID

from typing_extensions import Self

from smpclient import _request

if TYPE_CHECKING:
    from types_bits import u8

logger: Final = logging.getLogger(__name__)

SMP_SERVICE_UUID: Final = UUID("8D53DC1D-1DB7-4CD3-868B-8A527460AA84")
"""The 128-bit GATT service UUID for an SMP server.

Shared by all GATT-based transports (`ble`, `bumble`) so the constant has a
single source of truth.
"""

SMP_CHARACTERISTIC_UUID: Final = UUID("DA2E7828-FBCE-4E01-AE9E-261174997C48")
"""The 128-bit GATT characteristic UUID for the SMP write+notify channel."""


class SMPTransportDisconnected(Exception):
    """Raised when the SMP transport is disconnected."""


class SMPTransport(Protocol):
    _smp_server_transport_buffer_size: int | None = None
    """The SMP server transport buffer size, in 8-bit bytes."""

    async def send(self, data: bytes) -> None:  # pragma: no cover
        """Send the encoded `SMPRequest` `data`.

        Args:
            data: The encoded `SMPRequest`.
        """
        ...

    async def receive(self) -> bytes:  # pragma: no cover
        """Receive the decoded `SMPResponse` data.

        Returns:
            The `SMPResponse` bytes.
        """
        ...

    async def send_and_receive(self, data: bytes) -> bytes:  # pragma: no cover
        """Send the encoded `SMPRequest` `data` and receive the decoded `SMPResponse`.

        Args:
            data: The encoded `SMPRequest`.

        Returns:
            The `SMPResponse` bytes.
        """
        ...

    def initialize(self, smp_server_transport_buffer_size: int) -> None:  # pragma: no cover
        """Initialize the `SMPTransport` with the server transport buffer size.

        Args:
            smp_server_transport_buffer_size: The SMP server transport buffer size, in 8-bit bytes.
        """
        self._smp_server_transport_buffer_size = smp_server_transport_buffer_size

    @property
    def mtu(self) -> int:  # pragma: no cover
        """The Maximum Transmission Unit (MTU) in 8-bit bytes."""
        ...

    @property
    def max_unencoded_size(self) -> int:  # pragma: no cover
        """The maximum size of an unencoded message that can be sent, in 8-bit bytes."""
        # There is a potential speedup in the future by taking advantage of the
        # multiple buffers that are provided by the SMP server implementation.
        # Generally, the idea is to send as many as buf_count messages BEFORE
        # awaiting the response.  This will allow the SMP server to buffer the
        # new IO while waiting for flash writes to complete.  It creates some
        # complexity in both the client and server and it's debatable whether
        # or not the speedup is worth the complexity.  Specifically, if there is
        # an error in some write, then some of the writes that have already been
        # sent out are no longer valid.  That is, the response to each
        # concurrent write needs to be tracked very carefully!

        return self._smp_server_transport_buffer_size or self.mtu


class _ConnectableTransport(SMPTransport, Protocol):
    """An `SMPTransport` that opens and closes its own link.

    `SMPClient` sees only the `SMPTransport` part.  Prefer the `connected()` bracket;
    `connect()` and `disconnect()` are for a lifetime that a lexical scope can't express.
    """

    _sequence: Iterator[u8]
    """The SMP sequence space that the MCUmgr parameters read draws from."""

    _connect_timeout_s: float
    """Bounds establishing the link, including reading the MCUmgr parameters."""

    async def connect(self) -> None:  # pragma: no cover
        """Open the link, then `negotiate()`."""
        ...

    async def disconnect(self) -> None:  # pragma: no cover
        """Close the link."""
        ...

    async def negotiate(self) -> None:
        """Adopt the server's MCUmgr parameters, if it provides them."""
        params: Final = await _request.read_mcumgr_parameters(
            self, next(self._sequence), self._connect_timeout_s
        )
        if params is not None:
            self.initialize(params.buf_size)

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[Self]:
        """Open the link for the duration of the `async with`, then close it."""
        await self.connect()
        try:
            yield self
        finally:
            try:
                await self.disconnect()
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
