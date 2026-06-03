"""Serial fragmentation / netbuf-parameter integration tests (PR #73/#81).

These exercise the work in PR #73/#81 against the buffer-size matrix: the serial
transport's `Auto` strategy derives its fragmentation parameters from the server's
MCUmgr `buf_size`, falls back to conservative defaults when the params command is
unavailable, and fragmented messages reassemble correctly.
"""

from __future__ import annotations

import pytest

from smpclient.generics import success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport.serial import SMPSerialTransport
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_auto_config_matches_server_buf_size(connected_server: ConnectedServer) -> None:
    """Across the buffer-size matrix, the Auto MTU equals the server's buf_size."""
    cs = connected_server
    if cs.fixture.transport != "serial" or not cs.fixture.params_supported:
        pytest.skip("serial + MCUmgr params required for Auto buffer sizing")

    transport = cs.client._transport
    assert isinstance(transport, SMPSerialTransport)
    assert transport.mtu == cs.fixture.buf_size
    assert transport.max_unencoded_size < cs.fixture.buf_size
    # line_buffers derives from buf_size // line_length (0 when buf_size < line_length).
    assert transport._line_buffers == cs.fixture.buf_size // transport._line_length


async def test_noparams_falls_back_to_defaults(connected_server: ConnectedServer) -> None:
    """With the params command disabled, the client keeps conservative defaults and still works."""
    cs = connected_server
    if cs.fixture.params_supported:
        pytest.skip("only the params-disabled fixture exercises the fallback")

    transport = cs.client._transport
    assert isinstance(transport, SMPSerialTransport)
    # No buf_size from the server -> default BufferParams (one line buffer), not the server's 384.
    assert transport._line_buffers == 1
    assert transport.mtu == transport._line_length

    response = await cs.client.request(EchoWrite(d="fallback works"))
    assert success(response)
    assert response.r == "fallback works"


async def test_two_fragment_roundtrip(connected_server: ConnectedServer) -> None:
    """A message spanning two line packets reassembles on every serial fixture that can hold it."""
    cs = connected_server
    if cs.fixture.transport != "serial":
        pytest.skip("serial line-packet fragmentation")

    transport = cs.client._transport
    assert isinstance(transport, SMPSerialTransport)
    if transport.max_unencoded_size < transport._line_length:
        pytest.skip("buf_size too small to span two line packets")

    text = "Z" * transport._line_length  # > one line packet, within a 2-fragment burst
    response = await cs.client.request(EchoWrite(d=text))
    assert success(response)
    assert response.r == text


async def test_max_payload_roundtrip(connected_server: ConnectedServer) -> None:
    """The client's full advertised payload round-trips where the link sustains the burst."""
    cs = connected_server
    if cs.fixture.transport != "serial":
        pytest.skip("serial transport")
    if cs.fixture.bursty_fragment_drop:
        pytest.skip("native_sim PTY UART drops >2-fragment bursts; covered on QEMU + mps2")

    transport = cs.client._transport
    text = "M" * (transport.max_unencoded_size - len(EchoWrite(d="").BYTES) - 4)
    request = EchoWrite(d=text)
    assert len(request.BYTES) <= transport.max_unencoded_size

    response = await cs.client.request(request)
    assert success(response)
    assert response.r == text
