"""Tests for `SMPSerialTransport`."""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import Callable, Generator
from typing import Any, get_args
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from serial import SerialException
from smp import packet as smppacket

from smpclient.requests.os_management import EchoWrite
from smpclient.transport import SMPTransportDisconnected
from smpclient.transport.serial import (
    Auto,
    BufferParams,
    BufferSize,
    FragmentationStrategy,
    SMPSerialTransport,
)

FRAME_OVERHEAD = smppacket.FRAME_LENGTH_STRUCT.size + smppacket.CRC16_STRUCT.size
"""The SMP serial frame's 2-byte length + 2-byte CRC16 that share the decoded buffer."""


@pytest.fixture(autouse=True)
def mock_serial() -> Generator[None, Any, None]:
    with patch("smpclient.transport.serial.Serial"):
        yield


def test_constructor() -> None:
    # Test with Auto() (default): conservative 7.1.0-equivalent 128 * 2 budget pre-init
    t = SMPSerialTransport()
    assert t.mtu == 256  # 128 * 2, the conservative default before server params are read
    assert t._line_length == 128
    assert t._line_buffers == 2
    assert t._max_smp_encoded_frame_size == 256

    # Test with BufferParams
    t = SMPSerialTransport(fragmentation_strategy=BufferParams(line_length=128, line_buffers=4))
    assert t.mtu == 512  # 128 * 4
    assert t._line_length == 128
    assert t._line_buffers == 4
    assert t._max_smp_encoded_frame_size == 512
    assert t.max_unencoded_size < 512

    # Test with BufferSize: fills the decoded buffer (buf_size - 4), like Auto
    t = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=1024))
    assert t.mtu == 1024
    assert t._line_length == 128
    assert t._max_smp_encoded_frame_size == 1024
    assert t.max_unencoded_size == 1024 - FRAME_OVERHEAD


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
    t = SMPSerialTransport()
    m = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!")  # type: ignore
    p = [p for p in smppacket.encode(m.BYTES, t.max_unencoded_size)]
    t._read_one_smp_packet = AsyncMock(side_effect=p)  # type: ignore

    b = await t.receive()
    t._read_one_smp_packet.assert_awaited_once_with()

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
        assert p == await t._read_one_smp_packet()

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
        assert p == await t._read_one_smp_packet()

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


def test_initialize_with_auto() -> None:
    """Test that Auto mode updates parameters based on server's buffer size."""
    t = SMPSerialTransport()  # Uses Auto() by default

    # Before initialize, uses the conservative 7.1.0-equivalent 128 * 2 defaults
    assert t._line_length == 128
    assert t._line_buffers == 2
    assert t._max_smp_encoded_frame_size == 256

    # After initialize with server buffer size
    t.initialize(512)
    assert t._line_length == 128
    assert t._line_buffers == 512 // 128  # 4
    assert t._max_smp_encoded_frame_size == 512
    assert t.mtu == 512
    # Auto uses the full decoded netbuf: buf_size minus the 2-byte SMP serial frame
    # length and 2-byte CRC16 (verified on native_sim/QEMU/mps2: buf_size-4 round-trips).
    assert t.max_unencoded_size == 512 - FRAME_OVERHEAD


def test_initialize_with_buffer_params() -> None:
    """Test that BufferParams mode doesn't change user-specified parameters."""
    t = SMPSerialTransport(fragmentation_strategy=BufferParams(line_length=128, line_buffers=2))

    # Before initialize
    assert t._line_length == 128
    assert t._line_buffers == 2
    assert t._max_smp_encoded_frame_size == 256  # 128 * 2

    # After initialize - parameters should NOT change
    t.initialize(512)
    assert t._line_length == 128
    assert t._line_buffers == 2
    assert t._max_smp_encoded_frame_size == 256
    assert t.mtu == 256


def test_initialize_with_buffer_params_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Test that a warning is logged when user's params exceed server buffer size."""
    t = SMPSerialTransport(
        fragmentation_strategy=BufferParams(
            line_length=128,
            line_buffers=4,  # 128 * 4 = 512
        )
    )

    with caplog.at_level(logging.WARNING):
        t.initialize(256)  # Server buffer (256) is smaller than calculated size (512)

    assert any("exceeds server buffer size" in record.message for record in caplog.records)


