"""Pairing delegates, results, and module-level pairing helpers.

Three IO-capability flavors are provided directly; users can subclass
`bumble.pairing.PairingDelegate` for more exotic flows.

Pairing outcomes are exposed as a sum type (`PairingResult`) so callers
exhaustively handle every case.  See `SMPBumbleTransport.pair`.
"""

import asyncio
import logging
from enum import Enum
from typing import Awaitable, Callable, Final, NamedTuple, TypeAlias

from bumble.device import Connection, Device
from bumble.hci import Address
from bumble.pairing import PairingConfig, PairingDelegate

from smpclient.transport.bumble.device import (
    DEFAULT_HCI_TRANSPORT,
    MAC_ADDRESS_PATTERN,
    bumble_device,
)
from smpclient.transport.bumble.keystore import KeystoreStrategy, Tempfile
from smpclient.transport.bumble.scan import ScanForName
from smpclient.transport.bumble.scan import scan as scan_for_devices

logger = logging.getLogger(__name__)


DEFAULT_PAIR_TIMEOUT_S: Final = 30.0
DEFAULT_POST_PAIR_SETTLE_S: Final = 1.5
"""After `connection.pair()` returns, peers need a brief settle window to
finalize bonding on their side before disconnect.  Tune per-peer if a
particular peer needs longer."""

PinCallback: TypeAlias = Callable[[], Awaitable[int | None]]
"""Returns the 6-digit PIN the peer is displaying, or `None` to reject pairing."""

DisplayCallback: TypeAlias = Callable[[int], Awaitable[None]]
"""Called with the 6-digit PIN the user must read off the local device to the peer."""


class NoInputNoOutput(PairingDelegate):
    """JustWorks pairing — no MITM protection.  Use only when both sides agree to it."""

    def __init__(self) -> None:
        super().__init__(io_capability=PairingDelegate.NO_OUTPUT_NO_INPUT)


class KeyboardOnly(PairingDelegate):
    """The peer displays a 6-digit PIN; the user enters it via `pin_callback`."""

    def __init__(self, pin_callback: PinCallback) -> None:
        super().__init__(io_capability=PairingDelegate.KEYBOARD_INPUT_ONLY)
        self._pin_callback = pin_callback

    async def get_number(self) -> int | None:
        pin = await self._pin_callback()
        if pin is None:
            return None
        if 0 <= pin <= 999_999:
            return pin
        return None


class DisplayOnly(PairingDelegate):
    """The local device displays a 6-digit PIN via `display_callback`; the peer enters it."""

    def __init__(self, display_callback: DisplayCallback) -> None:
        super().__init__(io_capability=PairingDelegate.DISPLAY_OUTPUT_ONLY)
        self._display_callback = display_callback

    async def display_number(self, number: int, digits: int) -> None:
        await self._display_callback(number)


class PairingFailureReason(Enum):
    AUTH = "authentication"
    ENCRYPTION = "encryption"
    KEY_MISSING = "key_missing"
    USER_REJECTED = "user_rejected"
    NOT_FOUND = "not_found"
    BUMBLE = "bumble"


class PairingSucceeded(NamedTuple):
    bonded: bool
    """True if a long-term key was stored in the device's keystore."""


class PairingAlreadyBonded(NamedTuple):
    """The peer was already bonded; no new pairing was performed."""


class PairingTimedOut(NamedTuple):
    elapsed_s: float


class PairingFailed(NamedTuple):
    reason: PairingFailureReason
    detail: str


PairingResult: TypeAlias = PairingSucceeded | PairingAlreadyBonded | PairingTimedOut | PairingFailed


async def encrypt_using_bond(connection: Connection, device: Device) -> bool:
    """Proactively encrypt `connection` using a stored LTK if one exists.

    The peer-initiated `EVENT_SECURITY_REQUEST` flow races with GATT discovery,
    and `subscribe()` needs encryption first.

    Args:
        connection: An established LE connection.
        device: The local bumble device whose keystore is consulted.

    Returns:
        True if `connection.is_encrypted` after this call; False otherwise.
    """
    if connection.is_encrypted:
        return True
    if device.keystore is None:
        return False
    if (await device.keystore.get(str(connection.peer_address))) is None:
        return False
    logger.debug(f"Bond exists for {connection.peer_address}; initiating encryption")
    try:
        await connection.encrypt()
    except Exception as e:
        logger.warning(f"connection.encrypt() failed: {e}")
        return False
    return connection.is_encrypted


