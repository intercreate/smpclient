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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple

import pytest
import pytest_asyncio
from _pytest.mark.structures import ParameterSet
from typing_extensions import assert_never

from smpclient import SMPClient
from smpclient.exceptions import SMPBadSequence
from smpclient.generics import success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport import SMPTransport
from smpclient.transport.serial import SMPSerialRawTransport, SMPSerialTransport
from smpclient.transport.udp import SMPUDPTransport
from tests.integration.servers import (
    FIXTURES,
    Endpoint,
    PtyEndpoint,
    QemuSocketSerialRawTransport,
    QemuSocketSerialTransport,
    ServerFixture,
    SocketSerialEndpoint,
    UdpEndpoint,
    serve,
)

logger = logging.getLogger(__name__)

_READY_PROBE = "smpclient-integration-ready"


class ConnectedServer(NamedTuple):
    """A live `SMPClient`, the `ServerFixture` it is connected to, and its `Endpoint`."""

    client: SMPClient
    fixture: ServerFixture
    endpoint: Endpoint


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


_SMP_UDP_DEFAULT_PORT = 1337
"""`SMPUDPTransport.connect`'s default port; `SMPClient.connect` cannot override it."""


def _build_transport(fixture: ServerFixture, endpoint: Endpoint) -> tuple[SMPTransport, str]:
    match endpoint:
        case PtyEndpoint(pty):
            match fixture.transport:
                case "serial" | "shell":
                    return SMPSerialTransport(), pty
                case "serial_raw":
                    return SMPSerialRawTransport(), pty
                case "udp":
                    pytest.fail("UDP fixtures do not present as a PTY serial endpoint")
                case _ as unreachable:
                    assert_never(unreachable)
        case SocketSerialEndpoint(url):
            match fixture.transport:
                case "serial" | "shell":
                    return QemuSocketSerialTransport(url), url
                case "serial_raw":
                    return QemuSocketSerialRawTransport(url), url
                case "udp":
                    pytest.fail("UDP fixtures do not present as a socket serial endpoint")
                case _ as unreachable:
                    assert_never(unreachable)
        case UdpEndpoint(host, port):
            if port != _SMP_UDP_DEFAULT_PORT:
                pytest.skip(
                    f"UDP fixture port {port} is unreachable: SMPClient.connect cannot pass a "
                    f"non-default UDP port (only {_SMP_UDP_DEFAULT_PORT})"
                )
            return SMPUDPTransport(), host
        case _:
            assert_never(endpoint)


async def _wait_until_answering(client: SMPClient, *, attempts: int = 30) -> None:
    """Round-trip an echo until the server answers, tolerating boot-time stalls."""
    for _ in range(attempts):
        try:
            response = await client.request(EchoWrite(d=_READY_PROBE), timeout_s=1.0)
            if success(response) and response.r == _READY_PROBE:
                return
        except (TimeoutError, SMPBadSequence):
            await asyncio.sleep(0.1)
    raise TimeoutError(f"{client.address} never answered an echo")


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
    client: SMPClient, image: bytes, *, max_bytes: int | None = None
) -> list[int]:
    """Upload `image` via the img group, asserting monotonic offsets.

    Runs to completion when `max_bytes` is `None`; otherwise stops once the offset
    reaches `max_bytes` (to prove fragmented progress without a slow full upload
    under emulation).

    Args:
        client: a connected `SMPClient`.
        image: the signed image bytes to upload.
        max_bytes: stop early once the offset reaches this many bytes.

    Returns:
        The offsets yielded by the upload, in order.
    """
    offsets: list[int] = []
    async for offset in client.upload(image, first_timeout_s=30.0, subsequent_timeout_s=5.0):
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


@asynccontextmanager
async def connected(fixture: ServerFixture) -> AsyncIterator[ConnectedServer]:
    """Launch `fixture`, connect an `SMPClient`, and wait until the server answers."""
    async with serve(fixture) as endpoint:
        transport, address = _build_transport(fixture, endpoint)
        client = SMPClient(transport, address)
        await client.connect()
        await _wait_until_answering(client)
        # Re-initialize in case the first MCUMgr parameter read raced server boot.
        await client._initialize()
        try:
            yield ConnectedServer(client, fixture, endpoint)
        finally:
            # Tolerant: a recovery test may have rebooted the server out from under us.
            try:
                await client.disconnect()
            except Exception as e:
                logger.debug(f"disconnect during teardown failed: {e}")


@pytest_asyncio.fixture(params=fixture_params())
async def connected_server(request: pytest.FixtureRequest) -> AsyncIterator[ConnectedServer]:
    async with connected(request.param) as server:
        yield server
