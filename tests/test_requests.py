"""Test the `SMPRequest` `Protocol` implementations."""

from __future__ import annotations

from typing import Tuple, Type

import pytest
from smp import error as smperr
from smp import file_management as smpfs
from smp import header as smphdr
from smp import image_management as smpimg
from smp import message as smpmsg
from smp import os_management as smpos
from smp import settings_management as smpset
from smp import shell_management as smpsh
from smp import statistics_management as smpstat
from smp import zephyr_management as smpz
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
from smpclient.requests.os_management import (
    BootloaderInformationRead,
    DateTimeRead,
    DateTimeWrite,
    EchoWrite,
    MCUMgrParametersRead,
    MemoryPoolStatisticsRead,
    OSApplicationInfoRead,
    ResetWrite,
    TaskStatisticsRead,
)
from smpclient.requests.settings_management import (
    CommitSettings,
    DeleteSetting,
    LoadSettings,
    ReadSetting,
    SaveSettings,
    WriteSetting,
)
from smpclient.requests.shell_management import Execute
from smpclient.requests.statistics_management import GroupData, ListOfGroups
from smpclient.requests.user import intercreate as ic
from smpclient.requests.zephyr_management import EraseStorage


@pytest.mark.parametrize(
    "test_tuple",
    (
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
        (
            smpos.BootloaderInformationReadRequest(),
            BootloaderInformationRead(),
            smpos.BootloaderInformationReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.DateTimeReadRequest(),
            DateTimeRead(),
            smpos.DateTimeReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.DateTimeWriteRequest(datetime="2040-01-01T00:00:00"),
            DateTimeWrite(datetime="2040-01-01T00:00:00"),
            smpos.DateTimeWriteResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.MCUMgrParametersReadRequest(),
            MCUMgrParametersRead(),
            smpos.MCUMgrParametersReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.MemoryPoolStatisticsReadRequest(),
            MemoryPoolStatisticsRead(),
            smpos.MemoryPoolStatisticsReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.OSApplicationInfoReadRequest(),
            OSApplicationInfoRead(),
            smpos.OSApplicationInfoReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpos.TaskStatisticsReadRequest(),
            TaskStatisticsRead(),
            smpos.TaskStatisticsReadResponse,
            smpos.OSManagementErrorV1,
            smpos.OSManagementErrorV2,
        ),
        (
            smpset.CommitSettingsRequest(),
            CommitSettings(),
            smpset.CommitSettingsResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpset.DeleteSettingRequest(name="test"),
            DeleteSetting(name="test"),
            smpset.DeleteSettingResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpset.LoadSettingsRequest(),
            LoadSettings(),
            smpset.LoadSettingsResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpset.ReadSettingRequest(name="test"),
            ReadSetting(name="test"),
            smpset.ReadSettingResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpset.SaveSettingsRequest(),
            SaveSettings(),
            smpset.SaveSettingsResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpset.WriteSettingRequest(name="test", val=b"value"),
            WriteSetting(name="test", val=b"value"),
            smpset.WriteSettingResponse,
            smpset.SettingsManagementErrorV1,
            smpset.SettingsManagementErrorV2,
        ),
        (
            smpstat.GroupDataRequest(name="test"),
            GroupData(name="test"),
            smpstat.GroupDataResponse,
            smpstat.StatisticsManagementErrorV1,
            smpstat.StatisticsManagementErrorV2,
        ),
        (
            smpstat.ListOfGroupsRequest(),
            ListOfGroups(),
            smpstat.ListOfGroupsResponse,
            smpstat.StatisticsManagementErrorV1,
            smpstat.StatisticsManagementErrorV2,
        ),
        (
            smpz.EraseStorageRequest(),
            EraseStorage(),
            smpz.EraseStorageResponse,
            smpz.ZephyrManagementErrorV1,
            smpz.ZephyrManagementErrorV2,
        ),
    ),
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
    assert a.BYTES[smphdr.Header.SIZE :] == b.BYTES[smphdr.Header.SIZE :]

    # assert that the response and error types are as expected
    assert b._Response is Response
    assert b._ErrorV1 is ErrorV1
    assert b._ErrorV2 is ErrorV2
    # assert that the response and error types are as expected
    assert b._Response is Response
    assert b._ErrorV1 is ErrorV1
    assert b._ErrorV2 is ErrorV2
