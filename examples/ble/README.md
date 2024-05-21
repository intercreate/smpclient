# SMPBLETransport examples

Each of these can be run from the root of the repository:
```
python -m examples.ble.<EXAMPLE>
```
e.g.
```
python -m examples.ble.helloworld
```

If the example scripts require arguments then they should use the `argparse`
module.

For example, `examples.ble.upgrade` needs to know what board is being upgraded
so that it can get the matching DUT FW.

```
python -m examples.ble.upgrade --help
```

```
usage: upgrade.py [-h] board

Do an SMP DFU test

positional arguments:
  board       Name of the board; the "BUT"

options:
  -h, --help  show this help message and exit
```
## Upgrade Test

1. The `upgrade` script uses the programmer to flash the `merged.hex` (MCUBoot +
app) of the "A" DUT FW.
2. `smpclient` connects to the DUT and reads the state of images.
3. `smpclient` uploads the "B" DUT FW, marks it for test, and resets the DUT
4. `smpclient` waits for swap to completed then confirms that the "B" DUT is
   loaded.

For the existing SMP BLE DUT examples, the only difference between "A" and "B"
is the advertised name.  See [dutfirmware/](/dutfirmware/) for DUT FW
configuration.

When multiple transports are tested, new configurations will be required.

### Adafruit Feather nRF52840

Product: https://www.adafruit.com/product/4062

> Uses [`nrfjprog`](https://www.nordicsemi.com/Products/Development-tools/nRF-Command-Line-Tools/)
> as flash runner and assumes that it is in PATH

1. Power the board via the micro USB port (with or without data, doesn't matter)
2. Connect a JLink to the board's header
3. Connect the JLink to your host PC's USB port

```
python -m examples.ble.upgrade adafruit_feather_nrf52840
```

### nRF52 DK (nRF52832)

Product: https://www.nordicsemi.com/Products/Development-hardware/nRF52-DK

> Uses [`nrfjprog`](https://www.nordicsemi.com/Products/Development-tools/nRF-Command-Line-Tools/)
> as flash runner and assumes that it is in PATH

1. Connect the board to your PC via the micro USB port (J2)
2. Set the Power switch to ON

```
python -m examples.ble.upgrade nrf52dk_nrf52832
```