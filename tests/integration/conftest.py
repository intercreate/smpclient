"""Fixtures for the SMP-server integration suite.

`connected_server` is parameterized over every entry in `servers.FIXTURES`: it
launches the server, connects an `SMPClient` over the matching transport, waits
until the server answers, and yields both the client and its `ServerFixture`.
Fixtures that cannot run on this host (wrong OS, missing artifact, no QEMU, or an
unsupported server protocol) are skipped with a reason.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Final, NamedTuple

import pytest
import pytest_asyncio
from _pytest.mark.structures import ParameterSet
from smp.image_management import ImageStatesReadRequest
from smp.os_management import BootMode, EchoWriteRequest, ResetWriteRequest
from typing_extensions import assert_never

from smpclient import SMPClient, success
from smpclient.exceptions import SMPBadSequence
from smpclient.transport.serial import SMPSerialRawTransport, SMPSerialTransport
from smpclient.transport.udp import SMPUDPTransport
from tests.integration.servers import (
    FIXTURES,
    Endpoint,
    PtyEndpoint,
    ServerFixture,
    SocketSerialEndpoint,
    UdpEndpoint,
    serve,
    socket_link,
)

logger = logging.getLogger(__name__)

_READY_PROBE = "smpclient-integration-ready"


FixtureTransport = SMPSerialTransport | SMPSerialRawTransport | SMPUDPTransport


class ConnectedServer(NamedTuple):
    """A live `SMPClient`, its transport, its `ServerFixture`, `Endpoint`, and open link."""

    client: SMPClient
    transport: FixtureTransport
    fixture: ServerFixture
    endpoint: Endpoint
    link: AsyncExitStack


def fixture_params(
    predicate: Callable[[ServerFixture], bool] = lambda _: True,
) -> list[ParameterSet]:
    """Build skip-marked pytest params for the fixtures matching `predicate`."""
    return [
        pytest.param(
            fixture,
            id=fixture.id,
            marks=[pytest.mark.skip(reason=reason)]
            if (reason := fixture.unavailable_reason())
            else [],
        )
        for fixture in FIXTURES
        if predicate(fixture)
    ]


def _link(
    fixture: ServerFixture, endpoint: Endpoint
) -> AbstractAsyncContextManager[FixtureTransport]:
    match endpoint:
        case PtyEndpoint(pty):
            match fixture.transport:
                case "serial" | "shell":
                    return SMPSerialTransport(pty).connected()
                case "serial_raw":
                    return SMPSerialRawTransport(pty).connected()
                case "udp":
                    pytest.fail("UDP fixtures do not present as a PTY serial endpoint")
                case _ as unreachable:
                    assert_never(unreachable)
        case SocketSerialEndpoint(url):
            match fixture.transport:
                case "serial" | "shell":
                    return socket_link(SMPSerialTransport(url), url)
                case "serial_raw":
                    return socket_link(SMPSerialRawTransport(url), url)
                case "udp":
                    pytest.fail("UDP fixtures do not present as a socket serial endpoint")
                case _ as unreachable:
                    assert_never(unreachable)
        case UdpEndpoint(host, port):
            return SMPUDPTransport(host, port).connected()
        case _:
            assert_never(endpoint)


async def _poll_until_answering(
    client: SMPClient,
    probe: Callable[[SMPClient], Awaitable[bool]],
    *,
    attempts: int = 30,
    interval_s: float = 0.1,
) -> bool:
    """Retry `probe(client)` until it returns `True`, tolerating boot-time stalls.

    A server still booting answers with a timeout or an out-of-sequence frame; both are
    swallowed between attempts. Returns whether the probe succeeded within `attempts`.

    Args:
        client: the connected client to probe.
        probe: an awaitable that issues one request and reports whether it answered.
        attempts: how many times to probe before giving up.
        interval_s: how long to wait after a stalled attempt.

    Returns:
        `True` once the probe answered, `False` if it never did.
    """
    for _ in range(attempts):
        try:
            if await probe(client):
                return True
        except (TimeoutError, SMPBadSequence):
            await asyncio.sleep(interval_s)
    return False


async def _wait_until_answering(client: SMPClient, *, attempts: int = 30) -> None:
    """Round-trip an echo until the server answers, tolerating boot-time stalls."""

    async def echoes(c: SMPClient) -> bool:
        response = await c.request(EchoWriteRequest(d=_READY_PROBE), timeout_s=1.0)
        return success(response) and response.r == _READY_PROBE

    if not await _poll_until_answering(client, echoes, attempts=attempts):
        raise TimeoutError("the SMP server never answered an echo")


def signed_image(fixture: ServerFixture) -> Path:
    """The signed image paired with `fixture` -- its `.hex` artifact's sibling `.signed.bin`.

    This is the DFU payload an img-group server accepts, and what an MCUboot serial-recovery
    server reassembles.

    Args:
        fixture: the fixture whose paired signed image to locate.

    Returns:
        The path to the fixture's signed image.
    """
    return fixture.path.with_name(re.sub(r"\.(merged\.)?hex$", ".signed.bin", fixture.artifact))


async def upload_image(
    client: SMPClient,
    image: bytes,
    *,
    max_bytes: int | None = None,
    subsequent_timeout_s: float = 5.0,
) -> list[int]:
    """Upload `image` via the img group, asserting monotonic offsets.

    Runs to completion when `max_bytes` is `None`; otherwise stops once the offset
    reaches `max_bytes` (to prove fragmented progress without a slow full upload
    under emulation).

    Args:
        client: a connected `SMPClient`.
        image: the signed image bytes to upload.
        max_bytes: stop early once the offset reaches this many bytes.
        subsequent_timeout_s: per-chunk response timeout after the first chunk. Raise it
            for slower servers (MCUboot recovery erases flash as it writes).

    Returns:
        The offsets yielded by the upload, in order.
    """
    offsets: list[int] = []
    async for offset in client.upload(
        image, first_timeout_s=30.0, subsequent_timeout_s=subsequent_timeout_s
    ):
        assert offset >= (offsets[-1] if offsets else 0), "offsets must be non-decreasing"
        offsets.append(offset)
        if max_bytes is not None and offset >= min(max_bytes, len(image)):
            break
    assert offsets, "upload yielded no offsets"
    return offsets


def assert_chunks_maximized(
    offsets: list[int], max_unencoded_size: int, *, overhead_budget: int = 48
) -> None:
    """Assert an upload packed near-maximal chunks — i.e. it maximizes link throughput.

    Each offset stride is the payload one request carried; the largest is a full
    (non-final) request. It must come within `overhead_budget` bytes of the
    transport's `max_unencoded_size` (the slack is the SMP header + CBOR framing),
    proving smpclient fills each packet to the buffer rather than under-fragmenting.

    Args:
        offsets: the cumulative offsets yielded by an upload (see `upload_image`).
        max_unencoded_size: the transport's advertised max unencoded message size.
        overhead_budget: the largest slack below `max_unencoded_size` accepted as
            "maximized" (SMP header + CBOR keys; larger for requests that repeat a
            file path).
    """
    strides = [b - a for a, b in zip([0, *offsets], offsets)]
    assert len(strides) >= 2, "need >= 2 requests to measure a full (non-final) chunk"
    biggest = max(strides)
    assert biggest <= max_unencoded_size, f"chunk {biggest}B exceeds buffer {max_unencoded_size}B"
    assert biggest >= max_unencoded_size - overhead_budget, (
        f"largest chunk {biggest}B under-fills the {max_unencoded_size}B buffer by "
        f">{overhead_budget}B — link throughput not maximized"
    )


RECOVERY_UPLOAD_TIMEOUT_S = 15.0
"""Per-chunk upload timeout for MCUboot recovery, which erases flash as it writes -- more
generous than the app-mode default to absorb erase latency under emulation and host load."""


@asynccontextmanager
async def reboot_into_recovery(
    app: ConnectedServer,
    recovery: AbstractAsyncContextManager[SMPSerialTransport | SMPSerialRawTransport],
) -> AsyncIterator[SMPClient]:
    """Reboot the device into MCUboot serial recovery and yield a recovery-connected client.

    The app at `app` reboots via `os reset boot_mode=BOOTLOADER` (smp 4.1.0) and its link
    closes; the `recovery` link then opens to the bootloader on the same serial endpoint,
    probed until it answers (the recovery server speaks the img group, not echo).
    """
    app_client: Final = app.client
    assert success(await app_client.request(ImageStatesReadRequest()))
    try:
        assert success(
            await app_client.request(
                ResetWriteRequest(boot_mode=BootMode.BOOTLOADER), timeout_s=3.0
            )
        )
    except TimeoutError:
        pass  # some servers reset before sending the response
    await app.link.aclose()
    await asyncio.sleep(2.0)  # let MCUboot serial recovery come up

    async def lists_images(c: SMPClient) -> bool:
        return success(await c.request(ImageStatesReadRequest(), timeout_s=1.0))

    async with recovery as transport:
        bootloader = SMPClient(transport)
        if not await _poll_until_answering(bootloader, lists_images, interval_s=0.2):
            pytest.fail("MCUboot serial recovery SMP server never answered")
        yield bootloader


@asynccontextmanager
async def connected(fixture: ServerFixture) -> AsyncIterator[ConnectedServer]:
    """Launch `fixture`, connect an `SMPClient`, and wait until the server answers."""
    async with serve(fixture) as endpoint, AsyncExitStack() as link:
        # Tolerant on exit: `connected()` closes best-effort, and a recovery test may have
        # rebooted the server out from under us.
        transport = await link.enter_async_context(_link(fixture, endpoint))
        client = SMPClient(transport)
        await _wait_until_answering(client)
        # Re-negotiate in case the first MCUMgr parameter read raced server boot.
        await transport.negotiate()
        yield ConnectedServer(client, transport, fixture, endpoint, link)


@pytest_asyncio.fixture(params=fixture_params())
async def connected_server(request: pytest.FixtureRequest) -> AsyncIterator[ConnectedServer]:
    async with connected(request.param) as server:
        yield server
