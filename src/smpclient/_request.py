"""The SMP request/response exchange over a live `SMPTransport`."""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Final, TypeVar, Union, cast

import msgspec
from smp import SMPRequest
from smp import error as smperror
from smp import header as smpheader
from smp import message as smpmsg
from smp.os_management import MCUMgrParametersReadRequest, MCUMgrParametersReadResponse
from typing_extensions import TypeIs, assert_never

from smpclient.exceptions import SMPBadSequence, SMPValidationException

if TYPE_CHECKING:
    from types_bits import u8

    from smpclient.transport import SMPTransport

try:
    from asyncio import timeout  # type: ignore
except ImportError:  # backport for Python3.10 and below
    from async_timeout import timeout  # type: ignore

logger = logging.getLogger(__name__)

TEr1 = TypeVar("TEr1", bound=smperror.ErrorV1)
"""Type of SMP Error V1."""

TEr2 = TypeVar("TEr2", bound=smperror.ErrorV2)
"""Type of SMP Error V2."""

TRep = TypeVar("TRep", bound=Union[smpmsg.ReadResponse, smpmsg.WriteResponse])
"""Type of successful SMP Response (ReadResponse or WriteResponse)."""


def error_v1(response: smpmsg.Response) -> TypeIs[smperror.ErrorV1]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV1`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV1`.
    """
    return response.RESPONSE_TYPE == smpmsg.ResponseType.ERROR_V1


def error_v2(response: smpmsg.Response) -> TypeIs[smperror.ErrorV2[Any]]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV2`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV2`.
    """
    return response.RESPONSE_TYPE == smpmsg.ResponseType.ERROR_V2


def error(
    response: smpmsg.Response,
) -> TypeIs[Union[smperror.ErrorV1, smperror.ErrorV2[Any]]]:
    """`TypeIs` that returns `True` if the `response` is an `ErrorV1` or `ErrorV2`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is an `ErrorV1` or `ErrorV2`.
    """
    return error_v1(response) or error_v2(response)


def success(
    response: smpmsg.Response,
) -> TypeIs[Union[smpmsg.ReadResponse, smpmsg.WriteResponse]]:
    """`TypeIs` that returns `True` if the `response` is a successful `Response`.

    Args:
        response: The response to check.

    Returns:
        `True` if the `response` is a successful `Response`.
    """
    return response.RESPONSE_TYPE == smpmsg.ResponseType.SUCCESS


def wrapping_sequence() -> Iterator[u8]:
    """The default SMP sequence space: `0x00`-`0xFF`, wrapping."""
    return cast("Iterator[u8]", itertools.cycle(range(0x100)))


def _hexdump(frame: bytes) -> str:
    """Format `frame` as an offset/hex/printable-ASCII dump for readable debug logging."""

    def row(offset: int) -> str:
        chunk: Final = frame[offset : offset + 16]
        columns: Final = " ".join(f"{byte:02x}" for byte in chunk)
        printable: Final = "".join(chr(byte) if 0x20 <= byte <= 0x7E else "." for byte in chunk)
        return f"\t{offset:04x}  {columns:<47}  {printable}"

    return "\n".join(row(offset) for offset in range(0, len(frame), 16))


def _validation_failure(
    header: smpheader.Header,
    frame: bytes,
    errors: tuple[tuple[type[smpmsg.Response], msgspec.DecodeError], ...],
) -> tuple[str, str]:
    """Return the `(summary, details)` describing why `frame` matched none of `errors`' types."""
    summary: Final = (
        "\nFrame could not be parsed as any of:\n"
        f"\t{[response.__name__ for response, _ in errors]}\n"
    )
    details: Final = "\n".join(
        (
            f"Header:\n\t{header}",
            f"Frame:\n{_hexdump(frame)}",
            "Errors:",
            *(
                f"\tCould not be parsed as {response.__name__}: {error}"
                for response, error in errors
            ),
        )
    )
    return summary, details


async def exchange(
    transport: SMPTransport,
    request: SMPRequest[TRep, TEr1, TEr2],
    sequence: u8,
    timeout_s: float,
) -> TRep | TEr1 | TEr2:
    """Send `request` as SMP sequence `sequence` and return the typed Response or Error.

    Args:
        transport: the live transport to exchange the request over
        request: the `SMPRequest` to send
        sequence: the SMP sequence number to send `request` as
        timeout_s: the timeout for the exchange in seconds

    Returns:
        The typed and validated Response or Error

    Raises:
        TimeoutError: if the request times out
        SMPBadSequence: if the response sequence does not match the request sequence
        SMPValidationException: if the response cannot be parsed as a Response or Error
    """
    request_frame: Final = request.to_frame(sequence)

    try:
        async with timeout(timeout_s):
            frame = await transport.send_and_receive(bytes(request_frame))
    except asyncio.TimeoutError:
        timeout_message: Final = f"Timeout ({timeout_s}s) waiting for request {request}"
        logger.error(timeout_message)
        raise TimeoutError(timeout_message)

    header = smpheader.Header.loads(frame[: smpheader.Header.SIZE])

    if header.sequence != request_frame.header.sequence:
        raise SMPBadSequence(
            f"Bad sequence {header.sequence}, expected {request_frame.header.sequence}"
        )

    # `SMPMalformed` and `SMPMismatchedGroupId` are not caught: they fail all three
    # candidates identically, so they are transport errors rather than a mismatch.
    errors: list[tuple[type[smpmsg.Response], msgspec.DecodeError]] = []
    try:
        return request._Response.loads(frame).data  # type: ignore[return-value]
    except msgspec.DecodeError as error:
        errors.append((request._Response, error))
    try:
        return request._ErrorV1.loads(frame).data
    except msgspec.DecodeError as error:
        errors.append((request._ErrorV1, error))
    try:
        return request._ErrorV2.loads(frame).data
    except msgspec.DecodeError as error:
        errors.append((request._ErrorV2, error))

    summary, details = _validation_failure(header, frame, tuple(errors))
    logger.error(summary + details)
    raise SMPValidationException(summary, details) from None


async def read_mcumgr_parameters(
    transport: SMPTransport, sequence: u8, timeout_s: float
) -> MCUMgrParametersReadResponse | None:
    """Read the server's MCUmgr parameters over `transport`.

    Args:
        transport: the live transport to read the parameters over
        sequence: the SMP sequence number to send the request as
        timeout_s: the timeout for the exchange in seconds

    Returns:
        The parameters, or `None` (with a warning) if the server answers with an error or
        not at all
    """
    try:
        response: Final = await exchange(
            transport, MCUMgrParametersReadRequest(), sequence, timeout_s
        )
    except TimeoutError:
        logger.warning("Timeout waiting for MCUMgr parameters")
        return None
    if success(response):
        logger.debug(f"MCUMgr parameters: {response}")
        return response
    elif error(response):
        logger.warning(f"Error reading MCUMgr parameters: {response}")
        return None
    else:
        assert_never(response)
