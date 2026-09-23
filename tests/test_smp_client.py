"""Tests for `SMPClient`."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, call, patch

import pytest
from smp import header as smphdr
from smp import message as smpmsg
from smp import packet as smppacket
from smp.error import MGMT_ERR
from smp.error import Err as SMPErr
from smp.exceptions import SMPMismatchedGroupId
from smp.file_management import (
    FS_MGMT_ERR,
    FileDownloadRequest,
    FileDownloadResponse,
    FileSystemManagementErrorV1,
    FileSystemManagementErrorV2,
    FileUploadRequest,
    FileUploadResponse,
)
from smp.image_management import (
    IMG_MGMT_ERR,
    ImageManagementErrorV1,
    ImageManagementErrorV2,
    ImageUploadWriteRequest,
    ImageUploadWriteResponse,
)
from smp.os_management import (
    OS_MGMT_RET_RC,
    EchoWriteResponse,
    OSManagementErrorV1,
    OSManagementErrorV2,
    ResetWriteRequest,
    ResetWriteResponse,
)

from smpclient import SMPClient, error, error_v1, error_v2, success, wrapping_sequence
from smpclient import transport as smptransport
from smpclient.exceptions import SMPBadSequence, SMPUploadError, SMPValidationException
from smpclient.transport.serial import (
    BufferParams,
    BufferSize,
    SMPSerialRawTransport,
    SMPSerialTransport,
)

FRAME_OVERHEAD = smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size
"""The SMP serial frame's 2-byte length + 2-byte CRC16 that share the decoded buffer."""


def error_bytes(err: smpmsg.Response, command_id: smphdr.AnyCommandId) -> bytes:
    """Serialize an error response, whose header cannot be synthesized by `to_frame()`.

    `ErrorV1`/`ErrorV2` declare no `_OP` or `_COMMAND_ID` -- only the group they belong
    to -- so a caller that needs their bytes builds the header itself. `SMPMockTransport`
    re-stamps the sequence, so the one here is arbitrary.
    """
    payload = bytes(err)
    return (
        bytes(
            smphdr.Header(
                op=smphdr.OP.WRITE_RSP,
                version=smphdr.Version.V2,
                flags=smphdr.Flag(0),
                length=len(payload),
                group_id=err._GROUP_ID,
                sequence=0,
                command_id=command_id,
            )
        )
        + payload
    )


class SMPMockTransport:
    """Satisfies the `SMPTransport` `Protocol`."""

    def __init__(self) -> None:
        self.send = AsyncMock()
        self.receive = AsyncMock()
        self._mtu = 0
        self._max_unencoded_size = 0
        self.sequence_offset = 0
        """Added to the echoed sequence; non-zero fakes a server answering out of order."""

    @property
    def mtu(self) -> int:
        return self._mtu

    @property
    def max_unencoded_size(self) -> int:
        return self._max_unencoded_size

    async def send_and_receive(self, data: bytes) -> bytes:
        """Answer with `receive()`'s frame, re-stamped with the sequence a server would echo.

        `SMPClient.request` draws the sequence from smp's counter when it frames the
        request, so a test cannot know it in advance.
        """
        await self.send(data)
        response: bytes = await self.receive()
        sequence: int = smphdr.Header.loads(data[: smphdr.Header.SIZE]).sequence
        return (
            bytes(
                replace(
                    smphdr.Header.loads(response[: smphdr.Header.SIZE]),
                    sequence=(sequence + self.sequence_offset) % 0x100,
                )
            )
            + response[smphdr.Header.SIZE :]
        )


def sent_frame(m: SMPMockTransport) -> Any:
    """The frame handed to the most recent `send`."""
    assert m.send.await_args is not None
    return m.send.await_args.args[0]


def test_constructor() -> None:
    m = SMPMockTransport()
    s = SMPClient(m)
    assert s._transport is m


