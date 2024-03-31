"""Simple Management Protocol (SMP) Client."""

import logging
from hashlib import sha256
from types import TracebackType
from typing import AsyncIterator, Final, Tuple, Type, cast

from pydantic import ValidationError
from smp import header as smpheader
from smp import message as smpmsg

from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import SMPRequest, TEr0, TEr1, TErr, TRep, error, flatten_error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.transport import SMPTransport

logger = logging.getLogger(__name__)


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
            return request._Response.loads(frame)  # type: ignore
        except ValidationError:
            return flatten_error(  # type: ignore
                request._ErrorV0.loads(frame)
                if header.version == smpheader.Version.V0
                else request._ErrorV1.loads(frame)
            )

    async def upload(
        self, image: bytes, slot: int = 0, upgrade: bool = False
    ) -> AsyncIterator[int]:
        """Iteratively upload an `image` to `slot`, yielding the offset."""

        if self._transport.max_unencoded_size < 23:
            raise Exception("Upload requires an MTU >=23.")

        response = await self.request(
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
            )
        )

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            if response.off is None:
                raise SMPUploadError(f"No offset received: {response=}")
            yield response.off
        else:  # pragma: no cover
            raise Exception("Unreachable")

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(image):
            response = await self.request(
                self._maximize_packet(ImageUploadWrite(off=response.off, data=b''), image)
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                if response.off is None:
                    raise SMPUploadError(f"No offset received: {response=}")
                yield response.off
            else:  # pragma: no cover
                raise Exception("Unreachable")

    @property
    def address(self) -> str:
        return self._address

    async def __aenter__(self) -> 'SMPClient':
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_value is not None:
            logger.error(f"Exception in SMPClient: {exc_type=}, {exc_value=}, {traceback=}")
        await self.disconnect()

    @staticmethod
    def _cbor_integer_size(integer: int) -> int:
        """CBOR integers are packed as small as possible."""

        # If the integer is less than 24, then the size is encoded in the same
        # byte as the value.
        # https://datatracker.ietf.org/doc/html/rfc8949#name-core-deterministic-encoding
        return 0 if integer < 24 else 1 if integer < 0xFF else 2 if integer < 0xFFFF else 4

    def _get_max_cbor_and_data_size(self, request: smpmsg.WriteRequest) -> Tuple[int, int]:
        """Given an `ImageUploadWrite`, return the maximum CBOR size and data size."""

        h: Final = cast(smpheader.Header, request.header)

        # given empty data in the request, how many bytes are available for the data?
        unencoded_bytes_available: Final = self._transport.max_unencoded_size - len(bytes(request))

        # how many bytes are required to encode the data size?
        bytes_required_to_encode_data_size: Final = self._cbor_integer_size(
            unencoded_bytes_available
        )

        # the final data size is the unencoded bytes available minus the bytes
        # required to encode the data size
        data_size: Final = max(0, unencoded_bytes_available - bytes_required_to_encode_data_size)

        # the final CBOR size is the original header length plus the data size
        # plus the bytes required to encode the data size
        cbor_size: Final = h.length + data_size + self._cbor_integer_size(data_size)

        return cbor_size, data_size

    def _maximize_packet(self, request: ImageUploadWrite, image: bytes) -> ImageUploadWrite:
        """Given an `ImageUploadWrite` with empty `data`, return the largest packet possible."""

        h: Final = cast(smpheader.Header, request.header)
        cbor_size, data_size = self._get_max_cbor_and_data_size(request)

        if data_size > len(image) - request.off:  # final packet
            data_size = len(image) - request.off
            cbor_size = h.length + data_size + self._cbor_integer_size(data_size)

        return ImageUploadWrite(
            header=smpheader.Header(
                op=h.op,
                version=h.version,
                flags=h.flags,
                length=cbor_size,
                group_id=h.group_id,
                sequence=h.sequence,
                command_id=h.command_id,
            ),
            off=request.off,
            data=image[request.off : request.off + data_size],
            image=request.image,
            len=request.len,
            sha=request.sha,
            upgrade=request.upgrade,
        )
