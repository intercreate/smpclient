"""Enumeration-management integration smoke tests across every transport."""

from __future__ import annotations

import pytest
from smp import header as smphdr
from smp.enumeration_management import GroupCountRequest, ListOfGroupsRequest

from smpclient.generics import success
from tests.integration.conftest import ConnectedServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_count_supported_groups(connected_server: ConnectedServer) -> None:
    response = await connected_server.client.request(GroupCountRequest())
    assert success(response)
    assert response.count > 0


async def test_os_group_is_supported(connected_server: ConnectedServer) -> None:
    response = await connected_server.client.request(ListOfGroupsRequest())
    assert success(response)
    assert smphdr.GroupId.OS_MANAGEMENT in response.groups
