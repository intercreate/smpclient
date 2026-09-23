"""Shared connection management for the encoded and unencoded serial transports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import TYPE_CHECKING, Final, Generator, NamedTuple, final

try:
    from serial import Serial, SerialException
except ModuleNotFoundError as e:
    if e.name == "serial":
        raise ImportError(
            "Serial transport requires the 'serial' extra. Use smpclient[serial]"
        ) from e
    raise
from typing_extensions import override

from smpclient import _request
from smpclient.transport import SMPTransportDisconnected, _ConnectableTransport

if TYPE_CHECKING:
    from types_bits import u8

logger = logging.getLogger(__name__)


class SerialOptions(NamedTuple):
    """The `pyserial` port settings, named as `serial.Serial` names them."""

    baudrate: int = 115200
    """The baudrate of the serial connection.  OK to ignore for USB CDC ACM."""

    bytesize: int = 8
    """The number of data bits."""

    parity: str = "N"
    """The parity setting."""

    stopbits: float = 1
    """The number of stop bits."""

    timeout: float | None = None
    """The read timeout."""

    xonxoff: bool = False
    """Enable software flow control."""

    rtscts: bool = False
    """Enable hardware (RTS/CTS) flow control."""

    write_timeout: float | None = None
    """The write timeout."""

    dsrdtr: bool = False
    """Enable hardware (DSR/DTR) flow control."""

    inter_byte_timeout: float | None = None
    """The inter-byte timeout."""

    exclusive: bool | None = None
    """Set exclusive access mode (POSIX only).  A port cannot be opened in exclusive access
    mode if it is already open in exclusive access mode."""


class _SerialTransportBase(_ConnectableTransport):
    """Connection-management base class for serial-port-backed SMP transports.

    Holds the `pyserial` `Serial` instance, the open/retry connect loop, disconnect,
    and the small TX/RX helpers that wrap `SerialException` into
    `SMPTransportDisconnected`.

    Subclasses implement `send` and `receive` with their framing of choice, may
    override `_reset_state` to clear per-connection state on `connect`, and may
    override `_open` to back the transport with a byte pipe other than a local
    serial port (e.g. an emulator's `socket://` chardev).
    """

    _POLLING_INTERVAL_S: Final = 0.005
    _CONNECTION_RETRY_INTERVAL_S: Final = 0.500

    def __init__(
        self,
        port: str,
        connect_timeout_s: float = 2.5,
        sequence: Iterator[u8] | None = None,
        options: SerialOptions = SerialOptions(),
    ) -> None:
        """Initialize the underlying `pyserial` `Serial` instance.

        Args:
            port: The serial port, e.g. `/dev/ttyACM0` or `COM3`.
            connect_timeout_s: Bounds opening the port, and reading the server's MCUmgr
                parameters.
            sequence: The SMP sequence space the MCUmgr parameters read draws from;
                defaults to `wrapping_sequence()`.
            options: The `pyserial` port settings.
        """
        self._port: Final = port
        self._connect_timeout_s = connect_timeout_s
        self._sequence = _request.wrapping_sequence() if sequence is None else sequence
        self._conn: Final = Serial(**options._asdict())

    def _reset_state(self) -> None:
        """Reset any per-connection state. Subclasses override as needed."""

    @override
    async def connect(self) -> None:
        try:
            await self._open()
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            self._conn.close()
            raise

    async def _open(self) -> None:
        """Open the port off the event loop, retrying until `connect_timeout_s`."""
        self._reset_state()
        self._conn.port = self._port
        logger.debug(f"Connecting to {self._conn.port=}")
        start_time: Final = monotonic()
        while monotonic() - start_time <= self._connect_timeout_s:
            try:
                await asyncio.to_thread(self._conn.open)
            except SerialException as e:
                logger.debug(
                    f"Failed to connect to {self._conn.port=}: {e}, "
                    f"retrying in {self._CONNECTION_RETRY_INTERVAL_S} seconds"
                )
                await asyncio.sleep(self._CONNECTION_RETRY_INTERVAL_S)
            else:
                await asyncio.to_thread(self._conn.reset_input_buffer)
                logger.debug(f"Connected to {self._conn.port=}")
                return

        raise TimeoutError(f"Failed to connect to {self._port=}")

    @final
    @override
    async def disconnect(self) -> None:
        logger.debug(f"Disconnecting from {self._conn.port=}")
        self._conn.close()
        logger.debug(f"Disconnected from {self._conn.port=}")

    @final
    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @final
    @contextmanager
    def _serial_exception_to_disconnected(self) -> Generator[None, None, None]:
        """Translate `SerialException` from `pyserial` to `SMPTransportDisconnected`."""
        try:
            yield
        except SerialException as e:
            logger.error(f"Serial exception on {self._conn.port}: {e}")
            raise SMPTransportDisconnected(
                f"{self.__class__.__name__} disconnected from {self._conn.port}"
            ) from e

    @final
    async def _drain_tx(self) -> None:
        """Block until the serial TX buffer is empty.

        Fake-async polling until `pyserial` is replaced.
        """
        while self._conn.out_waiting > 0:
            await asyncio.sleep(self._POLLING_INTERVAL_S)

    @final
    async def _read_all(self) -> bytes:
        """Return all currently-available bytes (or empty bytes).

        Wraps `SerialException` into `SMPTransportDisconnected`. `StopIteration` is
        caught to keep mocked `read_all` side-effect lists usable in tests.
        """
        try:
            return self._conn.read_all() or b""
        except StopIteration:
            return b""
        except SerialException as exc:
            raise SMPTransportDisconnected(f"Failed to read from {self._conn.port}: {exc}") from exc
