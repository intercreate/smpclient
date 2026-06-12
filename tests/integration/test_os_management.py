"""OS-management integration tests: echo and MCUmgr parameters on every fixture."""

from __future__ import annotations

import pytest

from smpclient.generics import success
from smpclient.requests.os_management import EchoWrite, MCUMgrParametersRead
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("text", ["", "a", "Hello, SMP server!"])
async def test_echo_roundtrip(connected_server: ConnectedServer, text: str) -> None:
    response = await connected_server.client.request(EchoWrite(d=text))
    assert success(response)
    assert response.r == text


async def test_mcumgr_parameters(connected_server: ConnectedServer) -> None:
    fixture = connected_server.fixture
    if not fixture.params_supported:
        pytest.skip("MCUmgr params command disabled on this fixture")

    response = await connected_server.client.request(MCUMgrParametersRead())
    assert success(response)
    # The server reports exactly what the vendored manifest claims.
    assert response.buf_size == fixture.buf_size
    assert response.buf_count == fixture.buf_count