def test_buffer_size() -> None:
    """BufferSize fills the decoded reassembly buffer: max message == buf_size - 4."""
    for buf_size in (96, 256, 384, 512, 1024, 2048):
        t = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=buf_size))
        assert t.mtu == buf_size
        assert t._line_length == 128
        assert t.max_unencoded_size == buf_size - FRAME_OVERHEAD


def test_buffer_size_matches_initialized_auto() -> None:
    """BufferSize(n) is equivalent to Auto initialized with buf_size n."""
    auto = SMPSerialTransport()
    auto.initialize(1024)
    told = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=1024))

    assert told.max_unencoded_size == auto.max_unencoded_size == 1024 - FRAME_OVERHEAD
    assert told.mtu == auto.mtu == 1024
    assert told._line_length == auto._line_length == 128


def test_buffer_size_small_line_length() -> None:
    """A server with a sub-128 per-line buffer keeps the full decoded-buffer payload."""
    t = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=384, line_length=64))
    assert t._line_length == 64
    assert t.max_unencoded_size == 384 - FRAME_OVERHEAD


def test_line_buffers_never_misleading_zero() -> None:
    """Sub-line-length decoded buffers report >= 1 line buffer, never a misleading 0."""
    # BufferSize with a buffer smaller than one line still reports at least one line buffer.
    assert SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=96))._line_buffers == 1

    # Auto initialized against a sub-line-length server buffer, likewise.
    auto_small = SMPSerialTransport()
    auto_small.initialize(96)
    assert auto_small._line_buffers == 1

    # A non-multiple server buffer floors to a sane count and still fills buf_size - overhead.
    auto_400 = SMPSerialTransport()
    auto_400.initialize(400)
    assert auto_400._line_buffers == 400 // 128  # 3
    assert auto_400.max_unencoded_size == 400 - FRAME_OVERHEAD


async def _frame_on_the_wire(t: SMPSerialTransport, message: bytes) -> bytes:
    """Return the exact bytes `t.send(message)` writes to the serial connection."""
    written: list[bytes] = []

    def capture(data: bytes) -> int:
        written.append(bytes(data))
        return len(data)

    t._conn.write = capture  # type: ignore
    t._conn.out_waiting = 0  # type: ignore
    await t.send(message)
    return b"".join(written)


@pytest.mark.asyncio
async def test_decoded_buffer_strategies_put_full_encoded_frame_on_the_wire() -> None:
    """`BufferSize`/`Auto` transmit an encoded frame ~1.37x buf_size -- bigger than the buffer.

    The server base64-decodes each line into its `buf_size` reassembly buffer as the line
    arrives, so the client deliberately puts MORE than `buf_size` encoded bytes on the wire
    (the whole point of filling the decoded buffer). This pins the on-wire frame size for
    the largest message each decoded-buffer strategy will send, and that it exceeds the
    buffer rather than fitting within it.
    """
    expected_encoded = {384: 527, 512: 702, 1024: 1404, 2048: 2801}
    for buf_size, encoded_size in expected_encoded.items():
        told = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=buf_size))
        auto = SMPSerialTransport()
        auto.initialize(buf_size)

        for t in (told, auto):
            on_wire = await _frame_on_the_wire(t, b"\x5a" * t.max_unencoded_size)
            assert len(on_wire) == encoded_size
            assert len(on_wire) > buf_size  # more encoded bytes on the wire than the buffer holds


def test_initialize_with_buffer_size_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A BufferSize larger than the server's advertised buffer warns; the manual size wins."""
    t = SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=1024))

    with caplog.at_level(logging.WARNING):
        t.initialize(512)

    assert any(
        "exceeds the server's advertised buffer size" in record.message for record in caplog.records
    )
    assert t.max_unencoded_size == 1024 - FRAME_OVERHEAD


def test_fragmentation_strategy_alias() -> None:
    """`FragmentationStrategy` is the union of the three strategy types."""
    assert set(get_args(FragmentationStrategy)) == {Auto, BufferSize, BufferParams}


