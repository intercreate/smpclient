"""Image-management (DFU) integration tests for fixtures with the img group.

The signed image that pairs with each fixture is uploaded via the img group. To
keep the suite fast under emulation, the larger image proves fragmented *progress*
and the smaller one runs to completion (exercising the final SHA match).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from smpclient.generics import success
from smpclient.requests.image_management import ImageStatesRead
from tests.integration.conftest import connected, fixture_params, upload_image
from tests.integration.servers import ServerFixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_FULL_UPLOAD_LIMIT = 64 * 1024
"""Upload images at or below this size to completion; cap larger ones for speed."""


def _signed_image(fixture: ServerFixture) -> Path:
    """The signed DFU payload paired with this fixture's image."""
    return fixture.path.with_name(re.sub(r"\.(merged\.)?hex$", ".signed.bin", fixture.artifact))


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.has_group("img")))
async def test_image_states_read(fixture: ServerFixture) -> None:
    async with connected(fixture) as cs:
        response = await cs.client.request(ImageStatesRead())
        assert success(response)
        assert len(response.images) >= 1
        assert response.images[0].active is True


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.has_group("img")))
async def test_dfu_upload(fixture: ServerFixture) -> None:
    image = _signed_image(fixture).read_bytes()
    cap = None if len(image) <= _FULL_UPLOAD_LIMIT else 16 * 1024

    async with connected(fixture) as cs:
        offsets = await upload_image(cs.client, image, max_bytes=cap)

        if cap is None:
            assert offsets[-1] == len(image)  # ran to completion (incl. SHA match)
            states = await cs.client.request(ImageStatesRead())
            assert success(states)
            assert any(not im.confirmed for im in states.images)  # landed in a second slot
        else:
            assert (
                offsets[-1] >= cap
            )  # fragmented progress proven; full upload is slow under emulation
