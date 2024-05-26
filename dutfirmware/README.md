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

Initialize `west` for the NRF SDK:
```
west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.6.0 
```

Install Zephyr & NRF SDK dependencies:
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
west build -b nrf52840dk_nrf52840 zephyr/samples/subsys/mgmt/mcumgr/smp_svr -- -DEXTRA_CONF_FILE="overlay-cdc.conf;${ENVR_ROOT}/usb_smp_dut.conf;${ENVR_ROOT}/usb_smp_dut_mtu4096.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay"mp_dut_mtu4096.conf" -DEXTRA_DTC_OVERLAY_FILE="usb.overlay"
```