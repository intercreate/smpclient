"""Simple Management Protocol (SMP) Client.

This package implements transport layers for the Simple Management Protocol (SMP).
The SMP Protocol defines common management operations for MCUs like firmware
updates, file management, configuration, and statistics retrieval.

Additionally, SMP is extensible, allowing for custom commands to be defined to
meet the specific needs of the product.

### Transports

Transports are optional extras.  Install only the transports you need, or all:

```
smpclient[serial]
smpclient[ble]
smpclient[all]
```

The UDP transport has no additional dependencies and is always available.

### Operating Systems

|   | Windows 11 (x86) | Ubuntu (Arm/x86) | macOS (Arm/x86) |
|---|------------|-------|-------|
| Serial (UART, USB, CAN, ...) | ✅ | ✅ | ✅ |
| Bluetooth Low Energy (BLE)  | ✅ | ✅ | ✅ |
| UDP (Ethernet, Wi-Fi) | ✅ | ✅ | ✅ |

### Examples

Many usage examples are available on
[GitHub](https://github.com/intercreate/smpclient/tree/main/examples)
or in your local clone at `examples/`.

"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from hashlib import sha256
from typing import TYPE_CHECKING, Final, TypeVar

import msgspec
from smp import SMPRequest
from smp import header as smpheader
from smp import message as smpmsg
from smp.file_management import FileDownloadRequest, FileUploadRequest
from smp.image_management import ImageUploadWriteRequest
from smp.user import intercreate as smpic
from typing_extensions import assert_never

from smpclient import _request
from smpclient._request import TEr1 as TEr1
from smpclient._request import TEr2 as TEr2
from smpclient._request import TRep as TRep
from smpclient._request import error as error
from smpclient._request import error_v1 as error_v1
from smpclient._request import error_v2 as error_v2
from smpclient._request import success as success
from smpclient._request import wrapping_sequence as wrapping_sequence
from smpclient.exceptions import SMPUploadError
from smpclient.transport import SMPTransport

if TYPE_CHECKING:
    from types_bits import u8

logger = logging.getLogger(__name__)

TUploadRequest = TypeVar(
    "TUploadRequest",
    ImageUploadWriteRequest,
    FileUploadRequest,
    smpic.ImageUploadWriteRequest,
)
"""A single-shot upload request whose `data` field is filled to maximize throughput."""


class SMPClient:
    """Create a client to the SMP server at the other end of the live `transport`.

    This class provides a high-level interface to an SMP server.  Other than
    the `request` method, all methods are abstractions of common SMP routines,
    such as uploading a FW image or downloading a file.

    The `request` method is used to send an SMP request to the server and return
    the response or error.

    Args:
        transport: the connected `SMPTransport`; the client never opens or closes it
        timeout_s: the default timeout in seconds for SMP requests
        sequence: this client's SMP sequence space

    Example:
    ```python
    import asyncio
    from smpclient import SMPClient
    from smp.os_management import EchoWriteRequest
    from smpclient.transport.ble import SMPBLETransport

    async def main():
        async with SMPBLETransport().connected("00:11:22:33:44:55") as transport:
            client = SMPClient(transport)
            response = await client.request(EchoWriteRequest(d="Hello, World!"))

            if success(response):
                print(f"Response: {response=}")
            elif error(response):
                print(f"Error: {response=}")

    if __name__ == "__main__":
        asyncio.run(main())
    ```
    """

    def __init__(  # noqa: DOC301
        self,
        transport: SMPTransport,
        *,
        timeout_s: float = 2.5,
        sequence: Callable[[], Iterator[u8]] = wrapping_sequence,
    ):
        self._transport: Final = transport
        self._timeout_s: Final = timeout_s
        self._sequence: Final = sequence()

    async def request(
        self, request: SMPRequest[TRep, TEr1, TEr2], timeout_s: float | None = None
    ) -> TRep | TEr1 | TEr2:
        """Make an `SMPRequest` to the SMP server and return the Response or Error.

        Args:
            request: the `SMPRequest` to send
            timeout_s: the timeout for the request in seconds

        Returns:
            The typed and validated Response or Error

        Raises:
            TimeoutError: if the request times out
            SMPBadSequence: if the response sequence does not match the request sequence
            SMPValidationException: if the response cannot be parsed as a Response or Error

        Examples:
        Usage:

        ```python
        response = await client.request(EchoWriteRequest(d="Hello, World!"))
        if success(response):
            print(f"Response: {response=}")
        elif error(response):
            print(f"Error: {response=}")
        else:
            assert_never(response)
        ```

        Type Safety and Exhaustiveness with Generic Typing:

        ```python
        response = await client.request(EchoWriteRequest(d="Hello, World!"))
        reveal_type(response)
        # Revealed type is 'Union[EchoWriteResponse, EchoWriteErrorV1, EchoWriteErrorV2]'
        if success(response):
            reveal_type(response)
            # Revealed type is 'EchoWriteResponse'
        elif error(response):
            reveal_type(response)
            # Revealed type is 'Union[EchoWriteErrorV1, EchoWriteErrorV2]'
            if error_v1(response):
                reveal_type(response)
                # Revealed type is 'EchoWriteErrorV1'
            elif error_v2(response):
                reveal_type(response)
                # Revealed type is 'EchoWriteErrorV2'
            else:
                assert_never(response)
        else:
            assert_never(response)
        ```

        """  # noqa: DOC502
        return await _request.exchange(
            self._transport,
            request,
            next(self._sequence),
            timeout_s if timeout_s is not None else self._timeout_s,
        )

    async def upload(
        self,
        image: bytes,
        slot: int = 0,
        upgrade: bool = False,
        first_timeout_s: float = 40.0,
        subsequent_timeout_s: float | None = None,
        use_sha: bool = True,
    ) -> AsyncIterator[int]:
        """Iteratively upload an `image` to `slot`, yielding the offset.

        Args:
            image: the `bytes` to upload
            slot: the slot to upload to (0 for default)
            upgrade: `True` to mark the image as confirmed.  This is unsafe and
                can cause a boot-loop that could brick the device.  This setting
                should be left at the default `False` and the image should be
                confirmed from within the upgraded application.  Zephyr provides
                [boot_write_img_confirmed()](https://docs.zephyrproject.org/apidoc/latest/group__mcuboot__api.html#ga95ccc9e1c7460fec16b9ce9ac8ad7a72)
                for this purpose.
            first_timeout_s: the timeout for the first `ImageUploadWriteRequest` request
                which might take longer than subsequent requests (e.g. if a big
                chunk of flash memory has to be erased upfront).
            subsequent_timeout_s: the timeout for subsequent `ImageUploadWriteRequest` requests
            use_sha: `True` to include the SHA256 hash of the image in the first
                packet.

                Zephyr's SMP server will fail with `MGMT_ERR.EINVAL` if the
                MTU is too small to include both the SHA256 and the first 32-bytes
                of the image.  Increase the MTU or set `use_sha=False` in this case.

        Yields:
            the offset of the image upload

        Raises:
            SMPUploadError: if the upload routine fails
        """
        subsequent_timeout_s = (
            subsequent_timeout_s if subsequent_timeout_s is not None else self._timeout_s
        )

        response = await self.request(
            self._maximize_upload_packet(
                ImageUploadWriteRequest(
                    off=0,
                    data=b"",
                    image=slot,
                    len=len(image),
                    sha=sha256(image).digest() if use_sha else None,
                    upgrade=upgrade,
                ),
                image,
            ),
            timeout_s=first_timeout_s,
        )

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            if response.off is None:
                raise SMPUploadError(f"No offset received: {response=}")
            yield response.off
        else:
            assert_never(response)  # pragma: no cover

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(image):
            response = await self.request(
                self._maximize_upload_packet(
                    ImageUploadWriteRequest(
                        off=response.off,
                        data=b"",
                        len=len(image) if response.off == 0 else None,
                        image=slot if response.off == 0 else None,
                        upgrade=upgrade if response.off == 0 else None,
                    ),
                    image,
                ),
                timeout_s=subsequent_timeout_s,
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                if response.off is None:
                    raise SMPUploadError(f"No offset received: {response=}")
                yield response.off
            else:
                assert_never(response)  # pragma: no cover

        logger.info("Upload complete")

        if response.match is not None:
            logger.info(f"Server reports {response.match=}")
            if response.match is not True:
                message: Final = f"Upload failed, server reported mismatched SHA256: {response}"
                logger.error(message)
                raise SMPUploadError(message)

    async def upload_file(
        self,
        file_data: bytes,
        file_path: str,
        timeout_s: float | None = None,
    ) -> AsyncIterator[int]:
        """Iteratively upload a `file_data` to `file_path`, yielding the offset.

        Args:
            file_data: the `bytes` to upload
            file_path: the path to upload to
            timeout_s: the timeout for each `FileUploadRequest` request

        Yields:
            int: the offset of the file upload

        Raises:
            SMPUploadError: if the upload routine fails
        """
        timeout_s = timeout_s if timeout_s is not None else self._timeout_s

        response = await self.request(
            self._maximize_upload_packet(
                FileUploadRequest(name=file_path, off=0, data=b"", len=len(file_data)),
                file_data,
            ),
            timeout_s=timeout_s,
        )

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            if response.off is None:
                raise SMPUploadError(f"No offset received: {response=}")
            yield response.off
        else:
            assert_never(response)  # pragma: no cover

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off != len(file_data):
            response = await self.request(
                self._maximize_upload_packet(
                    FileUploadRequest(name=file_path, off=response.off, data=b""), file_data
                ),
                timeout_s=timeout_s,
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                yield response.off
            else:
                assert_never(response)  # pragma: no cover

        logger.info("Upload complete")

    async def download_file(
        self,
        file_path: str,
        timeout_s: float | None = None,
    ) -> bytes:
        """Download a file from the SMP server.

        Args:
            file_path: the path to download
            timeout_s: the timeout for each `FileDownloadRequest` request

        Returns:
            The downloaded file as `bytes`

        Raises:
            SMPUploadError: if the download routine fails
        """
        timeout_s = timeout_s if timeout_s is not None else self._timeout_s

        response = await self.request(
            FileDownloadRequest(off=0, name=file_path), timeout_s=timeout_s
        )
        file_length = 0

        if error(response):
            raise SMPUploadError(response)
        elif success(response):
            if response.len is None:
                raise SMPUploadError(f"No length received: {response=}")
            file_length = response.len
        else:
            assert_never(response)  # pragma: no cover

        file_data = response.data

        # send chunks until the SMP server reports that the offset is at the end of the image
        while response.off + len(response.data) != file_length:
            response = await self.request(
                FileDownloadRequest(off=response.off + len(response.data), name=file_path),
                timeout_s=timeout_s,
            )
            if error(response):
                raise SMPUploadError(response)
            elif success(response):
                file_data += response.data
            else:
                assert_never(response)  # pragma: no cover

        logger.info("Download complete")
        return file_data

    @staticmethod
    def _cbor_integer_size(integer: int) -> int:
        """CBOR integers are packed as small as possible."""
        # If the integer is less than 24, then the size is encoded in the same
        # byte as the value.
        # https://datatracker.ietf.org/doc/html/rfc8949#name-core-deterministic-encoding
        return 0 if integer < 24 else 1 if integer <= 0xFF else 2 if integer <= 0xFFFF else 4

    def get_max_cbor_and_data_size(self, request: smpmsg.WriteRequest) -> tuple[int, int]:
        """Given a `WriteRequest`, return the maximum CBOR size and data size.

        Args:
            request: request with all known fields filled out (only an unknown data
                     field may be left at an empty value)

        Returns:
            Tuple of (max_cbor_bytes, max_data_bytes), where:
                max_cbor_bytes: maximum CBOR-encoded bytes this packet will occupy if
                    `max_data_bytes` are all used.
                max_data_bytes: maximum amount of raw payload that can be stuffed into the
                    currently-empty data field.
        """
        encoded_request: Final = bytes(request)

        # given empty data in the request, how many bytes are available for the data?
        unencoded_bytes_available: Final = (
            self._transport.max_unencoded_size - smpheader.Header.SIZE - len(encoded_request)
        )

        # how many bytes are required to encode the data size?
        bytes_required_to_encode_data_size: Final = self._cbor_integer_size(
            unencoded_bytes_available
        )

        # the final data size is the unencoded bytes available minus the bytes
        # required to encode the data size
        data_size: Final = max(0, unencoded_bytes_available - bytes_required_to_encode_data_size)
        # the final CBOR size is the original header length plus the data size
        # plus the bytes required to encode the data size
        cbor_size: Final = len(encoded_request) + data_size + self._cbor_integer_size(data_size)

        return cbor_size, data_size

    def _maximize_upload_packet(self, request: TUploadRequest, data: bytes) -> TUploadRequest:
        """Given an upload request with empty `data`, return the largest packet possible.

        Fills the transport's `max_unencoded_size` so the encoded frame put on the wire
        is as large as the server's reassembly buffer allows (best throughput).  Works
        for any single-shot upload request (`ImageUploadWriteRequest`, `FileUploadRequest`): only
        `header` (with the buffer-filling `length`) and `data` change; every other field
        is carried over from `request`.
        """
        _, max_data_size = self.get_max_cbor_and_data_size(request)
        data_size: Final = min(max_data_size, len(data) - request.off)

        return msgspec.structs.replace(request, data=data[request.off : request.off + data_size])
