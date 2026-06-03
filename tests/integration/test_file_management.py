"""File-management (fs group) integration tests: upload/download round-trip.

Fixtures with the fs group mount littlefs at `/lfs1`; these exercise smpclient's
`upload_file` / `download_file` against a real filesystem.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import connected, fixture_params
from tests.integration.servers import ServerFixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.has_group("fs")))
async def test_file_upload_download_roundtrip(fixture: ServerFixture) -> None:
    path = "/lfs1/integration.txt"
    line = b"smpclient integration fs round-trip\n"
    # native_sim's PTY UART drops >2-fragment bursts, so keep the payload to a couple
    # of line packets there; emulated targets pace the link and take a larger one.
    payload = line * (2 if fixture.bursty_fragment_drop else 16)

    async with connected(fixture) as cs:
        async for _offset in cs.client.upload_file(payload, path, timeout_s=10.0):
            pass
        downloaded = await cs.client.download_file(path, timeout_s=10.0)

    assert downloaded == payload
