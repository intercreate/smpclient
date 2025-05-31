"""Test the base64 helpers."""

from __future__ import annotations

import random
from base64 import b64encode

from smpclient.transport.serial import _base64_cost, _base64_max

if not hasattr(random, 'randbytes'):
    from os import urandom

    def randbytes(n: int) -> bytes:
        """Generate `n` random bytes."""
        return urandom(n)

    random.randbytes = randbytes


def test_base64_sizing() -> None:
    """Assert that `_base64_max` is always within 4 of encoded size."""

    random.seed(1)

    for size in range(1, 0xFFFF):
        assert 0 <= size - _base64_cost(_base64_max(size)) < 4
        data = random.randbytes(_base64_max(size))
        encoded = b64encode(data)
        assert 0 <= size - len(encoded) < 4
