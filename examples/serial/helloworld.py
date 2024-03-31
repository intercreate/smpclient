"""Echo "Hello, World!" from an SMP server."""

import argparse
import asyncio
from typing import Final

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport.serial import SMPSerialTransport


async def main() -> None:
    parser = argparse.ArgumentParser(description="Echo 'Hello, World!' from an SMP server")
    parser.add_argument("port", help="The serial port to connect to")
    port = parser.parse_args().port

    async with SMPClient(SMPSerialTransport(), port) as client:
        print("OK")

        print("Sending request...", end="", flush=True)
        response: Final = await client.request(EchoWrite(d="Hello, World!"))
        print("OK")

        if success(response):
            print(f"Received response: {response}")
        elif error(response):
            print(f"Received error: {response}")
        else:
            raise Exception(f"Unknown response: {response}")


if __name__ == "__main__":
    asyncio.run(main())
