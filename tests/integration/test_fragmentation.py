"""Serial fragmentation / netbuf-parameter integration tests (PR #73/#81).

These exercise the work in PR #73/#81 against the buffer-size matrix: the serial
transport's `Auto` strategy derives its fragmentation parameters from the server's
MCUmgr `buf_size`, falls back to conservative defaults when the params command is
unavailable, and fragmented messages reassemble correctly.
"""

from __future__ import annotations

import pytest
from smp import packet as smppacket

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
    assert cs.fixture.buf_size is not None
    assert transport.mtu == cs.fixture.buf_size
    # Auto uses the full decoded netbuf: buf_size minus the SMP serial frame's
    # 2-byte length + 2-byte CRC16 (the test_max_payload_roundtrip cases prove the
    # server actually accepts a message of exactly this size).
    frame_overhead = smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size
    assert transport.max_unencoded_size == cs.fixture.buf_size - frame_overhead


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
    # Generous timeout: a multi-fragment round-trip is slow on emulated MCUs under CI load.
    response = await cs.client.request(EchoWrite(d=text), timeout_s=10.0)
    assert success(response)
    assert response.r == text


async def test_max_payload_roundtrip(connected_server: ConnectedServer) -> None:
    """A message at the client's advertised `max_unencoded_size` round-trips.

    This guards the `Auto` buf_size math against *overestimating* the payload: if
    `max_unencoded_size` were too large for the server's netbuf, this round-trip
    would fail. It runs even on bursty native_sim fixtures when the message fits in
    a 2-packet burst, so the sub-line-length (buf96) and non-multiple buffers are
    actually exercised end-to-end, not just asserted on.
    """
    cs = connected_server
    if cs.fixture.transport != "serial":
        pytest.skip("serial transport")

    transport = cs.client._transport
    assert isinstance(transport, SMPSerialTransport)
    text = "M" * (transport.max_unencoded_size - len(EchoWrite(d="").BYTES) - 4)
    request = EchoWrite(d=text)
    assert len(request.BYTES) <= transport.max_unencoded_size

    line_packets = len(list(smppacket.encode(request.BYTES, line_length=transport._line_length)))
    limit = cs.fixture.max_reliable_line_packets
    if limit is not None and line_packets > limit:
        pytest.skip(
            f"{cs.fixture.id}: max message spans {line_packets} line packets (> reliable {limit})"
        )

    # Generous timeout: a full multi-fragment round-trip is slow on emulated MCUs under CI load.
    response = await cs.client.request(request, timeout_s=10.0)
    assert success(response)
    assert response.r == text
