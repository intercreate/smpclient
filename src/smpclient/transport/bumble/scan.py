"""BLE scanning helpers for discovering SMP servers via bumble."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final, NamedTuple, TypeAlias
from uuid import UUID

from bumble.core import UUID as BumbleUUID
from bumble.device import Advertisement, Device
from typing_extensions import assert_never

logger = logging.getLogger(__name__)


class ScanResult(NamedTuple):
    address: str
    name: str | None
    rssi: int | None
    has_smp_service: bool


class ScanAll(NamedTuple):
    """Run for the full timeout and return every advertisement observed."""


class ScanForName(NamedTuple):
    """Filter advertisements by complete or shortened local name.

    When `eager=True` (default), the scan returns at the first matching
    advertisement.  Set `eager=False` to run for the full timeout and collect
    every advertised device with that name — useful when multiple peers
    legitimately share a name and the caller wants to enumerate them.
    """

    name: str
    eager: bool = True


ScanMode: TypeAlias = ScanAll | ScanForName


def _extract_name(advertisement: Advertisement) -> str | None:
    for ad_type in (
        advertisement.data.COMPLETE_LOCAL_NAME,
        advertisement.data.SHORTENED_LOCAL_NAME,
    ):
        value = advertisement.data.get(ad_type)
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def _advertises_service(advertisement: Advertisement, target: BumbleUUID) -> bool:
    # Compare BumbleUUID instances directly: their __eq__ handles cross
    # bit-width equality (16/32/128-bit via the Bluetooth base UUID).  Do not
    # round-trip through `str()` — bumble's str() for a 16-bit UUID emits
    # "UUID-16:FFFF" which the BumbleUUID constructor refuses to parse back.
    for ad_type in (
        advertisement.data.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
        advertisement.data.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
        advertisement.data.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
        advertisement.data.INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
    ):
        uuids = advertisement.data.get(ad_type)
        if not uuids:
            continue
        for u in uuids:  # type: ignore[union-attr]
            if u == target:
                return True
    return False


async def scan(
    device: Device,
    timeout_s: float,
    mode: ScanMode = ScanAll(),
    *,
    service_uuid: UUID | None = None,
) -> tuple[ScanResult, ...]:
    """Scan for advertising devices.

    Caller owns the lifecycle of `device` (`power_on` / `power_off`).

    Args:
        device: A bumble `Device` already powered on.
        timeout_s: Maximum scan duration in seconds.
        mode: A `ScanMode` sum type.  Defaults to `ScanAll()`, which returns
            everything observed for the full timeout.  `ScanForName(name)`
            (or `ScanForName(name, eager=True)`) returns at the first
            advertisement matching `name`;  `ScanForName(name, eager=False)`
            runs the full timeout and returns every matching device — useful
            for enumerating peers that share a name.
        service_uuid: When set, results have `has_smp_service=True` if they
            advertise this UUID.  Marker only — does not filter the result set.

    Returns:
        Observed `ScanResult`s, deduplicated by address.
    """
    target: Final = BumbleUUID(str(service_uuid)) if service_uuid is not None else None
    seen: Final[dict[str, ScanResult]] = {}
    first_match: Final = asyncio.Event() if isinstance(mode, ScanForName) and mode.eager else None

    def on_advertisement(advertisement: Advertisement) -> None:
        address = str(advertisement.address)
        observed_name = _extract_name(advertisement)
        rssi = getattr(advertisement, "rssi", None)
        has_smp = target is not None and _advertises_service(advertisement, target)

        existing = seen.get(address)
        result = ScanResult(
            address=address,
            name=observed_name
            if observed_name is not None
            else (existing.name if existing else None),
            rssi=rssi if rssi is not None else (existing.rssi if existing else None),
            has_smp_service=(existing.has_smp_service if existing else False) or has_smp,
        )
        seen[address] = result
        if first_match is not None and isinstance(mode, ScanForName) and result.name == mode.name:
            first_match.set()

    async with _scanning(device, on_advertisement):
        if first_match is not None:
            try:
                await asyncio.wait_for(first_match.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(timeout_s)

    match mode:
        case ScanAll():
            return tuple(seen.values())
        case ScanForName(name, _eager):
            return tuple(r for r in seen.values() if r.name == name)
        case _:
            assert_never(mode)


@asynccontextmanager
async def _scanning(
    device: Device,
    listener: Callable[[Advertisement], None],
) -> AsyncIterator[None]:
    """Attach `listener` and run `start_scanning`; tear down on exit."""
    device.on("advertisement", listener)
    try:
        await device.start_scanning()
        try:
            yield
        finally:
            try:
                await asyncio.wait_for(device.stop_scanning(), timeout=2.0)
            except Exception as e:
                logger.warning(f"stop_scanning failed: {e}")
    finally:
        try:
            device.remove_listener("advertisement", listener)
        except Exception as e:
            logger.warning(f"remove_listener('advertisement') failed: {e}")
