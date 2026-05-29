"""Simple Management Protocol (SMP) Client Transport Protocol."""

from typing import Final, Protocol
from uuid import UUID

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

    async def connect(self, address: str, timeout_s: float) -> None:  # pragma: no cover
        """Connect the `SMPTransport`.

        Args:
            address: The SMP server address.
            timeout_s: The connection timeout in seconds.
        """

    async def disconnect(self) -> None:  # pragma: no cover
        """Disconnect the `SMPTransport`."""

    async def send(self, data: bytes) -> None:  # pragma: no cover
        """Send the encoded `SMPRequest` `data`.

        Args:
            data: The encoded `SMPRequest`.
        """

    async def receive(self) -> bytes:  # pragma: no cover
        """Receive the decoded `SMPResponse` data.

        Returns:
            The `SMPResponse` bytes.
        """

    async def send_and_receive(self, data: bytes) -> bytes:  # pragma: no cover
        """Send the encoded `SMPRequest` `data` and receive the decoded `SMPResponse`.

        Args:
            data: The encoded `SMPRequest`.

        Returns:
            The `SMPResponse` bytes.
        """

    def initialize(self, smp_server_transport_buffer_size: int) -> None:  # pragma: no cover
        """Initialize the `SMPTransport` with the server transport buffer size.

        Args:
            smp_server_transport_buffer_size: The SMP server transport buffer size, in 8-bit bytes.
        """
        self._smp_server_transport_buffer_size = smp_server_transport_buffer_size

    @property
    def mtu(self) -> int:  # pragma: no cover
        """The Maximum Transmission Unit (MTU) in 8-bit bytes."""

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
