"""Test the generic UDP client implementation."""

import asyncio
from typing import List, Tuple, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from typing_extensions import AsyncGenerator

from smpclient.transport._udp_client import Addr, UDPClient, _UDPProtocol

try:
    from asyncio import timeout  # type: ignore
except ImportError:  # backport for Python3.10 and below
    from async_timeout import timeout  # type: ignore


def test_UDPClient_init() -> None:
    UDPClient()


def test_UDPProtocol_init() -> None:
    p = _UDPProtocol()

    assert p._receive_queue is p.receive_queue
    assert p.receive_queue.empty()

    assert p._error_queue is p.error_queue
    assert p.error_queue.empty()


@patch("smpclient.transport._udp_client._UDPProtocol", autospec=True)
@pytest.mark.asyncio
async def test_UDPClient_connect(_: MagicMock) -> None:
    c = UDPClient()

    await c.connect(Addr("127.0.0.1", 1337))
    assert isinstance(c._transport, asyncio.BaseTransport)
    assert isinstance(c._protocol, _UDPProtocol)
    assert isinstance(c._protocol.receive_queue, MagicMock)
    c._protocol = cast(MagicMock, c._protocol)
    c._protocol.connection_made.assert_called_once_with(c._transport)


def test_UDPClient_send() -> None:
    c = UDPClient()

    c._transport = MagicMock()
    c.send(b"hello")
    c._transport.sendto.assert_called_once_with(b"hello")


@pytest.mark.asyncio
async def test_UDPClient_receive() -> None:
    c = UDPClient()

    c._protocol = MagicMock()
    c._protocol.receive_queue.get = AsyncMock()
    await c.receive()
    c._protocol.receive_queue.get.assert_awaited_once()


@patch("smpclient.transport._udp_client._UDPProtocol", autospec=True)
@pytest.mark.asyncio
async def test_UDPClient_disconnect(_: MagicMock) -> None:
    c = UDPClient()

    c._transport = MagicMock()
    c.disconnect()
    c._transport.close.assert_called_once_with()

    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337))
    c.disconnect()
    await asyncio.sleep(0.001)
    c._protocol = cast(MagicMock, c._protocol)
    c._protocol.connection_lost.assert_called_once_with(None)


class _ServerProtocol(asyncio.DatagramProtocol):
    """A mock SMP server protocol for unit testing."""

    def __init__(self) -> None:
        self.datagrams_recieved: List[bytes] = []

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        self.datagrams_recieved.append(data)


@pytest_asyncio.fixture
async def udp_server() -> AsyncGenerator[Tuple[asyncio.DatagramTransport, _ServerProtocol], None]:
    transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _ServerProtocol(), local_addr=("127.0.0.1", 1337)
    )

    yield transport, protocol

    transport.close()


@pytest.mark.asyncio
async def test_send(udp_server: Tuple[asyncio.DatagramTransport, _ServerProtocol]) -> None:
    _, p = udp_server

    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337))

    c.send(b"hello")
    await asyncio.sleep(0.001)

    assert p.datagrams_recieved == [b"hello"]


@pytest.mark.asyncio
async def test_receive(udp_server: Tuple[asyncio.DatagramTransport, _ServerProtocol]) -> None:
    t, _ = udp_server

    CLIENT_ADDR = Addr("127.0.0.1", 1338)

    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337), CLIENT_ADDR)

    t.sendto(b"hello", CLIENT_ADDR)

    async with timeout(0.050):
        assert await c.receive() == b"hello"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.asyncio
async def test_error_received() -> None:
    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337))

    class MockError(OSError):
        ...

    c._protocol.error_received(MockError())
    async with timeout(0.050):
        assert isinstance(await c._protocol.error_queue.get(), MockError)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.asyncio
async def test_connection_lost_no_exception() -> None:
    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337))

    c._protocol.connection_lost(None)
    await asyncio.sleep(0.001)
    assert c._protocol.error_queue.empty()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.asyncio
async def test_connection_lost() -> None:
    c = UDPClient()
    await c.connect(Addr("127.0.0.1", 1337))

    class MockError(OSError):
        ...

    c._protocol.connection_lost(MockError())
    async with timeout(0.050):
        assert isinstance(await c._protocol.error_queue.get(), MockError)