@pytest.mark.asyncio
async def test_request() -> None:
    m = SMPMockTransport()
    s = SMPClient(m)

    req = ResetWriteRequest()
    m.receive.return_value = bytes(ResetWriteResponse().to_frame(sequence=0))
    rep = await s.request(req)
    assert ResetWriteRequest.loads(sent_frame(m)).data == req
    m.receive.assert_awaited()
    assert type(rep) is req._Response
    assert success(rep) is True
    assert error(rep) is False
    assert error_v1(rep) is False
    assert error_v2(rep) is False

    # test that a bad sequence raises `SMPBadSequence`
    m.receive.return_value = bytes(ResetWriteResponse().to_frame(sequence=0))
    m.sequence_offset = 1
    with pytest.raises(SMPBadSequence):
        await s.request(req)
    m.sequence_offset = 0

    # test that a genric MGMT_ERR error response is parsed
    m.receive.return_value = error_bytes(
        OSManagementErrorV1(rc=MGMT_ERR.ENOTSUP), smphdr.CommandId.OSManagement.RESET
    )

    rep = await s.request(req)
    assert ResetWriteRequest.loads(sent_frame(m)).data == req
    m.receive.assert_awaited()
    assert success(rep) is False
    assert error_v2(rep) is False
    assert error(rep) is True
    assert error_v1(rep) is True
    if error_v1(rep):
        assert rep.rc == MGMT_ERR.ENOTSUP
    else:
        raise AssertionError(f"Unexpected response type: {type(rep)}")

    # test that an OS_MGMT_RET_RC error response is parsed
    m.receive.return_value = error_bytes(
        OSManagementErrorV2(
            err=SMPErr[OS_MGMT_RET_RC](
                rc=OS_MGMT_RET_RC.UNKNOWN, group=smphdr.GroupId.OS_MANAGEMENT
            )
        ),
        smphdr.CommandId.OSManagement.RESET,
    )

    rep = await s.request(req)
    assert ResetWriteRequest.loads(sent_frame(m)).data == req
    m.receive.assert_awaited()
    assert success(rep) is False
    assert error(rep) is True
    assert error_v1(rep) is False
    assert error_v2(rep) is True
    if error_v2(rep):
        assert rep.err.rc == OS_MGMT_RET_RC.UNKNOWN
    else:
        raise AssertionError(f"Unexpected response type: {type(rep)}")


@pytest.mark.asyncio
async def test_request_unparseable_frame() -> None:
    """A frame matching none of the Response/ErrorV1/ErrorV2 types raises with diagnostics."""
    m = SMPMockTransport()
    s = SMPClient(m)

    req = ResetWriteRequest()
    # Same group, so the frame reaches the decoders -- but `r` is a field of none of
    # `ResetWriteRequest`'s three response types, so every one of them rejects it.
    m.receive.return_value = bytes(EchoWriteResponse(r="not a reset response").to_frame(sequence=0))

    with pytest.raises(SMPValidationException) as exc_info:
        await s.request(req)

    assert req._Response.__name__ in exc_info.value.msg
    assert "Header:" in exc_info.value.details
    assert "Frame:" in exc_info.value.details
    assert req._ErrorV1.__name__ in exc_info.value.details
    assert req._ErrorV2.__name__ in exc_info.value.details


def test_wrapping_sequence() -> None:
    """The default sequence space covers the header's 8 bit field and wraps."""
    sequence = wrapping_sequence()

    assert [next(sequence) for _ in range(0x100)] == list(range(0x100))
    assert next(sequence) == 0


@pytest.mark.asyncio
async def test_injected_sequence() -> None:
    """The sequence space is injectable, so a test can pin what goes on the wire."""
    m = SMPMockTransport()
    s = SMPClient(m, sequence=lambda: iter((7, 9)))
    m.receive.return_value = bytes(ResetWriteResponse().to_frame(sequence=0))

    for expected in (7, 9):
        await s.request(ResetWriteRequest())
        assert smphdr.Header.loads(sent_frame(m)[: smphdr.Header.SIZE]).sequence == expected


