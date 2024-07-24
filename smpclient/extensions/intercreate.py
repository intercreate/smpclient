"""Intercreate extensions of the `SMPClient`."""

from typing import AsyncIterator, Final

from smp import header as smpheader

from smpclient import SMPClient
from smpclient.exceptions import SMPUploadError
from smpclient.generics import error, success
from smpclient.requests.user import intercreate as ic


class ICUploadClient(SMPClient):
    """Support for Intercreate Group Upload."""

    async def ic_upload(self, data: bytes, image: int = 0) -> AsyncIterator[int]:
        """Iteratively upload `data` to the SMP server, yielding the offset."""

        response = await self.request(
            ic.ImageUploadWrite(off=0, data=b'', image=image, len=len(data))
        )

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            yield response.off
        else:  # pragma: no cover
            raise Exception("Unreachable")

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(data):
            response = await self.request(
                self._ic_maximize_packet(ic.ImageUploadWrite(off=response.off, data=b''), data)
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                yield response.off
            else:  # pragma: no cover
                raise Exception("Unreachable")

    def _ic_maximize_packet(self, request: ic.ImageUploadWrite, data: bytes) -> ic.ImageUploadWrite:
        """Given an `ic.ImageUploadWrite` with empty `data`, return the largest packet possible."""

        h: Final = request.header
        cbor_size, data_size = self._get_max_cbor_and_data_size(request)

        if data_size > len(data) - request.off:  # final packet
            data_size = len(data) - request.off
            cbor_size = h.length + data_size + self._cbor_integer_size(data_size)

        return ic.ImageUploadWrite(
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
            data=data[request.off : request.off + data_size],
            image=request.image,
            len=request.len,
            sha=request.sha,
        )
