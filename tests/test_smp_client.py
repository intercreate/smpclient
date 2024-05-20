"""Tests for `SMPClient`."""

from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path
from typing import List, cast
from unittest.mock import AsyncMock, PropertyMock, call, patch

import pytest
from smp import header as smphdr
from smp import packet as smppacket
from smp.error import Err as SMPErr
from smp.image_management import (
    IMG_MGMT_ERR,
    ImageManagementErrorV0,
    ImageManagementErrorV1,
    ImageUploadWriteRequest,
)
from smp.os_management import OS_MGMT_RET_RC, OSManagementErrorV0, ResetWriteResponse

from smpclient import SMPClient
from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.requests.os_management import ResetWrite
from smpclient.transport.serial import SMPSerialTransport

if sys.version_info < (3, 10):
    from typing import Any

    async def anext(iterator: Any, default: Any = None) -> Any:
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            if default is None:
                raise
            return default

    def aiter(iterable: Any) -> Any:
        if hasattr(iterable, '__aiter__'):
            return iterable.__aiter__()
        else:
            raise TypeError(f"{iterable} is not async iterable")


class SMPMockTransport:
    """Satisfies the `SMPTransport` `Protocol`."""

    def __init__(self) -> None:
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.send = AsyncMock()
        self.receive = AsyncMock()
        self.mtu = PropertyMock()
        self.max_unencoded_size = PropertyMock()

    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()


def test_constructor() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, "address")
    assert s._transport is m
    assert s._address == "address"


@pytest.mark.asyncio
async def test_connect() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, "address")
    await s.connect()

    m.connect.assert_awaited_once_with("address")


@pytest.mark.asyncio
async def test_request() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, "address")

    req = ResetWrite()
    m.receive.return_value = ResetWriteResponse(sequence=req.header.sequence).BYTES  # type: ignore # noqa
    rep = await s.request(req)  # type: ignore
    m.send.assert_has_awaits([call(req.BYTES)])
    m.receive.assert_awaited()
    assert type(rep) is req.Response
    assert success(rep) is True
    assert error(rep) is False

    # test that a bad sequence raises `SMPBadSequence`
    req = ResetWrite()
    m.receive.return_value = ResetWriteResponse(sequence=req.header.sequence + 1).BYTES
    with pytest.raises(SMPBadSequence):
        await s.request(req)  # type: ignore

    # test that an error response is parsed
    req = ResetWrite()
    m.receive.return_value = OSManagementErrorV0(
        header=ResetWriteResponse(sequence=req.header.sequence).header,
        sequence=req.header.sequence,
        rc=OS_MGMT_RET_RC.UNKNOWN,
    ).BYTES

    rep = await s.request(req)  # type: ignore
    m.send.assert_has_awaits([call(req.BYTES)])
    m.receive.assert_awaited()
    assert rep.rc == OS_MGMT_RET_RC.UNKNOWN
    assert success(rep) is False
    assert error(rep) is True


@pytest.mark.asyncio
async def test_upload() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, "address")

    s.request = AsyncMock()  # type: ignore

    # refer to: https://docs.python.org/3/library/unittest.mock.html#unittest.mock.PropertyMock
    type(m).mtu = PropertyMock(return_value=498)
    type(m).max_unencoded_size = PropertyMock(return_value=498)

    chunk_size = 415  # max chunk given MTU

    image = bytes([i % 255 for i in range(4097)])
    req = ImageUploadWrite(
        off=0,
        data=image[:chunk_size],
        image=0,
        len=len(image),
        sha=sha256(image).digest(),
        upgrade=False,
    )

    u = s.upload(image)
    h = cast(smphdr.Header, req.header)

    s.request.return_value = ImageUploadWrite.Response(off=415)
    offset = await anext(u)
    assert offset == 415
    s.request.assert_awaited_once_with(
        ImageUploadWrite(
            header=smphdr.Header(
                op=h.op,
                version=h.version,
                flags=h.flags,
                length=h.length,
                group_id=h.group_id,
                sequence=h.sequence + 1,
                command_id=h.command_id,
            ),
            off=0,
            data=image[:chunk_size],
            image=0,
            len=len(image),
            sha=sha256(image).digest(),
            upgrade=False,
        )
    )

    s.request.return_value = ImageUploadWrite.Response(off=415 + 474)
    offset = await anext(u)
    assert offset == 415 + 474
    s.request.assert_awaited_with(
        ImageUploadWrite(
            header=smphdr.Header(
                op=h.op,
                version=h.version,
                flags=h.flags,
                length=h.length,
                group_id=h.group_id,
                sequence=h.sequence + 2,
                command_id=h.command_id,
            ),
            off=415,
            data=image[415 : 415 + 474],
        )
    )

    # assert that upload() raises SMPUploadError
    s.request.return_value = ImageManagementErrorV0(
        header=req.header, sequence=req.header.sequence, rc=IMG_MGMT_ERR.FLASH_ERASE_FAILED  # type: ignore # noqa
    )
    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].rc == IMG_MGMT_ERR.FLASH_ERASE_FAILED
    u = s.upload(image)
    h = cast(smphdr.Header, req.header)
    s.request.return_value = ImageManagementErrorV1(
        header=req.header,
        sequence=req.header.sequence,  # type: ignore
        err=SMPErr(  # type: ignore
            rc=IMG_MGMT_ERR.FLASH_WRITE_FAILED, group=smphdr.GroupId.IMAGE_MANAGEMENT
        ).model_dump(),
    )
    with pytest.raises(SMPUploadError) as e:
        _ = await anext(u)
    assert e.value.args[0].err.rc == IMG_MGMT_ERR.FLASH_WRITE_FAILED


