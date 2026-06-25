"""Reset-into-bootloader (MCUboot serial recovery) integration test.

A fully-featured app reboots into MCUboot serial recovery via `os reset
boot_mode=BOOTLOADER` (smp 4.1.0, PR #113), and the bootloader's SMP server accepts a
fragmented image upload -- the path a client uses to recover a device that can't be
updated from the app.

Recovery advertises MCUmgr params (`CONFIG_BOOT_MGMT_MCUMGR_PARAMS`,
mcu-tools/mcuboot#2746), so the client negotiates the decoded reassembly buffer (`Auto`)
rather than being told it out of band. An explicit `BufferSize` that caps below the
advertised buffer covers the override path a client uses when it opts out of negotiation.
"""

from __future__ import annotations

import pytest
from smp import packet as smppacket
from typing_extensions import assert_never

from smpclient.generics import success
from smpclient.requests.os_management import MCUMgrParametersRead
from smpclient.transport.serial import Auto, BufferSize
from smpclient.transport.serial.encoded import _FRAME_OVERHEAD
from tests.integration.conftest import (
    RECOVERY_UPLOAD_TIMEOUT_S,
    assert_chunks_maximized,
    connected,
    fixture_params,
    reboot_into_recovery,
    signed_image,
    upload_image,
)
from tests.integration.servers import QemuSocketSerialTransport, ServerFixture, SocketSerialEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_RecoveryBuffer = Auto | BufferSize
"""How the client sizes recovery uploads: negotiate from params, or cap explicitly."""

_BUFFERS = [
    pytest.param(Auto(), id="negotiated"),
    pytest.param(BufferSize(buf_size=256), id="buffersize-256"),
]


# Only the canonical recovery image: the `serial_recovery_buf<N>` matrix varies the app's
# netbuf but drops into the same MCUboot recovery server, so its buffers are exercised in
# app mode (echo/fs/img buffer-fill tests), not here.
@pytest.mark.parametrize("buffer", _BUFFERS)
@pytest.mark.parametrize("fixture", fixture_params(lambda f: f.config == "serial_recovery"))
async def test_upload_to_mcuboot_recovery_smp_server(
    fixture: ServerFixture, buffer: _RecoveryBuffer
) -> None:
    advertised = fixture.recovery_buf_size
    assert advertised is not None, "serial_recovery fixture must advertise recovery params"

    async with connected(fixture) as cs:
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        transport = QemuSocketSerialTransport(cs.endpoint.url, fragmentation_strategy=buffer)

        async with reboot_into_recovery(cs.client, transport, cs.endpoint.url) as bootloader:
            await bootloader._initialize()  # negotiate buf_size (a no-op for explicit BufferSize)

            params = await bootloader.request(MCUMgrParametersRead(), timeout_s=2.0)
            assert success(params)
            assert (params.buf_count, params.buf_size) == (1, advertised)

            match buffer:
                case Auto():
                    target = advertised
                case BufferSize(buf_size=override):
                    target = override
                case _ as unreachable:
                    assert_never(unreachable)

            # The decoded payload fills the buffer minus the 2-byte frame length + 2-byte
            # CRC16 that share it; base64 + 128-byte line framing then expands it ~1.37x on
            # the wire (the negotiated 1024 B buffer -> ~1400 B per message), which the
            # server decodes incrementally back into the buffer.
            assert transport.max_unencoded_size == target - _FRAME_OVERHEAD
            wire = sum(
                len(packet)
                for packet in smppacket.encode(
                    bytes(transport.max_unencoded_size), transport._line_length
                )
            )
            assert target < wire < 2 * target

            offsets = await upload_image(
                bootloader,
                signed_image(fixture).read_bytes(),
                max_bytes=4096,
                subsequent_timeout_s=RECOVERY_UPLOAD_TIMEOUT_S,
            )
            assert offsets[-1] >= 4096  # the bootloader reassembles the fragmented upload
            assert_chunks_maximized(offsets, transport.max_unencoded_size)
