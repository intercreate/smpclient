"""Static request/response narrowing assertions for `SMPClient.request`.

These functions are verified by mypy and pyright (the `typecheck` task); they are never
executed. They encode the exhaustiveness contract that `SMPClient.request` inherits from
`smp.SMPRequest`: the returned union narrows to exactly the request's `Response`,
`ErrorV1`, and `ErrorV2`, and `assert_never` proves nothing else remains.

`assert_type` pins the narrowed type rather than merely asserting it is *a* response, so a
binding that silently widened -- the failure mode of a non-generic `TypeIs` -- fails here.
"""

from smp import enumeration_management as enum
from smp import file_management as fs
from smp import image_management as img
from smp import os_management as os
from smp import settings_management as settings
from smp import shell_management as shell
from smp import statistics_management as stat
from smp import zephyr_management as zephyr
from smp.user import intercreate as ic
from typing_extensions import assert_never, assert_type

from smpclient import SMPClient, error, error_v1, error_v2, success


async def _check_exhaustive_narrowing(client: SMPClient) -> None:
    """The whole point: every arm is reachable and `assert_never` closes the union."""
    response = await client.request(os.EchoWriteRequest(d="hello"))

    if success(response):
        assert_type(response, os.EchoWriteResponse)
    elif error_v1(response):
        assert_type(response, os.OSManagementErrorV1)
    elif error_v2(response):
        assert_type(response, os.OSManagementErrorV2)
    else:
        assert_never(response)


async def _check_error_groups_both_arms(client: SMPClient) -> None:
    """`error` covers both error types, and narrows further to each."""
    response = await client.request(img.ImageStatesReadRequest())

    if success(response):
        assert_type(response, img.ImageStatesReadResponse)
    elif error(response):
        if error_v1(response):
            assert_type(response, img.ImageManagementErrorV1)
        elif error_v2(response):
            assert_type(response, img.ImageManagementErrorV2)
        else:
            assert_never(response)
    else:
        assert_never(response)


async def _check_os_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(os.ResetWriteRequest()),
        os.ResetWriteResponse | os.OSManagementErrorV1 | os.OSManagementErrorV2,
    )


async def _check_image_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(img.ImageUploadWriteRequest(off=0, data=b"")),
        img.ImageUploadWriteResponse | img.ImageManagementErrorV1 | img.ImageManagementErrorV2,
    )


async def _check_file_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(fs.FileDownloadRequest(off=0, name="f")),
        fs.FileDownloadResponse | fs.FileSystemManagementErrorV1 | fs.FileSystemManagementErrorV2,
    )


async def _check_enumeration_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(enum.GroupCountRequest()),
        enum.GroupCountResponse | enum.EnumManagementErrorV1 | enum.EnumManagementErrorV2,
    )


async def _check_settings_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(settings.ReadSettingRequest(name="n")),
        settings.ReadSettingResponse
        | settings.SettingsManagementErrorV1
        | settings.SettingsManagementErrorV2,
    )


async def _check_shell_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(shell.ExecuteRequest(argv=["echo"])),
        shell.ExecuteResponse | shell.ShellManagementErrorV1 | shell.ShellManagementErrorV2,
    )


async def _check_statistics_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(stat.ListOfGroupsRequest()),
        stat.ListOfGroupsResponse
        | stat.StatisticsManagementErrorV1
        | stat.StatisticsManagementErrorV2,
    )


async def _check_zephyr_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(zephyr.EraseStorageRequest()),
        zephyr.EraseStorageResponse
        | zephyr.ZephyrManagementErrorV1
        | zephyr.ZephyrManagementErrorV2,
    )


async def _check_intercreate_binding(client: SMPClient) -> None:
    assert_type(
        await client.request(ic.ImageUploadWriteRequest(off=0, data=b"")),
        ic.ImageUploadWriteResponse | ic.ErrorV1 | ic.ErrorV2,
    )
