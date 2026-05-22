"""Tests for `SMPBumbleTransport`."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smpclient.transport import SMPTransportDisconnected
from smpclient.transport.bumble import (
    ATT_WRITE_OVERHEAD,
    DEFAULT_HCI_TRANSPORT,
    DEFAULT_HOST_NAME,
    SMP_CHARACTERISTIC_UUID,
    SMP_SERVICE_UUID,
    Connected,
    ConnectedBorrowed,
    Connecting,
    Disconnected,
    SMPBumbleTransport,
    SMPBumbleTransportException,
    SMPBumbleTransportNotSMPServer,
    _DisconnectSentinel,
    _find_smp_characteristic,
)
from smpclient.transport.bumble.keystore import (
    Custom,
    ExistingCustom,
    InMemory,
    InvalidKeystoreFilename,
    Local,
    Tempfile,
    resolve,
)
from smpclient.transport.bumble.pairing import (
    DisplayOnly,
    KeyboardOnly,
    NoInputNoOutput,
    PairingAlreadyBonded,
    PairingFailed,
    PairingFailureReason,
    PairingSucceeded,
    PairingTimedOut,
)


def test_smp_uuids_match_ble_transport() -> None:
    """Bumble must use the same SMP UUIDs as the bleak-backed transport."""
    from smpclient.transport import (
        SMP_CHARACTERISTIC_UUID as TRANSPORT_CHAR_UUID,
    )
    from smpclient.transport import (
        SMP_SERVICE_UUID as TRANSPORT_SVC_UUID,
    )
    from smpclient.transport.ble import (
        SMP_CHARACTERISTIC_UUID as BLE_CHAR_UUID,
    )
    from smpclient.transport.ble import (
        SMP_SERVICE_UUID as BLE_SVC_UUID,
    )

    assert SMP_SERVICE_UUID == TRANSPORT_SVC_UUID == BLE_SVC_UUID
    assert SMP_CHARACTERISTIC_UUID == TRANSPORT_CHAR_UUID == BLE_CHAR_UUID


def test_constructor_defaults() -> None:
    t = SMPBumbleTransport()
    assert isinstance(t._state, Disconnected)
    assert t._hci == DEFAULT_HCI_TRANSPORT
    assert t._host_name == DEFAULT_HOST_NAME
    assert t._pair_on_connect is None


@pytest.mark.asyncio
async def test_send_in_disconnected_state_raises() -> None:
    t = SMPBumbleTransport()
    with pytest.raises(SMPBumbleTransportException):
        await t.send(b"x")


def test_mtu_in_disconnected_state_raises() -> None:
    t = SMPBumbleTransport()
    with pytest.raises(SMPBumbleTransportException):
        _ = t.mtu


@pytest.mark.asyncio
async def test_pair_in_disconnected_state_raises() -> None:
    t = SMPBumbleTransport()
    with pytest.raises(SMPBumbleTransportException):
        await t.pair(NoInputNoOutput())


@pytest.mark.asyncio
async def test_pair_in_borrowed_state_raises() -> None:
    t = SMPBumbleTransport()
    t._state = ConnectedBorrowed(
        connection=MagicMock(), peer=MagicMock(), smp_characteristic=MagicMock(), max_write=20
    )
    with pytest.raises(SMPBumbleTransportException, match="borrowed"):
        await t.pair(NoInputNoOutput())


@pytest.mark.asyncio
async def test_disconnect_in_disconnected_state_is_noop() -> None:
    t = SMPBumbleTransport()
    await t.disconnect()
    assert isinstance(t._state, Disconnected)


@pytest.mark.asyncio
async def test_connect_while_connected_raises() -> None:
    t = SMPBumbleTransport()
    t._state = Connecting()
    with pytest.raises(SMPBumbleTransportException, match="Connecting"):
        await t.connect("00:11:22:33:44:55", 5.0)


def _make_connected(max_write: int = 244) -> tuple[SMPBumbleTransport, MagicMock]:
    t = SMPBumbleTransport()
    smp_char = MagicMock()
    smp_char.write_value = AsyncMock()
    t._state = Connected(
        transport=MagicMock(),
        device=MagicMock(),
        connection=MagicMock(),
        peer=MagicMock(),
        smp_characteristic=smp_char,
        max_write=max_write,
    )
    t._disconnected_event.clear()
    return t, smp_char


@pytest.mark.asyncio
async def test_send_chunks_data_to_max_write() -> None:
    t, smp_char = _make_connected(max_write=4)
    await t.send(b"abcdefghij")
    calls = [c.args[0] for c in smp_char.write_value.await_args_list]
    assert calls == [b"abcd", b"efgh", b"ij"]


@pytest.mark.asyncio
async def test_send_single_chunk_when_smaller_than_max_write() -> None:
    t, smp_char = _make_connected(max_write=64)
    await t.send(b"hello")
    smp_char.write_value.assert_awaited_once_with(b"hello")


@pytest.mark.asyncio
async def test_receive_assembles_smp_message_from_notification_chunks() -> None:
    from smpclient.requests.os_management import EchoWrite

    t, _ = _make_connected()
    response_bytes = EchoWrite._Response.get_default()(  # type: ignore[attr-defined]
        sequence=0, r="hi"
    ).BYTES

    async def push_chunks() -> None:
        await asyncio.sleep(0)  # let receive() start awaiting first
        t._on_notification(response_bytes[:5])
        await asyncio.sleep(0)
        t._on_notification(response_bytes[5:])

    out, _ = await asyncio.gather(t.receive(), push_chunks())
    assert out == response_bytes


@pytest.mark.asyncio
async def test_receive_raises_when_peer_disconnects() -> None:
    t, _ = _make_connected()
    t._on_disconnection(0x16)
    with pytest.raises(SMPTransportDisconnected):
        await t.receive()


@pytest.mark.asyncio
async def test_disconnect_sentinel_wakes_receive() -> None:
    t, _ = _make_connected()

    async def trigger() -> None:
        await asyncio.sleep(0)
        t._on_disconnection(0x16)

    with pytest.raises(SMPTransportDisconnected):
        await asyncio.gather(t.receive(), trigger())


@pytest.mark.asyncio
async def test_keyboard_only_delegate_accepts_valid_pin() -> None:
    async def cb() -> int | None:
        return 123456

    d = KeyboardOnly(cb)
    assert await d.get_number() == 123456


@pytest.mark.asyncio
async def test_keyboard_only_delegate_rejects_out_of_range() -> None:
    async def cb() -> int | None:
        return 1234567

    d = KeyboardOnly(cb)
    assert await d.get_number() is None


@pytest.mark.asyncio
async def test_keyboard_only_delegate_propagates_none() -> None:
    async def cb() -> int | None:
        return None

    d = KeyboardOnly(cb)
    assert await d.get_number() is None


@pytest.mark.asyncio
async def test_display_only_delegate_calls_display_callback() -> None:
    seen: list[int] = []

    async def cb(number: int) -> None:
        seen.append(number)

    d = DisplayOnly(cb)
    await d.display_number(987654, digits=6)
    assert seen == [987654]


def test_pairing_result_variants_are_distinguishable() -> None:
    """Smoke test that match/assert_never works on `PairingResult`."""
    from typing_extensions import assert_never

    from smpclient.transport.bumble.pairing import PairingResult

    def describe(r: PairingResult) -> str:
        match r:
            case PairingSucceeded(bonded):
                return f"ok bonded={bonded}"
            case PairingAlreadyBonded():
                return "already"
            case PairingTimedOut(elapsed):
                return f"timeout {elapsed}"
            case PairingFailed(reason, _):
                return f"failed {reason.value}"
            case _:
                assert_never(r)

    assert describe(PairingSucceeded(bonded=True)) == "ok bonded=True"
    assert describe(PairingAlreadyBonded()) == "already"
    assert describe(PairingTimedOut(elapsed_s=1.5)) == "timeout 1.5"
    assert (
        describe(PairingFailed(reason=PairingFailureReason.NOT_FOUND, detail="x"))
        == "failed not_found"
    )


def test_keystore_resolve_tempfile_uses_temp_dir() -> None:
    ks = resolve(Tempfile("custom_bonds.json"), namespace="aa:bb:cc:dd:ee:ff")
    expected = Path(tempfile.gettempdir()) / "custom_bonds.json"
    assert Path(ks.filename) == expected  # type: ignore[attr-defined]


def test_keystore_resolve_local_under_user_data_dir(tmp_path: Path) -> None:
    with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
        ks = resolve(Local("bonds.json"), namespace="aa:bb:cc:dd:ee:ff")
    assert Path(ks.filename) == tmp_path / "bonds.json"  # type: ignore[attr-defined]


def test_keystore_resolve_custom_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "bonds.json"
    resolve(Custom(target), namespace="aa:bb:cc:dd:ee:ff")
    assert target.parent.is_dir()


def test_keystore_resolve_existing_custom_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve(ExistingCustom(tmp_path / "nope.json"), namespace="aa:bb:cc:dd:ee:ff")


def test_keystore_resolve_existing_custom_when_present(tmp_path: Path) -> None:
    target = tmp_path / "have.json"
    target.write_text("{}")
    ks = resolve(ExistingCustom(target), namespace="aa:bb:cc:dd:ee:ff")
    assert Path(ks.filename) == target  # type: ignore[attr-defined]


def test_keystore_resolve_in_memory() -> None:
    from bumble.keys import MemoryKeyStore

    ks = resolve(InMemory(), namespace="aa:bb:cc:dd:ee:ff")
    assert isinstance(ks, MemoryKeyStore)


@pytest.mark.parametrize(
    "bad_name",
    [
        "/absolute/path.json",
        "rel/path.json",
        cast(str, os.path.join("a", "b.json")),
    ],
)
def test_keystore_tempfile_rejects_path_separators(bad_name: str) -> None:
    with pytest.raises(InvalidKeystoreFilename):
        resolve(Tempfile(bad_name), namespace="aa:bb:cc:dd:ee:ff")


def test_keystore_local_rejects_path_separators() -> None:
    with pytest.raises(InvalidKeystoreFilename):
        resolve(Local("nested/bonds.json"), namespace="aa:bb:cc:dd:ee:ff")


def test_find_smp_characteristic_raises_when_service_missing() -> None:
    peer = MagicMock()
    peer.get_services_by_uuid.return_value = []
    with pytest.raises(SMPBumbleTransportNotSMPServer, match="service"):
        _find_smp_characteristic(peer)


def test_find_smp_characteristic_raises_when_char_missing() -> None:
    peer = MagicMock()
    service = MagicMock()
    service.get_characteristics_by_uuid.return_value = []
    peer.get_services_by_uuid.return_value = [service]
    with pytest.raises(SMPBumbleTransportNotSMPServer, match="characteristic"):
        _find_smp_characteristic(peer)


def test_find_smp_characteristic_returns_first_match() -> None:
    peer = MagicMock()
    service = MagicMock()
    expected = MagicMock()
    service.get_characteristics_by_uuid.return_value = [expected, MagicMock()]
    peer.get_services_by_uuid.return_value = [service]
    assert _find_smp_characteristic(peer) is expected


def test_att_write_overhead_is_three() -> None:
    """1-byte opcode + 2-byte handle = 3 bytes."""
    assert ATT_WRITE_OVERHEAD == 3


def test_disconnect_sentinel_is_namedtuple() -> None:
    assert isinstance(_DisconnectSentinel(), tuple)


class _MockBumbleEnvironment:
    """Builds the mock bumble stack required by `SMPBumbleTransport.connect()`."""

    def __init__(self, *, with_bond: bool = False) -> None:
        self.transport = MagicMock()
        self.transport.close = AsyncMock()
        self.transport.__aenter__ = AsyncMock(return_value=self.transport)
        self.transport.__aexit__ = AsyncMock(return_value=None)
        self.open_transport = AsyncMock(return_value=self.transport)

        self.connection = MagicMock()
        self.connection.peer_address = "AA:BB:CC:DD:EE:FF"
        self.connection.is_encrypted = False
        self.connection.authenticated = False
        self.connection.att_mtu = 23
        self.connection.disconnect = AsyncMock()
        self.connection.encrypt = AsyncMock()
        self.connection.pair = AsyncMock()
        self.connection.on = MagicMock()

        self.device = MagicMock()
        self.device.public_address = "11:22:33:44:55:66"
        self.device.power_on = AsyncMock()
        self.device.power_off = AsyncMock()
        self.device.connect = AsyncMock(return_value=self.connection)
        self.keystore = MagicMock()
        self.keystore.get = AsyncMock(return_value=object() if with_bond else None)
        self.device.keystore = self.keystore

        self.smp_char = MagicMock()
        self.smp_char.subscribe = AsyncMock()
        self.smp_char.unsubscribe = AsyncMock()
        self.smp_char.write_value = AsyncMock()

        self.peer = MagicMock()
        self.peer.discover_all = AsyncMock()
        self.peer.services = []
        self.peer.request_mtu = AsyncMock(return_value=247)


@pytest.fixture
def bumble_env(monkeypatch: pytest.MonkeyPatch) -> _MockBumbleEnvironment:
    env = _MockBumbleEnvironment()

    def _device_with_hci(*_args: object, **_kwargs: object) -> MagicMock:
        return env.device

    monkeypatch.setattr("smpclient.transport.bumble.open_transport", env.open_transport)
    monkeypatch.setattr(
        "smpclient.transport.bumble.Device.with_hci", staticmethod(_device_with_hci)
    )
    monkeypatch.setattr("smpclient.transport.bumble.Peer", lambda _conn: env.peer)
    monkeypatch.setattr(
        "smpclient.transport.bumble._find_smp_characteristic",
        lambda _peer: env.smp_char,
    )
    monkeypatch.setattr(
        "smpclient.transport.bumble.resolve_keystore",
        lambda _strategy, namespace: env.keystore,
    )
    return env


@pytest.mark.asyncio
async def test_connect_transitions_to_connected_state(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    t = SMPBumbleTransport()
    await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    assert isinstance(t._state, Connected)
    assert t._state.max_write == 247 - ATT_WRITE_OVERHEAD
    bumble_env.device.power_on.assert_awaited_once()
    bumble_env.smp_char.subscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_proactively_encrypts_when_bonded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _MockBumbleEnvironment(with_bond=True)
    monkeypatch.setattr("smpclient.transport.bumble.open_transport", env.open_transport)
    monkeypatch.setattr(
        "smpclient.transport.bumble.Device.with_hci",
        staticmethod(lambda *a, **kw: env.device),
    )
    monkeypatch.setattr("smpclient.transport.bumble.Peer", lambda _conn: env.peer)
    monkeypatch.setattr(
        "smpclient.transport.bumble._find_smp_characteristic", lambda _p: env.smp_char
    )
    monkeypatch.setattr(
        "smpclient.transport.bumble.resolve_keystore",
        lambda _s, namespace: env.keystore,
    )
    t = SMPBumbleTransport()
    await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    env.connection.encrypt.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_failure_tears_down_partial_state(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    bumble_env.smp_char.subscribe.side_effect = RuntimeError("boom")
    t = SMPBumbleTransport()
    with pytest.raises(RuntimeError, match="boom"):
        await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    assert isinstance(t._state, Disconnected)
    bumble_env.connection.disconnect.assert_awaited()
    bumble_env.device.power_off.assert_awaited()
    bumble_env.transport.close.assert_awaited()


@pytest.mark.asyncio
async def test_disconnect_owned_tears_down_everything(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    t = SMPBumbleTransport()
    await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    await t.disconnect()
    assert isinstance(t._state, Disconnected)
    bumble_env.smp_char.unsubscribe.assert_awaited()
    bumble_env.connection.disconnect.assert_awaited()
    bumble_env.device.power_off.assert_awaited()
    bumble_env.transport.close.assert_awaited()


@pytest.mark.asyncio
async def test_use_connection_borrowed_only_unsubscribes_on_disconnect(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    t = SMPBumbleTransport()
    await t.use_connection(bumble_env.connection)
    assert isinstance(t._state, ConnectedBorrowed)
    await t.disconnect()
    bumble_env.smp_char.unsubscribe.assert_awaited()
    bumble_env.connection.disconnect.assert_not_called()
    bumble_env.device.power_off.assert_not_called()
    bumble_env.transport.close.assert_not_called()


@pytest.mark.asyncio
async def test_use_connection_skips_discover_when_services_present(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    bumble_env.peer.services = [MagicMock()]
    t = SMPBumbleTransport()
    await t.use_connection(bumble_env.connection, peer=bumble_env.peer)
    bumble_env.peer.discover_all.assert_not_called()


@pytest.mark.asyncio
async def test_use_connection_while_connected_raises(
    bumble_env: _MockBumbleEnvironment,
) -> None:
    t = SMPBumbleTransport()
    await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    with pytest.raises(SMPBumbleTransportException):
        await t.use_connection(bumble_env.connection)


@pytest.mark.asyncio
async def test_scan_returns_empty_when_no_devices_seen() -> None:
    from smpclient.transport.bumble.scan import scan

    device = MagicMock()
    device.on = MagicMock()
    device.start_scanning = AsyncMock()
    device.stop_scanning = AsyncMock()
    device.remove_listener = MagicMock()

    results = await scan(device, timeout_s=0.0)
    assert results == ()
    device.start_scanning.assert_awaited_once()
    device.stop_scanning.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_dedupes_by_address_and_keeps_latest_name() -> None:
    from smpclient.transport.bumble.scan import ScanResult, scan

    device = MagicMock()
    listener_holder: dict[str, object] = {}

    def _on(_event: str, listener: object) -> None:
        listener_holder["fn"] = listener

    device.on = _on
    device.start_scanning = AsyncMock()
    device.stop_scanning = AsyncMock()
    device.remove_listener = MagicMock()

    ad1 = MagicMock()
    ad1.address = "AA:BB:CC:DD:EE:FF"
    ad1.rssi = -50
    ad1.data.COMPLETE_LOCAL_NAME = "complete_local_name"
    ad1.data.SHORTENED_LOCAL_NAME = "shortened_local_name"
    ad1.data.get.side_effect = lambda key: "Foo" if key == "complete_local_name" else None

    ad2 = MagicMock()
    ad2.address = "AA:BB:CC:DD:EE:FF"
    ad2.rssi = -45
    ad2.data.COMPLETE_LOCAL_NAME = "complete_local_name"
    ad2.data.SHORTENED_LOCAL_NAME = "shortened_local_name"
    ad2.data.get.side_effect = lambda key: "Foo2" if key == "complete_local_name" else None

    async def emit_advertisements() -> None:
        await asyncio.sleep(0)
        listener_holder["fn"](ad1)  # type: ignore[operator]
        listener_holder["fn"](ad2)  # type: ignore[operator]

    results_t = asyncio.create_task(scan(device, timeout_s=0.05))
    await emit_advertisements()
    results = await results_t

    assert len(results) == 1
    assert results[0].address == "AA:BB:CC:DD:EE:FF"
    assert results[0].name == "Foo2"
    assert isinstance(results[0], ScanResult)


@pytest.mark.asyncio
async def test_cli_prompt_pin_valid() -> None:
    from smpclient.transport.bumble.__main__ import _prompt_pin

    with patch(
        "smpclient.transport.bumble.__main__.asyncio.to_thread", AsyncMock(return_value="123456")
    ):
        assert await _prompt_pin() == 123456


@pytest.mark.asyncio
async def test_cli_prompt_pin_rejects_non_digits() -> None:
    from smpclient.transport.bumble.__main__ import _prompt_pin

    with patch(
        "smpclient.transport.bumble.__main__.asyncio.to_thread", AsyncMock(return_value="abc")
    ):
        assert await _prompt_pin() is None


@pytest.mark.asyncio
async def test_cli_prompt_pin_rejects_wrong_length() -> None:
    from smpclient.transport.bumble.__main__ import _prompt_pin

    with patch(
        "smpclient.transport.bumble.__main__.asyncio.to_thread", AsyncMock(return_value="12345")
    ):
        assert await _prompt_pin() is None


@pytest.mark.asyncio
async def test_cli_scan_handler_prints_results(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _scan, _ScanArgs
    from smpclient.transport.bumble.scan import ScanResult

    with patch.object(
        SMPBumbleTransport,
        "scan",
        AsyncMock(
            return_value=(
                ScanResult(address="AA:BB:CC:DD:EE:FF", name="Foo", rssi=-50, has_smp_service=True),
            )
        ),
    ):
        rc = await _scan(_ScanArgs(hci="usb:0", timeout=1.0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "AA:BB:CC:DD:EE:FF" in out
    assert "[SMP]" in out


@pytest.mark.asyncio
async def test_cli_scan_handler_returns_1_when_empty(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _scan, _ScanArgs

    with patch.object(SMPBumbleTransport, "scan", AsyncMock(return_value=())):
        rc = await _scan(_ScanArgs(hci="usb:0", timeout=1.0))
    assert rc == 1


@pytest.mark.asyncio
async def test_cli_pair_handler_success(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _pair, _PairArgs

    with patch(
        "smpclient.transport.bumble.__main__.pair_device",
        AsyncMock(return_value=PairingSucceeded(bonded=True)),
    ):
        rc = await _pair(
            _PairArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", timeout=5.0, force=False)
        )
    assert rc == 0
    assert "bonded=True" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_pair_handler_failed(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _pair, _PairArgs

    with patch(
        "smpclient.transport.bumble.__main__.pair_device",
        AsyncMock(return_value=PairingFailed(reason=PairingFailureReason.AUTH, detail="bad")),
    ):
        rc = await _pair(
            _PairArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", timeout=5.0, force=False)
        )
    assert rc == 1
    assert "authentication" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_pair_handler_timed_out(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _pair, _PairArgs

    with patch(
        "smpclient.transport.bumble.__main__.pair_device",
        AsyncMock(return_value=PairingTimedOut(elapsed_s=3.5)),
    ):
        rc = await _pair(
            _PairArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", timeout=5.0, force=False)
        )
    assert rc == 1


@pytest.mark.asyncio
async def test_cli_pair_handler_already_bonded(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _pair, _PairArgs

    with patch(
        "smpclient.transport.bumble.__main__.pair_device",
        AsyncMock(return_value=PairingAlreadyBonded()),
    ):
        rc = await _pair(
            _PairArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", timeout=5.0, force=False)
        )
    assert rc == 0
    assert "Already bonded" in capsys.readouterr().out


def test_cli_argparse_routes_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from smpclient.transport.bumble.__main__ import smpbumble

    monkeypatch.setattr("sys.argv", ["smpbumble", "scan", "--hci", "usb:1", "--timeout", "2"])
    scanned = AsyncMock(return_value=0)
    monkeypatch.setattr("smpclient.transport.bumble.__main__._scan", scanned)
    rc = smpbumble()
    assert rc == 0
    args = scanned.call_args.args[0]  # type: ignore[union-attr]
    assert args.hci == "usb:1"
    assert args.timeout == 2.0


def test_cli_argparse_routes_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    from smpclient.transport.bumble.__main__ import smpbumble

    monkeypatch.setattr(
        "sys.argv", ["smpbumble", "pair", "AA:BB:CC:DD:EE:FF", "--force", "--timeout", "30"]
    )
    paired = AsyncMock(return_value=0)
    monkeypatch.setattr("smpclient.transport.bumble.__main__._pair", paired)
    rc = smpbumble()
    assert rc == 0
    args = paired.call_args.args[0]  # type: ignore[union-attr]
    assert args.address == "AA:BB:CC:DD:EE:FF"
    assert args.force is True
    assert args.timeout == 30.0


def test_cli_argparse_routes_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    from smpclient.transport.bumble.__main__ import smpbumble

    monkeypatch.setattr("sys.argv", ["smpbumble", "echo", "AA:BB:CC:DD:EE:FF", "hello"])
    echoed = AsyncMock(return_value=0)
    monkeypatch.setattr("smpclient.transport.bumble.__main__._echo", echoed)
    rc = smpbumble()
    assert rc == 0
    args = echoed.call_args.args[0]  # type: ignore[union-attr]
    assert args.message == "hello"


def _pair_test_objs() -> tuple[MagicMock, MagicMock, MagicMock]:
    keystore = MagicMock()
    keystore.get = AsyncMock(return_value=None)
    keystore.delete = AsyncMock()

    device = MagicMock()
    device.keystore = keystore
    device.pairing_config_factory = None

    connection = MagicMock()
    connection.peer_address = "AA:BB:CC:DD:EE:FF"
    connection.is_encrypted = True
    connection.authenticated = True
    connection.pair = AsyncMock()
    return connection, device, keystore


@pytest.mark.asyncio
async def test_module_pair_returns_already_bonded_when_keystore_has_entry() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, keystore = _pair_test_objs()
    keystore.get.return_value = object()
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=5.0, settle_s=0.0, force=False
    )
    assert isinstance(result, PairingAlreadyBonded)
    connection.pair.assert_not_called()


@pytest.mark.asyncio
async def test_module_pair_succeeds_when_post_pair_state_is_encrypted() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, keystore = _pair_test_objs()
    # First call (pre-pair existing-bond check) returns None; second (post-pair
    # bonded? check) returns a stored object.
    keystore.get.side_effect = [None, object()]
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=5.0, settle_s=0.0, force=False
    )
    assert isinstance(result, PairingSucceeded)
    assert result.bonded is True


@pytest.mark.asyncio
async def test_module_pair_force_deletes_bond_first() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, keystore = _pair_test_objs()
    keystore.get.return_value = object()
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=5.0, settle_s=0.0, force=True
    )
    keystore.delete.assert_awaited_once()
    assert isinstance(result, PairingSucceeded)


@pytest.mark.asyncio
async def test_module_pair_timeout() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, _keystore = _pair_test_objs()

    async def slow_pair() -> None:
        await asyncio.sleep(10)

    connection.pair.side_effect = slow_pair
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=0.01, settle_s=0.0, force=False
    )
    assert isinstance(result, PairingTimedOut)


@pytest.mark.asyncio
async def test_module_pair_bumble_exception() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, _keystore = _pair_test_objs()
    connection.pair.side_effect = RuntimeError("smp blew up")
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=5.0, settle_s=0.0, force=False
    )
    assert isinstance(result, PairingFailed)
    assert result.reason == PairingFailureReason.BUMBLE


@pytest.mark.asyncio
async def test_module_pair_post_pair_not_encrypted() -> None:
    from smpclient.transport.bumble import pair as module_pair

    connection, device, _keystore = _pair_test_objs()
    connection.is_encrypted = False
    connection.authenticated = False
    result = await module_pair(
        connection, device, NoInputNoOutput(), pair_timeout_s=5.0, settle_s=0.0, force=False
    )
    assert isinstance(result, PairingFailed)
    assert result.reason == PairingFailureReason.AUTH


def test_extract_name_decodes_bytes() -> None:
    from smpclient.transport.bumble.scan import _extract_name

    ad = MagicMock()
    ad.data.COMPLETE_LOCAL_NAME = "complete_local_name"
    ad.data.SHORTENED_LOCAL_NAME = "shortened_local_name"
    ad.data.get.side_effect = lambda key: b"hello" if key == "complete_local_name" else None
    assert _extract_name(ad) == "hello"


def test_extract_name_returns_none_when_no_name() -> None:
    from smpclient.transport.bumble.scan import _extract_name

    ad = MagicMock()
    ad.data.COMPLETE_LOCAL_NAME = "complete_local_name"
    ad.data.SHORTENED_LOCAL_NAME = "shortened_local_name"
    ad.data.get.return_value = None
    assert _extract_name(ad) is None


def test_advertises_service_finds_match() -> None:
    from bumble.core import UUID as BumbleUUID

    from smpclient.transport.bumble.scan import _advertises_service

    target_str = "8D53DC1D-1DB7-4CD3-868B-8A527460AA84"
    target = BumbleUUID(target_str)
    ad = MagicMock()
    ad.data.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS = "complete_128"
    ad.data.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS = "incomplete_128"
    ad.data.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS = "complete_16"
    ad.data.INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS = "incomplete_16"

    ad.data.get.side_effect = lambda key: (
        [BumbleUUID(target_str)] if key == "complete_128" else None
    )
    assert _advertises_service(ad, target) is True


def test_advertises_service_returns_false_when_no_match() -> None:
    from bumble.core import UUID as BumbleUUID

    from smpclient.transport.bumble.scan import _advertises_service

    target = BumbleUUID("8D53DC1D-1DB7-4CD3-868B-8A527460AA84")
    ad = MagicMock()
    ad.data.get.return_value = None
    assert _advertises_service(ad, target) is False


@pytest.mark.asyncio
async def test_cli_echo_success(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _echo, _EchoArgs

    response = MagicMock()
    response.r = "pong"
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock(return_value=response)

    with (
        patch("smpclient.transport.bumble.__main__.SMPClient", return_value=client),
        patch("smpclient.transport.bumble.__main__.success", return_value=True),
        patch("smpclient.transport.bumble.__main__.error", return_value=False),
    ):
        rc = await _echo(
            _EchoArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", message="ping", timeout=5.0)
        )
    assert rc == 0
    assert "pong" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_echo_returns_1_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    from smpclient.transport.bumble.__main__ import _echo, _EchoArgs

    response = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock(return_value=response)

    with (
        patch("smpclient.transport.bumble.__main__.SMPClient", return_value=client),
        patch("smpclient.transport.bumble.__main__.success", return_value=False),
        patch("smpclient.transport.bumble.__main__.error", return_value=True),
    ):
        rc = await _echo(
            _EchoArgs(hci="usb:0", address="AA:BB:CC:DD:EE:FF", message="ping", timeout=5.0)
        )
    assert rc == 1


@pytest.mark.asyncio
async def test_bumble_device_context_manager(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    from smpclient.transport.bumble import bumble_device

    async with bumble_device(hci="usb:0", delegate=NoInputNoOutput()) as device:
        assert device is bumble_env.device
    bumble_env.device.power_on.assert_awaited_once()
    bumble_env.device.power_off.assert_awaited_once()


@pytest.mark.asyncio
async def test_pair_device_resolves_mac_directly(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    from smpclient.transport.bumble import pair_device

    bumble_env.connection.is_encrypted = True
    bumble_env.connection.authenticated = True
    bumble_env.keystore.get.side_effect = [None, object()]

    result = await pair_device(
        "AA:BB:CC:DD:EE:FF",
        NoInputNoOutput(),
        hci="usb:0",
        pair_timeout_s=5.0,
        settle_s=0.0,
    )
    assert isinstance(result, PairingSucceeded)
    bumble_env.connection.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_pair_device_scans_by_name_when_not_mac(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    from smpclient.transport.bumble import pair_device
    from smpclient.transport.bumble.scan import ScanResult

    monkeypatch.setattr(
        "smpclient.transport.bumble.scan_for_devices",
        AsyncMock(
            return_value=(
                ScanResult(
                    address="AA:BB:CC:DD:EE:FF",
                    name="MyDev",
                    rssi=-40,
                    has_smp_service=False,
                ),
            )
        ),
    )
    bumble_env.connection.is_encrypted = True
    bumble_env.connection.authenticated = True
    bumble_env.keystore.get.side_effect = [None, object()]

    result = await pair_device(
        "MyDev",
        NoInputNoOutput(),
        hci="usb:0",
        pair_timeout_s=5.0,
        settle_s=0.0,
    )
    assert isinstance(result, PairingSucceeded)


@pytest.mark.asyncio
async def test_pair_device_returns_not_found_on_name_miss(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    from smpclient.transport.bumble import pair_device

    monkeypatch.setattr(
        "smpclient.transport.bumble.scan_for_devices",
        AsyncMock(return_value=()),
    )
    result = await pair_device(
        "Ghost",
        NoInputNoOutput(),
        hci="usb:0",
        scan_timeout_s=0.01,
    )
    assert isinstance(result, PairingFailed)
    assert result.reason == PairingFailureReason.NOT_FOUND


@pytest.mark.asyncio
async def test_pair_on_connect_runs_pair_when_no_bond(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    # Bond doesn't exist for the pre-pair check; appears after.
    bumble_env.keystore.get.side_effect = [None, None, object()]

    def _post_pair_encrypted() -> None:
        bumble_env.connection.is_encrypted = True
        bumble_env.connection.authenticated = True

    bumble_env.connection.pair = AsyncMock(side_effect=_post_pair_encrypted)

    t = SMPBumbleTransport(pair_on_connect=NoInputNoOutput(), settle_s=0.0)
    await t.connect("AA:BB:CC:DD:EE:FF", 5.0)
    bumble_env.connection.pair.assert_awaited_once()
    assert isinstance(t._state, Connected)


@pytest.mark.asyncio
async def test_resolve_target_raises_when_no_device_with_name(
    monkeypatch: pytest.MonkeyPatch, bumble_env: _MockBumbleEnvironment
) -> None:
    from smpclient.transport.bumble import SMPBumbleTransportDeviceNotFound

    monkeypatch.setattr(
        "smpclient.transport.bumble.scan_for_devices",
        AsyncMock(return_value=()),
    )
    t = SMPBumbleTransport()
    with pytest.raises(SMPBumbleTransportDeviceNotFound):
        await t.connect("UnknownName", 0.1)


# Suppress unused-imports warnings for symbols re-exported for downstream code.
_ = (os, tempfile, NoInputNoOutput, _DisconnectSentinel)
