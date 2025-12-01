"""Tests for `SMPSerialTransport`."""

from __future__ import annotations

import asyncio
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from serial import SerialException
from smp import packet as smppacket

from smpclient.requests.os_management import EchoWrite
from smpclient.transport import SMPTransportDisconnected
from smpclient.transport.serial import SMPSerialTransport


@pytest.fixture(autouse=True)
def mock_serial() -> Generator[None, Any, None]:
    with patch("smpclient.transport.serial.Serial"):
        yield


def test_constructor() -> None:
    t = SMPSerialTransport(max_smp_encoded_frame_size=512, line_length=128, line_buffers=4)
    assert t.mtu == 512
    assert t.max_unencoded_size < 512


@pytest.mark.asyncio
async def test_connect_disconnect() -> None:
    ports: list[str] = ["COM2", "/dev/ttyACM0", "/dev/ttyUSB0"]

    t = SMPSerialTransport()
    t._conn.read_all = MagicMock(return_value=b"")  # type: ignore

    for p in ports:
        await asyncio.wait_for(t.connect(p, 1.0), timeout=1.0)
        t._conn.open.assert_called_once()  # type: ignore

        assert t._conn.port == p

        await asyncio.wait_for(t.disconnect(), timeout=0.1)
        t._conn.close.assert_called_once()  # type: ignore

        t._conn.reset_mock()  # type: ignore


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
    t = SMPSerialTransport(timeout=0.1)
    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    p = [p for p in smppacket.encode(m.BYTES, t.max_unencoded_size)]
    t._read_one_smp_packet = AsyncMock(side_effect=p)  # type: ignore

    b = await t.receive()
    t._read_one_smp_packet.assert_awaited_once_with(timeout_s=0.1)

    assert b == m.BYTES

    p = [p for p in smppacket.encode(m.BYTES, 8)]  # test packet fragmentation
    t._read_one_smp_packet = AsyncMock(side_effect=p)  # type: ignore

    b = await t.receive()
    t._read_one_smp_packet.assert_awaited()
    assert b == m.BYTES


@pytest.mark.asyncio
async def test_read_one_smp_packet() -> None:
    t = SMPSerialTransport()
    await t.connect("COM2", timeout_s=1.0)

    m1 = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="Hello computer!")  # type: ignore
    p1 = [p for p in smppacket.encode(m1.BYTES, 8)]
    p2 = [p for p in smppacket.encode(m2.BYTES, 8)]
    packets = p1 + p2
    t._conn.read_all = MagicMock(side_effect=packets)  # type: ignore

    for p in packets:
        assert p == await t._read_one_smp_packet(timeout_s=0.1)

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
        assert p == await t._read_one_smp_packet(timeout_s=0.1)

    await t.disconnect()


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    t = SMPSerialTransport()
    t.send = AsyncMock()  # type: ignore
    t.receive = AsyncMock()  # type: ignore

    await t.send_and_receive(b"some data")

    t.send.assert_awaited_once_with(b"some data")
    t.receive.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_receive_timeout() -> None:
    t = SMPSerialTransport(timeout=0.1)
    t._read_one_smp_packet = AsyncMock(side_effect=TimeoutError)  # type: ignore

    with pytest.raises(TimeoutError):
        await t.receive()


@pytest.mark.asyncio
async def test_only_serial_data_no_smp() -> None:
    t = SMPSerialTransport()
    await t.connect("/dev/ttyACM0", timeout_s=1.0)

    t._conn.read_all = MagicMock(  # type: ignore
        side_effect=[
            b"First line\n",
            b"Second line\nThird line\n",
            b"Partial line",
            b" continues here\n",
            b"No newline at end",
        ]
    )

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"First line"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Second line"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Third line"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Partial line continues here"

    # Last line is not newline terminated:
    data = await t.read_serial(delimiter=b"\n")
    assert data == b""
    # So read remaining data without delimiter
    data = await t.read_serial()
    assert data == b"No newline at end"

    await t.disconnect()


@pytest.mark.asyncio
async def test_only_smp_data_no_serial() -> None:
    t = SMPSerialTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m1 = EchoWrite._Response.get_default()(sequence=0, r="SMP Message 1")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="SMP Message 2")  # type: ignore
    m3 = EchoWrite._Response.get_default()(sequence=2, r="SMP Message 3")  # type: ignore

    packets = (
        list(smppacket.encode(m1.BYTES, 512))
        + list(smppacket.encode(m2.BYTES, 512))
        + list(smppacket.encode(m3.BYTES, 512))
    )

    t._conn.read_all = MagicMock(side_effect=packets)  # type: ignore

    for expected_msg in [m1.BYTES, m2.BYTES, m3.BYTES]:
        received = await t.receive()
        assert received == expected_msg

    await t.disconnect()


@pytest.mark.asyncio
async def test_serial_and_smp_data() -> None:
    t = SMPSerialTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m1 = EchoWrite._Response.get_default()(sequence=0, r="SMP1")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="SMP2")  # type: ignore

    p1 = next(smppacket.encode(m1.BYTES, 512))
    p2 = next(smppacket.encode(m2.BYTES, 512))

    t._conn.read_all = MagicMock(  # type: ignore
        side_effect=[
            b"Start\n" + p1[:10],
            p1[10:] + b"Mid1\n",
            b"Mid2\n" + p2,
            b"End\n",
        ]
    )

    # Note that SMP and serial data may be read in any order, so reading SMP packets
    # first in a row must work:

    received1 = await t.receive()
    assert received1 == m1.BYTES

    received2 = await t.receive()
    assert received2 == m2.BYTES

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Start"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Mid1"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"Mid2"

    data = await t.read_serial(delimiter=b"\n")
    assert data == b"End"

    await t.disconnect()


@pytest.mark.asyncio
async def test_not_connected_exception_handling() -> None:
    t = SMPSerialTransport()
    t._conn.is_open = False
    t._conn.read_all = MagicMock(side_effect=SerialException("Not connected"))  # type: ignore

    with pytest.raises(SMPTransportDisconnected):
        await t.receive()
