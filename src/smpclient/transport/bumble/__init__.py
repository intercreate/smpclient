"""A bumble-backed `SMPTransport` for BLE.

This transport drives an external HCI controller (e.g. an nRF52840 DK running
the Zephyr `hci_usb` sample) through Google's bumble Bluetooth stack and
communicates with an SMP server over GATT.

It is an alternative to `smpclient.transport.ble.SMPBLETransport`, which uses
the OS's BLE stack via bleak.  The bumble transport is useful when the OS
stack is unavailable, missing required features (e.g. LE Secure Connections
with a custom IO capability), or when reproducible cross-platform behavior is
desired by using the *same* HCI controller everywhere.

The transport's connection lifecycle is modeled as a sum type
(`Disconnected | Connecting | Connected`).  Pattern-match on `transport._state`
or, better, treat the transport as a black box through the `SMPTransport`
Protocol.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Final, NamedTuple, TypeAlias, assert_never
from uuid import UUID

try:
    from bumble.core import UUID as BumbleUUID
    from bumble.device import Connection, Device, Peer
    from bumble.gatt_client import CharacteristicProxy
    from bumble.hci import Address, AddressType
    from bumble.keys import KeyStore
    from bumble.pairing import PairingConfig, PairingDelegate
    from bumble.transport import open_transport
    from bumble.transport.common import Transport
except ModuleNotFoundError as e:
    if e.name == "bumble":
        raise ImportError(
            "Bumble transport requires the 'bumble' extra. Use smpclient[bumble]"
        ) from e
    raise

from smp import header as smphdr
from typing_extensions import override

from smpclient.exceptions import SMPClientException
from smpclient.transport import SMPTransport, SMPTransportDisconnected
from smpclient.transport.bumble.keystore import KeystoreStrategy, Tempfile
from smpclient.transport.bumble.keystore import resolve as resolve_keystore
from smpclient.transport.bumble.pairing import (
    PairingAlreadyBonded,
    PairingFailed,
    PairingFailureReason,
    PairingResult,
    PairingSucceeded,
    PairingTimedOut,
)
from smpclient.transport.bumble.scan import scan as scan_for_devices

logger = logging.getLogger(__name__)


SMP_SERVICE_UUID: Final = UUID("8D53DC1D-1DB7-4CD3-868B-8A527460AA84")
SMP_CHARACTERISTIC_UUID: Final = UUID("DA2E7828-FBCE-4E01-AE9E-261174997C48")

MAC_ADDRESS_PATTERN: Final = re.compile(r"([0-9A-F]{2}[:]){5}[0-9A-F]{2}$", flags=re.IGNORECASE)

DEFAULT_HOST_ADDRESS: Final = Address("F0:5C:81:00:00:01", AddressType.RANDOM_DEVICE)
"""Default static-random BD_ADDR used by the local host.

