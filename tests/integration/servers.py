"""Launch vendored Zephyr SMP servers (described by `manifest.json`) as subprocesses.

The fixture registry is built from the `manifest.json` shipped with the vendored
release (`tests/fixtures/smp-server/`, pinned in that directory's `VERSION`): one
`ServerFixture` per manifest entry whose artifact is actually vendored and whose
target is runnable here (native_sim via `run`, or an emulator via `qemu_cmd`).
Adding a fixture is therefore just `gh release download <tag> --pattern ...` — the
manifest already describes how to launch and reach it.

`serve()` launches a fixture, pipes its output into the logging system, waits until
it is reachable, and yields an `Endpoint` describing how a client connects:

- native_sim runs directly (32-bit ELF, `run`); serial UARTs appear as host PTYs,
  UDP binds a host socket.
- Emulated targets (`qemu_cmd`) route serial to a TCP socket chardev rather than a
  PTY: the emulated UART holds a frame's final byte until further host I/O, which
  stalls fragmented SMP over a PTY, whereas the socket delivers frames promptly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import shlex
import shutil
import socket
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, NamedTuple

import serial as pyserial
from typing_extensions import override

from smpclient.transport import SMPTransportDisconnected
from smpclient.transport.serial import (
    FragmentationStrategy,
    SMPSerialRawTransport,
    SMPSerialTransport,
)

logger = logging.getLogger(__name__)

_FIXTURES_DIR: Final = Path(__file__).resolve().parent.parent / "fixtures" / "smp-server"
_SHA256SUMS: Final = _FIXTURES_DIR / "SHA256SUMS"
_MANIFEST: Final = _FIXTURES_DIR / "manifest.json"

_ANSI: Final = re.compile(rb"\x1b\[[0-9;]*m")
"""Strip the ANSI SGR colour codes that Zephyr's log backend emits."""

_PTY_LINE: Final = re.compile(rb"connected to pseudotty: (\S+)")
"""native_sim prints this once a UART is bound to a host pseudo-terminal."""

_UDP_READY: Final = re.compile(rb"smp_udp: Started")
"""The UDP fixtures log this once the server socket is bound (IPv4 or IPv6)."""

Transport = Literal["serial", "serial_raw", "shell", "udp"]


class PtyEndpoint(NamedTuple):
    """A native_sim UART exposed as a host pseudo-terminal."""

    pty: str


class SocketSerialEndpoint(NamedTuple):
    """An emulator's serial chardev exposed as a TCP socket (a pyserial `socket://` URL)."""

    url: str


class UdpEndpoint(NamedTuple):
    """A host UDP socket bound by the server."""

    host: str
    port: int


Endpoint = PtyEndpoint | SocketSerialEndpoint | UdpEndpoint


class ServerFixture(NamedTuple):
    """One prebuilt SMP server, from a `manifest.json` entry."""

    artifact: str
    target: str
    config: str
    transport: Transport
    buf_size: int | None
    buf_count: int | None
    recovery_buf_size: int | None
    recovery_buf_count: int | None
    groups: tuple[str, ...]
    line_length_max: int | None
    ip_family: str | None
    udp_port: int | None
    mcuboot: bool
    serial_recovery: bool
    run: str | None
    qemu_cmd: str | None

    @property
    def id(self) -> str:
        return f"{self.target}.{self.config}"

    @property
    def path(self) -> Path:
        return _FIXTURES_DIR / self.artifact

    @property
    def emulated(self) -> bool:
        return self.qemu_cmd is not None

    @property
    def params_supported(self) -> bool:
        """`False` for builds with the MCUmgr params command disabled (`noparams`)."""
        return "noparams" not in self.config

    @property
    def bursty_fragment_drop(self) -> bool:
        """`True` when an instant write burst overruns the server's UART RX pool.

        native_sim PTY serial has no baud pacing, so a >2-fragment message written
        all at once is dropped unless the build enlarged its UART RX pool (`bigrx`).
        Emulated (socket) and UDP fixtures are unaffected.
        """
        return (
            self.run is not None
            and self.transport in ("serial", "shell")
            and "bigrx" not in self.config
        )

    @property
    def max_reliable_line_packets(self) -> int | None:
        """Largest multi-fragment message, in line packets, this fixture handles reliably.

        `None` means unconstrained. native_sim's PTY UART has no baud pacing and drops
        bursts beyond two line packets; the emulated nRF51 (`qemu_cortex_m0`, 16 KB RAM)
        hangs on transactions beyond ~three line packets. Cortex-M3 (mps2) and UDP are
        unconstrained. Heavy tests steer full-buffer transactions onto the roomier
        targets rather than these.
        """
        if self.bursty_fragment_drop:
            return 2
        if self.target == "qemu_cortex_m0":
            return 3
        return None

    def has_group(self, group: str) -> bool:
        return group in self.groups

    def unavailable_reason(self) -> str | None:
        """Return why this fixture cannot run on this host, or `None` if it can."""
        if platform.system() != "Linux":
            return "SMP server fixtures run on Linux only"
        if not self.path.is_file():
            return f"vendored artifact {self.artifact} not found"
        mismatch = _verify_sha256(self.path)
        if mismatch is not None:
            return mismatch
        if self.run is not None and not _has_ia32_loader():
            return "32-bit ELF loader missing (install libc6:i386)"
        if self.qemu_cmd is not None:
            emulator = shlex.split(self.qemu_cmd)[0]
            if shutil.which(emulator) is None:
                return f"{emulator} not installed"
        return None

    def argv(self) -> tuple[list[str], int | None]:
        """The launch command (cwd = fixtures dir) and the socket port, if emulated."""
        if self.run is not None:
            return shlex.split(self.run), None
        assert self.qemu_cmd is not None
        port = _free_port()
        return shlex.split(self.qemu_cmd.replace("<PORT>", str(port))), port

    def ready_pattern(self) -> re.Pattern[bytes] | None:
        """The stdout line that signals readiness, or `None` (emulated → probe the socket)."""
        if self.emulated:
            return None
        return _UDP_READY if self.transport == "udp" else _PTY_LINE

    def endpoint(self, match: re.Match[bytes] | None, port: int | None) -> Endpoint:
        if self.transport == "udp":
            host = "::1" if self.ip_family == "ipv6" else "127.0.0.1"
            return UdpEndpoint(host, self.udp_port or 1337)
        if self.emulated:
            assert port is not None
            return SocketSerialEndpoint(f"socket://127.0.0.1:{port}")
        assert match is not None
        return PtyEndpoint(match.group(1).decode())


