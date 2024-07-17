"""Perform a full DFU routine."""

import argparse
import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Final, cast

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from smpclient import SMPClient
from smpclient.generics import SMPRequest, TEr1, TEr2, TRep, error, success
from smpclient.mcuboot import IMAGE_TLV, ImageInfo
from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
from smpclient.requests.os_management import ResetWrite
from smpclient.transport.ble import SMPBLETransport

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Do an SMP DFU test")
    parser.add_argument("board", help='Name of the board; the "BUT"')

    dut_folder: Final = Path(__file__).parent.parent / "duts" / parser.parse_args().board / "ble"
    print(f"Using DUT folder: {dut_folder}")
    merged_hex_path: Final = dut_folder / "a_smp_dut.merged.hex"
    print(f"Using merged.hex: {merged_hex_path}")

    print("Flashing the merged.hex...")
    assert (
        subprocess.run(
            ("nrfjprog", "--recover", "--reset", "--verify", "--program", merged_hex_path)
        ).returncode
        == 0
    )

    a_smp_dut_hash: Final = ImageInfo.load_file(str(dut_folder / "a_smp_dut.bin")).get_tlv(
        IMAGE_TLV.SHA256
    )
    print(f"A SMP DUT hash: {a_smp_dut_hash}")
    b_smp_dut_hash: Final = ImageInfo.load_file(str(dut_folder / "b_smp_dut.bin")).get_tlv(
        IMAGE_TLV.SHA256
    )
    print(f"B SMP DUT hash: {b_smp_dut_hash}")

    with open(dut_folder / "b_smp_dut.bin", "rb") as f:
        b_smp_dut_bin: Final = f.read()

    print("Searching for A SMP DUT...", end="", flush=True)
    a_smp_dut = await BleakScanner.find_device_by_name("A SMP DUT")  # type: ignore
    if a_smp_dut is None:
        print("FAILED")
        raise SystemExit("A SMP DUT not found")
    a_smp_dut = cast(BLEDevice, a_smp_dut)

    print("OK")

    print("Connecting to A SMP DUT...", end="", flush=True)
    async with SMPClient(SMPBLETransport(), a_smp_dut.name or a_smp_dut.address) as client:
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
        async for offset in client.upload(b_smp_dut_bin):
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

        print()
        print("Marking B SMP DUT for test...")
        await ensure_request(ImageStatesWrite(hash=response.images[1].hash))

        print()
        print("Resetting for swap...")
        await ensure_request(ResetWrite())

    print()
    print("Searching for B SMP DUT...", end="", flush=True)
    b_smp_dut = await BleakScanner.find_device_by_name("B SMP DUT", timeout=30)  # type: ignore
    if b_smp_dut is None:
        print("FAIL")
        raise SystemExit("A SMP DUT not found")
    print("OK")
    b_smp_dut = cast(BLEDevice, b_smp_dut)

    print("Connecting to B SMP DUT...", end="", flush=True)
    async with SMPClient(SMPBLETransport(), b_smp_dut.name or b_smp_dut.address) as client:
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


if __name__ == "__main__":
    asyncio.run(main())
