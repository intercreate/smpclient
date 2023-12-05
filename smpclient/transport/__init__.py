"""Simple Management Protocol (SMP) Client Transport Protocol."""

from typing import Protocol


class SMPTransport(Protocol):
    async def connect(self, address: str) -> None:  # pragma: no cover
        ...

    async def send(self, data: bytes) -> None:  # pragma: no cover
        ...

    def write(self, data: bytes) -> None:  # pragma: no cover
        ...

    async def readuntil(self, delimiter: bytes = b"\n") -> bytes:  # pragma: no cover
        ...
