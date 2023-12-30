"""Simple Management Protocol (SMP) Client Transport Protocol."""

from typing import Protocol


class SMPTransport(Protocol):
    async def connect(self, address: str) -> None:  # pragma: no cover
        """Connect the `SMPTransport`."""

    async def disconnect(self) -> None:  # pragma: no cover
        """Disconnect the `SMPTransport`."""

    async def send(self, data: bytes) -> None:  # pragma: no cover
        """Send the encoded `SMPRequest` `data`."""

    async def receive(self) -> bytes:  # pragma: no cover
        """Receive the decoded `SMPResponse`."""

    async def send_and_receive(self, data: bytes) -> bytes:  # pragma: no cover
        """Send the encoded `SMPRequest` `data` and receive the decoded `SMPResponse`."""

    @property
    def mtu(self) -> int:  # pragma: no cover
        """The Maximum Transmission Unit (MTU) in 8-bit bytes."""

    @property
    def max_unencoded_size(self) -> int:  # pragma: no cover
        """The maximum size of an unencoded message that can be sent in one MTU, in 8-bit bytes."""
