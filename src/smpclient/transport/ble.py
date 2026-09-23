"""A Bluetooth Low Energy (BLE) SMPTransport."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final, NamedTuple, Protocol, TypeAlias, TypeGuard, TypeVar
from uuid import UUID

try:
    from bleak import BleakClient, BleakScanner
    from bleak.args.bluez import BlueZClientArgs, BlueZScannerArgs
    from bleak.args.winrt import WinRTClientArgs
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.client import BaseBleakClient
    from bleak.backends.device import BLEDevice
except ModuleNotFoundError as e:
    if e.name == "bleak":
        raise ImportError("BLE transport requires the 'ble' extra. Use smpclient[ble]") from e
    raise
from smp import header as smphdr
from typing_extensions import Self, assert_never, override

from smpclient import _request
from smpclient.exceptions import SMPClientException
from smpclient.transport import (
    SMP_CHARACTERISTIC_UUID,
    SMP_SERVICE_UUID,
    Auto,
    GATTFragmentationStrategy,
    SMPTransportDisconnected,
    _GATTTransport,
)

if TYPE_CHECKING:
    from types_bits import u8

if sys.platform == "linux":
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
else:  # stub for mypy

    class BleakClientBlueZDBus(Protocol):
        async def _acquire_mtu(self) -> None: ...


if sys.platform == "win32":
    from bleak.backends.winrt.client import BleakClientWinRT
else:  # stub for mypy

    class GattSession(Protocol):
        max_pdu_size: int

    class BleakClientWinRT(Protocol):
        @property
        def _session(self) -> GattSession: ...


_ClientBackend: TypeAlias = BaseBleakClient | BleakClientBlueZDBus | BleakClientWinRT
"""Any `BleakClient._backend`: the platform's real backend, plus the off-platform stubs.

On each platform one of `BleakClientBlueZDBus`/`BleakClientWinRT` is bleak's real
`BaseBleakClient` subclass and the other is the local `Protocol` stub, so the backend
predicates below must accept the union to narrow either one.
"""


MAC_ADDRESS_PATTERN: Final = re.compile(r"([0-9A-F]{2}[:]){5}[0-9A-F]{2}$", flags=re.IGNORECASE)
UUID_PATTERN: Final = re.compile(
    r"^[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}\Z",
    flags=re.IGNORECASE,
)


class PlatformDefault(NamedTuple):
    """bleak's defaults for the platform's backend."""


class BlueZ(NamedTuple):
    """Options for bleak's BlueZ backend (Linux), e.g. the `adapter` to scan and connect with."""

    args: BlueZClientArgs


class WinRT(NamedTuple):
    """Options for bleak's WinRT backend (Windows), e.g. `use_cached_services`."""

    args: WinRTClientArgs


BleakBackend: TypeAlias = PlatformDefault | BlueZ | WinRT
"""The bleak backend options that `SMPBLETransport` scans and connects with."""


def _bluez_args(backend: BleakBackend) -> BlueZClientArgs:
    match backend:
        case BlueZ(args=args):
            return args
        case PlatformDefault() | WinRT():
            return {}
        case _ as unreachable:
            assert_never(unreachable)


def _winrt_args(backend: BleakBackend) -> WinRTClientArgs:
    match backend:
        case WinRT(args=args):
            return args
        case PlatformDefault() | BlueZ():
            return {}
        case _ as unreachable:
            assert_never(unreachable)


class SMPBLETransportException(SMPClientException):
    """Base class for SMP BLE transport exceptions."""


class SMPBLETransportDeviceNotFound(SMPBLETransportException):
    """Raised when a BLE device is not found."""


class SMPBLETransportNotSMPServer(SMPBLETransportException):
    """Raised when the SMP characteristic UUID is not found."""


logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_BORROWED_DISCONNECT_POLL_S: Final = 0.1
"""How often a wait on a borrowed client checks `is_connected`; its owner holds the callback."""


class _Closed(NamedTuple):
    """No link: not yet connected, disconnected, or a borrowed client returned."""


class _Owned(NamedTuple):
    """The link is the transport's own `BleakClient`, which `connect()` created."""

    client: BleakClient


class _Borrowed(NamedTuple):
    """The link is a caller's connected `BleakClient`, which the caller disconnects."""

    client: BleakClient


