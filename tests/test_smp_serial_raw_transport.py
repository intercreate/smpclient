"""Tests for `SMPSerialRawTransport`."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any, Final
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from serial import SerialException
from smp import header as smphdr
from smp.os_management import EchoWriteRequest, EchoWriteResponse
from smp.packet import CRC16_STRUCT, crc16_func

from smpclient.exceptions import SMPClientException
from smpclient.transport import BufferSize, SMPTransportDisconnected
from smpclient.transport.serial import Cobs, SMPSerialRawTransport
from smpclient.transport.serial.framing.cobs import cobs_encode
from tests.support import advertise, negotiated

pytestmark = pytest.mark.usefixtures("skip_negotiation")

PORT = "/dev/ttyUSB0"
"""A port name; `Serial` is mocked, so nothing is opened."""


@pytest.fixture(autouse=True)
def mock_serial() -> Generator[None, Any, None]:
    with patch("smpclient.transport.serial.common.Serial"):
        yield


def test_constructor() -> None:
    t = SMPSerialRawTransport(PORT, fragmentation_strategy=BufferSize(512))
    assert t.mtu == 512
    assert t.max_unencoded_size == 512


def test_constructor_defaults() -> None:
    t = SMPSerialRawTransport(PORT)
    assert t.mtu == 384


@pytest.mark.asyncio
async def test_negotiate_with_auto() -> None:
    """`Auto` adopts the server's buffer: the whole message rides in it, with no framing."""
    t = await negotiated(SMPSerialRawTransport(PORT), 1024)
    assert t.mtu == t.max_unencoded_size == 1024


@pytest.mark.asyncio
async def test_negotiate_never_reads_for_buffer_size() -> None:
    t = SMPSerialRawTransport(PORT, fragmentation_strategy=BufferSize(512))
    with advertise(1024) as read:
        await t.negotiate()
    read.assert_not_awaited()
    assert t.max_unencoded_size == 512


@pytest.mark.asyncio
async def test_connect_disconnect() -> None:
    ports: list[str] = ["COM2", "/dev/ttyACM0", "/dev/ttyUSB0"]

    for p in ports:
        t = SMPSerialRawTransport(p, connect_timeout_s=1.0)
        t._conn.read_all = MagicMock(return_value=b"")  # type: ignore

        await asyncio.wait_for(t.connect(), timeout=1.0)
        t._conn.open.assert_called_once()  # type: ignore

        assert t._conn.port == p

        await asyncio.wait_for(t.disconnect(), timeout=0.1)
        t._conn.close.assert_called_once()  # type: ignore

        t._conn.reset_mock()  # type: ignore


@pytest.mark.asyncio
async def test_connect_retries_until_timeout() -> None:
    t = SMPSerialRawTransport(PORT, connect_timeout_s=0.1)
    t._conn.open = MagicMock(side_effect=SerialException("nope"))  # type: ignore

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(t.connect(), timeout=2.0)


@pytest.mark.asyncio
async def test_connect_closes_the_port_when_the_flush_fails() -> None:
    """The flush is outside the retry, which would reopen the open port until the timeout."""
    t = SMPSerialRawTransport(PORT)
    t._conn.reset_input_buffer = MagicMock(side_effect=SerialException("flush"))  # type: ignore

    with pytest.raises(SerialException):
        await t.connect()

    t._conn.open.assert_called_once()  # type: ignore
    t._conn.close.assert_called_once()  # type: ignore


