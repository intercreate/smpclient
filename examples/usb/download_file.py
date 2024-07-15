"""Downloading a file."""

import argparse
import asyncio
import time

from smpclient import SMPClient
from smpclient.transport.serial import SMPSerialTransport


async def main() -> None:
    parser = argparse.ArgumentParser(description="Downloading an file from a smp server")
    parser.add_argument("port", help="The serial port to connect to")
    parser.add_argument("file_location", help="The location of the test file on the smp server")
    args = parser.parse_args()
    port = args.port
    file_location = args.file_location

    async with SMPClient(SMPSerialTransport(), port) as client:
        start_s = time.time()
        file_data = await client.download_file(file_location)
        end_s = time.time()
        duration = end_s - start_s
        speed = round(len(file_data) / ((duration)) / 1000, 2)

        print(f"Speed {speed} KB/s")

        print()
        print("Finished downloading file")


if __name__ == "__main__":
    asyncio.run(main())
