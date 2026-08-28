"""Generics and Type Narrowing for SMP Requests and Responses."""

from typing import TypeVar, Union

from smp import error as smperror
from smp import message as smpmessage
from typing_extensions import TypeIs

TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
"""Type of SMP Error V1."""

TEr2 = TypeVar("TEr2", bound=smperror.ErrorV2)
"""Type of SMP Error V2."""

TRep = TypeVar("TRep", bound=Union[smpmessage.ReadResponse, smpmessage.WriteResponse])
"""Type of successful SMP Response (ReadResponse or WriteResponse)."""


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
