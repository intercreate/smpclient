"""Shared connection management for the encoded and unencoded serial transports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from time import monotonic
from typing import TYPE_CHECKING, Final, Generator, NamedTuple, Protocol, TypeAlias, final

try:
    from serial import Serial, SerialException
except ModuleNotFoundError as e:
    if e.name == "serial":
        raise ImportError(
            "Serial transport requires the 'serial' extra. Use smpclient[serial]"
        ) from e
    raise
from typing_extensions import Self, assert_never, override

from smpclient.transport import SMPTransportDisconnected, _ConnectableTransport, _TStrategy

if TYPE_CHECKING:
    from _typeshed import ReadableBuffer
    from types_bits import u8

logger = logging.getLogger(__name__)


class SerialPort(Protocol):
    """The part of an open `pyserial` port that the serial transports use.

    Satisfied by `serial.Serial`, and by a `serial.serial_for_url` port that reports
    `out_waiting`.
    """

    @property
    def port(self) -> str | None: ...  # pragma: no cover
    @property
    def out_waiting(self) -> int: ...  # pragma: no cover
    def write(self, b: ReadableBuffer, /) -> int | None: ...  # pragma: no cover
    def read_all(self) -> bytes | None: ...  # pragma: no cover


class _Owned(NamedTuple):
    """The link is the transport's own `Serial`, which `connect()` opens."""


class _Borrowed(NamedTuple):
    """The link is a caller's open port, which the caller closes."""

    port: SerialPort


_Link: TypeAlias = _Owned | _Borrowed


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


class _SerialTransportBase(_ConnectableTransport[_TStrategy]):
    """Connection-management base class for serial-port-backed SMP transports.

    Holds the `pyserial` `Serial` instance, the open/retry connect loop, borrowing a
    caller's open port (e.g. an emulator's `socket://` chardev), disconnect, and the small
    TX/RX helpers that wrap `SerialException` into `SMPTransportDisconnected`.

    Subclasses implement `send` and `receive` with their framing of choice, and may
    override `_reset_state` to clear per-connection state on `connect` and `borrow`.
    """

    _POLLING_INTERVAL_S: Final = 0.005
    _CONNECTION_RETRY_INTERVAL_S: Final = 0.500

    def __init__(
        self,
        fragmentation_strategy: _TStrategy,
        connect_timeout_s: float,
        sequence: Callable[[], Iterator[u8]],
        options: SerialOptions,
    ) -> None:
        """Hold a closed `Serial` with the `options` until `connect()` opens it."""
        super().__init__(fragmentation_strategy, connect_timeout_s, sequence)
        self._serial: Final = Serial(**options._asdict())
        self._link: _Link = _Owned()

    @property
    def _conn(self) -> SerialPort:
        match self._link:
            case _Owned():
                return self._serial
            case _Borrowed(port=port):
                return port
            case _ as unreachable:
                assert_never(unreachable)

    def _reset_state(self) -> None:
        """Reset any per-connection state. Subclasses override as needed."""

    async def connect(self, port: str) -> None:
        """Open `port`, then `negotiate()`; prefer `connected()`."""
        try:
            await self._open(port)
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            self._serial.close()
            raise

    async def borrow(self, port: SerialPort) -> None:
        """Adopt the caller's open `port`, then `negotiate()`; prefer `borrowed()`."""
        self._reset_state()
        self._link = _Borrowed(port)
        try:
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            self._link = _Owned()
            raise

    @asynccontextmanager
    async def connected(self, port: str) -> AsyncIterator[Self]:
        """Open `port` for the duration of the `async with`, then close it."""
        await self.connect(port)
        async with self._released_on_exit():
            yield self

    @asynccontextmanager
    async def borrowed(self, port: SerialPort) -> AsyncIterator[Self]:
        """Borrow the caller's open `port` for the duration of the `async with`."""
        await self.borrow(port)
        async with self._released_on_exit():
            yield self

    async def _open(self, port: str) -> None:
        """Open `port` off the event loop, retrying until `connect_timeout_s`."""
        self._reset_state()
        self._serial.port = port
        logger.debug(f"Connecting to {self._serial.port=}")
        start_time: Final = monotonic()
        while monotonic() - start_time <= self._connect_timeout_s:
            try:
                await asyncio.to_thread(self._serial.open)
            except SerialException as e:
                logger.debug(
                    f"Failed to connect to {self._serial.port=}: {e}, "
                    f"retrying in {self._CONNECTION_RETRY_INTERVAL_S} seconds"
                )
                await asyncio.sleep(self._CONNECTION_RETRY_INTERVAL_S)
            else:
                await asyncio.to_thread(self._serial.reset_input_buffer)
                logger.debug(f"Connected to {self._serial.port=}")
                return

        raise TimeoutError(f"Failed to connect to {port=}")

    @final
    @override
    async def disconnect(self) -> None:
        match self._link:
            case _Owned():
                logger.debug(f"Disconnecting from {self._serial.port=}")
                self._serial.close()
                logger.debug(f"Disconnected from {self._serial.port=}")
            case _Borrowed(port=port):
                logger.debug(f"Returning the borrowed {port.port=}")
                self._link = _Owned()
            case _ as unreachable:
                assert_never(unreachable)

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
