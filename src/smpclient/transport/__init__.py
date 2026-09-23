"""Simple Management Protocol (SMP) Client Transport Protocol."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final, Generic, NamedTuple, Protocol, TypeAlias, TypeVar
from uuid import UUID

from typing_extensions import Self, assert_never, override

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


class Auto(NamedTuple):
    """Size messages from the server's MCUmgr parameters, read while the transport connects."""


class BufferSize(NamedTuple):
    """Size messages from a known server buffer; the server's parameters are not read."""

    buf_size: int
    """The server's SMP reassembly buffer (`CONFIG_MCUMGR_TRANSPORT_NETBUF_SIZE`)."""


class Unfragmented(NamedTuple):
    """Like `Auto`, but one SMP message per GATT write.

    For a server that does not reassemble a message split across writes (Zephyr without
    `CONFIG_MCUMGR_TRANSPORT_BT_REASSEMBLY`).
    """


GATTFragmentationStrategy: TypeAlias = Auto | Unfragmented | BufferSize
"""How a GATT transport (`ble`, `bumble`) sizes SMP messages."""


class SMPTransport(Protocol):
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
        ...


_TStrategy = TypeVar("_TStrategy")
"""A transport's fragmentation strategy union."""


class _ConnectableTransport(SMPTransport, Generic[_TStrategy]):
    """An `SMPTransport` that opens and closes its own link.

    `SMPClient` sees only the `SMPTransport` part.  Open a link with the `connected()` or
    `borrowed()` bracket: the bare `connect()`, `borrow()`, and `disconnect()` primitives give
    up the bracket's guarantee that the link is released on error and on cancellation.
    """

    def __init__(
        self,
        fragmentation_strategy: _TStrategy,
        connect_timeout_s: float,
        sequence: Callable[[], Iterator[u8]],
    ) -> None:
        self._fragmentation_strategy: Final = fragmentation_strategy
        self._sizing: _TStrategy = fragmentation_strategy
        """The fragmentation strategy as `negotiate()` resolved it."""
        self._connect_timeout_s: Final = connect_timeout_s
        self._sequence: Final = sequence()

    @abstractmethod
    async def disconnect(self) -> None:  # pragma: no cover
        """Release the link; prefer the bracket that opened it, which releases it on every exit."""

    @abstractmethod
    async def negotiate(self) -> None:  # pragma: no cover
        """Adopt the server's MCUmgr parameters, if the fragmentation strategy asks for them."""

    async def _read_buf_size(self) -> int | None:
        """The server's advertised `buf_size`, or `None` if it doesn't provide one."""
        params: Final = await _request.read_mcumgr_parameters(
            self, next(self._sequence), self._connect_timeout_s
        )
        return None if params is None else params.buf_size

    @asynccontextmanager
    async def _released_on_exit(self) -> AsyncIterator[Self]:
        """Yield the open link, then release it best-effort on every exit."""
        try:
            yield self
        finally:
            try:
                await self.disconnect()
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")


class _GATTTransport(_ConnectableTransport[GATTFragmentationStrategy]):
    """A `_ConnectableTransport` that writes SMP messages to a GATT characteristic."""

    @override
    async def negotiate(self) -> None:
        match self._fragmentation_strategy:
            case Auto():
                match await self._read_buf_size():
                    case None:
                        self._sizing = Auto()
                    case int() as buf_size:
                        self._sizing = BufferSize(buf_size)
                    case _ as unreachable:
                        assert_never(unreachable)
            case Unfragmented():
                match await self._read_buf_size():
                    case None:
                        self._sizing = Unfragmented()
                    case int() as buf_size:
                        self._sizing = BufferSize(min(self.mtu, buf_size))
                    case _ as unreachable:
                        assert_never(unreachable)
            case BufferSize():
                pass
            case _ as unreachable:
                assert_never(unreachable)

    @property
    @override
    def max_unencoded_size(self) -> int:
        match self._sizing:
            case Auto() | Unfragmented():
                return self.mtu
            case BufferSize(buf_size=buf_size):
                return buf_size
            case _ as unreachable:
                assert_never(unreachable)