@pytest.mark.asyncio
async def test_request_mismatched_group_propagates() -> None:
    """A frame from the wrong group is a transport error, not an unparseable response.

    It fails all three candidate types identically, so `SMPMismatchedGroupId` propagates
    instead of being collected as one more parse failure.
    """
    m = SMPMockTransport()
    s = SMPClient(m)

    m.receive.return_value = bytes(ImageUploadWriteResponse(off=0).to_frame(sequence=0))

    with pytest.raises(SMPMismatchedGroupId):
        await s.request(ResetWriteRequest())


@pytest.mark.asyncio
async def test_request_truncated_payload_is_diagnosed() -> None:
    """A payload that is not decodable CBOR still raises the diagnostic exception.

    msgspec reports this as a bare `DecodeError` rather than a `ValidationError`, so the
    decode chain catches the wider type.
    """
    m = SMPMockTransport()
    s = SMPClient(m)

    truncated = b"\xbf\x61\x72"  # an indefinite-length map that simply stops
    m.receive.return_value = (
        bytes(
            smphdr.Header(
                op=smphdr.OP.WRITE_RSP,
                version=smphdr.Version.V2,
                flags=smphdr.Flag(0),
                length=len(truncated),
                group_id=smphdr.GroupId.OS_MANAGEMENT,
                sequence=0,
                command_id=smphdr.CommandId.OSManagement.RESET,
            )
        )
        + truncated
    )

    with pytest.raises(SMPValidationException):
        await s.request(ResetWriteRequest())


@pytest.mark.asyncio
async def test_upload() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, timeout_s=2.5)

    s.request = AsyncMock()  # type: ignore

    m._mtu = 498
    m._max_unencoded_size = 498

    chunk_size = 415  # max chunk given MTU

    image = bytes([i % 255 for i in range(4097)])
    u = s.upload(image)

    s.request.return_value = ImageUploadWriteResponse(off=415)  # type: ignore
    offset = await anext(u)
    assert offset == 415
    s.request.assert_awaited_once_with(
        ImageUploadWriteRequest(
            off=0,
            data=image[:chunk_size],
            image=0,
            len=len(image),
            sha=sha256(image).digest(),
            upgrade=False,
        ),
        timeout_s=40.000,
    )

    s.request.return_value = ImageUploadWriteResponse(off=415 + 474)  # type: ignore
    offset = await anext(u)
    assert offset == 415 + 474
    s.request.assert_awaited_with(
        ImageUploadWriteRequest(
            off=415,
            data=image[415 : 415 + 474],
        ),
        timeout_s=2.5,
    )

    # assert that upload() raises SMPUploadError
    s.request.return_value = ImageManagementErrorV1(
        rc=MGMT_ERR.ECORRUPT,
    )
    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].rc == MGMT_ERR.ECORRUPT
    u = s.upload(image)
    s.request.return_value = ImageManagementErrorV2(
        err=SMPErr(  # type: ignore
            rc=IMG_MGMT_ERR.FLASH_WRITE_FAILED, group=smphdr.GroupId.IMAGE_MANAGEMENT
        ),
    )
    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].err.rc == IMG_MGMT_ERR.FLASH_WRITE_FAILED


@patch("tests.test_smp_client.SMPMockTransport.mtu", new_callable=PropertyMock)
@patch("tests.test_smp_client.SMPMockTransport.max_unencoded_size", new_callable=PropertyMock)
@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [124, 127, 251, 498, 512, 1024, 2048, 4096, 8192])
async def test_upload_hello_world_bin(
    mock_mtu: PropertyMock, mock_max_unencoded_size: PropertyMock, mtu: int
) -> None:
    mock_mtu.return_value = mtu
    mock_max_unencoded_size.return_value = mtu

    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        "rb",
    ) as f:
        image = f.read()

    m = SMPMockTransport()
    s = SMPClient(m)

    accumulated_image = bytearray([])

    async def mock_request(
        request: ImageUploadWriteRequest, timeout_s: float = 120.000
    ) -> ImageUploadWriteResponse:
        accumulated_image.extend(request.data)
        return ImageUploadWriteResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    async for _ in s.upload(image):
        pass

    assert accumulated_image == image


