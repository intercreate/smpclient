"""Simple Management Protocol (SMP) Client."""

from hashlib import sha256
from typing import AsyncIterator, Final, cast

from pydantic import ValidationError
from smp import header as smpheader

from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import SMPRequest, TEr0, TEr1, TErr, TRep, error, flatten_error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.transport import SMPTransport
from smpclient.transport.ble import SMPBLETransport
from smpclient.requests.os_management import MCUMgrParametersRead
import asyncio


class SMPClient:
    DEFAULT_TIMEOUT = 40.000
    SHORT_TIMEOUT = 2.500
    MEDIUM_TIMEOUT = 5.000
    def __init__(self, transport: SMPTransport, address: str):
        """Create a client to the SMP server `address`, using `transport`."""
        self._transport: Final = transport
        self._address: Final = address

    async def connect(self) -> None:
        """Connect to the SMP server."""
        await self._transport.connect(self._address)

    async def disconnect(self) -> None:
        """Disconnect from the SMP server."""
        await self._transport.disconnect()

    async def request(self, request: SMPRequest[TRep, TEr0, TEr1, TErr]) -> TRep | TErr:
        """Make an `SMPRequest` to the SMP server."""

        frame = await self._transport.send_and_receive(request.BYTES)

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
        self, image: bytes, slot: int = 0, upgrade: bool = False
    ) -> AsyncIterator[int]:
        """Iteratively upload an `image` to `slot`, yielding the offset."""

        max_packet_size = self._transport.max_unencoded_size
        if isinstance(self._transport, SMPBLETransport):
            mcumgr_parameters = await asyncio.wait_for(self.request(MCUMgrParametersRead()), timeout=1.0)
            if success(mcumgr_parameters):
                max_packet_size = mcumgr_parameters.buf_size

        # why 23? should be max_packet_size
        if self._transport.max_unencoded_size < 23:
            raise Exception("Upload requires an MTU >=23.")

        #  Timeout for the initial chunk is long, as the device may need to erase the flash.
        response = await asyncio.wait_for(self.request(
            self._maximize_packet(
                ImageUploadWrite(  # type: ignore
                    off=0,
                    data=b'',
                    image=slot,
                    len=len(image),
                    sha=sha256(image).digest(),
                    upgrade=upgrade,
                ),
                image,
                max_packet_size,
            )
        ), SMPClient.DEFAULT_TIMEOUT)

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            yield response.off
        else:  # pragma: no cover
            raise Exception("Unreachable")

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(image):
            response = await asyncio.wait_for(self.request(
                self._maximize_packet(ImageUploadWrite(off=response.off, data=b''), image, max_packet_size)
            ), SMPClient.SHORT_TIMEOUT)
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                yield response.off
            else:  # pragma: no cover
                raise Exception("Unreachable")

    @property
    def address(self) -> str:
        return self._address

    async def __aenter__(self) -> 'SMPClient':
        await self.connect()
        return self

    async def __aexit__(self) -> None:
        await self.disconnect()

    def _maximize_packet(self, request: ImageUploadWrite, image: bytes, max_packet_size: int) -> ImageUploadWrite:
        """Given an `ImageUploadWrite` with empty `data`, return the largest packet possible."""

        def cbor_integer_size(integer: int) -> int:
            """CBOR integers are packed as small as possible."""
            return 0 if integer < 24 else 1 if integer < 0xFF else 2 if integer < 0xFFFF else 4

        _h = cast(smpheader.Header, request.header)

        chunk_size = max_packet_size - len(bytes(request))
        chunk_size -= cbor_integer_size(chunk_size)
        chunk_size = min(len(image) - request.off, chunk_size)
        cbor_size = _h.length + chunk_size + cbor_integer_size(chunk_size)

        return ImageUploadWrite(
            header=smpheader.Header(
                op=_h.op,
                version=_h.version,
                flags=_h.flags,
                length=cbor_size,
                group_id=_h.group_id,
                sequence=_h.sequence,
                command_id=_h.command_id,
            ),
            off=request.off,
            data=image[request.off : request.off + chunk_size],
            image=request.image,
            len=request.len,
            sha=request.sha,
            upgrade=request.upgrade,
        )
