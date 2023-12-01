"""Simple Management Protocol (SMP) Client Transport Protocol."""

from typing import Protocol


class SMPTransport(Protocol):
    async def connect(self, address: str) -> None:
        ...

    async def send(self, data: bytes) -> None:
        ...

    def write(self, data: bytes) -> None:
        ...

    async def readuntil(self, delimiter: bytes = b"\n") -> bytes:
        ...
