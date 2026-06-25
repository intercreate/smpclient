"""Reset-into-bootloader (MCUboot serial recovery) integration test, over every framing.

A fully-featured app reboots into MCUboot serial recovery via `os reset
boot_mode=BOOTLOADER` (smp 4.1.0), and the bootloader's SMP server reassembles a
fragmented image upload -- the path a client uses to recover a device it can't update
from the app.  Recovery advertises MCUmgr params (mcu-tools/mcuboot#2746), so the client
negotiates the cap rather than being told it out of band.

One parameterized test covers every recovery transport permutation as a sum type:

- `Console` -- SMP-over-console (base64 + length + CRC16 + line framing), negotiated via
  `Auto` and, as the opt-out path, an explicit `BufferSize` capped below the buffer.  The
  cap is `buf_size - 4` and the message expands ~1.37x on the wire.
- `Raw` -- the unframed `[header][payload]` (`BOOT_SERIAL_RAW_PROTOCOL`, mcu-tools/mcuboot#2755).
- `RawCobs` -- raw + COBS+CRC16 framing (`BOOT_SERIAL_RAW_PROTOCOL_COBS`, intercreate/mcuboot#5).

Both raw framings carry the whole SMP message into the server's decode buffer, so the cap
is the advertised `buf_size` itself.  A full recovery upload stalls intermittently under
emulation (flash-erase latency), so each leg proves fragmented *progress* and then reads
image state to confirm the recovery img group is still coherent; byte-level upload
verification lives in `test_image_management.py`.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from _pytest.mark.structures import ParameterSet
from smp import packet as smppacket
from typing_extensions import assert_never

from smpclient.generics import success
from smpclient.requests.image_management import ImageStatesRead
from smpclient.requests.os_management import MCUMgrParametersRead
from smpclient.transport.serial import Auto, BufferSize, Cobs, SMPSerialTransport
from smpclient.transport.serial.encoded import _FRAME_OVERHEAD
from tests.integration.conftest import (
    RECOVERY_UPLOAD_TIMEOUT_S,
    assert_chunks_maximized,
    connected,
    reboot_into_recovery,
    signed_image,
    upload_image,
)
from tests.integration.servers import (
    FIXTURES,
    QemuSocketSerialRawTransport,
    QemuSocketSerialTransport,
    ServerFixture,
    SocketSerialEndpoint,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SocketTransport = QemuSocketSerialTransport | QemuSocketSerialRawTransport


class Console(NamedTuple):
    """SMP-over-console framing, sized by a fragmentation strategy."""

    strategy: Auto | BufferSize


class Raw(NamedTuple):
    """Unframed raw `[header][payload]`."""


class RawCobs(NamedTuple):
    """Raw framing wrapped in COBS+CRC16."""


_Recovery = Console | Raw | RawCobs

_VARIANTS: list[_Recovery] = [
    Console(Auto()),
    Console(BufferSize(buf_size=256)),
    Raw(),
    RawCobs(),
]


def _fixture(variant: _Recovery) -> tuple[str, str]:
    """The fixture `config` and a test-id `label` for `variant`."""
    match variant:
        case Console(strategy=strategy):
            match strategy:
                case Auto():
                    return "serial_recovery", "console-negotiated"
                case BufferSize():
                    return "serial_recovery", "console-buffersize-256"
                case _ as unreachable:
                    assert_never(unreachable)
        case Raw():
            return "serial_recovery_raw", "raw"
        case RawCobs():
            return "serial_recovery_raw_cobs", "raw-cobs"
        case _ as unreachable:
            assert_never(unreachable)


def _build_transport(variant: _Recovery, url: str) -> _SocketTransport:
    match variant:
        case Console(strategy=strategy):
            return QemuSocketSerialTransport(url, fragmentation_strategy=strategy)
        case Raw():
            return QemuSocketSerialRawTransport(url)
        case RawCobs():
            return QemuSocketSerialRawTransport(url, framing=Cobs())
        case _ as unreachable:
            assert_never(unreachable)


def _assert_negotiated_cap(variant: _Recovery, transport: _SocketTransport, advertised: int) -> int:
    """Assert the negotiated message cap and the on-wire size for `variant`; return the cap."""
    match variant:
        case Console(strategy=strategy):
            assert isinstance(transport, SMPSerialTransport)
            match strategy:
                case Auto():
                    cap = advertised - _FRAME_OVERHEAD
                case BufferSize(buf_size=buf_size):
                    cap = buf_size - _FRAME_OVERHEAD
                case _ as unreachable:
                    assert_never(unreachable)
            assert transport.max_unencoded_size == cap
            wire = sum(len(p) for p in smppacket.encode(bytes(cap), transport._line_length))
            assert cap < wire < 2 * cap  # base64 + line framing expands the message ~1.37x
            return cap
        case Raw():
            assert transport.max_unencoded_size == advertised  # the message is the wire
            return advertised
        case RawCobs():
            assert transport.max_unencoded_size == advertised
            (frame,) = Cobs().encode(bytes(advertised))
            assert (
                advertised < len(frame) <= advertised + advertised // 254 + 4
            )  # COBS + CRC + 0x00
            return advertised
        case _ as unreachable:
            assert_never(unreachable)


def _recovery_params() -> list[ParameterSet]:
    """One skip-aware param per permutation, pairing each variant with its fixture."""
    by_config = {fixture.config: fixture for fixture in FIXTURES}
    params: list[ParameterSet] = []
    for variant in _VARIANTS:
        config, label = _fixture(variant)
        fixture = by_config.get(config)
        if fixture is None:
            params.append(
                pytest.param(
                    variant, None, id=label, marks=pytest.mark.skip(reason=f"{config} not vendored")
                )
            )
            continue
        reason = fixture.unavailable_reason()
        marks = [pytest.mark.skip(reason=reason)] if reason else []
        params.append(pytest.param(variant, fixture, id=f"{fixture.id}-{label}", marks=marks))
    return params


@pytest.mark.parametrize("variant, fixture", _recovery_params())
async def test_upload_to_mcuboot_recovery(variant: _Recovery, fixture: ServerFixture) -> None:
    advertised = fixture.recovery_buf_size
    assert advertised is not None, "recovery fixture must advertise MCUmgr params"

    async with connected(fixture) as cs:
        assert isinstance(cs.endpoint, SocketSerialEndpoint)
        transport = _build_transport(variant, cs.endpoint.url)

        async with reboot_into_recovery(cs.client, transport, cs.endpoint.url) as bootloader:
            await bootloader._initialize()  # negotiate buf_size (a no-op for explicit BufferSize)

            params = await bootloader.request(MCUMgrParametersRead(), timeout_s=2.0)
            assert success(params)
            assert (params.buf_count, params.buf_size) == (1, advertised)

            cap = _assert_negotiated_cap(variant, transport, advertised)

            offsets = await upload_image(
                bootloader,
                signed_image(fixture).read_bytes(),
                max_bytes=4096,
                subsequent_timeout_s=RECOVERY_UPLOAD_TIMEOUT_S,
            )
            assert offsets[-1] >= 4096  # the bootloader reassembles the fragmented upload
            assert_chunks_maximized(offsets, cap)

            states = await bootloader.request(ImageStatesRead(), timeout_s=5.0)
            assert success(states)
            assert len(states.images) >= 1  # the recovery img group is coherent after the upload
