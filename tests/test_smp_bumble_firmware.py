"""Tests for `smpclient.transport.bumble.firmware`.

These only run when the optional `[hci_firmware]` extra is installed (provided
by the dev group via `smpclient[all]`).  When `zephyr_4_4_0_hci` isn't on the
import path the whole module is skipped.
"""

import importlib
import sys
from hashlib import sha256
from pathlib import Path
from typing import Final

import pytest

zephyr_4_4_0_hci = pytest.importorskip("zephyr_4_4_0_hci")

from smpclient.transport.bumble import firmware as fw_helper  # noqa: E402

EXPECTED_VARIANTS: Final = (
    "nrf52840dk_default",
    "nrf52840dk_acl_502",
    "nrf52840dk_legacy",
    "nrf5340dk_default",
    "nrf5340dk_acl_502",
)


def test_reexports_match_umbrella() -> None:
    assert fw_helper.firmware is zephyr_4_4_0_hci.firmware
    assert fw_helper.Firmware is zephyr_4_4_0_hci.Firmware


def test_namedtuple_fields() -> None:
    assert fw_helper.Firmware._fields == EXPECTED_VARIANTS


@pytest.mark.parametrize("variant", EXPECTED_VARIANTS)
def test_hex_path_resolves_to_real_file(variant: str) -> None:
    module = getattr(fw_helper.firmware, variant)
    assert module.BOARD in variant
    assert isinstance(module.HEX_PATH, Path)
    assert module.HEX_PATH.is_file()
    assert module.HEX_PATH.suffix == ".hex"


@pytest.mark.parametrize("variant", EXPECTED_VARIANTS)
def test_sha256_matches_declared(variant: str) -> None:
    module = getattr(fw_helper.firmware, variant)
    actual = sha256(module.HEX_PATH.read_bytes()).hexdigest()
    assert actual == module.HEX_SHA256
    assert module.read_firmware_bytes() == module.HEX_PATH.read_bytes()


def test_iteration_walks_every_variant() -> None:
    walked = tuple(fw.BOARD + "_" + fw.OPTIONS for fw in fw_helper.firmware)
    assert len(walked) == len(EXPECTED_VARIANTS)
    assert set(walked) == set(EXPECTED_VARIANTS)


def test_missing_umbrella_raises_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "zephyr_4_4_0_hci", None)
    monkeypatch.delitem(sys.modules, "smpclient.transport.bumble.firmware", raising=False)
    with pytest.raises(ModuleNotFoundError, match=r"smpclient\[hci_firmware\]"):
        importlib.import_module("smpclient.transport.bumble.firmware")
