"""Minimal CLI for the bumble SMP transport.

Run `smpbumble --help` or `python -m smpclient.transport.bumble --help`.
"""

import argparse
import asyncio
import logging
import sys
from typing import Final, NamedTuple

from typing_extensions import assert_never

from smpclient import SMPClient
from smpclient.generics import error, success
from smpclient.requests.os_management import EchoWrite
from smpclient.transport.bumble import SMPBumbleTransport
from smpclient.transport.bumble.pairing import (
    KeyboardOnly,
    PairingAlreadyBonded,
    PairingFailed,
    PairingSucceeded,
    PairingTimedOut,
    pair_device,
)


class _ScanArgs(NamedTuple):
    hci: str
    timeout: float


class _PairArgs(NamedTuple):
    hci: str
    address: str
    timeout: float
    force: bool


class _EchoArgs(NamedTuple):
    hci: str
    address: str
    message: str
    timeout: float


async def _prompt_pin() -> int | None:
    raw: Final = (
        await asyncio.to_thread(input, "Enter the 6-digit PIN shown on the device: ")
    ).strip()
    if not (raw.isdigit() and len(raw) == 6):
        print(f"Invalid PIN {raw!r}; expected exactly 6 digits", file=sys.stderr)
        return None
    return int(raw)


async def _scan(args: _ScanArgs) -> int:
    print(f"Scanning on {args.hci!r} for {args.timeout}s...", file=sys.stderr)
    results: Final = await SMPBumbleTransport.scan(hci=args.hci, timeout_s=args.timeout)
    if not results:
        print("No devices found", file=sys.stderr)
        return 1
    for r in results:
        marker = " [SMP]" if r.has_smp_service else ""
        rssi = f" rssi={r.rssi}" if r.rssi is not None else ""
        print(f"  {r.address}  {r.name or '<unnamed>'}{rssi}{marker}")
    return 0


async def _pair(args: _PairArgs) -> int:
    result: Final = await pair_device(
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


async def _echo(args: _EchoArgs) -> int:
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
        assert_never(response)


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

    args: Final = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    match args.cmd:
        case "scan":
            return asyncio.run(_scan(_ScanArgs(hci=args.hci, timeout=args.timeout)))
        case "pair":
            return asyncio.run(
                _pair(
                    _PairArgs(
                        hci=args.hci,
                        address=args.address,
                        timeout=args.timeout,
                        force=args.force,
                    )
                )
            )
        case "echo":
            return asyncio.run(
                _echo(
                    _EchoArgs(
                        hci=args.hci,
                        address=args.address,
                        message=args.message,
                        timeout=args.timeout,
                    )
                )
            )
        case _:
            # argparse(required=True) already rejects unknown commands.
            raise SystemExit(2)


if __name__ == "__main__":
    sys.exit(smpbumble())
