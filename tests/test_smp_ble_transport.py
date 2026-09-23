"""Tests for `SMPBLETransport`."""

import asyncio
from typing import Final, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from bleak import BleakClient
from bleak.args.bluez import BlueZClientArgs
from bleak.args.winrt import WinRTClientArgs
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from smp.os_management import EchoWriteResponse

from smpclient.transport import BufferSize, SMPTransportDisconnected, Unfragmented
from smpclient.transport.ble import (
    MAC_ADDRESS_PATTERN,
    SMP_CHARACTERISTIC_UUID,
    SMP_SERVICE_UUID,
    UUID_PATTERN,
    BleakBackend,
    BlueZ,
    PlatformDefault,
    SMPBLETransport,
    SMPBLETransportDeviceNotFound,
    WinRT,
    _Owned,
)
from tests.support import advertise, negotiated


class MockBleakClient:
    class Backend: ...

    def __new__(cls, *args, **kwargs) -> "MockBleakClient":  # type: ignore
        client = MagicMock(spec=BleakClient, name="MockBleakClient")
        client._backend = MockBleakClient.Backend()
        return client


pytestmark = pytest.mark.usefixtures("skip_negotiation")

ADDRESS = "00:00:00:00:00:00"
"""An address; bleak is mocked, so nothing is scanned for."""


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
    return_value=BLEDevice("address", "name", None),
)
@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_name",
    return_value=BLEDevice("address", "name", None),
)
@patch("smpclient.transport.ble.BleakClient", new=MockBleakClient)
@pytest.mark.asyncio
async def test_connect(
    mock_find_device_by_name: MagicMock,
    mock_find_device_by_address: MagicMock,
) -> None:
    # assert that it searches by name if MAC or UUID is not provided
    await SMPBLETransport(connect_timeout_s=1.0).connect("device name")
    mock_find_device_by_name.assert_called_once_with("device name", timeout=1.0, bluez={})
    mock_find_device_by_name.reset_mock()

    # assert that it searches by MAC if MAC is provided
    await SMPBLETransport(connect_timeout_s=1.0).connect("00:00:00:00:00:00")
    mock_find_device_by_address.assert_called_once_with("00:00:00:00:00:00", timeout=1.0, bluez={})
    mock_find_device_by_address.reset_mock()

    # assert that it searches by UUID if UUID is provided
    await SMPBLETransport(connect_timeout_s=1.0).connect(
        UUID("00000000-0000-4000-8000-000000000000").hex
    )
    mock_find_device_by_address.assert_called_once_with(
        "00000000000040008000000000000000", timeout=1.0, bluez={}
    )
    mock_find_device_by_address.reset_mock()

    # assert that it raises an exception if the device is not found
    mock_find_device_by_address.return_value = None
    with pytest.raises(SMPBLETransportDeviceNotFound):
        await SMPBLETransport(connect_timeout_s=1.0).connect("00:00:00:00:00:00")
    mock_find_device_by_address.reset_mock()

    # assert that connect is awaited
    t = SMPBLETransport(connect_timeout_s=1.0)
    await t.connect("name")
    _owned_client(t).connect.assert_awaited_once_with()

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
    _owned_client(t).start_notify.assert_called_once_with(
        SMP_CHARACTERISTIC_UUID, t._notify_callback
    )


@pytest.mark.parametrize(
    "backend, bluez, winrt",
    [
        pytest.param(PlatformDefault(), {}, {}, id="default"),
        pytest.param(BlueZ(BlueZClientArgs(adapter="hci1")), {"adapter": "hci1"}, {}, id="bluez"),
        pytest.param(
            WinRT(WinRTClientArgs(use_cached_services=True)),
            {},
            {"use_cached_services": True},
            id="winrt",
        ),
    ],
)
@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_address",
    return_value=BLEDevice(ADDRESS, "name", None),
)
@patch("smpclient.transport.ble.BleakClient", side_effect=MockBleakClient)
@pytest.mark.asyncio
async def test_connect_passes_bleak_only_the_chosen_backend(
    mock_bleak_client: MagicMock,
    mock_find_device_by_address: MagicMock,
    backend: BleakBackend,
    bluez: BlueZClientArgs,
    winrt: WinRTClientArgs,
) -> None:
    await SMPBLETransport(backend=backend, connect_timeout_s=1.0).connect(ADDRESS)

    mock_find_device_by_address.assert_called_once_with(ADDRESS, timeout=1.0, bluez=bluez)
    assert mock_bleak_client.call_args.kwargs["bluez"] == bluez
    assert mock_bleak_client.call_args.kwargs["winrt"] == winrt