@pytest.mark.parametrize(
    "make, mtu, line_length, line_buffers",
    [
        pytest.param(
            lambda: SMPSerialTransport(
                max_smp_encoded_frame_size=512, line_length=128, line_buffers=4
            ),
            512,
            128,
            4,
            id="kw-frame-ll-lb",
        ),
        pytest.param(
            lambda: SMPSerialTransport(line_length=64, line_buffers=4),
            256,  # max_smp_encoded_frame_size defaults to the 7.1.0 256
            64,
            4,
            id="kw-ll-lb-defaults-frame-256",
        ),
        # 7.1.0 positional layout was (max_smp_encoded_frame_size, line_length, line_buffers),
        # with the line_length=128, line_buffers=2 defaults.
        pytest.param(lambda: SMPSerialTransport(256), 256, 128, 2, id="pos-frame"),
        pytest.param(lambda: SMPSerialTransport(256, 128, 2), 256, 128, 2, id="pos-triple"),
        # A frame size larger than line_length * line_buffers still drives mtu (as in 7.1.0),
        # rather than being silently downgraded to the 128 * 2 == 256 budget.
        pytest.param(lambda: SMPSerialTransport(512), 512, 128, 2, id="pos-frame-gt-budget"),
        pytest.param(
            lambda: SMPSerialTransport(max_smp_encoded_frame_size=1024),
            1024,
            128,
            2,
            id="kw-frame-only-gt-budget",
        ),
    ],
)
def test_deprecated_params_reproduce_7_1_0(
    make: Callable[[], SMPSerialTransport], mtu: int, line_length: int, line_buffers: int
) -> None:
    """The deprecated 7.1.0 params still construct, warn, and keep 7.1.0 sizing.

    `mtu` is the explicit `max_smp_encoded_frame_size` (defaulting to the 7.1.0 256),
    independent of `line_length * line_buffers` -- not silently downgraded to the budget.
    """
    with pytest.warns(DeprecationWarning, match="fragmentation_strategy"):
        t = make()
    assert t.mtu == mtu
    assert t._line_length == line_length
    assert t._line_buffers == line_buffers


@pytest.mark.parametrize(
    "frame_size, expected_mtu, expected_max_unencoded",
    # Values from smpclient 7.1.0 (default line_length=128, line_buffers=2):
    # mtu == max_smp_encoded_frame_size, max_unencoded == _base64_max(mtu) - framing(2).
    [(256, 256, 169), (512, 512, 361), (1024, 1024, 745)],
)
def test_deprecated_frame_size_matches_7_1_0_throughput(
    frame_size: int, expected_mtu: int, expected_max_unencoded: int
) -> None:
    """A legacy `max_smp_encoded_frame_size` yields the exact 7.1.0 mtu/max_unencoded_size.

    Guards against the regression where the frame size was downgraded to 128 * 2 == 256
    (which halved, or worse, the per-request payload for upgraders).
    """
    with pytest.warns(DeprecationWarning):
        t = SMPSerialTransport(max_smp_encoded_frame_size=frame_size)
    assert t.mtu == expected_mtu
    assert t.max_unencoded_size == expected_max_unencoded


def test_deprecated_params_match_equivalent_buffer_params() -> None:
    """A *consistent* deprecated call (frame == line_length*line_buffers) equals its BufferParams."""
    with pytest.warns(DeprecationWarning):
        legacy = SMPSerialTransport(max_smp_encoded_frame_size=512, line_length=128, line_buffers=4)
    modern = SMPSerialTransport(
        fragmentation_strategy=BufferParams(line_length=128, line_buffers=4)
    )

    assert legacy.mtu == modern.mtu  # 512 == 128 * 4
    assert legacy.max_unencoded_size == modern.max_unencoded_size
    assert legacy._line_length == modern._line_length
    assert legacy._line_buffers == modern._line_buffers


