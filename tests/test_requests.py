"""Test the `SMPRequest` `Protocol` implementations."""

from __future__ import annotations

from typing import Tuple, Type

import pytest
from smp import error as smperr
from smp import image_management as smpimg
from smp import message as smpmsg
from smp import os_management as smpos
from smp import shell_management as smpsh
from smp.user import intercreate as smpic

from smpclient.generics import SMPError, SMPRequest, TEr0, TEr1, TErr, TRep
from smpclient.requests.image_management import (
    ImageManagementError,
    ImageStatesRead,
    ImageStatesWrite,
    ImageUploadWrite,
)
from smpclient.requests.os_management import EchoWrite, OSManagementError, ResetWrite
from smpclient.requests.shell_management import Execute, ShellManagementError
from smpclient.requests.user import intercreate as ic


@pytest.mark.parametrize(
    "test_tuple",
    [
        (
            smpimg.ImageStatesReadRequest(),
            ImageStatesRead(),
            smpimg.ImageStatesReadResponse,
            smpimg.ImageManagementErrorV0,
            smpimg.ImageManagementErrorV1,
            ImageManagementError,
        ),
        (
            smpimg.ImageStatesWriteRequest(hash=b"da hash"),
            ImageStatesWrite(hash=b"da hash"),
            smpimg.ImageStatesWriteResponse,
            smpimg.ImageManagementErrorV0,
            smpimg.ImageManagementErrorV1,
            ImageManagementError,
        ),
        (
            smpimg.ImageUploadWriteRequest(off=0, data=b"a"),
            ImageUploadWrite(off=0, data=b"a"),
            smpimg.ImageUploadProgressWriteResponse,
            smpimg.ImageManagementErrorV0,
            smpimg.ImageManagementErrorV1,
            ImageManagementError,
        ),
        (
            smpos.EchoWriteRequest(d="a"),
            EchoWrite(d="a"),
            smpos.EchoWriteResponse,
            smpos.OSManagementErrorV0,
            smpos.OSManagementErrorV1,
            OSManagementError,
        ),
        (
            smpos.ResetWriteRequest(),
            ResetWrite(),
            smpos.ResetWriteResponse,
            smpos.OSManagementErrorV0,
            smpos.OSManagementErrorV1,
            OSManagementError,
        ),
        (
            smpsh.ExecuteRequest(argv=["echo", "Hello"]),
            Execute(argv=["echo", "Hello"]),
            smpsh.ExecuteResponse,
            smpsh.ShellManagementErrorV0,
            smpsh.ShellManagementErrorV1,
            ShellManagementError,
        ),
        (
            smpic.ImageUploadWriteRequest(off=0, data=b"a"),
            ic.ImageUploadWrite(off=0, data=b"a"),
            smpic.ImageUploadWriteResponse,
            smpic.ErrorV0,
            smpic.ErrorV1,
            ic.Error,
        ),
    ],
)
def test_requests(
    test_tuple: Tuple[
        smpmsg.Request,
        SMPRequest[TRep, TEr0, TEr1, TErr],
        Type[smpmsg.Response],
        Type[smperr.ErrorV0],
        Type[smperr.ErrorV1],
        Type[SMPError],
    ],
) -> None:
    a, b, Response, ErrorV0, ErrorV1, Error = test_tuple

    # assert that headers match (other than sequence)
    assert a.header.op == b.header.op  # type: ignore
    assert a.header.version == b.header.version  # type: ignore
    assert a.header.flags == b.header.flags  # type: ignore
    assert a.header.length == b.header.length  # type: ignore
    assert a.header.group_id == b.header.group_id  # type: ignore
    assert a.header.command_id == b.header.command_id  # type: ignore

    # assert that the CBOR payloads match
    amodel = a.model_dump(exclude_unset=True, exclude={'header'}, exclude_none=True)
    bmodel = b.model_dump(exclude_unset=True, exclude={'header'}, exclude_none=True)  # type: ignore
    assert amodel == bmodel

    # assert that the response and error types are as expected
    assert b._Response is Response
    assert b._ErrorV0 is ErrorV0
    assert b._ErrorV1 is ErrorV1
    assert b._Error is Error