@patch("smpclient.transport.ble.BleakScanner")
@pytest.mark.asyncio
async def test_scan_uses_the_bluez_adapter(mock_bleak_scanner: MagicMock) -> None:
    mock_bleak_scanner.return_value.discover = AsyncMock(return_value={})

    assert await SMPBLETransport.scan(backend=BlueZ(BlueZClientArgs(adapter="hci1"))) == []

    mock_bleak_scanner.assert_called_once_with(
        service_uuids=[str(SMP_SERVICE_UUID)], bluez={"adapter": "hci1"}
    )


@pytest.mark.asyncio
async def test_disconnect() -> None:
    client: Final = MagicMock(spec=BleakClient)
    t = SMPBLETransport()
    t._link = _Owned(client)

    await t.disconnect()
    await t.disconnect()

    client.disconnect.assert_awaited_once_with()
    with pytest.raises(SMPTransportDisconnected):
        await t.send(b"Hello pytest!")


def _owned_client(t: SMPBLETransport) -> MagicMock:
    match t._link:
        case _Owned(client=client):
            return cast(MagicMock, client)
        case _:
            pytest.fail(f"expected an owned link, got {t._link}")


def _borrowable_client(max_write: int = 244) -> MagicMock:
    """A caller's connected `BleakClient` that serves the SMP characteristic."""
    client = MagicMock(spec=BleakClient, name="BorrowedBleakClient")
    client._backend = MockBleakClient.Backend()
    client.address = ADDRESS
    client.is_connected = True
    client.services.get_characteristic.return_value = MagicMock(
        spec=BleakGATTCharacteristic, max_write_without_response_size=max_write
    )
    return client


@pytest.mark.asyncio
async def test_borrowed_subscribes_and_leaves_the_client_connected() -> None:
    client: Final = _borrowable_client(max_write=244)
    t = SMPBLETransport()

    async with t.borrowed(client) as borrowed:
        assert borrowed is t
        assert t.mtu == 244
        await t.send(b"Hello pytest!")

    client.start_notify.assert_awaited_once_with(SMP_CHARACTERISTIC_UUID, t._notify_callback)
    client.write_gatt_char.assert_awaited_once_with(
        t._smp_characteristic, b"Hello pytest!", response=False
    )
    client.stop_notify.assert_awaited_once_with(SMP_CHARACTERISTIC_UUID)
    client.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_returned_borrow_raises_disconnected() -> None:
    t = SMPBLETransport()
    async with t.borrowed(_borrowable_client()):
        pass

    with pytest.raises(SMPTransportDisconnected):
        await t.send(b"Hello pytest!")
    with pytest.raises(SMPTransportDisconnected):
        await asyncio.wait_for(t.receive(), timeout=1.0)
    await t.disconnect()


@pytest.mark.asyncio
async def test_borrowed_receive_raises_once_the_client_disconnects() -> None:
    """The owner holds the client's disconnect callback, so a borrowed wait polls instead."""
    client: Final = _borrowable_client()
    t = SMPBLETransport()

    async with t.borrowed(client):
        client.is_connected = False
        with pytest.raises(SMPTransportDisconnected):
            await asyncio.wait_for(t.receive(), timeout=1.0)


