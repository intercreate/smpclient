"""Re-export of the bundled Zephyr HCI controller firmware.

Importing this module requires the `smpclient[hci_firmware]` extra, which
pulls in `zephyr-4.4.0-hci` — an umbrella that depends on every published
`hci_usb` firmware variant.  The umbrella exposes `firmware: Firmware`, a
NamedTuple whose fields are the per-variant modules, so callers get typed
attribute access plus IDE autocomplete:

    >>> from smpclient.transport.firmware.hci import firmware
    >>> firmware.nrf52840dk_default.HEX_PATH  # doctest: +SKIP
    PosixPath('.../zephyr_4_4_0_hci_usb_nrf52840dk_default/firmware.hex')
"""

try:
    from zephyr_4_4_0_hci import Firmware as Firmware
    from zephyr_4_4_0_hci import firmware as firmware
except ModuleNotFoundError as e:
    if e.name != "zephyr_4_4_0_hci":
        raise
    raise ModuleNotFoundError(
        "Bundled Zephyr HCI firmware is not installed.  Install the "
        "`smpclient[hci_firmware]` extra (or add the `zephyr-4.4.0-hci` package)."
    ) from e
