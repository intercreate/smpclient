"""Echo "Hello, World!" from an SMP server."""

import asyncio
from typing import Final

from smp.os_management import EchoWriteRequest

from smpclient import SMPClient, error, success
from smpclient.transport.ble import SMPBLETransport


async def main() -> None:
    print("Scanning for SMP servers...", end="", flush=True)
    smp_servers: Final = await SMPBLETransport.scan()
    print("OK")
    print(f"Found {len(smp_servers)} SMP servers: {smp_servers}")

    print("Connecting to the first SMP server...", end="", flush=True)
    async with SMPBLETransport().connected(smp_servers[0].address) as transport:
        client = SMPClient(transport)
        print("OK")

        print("Sending request...", end="", flush=True)
        response: Final = await client.request(EchoWriteRequest(d="Hello, World!"))
        print("OK")

        if success(response):
            print(f"Received response: {response}")
        elif error(response):
            print(f"Received error: {response}")
        else:
            raise Exception(f"Unknown response: {response}")


if __name__ == "__main__":
    asyncio.run(main())
