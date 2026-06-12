"""File-management (fs group) integration tests: upload/download round-trip.

Fixtures with the fs group mount littlefs at `/lfs1`; these exercise smpclient's
`upload_file` / `download_file` against a real filesystem.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import assert_chunks_maximized, connected, fixture_params
from tests.integration.servers import ServerFixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fs_capable(fixture: ServerFixture) -> bool:
    """Fixtures whose filesystem reliably round-trips a buffer-filling file.

    The do-it-all MCUboot recovery image mounts littlefs in a RAM-backed flash
    simulator that shares SRAM with its image slots; on the large-netbuf variants
    (buf1024/buf2048) read-back of a sizeable file is unreliable (the client uploads
    fine — the server's fs flakes). Those buffer sizes keep buffer-fill / maximization
    coverage via DFU (flash slot) and the max-payload echo instead.
    """
    return fixture.has_group("fs") and not (
        fixture.serial_recovery and (fixture.buf_size or 0) > 512
    )


@pytest.mark.parametrize("fixture", fixture_params(_fs_capable))
async def test_file_upload_download_roundtrip(fixture: ServerFixture) -> None:
    path = "/lfs1/integration.txt"

    async with connected(fixture) as cs:
        transport = cs.client._transport
        # On a target that paces the link (mps2 buffer matrix, UDP), size the payload to
        # span more than one full buffer-filling request, so the calculated max
        # transaction size is exercised end-to-end; native_sim's PTY UART drops
        # >2-fragment bursts, so keep it to a couple of line packets there.
        if fixture.bursty_fragment_drop:
            payload = b"smpclient integration fs round-trip\n" * 2
        else:
            payload = b"\x5a" * (2 * transport.max_unencoded_size + 1)

        offsets = [off async for off in cs.client.upload_file(payload, path, timeout_s=10.0)]
        downloaded = await cs.client.download_file(path, timeout_s=10.0)

    assert downloaded == payload
    # On a paced target, confirm each request filled the buffer (best throughput). The
    # repeated file path costs more CBOR overhead than an image upload, hence the budget.
    if not fixture.bursty_fragment_drop:
        assert_chunks_maximized(offsets, transport.max_unencoded_size, overhead_budget=80)