@pytest.mark.asyncio
@pytest.mark.parametrize("max_smp_encoded_frame_size", [128, 256, 512, 1024, 2048, 4096, 8192])
@pytest.mark.parametrize("line_buffers", [1, 2, 3, 4, 8])
async def test_upload_hello_world_bin_encoded(
    max_smp_encoded_frame_size: int, line_buffers: int
) -> None:
    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        'rb',
    ) as f:
        image = f.read()

    line_length = max_smp_encoded_frame_size // line_buffers
    if line_length < 82:  # TODO: get better coverage
        pytest.skip("The line buffer size is too small")

    m = SMPSerialTransport(
        fragmentation_strategy=BufferParams(
            line_length=line_length,
            line_buffers=line_buffers,
        ),
    )
    s = SMPClient(m)
    # MTU is line_length * line_buffers, which may be <= max_smp_encoded_frame_size
    # due to integer division
    assert s._transport.mtu == line_length * line_buffers
    assert s._transport.mtu <= max_smp_encoded_frame_size

    packets: list[bytes] = []

    def mock_write(data: bytes) -> int:
        """Accumulate the raw packets in the global `packets`."""
        packets.append(data)
        return len(data)

    s._transport._conn.write = mock_write  # type: ignore
    type(s._transport._conn).out_waiting = 0  # type: ignore

    async def mock_request(
        request: ImageUploadWriteRequest, timeout_s: float = 120.000
    ) -> ImageUploadWriteResponse:
        # call the real send method (with write mocked) but don't bother with receive
        # this does provide coverage for the MTU-limited encoding done in the send method
        await s._transport.send(bytes(request.to_frame(sequence=0)))
        return ImageUploadWriteResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    assert s._transport.max_unencoded_size < s._transport.mtu, (
        "The serial transport has encoding overhead"
    )

    async for _ in s.upload(image):
        pass

    reconstructed_image = bytearray([])

    decoder = smppacket.decode()
    next(decoder)

    for packet in packets:
        try:
            decoder.send(packet)
        except StopIteration as e:
            reconstructed_request = ImageUploadWriteRequest.loads(e.value).data
            reconstructed_image.extend(reconstructed_request.data)

            decoder = smppacket.decode()
            next(decoder)

    assert reconstructed_image == image


@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [128, 256, 512, 1024, 2048, 4096, 8192])
async def test_upload_hello_world_bin_raw(mtu: int) -> None:
    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        'rb',
    ) as f:
        image = f.read()

    m = SMPSerialRawTransport(fragmentation_strategy=smptransport.BufferSize(mtu))
    s = SMPClient(m)
    assert s._transport.mtu == mtu
    assert s._transport.max_unencoded_size == mtu, "The raw transport has no encoding overhead"

    packets: list[bytes] = []

    def mock_write(data: bytes) -> int:
        """Accumulate the raw packets in the global `packets`."""
        packets.append(data)
        return len(data)

    s._transport._conn.write = mock_write  # type: ignore

    async def mock_request(
        request: ImageUploadWriteRequest, timeout_s: float = 120.000
    ) -> ImageUploadWriteResponse:
        # call the real send method (with write mocked) but don't bother with receive
        # this provides coverage for the MTU-limited chunking done by SMPClient.upload
        await s._transport.send(bytes(request.to_frame(sequence=0)))
        return ImageUploadWriteResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    # `out_waiting` is a property on the real Serial class - scope the patch so it
    # restores cleanly when the test finishes.
    with patch.object(type(s._transport._conn), 'out_waiting', 0):  # type: ignore
        async for _ in s.upload(image):
            pass

    # Each captured write is one complete SMP message [header][payload], no decoding needed.
    reconstructed_image = bytearray([])
    for packet in packets:
        reconstructed_image.extend(ImageUploadWriteRequest.loads(packet).data.data)

    assert reconstructed_image == image


