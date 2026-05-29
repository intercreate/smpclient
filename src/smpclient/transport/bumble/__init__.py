"""A bumble-backed `SMPTransport` driving an external HCI controller over GATT."""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Final, NamedTuple, Protocol, TypeAlias
from uuid import UUID

try:
    from bumble.core import UUID as BumbleUUID
    from bumble.device import Connection, Device, Peer
    from bumble.gatt_client import CharacteristicProxy
    from bumble.hci import Address, HCI_ErrorCode
    from bumble.keys import KeyStore
    from bumble.pairing import PairingConfig, PairingDelegate
    from bumble.smp import AuthReq
    from bumble.transport import open_transport
    from bumble.transport.common import Transport
except ModuleNotFoundError as e:
    if e.name == "bumble":
        raise ImportError(
            "Bumble transport requires the 'bumble' extra. Use smpclient[bumble]"
        ) from e
    raise

from smp import header as smphdr
from typing_extensions import assert_never, override

from smpclient.exceptions import SMPClientException
from smpclient.transport import (
    SMP_CHARACTERISTIC_UUID,
    SMP_SERVICE_UUID,
    SMPTransport,
    SMPTransportDisconnected,
)
from smpclient.transport.bumble.device import (
    DEFAULT_HCI_TRANSPORT,
    DEFAULT_HOST_ADDRESS,
    DEFAULT_HOST_NAME,
    MAC_ADDRESS_PATTERN,
    bumble_device,
)
from smpclient.transport.bumble.keystore import KeystoreStrategy, Tempfile
from smpclient.transport.bumble.keystore import resolve as resolve_keystore
from smpclient.transport.bumble.pairing import (
    DEFAULT_PAIR_TIMEOUT_S,
    DEFAULT_POST_PAIR_SETTLE_S,
    PairingAlreadyBonded,
    PairingFailed,
    PairingResult,
    PairingSucceeded,
    PairingTimedOut,
    encrypt_using_bond,
    pair,
)
from smpclient.transport.bumble.scan import ScanAll, ScanForName, ScanMode, ScanResult
from smpclient.transport.bumble.scan import scan as scan_for_devices

logger: Final = logging.getLogger(__name__)


DEFAULT_PREFERRED_MTU: Final = 247
ATT_WRITE_OVERHEAD: Final = 3
"""ATT write PDU header: 1-byte opcode + 2-byte handle."""


class SMPBumbleTransportException(SMPClientException):
    """Base class for SMP bumble transport exceptions."""


class SMPBumbleTransportDeviceNotFound(SMPBumbleTransportException):
    """Raised when no advertising device matches the requested scan filter."""


class SMPBumbleTransportNotSMPServer(SMPBumbleTransportException):
    """Raised when the connected peripheral does not expose the SMP characteristic."""


class Disconnected(NamedTuple): ...


class _DisconnectSentinel(NamedTuple): ...


@dataclass(slots=True)
class Connecting:
    transport: Transport | None = None
    device: Device | None = None
    connection: Connection | None = None
    peer: Peer | None = None
    smp_characteristic: CharacteristicProxy[bytes] | None = None


class ConnectedProtocol(Protocol):
    @property
    def connection(self) -> Connection: ...
    @property
    def peer(self) -> Peer: ...
    @property
    def smp_characteristic(self) -> CharacteristicProxy[bytes]: ...
    @property
    def max_write(self) -> int: ...


class Connected(NamedTuple):
    transport: Transport
    device: Device
    connection: Connection
    peer: Peer
    smp_characteristic: CharacteristicProxy[bytes]
    max_write: int


class ConnectedBorrowed(NamedTuple):
    connection: Connection
    peer: Peer
    smp_characteristic: CharacteristicProxy[bytes]
    max_write: int