def _load_fixtures() -> tuple[ServerFixture, ...]:
    """Build the registry from `manifest.json`, keeping runnable + vendored fixtures."""
    if not _MANIFEST.is_file():
        return ()
    entries = json.loads(_MANIFEST.read_text())
    fixtures = [
        ServerFixture(
            artifact=e["artifact"],
            target=e["target"],
            config=e["config"],
            transport=e["transport"],
            buf_size=e["buf_size"],
            buf_count=e["buf_count"],
            # Present only on serial-recovery entries re-vendored from a release that
            # carries the field (intercreate/smp-server-fixtures#10); .get() tolerates
            # the older baseline entries that predate it.
            recovery_buf_size=e.get("recovery_buf_size"),
            recovery_buf_count=e.get("recovery_buf_count"),
            groups=tuple(e["groups"]),
            line_length_max=e["line_length_max"],
            ip_family=e["ip_family"],
            udp_port=e["udp_port"],
            mcuboot=e["mcuboot"],
            serial_recovery=e["serial_recovery"],
            run=e["run"],
            qemu_cmd=e["qemu_cmd"],
        )
        for e in entries
        # runnable here (has a launch command) and present in the vendored subset
        if (e["run"] is not None or e["qemu_cmd"] is not None)
        and (_FIXTURES_DIR / e["artifact"]).is_file()
    ]
    return tuple(sorted(fixtures, key=lambda f: f.id))


FIXTURES: Final = _load_fixtures()


async def _connect_socket_chardev(
    transport: SMPSerialTransport | SMPSerialRawTransport, url: str, timeout_s: float
) -> None:
    """Back `transport` with an emulator's `socket://` serial chardev, retrying until it accepts.

    Replaces the `Final` pyserial `_conn` with a socket-backed `Serial`, sidestepping the
    PTY held-byte quirk of an emulated UART.  Shared by the encoded and raw socket
    transports, which differ only in their on-wire framing.

    Args:
        transport: the socket-backed serial transport whose `_conn` to (re)bind.
        url: the emulator's `socket://host:port` chardev URL.
        timeout_s: how long to keep retrying before the socket must have accepted.

    Raises:
        TimeoutError: if the emulator's serial socket never accepts within `timeout_s`.
    """
    transport._reset_state()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        try:
            conn = pyserial.serial_for_url(url, timeout=0, write_timeout=0)
        except (OSError, pyserial.SerialException) as e:
            if loop.time() >= deadline:
                raise TimeoutError(f"emulator serial socket {url} never accepted: {e}")
            await asyncio.sleep(transport._CONNECTION_RETRY_INTERVAL_S)
            continue
        # `_conn` is `Final` on the base class; replace it for the socket backend.
        object.__setattr__(transport, "_conn", conn)
        # A socket chardev has no host-side TX buffer; pyserial omits `out_waiting` for it.
        # Supply 0 so the inherited `send`'s `_drain_tx` poll is a no-op (nothing to drain).
        object.__setattr__(conn, "out_waiting", 0)
        logger.debug(f"Connected to {url}")
        return


class QemuSocketSerialTransport(SMPSerialTransport):
    """`SMPSerialTransport` whose byte pipe is a TCP socket (an emulator's serial chardev).

    Only `connect` differs -- it binds a `socket://` chardev instead of a local serial
    port, sidestepping the PTY held-byte quirk of an emulated UART.  Framing,
    fragmentation, `send`, and `receive` are inherited unchanged, so the suite exercises
    the real transport rather than a copy of it.
    """

    def __init__(  # noqa: DOC301
        self,
        url: str,
        fragmentation_strategy: FragmentationStrategy | None = None,
    ) -> None:
        if fragmentation_strategy is None:
            super().__init__()
        else:
            super().__init__(fragmentation_strategy=fragmentation_strategy)
        self._url: Final = url

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        await _connect_socket_chardev(self, self._url, timeout_s)


