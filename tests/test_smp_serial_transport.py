"""Tests for `SMPSerialTransport`."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from serial import Serial
from smp import packet as smppacket

from smpclient.requests.os_management import EchoWrite
from smpclient.transport.serial import SMPSerialTransport


def test_constructor() -> None:
    t = SMPSerialTransport()
    assert isinstance(t._conn, Serial)

    t = SMPSerialTransport(max_smp_encoded_frame_size=512, line_length=128, line_buffers=4)
    assert isinstance(t._conn, Serial)
    assert t.mtu == 512
    assert t.max_unencoded_size < 512


@patch("smpclient.transport.serial.Serial")
@pytest.mark.asyncio
async def test_connect(_: MagicMock) -> None:
    t = SMPSerialTransport()

    await t.connect("COM2", 1.0)
    assert t._conn.port == "COM2"

    t._conn.open.assert_called_once()  # type: ignore

    t._conn.reset_mock()  # type: ignore

    t = SMPSerialTransport()

    await t.connect("/dev/ttyACM0", 1.0)
    assert t._conn.port == "/dev/ttyACM0"

    t._conn.open.assert_called_once()  # type: ignore


@patch("smpclient.transport.serial.Serial")
@pytest.mark.asyncio
async def test_disconnect(_: MagicMock) -> None:
    t = SMPSerialTransport()
    await t.disconnect()
    t._conn.close.assert_called_once()  # type: ignore


@pytest.mark.asyncio
async def test_send() -> None:
    t = SMPSerialTransport()
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(return_value=0)
    type(t._conn).out_waiting = p  # type: ignore

    r = EchoWrite(d="Hello pytest!")
    await t.send(r.BYTES)
    t._conn.write.assert_called_once()
    p.assert_called_once_with()

    t._conn.write.reset_mock()
    p = PropertyMock(side_effect=(1, 0))
    type(t._conn).out_waiting = p  # type: ignore

    await t.send(r.BYTES)
    t._conn.write.assert_called_once()
    assert p.call_count == 2  # called twice since out buffer was not drained on first call


@pytest.mark.asyncio
async def test_receive() -> None:
    t = SMPSerialTransport()
    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    p = [p for p in smppacket.encode(m.BYTES, t.max_unencoded_size)]
    t._readuntil = AsyncMock(side_effect=p)  # type: ignore

    b = await t.receive()
    t._readuntil.assert_awaited_once_with()
    assert b == m.BYTES

    p = [p for p in smppacket.encode(m.BYTES, 8)]  # test packet fragmentation
    t._readuntil = AsyncMock(side_effect=p)  # type: ignore

    b = await t.receive()
    t._readuntil.assert_awaited()
    assert b == m.BYTES


@pytest.mark.asyncio
async def test_readuntil() -> None:
    t = SMPSerialTransport()
    m1 = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="Hello computer!")  # type: ignore
    p1 = [p for p in smppacket.encode(m1.BYTES, 8)]
    p2 = [p for p in smppacket.encode(m2.BYTES, 8)]
    packets = p1 + p2
    t._conn.read_all = MagicMock(side_effect=packets)  # type: ignore

    for p in packets:
        assert p == await t._readuntil()

    # do again, but manually fragment the buffers
    packets = [p for p in smppacket.encode(m1.BYTES, 512)] + [
        p for p in smppacket.encode(m2.BYTES, 512)
    ]
    assert len(packets) == 2
    buffers = [
        packets[0][0:3],
        packets[0][3:5],
        packets[0][5:12],
        packets[0][12:] + packets[1][0:3],
        packets[1][3:5],
        packets[1][5:12],
        packets[1][12:],
    ]

    t._conn.read_all = MagicMock(side_effect=buffers)  # type: ignore

    for p in packets:
        assert p == await t._readuntil()


@pytest.mark.asyncio
async def test_readuntil_with_smp_server_logging(caplog: pytest.LogCaptureFixture) -> None:
    t = SMPSerialTransport()
    m1 = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="Hello computer!")  # type: ignore
    p1 = [p for p in smppacket.encode(m1.BYTES, 8)]
    p2 = [p for p in smppacket.encode(m2.BYTES, 8)]
    packets = p1 + p2

    t._conn.read_all = MagicMock(  # type: ignore
        side_effect=(
            [b"Hi, there!"]
            + [b"newline \n"]
            + [b"Another line\nAgain \n"]
            + [b"log with no newline"]
            + p1
            + [b"Thought \n I'd just say hi!\n"]
            + [bytes([0, 1, 2, 3])]
            + [b"Bye!\n"]
            + p2
            + [b"One more thing...\n"]
            + [b"We \n could \n use \n newlines\n"]
        )
    )

    t._conn.port = "/dev/ttyUSB0"

    with caplog.at_level(logging.WARNING):
        for p in packets:
            assert p == await t._readuntil()

        messages = {r.message for r in caplog.records}
        assert "/dev/ttyUSB0: Hi, there!newline " in messages
        assert "/dev/ttyUSB0: Another line" in messages
        assert "/dev/ttyUSB0: Again " in messages
        assert "/dev/ttyUSB0: log with no newline" in messages
        assert "/dev/ttyUSB0: Thought \n I'd just say hi!\n\x00\x01\x02\x03Bye!\n" in messages


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    t = SMPSerialTransport()
    t.send = AsyncMock()  # type: ignore
    t.receive = AsyncMock()  # type: ignore

    await t.send_and_receive(b"some data")

    t.send.assert_awaited_once_with(b"some data")
    t.receive.assert_awaited_once_with()
