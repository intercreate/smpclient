"""Intercreate extensions of the `SMPClient`."""

from collections.abc import AsyncIterator

from smp.user import intercreate as ic

from smpclient import SMPClient, TTransport, error, success
from smpclient.exceptions import SMPUploadError


class ICUploadClient(SMPClient[TTransport]):
    """Support for Intercreate Group Upload."""

    async def ic_upload(self, data: bytes, image: int = 0) -> AsyncIterator[int]:
        """Iteratively upload `data` to the SMP server, yielding the offset."""
        response = await self.request(
            ic.ImageUploadWriteRequest(off=0, data=b'', image=image, len=len(data))
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
                self._maximize_upload_packet(
                    ic.ImageUploadWriteRequest(off=response.off, data=b''), data
                )
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                yield response.off
            else:  # pragma: no cover
                raise Exception("Unreachable")