class QemuSocketSerialRawTransport(SMPSerialRawTransport):
    """`SMPSerialRawTransport` whose byte pipe is a TCP socket (an emulator's serial chardev).

    The raw counterpart of `QemuSocketSerialTransport`: only `connect` differs; the raw
    `[header][payload]` framing, `send`, and `receive` are inherited from
    `SMPSerialRawTransport` unchanged.
    """

    def __init__(self, url: str, mtu: int = 384) -> None:  # noqa: DOC301
        super().__init__(mtu=mtu)
        self._url: Final = url

    @override
    async def connect(self, address: str, timeout_s: float) -> None:
        await _connect_socket_chardev(self, self._url, timeout_s)


def _verify_sha256(artifact: Path) -> str | None:
    """Return a mismatch message if `artifact` fails its `SHA256SUMS` entry, else `None`."""
    if not _SHA256SUMS.is_file():
        return f"{_SHA256SUMS} not found"
    expected = {
        name: digest
        for line in _SHA256SUMS.read_text().splitlines()
        if line.strip()
        for digest, name in (line.split(maxsplit=1),)
    }
    want = expected.get(artifact.name)
    if want is None:
        return f"{artifact.name} not listed in {_SHA256SUMS.name}"
    got = sha256(artifact.read_bytes()).hexdigest()
    return None if got == want.strip() else f"{artifact.name} sha256 mismatch"


def _has_ia32_loader() -> bool:
    """`True` when this host can run the 32-bit native_sim ELF (needs the i386 loader)."""
    return Path("/lib/ld-linux.so.2").exists() or Path("/lib32/ld-linux.so.2").exists()


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ensure_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


@asynccontextmanager
async def serve(
    fixture: ServerFixture, *, ready_timeout_s: float = 20.0
) -> AsyncIterator[Endpoint]:
    """Launch `fixture`, pipe its output into logging, and yield its `Endpoint`.

    The server is terminated on exit.

    Args:
        fixture: the `ServerFixture` to launch.
        ready_timeout_s: how long to wait for the readiness signal on stdout.

    Yields:
        the `Endpoint` a client uses to connect.

    Raises:
        TimeoutError: if the server does not signal readiness in time.
    """
    argv, port = fixture.argv()
    if fixture.run is not None:
        _ensure_executable(fixture.path)

    log = logging.getLogger(f"smp.server.{fixture.id}")
    log.info("launching: %s", " ".join(argv))

    # Run in a throwaway dir with the artifacts symlinked in, so the server's
    # relative launch paths resolve while any runtime files it writes (e.g. the
    # mps2 flash simulator's flash.bin) never touch the vendored fixtures dir.
    with tempfile.TemporaryDirectory(prefix="smp-server-") as workdir:
        work = Path(workdir)
        for artifact in _FIXTURES_DIR.iterdir():
            if artifact.suffix in (".exe", ".hex", ".bin", ".elf"):
                (work / artifact.name).symlink_to(artifact)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=work,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        ready_pattern = fixture.ready_pattern()
        endpoint_future: asyncio.Future[Endpoint] | None = (
            None if ready_pattern is None else asyncio.get_running_loop().create_future()
        )
        pump = asyncio.ensure_future(
            _pump(proc, log, fixture, ready_pattern, port, endpoint_future)
        )

        try:
            if endpoint_future is None:
                endpoint = fixture.endpoint(None, port)
            else:
                endpoint = await asyncio.wait_for(asyncio.shield(endpoint_future), ready_timeout_s)
            yield endpoint
        finally:
            await _terminate(proc, pump)


async def _pump(
    proc: asyncio.subprocess.Process,
    log: logging.Logger,
    fixture: ServerFixture,
    ready_pattern: re.Pattern[bytes] | None,
    port: int | None,
    endpoint_future: asyncio.Future[Endpoint] | None,
) -> None:
    """Forward the server's output into `log` and resolve `endpoint_future` on readiness."""
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        text = _ANSI.sub(b"", raw).rstrip().decode(errors="replace")
        if text:
            log.info(text)
        if ready_pattern is not None and endpoint_future is not None and not endpoint_future.done():
            if match := ready_pattern.search(raw):
                endpoint_future.set_result(fixture.endpoint(match, port))
    if endpoint_future is not None and not endpoint_future.done():
        endpoint_future.set_exception(
            SMPTransportDisconnected(f"{fixture.id} exited before signalling readiness")
        )


async def _terminate(proc: asyncio.subprocess.Process, pump: asyncio.Task[None]) -> None:
    """Stop the server process and its output pump, tolerating partial failure."""
    try:
        if proc.returncode is None:
            proc.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
    pump.cancel()
    try:
        await pump
    except (asyncio.CancelledError, Exception):
        pass
