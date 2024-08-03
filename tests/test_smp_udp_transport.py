"""Tests for `SMPUDPTransport`."""

import asyncio
from typing import Final, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from smpclient.exceptions import SMPClientException
from smpclient.requests.os_management import EchoWrite
from smpclient.transport._udp_client import Addr, UDPClient
from smpclient.transport.udp import SMPUDPTransport


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
