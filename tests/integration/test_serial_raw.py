"""Raw-UART (`CONFIG_MCUMGR_TRANSPORT_RAW_UART`) serial integration tests.

`SMPSerialRawTransport` round-trips (echo, enumeration, MCUmgr params) are exercised by
the generic `connected_server` suite; these cover what is specific to the raw transport.
"""

from __future__ import annotations

import pytest

from smpclient.transport.serial import SMPSerialRawTransport
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_raw_transport_sizes_messages_to_server_buf_size(
    connected_server: ConnectedServer,
) -> None:
    """The raw transport caps an SMP message at the server's reported `buf_size`.

    `_initialize` reads the server's MCUmgr `buf_size` and the raw transport adopts it
    verbatim as `max_unencoded_size` -- the chunk size `SMPClient.upload` fills -- with
    no on-wire framing to subtract (the whole `[header][payload]` rides in the netbuf).
    """
    cs = connected_server
    if cs.fixture.transport != "serial_raw":
        pytest.skip("raw UART transport")

    transport = cs.client._transport
    assert isinstance(transport, SMPSerialRawTransport)
    assert transport.max_unencoded_size == cs.fixture.buf_size