@pytest.mark.asyncio
async def test_connect_closes_the_port_when_cancelled_while_negotiating() -> None:
    t = SMPSerialRawTransport(PORT)

    with (
        patch(
            "smpclient._request.read_mcumgr_parameters",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await t.connect()

    t._conn.close.assert_called_once()  # type: ignore


@pytest.mark.asyncio
async def test_borrowed_uses_the_port_and_leaves_it_open() -> None:
    port: Final = MagicMock(out_waiting=0)
    t = SMPSerialRawTransport(PORT)
    r = EchoWriteRequest(d="Hello pytest!").to_frame(sequence=0)

    async with t.borrowed(port) as borrowed:
        assert borrowed is t
        await t.send(bytes(r))

    port.write.assert_called_once_with(bytes(r))
    port.close.assert_not_called()
    t._serial.open.assert_not_called()  # type: ignore
    t._serial.close.assert_not_called()  # type: ignore
    assert t._conn is t._serial


@pytest.mark.asyncio
async def test_borrow_negotiates_the_fragmentation_strategy() -> None:
    t = SMPSerialRawTransport(PORT)

    with advertise(2048) as read_mcumgr_parameters:
        await t.borrow(MagicMock())

    read_mcumgr_parameters.assert_awaited_once()
    assert t.max_unencoded_size == 2048


@pytest.mark.asyncio
async def test_borrow_reverts_to_the_owned_port_when_negotiation_fails() -> None:
    t = SMPSerialRawTransport(PORT)

    with (
        patch(
            "smpclient._request.read_mcumgr_parameters",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await t.borrow(MagicMock())

    assert t._conn is t._serial


@pytest.mark.asyncio
async def test_send() -> None:
    t = SMPSerialRawTransport(PORT)
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(return_value=0)
    type(t._conn).out_waiting = p  # type: ignore

    r = EchoWriteRequest(d="Hello pytest!").to_frame(sequence=0)
    await t.send(bytes(r))

    # Raw transport writes the bytes verbatim - no encoding.
    t._conn.write.assert_called_once_with(bytes(r))
    p.assert_called_once_with()


@pytest.mark.asyncio
async def test_send_waits_for_tx_drain() -> None:
    t = SMPSerialRawTransport(PORT)
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(side_effect=(1, 0))
    type(t._conn).out_waiting = p  # type: ignore

    await t.send(bytes(EchoWriteRequest(d="x").to_frame(sequence=0)))
    assert p.call_count == 2


@pytest.mark.asyncio
async def test_send_too_large_raises() -> None:
    t = SMPSerialRawTransport(PORT, fragmentation_strategy=BufferSize(16))
    with pytest.raises(ValueError):
        await t.send(b"\x00" * 32)


@pytest.mark.asyncio
async def test_send_disconnected_raises() -> None:
    t = SMPSerialRawTransport(PORT)
    t._conn.write = MagicMock(side_effect=SerialException("disconnected"))  # type: ignore

    with pytest.raises(SMPTransportDisconnected):
        await t.send(bytes(EchoWriteRequest(d="x").to_frame(sequence=0)))


@pytest.mark.asyncio
async def test_receive_single_packet() -> None:
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m = EchoWriteResponse(r="Hello pytest!").to_frame(sequence=0)
    t._conn.read_all = MagicMock(side_effect=[bytes(m)])  # type: ignore

    received = await t.receive()
    assert received == bytes(m)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_fragmented() -> None:
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m = EchoWriteResponse(r="Hello pytest!").to_frame(sequence=0)
    fragments = [
        bytes(m)[:3],  # less than a header
        bytes(m)[3:8],  # completes the header but no payload yet
        bytes(m)[8:10],
        bytes(m)[10:],  # rest of payload
    ]
    t._conn.read_all = MagicMock(side_effect=fragments)  # type: ignore

    received = await t.receive()
    assert received == bytes(m)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_byte_at_a_time() -> None:
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m = EchoWriteResponse(r="Hi").to_frame(sequence=0)
    t._conn.read_all = MagicMock(  # type: ignore
        side_effect=[bytes([b]) for b in bytes(m)]
    )

    received = await t.receive()
    assert received == bytes(m)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_consecutive_messages() -> None:
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m1 = EchoWriteResponse(r="SMP Message 1").to_frame(sequence=0)
    m2 = EchoWriteResponse(r="SMP Message 2").to_frame(sequence=1)
    m3 = EchoWriteResponse(r="SMP Message 3").to_frame(sequence=2)

    # Each receive() reads one full message, just like a normal request/response loop.
    t._conn.read_all = MagicMock(side_effect=[bytes(m1), bytes(m2), bytes(m3)])  # type: ignore

    assert await t.receive() == bytes(m1)
    assert await t.receive() == bytes(m2)
    assert await t.receive() == bytes(m3)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_overrun_raises() -> None:
    """A single read returning more bytes than the header advertises is an error.

    SMP is strictly request/response; the server should never send unsolicited bytes.
    """
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m = EchoWriteResponse(r="Hello!").to_frame(sequence=0)
    t._conn.read_all = MagicMock(side_effect=[bytes(m) + b"\x00\x01\x02"])  # type: ignore

    with pytest.raises(SMPClientException):
        await t.receive()

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_polls_when_nothing_available() -> None:
    t = SMPSerialRawTransport(PORT)
    await t.connect()

    m = EchoWriteResponse(r="ok").to_frame(sequence=0)
    t._conn.read_all = MagicMock(side_effect=[b"", b"", bytes(m)])  # type: ignore

    received = await t.receive()
    assert received == bytes(m)
    assert t._conn.read_all.call_count >= 3

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_oversized_header_raises() -> None:
    """A header claiming more bytes than max_unencoded_size is rejected.

    Defensive bound against noisy or corrupted UART traffic that would
    otherwise cause an unbounded wait.
    """
    t = SMPSerialRawTransport(PORT, fragmentation_strategy=BufferSize(64))
    await t.connect()

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
    t = SMPSerialRawTransport(PORT)
    t._conn.read_all = MagicMock(side_effect=SerialException("disconnected"))  # type: ignore

    with pytest.raises(SMPTransportDisconnected):
        await t.receive()


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    t = SMPSerialRawTransport(PORT)
    t.send = AsyncMock()  # type: ignore
    t.receive = AsyncMock()  # type: ignore

    await t.send_and_receive(b"some data")

    t.send.assert_awaited_once_with(b"some data")
    t.receive.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_send_with_cobs_framing_encodes() -> None:
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    t._conn.write = MagicMock()  # type: ignore
    p = PropertyMock(return_value=0)
    type(t._conn).out_waiting = p  # type: ignore

    msg = bytes(EchoWriteRequest(d="Hello pytest!").to_frame(sequence=0))
    await t.send(msg)

    expected = cobs_encode(msg + CRC16_STRUCT.pack(crc16_func(msg))) + b"\x00"
    t._conn.write.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_receive_with_cobs_framing_decodes() -> None:
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    await t.connect()

    m = EchoWriteResponse(r="Hello pytest!").to_frame(sequence=0)
    (wire,) = Cobs().encode(bytes(m))
    t._conn.read_all = MagicMock(side_effect=[wire])  # type: ignore

    assert await t.receive() == bytes(m)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_with_cobs_framing_fragmented() -> None:
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    await t.connect()

    m = EchoWriteResponse(r="fragment me across reads").to_frame(sequence=0)
    (wire,) = Cobs().encode(bytes(m))
    t._conn.read_all = MagicMock(side_effect=[wire[:5], b"", wire[5:]])  # type: ignore

    assert await t.receive() == bytes(m)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_two_cobs_frames_in_one_read() -> None:
    """Two frames in one read: the second is drained from the persisted decoder buffer.

    The next receive returns it without consulting read_all again.
    """
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    await t.connect()

    m1 = EchoWriteResponse(r="first").to_frame(sequence=0)
    m2 = EchoWriteResponse(r="second").to_frame(sequence=1)
    (w1,) = Cobs().encode(bytes(m1))
    (w2,) = Cobs().encode(bytes(m2))
    t._conn.read_all = MagicMock(side_effect=[w1 + w2])  # type: ignore

    assert await t.receive() == bytes(m1)
    assert await t.receive() == bytes(m2)  # from leftover; read_all not consulted again
    assert t._conn.read_all.call_count == 1

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_cobs_framing_resyncs_past_corrupt_frame() -> None:
    """A corrupt frame ahead of a good one is dropped; receive resyncs to the good frame.

    The two frames carry *different* payloads, so a decoder that wrongly accepted the
    corrupt frame would surface `dropped`, not `recovered`.
    """
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    await t.connect()

    dropped = EchoWriteResponse(r="dropped").to_frame(sequence=0)
    recovered = EchoWriteResponse(r="recovered").to_frame(sequence=1)
    corrupt = (
        cobs_encode(bytes(dropped) + CRC16_STRUCT.pack(crc16_func(bytes(dropped)) ^ 0xFFFF))
        + b"\x00"
    )
    (good,) = Cobs().encode(bytes(recovered))
    t._conn.read_all = MagicMock(side_effect=[corrupt + good])  # type: ignore

    assert await t.receive() == bytes(recovered)

    await t.disconnect()


@pytest.mark.asyncio
async def test_receive_framed_yields_so_an_outer_timeout_can_fire() -> None:
    """A non-stop stream that never forms a valid frame must not wedge the loop.

    `_read_all` is synchronous, so the loop must yield each iteration; otherwise an outer
    `asyncio.timeout` could never fire on a wrong-baud / wrong-protocol / noisy peer.
    """
    t = SMPSerialRawTransport(PORT, framing=Cobs())
    await t.connect()

    m = EchoWriteResponse(r="never valid").to_frame(sequence=0)
    corrupt = cobs_encode(bytes(m) + CRC16_STRUCT.pack(crc16_func(bytes(m)) ^ 0xFFFF)) + b"\x00"
    t._conn.read_all = MagicMock(return_value=corrupt)  # type: ignore  # endless, never valid

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(t.receive(), timeout=0.2)

    await t.disconnect()