@pytest.mark.asyncio
async def test_upload_file() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, timeout_s=2.5)

    s.request = AsyncMock()  # type: ignore

    m._mtu = 498
    m._max_unencoded_size = 498

    chunk_size = 455  # max chunk given MTU

    data = bytes([i % 255 for i in range(4097)])
    u = s.upload_file(data, file_path="test.txt")

    s.request.return_value = FileUploadResponse(off=455)  # type: ignore
    offset = await anext(u)
    assert offset == 455
    s.request.assert_awaited_once_with(
        FileUploadRequest(
            off=0,
            data=data[:chunk_size],
            len=len(data),
            name="test.txt",
        ),
        timeout_s=2.500,
    )

    s.request.return_value = FileUploadResponse(off=455 + 460)  # type: ignore
    offset = await anext(u)
    assert offset == 455 + 460
    s.request.assert_awaited_with(
        FileUploadRequest(
            off=455,
            data=data[455 : 455 + 460],
            name="test.txt",
        ),
        timeout_s=2.500,
    )

    # assert that upload() raises SMPUploadError
    s.request.return_value = FileSystemManagementErrorV1(
        rc=MGMT_ERR.EACCESSDENIED,
    )

    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].rc == MGMT_ERR.EACCESSDENIED
    u = s.upload_file(data, file_path="test.txt")
    s.request.return_value = FileSystemManagementErrorV2(
        err=SMPErr(  # type: ignore
            rc=FS_MGMT_ERR.FILE_WRITE_FAILED, group=smphdr.GroupId.FILE_MANAGEMENT
        ),
    )
    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].err.rc == FS_MGMT_ERR.FILE_WRITE_FAILED


@patch("tests.test_smp_client.SMPMockTransport.mtu", new_callable=PropertyMock)
@patch("tests.test_smp_client.SMPMockTransport.max_unencoded_size", new_callable=PropertyMock)
@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [124, 127, 251, 498, 512, 1024, 2048, 4096, 8192])
async def test_file_upload_test_txt(
    mock_mtu: PropertyMock, mock_max_unencoded_size: PropertyMock, mtu: int
) -> None:
    mock_mtu.return_value = mtu
    mock_max_unencoded_size.return_value = mtu
    with open(
        str(Path("tests", "fixtures", "file_system", "test.txt")),
        "rb",
    ) as f:
        data = f.read()

    m = SMPMockTransport()
    s = SMPClient(m)

    accumulated_data = bytearray([])

    async def mock_request(
        request: FileUploadRequest, timeout_s: float = 120.000
    ) -> FileUploadResponse:
        accumulated_data.extend(request.data)
        return FileUploadResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    async for _ in s.upload_file(data, file_path="test.txt"):
        pass

    assert accumulated_data == data


@patch("tests.test_smp_client.SMPMockTransport.mtu", new_callable=PropertyMock)
@patch("tests.test_smp_client.SMPMockTransport.max_unencoded_size", new_callable=PropertyMock)
@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [124, 127, 251, 498, 512, 1024, 2048, 4096, 8192])
async def test_file_upload_test_255_bytes_file(
    mock_mtu: PropertyMock, mock_max_unencoded_size: PropertyMock, mtu: int
) -> None:
    mock_mtu.return_value = mtu
    mock_max_unencoded_size.return_value = mtu
    with open(
        str(Path("tests", "fixtures", "file_system", "255_bytes.txt")),
        "rb",
    ) as f:
        data = f.read()

    m = SMPMockTransport()
    s = SMPClient(m)

    accumulated_data = bytearray([])

    async def mock_request(
        request: FileUploadRequest, timeout_s: float = 120.000
    ) -> FileUploadResponse:
        accumulated_data.extend(request.data)
        return FileUploadResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    async for _ in s.upload_file(data, file_path="255_bytes.txt"):
        pass

    assert accumulated_data == data


