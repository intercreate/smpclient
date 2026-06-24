"""Reset-into-bootloader MCUboot *raw* serial-recovery integration test.

The raw-framing analog of `test_serial_recovery.py`: a fully-featured app reboots into
MCUboot serial recovery via `os reset boot_mode=BOOTLOADER`, and the bootloader's SMP
server -- built with MCUboot's raw (non-console) recovery protocol
(`BOOT_SERIAL_RAW_PROTOCOL`, mcu-tools/mcuboot#2755) -- reassembles a fragmented image
upload sent over `SMPSerialRawTransport`.

The fixture is emulated (`mps2_an385`, reached over a TCP socket chardev), so it connects
with the socket-backed `QemuSocketSerialRawTransport` rather than the PTY-backed raw
transport.

Raw recovery carries the whole `[header][payload]` SMP message in its receive buffer with
no base64/length/CRC framing, so the transport's `mtu` *is* the per-message cap (the
recovery server does not advertise MCUmgr params, so `max_unencoded_size` falls back to
`mtu`). We upload at two `mtu`s to exercise the recovery server's reassembly across very
different transaction sizes:

- `mtu-384` (the raw transport default): ~360 B payloads.
- `mtu-1024` (fills MCUboot's recovery receive buffer): ~1000 B payloads.
"""

from __future__ import annotations

import asyncio

import pytest
from smp.os_management import BootMode

from smpclient import SMPClient
from smpclient.exceptions import SMPBadSequence
from smpclient.generics import success
from smpclient.requests.image_management import ImageStatesRead
from smpclient.requests.os_management import ResetWrite
from tests.integration.conftest import (
    assert_chunks_maximized,
    connected,
    fixture_params,
    signed_image,
    upload_image,
)
from tests.integration.servers import (
    QemuSocketSerialRawTransport,
    ServerFixture,
    SocketSerialEndpoint,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_MTU_CONFIGS = [
    pytest.param(384, id="mtu-384"),
    pytest.param(1024, id="mtu-1024"),
]


@pytest.mark.parametrize("mtu", _MTU_CONFIGS)
@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.config == "serial_recovery_raw"))
async def test_upload_to_mcuboot_raw_recovery_smp_server(
    fixture: ServerFixture,
    mtu: int,
) -> None:
    signed = signed_image(fixture).read_bytes()

    async with connected(fixture) as cs:
        # The app serves the img group before we drop into recovery.
        assert success(await cs.client.request(ImageStatesRead()))

        # Reboot into MCUboot serial recovery (smp 4.1.0 boot_mode).
        try:
            reset = await cs.client.request(
                ResetWrite(boot_mode=BootMode.BOOTLOADER), timeout_s=3.0
            )
            assert success(reset)
        except TimeoutError:
            pass  # some servers reset before sending the response
        await cs.client.disconnect()
        await asyncio.sleep(2.0)  # let MCUboot serial recovery come up

        # Reconnect to the bootloader's raw SMP server on the same serial socket, at the mtu
        # under test (recovery does not advertise MCUmgr params, so mtu is the message cap).
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        transport = QemuSocketSerialRawTransport(cs.endpoint.url, mtu=mtu)
        bootloader = SMPClient(transport, cs.endpoint.url)
        await bootloader.connect()
        try:
            # The recovery SMP server speaks img (not echo), so probe with image-list.
            for _ in range(30):
                try:
                    state = await bootloader.request(ImageStatesRead(), timeout_s=1.0)
                    if success(state):
                        break
                except (TimeoutError, SMPBadSequence):
                    await asyncio.sleep(0.2)
            else:
                pytest.fail("MCUboot raw serial recovery SMP server never answered")

            offsets = await upload_image(bootloader, signed, max_bytes=4096)
            assert offsets[-1] >= 4096  # the bootloader reassembles the fragmented upload
            # Each request fills the configured mtu: mtu-1024 moves ~3x the payload of the
            # 384 B default (the point of naming the larger buffer).
            assert_chunks_maximized(offsets, transport.max_unencoded_size)
        finally:
            await bootloader.disconnect()
