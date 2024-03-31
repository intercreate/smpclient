"""Get the "MCUmgr" (AKA SMP) parameters."""

import asyncio
from typing import Final

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.os_management import MCUMgrParametersRead
from smpclient.transport.ble import SMPBLETransport


async def main() -> None:
    print("Scanning for SMP servers...", end="", flush=True)
    smp_servers: Final = await SMPBLETransport.scan()
    print("OK")
    print(f"Found {len(smp_servers)} SMP servers: {smp_servers}")

    print("Connecting to the first SMP server...", end="", flush=True)
    async with SMPClient(SMPBLETransport(), smp_servers[0].address) as client:
        print("OK")
        print(f"Client MTU is {client._transport.mtu}B")
        print(f"Client max unencoded size is {client._transport.max_unencoded_size}B")

        print("Sending request...", end="", flush=True)
        response: Final = await client.request(MCUMgrParametersRead())
        print("OK")

        if success(response):
            print(f"Received response: {response}")
        elif error(response):
            print(f"Received error: {response}")
        else:
            raise Exception(f"Unknown response: {response}")


if __name__ == "__main__":
    asyncio.run(main())