@pytest.mark.asyncio
async def test_borrowed_receive_leaves_no_task_polling_when_cancelled() -> None:
    t = SMPBLETransport()

    async with t.borrowed(_borrowable_client()):
        tasks_before: Final = asyncio.all_tasks()
        receive: Final = asyncio.create_task(t.receive())
        await asyncio.sleep(0.01)  # the receive is waiting on a notify or a disconnect
        receive.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receive

        assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_borrow_negotiates_the_fragmentation_strategy() -> None:
    t = SMPBLETransport()

    with advertise(2048) as read_mcumgr_parameters:
        await t.borrow(_borrowable_client())

    read_mcumgr_parameters.assert_awaited_once()
    assert t.max_unencoded_size == 2048


@pytest.mark.asyncio
async def test_borrow_returns_the_client_when_negotiation_fails() -> None:
    client: Final = _borrowable_client()
    t = SMPBLETransport()

    with (
        patch(
            "smpclient._request.read_mcumgr_parameters",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await t.borrow(client)

    client.stop_notify.assert_awaited_once_with(SMP_CHARACTERISTIC_UUID)
    client.disconnect.assert_not_awaited()


async def _never_returns(*_args: object) -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_notify", [BleakError("Not connected"), _never_returns], ids=["raises", "hangs"]
)
async def test_returning_a_borrowed_client_survives_its_unsubscribe(stop_notify: object) -> None:
    client: Final = _borrowable_client()
    client.stop_notify.side_effect = stop_notify
    t = SMPBLETransport(connect_timeout_s=0.1)

    async with t.borrowed(client):
        pass

    client.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_send() -> None:
    client: Final = MagicMock(spec=BleakClient)
    t = SMPBLETransport()
    t._link = _Owned(client)
    t._smp_characteristic = MagicMock(spec=BleakGATTCharacteristic)
    t._smp_characteristic.max_write_without_response_size = 20
    await t.send(b"Hello pytest!")
    client.write_gatt_char.assert_awaited_once_with(
        t._smp_characteristic, b"Hello pytest!", response=False
    )


@pytest.mark.asyncio
async def test_receive() -> None:
    t = SMPBLETransport()
    t._link = _Owned(MagicMock(spec=BleakClient))
    t._smp_characteristic = MagicMock(spec=BleakGATTCharacteristic)
    t._smp_characteristic.uuid = str(SMP_CHARACTERISTIC_UUID)
    t._disconnected_event.clear()  # pretend t.connect() was successful

    REP = bytes(EchoWriteResponse(r="Hello pytest!").to_frame(sequence=0))

    b, _ = await asyncio.gather(
        t.receive(),
        t._notify_callback(t._smp_characteristic, bytearray(REP)),
    )

    assert b == REP

    # cool, now try with a fragmented response
    async def fragmented_notifies() -> None:
        await t._notify_callback(t._smp_characteristic, bytearray(REP[:10]))
        await asyncio.sleep(0.001)
        await t._notify_callback(t._smp_characteristic, bytearray(REP[10:]))

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
    t._max_write_without_response_size = 42
    assert t.max_unencoded_size == 42


@pytest.mark.asyncio
async def test_max_unencoded_size_mcumgr_param() -> None:
    t = SMPBLETransport()
    t._max_write_without_response_size = 42
    assert (await negotiated(t, 9001)).max_unencoded_size == 9001


@pytest.mark.asyncio
@pytest.mark.parametrize("buf_size, expected", [(9001, 42), (30, 30)])
async def test_unfragmented_caps_at_the_write_size(buf_size: int, expected: int) -> None:
    """One message per write: never more than one write, nor more than the server holds."""
    t = SMPBLETransport(fragmentation_strategy=Unfragmented())
    t._max_write_without_response_size = 42
    assert (await negotiated(t, buf_size)).max_unencoded_size == expected


@pytest.mark.asyncio
async def test_buffer_size_never_reads() -> None:
    t = SMPBLETransport(fragmentation_strategy=BufferSize(512))
    with advertise(9001) as read:
        await t.negotiate()
    read.assert_not_awaited()
    assert t.max_unencoded_size == 512


class _HangingBleakClient:
    """A `BleakClient` stand-in whose `start_notify` never returns.

    Reproduces the failure mode reported in intercreate/smpmgr#97: the BlueZ
    `StartNotify` D-Bus call hangs indefinitely when the peer disconnects
    mid-pairing.
    """

    def __new__(cls, *args: object, **kwargs: object) -> "_HangingBleakClient":  # type: ignore[misc] # noqa: E501
        captured_callback = kwargs.get("disconnected_callback")
        client = MagicMock(spec=BleakClient, name="HangingBleakClient")
        client._backend = type("Backend", (), {})()
        client.connect = AsyncMock(name="connect")

        async def _hang(*_a: object, **_kw: object) -> None:
            await asyncio.Event().wait()  # never fires

        client.start_notify = AsyncMock(side_effect=_hang)
        client.disconnect = AsyncMock(name="disconnect")
        client.address = "00:00:00:00:00:00"
        client._captured_disconnected_callback = captured_callback  # type: ignore[attr-defined]
        return client


@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_address",
    return_value=BLEDevice("00:00:00:00:00:00", "name", None),
)
@patch("smpclient.transport.ble.BleakClient", new=_HangingBleakClient)
@pytest.mark.asyncio
async def test_connect_raises_on_peer_disconnect_during_start_notify(
    _mock_find_device_by_address: MagicMock,
) -> None:
    """Regression test for intercreate/smpmgr#97.

    When the peer disconnects mid-`start_notify` (e.g. failed pairing), `connect()`
    must surface `SMPTransportDisconnected` rather than hang.
    """
    t = SMPBLETransport(connect_timeout_s=5.0)

    async def _trip_disconnect_callback() -> MagicMock:
        # Wait until the transport reaches start_notify and clears the event,
        # then simulate the bleak `disconnected_callback` firing.
        while t._disconnected_event.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0)  # let start_notify await begin
        client: Final = _owned_client(t)
        t._set_disconnected_event(client)
        return client

    connect_task = asyncio.create_task(t.connect(ADDRESS))
    trip_task = asyncio.create_task(_trip_disconnect_callback())

    with pytest.raises(SMPTransportDisconnected):
        await connect_task

    # `_best_effort_disconnect` should have been called to release the client.
    (await trip_task).disconnect.assert_awaited()


