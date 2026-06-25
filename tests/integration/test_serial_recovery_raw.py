"""Reset-into-bootloader MCUboot *raw* serial-recovery integration test.

The raw-framing analog of `test_serial_recovery.py`: the app reboots into MCUboot serial
recovery and the bootloader's SMP server -- built with the raw (non-console) protocol
(`BOOT_SERIAL_RAW_PROTOCOL`, mcu-tools/mcuboot#2755) -- reassembles a fragmented upload
sent over `SMPSerialRawTransport`.

Recovery advertises MCUmgr params (mcu-tools/mcuboot#2746), so the client negotiates the
cap. Raw framing carries the whole `[header][payload]` with no base64/length/CRC, so the
negotiated cap is the advertised `buf_size` itself (no `- 4`): ~1024 B per message on the
wire, versus the encoded transport's ~1400 B.
"""

from __future__ import annotations

import pytest

from smpclient.generics import success
from smpclient.requests.os_management import MCUMgrParametersRead
from tests.integration.conftest import (
    RECOVERY_UPLOAD_TIMEOUT_S,
    assert_chunks_maximized,
    connected,
    fixture_params,
    reboot_into_recovery,
    signed_image,
    upload_image,
)
from tests.integration.servers import (
    QemuSocketSerialRawTransport,
    ServerFixture,
    SocketSerialEndpoint,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.config == "serial_recovery_raw"))
async def test_upload_to_mcuboot_raw_recovery_smp_server(fixture: ServerFixture) -> None:
    advertised = fixture.recovery_buf_size
    assert advertised is not None, "serial_recovery_raw fixture must advertise recovery params"

    async with connected(fixture) as cs:
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        transport = QemuSocketSerialRawTransport(cs.endpoint.url)

        async with reboot_into_recovery(cs.client, transport, cs.endpoint.url) as bootloader:
            params = await bootloader.request(MCUMgrParametersRead(), timeout_s=2.0)
            assert success(params)
            assert (params.buf_count, params.buf_size) == (1, advertised)

            await bootloader._initialize()  # adopt the advertised buf_size as the message cap
            assert transport.max_unencoded_size == advertised

            offsets = await upload_image(
                bootloader,
                signed_image(fixture).read_bytes(),
                max_bytes=4096,
                subsequent_timeout_s=RECOVERY_UPLOAD_TIMEOUT_S,
            )
            assert offsets[-1] >= 4096  # the bootloader reassembles the fragmented upload
            assert_chunks_maximized(offsets, transport.max_unencoded_size)
