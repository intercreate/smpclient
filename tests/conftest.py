"""Fixtures shared by the unit tests."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def skip_negotiation() -> Generator[AsyncMock, Any, None]:
    """Answer every transport's MCUmgr parameters read with `None`, as a server without them."""
    with patch("smpclient._request.read_mcumgr_parameters", AsyncMock(return_value=None)) as read:
        yield read