@pytest.mark.asyncio
@pytest.mark.parametrize("max_smp_encoded_frame_size", [128, 256, 512, 1024, 2048, 4096, 8192])
@pytest.mark.parametrize("line_buffers", [1, 2, 3, 4, 8])
async def test_file_upload_test_encoded(max_smp_encoded_frame_size: int, line_buffers: int) -> None:
    with open(
        str(Path("tests", "fixtures", "file_system", "test.txt")),
        "rb",
    ) as f:
        file_data = f.read()

    line_length = max_smp_encoded_frame_size // line_buffers
    if line_length < 83:  # TODO: get better coverage
        pytest.skip("The line buffer size is too small")

    m = SMPSerialTransport(
        fragmentation_strategy=BufferParams(
            line_length=line_length,
            line_buffers=line_buffers,
        ),
    )
    s = SMPClient(m)
    # MTU is line_length * line_buffers, which may be <= max_smp_encoded_frame_size
    # due to integer division
    assert s._transport.mtu == line_length * line_buffers
    assert s._transport.mtu <= max_smp_encoded_frame_size

    packets: list[bytes] = []

    def mock_write(data: bytes) -> int:
        """Accumulate the raw packets in the global `packets`."""
        packets.append(data)
        return len(data)

    s._transport._conn.write = mock_write  # type: ignore
    type(s._transport._conn).out_waiting = 0  # type: ignore

    async def mock_request(
        request: ImageUploadWriteRequest, timeout_s: float = 120.000
    ) -> ImageUploadWriteResponse:
        # call the real send method (with write mocked) but don't bother with receive
        # this does provide coverage for the MTU-limited encoding done in the send method
        await s._transport.send(bytes(request.to_frame(sequence=0)))
        return ImageUploadWriteResponse(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    assert s._transport.max_unencoded_size < s._transport.mtu, (
        "The serial transport has encoding overhead"
    )

    async for _ in s.upload(file_data):
        pass

    reconstructed_file = bytearray([])

    decoder = smppacket.decode()
    next(decoder)

    for packet in packets:
        try:
            decoder.send(packet)
        except StopIteration as e:
            reconstructed_request = ImageUploadWriteRequest.loads(e.value).data
            reconstructed_file.extend(reconstructed_request.data)

            decoder = smppacket.decode()
            next(decoder)

    assert reconstructed_file == file_data


@pytest.mark.asyncio
async def test_download_file() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, timeout_s=2.5)

    s.request = AsyncMock()  # type: ignore

    m._mtu = 498
    m._max_unencoded_size = 498

    data = bytes([i % 255 for i in range(4097)])
    s.request.side_effect = [
        FileDownloadResponse(off=0, data=data[0:456], len=4097),
        FileDownloadResponse(off=456, data=data[456:912]),
        FileDownloadResponse(off=912, data=data[912:1368]),
        FileDownloadResponse(off=1368, data=data[1368:1824]),
        FileDownloadResponse(off=1824, data=data[1824:2280]),
        FileDownloadResponse(off=2280, data=data[2280:2736]),
        FileDownloadResponse(off=2736, data=data[2736:3192]),
        FileDownloadResponse(off=3192, data=data[3192:3648]),
        FileDownloadResponse(off=3648, data=data[3648:4097]),
    ]

    file_data = await s.download_file(file_path="test.txt")
    calls = [
        call(
            FileDownloadRequest(
                off=0,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=456,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=912,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=1368,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=1824,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=2280,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=2736,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=3192,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
        call(
            FileDownloadRequest(
                off=3648,
                name="test.txt",
            ),
            timeout_s=2.500,
        ),
    ]
    s.request.assert_has_awaits(
        calls,
        any_order=False,
    )

    assert file_data == data


@pytest.mark.asyncio
async def test_download_file_error_first() -> None:
    m = SMPMockTransport()
    s = SMPClient(m)

    s.request = AsyncMock()  # type: ignore

    s.request.return_value = FileSystemManagementErrorV2(
        err=SMPErr(  # type: ignore
            rc=FS_MGMT_ERR.FILE_WRITE_FAILED, group=smphdr.GroupId.FILE_MANAGEMENT
        ),
    )

    with pytest.raises(SMPUploadError) as e:
        await s.download_file("test.txt")
    assert e.value.args[0].err.rc == FS_MGMT_ERR.FILE_WRITE_FAILED


@pytest.mark.asyncio
async def test_download_file_no_len_first() -> None:
    m = SMPMockTransport()
    s = SMPClient(m)

    s.request = AsyncMock()  # type: ignore

    data = bytes([i % 255 for i in range(4097)])

    s.request.return_value = FileDownloadResponse(
        off=456,
        data=data[:456],
    )

    with pytest.raises(SMPUploadError) as e:
        await s.download_file("test.txt")
    assert e.value.args[0].startswith("No length received: ")


@pytest.mark.asyncio
async def test_download_file_error_not_first() -> None:
    m = SMPMockTransport()
    s = SMPClient(m)

    s.request = AsyncMock()  # type: ignore

    data = bytes([i % 255 for i in range(4097)])

    s.request.side_effect = [
        FileDownloadResponse(
            off=456,
            data=data[:456],
            len=len(data),
        ),
        FileSystemManagementErrorV2(
            err=SMPErr(  # type: ignore
                rc=FS_MGMT_ERR.FILE_WRITE_FAILED, group=smphdr.GroupId.FILE_MANAGEMENT
            ),
        ),
    ]
    with pytest.raises(SMPUploadError) as e:
        await s.download_file("test.txt")
    assert e.value.args[0].err.rc == FS_MGMT_ERR.FILE_WRITE_FAILED


@pytest.mark.parametrize(
    "buf_size, encoded_frame_size", [(384, 527), (512, 702), (1024, 1404), (2048, 2801)]
)
def test_maximize_upload_packet_fills_decoded_buffer(
    buf_size: int, encoded_frame_size: int
) -> None:
    """`_maximize_upload_packet` fills `max_unencoded_size` for image AND file uploads.

    Filling the decoded reassembly buffer (`buf_size - 4`) is the whole point of the
    maximizer: the resulting SMP message base64-encodes to a frame ~1.37x `buf_size` on
    the wire -- larger than the buffer, which the server decodes incrementally as the
    lines arrive. The unified generic handles both `ImageUploadWriteRequest` and `FileUploadRequest`.
    """
    client = SMPClient(SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=buf_size)))
    max_unencoded_size = client._transport.max_unencoded_size
    assert max_unencoded_size == buf_size - FRAME_OVERHEAD

    image = b"\xa5" * (4 * buf_size)  # plenty of source so the packet is never a short final
    image_packet = client._maximize_upload_packet(
        ImageUploadWriteRequest(
            off=0, data=b"", image=0, len=len(image), sha=sha256(image).digest()
        ),
        image,
    )
    file_packet = client._maximize_upload_packet(
        FileUploadRequest(name="/lfs1/firmware.bin", off=0, data=b"", len=len(image)), image
    )
    for maximized in (image_packet, file_packet):
        frame = bytes(maximized.to_frame(sequence=0))

        # the maximizer fills the decoded reassembly buffer exactly
        assert len(frame) == max_unencoded_size

        # ... so the encoded frame on the wire is ~1.37x buf_size -- bigger than the buffer
        on_wire = b"".join(smppacket.encode(frame, line_length=128))
        assert len(on_wire) == encoded_frame_size
        assert len(on_wire) > buf_size
