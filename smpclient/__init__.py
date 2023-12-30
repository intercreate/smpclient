"""Simple Management Protocol (SMP) Client."""

from hashlib import sha256
from typing import AsyncIterator, Final, cast

from pydantic import ValidationError
from smp import header as smpheader

from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import SMPRequest, TEr0, TEr1, TErr, TRep, error, flatten_error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.transport import SMPTransport


class SMPClient:
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

        if self._transport.max_unencoded_size < 23:
            raise Exception("Upload requires an MTU >=23.")

        def cbor_integer_size(integer: int) -> int:
            """CBOR integers are packed as small as possible."""
            return 0 if integer < 24 else 1 if integer < 0xFF else 2 if integer < 0xFFFF else 4

        # the first write contains some extra info

        # create an empty request to see how much room is left for data
        _r = ImageUploadWrite(
            off=0,
            data=b'',
            image=slot,
            len=len(image),
            sha=sha256(image).digest(),
            upgrade=upgrade,
        )
        _h = cast(smpheader.Header, _r.header)

        chunk_size = self._transport.max_unencoded_size - len(bytes(_r))
        chunk_size -= cbor_integer_size(chunk_size)
        chunk_size = max(0, chunk_size)
        cbor_size = _h.length + chunk_size + cbor_integer_size(chunk_size)

        image_upload_write = ImageUploadWrite(
            header=smpheader.Header(
                op=_h.op,
                version=_h.version,
                flags=_h.flags,
                length=cbor_size,
                group_id=_h.group_id,
                sequence=_h.sequence,
                command_id=_h.command_id,
            ),
            off=0,
            data=image[:chunk_size],
            image=slot,
            len=len(image),
            sha=sha256(image).digest(),
            upgrade=upgrade,
        )

        response = await self.request(image_upload_write)  # type: ignore

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            yield response.off
        else:  # pragma: no cover
            raise Exception("Unreachable")

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(image):
            # create an empty request to see how much room is left for data
            _r = ImageUploadWrite(off=response.off, data=b'')

            chunk_size = self._transport.max_unencoded_size - len(bytes(_r))
            chunk_size -= cbor_integer_size(chunk_size)
            assert chunk_size > 0
            cbor_size = _r.header.length + chunk_size + cbor_integer_size(chunk_size)

            data = image[response.off : response.off + chunk_size]

            cbor_size = (
                cbor_size if len(data) == chunk_size else len(data) + cbor_integer_size(len(data))
            )

            image_upload_write = ImageUploadWrite(
                header=smpheader.Header(
                    op=_r.header.op,
                    version=_r.header.version,
                    flags=_r.header.flags,
                    length=cbor_size,
                    group_id=_r.header.group_id,
                    sequence=_r.header.sequence,
                    command_id=_r.header.command_id,
                ),
                off=response.off,
                data=data,
            )

            if (
                len(bytes(image_upload_write)) + response.off <= len(image)
                and len(bytes(image_upload_write)) != self._transport.max_unencoded_size
            ):
                raise Exception(
                    f"{len(bytes(image_upload_write))} > {self._transport.max_unencoded_size}; "
                    f"An upload write must fit in MTU, this is a bug!"
                )

            response = await self.request(image_upload_write)
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
