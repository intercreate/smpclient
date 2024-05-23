"""Some Generic helpers for the SMP module."""

from __future__ import annotations

from enum import IntEnum
from typing import Generic, Protocol, Type, TypeVar, Union

from pydantic import BaseModel, ConfigDict
from smp import error as smperror
from smp import header as smpheader
from smp import message as smpmessage
from typing_extensions import TypeGuard

TErrEnum = TypeVar("TErrEnum", bound=IntEnum)
TEr0 = TypeVar("TEr0", bound=smperror.ErrorV0)
TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
TErr = TypeVar("TErr", bound='SMPError')
TRep = TypeVar("TRep", bound=Union[smpmessage.ReadResponse, smpmessage.WriteResponse])


class SMPError(BaseModel, Generic[TErrEnum]):
    """An abstraction on top of SMP that joins and flattens V0 and V1 `Error`s"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    RESPONSE_TYPE: smpmessage.ResponseType
    header: smpheader.Header
    group: IntEnum | int | smpheader.GroupId | smpheader.UserGroupId
    rc: smperror.MGMT_ERR | TErrEnum
    rsn: str | None = None


def _is_ErrorV0(error: TEr0 | TEr1 | TRep) -> TypeGuard[TEr0]:
    return error.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V0


def _is_ErrorV1(error: TEr0 | TEr1 | TRep) -> TypeGuard[TEr1]:
    return error.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V1


def flatten_error(
    error: smperror.ErrorV0 | smperror.ErrorV1[TErrEnum],
) -> SMPError[TErrEnum]:
    """Flatten a `Generic` `ErrorV0` or `ErrorV1` into a `Generic` `SMPError`."""
    if error.header is None:
        raise TypeError(f"{error} does not have a valid header!")
    if _is_ErrorV0(error):
        return SMPError(
            RESPONSE_TYPE=error.RESPONSE_TYPE,
            header=error.header,
            group=error.header.group_id,
            rc=error.rc,
            rsn=error.rsn,
        )
    elif _is_ErrorV1(error):
        return SMPError[TErrEnum](
            RESPONSE_TYPE=error.RESPONSE_TYPE,
            header=error.header,
            group=error.err.group,
            rc=error.err.rc,
        )
    else:
        raise Exception(f"{error} is not an Error?")


class SMPRequest(Protocol[TRep, TEr0, TEr1, TErr]):
    """A `Protocol` that groups the expected response and errors with a request.

    To use, inherit from an SMP Read or Write `Request` and define its expected
    `Response`, `ErrorV0`, `ErrorV1`, and `SMPError`.

    Example:
    ```python
    class ImageManagementError(SMPError[smpimg.IMG_MGMT_ERR]):
        _GROUP_ID = smpheader.GroupId.IMAGE_MANAGEMENT


    class ImageStatesRead(smpimg.ImageStatesReadRequest):
        _Response = smpimg.ImageStatesReadResponse
        _ErrorV0 = smpimg.ImageManagementError1
        _ErrorV1 = smpimg.ImageManagementError2
        _Error = ImageManagementError
    ```
    """

    _Response: Type[TRep]
    _ErrorV0: Type[TEr0]
    _ErrorV1: Type[TEr1]
    _Error: Type[TErr]

    @property
    def BYTES(self) -> bytes:  # pragma: no cover
        ...


def error(response: TErr | TRep) -> TypeGuard[TErr]:
    """`TypeGuard` that returns `True` if the `response` is an `Error` (including `SMPError`)."""
    return response.RESPONSE_TYPE in {
        smpmessage.ResponseType.ERROR_V0,
        smpmessage.ResponseType.ERROR_V1,
    }


def success(response: TRep | TErr) -> TypeGuard[TRep]:
    """`TypeGuard` that returns `True` if the `response` is a successful `Response`."""
    return response.RESPONSE_TYPE == smpmessage.ResponseType.SUCCESS