@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_address",
    return_value=BLEDevice("00:00:00:00:00:00", "name", None),
)
@patch("smpclient.transport.ble.BleakClient", return_value=_HangingBleakClient())
@pytest.mark.asyncio
async def test_connect_raises_on_timeout_during_start_notify(
    mock_bleak_client: MagicMock,
    _mock_find_device_by_address: MagicMock,
) -> None:
    """`connect()` must honor `connect_timeout_s` even when `start_notify` hangs."""
    t = SMPBLETransport(connect_timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await t.connect(ADDRESS)
    mock_bleak_client.return_value.disconnect.assert_awaited()


@patch(
    "smpclient.transport.ble.BleakScanner.find_device_by_address",
    return_value=BLEDevice("00:00:00:00:00:00", "name", None),
)
@patch("smpclient.transport.ble.BleakClient", new=_HangingBleakClient)
@pytest.mark.asyncio
async def test_connect_does_not_leak_tasks_on_external_cancel(
    _mock_find_device_by_address: MagicMock,
) -> None:
    """Caller-driven cancellation must not leave `_await_or_disconnect` sub-tasks running."""
    t = SMPBLETransport(connect_timeout_s=60.0)
    tasks_before = {id(task) for task in asyncio.all_tasks()}

    connect_task = asyncio.create_task(t.connect(ADDRESS))
    while t._disconnected_event.is_set():
        await asyncio.sleep(0)  # wait until BleakClient.connect() returned
    await asyncio.sleep(0)  # let start_notify await begin

    connect_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connect_task

    # Let any cancellations propagate to the spawned sub-tasks.
    for _ in range(5):
        await asyncio.sleep(0)

    leaked = [
        task for task in asyncio.all_tasks() if id(task) not in tasks_before and not task.done()
    ]
    assert not leaked, f"sub-tasks leaked after external cancel: {leaked}"
