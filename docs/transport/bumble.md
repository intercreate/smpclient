# Bumble (BLE)

The bumble transport uses [Google's bumble Bluetooth stack](https://github.com/google/bumble) to
talk directly to an HCI controller, bypassing the host OS Bluetooth stack entirely. This is handy
when you're on a machine where the system BLE stack is flaky or unavailable — CI machines, embedded
Linux, or just wanting a more predictable setup with a USB dongle.

## Install

```
smpclient[bumble]
```

### HCI firmware extra

The `hci` parameter takes any transport spec that bumble's `open_transport()` understands, like
`"usb:0"` for the first USB Bluetooth dongle. If you need firmware for your controller, there's an
optional extra that bundles a pre-built Zephyr HCI image:

```
smpclient[hci_firmware]
```

Or grab both at once:

```
smpclient[bumble,hci_firmware]
```

#### Flashing an nRF52840 DK

The nRF52840 DK is the hardware the bumble transport has been tested on. Flash the bundled firmware
with [nrfutil](https://docs.nordicsemi.com/bundle/nrfutil/page/README.html):

```python
# save the firmware bytes to a file
from smpclient.transport.firmware.hci import firmware

with open("hci_firmware.hex", "wb") as f:
    f.write(firmware)
```

```bash
# flash via JLink
nrfutil device program --firmware hci_firmware.hex --traits jlink
```

After flashing, the DK shows up as a USB HCI device. Use `hci="usb:0"` (or a higher index if
you have multiple USB Bluetooth devices plugged in).

Note: bumble supposedly supports some "ready made" consumer dongles too, but that hasn't been
verified with this transport.

## smpbumble CLI

There's a small CLI app included — `smpbumble` — that lets you scan, pair, and send a quick echo
without writing any code. Good for testing your setup before building anything.

```bash
# see what's advertising nearby ([SMP] marks devices with the SMP service)
smpbumble scan --hci usb:0

# pair with a device (will prompt you to enter the PIN shown on the peripheral)
smpbumble pair AA:BB:CC:DD:EE:FF --hci usb:0

# send an SMP echo to verify the connection works
smpbumble echo AA:BB:CC:DD:EE:FF "hello" --hci usb:0
```

Run `smpbumble --help` for the full list of options.

## Basic usage

```python
import asyncio
from smpclient import SMPClient
from smpclient.transport.bumble import SMPBumbleTransport

async def main() -> None:
    async with SMPClient(SMPBumbleTransport(hci="usb:0"), "AA:BB:CC:DD:EE:FF") as client:
        # use client...
        pass

asyncio.run(main())
```

You can pass a device name instead of a MAC address and the transport will scan for it:

```python
async with SMPClient(SMPBumbleTransport(hci="usb:0"), "MyDevice") as client:
    ...
```

## Scanning

```python
from smpclient.transport.bumble import SMPBumbleTransport

results = await SMPBumbleTransport.scan(hci="usb:0", timeout_s=5.0)
for r in results:
    print(r.address, r.name, r.rssi, r.has_smp_service)
```

## Pairing

Pairing with a PIN isn't fully automatic — the PIN either needs a human to type it in, or your
code needs to read it from somewhere (like the peripheral's serial console). There's no way to
just "auto-pair" with PIN-based security.

### User types the PIN

Use `KeyboardOnly` when someone will be at the terminal to enter the 6-digit PIN:

```python
import asyncio
from smpclient.transport.bumble.pairing import KeyboardOnly, pair_device

async def prompt_pin() -> int | None:
    raw = (await asyncio.to_thread(input, "PIN: ")).strip()
    return int(raw) if raw.isdigit() and len(raw) == 6 else None

result = await pair_device("AA:BB:CC:DD:EE:FF", KeyboardOnly(prompt_pin), hci="usb:0")
```

`smpbumble pair` does the same thing from the command line.

### Reading the PIN over serial (OOB)

If you're running automated tests or the peripheral prints the PIN on a serial port, you can read
it programmatically instead:

```python
import asyncio
import serial_asyncio
from smpclient.transport.bumble.pairing import KeyboardOnly, pair_device

async def read_pin_from_serial() -> int | None:
    reader, _ = await serial_asyncio.open_serial_connection(url="/dev/ttyACM0", baudrate=115200)
    async for line in reader:
        text = line.decode().strip()
        if text.isdigit() and len(text) == 6:
            return int(text)
    return None

result = await pair_device("AA:BB:CC:DD:EE:FF", KeyboardOnly(read_pin_from_serial), hci="usb:0")
```

### Pairing on connect

Some Zephyr peripherals (built with `CONFIG_BT_SMP_ENFORCE_MITM=y`) issue a security request the
moment you connect, before GATT discovery. Pass `pair_on_connect` to handle that:

```python
from smpclient.transport.bumble import SMPBumbleTransport
from smpclient.transport.bumble.pairing import KeyboardOnly

transport = SMPBumbleTransport(hci="usb:0", pair_on_connect=KeyboardOnly(prompt_pin))
```

Bond keys are stored via the `keystore` strategy — see `smpclient.transport.bumble.keystore` for
the options (`Tempfile`, `Local`, `Custom`, `ExistingCustom`, `InMemory`).

## API Reference

::: smpclient.transport.bumble

::: smpclient.transport.bumble.scan

::: smpclient.transport.bumble.keystore

::: smpclient.transport.bumble.pairing

::: smpclient.transport.bumble.device

::: smpclient.transport.firmware.hci
