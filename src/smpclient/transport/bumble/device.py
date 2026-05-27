"""Generic bumble `Device` helpers — not SMP-specific."""

import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

from bumble.device import Device
from bumble.hci import Address, AddressType
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport

from smpclient.transport.bumble.keystore import KeystoreStrategy, Tempfile
from smpclient.transport.bumble.keystore import resolve as resolve_keystore

logger = logging.getLogger(__name__)


MAC_ADDRESS_PATTERN: Final = re.compile(r"([0-9A-F]{2}[:]){5}[0-9A-F]{2}$", flags=re.IGNORECASE)

DEFAULT_HCI_TRANSPORT: Final = "usb:0"
DEFAULT_HOST_ADDRESS: Final = Address("F0:5C:81:00:00:01", AddressType.RANDOM_DEVICE)
DEFAULT_HOST_NAME: Final = "smpclient-bumble"


@asynccontextmanager
async def bumble_device(
    *,
    hci: str = DEFAULT_HCI_TRANSPORT,
    delegate: PairingDelegate | None = None,
    host_address: Address = DEFAULT_HOST_ADDRESS,
    host_name: str = DEFAULT_HOST_NAME,
    keystore: KeystoreStrategy = Tempfile(),
) -> AsyncIterator[Device]:
    async with await open_transport(hci) as transport:
        device = Device.with_hci(host_name, host_address, transport.source, transport.sink)
        device.keystore = resolve_keystore(  # type: ignore[assignment]
            keystore, namespace=str(host_address)
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
