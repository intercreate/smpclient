"""Helpers shared by the transport tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar
from unittest.mock import AsyncMock, patch

from smp.os_management import MCUMgrParametersReadResponse

from smpclient.transport import _ConnectableTransport

_T = TypeVar("_T", bound=_ConnectableTransport)


@contextmanager
def advertise(buf_size: int) -> Iterator[AsyncMock]:
    """Answer every transport's MCUmgr parameters read as a server whose buffer is `buf_size`."""
    with patch(
        "smpclient._request.read_mcumgr_parameters",
        AsyncMock(return_value=MCUMgrParametersReadResponse(buf_size=buf_size, buf_count=1)),
    ) as read:
        yield read


async def negotiated(transport: _T, buf_size: int) -> _T:
    """`transport`, after negotiating against a server whose buffer is `buf_size`."""
    with advertise(buf_size):
        await transport.negotiate()
    return transport
