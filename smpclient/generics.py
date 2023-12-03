"""Some Generic helpers for the SMP module."""

from enum import IntEnum
from typing import Protocol, Type, TypeGuard, TypeVar

from smp import error as smperror
from smp import header as smpheader
from smp import message as smpmessage

TErrEnum = TypeVar("TErrEnum", bound=IntEnum)
TEr0 = TypeVar("TEr0", bound=smperror.ErrorV0)
TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
TErr = TypeVar("TErr", bound='SMPError')
TRep = TypeVar("TRep", bound=smpmessage.ReadResponse | smpmessage.WriteResponse)


class SMPError(smperror.ErrorV0[TErrEnum]):
    """An abstraction on top of SMP that joins and flattens V0 and V1 `Error`s"""

    group: smpheader.GroupId | None = None


def _is_ErrorV0(error: TEr0 | TEr1 | TRep) -> TypeGuard[TEr0]:
    return error.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V0


def _is_ErrorV1(error: TEr0 | TEr1 | TRep) -> TypeGuard[TEr1]:
    return error.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V1


def flatten_error(
    error: smperror.ErrorV0[TErrEnum] | smperror.ErrorV1[TErrEnum],
) -> SMPError[TErrEnum]:
    """Flatten a `Generic` `ErrorV0` or `ErrorV1` into a `Generic` `SMPError`."""
    if _is_ErrorV0(error):
        return SMPError(header=error.header, rc=error.rc, rsn=error.rsn)
    elif _is_ErrorV1(error):
        return SMPError(header=error.header, rc=error.err.rc, group=error.err.group)
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
        Response = smpimg.ImageStatesReadResponse
        ErrorV0 = smpimg.ImageManagementError1
        ErrorV1 = smpimg.ImageManagementError2
        Error = ImageManagementError
    ```
    """

    Response: Type[TRep]
    ErrorV0: Type[TEr0]
    ErrorV1: Type[TEr1]
    Error: Type[TErr]

    @property
    def BYTES(self) -> bytes:
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
