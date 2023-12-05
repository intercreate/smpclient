"""Simple Management Protocol (SMP) Client."""

from hashlib import sha256
from typing import AsyncIterator

from pydantic import ValidationError
from smp import header as smpheader
from smp import packet as smppacket

from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import SMPRequest, TEr0, TEr1, TErr, TRep, error, flatten_error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.transport import SMPTransport


class SMPClient:
    def __init__(self, transport: SMPTransport, address: str):
        self._transport = transport
        self._address = address

    async def connect(self) -> None:
        await self._transport.connect(self._address)

    async def request(self, request: SMPRequest[TRep, TEr0, TEr1, TErr]) -> TRep | TErr:
        for packet in smppacket.encode(request.BYTES):
            await self._transport.send(packet)

        decoder = smppacket.decode()
        next(decoder)

        while True:
            try:
                b = await self._transport.readuntil()
                decoder.send(b)
            except StopIteration as e:
                frame = e.value
                break

        header = smpheader.Header.loads(frame[: smpheader.Header.SIZE])

        if header.sequence != request.header.sequence:  # type: ignore
            raise SMPBadSequence("Bad sequence")

        try:
            return request.Response.loads(frame)  # type: ignore
        except ValidationError:
            return flatten_error(  # type: ignore
                request.ErrorV0.loads(frame)
                if header.version == smpheader.Version.V0
                else request.ErrorV1.loads(frame)
            )

    async def upload(
        self, image: bytes, slot: int = 0, chunksize: int = 2048, upgrade: bool = False
    ) -> AsyncIterator[int]:
        """Iteratively upload an `image` to `slot`, yielding the offset."""

        # the first write contains some extra info
        r = await self.request(
            ImageUploadWrite(  # type: ignore
                off=0,
                data=image[:chunksize],
                image=slot,
                len=len(image),
                sha=sha256(image).digest(),
                upgrade=upgrade,
            )
        )

        if error(r):
            raise SMPUploadError(r)
        elif success(r):
            yield r.off
        else:  # pragma: no cover
            raise Exception("Unreachable")

        # send chunks until the SMP server reports that the offset is at the end of the image
        while r.off != len(image):
            r = await self.request(
                ImageUploadWrite(off=r.off, data=image[r.off : r.off + chunksize])
            )
            if error(r):
                raise SMPUploadError(r)
            elif success(r):
                yield r.off
            else:  # pragma: no cover
                raise Exception("Unreachable")