async def pair(
    connection: Connection,
    device: Device,
    delegate: PairingDelegate,
    *,
    pair_timeout_s: float,
    settle_s: float,
    force: bool,
) -> PairingResult:
    """Perform SMP pairing on an established `connection`.

    Args:
        connection: An established `Connection`.
        device: The local bumble `Device` (must have keystore set).
        delegate: The `PairingDelegate` for this exchange.
        pair_timeout_s: Upper bound on `connection.pair()`.
        settle_s: Sleep after successful pair so the peer can finalize bonding.
        force: When True, delete any existing local bond first.

    Returns:
        A `PairingResult` variant.
    """
    if (ks := device.keystore) is not None:
        if (existing := await ks.get(str(connection.peer_address))) is not None:
            if not force:
                logger.info(f"Already bonded to {connection.peer_address}")
                return PairingAlreadyBonded()
            logger.info(f"force=True; deleting local bond for {connection.peer_address}")
            try:
                await ks.delete(str(connection.peer_address))
            except Exception as e:
                logger.warning(f"keystore.delete failed: {e}")
            del existing

    device.pairing_config_factory = lambda _c: PairingConfig(delegate=delegate)

    loop: Final = asyncio.get_running_loop()
    start: Final = loop.time()
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
        stored: Final = await ks.get(str(connection.peer_address)) if ks is not None else None
        return PairingSucceeded(bonded=stored is not None)

    return PairingFailed(
        reason=PairingFailureReason.AUTH,
        detail=(
            f"post-pair state: is_encrypted={connection.is_encrypted}, "
            f"authenticated={connection.authenticated}"
        ),
    )


async def pair_device(
    address: str,
    delegate: PairingDelegate,
    *,
    hci: str = DEFAULT_HCI_TRANSPORT,
    keystore: KeystoreStrategy = Tempfile(),
    scan_timeout_s: float = 10.0,
    pair_timeout_s: float = DEFAULT_PAIR_TIMEOUT_S,
    settle_s: float = DEFAULT_POST_PAIR_SETTLE_S,
    force: bool = False,
) -> PairingResult:
    """One-shot bonding: open HCI, connect, pair, disconnect.

    No GATT discovery or subscribe — use this to bond a peer before any SMP
    traffic.  For peers whose SMP characteristic requires encryption on first
    connect, pass `pair_on_connect=` to `SMPBumbleTransport` instead.

    Args:
        address: BD_ADDR or advertised local name of the peer.
        delegate: A `PairingDelegate` (e.g. `KeyboardOnly(pin_callback)`).
        hci: bumble HCI transport spec.
        keystore: How bond keys are persisted.
        scan_timeout_s: Max scan time when `address` is a local name.
        pair_timeout_s: Upper bound on the pairing exchange.
        settle_s: Wait between successful pair and disconnect so the peer can
            finalize bonding.
        force: When True, delete any existing local bond for this peer and
            pair from scratch.  Use this after the peer has wiped its side.

    Returns:
        A `PairingResult` variant.
    """
    async with bumble_device(hci=hci, delegate=delegate, keystore=keystore) as device:
        if MAC_ADDRESS_PATTERN.match(address):
            target = address
        elif hits := await scan_for_devices(device, scan_timeout_s, ScanForName(address)):
            target = hits[0].address
        else:
            return PairingFailed(
                reason=PairingFailureReason.NOT_FOUND,
                detail=f"no device matched name {address!r} within {scan_timeout_s}s",
            )

        connection: Final = await device.connect(Address(target))
        try:
            return await pair(
                connection,
                device,
                delegate,
                pair_timeout_s=pair_timeout_s,
                settle_s=settle_s,
                force=force,
            )
        finally:
            try:
                await connection.disconnect()
            except Exception as e:
                logger.warning(f"connection.disconnect failed: {e}")