_State: TypeAlias = Disconnected | Connecting | Connected | ConnectedBorrowed


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
        pair_on_connect: PairingDelegate | None = None,
        pair_timeout_s: float = DEFAULT_PAIR_TIMEOUT_S,
        settle_s: float = DEFAULT_POST_PAIR_SETTLE_S,
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
            pair_on_connect: When set, `connect()` will pair with this
                delegate after LE-connect and before GATT discovery, so
                peripherals whose SMP characteristic requires encryption work
                on first connect.  When a bond already exists, the proactive
                `connection.encrypt()` path is used instead — pairing is only
                attempted when no LTK is on file.
            pair_timeout_s: Upper bound on the pairing exchange used by
                `pair_on_connect` and `pair()`.
            settle_s: Wait between successful pair and proceeding (or
                disconnecting) so the peer can finalize bonding.
        """
        self._hci: Final = hci
        self._host_address: Final = host_address
        self._host_name: Final = host_name
        self._keystore: Final = keystore
        self._preferred_mtu: Final = preferred_mtu
        self._pair_on_connect: Final = pair_on_connect
        self._pair_timeout_s: Final = pair_timeout_s
        self._settle_s: Final = settle_s

        self._state: _State = Disconnected()

        self._notifications: Final[asyncio.Queue[bytes | _DisconnectSentinel]] = asyncio.Queue()
        self._disconnected_event: Final = asyncio.Event()
        self._disconnected_event.set()

        # Pair-on-connect coordination — both `connect()` and `_on_security_request`
        # may try to drive the SMP exchange concurrently when the peer issues a
        # security request immediately after LE-connect.  Lock + cached result
        # ensure exactly one pair() call.
        self._pair_lock: asyncio.Lock | None = None
        self._pair_result: PairingResult | None = None

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
        self._pair_lock = asyncio.Lock()
        self._pair_result = None

        try:
            self._state.transport = await open_transport(self._hci)
            self._state.device = Device.with_hci(
                self._host_name,
                self._host_address,
                self._state.transport.source,
                self._state.transport.sink,
            )
            self._state.device.keystore = resolve_keystore(  # type: ignore[assignment]
                self._keystore, namespace=str(self._host_address)
            )
            # Install the pairing delegate before `device.connect()` so it's in
            # place if the peer issues a security request the instant the LE
            # link is up (e.g. Zephyr peripherals built with
            # `CONFIG_BT_SMP_ENFORCE_MITM=y`).
            if self._pair_on_connect is not None:
                delegate: Final = self._pair_on_connect
                self._state.device.pairing_config_factory = lambda _c: PairingConfig(
                    delegate=delegate
                )
            await self._state.device.power_on()

            target = await _resolve_target(self._state.device, address, timeout_s)
            logger.info(f"Connecting to {target}")
            self._state.connection = await self._state.device.connect(Address(target))
            self._state.connection.on(Connection.EVENT_DISCONNECTION, self._on_disconnection)
            self._state.connection.on(Connection.EVENT_SECURITY_REQUEST, self._on_security_request)

            await encrypt_using_bond(self._state.connection, self._state.device)

            if self._pair_on_connect is not None and not self._state.connection.is_encrypted:
                pair_result = await self._pair_during_connect()
                match pair_result:
                    case PairingSucceeded() | PairingAlreadyBonded():
                        pass
                    case PairingTimedOut(elapsed_s):
                        raise SMPBumbleTransportException(
                            f"pair_on_connect timed out after {elapsed_s:.1f}s"
                        )
                    case PairingFailed(reason, detail):
                        raise SMPBumbleTransportException(
                            f"pair_on_connect failed: {reason.value}: {detail}"
                        )
                    case _:
                        assert_never(pair_result)

            self._state.peer = Peer(self._state.connection)
            await self._state.peer.discover_all()
            self._state.smp_characteristic = _find_smp_characteristic(self._state.peer)
            await self._state.smp_characteristic.subscribe(self._on_notification)

            max_write = await _negotiate_mtu(
                self._state.peer, self._state.connection, self._preferred_mtu
            )

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
            case ConnectedBorrowed():
                await self._teardown_borrowed()
            case _:
                assert_never(self._state)
        self._state = Disconnected()
        self._disconnected_event.set()
        self._notifications.put_nowait(_DisconnectSentinel())
        logger.info("Disconnected")

    @override
    async def send(self, data: bytes) -> None:
        state: Final = self._require_connected("send")
        logger.debug(f"Sending {len(data)} bytes, max_write={state.max_write}")
        for offset in range(0, len(data), state.max_write):
            await state.smp_characteristic.write_value(data[offset : offset + state.max_write])
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

    @staticmethod
    async def scan(
        *,
        hci: str = DEFAULT_HCI_TRANSPORT,
        timeout_s: float = 5.0,
        mode: ScanMode = ScanAll(),
        service_uuid: UUID | None = SMP_SERVICE_UUID,
    ) -> tuple[ScanResult, ...]:
        """Scan for advertising devices via a one-shot bumble device."""
        async with bumble_device(hci=hci) as device:
            return await scan_for_devices(device, timeout_s, mode, service_uuid=service_uuid)

    async def use_connection(
        self,
        connection: Connection,
        *,
        peer: Peer | None = None,
    ) -> None:
        """Adopt a caller-owned `Connection`; `disconnect()` only unsubscribes."""
        if not isinstance(self._state, Disconnected):
            raise SMPBumbleTransportException(
                f"use_connection() called while in state {type(self._state).__name__}"
            )

        while not self._notifications.empty():
            self._notifications.get_nowait()
        self._disconnected_event.clear()

        connection.on(Connection.EVENT_DISCONNECTION, self._on_disconnection)

        if not (p := peer if peer is not None else Peer(connection)).services:
            await p.discover_all()
        smp_char: Final = _find_smp_characteristic(p)
        await smp_char.subscribe(self._on_notification)
        max_write: Final = await _negotiate_mtu(p, connection, self._preferred_mtu)

        self._state = ConnectedBorrowed(
            connection=connection,
            peer=p,
            smp_characteristic=smp_char,
            max_write=max_write,
        )
        logger.info(f"Borrowing connection to {connection.peer_address}, max_write={max_write}")

    async def bonded_devices(self) -> tuple[str, ...]:
        """Return the BD_ADDRs of peers currently in the keystore."""
        return tuple(addr for addr, _keys in await self._standalone_keystore().get_all())

    async def clear_bond(self, address: str) -> None:
        """Delete the bond for `address` from the keystore."""
        await self._standalone_keystore().delete(address)
        logger.info(f"Cleared bond for {address}")

    async def clear_bonds(self) -> None:
        """Delete every bond from the keystore."""
        await self._standalone_keystore().delete_all()
        logger.info("Cleared all bonds")

    def _standalone_keystore(self) -> KeyStore:
        return resolve_keystore(self._keystore, namespace=str(self._host_address))

    async def pair(
        self,
        delegate: PairingDelegate,
        timeout_s: float = DEFAULT_PAIR_TIMEOUT_S,
        *,
        force: bool = False,
        settle_s: float = DEFAULT_POST_PAIR_SETTLE_S,
    ) -> PairingResult:
        """Pair or upgrade security with the connected peer.

        Args:
            delegate: A `PairingDelegate` describing this side's IO capability
                and providing PIN/passkey callbacks.  See
                `smpclient.transport.bumble.pairing`.
            timeout_s: Upper bound on the pairing exchange.
            force: When True, delete any existing local bond first and pair
                from scratch.
            settle_s: Sleep after successful pair so the peer can finalize
                bonding.  See `DEFAULT_POST_PAIR_SETTLE_S`.

        Returns:
            A `PairingResult` describing the outcome.

        Raises:
            SMPBumbleTransportException: if called outside the `Connected` state.
        """
        match self._state:
            case Connected() as s:
                return await pair(
                    s.connection,
                    s.device,
                    delegate,
                    pair_timeout_s=timeout_s,
                    settle_s=settle_s,
                    force=force,
                )
            case ConnectedBorrowed():
                raise SMPBumbleTransportException(
                    "pair() not supported on a borrowed connection; "
                    "pair via the owning Device instead"
                )
            case Disconnected() | Connecting():
                raise SMPBumbleTransportException(
                    f"pair() called while in state {type(self._state).__name__}"
                )
            case _:
                assert_never(self._state)

    @override
    @property
    def mtu(self) -> int:
        return self._require_connected("mtu").max_write

    def _require_connected(self, op: str) -> ConnectedProtocol:
        match self._state:
            case Connected() | ConnectedBorrowed() as s:
                return s
            case Disconnected() | Connecting():
                raise SMPBumbleTransportException(
                    f"{op} called while in state {type(self._state).__name__}"
                )
            case _:
                assert_never(self._state)

    def _on_notification(self, data: bytes) -> None:
        # bumble's CharacteristicProxy.subscribe() wraps with a sync on_change
        # that discards async return values, so this must stay sync.
        logger.debug(f"Received notification: {len(data)} bytes")
        self._notifications.put_nowait(data)

    def _on_disconnection(self, reason: int) -> None:
        named: HCI_ErrorCode | None
        try:
            named = HCI_ErrorCode(reason)
        except ValueError:
            named = None
        msg: Final = f"Peer disconnected: reason=0x{reason:02x}" + (
            f" ({named.name})" if named is not None else ""
        )
        match named:
            case HCI_ErrorCode.CONNECTION_TERMINATED_BY_LOCAL_HOST_ERROR:
                logger.debug(msg)
            case (
                HCI_ErrorCode.REMOTE_USER_TERMINATED_CONNECTION_ERROR
                | HCI_ErrorCode.REMOTE_DEVICE_TERMINATED_CONNECTION_DUE_TO_LOW_RESOURCES_ERROR
                | HCI_ErrorCode.REMOTE_DEVICE_TERMINATED_CONNECTION_DUE_TO_POWER_OFF_ERROR
            ):
                logger.info(msg)
            case _:
                logger.warning(msg)
        self._disconnected_event.set()
        self._notifications.put_nowait(_DisconnectSentinel())

    async def _on_security_request(self, auth_req: AuthReq) -> None:
        match self._state:
            case Connected() as s:
                # Auto-encrypt only when an LTK exists; never auto-initiate a fresh pair.
                logger.debug(f"Peer security request: {auth_req!r}")
                await encrypt_using_bond(s.connection, s.device)
            case Connecting():
                # Peer-initiated SMP during the connect() flow.  If the user
                # provided a `pair_on_connect` delegate, drive the pairing
                # exchange now via the shared idempotent helper so we don't
                # race with the central-driven pair() that connect() will
                # also attempt.
                if self._pair_on_connect is None:
                    logger.warning(
                        f"Peer security request {auth_req!r} during connect() but no "
                        "pair_on_connect delegate; dropping"
                    )
                    return
                logger.info(f"Peer security request {auth_req!r} during connect(); initiating pair")
                await self._pair_during_connect()
            case ConnectedBorrowed():
                logger.info(
                    f"Ignoring peer security request {auth_req!r}: "
                    "caller owns the Device and its keystore"
                )
            case Disconnected():
                logger.warning(
                    f"Peer security request {auth_req!r} in unexpected state Disconnected"
                )
            case _:
                assert_never(self._state)

    async def _pair_during_connect(self) -> PairingResult:
        """Idempotent pair-on-connect driver.

        Both `connect()` and `_on_security_request` (when state is `Connecting`)
        may invoke this concurrently.  The lock + cached result ensure only the
        first caller runs the exchange; later callers return the cached result.
        """
        assert self._pair_lock is not None
        assert self._pair_on_connect is not None
        async with self._pair_lock:
            if self._pair_result is not None:
                return self._pair_result
            assert isinstance(self._state, Connecting)
            assert self._state.connection is not None
            assert self._state.device is not None
            self._pair_result = await pair(
                self._state.connection,
                self._state.device,
                self._pair_on_connect,
                pair_timeout_s=self._pair_timeout_s,
                settle_s=self._settle_s,
                force=False,
            )
            return self._pair_result

    async def _next_chunk(self) -> bytes:
        if self._disconnected_event.is_set():
            raise SMPTransportDisconnected(f"{self.__class__.__name__} peer disconnected")
        match chunk := await self._notifications.get():
            case _DisconnectSentinel():
                raise SMPTransportDisconnected(f"{self.__class__.__name__} peer disconnected")
            case bytes():
                return chunk
            case _:
                assert_never(chunk)

    async def _teardown_borrowed(self) -> None:
        assert isinstance(self._state, ConnectedBorrowed)
        try:
            await self._state.smp_characteristic.unsubscribe()
        except Exception as e:
            logger.warning(f"smp_characteristic.unsubscribe failed: {e}")
        try:
            self._state.connection.remove_listener(
                Connection.EVENT_DISCONNECTION, self._on_disconnection
            )
        except Exception as e:
            logger.warning(f"remove_listener(EVENT_DISCONNECTION) failed: {e}")


async def _resolve_target(device: Device, address: str, timeout_s: float) -> str:
    if MAC_ADDRESS_PATTERN.match(address):
        return address

    logger.info(f"Scanning for device name {address!r} (eager, up to {timeout_s}s)")
    if not (results := await scan_for_devices(device, timeout_s, ScanForName(address))):
        raise SMPBumbleTransportDeviceNotFound(
            f"No advertising device matched name {address!r} in {timeout_s}s"
        )
    return results[0].address


async def _negotiate_mtu(peer: Peer, connection: Connection, preferred_mtu: int) -> int:
    # ATT MTU only ratchets up; if the link already negotiated >= preferred,
    # there's nothing to request — and the round-trip would log misleadingly.
    if connection.att_mtu >= preferred_mtu:
        logger.debug(f"ATT MTU already at {connection.att_mtu}; skipping request")
        return connection.att_mtu - ATT_WRITE_OVERHEAD
    try:
        negotiated: Final = await peer.request_mtu(preferred_mtu)
        logger.info(f"Requested MTU {preferred_mtu}, negotiated {negotiated}")
        return negotiated - ATT_WRITE_OVERHEAD
    except Exception as e:
        logger.warning(
            f"MTU exchange failed (preferred {preferred_mtu}), "
            f"falling back to ATT MTU {connection.att_mtu}: {e}"
        )
        return connection.att_mtu - ATT_WRITE_OVERHEAD


async def _teardown(s: Connecting | Connected) -> None:
    # Per-step try/except is load-bearing: skipping a cleanup step can leave
    # bumble hung on subsequent operations (including process exit).
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
    if not (services := peer.get_services_by_uuid(BumbleUUID(str(SMP_SERVICE_UUID)))):
        raise SMPBumbleTransportNotSMPServer(f"SMP service {SMP_SERVICE_UUID} not found on peer")
    if not (
        characteristics := services[0].get_characteristics_by_uuid(
            BumbleUUID(str(SMP_CHARACTERISTIC_UUID))
        )
    ):
        raise SMPBumbleTransportNotSMPServer(
            f"SMP characteristic {SMP_CHARACTERISTIC_UUID} not found on peer"
        )
    return characteristics[0]


@asynccontextmanager
async def borrowed_connection(
    transport: SMPBumbleTransport,
    connection: Connection,
    *,
    peer: Peer | None = None,
) -> AsyncIterator[SMPBumbleTransport]:
    """`async with`-friendly wrapper around `use_connection()` + `disconnect()`."""
    await transport.use_connection(connection, peer=peer)
    try:
        yield transport
    finally:
        await transport.disconnect()
