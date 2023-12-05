"""Tests for `SMPClient`."""

from hashlib import sha256
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from smp import header as smphdr
from smp import packet as smppacket
from smp.error import Err as SMPErr
from smp.image_management import IMG_MGMT_ERR, ImageManagementErrorV0, ImageManagementErrorV1
from smp.os_management import OS_MGMT_RET_RC, OSManagementErrorV0, ResetWriteResponse

from smpclient import SMPClient
from smpclient.exceptions import SMPBadSequence, SMPUploadError
from smpclient.generics import error, success
from smpclient.requests.image_management import ImageUploadWrite
from smpclient.requests.os_management import ResetWrite


class SMPMockTransport:
    def __init__(self) -> None:
        self.connect = AsyncMock()
        self.send = AsyncMock()
        self.write = MagicMock()
        self.readuntil = AsyncMock()


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
    m.readuntil.return_value = [
        p for p in smppacket.encode(ResetWriteResponse(sequence=req.header.sequence).BYTES)  # type: ignore # noqa
    ][0]
    rep = await s.request(req)  # type: ignore
    packets = [call(p) for p in smppacket.encode(req.BYTES)]
    m.send.assert_has_awaits(packets)
    m.readuntil.assert_awaited()
    assert type(rep) is req.Response
    assert success(rep) is True
    assert error(rep) is False

    # test that a bad sequence raises `SMPBadSequence`
    req = ResetWrite()
    m.readuntil.return_value = [
        p for p in smppacket.encode(ResetWriteResponse(sequence=req.header.sequence + 1).BYTES)
    ][0]
    with pytest.raises(SMPBadSequence):
        await s.request(req)  # type: ignore

    # test that an error response is parsed
    req = ResetWrite()
    m.readuntil.return_value = [
        p
        for p in smppacket.encode(
            OSManagementErrorV0(
                header=ResetWriteResponse(sequence=req.header.sequence).header,
                sequence=req.header.sequence,
                rc=OS_MGMT_RET_RC.UNKNOWN,
            ).BYTES
        )
    ][0]
    rep = await s.request(req)  # type: ignore
    packets = [call(p) for p in smppacket.encode(req.BYTES)]
    m.send.assert_has_awaits(packets)
    m.readuntil.assert_awaited()
    assert rep.rc == OS_MGMT_RET_RC.UNKNOWN
    assert success(rep) is False
    assert error(rep) is True


@pytest.mark.asyncio
async def test_upload() -> None:
    m = SMPMockTransport()
    s = SMPClient(m, "address")

    s.request = AsyncMock()  # type: ignore

    image = bytes([i % 255 for i in range(4097)])
    req = ImageUploadWrite(
        off=0,
        data=image[:2048],
        image=0,
        len=len(image),
        sha=sha256(image).digest(),
        upgrade=False,
    )

    u = s.upload(image)
    h = cast(smphdr.Header, req.header)

    s.request.return_value = ImageUploadWrite.Response(off=2048)
    offset = await anext(u)
    assert offset == 2048
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
            data=image[:2048],
            image=0,
            len=len(image),
            sha=sha256(image).digest(),
            upgrade=False,
        )
    )

    s.request.return_value = ImageUploadWrite.Response(off=2049)
    offset = await anext(u)
    assert offset == 2049
    s.request.assert_awaited_with(
        ImageUploadWrite(
            header=smphdr.Header(
                op=h.op,
                version=h.version,
                flags=h.flags,
                length=2064,
                group_id=h.group_id,
                sequence=h.sequence + 2,
                command_id=h.command_id,
            ),
            off=2048,
            data=image[2048:4096],
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
