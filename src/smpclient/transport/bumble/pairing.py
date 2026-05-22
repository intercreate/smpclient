"""Pairing delegates and result types for the bumble SMP transport.

Three IO-capability flavors are provided directly; users can subclass
`bumble.pairing.PairingDelegate` for more exotic flows.

Pairing outcomes are exposed as a sum type (`PairingResult`) so callers
exhaustively handle every case.  See `SMPBumbleTransport.pair`.
"""

from enum import Enum
from typing import Awaitable, Callable, NamedTuple, TypeAlias

from bumble.pairing import PairingDelegate

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
