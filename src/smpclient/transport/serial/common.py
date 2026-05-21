"""Shared connection management for the encoded and unencoded serial transports."""

import asyncio
import logging
from contextlib import contextmanager
from time import monotonic
from typing import Final, Generator, final

try:
    from serial import Serial, SerialException
except ModuleNotFoundError as e:
    if e.name == "serial":
        raise ImportError(
            "Serial transport requires the 'serial' extra. Use smpclient[serial]"
        ) from e
    raise
from typing_extensions import override

from smpclient.transport import SMPTransport, SMPTransportDisconnected

logger = logging.getLogger(__name__)


class _SerialTransportBase(SMPTransport):
    """Connection-management base class for serial-port-backed SMP transports.

    Holds the `pyserial` `Serial` instance, the open/retry connect loop, disconnect,
    and the small TX/RX helpers that wrap `SerialException` into
    `SMPTransportDisconnected`.

    Subclasses implement `send` and `receive` with their framing of choice, may
    override `_reset_state` to clear per-connection state on `connect`, and may
    override `connect` to back the transport with a byte pipe other than a local
    serial port (e.g. an emulator's `socket://` chardev).
    """

    _POLLING_INTERVAL_S: Final = 0.005
    _CONNECTION_RETRY_INTERVAL_S: Final = 0.500

    def __init__(
        self,
        baudrate: int = 115200,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout: float | None = None,
        xonxoff: bool = False,
        rtscts: bool = False,
        write_timeout: float | None = None,
        dsrdtr: bool = False,
        inter_byte_timeout: float | None = None,
        exclusive: bool | None = None,
    ) -> None:
        """Initialize the underlying `pyserial` `Serial` instance.

        Args:
            baudrate: The baudrate of the serial connection.  OK to ignore for
                USB CDC ACM.
            bytesize: The number of data bits.
            parity: The parity setting.
            stopbits: The number of stop bits.
            timeout: The read timeout.
            xonxoff: Enable software flow control.
            rtscts: Enable hardware (RTS/CTS) flow control.
            write_timeout: The write timeout.
            dsrdtr: Enable hardware (DSR/DTR) flow control.
            inter_byte_timeout: The inter-byte timeout.
            exclusive: Set exclusive access mode (POSIX only).  A port cannot be
                opened in exclusive access mode if it is already open in
                exclusive access mode.
        """
        self._conn: Final = Serial(
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            xonxoff=xonxoff,
            rtscts=rtscts,
            write_timeout=write_timeout,
            dsrdtr=dsrdtr,
            inter_byte_timeout=inter_byte_timeout,
            exclusive=exclusive,
        )

    def _reset_state(self) -> None:
        """Reset any per-connection state. Subclasses override as needed."""

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        self._reset_state()
        self._conn.port = address
        logger.debug(f"Connecting to {self._conn.port=}")
        start_time: Final = monotonic()
        while monotonic() - start_time <= timeout_s:
            try:
                self._conn.open()
                self._conn.reset_input_buffer()
                logger.debug(f"Connected to {self._conn.port=}")
                return
            except SerialException as e:
                logger.debug(
                    f"Failed to connect to {self._conn.port=}: {e}, "
                    f"retrying in {self._CONNECTION_RETRY_INTERVAL_S} seconds"
                )
                await asyncio.sleep(self._CONNECTION_RETRY_INTERVAL_S)

        raise TimeoutError(f"Failed to connect to {address=}")

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
