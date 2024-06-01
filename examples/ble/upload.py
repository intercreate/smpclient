"""Upload some FW."""

import argparse
import asyncio
import logging
import time
from typing import Final

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.image_management import ImageStatesRead
from smpclient.transport.ble import SMPBLETransport

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Upload some FW.")
    parser.add_argument("path", help="Path to the FW.")
    with open(parser.parse_args().path, "rb") as f:
        fw_file: Final = f.read()

    print("Scanning for SMP servers...", end="", flush=True)
    smp_servers: Final = await SMPBLETransport.scan()
    print("OK")
    print(f"Found {len(smp_servers)} SMP servers: {smp_servers}")

    print("Connecting to the first SMP server...", end="", flush=True)
    async with SMPClient(
        SMPBLETransport(), smp_servers[0].name or smp_servers[0].address
    ) as client:
        print("OK")

        print("Sending request...", end="", flush=True)
        response = await client.request(ImageStatesRead())
        print("OK")

        if success(response):
            print(f"Received response: {response}")
        elif error(response):
            print(f"Received error: {response}")
        else:
            raise Exception(f"Unknown response: {response}")

        print()
        start_s = time.time()
        async for offset in client.upload(fw_file, 2):
            print(
                f"\rUploaded {offset:,} / {len(fw_file):,} Bytes | "
                f"{offset / (time.time() - start_s) / 1000:.2f} KB/s           ",
                end="",
                flush=True,
            )

        print()
        print("Sending request...", end="", flush=True)
        response = await client.request(ImageStatesRead())
        print("OK")

        if success(response):
            print(f"Received response: {response}")
        elif error(response):
            print(f"Received error: {response}")
        else:
            raise Exception(f"Unknown response: {response}")


if __name__ == "__main__":
    asyncio.run(main())