@patch('tests.test_smp_client.SMPMockTransport.mtu', new_callable=PropertyMock)
@patch('tests.test_smp_client.SMPMockTransport.max_unencoded_size', new_callable=PropertyMock)
@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [23, 124, 127, 251, 498, 512, 1024, 2048, 4096, 8192])
async def test_upload_hello_world_bin(
    mock_mtu: PropertyMock, mock_max_unencoded_size: PropertyMock, mtu: int
) -> None:
    mock_mtu.return_value = mtu
    mock_max_unencoded_size.return_value = mtu

    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        'rb',
    ) as f:
        image = f.read()

    m = SMPMockTransport()
    s = SMPClient(m, "address")

    accumulated_image = bytearray([])

    async def mock_request(request: ImageUploadWrite) -> ImageUploadWrite.Response:
        accumulated_image.extend(request.data)
        return ImageUploadWrite.Response(off=request.off + len(request.data))

    s.request = mock_request  # type: ignore

    async for _ in s.upload(image):
        pass

    assert accumulated_image == image


@patch('tests.test_smp_client.SMPSerialTransport.mtu', new_callable=PropertyMock)
@pytest.mark.asyncio
@pytest.mark.parametrize("mtu", [48, 80, 124, 127, 256, 512, 1024, 2048, 4096, 8192])
async def test_upload_hello_world_bin_encoded(mock_mtu: PropertyMock, mtu: int) -> None:
    mock_mtu.return_value = mtu

    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        'rb',
    ) as f:
        image = f.read()

    m = SMPSerialTransport()
    s = SMPClient(m, "address")

    packets: List[bytes] = []

    def mock_write(data: bytes) -> int:
        """Accumulate the raw packets in the global `packets`."""
        packets.append(data)
        return len(data)

    s._transport._conn.write = mock_write  # type: ignore
    type(s._transport._conn).out_waiting = 0  # type: ignore

    async def mock_request(request: ImageUploadWrite) -> ImageUploadWrite.Response:
        # call the real send method (with write mocked) but don't bother with receive
        # this does provide coverage for the MTU-limited encoding done in the send method
        await s._transport.send(request.BYTES)
        return ImageUploadWrite.Response(off=request.off + len(request.data))

    s.request = mock_request  # type: ignore

    # refer to: https://docs.python.org/3/library/unittest.mock.html#unittest.mock.PropertyMock
    # type(m).mtu = PropertyMock(return_value=mtu)  # type: ignore

    assert (
        s._transport.max_unencoded_size < s._transport.mtu
    ), "The serial transport has encoding overhead"

    async for _ in s.upload(image):
        pass

    reconstructed_image = bytearray([])

    decoder = smppacket.decode()
    next(decoder)

    for packet in packets:
        try:
            decoder.send(packet)
        except StopIteration as e:
            reconstructed_request = ImageUploadWriteRequest.loads(e.value)
            reconstructed_image.extend(reconstructed_request.data)

            decoder = smppacket.decode()
            next(decoder)

    assert reconstructed_image == image
