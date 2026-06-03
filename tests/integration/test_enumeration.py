"""Enumeration-management integration smoke tests across every transport."""

from __future__ import annotations

import pytest
from smp import header as smphdr

from smpclient.generics import success
from smpclient.requests.enumeration_management import CountSupportedGroups, ListSupportedGroups
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_count_supported_groups(connected_server: ConnectedServer) -> None:
    response = await connected_server.client.request(CountSupportedGroups())
    assert success(response)
    assert response.count > 0


async def test_os_group_is_supported(connected_server: ConnectedServer) -> None:
    response = await connected_server.client.request(ListSupportedGroups())
    assert success(response)
    assert smphdr.GroupId.OS_MANAGEMENT in response.groups
