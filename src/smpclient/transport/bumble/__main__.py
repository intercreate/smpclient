"""Minimal CLI for the bumble SMP transport.

Run `smpbumble --help` or `python -m smpclient.transport.bumble --help`.
"""

import argparse
import asyncio
import logging
import sys
from typing import assert_never

from bumble.device import Device
from bumble.transport import open_transport

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport.bumble import (
    DEFAULT_HOST_ADDRESS,
    DEFAULT_HOST_NAME,
    SMP_SERVICE_UUID,
    SMPBumbleTransport,
    pair_device,
)
from smpclient.transport.bumble.pairing import (
    KeyboardOnly,
    PairingAlreadyBonded,
    PairingFailed,
    PairingSucceeded,
    PairingTimedOut,
)
from smpclient.transport.bumble.scan import scan


async def _prompt_pin() -> int | None:
    raw = (await asyncio.to_thread(input, "Enter the 6-digit PIN shown on the device: ")).strip()
    if not raw.isdigit():
        print(f"Invalid PIN {raw!r}; expected digits only", file=sys.stderr)
        return None
    return int(raw)


async def _scan(args: argparse.Namespace) -> int:
    print(f"Opening HCI transport {args.hci!r}...", file=sys.stderr)
    async with await open_transport(args.hci) as hci:
        print("HCI transport opened", file=sys.stderr)
        device = Device.with_hci(DEFAULT_HOST_NAME, DEFAULT_HOST_ADDRESS, hci.source, hci.sink)
        print("Powering on...", file=sys.stderr)
        try:
            await asyncio.wait_for(device.power_on(), timeout=5.0)
        except asyncio.TimeoutError:
            print("Warning: power_on timed out after 5s; continuing", file=sys.stderr)
        except Exception as e:
            print(f"Warning: power_on failed: {e}; continuing", file=sys.stderr)

        print(f"Scanning for {args.timeout}s...", file=sys.stderr)
        try:
            results = await scan(device, args.timeout, service_uuid=SMP_SERVICE_UUID)
        finally:
            try:
                print("Powering off bumble device...", file=sys.stderr)
                await asyncio.wait_for(device.power_off(), timeout=2.0)
            except Exception as e:
                print(f"Warning: power_off failed: {e}", file=sys.stderr)

    if not results:
        print("No devices found", file=sys.stderr)
        return 1
    for r in results:
        marker = " [SMP]" if r.has_smp_service else ""
        rssi = f" rssi={r.rssi}" if r.rssi is not None else ""
        print(f"  {r.address}  {r.name or '<unnamed>'}{rssi}{marker}")
    return 0


async def _pair(args: argparse.Namespace) -> int:
    result = await pair_device(
        args.address,
        KeyboardOnly(_prompt_pin),
        hci=args.hci,
        scan_timeout_s=args.timeout,
        pair_timeout_s=args.timeout,
        force=args.force,
    )
    match result:
        case PairingSucceeded(bonded):
            print(f"Pairing succeeded (bonded={bonded})")
            return 0
        case PairingAlreadyBonded():
            print("Already bonded; nothing to do")
            return 0
        case PairingTimedOut(elapsed_s):
            print(f"Pairing timed out after {elapsed_s:.1f}s", file=sys.stderr)
            return 1
        case PairingFailed(reason, detail):
            print(f"Pairing failed: {reason.value}: {detail}", file=sys.stderr)
            return 1
        case _:
            assert_never(result)


async def _echo(args: argparse.Namespace) -> int:
    async with SMPClient(
        SMPBumbleTransport(hci=args.hci), args.address, timeout_s=args.timeout
    ) as client:
        response = await client.request(EchoWrite(d=args.message))
        if success(response):
            print(response.r)
            return 0
        if error(response):
            print(f"SMP error: {response}", file=sys.stderr)
            return 1
        return 2


def smpbumble() -> int:
    parser = argparse.ArgumentParser(
        prog="smpbumble",
        description=(
            "CLI for the bumble-backed SMP transport.\n"
            "Copyright (C) 2026 Intercreate, Inc. | github.com/intercreate/smpclient"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable DEBUG-level logging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan for advertising devices (marks [SMP] when present)")
    p_scan.add_argument("--hci", default="usb:0")
    p_scan.add_argument("--timeout", type=float, default=5.0)

    p_pair = sub.add_parser("pair", help="Connect, pair (PIN entry), disconnect")
    p_pair.add_argument("address", help="BD_ADDR or advertised local name")
    p_pair.add_argument("--hci", default="usb:0")
    p_pair.add_argument("--timeout", type=float, default=30.0)
    p_pair.add_argument(
        "--force",
        action="store_true",
        help="Delete the local bond first; pair from scratch even if we already have one",
    )

    p_echo = sub.add_parser("echo", help="Send an SMP Echo and print the response")
    p_echo.add_argument("address", help="BD_ADDR or advertised local name")
    p_echo.add_argument("message")
    p_echo.add_argument("--hci", default="usb:0")
    p_echo.add_argument("--timeout", type=float, default=5.0)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    dispatch = {"scan": _scan, "pair": _pair, "echo": _echo}
    return asyncio.run(dispatch[args.cmd](args))


if __name__ == "__main__":
    sys.exit(smpbumble())
