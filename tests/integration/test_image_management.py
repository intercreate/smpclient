"""Image-management (DFU) integration tests for fixtures with the img group.

The signed image that pairs with each fixture is uploaded via the img group. To
keep the suite fast under emulation, the larger image proves fragmented *progress*
and the smaller one runs to completion (exercising the final SHA match).
"""

from __future__ import annotations

import pytest
from smp import packet as smppacket

from smpclient.generics import success
from smpclient.requests.image_management import ImageStatesRead
from smpclient.transport.serial import SMPSerialRawTransport, SMPSerialTransport
from tests.integration.conftest import (
    assert_chunks_maximized,
    connected,
    fixture_params,
    signed_image,
    upload_image,
)
from tests.integration.servers import ServerFixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_FULL_UPLOAD_LIMIT = 64 * 1024
"""Upload images at or below this size to completion; cap larger ones for speed."""


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.has_group("img")))
async def test_image_states_read(fixture: ServerFixture) -> None:
    async with connected(fixture) as cs:
        response = await cs.client.request(ImageStatesRead())
        assert success(response)
        assert len(response.images) >= 1
        assert response.images[0].active is True


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.has_group("img")))
async def test_dfu_upload(fixture: ServerFixture) -> None:
    image = signed_image(fixture).read_bytes()
    cap = None if len(image) <= _FULL_UPLOAD_LIMIT else 16 * 1024

    async with connected(fixture) as cs:
        transport = cs.client._transport
        assert isinstance(transport, (SMPSerialTransport, SMPSerialRawTransport))
        # The line-packet burst limit is an encoded-transport concern; raw writes each
        # message in one go.
        if isinstance(transport, SMPSerialTransport):
            chunk_packets = len(
                list(
                    smppacket.encode(b"\x00" * transport.max_unencoded_size, transport._line_length)
                )
            )
            limit = fixture.max_reliable_line_packets
            if limit is not None and chunk_packets > limit:
                pytest.skip(
                    f"{fixture.id}: DFU chunk spans {chunk_packets} line packets "
                    f"(> reliable {limit}); full-buffer DFU covered on mps2"
                )

        offsets = await upload_image(cs.client, image, max_bytes=cap)

        # Every DFU chunk must fill the buffer: chunk size scales with buf_size, so a
        # larger netbuf means proportionally fewer, larger requests (best throughput).
        assert_chunks_maximized(offsets, transport.max_unencoded_size)

        if cap is None:
            assert offsets[-1] == len(image)  # ran to completion (incl. SHA match)
            states = await cs.client.request(ImageStatesRead())
            assert success(states)
            assert any(not im.confirmed for im in states.images)  # landed in a second slot
        else:
            assert (
                offsets[-1] >= cap
            )  # fragmented progress proven; full upload is slow under emulation
