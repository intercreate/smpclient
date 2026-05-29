"""Re-export of the bundled Zephyr HCI controller firmware."""

try:
    from zephyr_4_4_0_hci import Firmware as Firmware
    from zephyr_4_4_0_hci import firmware as firmware
except ModuleNotFoundError as e:
    if e.name != "zephyr_4_4_0_hci":
        raise
    raise ImportError(
        "Bundled Zephyr HCI firmware requires the 'hci_firmware' extra. Use smpclient[hci_firmware]"
    ) from e
