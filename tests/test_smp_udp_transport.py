"""Tests for `SMPUDPTransport`."""

import asyncio
from typing import Final, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from smpclient.exceptions import SMPClientException
from smpclient.requests.os_management import EchoWrite
from smpclient.transport._udp_client import Addr, UDPClient
from smpclient.transport.udp import IPV4_UDP_OVERHEAD, IPV6_UDP_OVERHEAD, SMPUDPTransport


def test_init() -> None:
    t = SMPUDPTransport()
    assert t.mtu == 1500
    assert isinstance(t._client, UDPClient)

    t = SMPUDPTransport(mtu=512)
    assert t.mtu == 512


@patch("smpclient.transport.udp.UDPClient", autospec=True)
@pytest.mark.asyncio
async def test_connect(_: MagicMock) -> None:
    t = SMPUDPTransport()
    t._client = cast(MagicMock, t._client)  # type: ignore

    # Mock _transport for IPv4/IPv6 detection
    t._client._transport = MagicMock()
    t._client._transport.get_extra_info.return_value = None

    await t.connect("192.168.0.1", 0.001)
    t._client.connect.assert_awaited_once_with(Addr(host="192.168.0.1", port=1337))


@patch("smpclient.transport.udp.UDPClient", autospec=True)
@pytest.mark.asyncio
async def test_disconnect(_: MagicMock) -> None:
    t = SMPUDPTransport()
    t._client = cast(MagicMock, t._client)  # type: ignore
    t._client._protocol = MagicMock()

    # no errors in error queue
    t._client._protocol.error_queue.empty.return_value = True
    await t.disconnect()
    t._client.disconnect.assert_called_once()

    # errors in error queue
    t._client._protocol.error_queue = asyncio.Queue()
    t._client._protocol.error_queue.put_nowait(Exception("beep"))
    t._client._protocol.error_queue.put_nowait(Exception("boop"))
    assert t._client._protocol.error_queue.empty() is False
    await t.disconnect()
    assert t._client._protocol.error_queue.empty() is True


@patch("smpclient.transport.udp.UDPClient", autospec=True)
@pytest.mark.asyncio
async def test_send(_: MagicMock) -> None:
    t = SMPUDPTransport()
    t._client.send = cast(MagicMock, t._client.send)  # type: ignore

    await t.send(b"hello")
    t._client.send.assert_called_once_with(b"hello")

    t._client.send.reset_mock()

    # test fragmentation - really don't suggest this over UDP
    big_message: Final = bytes(t.max_unencoded_size + 1)
    await t.send(big_message)
    assert t._client.send.call_count == 2
    t._client.send.assert_has_calls(
        (call(big_message[: t.max_unencoded_size]), call(big_message[t.max_unencoded_size :]))
    )


@patch("smpclient.transport.udp.UDPClient", autospec=True)
@pytest.mark.asyncio
async def test_receive(_: MagicMock) -> None:
    t = SMPUDPTransport()
    t._client.receive = AsyncMock()  # type: ignore

    message = bytes(EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!"))  # type: ignore # noqa

    # no fragmentation
    t._client.receive.return_value = message
    assert await t.receive() == message

    # fragmentation
    t._client.receive.side_effect = (message[:10], message[10:11], message[11:12], message[12:])
    assert await t.receive() == message

    # received transmission that included some of the next packet
    # technically we could support this, but we don't for now
    with pytest.raises(SMPClientException):
        t._client.receive.side_effect = (
            message[:10],
            message[10:11],
            message[11:12],
            message[12:] + message[:10],
        )
        await t.receive()


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    with patch("smpclient.transport.udp.SMPUDPTransport.send") as send_mock, patch(
        "smpclient.transport.udp.SMPUDPTransport.receive"
    ) as receive_mock:
        t = SMPUDPTransport()
        message: Final = b"hello"
        await t.send_and_receive(message)
        send_mock.assert_awaited_once_with(message)
        receive_mock.assert_awaited_once()


def test_max_unencoded_size_ipv4() -> None:
    """Test MSS calculation for IPv4 (default)."""
    t = SMPUDPTransport(mtu=1500)
    # Before connection, defaults to IPv4
    assert t.max_unencoded_size == 1500 - IPV4_UDP_OVERHEAD
    assert t.max_unencoded_size == 1472


def test_max_unencoded_size_custom_mtu() -> None:
    """Test MSS calculation with custom MTU."""
    t = SMPUDPTransport(mtu=512)
    assert t.max_unencoded_size == 512 - IPV4_UDP_OVERHEAD
    assert t.max_unencoded_size == 484


@pytest.mark.asyncio
async def test_ipv4_detection_real_socket() -> None:
    """Test IPv4 auto-detection with real socket connection."""
    t = SMPUDPTransport(mtu=1500)

    # Create a real UDP connection to localhost IPv4
    await t.connect("127.0.0.1", 1.0)

    assert t._is_ipv6 is False
    assert t.max_unencoded_size == 1500 - IPV4_UDP_OVERHEAD
    assert t.max_unencoded_size == 1472

    await t.disconnect()


@pytest.mark.asyncio
async def test_ipv6_detection_real_socket() -> None:
    """Test IPv6 auto-detection with real socket connection."""
    t = SMPUDPTransport(mtu=1500)

    # Create a real UDP connection to localhost IPv6
    await t.connect("::1", 1.0)

    assert t._is_ipv6 is True
    assert t.max_unencoded_size == 1500 - IPV6_UDP_OVERHEAD
    assert t.max_unencoded_size == 1452

    await t.disconnect()
