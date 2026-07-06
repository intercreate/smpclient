"""Tests for `SMPSerialRawTransport`."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from serial import SerialException
from smp import header as smphdr
from smp.packet import CRC16_STRUCT, crc16_func

from smpclient.exceptions import SMPClientException
from smpclient.requests.os_management import EchoWrite
from smpclient.transport import SMPTransportDisconnected
from smpclient.transport.serial import Cobs, SMPSerialRawTransport
from smpclient.transport.serial.framing.cobs import cobs_encode


@pytest.fixture(autouse=True)
def mock_serial() -> Generator[None, Any, None]:
    with patch("smpclient.transport.serial.common.Serial"):
        yield


def test_constructor() -> None:
    t = SMPSerialRawTransport(mtu=512)
    assert t.mtu == 512
    assert t.max_unencoded_size == 512


def test_constructor_defaults() -> None:
    t = SMPSerialRawTransport()
    assert t.mtu == 384


@pytest.mark.asyncio
async def test_connect_disconnect() -> None:
    ports: list[str] = ["COM2", "/dev/ttyACM0", "/dev/ttyUSB0"]

    t = SMPSerialRawTransport()
    t._conn.read_all = MagicMock(return_value=b"")  # type: ignore

    for p in ports:
        await asyncio.wait_for(t.connect(p, 1.0), timeout=1.0)
        t._conn.open.assert_called_once()  # type: ignore

        assert t._conn.port == p

        await asyncio.wait_for(t.disconnect(), timeout=0.1)
        t._conn.close.assert_called_once()  # type: ignore

        t._conn.reset_mock()  # type: ignore


@pytest.mark.asyncio
async def test_connect_retries_until_timeout() -> None:
    t = SMPSerialRawTransport()
    t._conn.open = MagicMock(side_effect=SerialException("nope"))  # type: ignore

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(t.connect("/dev/ttyUSB0", 0.1), timeout=2.0)


@pytest.mark.asyncio
async def test_send() -> None:
    t = SMPSerialRawTransport()
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(return_value=0)
    type(t._conn).out_waiting = p  # type: ignore

    r = EchoWrite(d="Hello pytest!")
    await t.send(r.BYTES)

    # Raw transport writes the bytes verbatim - no encoding.
    t._conn.write.assert_called_once_with(r.BYTES)
    p.assert_called_once_with()


@pytest.mark.asyncio
async def test_send_waits_for_tx_drain() -> None:
    t = SMPSerialRawTransport()
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(side_effect=(1, 0))
    type(t._conn).out_waiting = p  # type: ignore

    await t.send(EchoWrite(d="x").BYTES)
    assert p.call_count == 2


@pytest.mark.asyncio
async def test_send_too_large_raises() -> None:
    t = SMPSerialRawTransport(mtu=16)
    with pytest.raises(ValueError):
        await t.send(b"\x00" * 32)


@pytest.mark.asyncio
async def test_send_disconnected_raises() -> None:
    t = SMPSerialRawTransport()
    t._conn.write = MagicMock(side_effect=SerialException("disconnected"))  # type: ignore

    with pytest.raises(SMPTransportDisconnected):
        await t.send(EchoWrite(d="x").BYTES)


@pytest.mark.asyncio
async def test_receive_single_packet() -> None:
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    t._conn.read_all = MagicMock(side_effect=[m.BYTES])  # type: ignore

    received = await t.receive()
    assert received == m.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_fragmented() -> None:
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    fragments = [
        m.BYTES[:3],  # less than a header
        m.BYTES[3:8],  # completes the header but no payload yet
        m.BYTES[8:10],
        m.BYTES[10:],  # rest of payload
    ]
    t._conn.read_all = MagicMock(side_effect=fragments)  # type: ignore

    received = await t.receive()
    assert received == m.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_byte_at_a_time() -> None:
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="Hi")  # type: ignore
    t._conn.read_all = MagicMock(  # type: ignore
        side_effect=[bytes([b]) for b in m.BYTES]
    )

    received = await t.receive()
    assert received == m.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_consecutive_messages() -> None:
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m1 = EchoWrite._Response.get_default()(sequence=0, r="SMP Message 1")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="SMP Message 2")  # type: ignore
    m3 = EchoWrite._Response.get_default()(sequence=2, r="SMP Message 3")  # type: ignore

    # Each receive() reads one full message, just like a normal request/response loop.
    t._conn.read_all = MagicMock(side_effect=[m1.BYTES, m2.BYTES, m3.BYTES])  # type: ignore

    assert await t.receive() == m1.BYTES
    assert await t.receive() == m2.BYTES
    assert await t.receive() == m3.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_overrun_raises() -> None:
    """A single read returning more bytes than the header advertises is an error.

    SMP is strictly request/response; the server should never send unsolicited bytes.
    """
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="Hello!")  # type: ignore
    t._conn.read_all = MagicMock(side_effect=[m.BYTES + b"\x00\x01\x02"])  # type: ignore

    with pytest.raises(SMPClientException):
        await t.receive()

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_polls_when_nothing_available() -> None:
    t = SMPSerialRawTransport()
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="ok")  # type: ignore
    t._conn.read_all = MagicMock(side_effect=[b"", b"", m.BYTES])  # type: ignore

    received = await t.receive()
    assert received == m.BYTES
    assert t._conn.read_all.call_count >= 3

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_oversized_header_raises() -> None:
    """A header claiming more bytes than max_unencoded_size is rejected.

    Defensive bound against noisy or corrupted UART traffic that would
    otherwise cause an unbounded wait.
    """
    t = SMPSerialRawTransport(mtu=64)
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    bogus_header = smphdr.Header(
        op=smphdr.OP.WRITE_RSP,
        version=smphdr.Version.V2,
        flags=smphdr.Flag(0),
        length=10_000,
        group_id=smphdr.GroupId.OS_MANAGEMENT,
        sequence=0,
        command_id=smphdr.CommandId.OSManagement.ECHO,
    ).BYTES
    t._conn.read_all = MagicMock(side_effect=[bogus_header])  # type: ignore

    with pytest.raises(SMPClientException):
        await t.receive()

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_disconnected_raises() -> None:
    t = SMPSerialRawTransport()
    t._conn.read_all = MagicMock(side_effect=SerialException("disconnected"))  # type: ignore

    with pytest.raises(SMPTransportDisconnected):
        await t.receive()


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    t = SMPSerialRawTransport()
    t.send = AsyncMock()  # type: ignore
    t.receive = AsyncMock()  # type: ignore

    await t.send_and_receive(b"some data")

    t.send.assert_awaited_once_with(b"some data")
    t.receive.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_send_with_cobs_framing_encodes() -> None:
    t = SMPSerialRawTransport(framing=Cobs())
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(return_value=0)
    type(t._conn).out_waiting = p  # type: ignore

    msg = EchoWrite(d="Hello pytest!").BYTES
    await t.send(msg)

    expected = cobs_encode(msg + CRC16_STRUCT.pack(crc16_func(msg))) + b"\x00"
    t._conn.write.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_receive_with_cobs_framing_decodes() -> None:
    t = SMPSerialRawTransport(framing=Cobs())
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    (wire,) = Cobs().encode(m.BYTES)
    t._conn.read_all = MagicMock(side_effect=[wire])  # type: ignore

    assert await t.receive() == m.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_with_cobs_framing_fragmented() -> None:
    t = SMPSerialRawTransport(framing=Cobs())
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="fragment me across reads")  # type: ignore
    (wire,) = Cobs().encode(m.BYTES)
    t._conn.read_all = MagicMock(side_effect=[wire[:5], b"", wire[5:]])  # type: ignore

    assert await t.receive() == m.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_two_cobs_frames_in_one_read() -> None:
    """Two frames in one read: the second is drained from the persisted decoder buffer.

    The next receive returns it without consulting read_all again.
    """
    t = SMPSerialRawTransport(framing=Cobs())
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m1 = EchoWrite._Response.get_default()(sequence=0, r="first")  # type: ignore
    m2 = EchoWrite._Response.get_default()(sequence=1, r="second")  # type: ignore
    (w1,) = Cobs().encode(m1.BYTES)
    (w2,) = Cobs().encode(m2.BYTES)
    t._conn.read_all = MagicMock(side_effect=[w1 + w2])  # type: ignore

    assert await t.receive() == m1.BYTES
    assert await t.receive() == m2.BYTES  # from leftover; read_all not consulted again
    assert t._conn.read_all.call_count == 1

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_cobs_framing_resyncs_past_corrupt_frame() -> None:
    """A corrupt frame ahead of a good one is dropped; receive resyncs to the good frame.

    The two frames carry *different* payloads, so a decoder that wrongly accepted the
    corrupt frame would surface `dropped`, not `recovered`.
    """
    t = SMPSerialRawTransport(framing=Cobs())
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    dropped = EchoWrite._Response.get_default()(sequence=0, r="dropped")  # type: ignore
    recovered = EchoWrite._Response.get_default()(sequence=1, r="recovered")  # type: ignore
    corrupt = (
        cobs_encode(dropped.BYTES + CRC16_STRUCT.pack(crc16_func(dropped.BYTES) ^ 0xFFFF)) + b"\x00"
    )
    (good,) = Cobs().encode(recovered.BYTES)
    t._conn.read_all = MagicMock(side_effect=[corrupt + good])  # type: ignore

    assert await t.receive() == recovered.BYTES

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_framed_yields_so_an_outer_timeout_can_fire() -> None:
    """A non-stop stream that never forms a valid frame must not wedge the loop.

    `_read_all` is synchronous, so the loop must yield each iteration; otherwise an outer
    `asyncio.timeout` could never fire on a wrong-baud / wrong-protocol / noisy peer.
    """
    t = SMPSerialRawTransport(framing=Cobs())
    await t.connect("/dev/ttyUSB0", timeout_s=1.0)

    m = EchoWrite._Response.get_default()(sequence=0, r="never valid")  # type: ignore
    corrupt = cobs_encode(m.BYTES + CRC16_STRUCT.pack(crc16_func(m.BYTES) ^ 0xFFFF)) + b"\x00"
    t._conn.read_all = MagicMock(return_value=corrupt)  # type: ignore  # endless, never valid

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(t.receive(), timeout=0.2)

    await t.disconnect()
