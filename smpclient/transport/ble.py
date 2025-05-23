"""A Bluetooth Low Energy (BLE) SMPTransport."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Final, List, Protocol
from uuid import UUID

from bleak import BleakClient, BleakGATTCharacteristic, BleakScanner
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from smp import header as smphdr
from typing_extensions import TypeGuard, override

from smpclient.exceptions import SMPClientException
from smpclient.transport import SMPTransport, SMPTransportDisconnected

if sys.platform == "linux":
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
else:  # stub for mypy

    class BleakClientBlueZDBus(Protocol):
        async def _acquire_mtu(self) -> None:
            ...


if sys.platform == "win32":
    from bleak.backends.winrt.client import BleakClientWinRT
else:  # stub for mypy

    class GattSession(Protocol):
        max_pdu_size: int

    class BleakClientWinRT(Protocol):
        @property
        def _session(self) -> GattSession:
            ...


SMP_SERVICE_UUID: Final = UUID("8D53DC1D-1DB7-4CD3-868B-8A527460AA84")
SMP_CHARACTERISTIC_UUID: Final = UUID("DA2E7828-FBCE-4E01-AE9E-261174997C48")

MAC_ADDRESS_PATTERN: Final = re.compile(r"([0-9A-F]{2}[:]){5}[0-9A-F]{2}$", flags=re.IGNORECASE)
UUID_PATTERN: Final = re.compile(
    r"^[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}\Z",
    flags=re.IGNORECASE,
)


class SMPBLETransportException(SMPClientException):
    """Base class for SMP BLE transport exceptions."""


class SMPBLETransportDeviceNotFound(SMPBLETransportException):
    """Raised when a BLE device is not found."""


class SMPBLETransportNotSMPServer(SMPBLETransportException):
    """Raised when the SMP characteristic UUID is not found."""


logger = logging.getLogger(__name__)


class SMPBLETransport(SMPTransport):
    """A Bluetooth Low Energy (BLE) SMPTransport."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._notify_condition = asyncio.Condition()
        self._disconnected_event = asyncio.Event()
        self._disconnected_event.set()

        self._max_write_without_response_size = 20
        """Initially set to BLE minimum; may be mutated by the `connect()` method."""

        logger.debug(f"Initialized {self.__class__.__name__}")

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        logger.debug(f"Scanning for {address=}")
        device: BLEDevice | None = (
            await BleakScanner.find_device_by_address(address, timeout=timeout_s)
            if MAC_ADDRESS_PATTERN.match(address) or UUID_PATTERN.match(address)
            else await BleakScanner.find_device_by_name(address)
        )

        if type(device) is BLEDevice:
            self._client = BleakClient(
                device,
                services=(str(SMP_SERVICE_UUID),),
                disconnected_callback=self._set_disconnected_event,
            )
        else:
            raise SMPBLETransportDeviceNotFound(f"Device '{address}' not found")

        logger.debug(f"Found device: {device=}, connecting...")
        await self._client.connect()
        self._disconnected_event.clear()
        logger.debug(f"Connected to {device=}")

        smp_characteristic = self._client.services.get_characteristic(SMP_CHARACTERISTIC_UUID)
        if smp_characteristic is None:
            raise SMPBLETransportNotSMPServer("Missing the SMP characteristic UUID.")

        logger.debug(f"Found SMP characteristic: {smp_characteristic=}")
        logger.info(f"{smp_characteristic.max_write_without_response_size=}")
        self._max_write_without_response_size = smp_characteristic.max_write_without_response_size
        if (
            self._winrt_backend(self._client._backend)
            and self._max_write_without_response_size == 20
        ):
            # https://github.com/hbldh/bleak/pull/1552#issuecomment-2105573291
            logger.warning(
                "The SMP characteristic MTU is 20 bytes, possibly a Windows bug, checking again"
            )
            await asyncio.sleep(2)
            smp_characteristic._max_write_without_response_size = (
                self._client._backend._session.max_pdu_size - 3  # type: ignore
            )
            self._max_write_without_response_size = (
                smp_characteristic.max_write_without_response_size
            )
            logger.warning(f"{smp_characteristic.max_write_without_response_size=}")
        elif self._bluez_backend(self._client._backend):
            logger.debug("Getting MTU from BlueZ backend")
            await self._client._backend._acquire_mtu()
            logger.debug(f"Got MTU: {self._client.mtu_size}")
            self._max_write_without_response_size = self._client.mtu_size - 3

        logger.info(f"{self._max_write_without_response_size=}")
        self._smp_characteristic = smp_characteristic

        logger.debug(f"Starting notify on {SMP_CHARACTERISTIC_UUID=}")
        await self._client.start_notify(SMP_CHARACTERISTIC_UUID, self._notify_callback)
        logger.debug(f"Started notify on {SMP_CHARACTERISTIC_UUID=}")

    @override
    async def disconnect(self) -> None:
        logger.debug(f"Disonnecting from {self._client.address}")
        await self._client.disconnect()
        logger.debug(f"Disconnected from {self._client.address}")

    @override
    async def send(self, data: bytes) -> None:
        logger.debug(f"Sending {len(data)} bytes, {self.mtu=}")
        for offset in range(0, len(data), self.mtu):
            await self._client.write_gatt_char(
                self._smp_characteristic, data[offset : offset + self.mtu], response=False
            )
        logger.debug(f"Sent {len(data)} bytes")

    @override
    async def receive(self) -> bytes:
        # Note: self._buffer is mutated asynchronously by this method and self._notify_callback().
        #       self._notify_condition is used to synchronize access to self._buffer.

        async with self._notify_condition:  # wait for the header
            logger.debug(f"Waiting for notify on {SMP_CHARACTERISTIC_UUID=}")
            await self._notify_or_disconnect()

            if len(self._buffer) < smphdr.Header.SIZE:  # pragma: no cover
                raise SMPBLETransportException(
                    f"Buffer contents not big enough for SMP header: {self._buffer=}"
                )

            header: Final = smphdr.Header.loads(self._buffer[: smphdr.Header.SIZE])
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

    async def _notify_callback(self, sender: BleakGATTCharacteristic, data: bytes) -> None:
        if sender.uuid != str(SMP_CHARACTERISTIC_UUID):  # pragma: no cover
            raise SMPBLETransportException(f"Unexpected notify from {sender}; {data=}")
        async with self._notify_condition:
            logger.debug(f"Received {len(data)} bytes from {SMP_CHARACTERISTIC_UUID=}")
            self._buffer.extend(data)
            self._notify_condition.notify()

    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    @override
    @property
    def mtu(self) -> int:
        return self._max_write_without_response_size

    @staticmethod
    async def scan(timeout: int = 5) -> List[BLEDevice]:
        """Scan for BLE devices."""
        logger.debug(f"Scanning for BLE devices for {timeout} seconds")
        devices: Final = await BleakScanner(service_uuids=[str(SMP_SERVICE_UUID)]).discover(
            timeout=timeout, return_adv=True
        )
        smp_servers: Final = [
            d for d, a in devices.values() if SMP_SERVICE_UUID in {UUID(u) for u in a.service_uuids}
        ]
        logger.debug(f"Found {len(smp_servers)} SMP devices: {smp_servers=}")
        return smp_servers

    @staticmethod
    def _bluez_backend(client_backend: BaseBleakClient) -> TypeGuard[BleakClientBlueZDBus]:
        return client_backend.__class__.__name__ == "BleakClientBlueZDBus"

    @staticmethod
    def _winrt_backend(client_backend: BaseBleakClient) -> TypeGuard[BleakClientWinRT]:
        return client_backend.__class__.__name__ == "BleakClientWinRT"

    def _set_disconnected_event(self, client: BleakClient) -> None:
        if client is not self._client:
            raise SMPBLETransportException(
                f"Unexpected client disconnected: {client=}, {self._client=}"
            )
        logger.warning(f"Disconnected from {client.address}")
        self._disconnected_event.set()

    async def _notify_or_disconnect(self) -> None:
        disconnected_task: Final = asyncio.create_task(self._disconnected_event.wait())
        notify_task: Final = asyncio.create_task(self._notify_condition.wait())
        done, pending = await asyncio.wait(
            (disconnected_task, notify_task), return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        try:
            await asyncio.gather(*pending)
        except asyncio.CancelledError:
            pass
        if disconnected_task in done:
            raise SMPTransportDisconnected(
                f"{self.__class__.__name__} disconnected from {self._client.address}"
            )