_Link: TypeAlias = _Closed | _Owned | _Borrowed


class SMPBLETransport(_GATTTransport):
    """A Bluetooth Low Energy (BLE) SMPTransport."""

    def __init__(
        self,
        *,
        backend: BleakBackend = PlatformDefault(),
        fragmentation_strategy: GATTFragmentationStrategy = Auto(),
        connect_timeout_s: float = 2.5,
        sequence: Callable[[], Iterator[u8]] = _request.wrapping_sequence,
    ) -> None:
        """Initialize the BLE transport.

        Args:
            backend: The bleak backend options to scan and connect with.
            fragmentation_strategy: How to size SMP messages.
            connect_timeout_s: Bounds scanning and connecting, and reading the server's
                MCUmgr parameters.
            sequence: The SMP sequence space the MCUmgr parameters read draws from.
        """
        super().__init__(fragmentation_strategy, connect_timeout_s, sequence)
        self._buffer: Final = bytearray()
        self._notify_condition: Final = asyncio.Condition()
        self._disconnected_event: Final = asyncio.Event()
        self._disconnected_event.set()
        self._backend: Final = backend
        self._link: _Link = _Closed()

        self._max_write_without_response_size = 20
        """Initially set to BLE minimum; may be mutated by the `connect()` method."""

        logger.debug(f"Initialized {self.__class__.__name__}")

    async def connect(self, address: str) -> None:
        """Scan for and connect to `address`, then `negotiate()`; prefer `connected()`.

        Args:
            address: The device's MAC address, macOS UUID, or advertised name.
        """  # noqa: DOC501, DOC503
        try:
            await asyncio.wait_for(
                self._connect(address, self._connect_timeout_s),
                timeout=self._connect_timeout_s,
            )
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            await self._best_effort_disconnect()
            raise

    @asynccontextmanager
    async def connected(self, address: str) -> AsyncIterator[Self]:
        """Connect to `address` for the duration of the `async with`, then disconnect."""
        await self.connect(address)
        async with self._released_on_exit():
            yield self

    async def _connect(self, address: str, timeout_s: float) -> None:
        logger.debug(f"Scanning for {address=}")
        device: BLEDevice | None = (
            await BleakScanner.find_device_by_address(
                address, timeout=timeout_s, bluez=BlueZScannerArgs(**_bluez_args(self._backend))
            )
            if MAC_ADDRESS_PATTERN.match(address) or UUID_PATTERN.match(address)
            else await BleakScanner.find_device_by_name(
                address, timeout=timeout_s, bluez=BlueZScannerArgs(**_bluez_args(self._backend))
            )
        )

        if type(device) is BLEDevice:
            self._link = _Owned(
                BleakClient(
                    device,
                    services=(str(SMP_SERVICE_UUID),),
                    winrt=_winrt_args(self._backend),
                    bluez=_bluez_args(self._backend),
                    timeout=timeout_s,
                    disconnected_callback=self._set_disconnected_event,
                )
            )
        else:
            raise SMPBLETransportDeviceNotFound(f"Device '{address}' not found")

        logger.debug(f"Found device: {device=}, connecting...")
        await self._active_client.connect()
        self._disconnected_event.clear()
        logger.debug(f"Connected to {device=}")
        await self._start_smp()

    async def borrow(self, client: BleakClient) -> None:
        """Adopt the caller's connected `client`, then `negotiate()`; prefer `borrowed()`."""
        self._link = _Borrowed(client)
        try:
            await self._start_smp()
            await self.negotiate()
        except (Exception, asyncio.CancelledError):
            await self.disconnect()
            raise

    @asynccontextmanager
    async def borrowed(self, client: BleakClient) -> AsyncIterator[Self]:
        """Borrow the caller's connected `client` for the duration of the `async with`."""
        await self.borrow(client)
        async with self._released_on_exit():
            yield self

    @property
    def _active_client(self) -> BleakClient:
        match self._link:
            case _Closed():
                raise SMPTransportDisconnected(f"{self.__class__.__name__} is not connected")
            case _Owned(client=client) | _Borrowed(client=client):
                return client
            case _ as unreachable:
                assert_never(unreachable)

    async def _start_smp(self) -> None:
        """Find the SMP characteristic, size writes to the link, and subscribe to it."""
        self._buffer.clear()
        smp_characteristic = self._active_client.services.get_characteristic(
            SMP_CHARACTERISTIC_UUID
        )
        if smp_characteristic is None:
            raise SMPBLETransportNotSMPServer("Missing the SMP characteristic UUID.")

        logger.debug(f"Found SMP characteristic: {smp_characteristic=}")
        logger.info(f"{smp_characteristic.max_write_without_response_size=}")
        self._max_write_without_response_size = smp_characteristic.max_write_without_response_size
        if (
            self._winrt_backend(self._active_client._backend)
            and self._max_write_without_response_size == 20
        ):
            # https://github.com/hbldh/bleak/pull/1552#issuecomment-2105573291
            logger.warning(
                "The SMP characteristic MTU is 20 bytes, possibly a Windows bug, checking again"
            )
            await asyncio.sleep(2)
            smp_characteristic._max_write_without_response_size = (  # pyright: ignore[reportAttributeAccessIssue]
                self._active_client._backend._session.max_pdu_size - 3  # type: ignore
            )
            self._max_write_without_response_size = (
                smp_characteristic.max_write_without_response_size
            )
            logger.warning(f"{smp_characteristic.max_write_without_response_size=}")
        elif self._bluez_backend(self._active_client._backend):
            logger.debug("Getting MTU from BlueZ backend")
            await self._active_client._backend._acquire_mtu()
            logger.debug(f"Got MTU: {self._active_client.mtu_size}")
            self._max_write_without_response_size = self._active_client.mtu_size - 3

        logger.info(f"{self._max_write_without_response_size=}")
        self._smp_characteristic = smp_characteristic

        logger.debug(f"Starting notify on {SMP_CHARACTERISTIC_UUID=}")
        await self._await_or_disconnect(
            self._active_client.start_notify(SMP_CHARACTERISTIC_UUID, self._notify_callback)
        )
        logger.debug(f"Started notify on {SMP_CHARACTERISTIC_UUID=}")

    @override
    async def disconnect(self) -> None:
        match self._link:
            case _Closed():
                pass
            case _Owned(client=client):
                logger.debug(f"Disonnecting from {client.address}")
                self._link = _Closed()
                await client.disconnect()
                logger.debug(f"Disconnected from {client.address}")
            case _Borrowed(client=client):
                logger.debug(f"Returning the borrowed client for {client.address}")
                self._link = _Closed()
                try:
                    await asyncio.wait_for(
                        client.stop_notify(SMP_CHARACTERISTIC_UUID), timeout=self._connect_timeout_s
                    )
                except Exception as e:
                    logger.warning(f"Error unsubscribing from the borrowed client: {e}")
            case _ as unreachable:
                assert_never(unreachable)

    @override
    async def send(self, data: bytes) -> None:
        logger.debug(f"Sending {len(data)} bytes, {self.mtu=}")
        for offset in range(0, len(data), self.mtu):
            await self._active_client.write_gatt_char(
                self._smp_characteristic, data[offset : offset + self.mtu], response=False
            )
        logger.debug(f"Sent {len(data)} bytes")

    @override
    async def receive(self) -> bytes:
        # Note: self._buffer is mutated asynchronously by this method and self._notify_callback().
        #       self._notify_condition is used to synchronize access to self._buffer.

        async with self._notify_condition:  # wait for the header
            while len(self._buffer) < smphdr.Header.SIZE:
                logger.debug(f"Waiting for notify on {SMP_CHARACTERISTIC_UUID=}")
                await self._notify_or_disconnect()

            header: Final = smphdr.Header.loads(bytes(self._buffer[: smphdr.Header.SIZE]))
            logger.debug(f"Received {header=}")

        message_length: Final = header.length + header.SIZE
        logger.debug(f"Waiting for the rest of the {message_length} byte response")

        while True:  # wait for the rest of the message
            async with self._notify_condition:
                if len(self._buffer) == message_length:
                    logger.debug(f"Finished receiving {message_length} byte response")
                    out = bytes(self._buffer)
                    self._buffer.clear()
                    return out
                elif len(self._buffer) > message_length:  # pragma: no cover
                    raise SMPBLETransportException("Length of buffer passed expected message size.")
                await self._notify_or_disconnect()

    async def _notify_callback(self, sender: BleakGATTCharacteristic, data: bytearray) -> None:
        if sender.uuid != str(SMP_CHARACTERISTIC_UUID):  # pragma: no cover
            raise SMPBLETransportException(f"Unexpected notify from {sender}; {data=}")
        async with self._notify_condition:
            logger.debug(f"Received {len(data)} bytes from {SMP_CHARACTERISTIC_UUID=}")
            self._buffer.extend(data)
            self._notify_condition.notify()

    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @property
    @override
    def mtu(self) -> int:
        return self._max_write_without_response_size

    @staticmethod
    async def scan(timeout: int = 5, backend: BleakBackend = PlatformDefault()) -> list[BLEDevice]:
        """Scan for BLE devices with the bleak `backend` options."""
        logger.debug(f"Scanning for BLE devices for {timeout} seconds")
        devices: Final = await BleakScanner(
            service_uuids=[str(SMP_SERVICE_UUID)], bluez=BlueZScannerArgs(**_bluez_args(backend))
        ).discover(timeout=timeout, return_adv=True)
        smp_servers: Final = [
            d for d, a in devices.values() if SMP_SERVICE_UUID in {UUID(u) for u in a.service_uuids}
        ]
        logger.debug(f"Found {len(smp_servers)} SMP devices: {smp_servers=}")
        return smp_servers

    @staticmethod
    def _bluez_backend(client_backend: _ClientBackend) -> TypeGuard[BleakClientBlueZDBus]:
        return client_backend.__class__.__name__ == "BleakClientBlueZDBus"

    @staticmethod
    def _winrt_backend(client_backend: _ClientBackend) -> TypeGuard[BleakClientWinRT]:
        return client_backend.__class__.__name__ == "BleakClientWinRT"

    def _set_disconnected_event(self, client: BleakClient) -> None:
        match self._link:
            case _Owned(client=owned) if owned is not client:
                raise SMPBLETransportException(
                    f"Unexpected client disconnected: {client=}, {owned=}"
                )
            case _Closed() | _Owned() | _Borrowed():
                pass
            case _ as unreachable:
                assert_never(unreachable)
        logger.warning(f"Disconnected from {client.address}")
        self._disconnected_event.set()

    async def _until_disconnected(self) -> None:
        match self._link:
            case _Closed():
                pass
            case _Owned():
                await self._disconnected_event.wait()
            case _Borrowed(client=client):
                while client.is_connected:
                    await asyncio.sleep(_BORROWED_DISCONNECT_POLL_S)
            case _ as unreachable:
                assert_never(unreachable)

    async def _notify_or_disconnect(self) -> None:
        disconnected_task: Final = asyncio.create_task(self._until_disconnected())
        notify_task: Final = asyncio.create_task(self._notify_condition.wait())
        try:
            done, _ = await asyncio.wait(
                (disconnected_task, notify_task), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (disconnected_task, notify_task):
                task.cancel()
            await asyncio.gather(disconnected_task, notify_task, return_exceptions=True)
        if disconnected_task in done:
            raise SMPTransportDisconnected(
                f"{self.__class__.__name__} disconnected from {self._active_client.address}"
            )

    async def _await_or_disconnect(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Await `coro`; raise `SMPTransportDisconnected` if the peer disconnects first.

        Guards GATT operations that can hang indefinitely when the peer
        disconnects mid-flow (e.g. failed pairing) — see
        https://github.com/intercreate/smpmgr/issues/97.
        """
        op_task: Final = asyncio.create_task(coro)
        disconnected_task: Final = asyncio.create_task(self._until_disconnected())
        try:
            done, _ = await asyncio.wait(
                (op_task, disconnected_task), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (op_task, disconnected_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(op_task, disconnected_task, return_exceptions=True)
        if disconnected_task in done:
            raise SMPTransportDisconnected(
                f"{self.__class__.__name__} disconnected from {self._active_client.address}"
            )
        return op_task.result()

    async def _best_effort_disconnect(self) -> None:
        """Best-effort cleanup after a failed `connect()`; never raises."""
        try:
            await self.disconnect()
        except Exception:
            logger.warning("Best-effort disconnect after failed connect raised", exc_info=True)
