"""Tests for `SMPBLETransport`."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from bleak import BleakClient, BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

from smpclient.requests.os_management import EchoWrite
from smpclient.transport.ble import (
    MAC_ADDRESS_PATTERN,
    SMP_CHARACTERISTIC_UUID,
    SMP_SERVICE_UUID,
    UUID_PATTERN,
    SMPBLETransport,
    SMPBLETransportDeviceNotFound,
)


class MockBleakClient:
    class Backend:
        ...

    def __new__(cls, *args, **kwargs) -> "MockBleakClient":  # type: ignore
        client = MagicMock(spec=BleakClient, name="MockBleakClient")
        client._backend = MockBleakClient.Backend()
        return client


def test_constructor() -> None:
    t = SMPBLETransport()
    assert t._buffer == bytearray()
    assert isinstance(t._notify_condition, asyncio.Condition)


def test_MAC_ADDRESS_PATTERN() -> None:
    assert MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00")
    assert MAC_ADDRESS_PATTERN.match("FF:FF:FF:FF:FF:FF")
    assert MAC_ADDRESS_PATTERN.match("00:FF:00:FF:00:FF")
    assert MAC_ADDRESS_PATTERN.match("FF:00:FF:00:FF:00")

    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:00")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:00:00")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:00:00:00")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:0G")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:0G")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:00:0G")
    assert not MAC_ADDRESS_PATTERN.match("00:00:00:00:00:00:00:00:0G")


def test_UUID_PATTERN() -> None:
    assert UUID_PATTERN.match("00000000-0000-4000-8000-000000000000")
    assert UUID_PATTERN.match("FFFFFFFF-FFFF-4FFF-9FFF-FFFFFFFFFFFF")
    assert UUID_PATTERN.match("0000FFFF-0000-4FFF-a000-FFFFFFFFFFFF")
    assert UUID_PATTERN.match("FFFF0000-FFFF-4000-bFFF-000000000000")

    assert UUID_PATTERN.match(UUID("00000000-0000-4000-8000-000000000000").hex)
    assert UUID_PATTERN.match(UUID("FFFFFFFF-FFFF-4FFF-9FFF-FFFFFFFFFFFF").hex)
    assert UUID_PATTERN.match(UUID("0000FFFF-0000-4FFF-a000-FFFFFFFFFFFF").hex)
    assert UUID_PATTERN.match(UUID("FFFF0000-FFFF-4000-bFFF-000000000000").hex)


def test_SMP_gatt_consts() -> None:
    assert SMP_CHARACTERISTIC_UUID == UUID("DA2E7828-FBCE-4E01-AE9E-261174997C48")
    assert SMP_SERVICE_UUID == UUID("8D53DC1D-1DB7-4CD3-868B-8A527460AA84")


@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_address",
    return_value=BLEDevice("address", "name", None, -60),
)
@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_name",
    return_value=BLEDevice("address", "name", None, -60),
)
@patch("smpclient.transport.ble.BleakClient", new=MockBleakClient)
@pytest.mark.asyncio
async def test_connect(
    mock_find_device_by_name: MagicMock,
    mock_find_device_by_address: MagicMock,
) -> None:
    # assert that it searches by name if MAC or UUID is not provided
    await SMPBLETransport().connect("device name", 1.0)
    mock_find_device_by_name.assert_called_once_with("device name")
    mock_find_device_by_name.reset_mock()

    # assert that it searches by MAC if MAC is provided
    await SMPBLETransport().connect("00:00:00:00:00:00", 1.0)
    mock_find_device_by_address.assert_called_once_with("00:00:00:00:00:00", timeout=1.0)
    mock_find_device_by_address.reset_mock()

    # assert that it searches by UUID if UUID is provided
    await SMPBLETransport().connect(UUID("00000000-0000-4000-8000-000000000000").hex, 1.0)
    mock_find_device_by_address.assert_called_once_with(
        "00000000000040008000000000000000", timeout=1.0
    )
    mock_find_device_by_address.reset_mock()

    # assert that it raises an exception if the device is not found
    mock_find_device_by_address.return_value = None
    with pytest.raises(SMPBLETransportDeviceNotFound):
        await SMPBLETransport().connect("00:00:00:00:00:00", 1.0)
    mock_find_device_by_address.reset_mock()

    # assert that connect is awaited
    t = SMPBLETransport()
    await t.connect("name", 1.0)
    t._client = cast(MagicMock, t._client)
    t._client.reset_mock()
    await t.connect("name", 1.0)
    t._client.connect.assert_awaited_once_with()

    # these are hard to mock now because the _client is created in the connect method
    # reenable these after the SMPTransport Protocol is updated to take address
    # at initialization rather than in the connect method - a BREAKING CHANGE

    # # assert that the SMP characteristic is checked
    # t._client.services.get_characteristic.assert_called_once_with(SMP_CHARACTERISTIC_UUID)

    # # assert that an exception is raised if the SMP characteristic is not found
    # t._client.services.get_characteristic.return_value = None
    # with pytest.raises(SMPBLETransportNotSMPServer):
    #     await t.connect("name", 1.0)
    # t._client.reset_mock()

    # # assert that the SMP characteristic is saved
    # m = MagicMock()
    # t._client.services.get_characteristic.return_value = m
    # await t.connect("name", 1.0)
    # assert t._smp_characteristic is m

    # assert that SMP characteristic notifications are started
    t._client.start_notify.assert_called_once_with(SMP_CHARACTERISTIC_UUID, t._notify_callback)


@pytest.mark.asyncio
async def test_disconnect() -> None:
    t = SMPBLETransport()
    t._client = MagicMock(spec=BleakClient)
    await t.disconnect()
    t._client.disconnect.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_send() -> None:
    t = SMPBLETransport()
    t._client = MagicMock(spec=BleakClient)
    t._smp_characteristic = MagicMock(spec=BleakGATTCharacteristic)
    t._smp_characteristic.max_write_without_response_size = 20
    await t.send(b"Hello pytest!")
    t._client.write_gatt_char.assert_awaited_once_with(
        t._smp_characteristic, b"Hello pytest!", response=False
    )


@pytest.mark.asyncio
async def test_receive() -> None:
    t = SMPBLETransport()
    t._client = MagicMock(spec=BleakClient)
    t._smp_characteristic = MagicMock(spec=BleakGATTCharacteristic)
    t._smp_characteristic.uuid = str(SMP_CHARACTERISTIC_UUID)
    t._disconnected_event.clear()  # pretend t.connect() was successful

    REP = EchoWrite._Response.get_default()(sequence=0, r="Hello pytest!").BYTES  # type: ignore

    b, _ = await asyncio.gather(
        t.receive(),
        t._notify_callback(t._smp_characteristic, REP),
    )

    assert b == REP

    # cool, now try with a fragmented response
    async def fragmented_notifies() -> None:
        await t._notify_callback(t._smp_characteristic, REP[:10])
        await asyncio.sleep(0.001)
        await t._notify_callback(t._smp_characteristic, REP[10:])

    b, _ = await asyncio.gather(
        t.receive(),
        fragmented_notifies(),
    )

    assert b == REP


@pytest.mark.asyncio
async def test_send_and_receive() -> None:
    t = SMPBLETransport()
    t.send = AsyncMock()  # type: ignore
    t.receive = AsyncMock()  # type: ignore
    await t.send_and_receive(b"Hello pytest!")
    t.send.assert_awaited_once_with(b"Hello pytest!")
    t.receive.assert_awaited_once_with()


def test_max_unencoded_size() -> None:
    t = SMPBLETransport()
    t._client = MagicMock(spec=BleakClient)
    t._max_write_without_response_size = 42
    assert t.max_unencoded_size == 42


def test_max_unencoded_size_mcumgr_param() -> None:
    t = SMPBLETransport()
    t._client = MagicMock(spec=BleakClient)
    t._smp_server_transport_buffer_size = 9001
    assert t.max_unencoded_size == 9001
