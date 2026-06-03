"""Reset-into-bootloader (MCUboot serial recovery) integration test.

Exercises the smp 4.1.0 `boot_mode` field (PR #113): a fully-featured app reboots
into MCUboot serial recovery via `os reset boot_mode=BOOTLOADER`, and the
bootloader's SMP server then accepts a fragmented image upload. This is the path a
client uses to recover a device that can't otherwise be updated from the app.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from smp.os_management import BootMode

from smpclient import SMPClient
from smpclient.exceptions import SMPBadSequence
from smpclient.generics import success
from smpclient.requests.image_management import ImageStatesRead
from smpclient.requests.os_management import ResetWrite
from tests.integration.conftest import connected, fixture_params, upload_image
from tests.integration.servers import QemuSocketSerialTransport, ServerFixture, SocketSerialEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _signed_image(fixture: ServerFixture) -> Path:
    return fixture.path.with_name(re.sub(r"\.(merged\.)?hex$", ".signed.bin", fixture.artifact))


@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.serial_recovery))
async def test_reset_into_recovery_then_upload(fixture: ServerFixture) -> None:
    signed = _signed_image(fixture).read_bytes()

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

        # Reconnect to the bootloader's SMP server on the same serial socket.
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        bootloader = SMPClient(QemuSocketSerialTransport(cs.endpoint.url), cs.endpoint.url)
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
                pytest.fail("MCUboot serial recovery SMP server never answered")

            offsets = await upload_image(bootloader, signed, max_bytes=8192)
            assert offsets[-1] >= 8192  # the bootloader accepts a fragmented upload
        finally:
            await bootloader.disconnect()
