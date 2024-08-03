"""Perform a full DFU routine."""

import argparse
import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Final, Tuple

from serial.tools.list_ports import comports
from smp import error as smperr
from smp.os_management import OS_MGMT_RET_RC

from smpclient import SMPClient
from smpclient.generics import SMPRequest, TEr1, TEr2, TRep, error, error_v1, error_v2, success
from smpclient.mcuboot import IMAGE_TLV, ImageInfo
from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
from smpclient.requests.os_management import ResetWrite
from smpclient.transport.serial import SMPSerialTransport

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

HEX_PATTERN: Final = re.compile(r'a_smp_dut_(\d+)_(\d+)_(\d+)[\.merged]?\.hex')
MCUBOOT_HEX_PATTERN: Final = re.compile(r'mcuboot_a_(\d+)_(\d+)_(\d+)\.merged\.hex')


async def main() -> None:
    parser = argparse.ArgumentParser(description="Do an SMP DFU test")
    parser.add_argument("board", help='Name of the board; the "BUT"')
    parser.add_argument(
        "--hex",
        help="a_smp_dut_<line_length>_<line_buffers>_<netbuf_size>.merged.hex",
        default="a_smp_dut_128_2_256.merged.hex",
        required=False,
        type=str,
    )
    args: Final = parser.parse_args()

    hex: Final[str] = args.hex
    print(f"Using hex: {hex}")

    match = HEX_PATTERN.match(hex)
    testing_mcuboot: Final = match is None
    if testing_mcuboot:
        match = MCUBOOT_HEX_PATTERN.match(hex)
        # This example uses CONFIG_BOOT_SERIAL_WAIT_FOR_DFU=y to enter MCUBoot
        if match is None:
            raise ValueError(f"Invalid hex: {hex}")
    assert match is not None

    line_length, line_buffers, max_smp_encoded_frame_size = map(int, match.groups())

    print(f"Using line_length: {line_length}")
    print(f"Using line_buffers: {line_buffers}")
    print(f"Using max_smp_encoded_frame_size (netbuf): {max_smp_encoded_frame_size}")

    a_smp_bin: Final = hex.replace(".merged.hex", ".bin")
    print(f"Using a_smp_dut.bin: {a_smp_bin}")

    dut_folder: Final = Path(__file__).parent.parent / "duts" / args.board / "usb"
    print(f"Using DUT folder: {dut_folder}")
    hex_path: Final = dut_folder / hex

    if "merged" in str(hex):
        print(f"Using merged.hex: {hex_path}")
        print("Flashing the merged.hex...")
    else:
        mcuboot_path: Final = dut_folder / "mcuboot.hex"
        print(f"Using mcuboot: {mcuboot_path}")
        print("Flashing the mcuboot.hex...")
        assert subprocess.run(get_runner_command(args.board, mcuboot_path)).returncode == 0

        print(f"Using app hex: {hex_path}")
        print("Flashing the app hex...")

    assert subprocess.run(get_runner_command(args.board, hex_path)).returncode == 0

    a_smp_dut_hash: Final = ImageInfo.load_file(str(dut_folder / a_smp_bin)).get_tlv(
        IMAGE_TLV.SHA256
    )
    print(f"A SMP DUT hash: {a_smp_dut_hash}")

    b_smp_dut_path: Final = dut_folder / f"{'mcuboot_' if testing_mcuboot else ''}b_smp_dut.bin"
    b_smp_dut_hash: Final = ImageInfo.load_file(str(b_smp_dut_path)).get_tlv(IMAGE_TLV.SHA256)
    print(f"B SMP DUT hash: {b_smp_dut_hash}")

    with open(b_smp_dut_path, "rb") as f:
        b_smp_dut_bin: Final = f.read()

    smp_server_pid: Final = 0x000A if not testing_mcuboot else 0x000C

    print()
    print("Searching for SMP DUT...", end="", flush=True)
    while not any(smp_server_pid == p.pid for p in comports()):
        print(".", end="", flush=True)
        await asyncio.sleep(1)
    port_a = next(p for p in comports() if smp_server_pid == p.pid)
    print(f"OK - found DUT at {port_a.device}")

    await asyncio.sleep(1)

    print("Connecting to SMP DUT...", end="", flush=True)
    async with SMPClient(
        SMPSerialTransport(
            max_smp_encoded_frame_size=max_smp_encoded_frame_size,
            line_length=line_length,
            line_buffers=line_buffers,
        ),
        port_a.device,
    ) as client:
        print("OK")

        async def ensure_request(request: SMPRequest[TRep, TEr1, TEr2]) -> TRep:
            print("Sending request...", end="", flush=True)
            response = await client.request(request)
            print("OK")

            if success(response):
                print(f"Received response: {response}")
                return response
            elif error(response):
                raise Exception(f"Received error: {response}")
            else:
                raise Exception(f"Unknown response: {response}")

        response = await ensure_request(ImageStatesRead())
        assert response.images[0].hash == a_smp_dut_hash.value
        assert response.images[0].slot == 0

        print()
        start_s = time.time()

        # TODO: MCUBoot should allow 0, 1, or 2 here but only 2 works!
        #       It would be best to test with 1 to avoid the swap.  And test
        #       with CONFIG_SINGLE_APPLICATION_SLOT=y.
        #       Refer to dutfirmware/mcuboot_usb.conf
        slot: Final = 2 if testing_mcuboot else 0

        print(f"Uploading {b_smp_dut_path} to slot {slot}")
        print()

        async for offset in client.upload(b_smp_dut_bin, slot=slot, first_timeout_s=2.500):
            print(
                f"\rUploaded {offset:,} / {len(b_smp_dut_bin):,} Bytes | "
                f"{offset / (time.time() - start_s) / 1000:.2f} KB/s           ",
                end="",
                flush=True,
            )

        print()
        response = await ensure_request(ImageStatesRead())
        assert response.images[1].hash == b_smp_dut_hash.value
        assert response.images[1].slot == 1
        print("Confirmed the upload")

        # TODO: complete the testing with swap and reset.  It is not working
        #       with the current test images.
        if testing_mcuboot:
            return

        print()
        print("Marking B SMP DUT for test...")
        await ensure_request(ImageStatesWrite(hash=response.images[1].hash))

        print()
        print("Resetting for swap...")
        reset_response = await client.request(ResetWrite())
        if error_v1(reset_response):
            assert reset_response.rc == smperr.MGMT_ERR.EOK
        elif error_v2(reset_response):
            assert reset_response.err.rc == OS_MGMT_RET_RC.OK

    print()
    print("Searching for B SMP DUT...", end="", flush=True)
    while not any(0x000B == p.pid for p in comports()):
        print(".", end="", flush=True)
        await asyncio.sleep(1)
    port_b = next(p for p in comports() if 0x000B == p.pid)
    print(f"OK - found DUT B at {port_b.device}")

    print("Connecting to B SMP DUT...", end="", flush=True)
    async with SMPClient(
        SMPSerialTransport(
            max_smp_encoded_frame_size=max_smp_encoded_frame_size,
            line_length=line_length,
            line_buffers=line_buffers,
        ),
        port_b.device,
    ) as client:
        print("OK")

        print()
        print("Sending request...", end="", flush=True)
        images = await client.request(ImageStatesRead())
        print("OK")

        if success(images):
            print(f"Received response: {images}")
            # assert the swap - B is in primary, A has been swapped to secondary
            assert images.images[0].hash == b_smp_dut_hash.value
            assert images.images[0].slot == 0
            assert images.images[1].hash == a_smp_dut_hash.value
            assert images.images[1].slot == 1
            print()
            print("Confirmed the swap")
        elif error(images):
            raise SystemExit(f"Received error: {images}")
        else:
            raise SystemExit(f"Unknown response: {images}")


def get_runner_command(board: str, hex_path: Path) -> Tuple[str, ...]:
    if "nrf" in board:
        print("Using the nrfjprog runner")
        return ("nrfjprog", "--recover", "--reset", "--verify", "--program", str(hex_path))
    elif "mimxrt" in board:
        print("Using the NXP linkserver runner")
        return ("linkserver", "flash", "MIMXRT1062xxxxA:EVK-MIMXRT1060", "load", str(hex_path))
    else:
        raise ValueError(f"Don't know what runner to use for {board}")


if __name__ == "__main__":
    asyncio.run(main())
