import argparse
import asyncio
import logging
import time
from typing import Final

from serial.tools.list_ports import comports

from smpclient import SMPClient
from smpclient.generics import SMPRequest, TEr1, TEr2, TRep, error, success
from smpclient.mcuboot import IMAGE_TLV, ImageInfo
from smpclient.requests.image_management import ImageStatesRead
from smpclient.requests.os_management import EchoWrite, ResetWrite
from smpclient.transport.serial import SMPSerialTransport

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Do an SMP DFU test")
    parser.add_argument(
        "--bin",
        help="Path to DFU image binary",
        required=True,
        type=str,
    )
    parser.add_argument(
        "--port",
        help="Com port of the DUT",
        required=True,
        type=str,
    )
    args: Final = parser.parse_args()

    dfu_img_path: Final[str] = args.bin
    print(f"Using hex: {bin}")

    dfu_img_hash: Final = ImageInfo.load_file(str(dfu_img_path)).get_tlv(IMAGE_TLV.SHA256)
    print(f"DFU img hash: {dfu_img_hash}")

    with open(dfu_img_path, "rb") as f:
        dfu_bin: Final = f.read()

    port_a = next(p for p in comports() if args.port == p.device)
    print(f"DUT com port ({port_a.device}) found!")

    await asyncio.sleep(1)

    print("Connecting to SMP DUT...", end="", flush=True)
    async with SMPClient(
        SMPSerialTransport(max_smp_encoded_frame_size=512),
        port_a.device,
    ) as client:
        print("OK")

        async def ensure_request(request: SMPRequest[TRep, TEr1, TEr2]) -> TRep:
            print("Sending request...", end="", flush=True)
            response = await client.request(request)
            print("OK")

            # if success(response):
            print(f"Received response: {response}")
            return response
            # elif error(response):
            #     raise Exception(f"Received error: {response}")
            # else:
            #     raise Exception(f"Unknown response: {response}")

        # Test `echo`
        response = await ensure_request(EchoWrite(d="hello world!"))
        print(response)

        # Test `reset`
        # response = await ensure_request(ResetWrite())
        # print(response)

        # Test `image list`
        response = await ensure_request(ImageStatesRead())
        for image in response.images:
            print()
            print(f"Image at slot {image.slot}:")
            print(f"Version: {image.version}")
            print(f"Hash: {bytes(image.hash).hex()}")
            print(f"Bootable: {image.bootable}")
            print(f"Pending: {image.pending}")
            print(f"Active: {image.active}")
            print(f"Permanent: {image.permanent}")
            print()

        # response = await ensure_request(ResetWrite())
        # print(response)

        # Test `image upload`

        # Comment carried over from examples/usb/upgrade.py in the SMP Client repo:
        #  TODO: MCUBoot should allow 0, 1, or 2 here but only 2 works!
        #       It would be best to test with 1 to avoid the swap.  And test
        #       with CONFIG_SINGLE_APPLICATION_SLOT=y.
        #       Refer to dutfirmware/mcuboot_usb.conf
        # slot: Final = 2 if testing_mcuboot else 0

        # TODO: CONDOR-specific handling:
        # For the app image, slot should be 0.
        # For the net image, slot should be 3(?)
        # Additional context: https://gitlab.com/synchron/gen-2/scu/scu-ble/scu-ble-fw/-/merge_requests/178#note_1967164760
        slot: Final = 2

        print(f"Uploading {dfu_img_path} to slot {slot}")
        print()

        start_s = time.time()
        async for offset in client.upload(dfu_bin, slot=slot, first_timeout_s=2.500, use_sha=False):
            print(
                f"\rUploaded {offset:,} / {len(dfu_bin):,} Bytes | "
                f"{offset / (time.time() - start_s) / 1000:.2f} KB/s           ",
                end="",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
