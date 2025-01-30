"""Simple Management Protocol (SMP) Client.

This package implements transport layers for the Simple Management Protocol (SMP).
The SMP Protocol defines common management operations for MCUs like firmware
updates, file management, configuration, and statistics retrieval.

Additionally, SMP is extensible, allowing for custom commands to be defined to
meet the specific needs of the product.

### Operating Systems

|   | Windows 11 | Ubuntu | macOS (Arm/x86) |
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

import asyncio
import logging
from hashlib import sha256
from types import TracebackType
from typing import AsyncIterator, Final, Tuple, Type

from pydantic import ValidationError
from smp import header as smpheader
from smp import message as smpmsg
from typing_extensions import assert_never

from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import SMPRequest, TEr1, TEr2, TRep, error, success
from smpclient.requests.file_management import FileDownload, FileUpload
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.requests.os_management import MCUMgrParametersRead
from smpclient.transport import SMPTransport

try:
    from asyncio import timeout  # type: ignore
except ImportError:  # backport for Python3.10 and below
    from async_timeout import timeout  # type: ignore

logger = logging.getLogger(__name__)


class SMPClient:
    """Create a client to the SMP server `address`, using `transport`.

    This class provides a high-level interface to an SMP server.  Other than
    the `request` method, all methods are abstractions of common SMP routines,
    such as uploading a FW image or downloading a file.

    The `request` method is used to send an SMP request to the server and return
    the response or error.

    Args:
        transport: the `SMPTransport` to use
        address: the address of the SMP server, see `smpclient.transport` for details

    Example:

    ```python
    import asyncio
    from smpclient import SMPClient
    from smpclient.requests.os_management import EchoWrite
    from smpclient.transport.ble import SMPBLETransport

    async def main():
        async with SMPClient(SMPBLETransport(), "00:11:22:33:44:55") as client:
            response = await client.request(EchoWrite(d="Hello, World!"))

            if success(response):
                print(f"Response: {response=}")
            elif error(response):
                print(f"Error: {response=}")

    if __name__ == "__main__":
        asyncio.run(main())
    ```
    """

    def __init__(self, transport: SMPTransport, address: str):  # noqa: DOC301
        self._transport: Final = transport
        self._address: Final = address

    async def connect(self, timeout_s: float = 5.0) -> None:
        """Connect to the SMP server.

        Args:
            timeout_s: the timeout for the connection attempt in seconds
        """
        await self._transport.connect(self._address, timeout_s)
        await self._initialize()

    async def disconnect(self) -> None:
        """Disconnect from the SMP server."""
        await self._transport.disconnect()

    async def request(
        self, request: SMPRequest[TRep, TEr1, TEr2], timeout_s: float = 120.000
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
            ValidationError: if the response cannot be parsed as a Response or Error

        Examples:

        Usage:

        ```python
        response = await client.request(EchoWrite(d="Hello, World!"))
        if success(response):
            print(f"Response: {response=}")
        elif error(response):
            print(f"Error: {response=}")
        else:
            assert_never(response)
        ```

        Type Safety and Exhaustiveness with Generic Typing:

        ```python
        response = await client.request(EchoWrite(d="Hello, World!"))
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

        """

        try:
            async with timeout(timeout_s):
                frame = await self._transport.send_and_receive(request.BYTES)
        except asyncio.TimeoutError:
            timeout_message: Final = f"Timeout ({timeout_s}s) waiting for request {request}"
            logger.error(timeout_message)
            raise TimeoutError(timeout_message)

        header = smpheader.Header.loads(frame[: smpheader.Header.SIZE])

        if header.sequence != request.header.sequence:
            raise SMPBadSequence(
                f"Bad sequence {header.sequence}, expected {request.header.sequence}"
            )

        try:
            return request._Response.loads(frame)  # type: ignore
        except ValidationError:
            pass
        try:
            return request._ErrorV1.loads(frame)
        except ValidationError:
            pass
        try:
            return request._ErrorV2.loads(frame)
        except ValidationError:
            error_message = (
                f"Response could not by parsed as one of {request._Response}, "
                f"{request._ErrorV1}, or {request._ErrorV2}. {header=} {frame=}"
            )
            logger.error(error_message)
            raise ValidationError(error_message)

    async def upload(
        self,
        image: bytes,
        slot: int = 0,
        upgrade: bool = False,
        first_timeout_s: float = 40.000,
        subsequent_timeout_s: float = 2.500,
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
            first_timeout_s: the timeout for the first `ImageUploadWrite` request
            subsequent_timeout_s: the timeout for subsequent `ImageUploadWrite` requests
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

        response = await self.request(
            self._maximize_image_upload_write_packet(
                ImageUploadWrite(
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
                self._maximize_image_upload_write_packet(
                    ImageUploadWrite(
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
        timeout_s: float = 2.500,
    ) -> AsyncIterator[int]:
        """Iteratively upload a `file_data` to `file_path`, yielding the offset.

        Args:
            file_data: the `bytes` to upload
            file_path: the path to upload to
            timeout_s: the timeout for each `FileUpload` request

        Yields:
            int: the offset of the file upload

        Raises:
            SMPUploadError: if the upload routine fails
        """
        response = await self.request(
            self._maximize_file_upload_packet(
                FileUpload(name=file_path, off=0, data=b"", len=len(file_data)),
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
                self._maximize_file_upload_packet(
                    FileUpload(name=file_path, off=response.off, data=b""), file_data
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
        timeout_s: float = 2.500,
    ) -> bytes:
        """Download a file from the SMP server.

        Args:
            file_path: the path to download
            timeout_s: the timeout for each `FileDownload` request

        Returns:
            The downloaded file as `bytes`

        Raises:
            SMPUploadError: if the download routine fails
        """
        response = await self.request(FileDownload(off=0, name=file_path), timeout_s=timeout_s)
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
                FileDownload(off=response.off + len(response.data), name=file_path),
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

    @property
    def address(self) -> str:
        """The SMP server address."""

        return self._address

    async def __aenter__(self) -> "SMPClient":
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
        return 0 if integer < 24 else 1 if integer <= 0xFF else 2 if integer <= 0xFFFF else 4

    def _get_max_cbor_and_data_size(self, request: smpmsg.WriteRequest) -> Tuple[int, int]:
        """Given an `ImageUploadWrite`, return the maximum CBOR size and data size."""

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
        cbor_size: Final = request.header.length + data_size + self._cbor_integer_size(data_size)

        return cbor_size, data_size

    def _maximize_image_upload_write_packet(
        self, request: ImageUploadWrite, image: bytes
    ) -> ImageUploadWrite:
        """Given an `ImageUploadWrite` with empty `data`, return the largest packet possible."""

        h: Final = request.header
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

    def _maximize_file_upload_packet(self, request: FileUpload, data: bytes) -> FileUpload:
        """Given an `FileUpload` with empty `data`, return the largest packet possible."""
        h: Final = request.header
        cbor_size, data_size = self._get_max_cbor_and_data_size(request)
        if data_size > len(data) - request.off:  # final packet
            data_size = len(data) - request.off
            cbor_size = h.length + data_size + self._cbor_integer_size(data_size)
        return FileUpload(
            header=smpheader.Header(
                op=h.op,
                version=h.version,
                flags=h.flags,
                length=cbor_size,
                group_id=h.group_id,
                sequence=h.sequence,
                command_id=h.command_id,
            ),
            name=request.name,
            off=request.off,
            data=data[request.off : request.off + data_size],
            len=request.len,
        )

    async def _initialize(self) -> None:
        """Gather initialization information from the SMP server."""

        try:
            async with timeout(2):
                mcumgr_parameters = await self.request(MCUMgrParametersRead())
                if success(mcumgr_parameters):
                    logger.debug(f"MCUMgr parameters: {mcumgr_parameters}")
                    self._transport.initialize(mcumgr_parameters.buf_size)
                elif error(mcumgr_parameters):
                    logger.warning(f"Error reading MCUMgr parameters: {mcumgr_parameters}")
                else:
                    assert_never(mcumgr_parameters)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for MCUMgr parameters")
