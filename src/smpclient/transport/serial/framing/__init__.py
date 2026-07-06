"""Pluggable wire framing for the raw serial SMP transport.

`SMPSerialRawTransport` sends bare bytes when its `framing` is `None`; a `SerialFraming`
wraps each SMP message instead (e.g. `cobs.Cobs`).  A framing owns its connection's
reassembly buffer, so it is stateful: one instance per transport.
"""

from typing import Iterator, Protocol


class SerialFraming(Protocol):
    """Wire framing plus a stateful frame decoder for one serial connection."""

    def encode(self, data: bytes) -> Iterator[bytes]:  # pragma: no cover
        """Yield the wire bytes framing the SMP message `data`."""

    def feed(self, data: bytes) -> None:  # pragma: no cover
        """Buffer received bytes for decoding."""

    def take(self) -> bytes | None:  # pragma: no cover
        """Return the next decoded SMP message, or `None` if no complete frame is buffered.

        Unconsumed bytes persist for the next call (a read may span frame boundaries), and a
        framing that can detect corruption drops the damaged frame and resynchronises.
        """

    def reset(self) -> None:  # pragma: no cover
        """Discard buffered bytes so a new connection starts clean."""
