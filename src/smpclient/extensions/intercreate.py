"""Intercreate extensions of the `SMPClient`."""

from collections.abc import AsyncIterator
from typing import Final

from smp import header as smpheader

from smpclient import SMPClient
from smpclient.exceptions import SMPUploadError
from smpclient.generics import error, success
from smpclient.requests.user import intercreate as ic


class ICUploadClient(SMPClient):
    """Support for Intercreate Group Upload."""

    async def ic_upload(
        self,
        data: bytes,
        image: int = 0,
        version: smpheader.Version = smpheader.Version.V2,
    ) -> AsyncIterator[int]:
        """Iteratively upload `data` to the SMP server, yielding the offset.

        Args:
            data: the `bytes` to upload
            image: the image to upload to
            version: the SMP version of the requests sent by this routine.  The
                default, `Version.V2`, is what current SMP servers expect; pass
                `Version.V1` for servers that predate SMP version 2.

        Yields:
            the offset of the upload

        Raises:
            SMPUploadError: if the upload routine fails
            Exception: if the response is neither a success nor an error
        """
        response = await self.request(
            ic.ImageUploadWrite(off=0, data=b'', image=image, len=len(data), version=version)
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
                self._ic_maximize_packet(
                    ic.ImageUploadWrite(off=response.off, data=b'', version=version), data
                )
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
        cbor_size, data_size = self.get_max_cbor_and_data_size(request)

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
            version=h.version,
            off=request.off,
            data=data[request.off : request.off + data_size],
            image=request.image,
            len=request.len,
            sha=request.sha,
        )
