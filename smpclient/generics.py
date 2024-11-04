"""Generics and Type Narrowing for SMP Requests and Responses."""

from __future__ import annotations

from typing import Protocol, Type, TypeVar, Union

from smp import error as smperror
from smp import header as smphdr
from smp import message as smpmessage
from typing_extensions import TypeIs

TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
"""Type of SMP Error V1."""

TEr2 = TypeVar("TEr2", bound=smperror.ErrorV2)
"""Type of SMP Error V2."""

TRep = TypeVar("TRep", bound=Union[smpmessage.ReadResponse, smpmessage.WriteResponse])
"""Type of successful SMP Response (ReadResponse or WriteResponse)."""


class SMPRequest(Protocol[TRep, TEr1, TEr2]):
    """A `Protocol` that groups the expected response and errors with a request.

    To use, inherit from an SMP Read or Write `Request` and define its expected
    `Response`, `ErrorV1`, and `ErrorV2`.

    Example:
    ```python
    class ImageStatesRead(smpimg.ImageStatesReadRequest):
        _Response = smpimg.ImageStatesReadResponse
        _ErrorV1 = smpimg.ImageManagementErrorV1
        _ErrorV2 = smpimg.ImageManagementErrorV2
    ```
    """

    _Response: Type[TRep]
    _ErrorV1: Type[TEr1]
    _ErrorV2: Type[TEr2]

    @property
    def BYTES(self) -> bytes:  # pragma: no cover
        ...

    @property
    def header(self) -> smphdr.Header:  # pragma: no cover
        ...


def error_v1(response: smperror.ErrorV1 | TEr2 | TRep) -> TypeIs[smperror.ErrorV1]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV1`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV1`.
    """
    return response.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V1


def error_v2(response: smperror.ErrorV1 | TEr2 | TRep) -> TypeIs[TEr2]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV2`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV2`.
    """
    return response.RESPONSE_TYPE == smpmessage.ResponseType.ERROR_V2


def error(response: smperror.ErrorV1 | TEr2 | TRep) -> TypeIs[smperror.ErrorV1 | TEr2]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV1` or `ErrorV2`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV1` or `ErrorV2`.
    """
    return error_v1(response) or error_v2(response)


def success(response: smperror.ErrorV1 | TEr2 | TRep) -> TypeIs[TRep]:
    """`TypeIs` that returns `True` if the `response` is a successful `Response`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is a successful `Response`.
    """
    return response.RESPONSE_TYPE == smpmessage.ResponseType.SUCCESS
