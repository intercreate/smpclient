"""UDP transport integration tests (IPv4 and IPv6)."""

from __future__ import annotations

import pytest

from smpclient.generics import success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport.udp import SMPUDPTransport
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_udp_ip_family(connected_server: ConnectedServer) -> None:
    """The UDP transport detects the server's IP family and still round-trips."""
    cs = connected_server
    if cs.fixture.transport != "udp":
        pytest.skip("UDP transport only")

    transport = cs.client._transport
    assert isinstance(transport, SMPUDPTransport)
    assert transport._is_ipv6 == (cs.fixture.ip_family == "ipv6")

    response = await cs.client.request(EchoWrite(d="udp family"))
    assert success(response)
    assert response.r == "udp family"
