"""Reset-into-bootloader (MCUboot serial recovery) integration test.

Exercises the smp 4.1.0 `boot_mode` field (PR #113): a fully-featured app reboots
into MCUboot serial recovery via `os reset boot_mode=BOOTLOADER`, and the
bootloader's SMP server then accepts a fragmented image upload. This is the path a
client uses to recover a device that can't otherwise be updated from the app.

The recovery server does not advertise MCUmgr params, so the client must be told
its buffer size. We upload at two configurations to exercise the recovery server's
reassembly across very different transaction sizes:

- `auto-default` (no params -> conservative Auto): ~83 B payloads, one line packet.
- `buffersize-1024` (MCUboot's decoded recovery buffer, BOOT_SERIAL_MAX_RECEIVE_SIZE
  default 1024): ~1020 B payloads filling the buffer, ~9 line packets each.
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
from smpclient.transport.serial import SMPSerialTransport
from tests.integration.conftest import (
    assert_chunks_maximized,
    connected,
    fixture_params,
    upload_image,
)
from tests.integration.servers import QemuSocketSerialTransport, ServerFixture, SocketSerialEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_BUFFER_CONFIGS = [
    pytest.param(None, id="auto-default"),
    pytest.param(SMPSerialTransport.BufferSize(buf_size=1024), id="buffersize-1024"),
]


def _signed_image(fixture: ServerFixture) -> Path:
    return fixture.path.with_name(re.sub(r"\.(merged\.)?hex$", ".signed.bin", fixture.artifact))


# Only the canonical recovery image: the `serial_recovery_buf<N>` matrix varies the
# *app's* netbuf, but they all drop into the same MCUboot recovery server (same line
# buffers), so re-testing recovery on each adds nothing. Their buffer sizes are
# exercised in app mode (echo/fs/img buffer-fill tests).
@pytest.mark.parametrize("fragmentation", _BUFFER_CONFIGS)
@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.config == "serial_recovery"))
async def test_upload_to_mcuboot_recovery_smp_server(
    fixture: ServerFixture,
    fragmentation: SMPSerialTransport.BufferSize | None,
) -> None:
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

        # Reconnect to the bootloader's SMP server on the same serial socket, at the
        # buffer geometry under test (recovery does not advertise MCUmgr params).
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        transport = QemuSocketSerialTransport(cs.endpoint.url, fragmentation_strategy=fragmentation)
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
                pytest.fail("MCUboot serial recovery SMP server never answered")

            offsets = await upload_image(bootloader, signed, max_bytes=4096)
            assert offsets[-1] >= 4096  # the bootloader reassembles the fragmented upload
            # Each request fills the configured buffer: buffersize-1024 moves ~12x the
            # payload of the conservative default (the point of naming the larger buffer).
            assert_chunks_maximized(offsets, transport.max_unencoded_size)
        finally:
            await bootloader.disconnect()
