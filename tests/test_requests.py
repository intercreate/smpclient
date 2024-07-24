"""Test the `SMPRequest` `Protocol` implementations."""

from __future__ import annotations

from typing import Tuple, Type

import pytest
from smp import error as smperr
from smp import file_management as smpfs
from smp import image_management as smpimg
from smp import message as smpmsg
from smp import os_management as smpos
from smp import shell_management as smpsh
from smp.user import intercreate as smpic

from smpclient.generics import SMPRequest, TEr1, TEr2, TRep
from smpclient.requests.file_management import (
    FileClose,
    FileDownload,
    FileHashChecksum,
    FileStatus,
    FileUpload,
    SupportedFileHashChecksumTypes,
)
from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite, ImageUploadWrite
from smpclient.requests.os_management import EchoWrite, ResetWrite
from smpclient.requests.shell_management import Execute
from smpclient.requests.user import intercreate as ic


@pytest.mark.parametrize(
    "test_tuple",
    [
        (
            smpimg.ImageStatesReadRequest(),
            ImageStatesRead(),
            smpimg.ImageStatesReadResponse,
            smpimg.ImageManagementErrorV1,
            smpimg.ImageManagementErrorV2,
        ),
        (
            smpimg.ImageStatesWriteRequest(hash=b"da hash"),
            ImageStatesWrite(hash=b"da hash"),
            smpimg.ImageStatesWriteResponse,
            smpimg.ImageManagementErrorV1,
            smpimg.ImageManagementErrorV2,
        ),
        (
            smpimg.ImageUploadWriteRequest(off=0, data=b"a"),
            ImageUploadWrite(off=0, data=b"a"),
            smpimg.ImageUploadWriteResponse,
            smpimg.ImageManagementErrorV1,
            smpimg.ImageManagementErrorV2,
        ),
        (
            smpos.EchoWriteRequest(d="a"),
            EchoWrite(d="a"),
            smpos.EchoWriteResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.ResetWriteRequest(),
            ResetWrite(),
            smpos.ResetWriteResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpsh.ExecuteRequest(argv=["echo", "Hello"]),
            Execute(argv=["echo", "Hello"]),
            smpsh.ExecuteResponse,
            smpsh.ShellManagementErrorV1,
            smpsh.ShellManagementErrorV2,
        ),
        (
            smpic.ImageUploadWriteRequest(off=0, data=b"a"),
            ic.ImageUploadWrite(off=0, data=b"a"),
            smpic.ImageUploadWriteResponse,
            smpic.ErrorV1,
            smpic.ErrorV2,
        ),
        (
            smpfs.FileDownloadRequest(off=0, name="test.txt"),
            FileDownload(off=0, name="test.txt"),
            smpfs.FileDownloadResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
        (
            smpfs.FileUploadRequest(off=0, name="test.txt", data=b"a", len=100),
            FileUpload(off=0, name="test.txt", data=b"a", len=100),
            smpfs.FileUploadResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
        (
            smpfs.FileStatusRequest(name="test.txt"),
            FileStatus(name="test.txt"),
            smpfs.FileStatusResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
        (
            smpfs.FileHashChecksumRequest(name="test.txt", type="sha256", off=0, len=200),
            FileHashChecksum(name="test.txt", type="sha256", off=0, len=200),
            smpfs.FileHashChecksumResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
        (
            smpfs.SupportedFileHashChecksumTypesRequest(),
            SupportedFileHashChecksumTypes(),
            smpfs.SupportedFileHashChecksumTypesResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
        (
            smpfs.FileCloseRequest(),
            FileClose(),
            smpfs.FileCloseResponse,
            smpfs.FileSystemManagementErrorV1,
            smpfs.FileSystemManagementErrorV2,
        ),
    ],
)
def test_requests(
    test_tuple: Tuple[
        smpmsg.Request,
        SMPRequest[TRep, TEr1, TEr2],
        Type[smpmsg.Response],
        Type[smperr.ErrorV1],
        Type[smperr.ErrorV2],
    ],
) -> None:
    a, b, Response, ErrorV1, ErrorV2 = test_tuple

    # assert that headers match (other than sequence)
    assert a.header.op == b.header.op
    assert a.header.version == b.header.version
    assert a.header.flags == b.header.flags
    assert a.header.length == b.header.length
    assert a.header.group_id == b.header.group_id
    assert a.header.command_id == b.header.command_id

    # assert that the CBOR payloads match
    amodel = a.model_dump(exclude_unset=True, exclude={'header'}, exclude_none=True)
    bmodel = b.model_dump(exclude_unset=True, exclude={'header'}, exclude_none=True)  # type: ignore
    assert amodel == bmodel

    # assert that the response and error types are as expected
    assert b._Response is Response
    assert b._ErrorV1 is ErrorV1
    assert b._ErrorV2 is ErrorV2
