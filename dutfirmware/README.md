# Generate DUT Firmware for testing SMP

All commands should be run from this folder.

## Setup

> This is not a tutorial on Zephyr environments or build systems!

Create the `venv`:
```
python -m venv .venv
```

Activate the environment (in `dutfirmware/`):
```
. ./envr.ps1
```

Install `west`:
```
pip install west
```

Initialize `west`:
* Zephyr main:
  ```
  west init .
  ```
* Or use the NRF SDK fork and manifest, for example:
  ```
  west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.6.0 
  ```

Install Zephyr dependencies:
```
west update
```

Install Python dependencies:
```
pip install -r zephyr/scripts/requirements.txt
```

Configure west to create new build folders for each board:
```
west config build.dir-fmt "build/{board}"
```

## Usage

Activate the environment (in `dutfirmware/`):
```
. ./envr.ps1
```

### Nordic

> Note: documented from NRF Connect v2.6.0 which is pre Zephyr 3.7.0

Build some FW, for example:
```
west build -b nrf52dk_nrf52832 zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-bt.conf;${ENVR_ROOT}/ble_a_smp_dut.conf"
```

Flash that FW, for example:
```
west flash -d build/nrf52dk_nrf52832 --recover
```

Or, for USB CDC ACM:
```
west build -b adafruit_feather_nrf52840 zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-cdc.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay"
```

Fast USB CDC ACM:
```
west build -b nrf52840dk_nrf52840 zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-cdc.conf;${ENVR_ROOT}/usb_a_smp_dut.conf;${ENVR_ROOT}/usb_smp_dut_512_8_4096.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay"
```

MCUBoot configuration with SMP USB DFU. USB PID will be 0x000C in bootloader.
```
west build -b nrf52840dk_nrf52840 zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-bt.conf;overlay-cdc.conf;${ENVR_ROOT}/usb_a_smp_dut.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay" -Dmcuboot_CONF_FILE="../../../../mcuboot_usb.conf" -Dmcuboot_DTS_FILE="../../../../mcuboot_usb.overlay"
```

### NXP

#### MIMXRT1060-EVKB

> Note: documented on Zephyr v3.7.0-rc3, commit `52a9e7014a70916041ffef4a3549448907578343`

> Note: I installed LinkServer but would rather use JLink.  Also, NXP is silly
> AF and installs to C: 🙄

Create bootloader:
```
west build -b mimxrt1060_evkb -d build/mimxrt1060_evkb_mcuboot bootloader/mcuboot/boot/zephyr -- -DCONFIG_BUILD_OUTPUT_HEX=y
```

Flash bootloader:
> Note: your board will be erased
```
west flash --runner=linkserver -d build/mimxrt1060_evkb_mcuboot
```

Create FW for USB CDC ACM SMP server:
> Note: this generates the firmware "A" found in `examples/duts/mimxrt1060_evkb/usb/a_smp_dut_8192_1_8192.hex
```
west build -b mimxrt1060_evkb zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-cdc.conf;${ENVR_ROOT}/usb_a_smp_dut.conf;${ENVR_ROOT}/usb_smp_dut_8192_1_8192.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay" -DCONFIG_BUILD_OUTPUT_HEX=y
```

Create FW for UDP (ethernet) SMP server:
```
west build -b mimxrt1060_evkb zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-udp.conf;${ENVR_ROOT}/udp_a_smp_dut.conf" -DCONFIG_BUILD_OUTPUT_HEX=y
```

Flash signed app:
```
west flash --runner=linkserver -d build/mimxrt1060_evkb
```

For convenience, you could merge the bootloader and app:
> Note: this merged.hex isn't working and I don't see why not!
```
python zephyr/scripts/build/mergehex.py --output a_smp_dut_8192_1_8192.merged.hex build/mimxrt1060_evkb_mcuboot/zephyr/zephyr.hex build/mimxrt1060_evkb/zephyr/zephyr.signed.hex
```
And then you only have to flash once:
```
west flash --runner=linkserver -d build/mimxrt1060_evkb --hex-file a_smp_dut_8192_1_8192.merged.hex
```

### ST

#### stm32f4_disco

> Note: documented on Zephyr `v3.7.0-1987-g1540bd7d`

Create bootloader with a serial recovery button that can update FW over serial:

```
west build -b stm32f4_disco -d build/stm32f4_disco_mcuboot bootloader/mcuboot/boot/zephyr -- -DCONFIG_BUILD_OUTPUT_HEX=y -DEXTRA_DTC_OVERLAY_FILE="${ENVR_ROOT}/stm32f4_disco_flash_overlay.dts;${ENVR_ROOT}/stm32f4_disco_serial_overlay.dts;${ENVR_ROOT}/stm32f4_disco_serial_recovery_button_overlay.dts" -DEXTRA_CONF_FILE="${ENVR_ROOT}/mcuboot_serial.conf"
```