def test_deprecated_frame_size_mismatch_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A frame size disagreeing with line_length*line_buffers is logged but still drives mtu.

    7.1.0 logged the mismatch (WARNING when greater, ERROR when smaller) and kept using the
    explicit max_smp_encoded_frame_size; this reproduces that, rather than downgrading mtu.
    """
    with caplog.at_level(logging.WARNING), pytest.warns(DeprecationWarning):
        t = SMPSerialTransport(max_smp_encoded_frame_size=512, line_length=128, line_buffers=2)
    assert any("is not equal to" in record.message for record in caplog.records)
    assert t.mtu == 512  # the explicit frame size wins, as in 7.1.0 (not 128 * 2 == 256)

    caplog.clear()
    with caplog.at_level(logging.ERROR), pytest.warns(DeprecationWarning):
        t = SMPSerialTransport(max_smp_encoded_frame_size=64, line_length=128, line_buffers=2)
    assert any(
        record.levelno == logging.ERROR and "is less than" in record.message
        for record in caplog.records
    )
    assert t.mtu == 64  # still honored, as in 7.1.0


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: SMPSerialTransport(), id="auto-default"),
        pytest.param(lambda: SMPSerialTransport(fragmentation_strategy=Auto()), id="auto-explicit"),
        pytest.param(lambda: SMPSerialTransport(BufferSize(buf_size=1024)), id="buffersize"),
        pytest.param(
            lambda: SMPSerialTransport(BufferParams(line_length=128, line_buffers=4)),
            id="bufferparams",
        ),
    ],
)
def test_modern_constructors_do_not_warn(make: Callable[[], SMPSerialTransport]) -> None:
    """The modern fragmentation_strategy API must never emit a DeprecationWarning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        make()


def test_explicit_strategy_wins_over_stray_legacy_args(caplog: pytest.LogCaptureFixture) -> None:
    """An explicit strategy is returned as-is (never the legacy path), but stray args are logged."""
    resolve = SMPSerialTransport._resolve_fragmentation_strategy
    with warnings.catch_warnings(), caplog.at_level(logging.WARNING):
        warnings.simplefilter("error", DeprecationWarning)  # the explicit strategy must not warn
        assert resolve(BufferSize(buf_size=1024), None, 64, None) == BufferSize(buf_size=1024)
        assert resolve(Auto(), 999, 64, 8) == Auto()
    # the silently-dropped legacy args are surfaced rather than ignored without a trace
    assert any("ignoring deprecated" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "strategy",
    [
        pytest.param(BufferSize(buf_size=4), id="buffersize-buf-eq-overhead"),
        pytest.param(BufferSize(buf_size=2), id="buffersize-buf-lt-overhead"),
        pytest.param(BufferSize(buf_size=1024, line_length=4), id="buffersize-line-too-small"),
        pytest.param(BufferParams(line_length=4, line_buffers=2), id="bufferparams-line-too-small"),
        pytest.param(BufferParams(line_length=16, line_buffers=1), id="bufferparams-neg-budget"),
        pytest.param(BufferParams(line_length=128, line_buffers=0), id="bufferparams-zero-buffers"),
    ],
)
def test_invalid_strategy_raises_value_error(strategy: FragmentationStrategy) -> None:
    """The modern API rejects sizes that would hang the encoder or yield a non-positive payload."""
    with pytest.raises(ValueError):
        SMPSerialTransport(fragmentation_strategy=strategy)


@pytest.mark.parametrize(
    "strategy",
    [
        pytest.param(Auto(), id="auto"),
        pytest.param(BufferSize(buf_size=5), id="buffersize-min"),
        pytest.param(BufferSize(buf_size=2048), id="buffersize"),
        pytest.param(BufferSize(buf_size=384, line_length=8), id="buffersize-min-line-length"),
        pytest.param(BufferParams(line_length=128, line_buffers=1), id="bufferparams"),
    ],
)
def test_valid_strategy_does_not_raise(strategy: FragmentationStrategy) -> None:
    """Valid strategies construct and report a positive max_unencoded_size."""
    t = SMPSerialTransport(fragmentation_strategy=strategy)
    assert t.max_unencoded_size > 0


def test_auto_rejects_tiny_server_buffer() -> None:
    """Auto raises if the server advertises a buffer too small to hold a framed message."""
    t = SMPSerialTransport()
    with pytest.raises(ValueError, match="frame overhead"):
        t.initialize(FRAME_OVERHEAD)  # buf_size == overhead -> zero-byte payload