Static-random addresses have the top two bits of the MSB set to 1.  Override
via the transport constructor if you need a stable identity for bonding or to
avoid collision with another bumble-based process.
"""

DEFAULT_HOST_NAME: Final = "smpclient-bumble"
DEFAULT_HCI_TRANSPORT: Final = "usb:0"
DEFAULT_PREFERRED_MTU: Final = 247
DEFAULT_POST_PAIR_SETTLE_S: Final = 1.5
"""After `connection.pair()` returns, peers need a brief settle window to
finalize bonding on their side before disconnect.  Tune per-peer via
`pair_device(..., settle_s=...)` if a particular peer needs longer."""


class SMPBumbleTransportException(SMPClientException):
    """Base class for SMP bumble transport exceptions."""


class SMPBumbleTransportDeviceNotFound(SMPBumbleTransportException):
    """Raised when no advertising device matches the requested scan filter."""


class SMPBumbleTransportNotSMPServer(SMPBumbleTransportException):
    """Raised when the connected peripheral does not expose the SMP characteristic."""


class Disconnected(NamedTuple): ...


class _DisconnectSentinel(NamedTuple):
    """Pushed onto `_notifications` to wake a pending `receive()` on disconnect."""


@dataclass(slots=True)
class Connecting:
    """Partial state populated field-by-field during `connect()`, drained by `disconnect()` on failure."""

    transport: Transport | None = None
    device: Device | None = None
    connection: Connection | None = None
    peer: Peer | None = None
    smp_characteristic: CharacteristicProxy[bytes] | None = None


class Connected(NamedTuple):
    transport: Transport
    device: Device
    connection: Connection
    peer: Peer
    smp_characteristic: CharacteristicProxy[bytes]
    max_write: int


_State: TypeAlias = Disconnected | Connecting | Connected


class SMPBumbleTransport(SMPTransport):
    """An `SMPTransport` backed by Google's bumble Bluetooth stack."""

    def __init__(
        self,
        *,
        hci: str = DEFAULT_HCI_TRANSPORT,
        host_address: Address = DEFAULT_HOST_ADDRESS,
        host_name: str = DEFAULT_HOST_NAME,
        keystore: KeystoreStrategy = Tempfile(),
        preferred_mtu: int = DEFAULT_PREFERRED_MTU,
    ) -> None:
        """Initialize the bumble transport.

        Args:
            hci: The bumble HCI transport spec, e.g. `"usb:0"` or
                `"tcp-client:host:port"`.  See bumble's `open_transport()` for
                the full list of supported schemes.
            host_address: The local BD_ADDR used by this host.  Default is a
                static-random address; override for stable bonding identity.
            host_name: The local device name reported to peers during pairing.
            keystore: Strategy for persisting bond keys.  See
                `smpclient.transport.bumble.keystore`.
            preferred_mtu: The ATT MTU to request after connection.  Falls
                back to the controller default if the exchange fails.
        """
        self._hci: Final = hci
        self._host_address: Final = host_address
        self._host_name: Final = host_name
        self._keystore: Final = keystore
        self._preferred_mtu: Final = preferred_mtu

        self._state: _State = Disconnected()

        self._notifications: asyncio.Queue[bytes | _DisconnectSentinel] = asyncio.Queue()
        self._disconnected_event = asyncio.Event()
        self._disconnected_event.set()

        logger.debug(f"Initialized {self.__class__.__name__}(hci={hci!r})")

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        if not isinstance(self._state, Disconnected):
            raise SMPBumbleTransportException(
                f"connect() called while in state {type(self._state).__name__}"
            )

        self._state = Connecting()
        while not self._notifications.empty():
            self._notifications.get_nowait()
        self._disconnected_event.clear()

        try:
            self._state.transport = await open_transport(self._hci)
            self._state.device = Device.with_hci(
                self._host_name,
                self._host_address,
                self._state.transport.source,
                self._state.transport.sink,
            )
            self._state.device.keystore = resolve_keystore(  # type: ignore[assignment]
                self._keystore, namespace=str(self._state.device.public_address)
            )
            await self._state.device.power_on()

            target = await self._resolve_target(self._state.device, address, timeout_s)
            logger.info(f"Connecting to {target}")
            self._state.connection = await self._state.device.connect(Address(target))
            self._state.connection.on(Connection.EVENT_DISCONNECTION, self._on_disconnection)
            self._state.connection.on(Connection.EVENT_SECURITY_REQUEST, self._on_security_request)

            await self._encrypt_if_bonded(self._state.connection, self._state.device)

            self._state.peer = Peer(self._state.connection)
            await self._state.peer.discover_all()
            self._state.smp_characteristic = _find_smp_characteristic(self._state.peer)
            await self._state.smp_characteristic.subscribe(self._on_notification)

            max_write = await self._negotiate_mtu(self._state.peer, self._state.connection)

            self._state = Connected(
                transport=self._state.transport,
                device=self._state.device,
                connection=self._state.connection,
                peer=self._state.peer,
                smp_characteristic=self._state.smp_characteristic,
                max_write=max_write,
            )
            logger.info(f"Connected to {target}, max_write={max_write}")
        except Exception:
            logger.exception("connect() failed; tearing down partial state")
            await self.disconnect()
            raise

    @override
    async def disconnect(self) -> None:
        match self._state:
            case Disconnected():
                return
            case Connecting() | Connected() as s:
                await _teardown(s)
                self._state = Disconnected()
                self._disconnected_event.set()
                self._notifications.put_nowait(_DisconnectSentinel())
                logger.info("Disconnected")
            case _:
                assert_never(self._state)

    @override
    async def send(self, data: bytes) -> None:
        if not isinstance(self._state, Connected):
            raise SMPBumbleTransportException(
                f"send() called while in state {type(self._state).__name__}"
            )
        logger.debug(f"Sending {len(data)} bytes, max_write={self._state.max_write}")
        for offset in range(0, len(data), self._state.max_write):
            await self._state.smp_characteristic.write_value(
                data[offset : offset + self._state.max_write]
            )
        logger.debug(f"Sent {len(data)} bytes")

    @override
    async def receive(self) -> bytes:
        buffer = bytearray()

        while len(buffer) < smphdr.Header.SIZE:
            buffer.extend(await self._next_chunk())
        header: Final = smphdr.Header.loads(buffer[: smphdr.Header.SIZE])
        logger.debug(f"Received {header=}")

        message_length: Final = header.length + smphdr.Header.SIZE
        logger.debug(f"Waiting for the rest of the {message_length} byte response")
        while len(buffer) < message_length:
            buffer.extend(await self._next_chunk())

        if len(buffer) > message_length:
            raise SMPBumbleTransportException(
                f"Buffer length {len(buffer)} exceeded expected {message_length}"
            )

        logger.debug(f"Finished receiving {message_length} byte response")
        return bytes(buffer)

    @override
    async def send_and_receive(self, data: bytes) -> bytes:
        await self.send(data)
        return await self.receive()

    async def bonded_devices(self) -> tuple[str, ...]:
        """Return the BD_ADDRs of peers currently in the keystore.

        Works in any state — the keystore is constructed from the configured
        strategy + the local host address.
        """
        bonds = await self._standalone_keystore().get_all()
        return tuple(addr for addr, _keys in bonds)

    async def clear_bond(self, address: str | None = None) -> None:
        """Delete one bond, or all bonds when `address` is `None`.

        Args:
            address: The peer BD_ADDR whose bond should be removed.  If
                `None`, every bond in the keystore is removed.
        """
        keystore = self._standalone_keystore()
        if address is None:
            await keystore.delete_all()
            logger.info("Cleared all bonds")
        else:
            await keystore.delete(address)
            logger.info(f"Cleared bond for {address}")

    def _standalone_keystore(self) -> KeyStore:
        return resolve_keystore(self._keystore, namespace=str(self._host_address))

    async def pair(self, delegate: PairingDelegate, timeout_s: float = 30.0) -> PairingResult:
        """Pair or upgrade security with the connected peer.

        Args:
            delegate: A `PairingDelegate` describing this side's IO capability
                and providing PIN/passkey callbacks.  See
                `smpclient.transport.bumble.pairing`.
            timeout_s: Upper bound on the pairing exchange.

        Returns:
            A `PairingResult` describing the outcome.

        Raises:
            SMPBumbleTransportException: if called outside the `Connected` state.
        """
        if not isinstance(self._state, Connected):
            raise SMPBumbleTransportException(
                f"pair() called while in state {type(self._state).__name__}"
            )
        connection = self._state.connection
        device = self._state.device

        if device.keystore is not None:
            existing = await device.keystore.get(str(connection.peer_address))
            if existing is not None:
                logger.info(f"Already bonded to {connection.peer_address}")
                return PairingAlreadyBonded()

        device.pairing_config_factory = lambda _c: PairingConfig(delegate=delegate)

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        try:
            await asyncio.wait_for(connection.pair(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return PairingTimedOut(elapsed_s=loop.time() - start_time)
        except Exception as e:
            return PairingFailed(
                reason=PairingFailureReason.BUMBLE,
                detail=f"connection.pair() raised: {e!r}",
            )

        if connection.is_encrypted and connection.authenticated:
            bonded = False
            if device.keystore is not None:
                stored = await device.keystore.get(str(connection.peer_address))
                bonded = stored is not None
            return PairingSucceeded(bonded=bonded)

        return PairingFailed(
            reason=PairingFailureReason.AUTH,
            detail=(
                f"post-pair state: is_encrypted={connection.is_encrypted}, "
                f"authenticated={connection.authenticated}"
            ),
        )

    @override
    @property
    def mtu(self) -> int:
        if not isinstance(self._state, Connected):
            return 0
        return self._state.max_write

    def _on_notification(self, data: bytes) -> None:
        # bumble's CharacteristicProxy.subscribe() wraps with a sync on_change
        # that discards async return values, so this must stay sync.
        logger.debug(f"Received notification: {len(data)} bytes")
        self._notifications.put_nowait(data)

    def _on_disconnection(self, reason: int) -> None:
        logger.warning(f"Peer disconnected: reason=0x{reason:02x}")
        self._disconnected_event.set()
        self._notifications.put_nowait(_DisconnectSentinel())

    async def _encrypt_if_bonded(self, connection: Connection, device: Device) -> None:
        # When a bond exists, the SMP characteristic's CCCD write will fail with
        # INSUFFICIENT_ENCRYPTION unless encryption is established first.  We
        # cannot rely on the peer-initiated EVENT_SECURITY_REQUEST flow because
        # it races with GATT discovery — discover_all() may finish (and
        # subscribe() begin) before our async listener has had a chance to
        # complete connection.encrypt().  So: proactively encrypt here, before
        # any GATT traffic.
        if connection.is_encrypted:
            return
        if device.keystore is None:
            return
        existing = await device.keystore.get(str(connection.peer_address))
        if existing is None:
            return
        logger.info(f"Bond exists for {connection.peer_address}; initiating encryption")
        try:
            await connection.encrypt()
        except Exception as e:
            logger.warning(f"connection.encrypt() failed: {e}; subscribe may fail too")

    async def _on_security_request(self, auth_req: object) -> None:
        # Bumble emits this when a bonded peripheral asks us to start
        # encryption.  We auto-encrypt only when an LTK exists; we never
        # auto-initiate a fresh pairing.
        if not isinstance(self._state, Connected):
            return
        connection = self._state.connection
        device = self._state.device
        if connection.is_encrypted:
            return
        if device.keystore is None:
            logger.warning(f"Peer requested security ({auth_req!r}) but no keystore is configured")
            return
        existing = await device.keystore.get(str(connection.peer_address))
        if existing is None:
            logger.warning(
                f"Peer requested security ({auth_req!r}) but no bond exists for "
                f"{connection.peer_address}; call transport.pair(...) explicitly"
            )
            return
        try:
            await connection.encrypt()
        except Exception as e:
            logger.warning(f"connection.encrypt() failed in response to security request: {e}")

    async def _next_chunk(self) -> bytes:
        if self._disconnected_event.is_set():
            raise SMPTransportDisconnected(f"{self.__class__.__name__} peer disconnected")
        chunk = await self._notifications.get()
        match chunk:
            case _DisconnectSentinel():
                raise SMPTransportDisconnected(f"{self.__class__.__name__} peer disconnected")
            case bytes():
                return chunk
            case _:
                assert_never(chunk)

    async def _resolve_target(self, device: Device, address: str, timeout_s: float) -> str:
        if MAC_ADDRESS_PATTERN.match(address):
            return address

        logger.info(f"Scanning for device name {address!r} (eager, up to {timeout_s}s)")
        results = await scan_for_devices(device, timeout_s, name=address, eager=True)
        if not results:
            raise SMPBumbleTransportDeviceNotFound(
                f"No advertising device matched name {address!r} in {timeout_s}s"
            )
        return results[0].address

    async def _negotiate_mtu(self, peer: Peer, connection: Connection) -> int:
        try:
            negotiated = await peer.request_mtu(self._preferred_mtu)
            logger.info(f"Requested MTU {self._preferred_mtu}, negotiated {negotiated}")
            return negotiated - 3
        except Exception as e:
            logger.warning(
                f"MTU exchange failed (preferred {self._preferred_mtu}), "
                f"falling back to ATT MTU {connection.att_mtu}: {e}"
            )
            return connection.att_mtu - 3


async def _teardown(s: Connecting | Connected) -> None:
    # Every step is wrapped so the next still runs: skipping a cleanup step
    # because an earlier one raised leaves bumble in a state where subsequent
    # operations — including process exit — may hang indefinitely.
    if s.smp_characteristic is not None:
        try:
            await s.smp_characteristic.unsubscribe()
        except Exception as e:
            logger.warning(f"smp_characteristic.unsubscribe failed: {e}")

    if s.connection is not None:
        try:
            await s.connection.disconnect()
        except Exception as e:
            logger.warning(f"connection.disconnect failed: {e}")

    if s.device is not None:
        try:
            await s.device.power_off()
        except Exception as e:
            logger.warning(f"device.power_off failed: {e}")

    if s.transport is not None:
        try:
            await s.transport.close()
        except Exception as e:
            logger.warning(f"transport.close failed: {e}")


def _find_smp_characteristic(peer: Peer) -> CharacteristicProxy[bytes]:
    services = peer.get_services_by_uuid(BumbleUUID(str(SMP_SERVICE_UUID)))
    if not services:
        raise SMPBumbleTransportNotSMPServer(f"SMP service {SMP_SERVICE_UUID} not found on peer")
    characteristics = services[0].get_characteristics_by_uuid(
        BumbleUUID(str(SMP_CHARACTERISTIC_UUID))
    )
    if not characteristics:
        raise SMPBumbleTransportNotSMPServer(
            f"SMP characteristic {SMP_CHARACTERISTIC_UUID} not found on peer"
        )
    return characteristics[0]


@asynccontextmanager
async def bumble_device(
    *,
    hci: str = DEFAULT_HCI_TRANSPORT,
    delegate: PairingDelegate | None = None,
    host_address: Address = DEFAULT_HOST_ADDRESS,
    host_name: str = DEFAULT_HOST_NAME,
    keystore: KeystoreStrategy = Tempfile(),
) -> AsyncIterator[Device]:
    """Open an HCI transport, configure a powered-on bumble `Device`, yield it.

    On exit, powers off the device and closes the transport, attempting each
    step regardless of earlier failures.
    """
    async with await open_transport(hci) as transport:
        device = Device.with_hci(host_name, host_address, transport.source, transport.sink)
        device.keystore = resolve_keystore(  # type: ignore[assignment]
            keystore, namespace=str(device.public_address)
        )
        if delegate is not None:
            device.pairing_config_factory = lambda _c: PairingConfig(delegate=delegate)
        await device.power_on()
        try:
            yield device
        finally:
            try:
                await device.power_off()
            except Exception as e:
                logger.warning(f"device.power_off failed: {e}")


async def pair_device(
    address: str,
    delegate: PairingDelegate,
    *,
    hci: str = DEFAULT_HCI_TRANSPORT,
    keystore: KeystoreStrategy = Tempfile(),
    scan_timeout_s: float = 10.0,
    pair_timeout_s: float = 30.0,
    settle_s: float = DEFAULT_POST_PAIR_SETTLE_S,
    force: bool = False,
) -> PairingResult:
    """One-shot bonding: open HCI, connect, pair, disconnect.

    No GATT discovery or subscribe — use this to bond a peer before any SMP
    traffic.  For peers whose SMP characteristic requires encryption, follow
    up with `SMPBumbleTransport(pair_on_connect=delegate)`.

    Args:
        address: BD_ADDR or advertised local name of the peer.
        delegate: A `PairingDelegate` (e.g. `KeyboardOnly(pin_callback)`).
        hci: bumble HCI transport spec.
        keystore: How bond keys are persisted.
        scan_timeout_s: Max scan time when `address` is a local name.
        pair_timeout_s: Upper bound on the pairing exchange.
        settle_s: Wait between successful pair and disconnect so the peer can
            finalize bonding.  See `DEFAULT_POST_PAIR_SETTLE_S`.
        force: When True, delete any existing local bond for this peer and
            pair from scratch.  Use this after the peer has wiped its side.

    Returns:
        A `PairingResult` variant.
    """
    async with bumble_device(hci=hci, delegate=delegate, keystore=keystore) as device:
        ks: Final = device.keystore
        assert ks is not None  # bumble_device always sets it

        if MAC_ADDRESS_PATTERN.match(address):
            target = address
        else:
            hits = await scan_for_devices(device, scan_timeout_s, name=address, eager=True)
            if not hits:
                return PairingFailed(
                    reason=PairingFailureReason.USER_REJECTED,
                    detail=f"no device matched name {address!r} within {scan_timeout_s}s",
                )
            target = hits[0].address

        connection = await device.connect(Address(target))
        try:
            existing = await ks.get(str(connection.peer_address))
            if existing is not None:
                if not force:
                    return PairingAlreadyBonded()
                logger.info(f"force=True; deleting local bond for {connection.peer_address}")
                try:
                    await ks.delete(str(connection.peer_address))
                except Exception as e:
                    logger.warning(f"keystore.delete failed: {e}")

            loop = asyncio.get_running_loop()
            start = loop.time()
            try:
                await asyncio.wait_for(connection.pair(), timeout=pair_timeout_s)
            except asyncio.TimeoutError:
                return PairingTimedOut(elapsed_s=loop.time() - start)
            except Exception as e:
                return PairingFailed(
                    reason=PairingFailureReason.BUMBLE,
                    detail=f"connection.pair() raised: {e!r}",
                )

            if connection.is_encrypted and connection.authenticated:
                logger.info(f"Pair succeeded; settling for {settle_s}s")
                await asyncio.sleep(settle_s)
                stored = await ks.get(str(connection.peer_address))
                return PairingSucceeded(bonded=stored is not None)
            return PairingFailed(
                reason=PairingFailureReason.AUTH,
                detail=(
                    f"post-pair state: is_encrypted={connection.is_encrypted}, "
                    f"authenticated={connection.authenticated}"
                ),
            )
        finally:
            try:
                await connection.disconnect()
            except Exception as e:
                logger.warning(f"connection.disconnect failed: {e}")
