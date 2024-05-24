"""Some Generic helpers for the SMP module."""

from __future__ import annotations

from typing import Protocol, Type, TypeVar, Union

from smp import error as smperror
from smp import message as smpmessage
from typing_extensions import TypeGuard

TEr0 = TypeVar("TEr0", bound=smperror.ErrorV0)
TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
TRep = TypeVar("TRep", bound=Union[smpmessage.ReadResponse, smpmessage.WriteResponse])


class SMPRequest(Protocol[TRep, TEr0, TEr1]):
    """A `Protocol` that groups the expected response and errors with a request.

    To use, inherit from an SMP Read or Write `Request` and define its expected
    `Response`, `ErrorV0`, and `ErrorV1`.

    Example:
    ```python
    class ImageStatesRead(smpimg.ImageStatesReadRequest):
        _Response = smpimg.ImageStatesReadResponse
        _ErrorV0 = smpimg.ImageManagementErrorV0
        _ErrorV1 = smpimg.ImageManagementErrorV1
    ```
    """

    _Response: Type[TRep]
    _ErrorV0: Type[TEr0]
    _ErrorV1: Type[TEr1]

    @property
    def BYTES(self) -> bytes:  # pragma: no cover
        ...


def error_v0(response: smperror.ErrorV0 | TEr1 | TRep) -> TypeGuard[smperror.ErrorV0]:
    """`TypeGuard` that returns `True` if the `response` is an `ErrorV0`."""
    return response.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V0


def error_v1(response: smperror.ErrorV0 | TEr1 | TRep) -> TypeGuard[TEr1]:
    """`TypeGuard` that returns `True` if the `response` is an `ErrorV1`."""
    return response.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V1


def error(response: smperror.ErrorV0 | TEr1 | TRep) -> TypeGuard[smperror.ErrorV0 | TEr1]:
    """`TypeGuard` that returns `True` if the `response` is an `ErrorV0` or `ErrorV1`."""
    return error_v0(response) or error_v1(response)


def success(response: smperror.ErrorV0 | TEr1 | TRep) -> TypeGuard[TRep]:
    """`TypeGuard` that returns `True` if the `response` is a successful `Response`."""
    return response.RESPONSE_TYPE == smpmessage.ResponseType.SUCCESS
