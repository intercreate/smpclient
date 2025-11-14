"""Tools for inspecting MCUBoot compatible firmware images.

Specification: https://docs.mcuboot.com/design.html
"""

from __future__ import annotations

import argparse
import pathlib
import struct
from enum import IntEnum, IntFlag, unique
from functools import cached_property
from io import BufferedReader, BytesIO
from typing import Annotated, Any, Dict, Final, List, Union

from intelhex import hex2bin  # type: ignore
from pydantic import Field, GetCoreSchemaHandler
from pydantic.dataclasses import dataclass
from pydantic_core import CoreSchema, core_schema

IMAGE_MAGIC: Final = 0x96F3B83D
IMAGE_HEADER_SIZE: Final = 32

_IMAGE_VERSION_FORMAT_STRING: Final = "BBHL"
IMAGE_VERSION_STRUCT: Final = struct.Struct(f"<{_IMAGE_VERSION_FORMAT_STRING}")
assert IMAGE_VERSION_STRUCT.size == 8

IMAGE_HEADER_STRUCT: Final = struct.Struct(f"<LLHHLL{_IMAGE_VERSION_FORMAT_STRING}4x")
assert IMAGE_HEADER_STRUCT.size == IMAGE_HEADER_SIZE

IMAGE_TLV_INFO_MAGIC: Final = 0x6907
IMAGE_TLV_PROT_INFO_MAGIC: Final = 0x6908

IMAGE_TLV_INFO_STRUCT: Final = struct.Struct("<HH")
assert IMAGE_TLV_INFO_STRUCT.size == 4
IMAGE_TLV_STRUCT: Final = struct.Struct("<BxH")
assert IMAGE_TLV_STRUCT.size == 4


class MCUBootImageError(Exception):
    ...


class TLVNotFound(MCUBootImageError):
    ...


@unique
class IMAGE_F(IntFlag):
    """Image header flags."""

    PIC = 0x01
    """Not supported."""
    ENCRYPTED_AES128 = 0x04
    """Encrypted using AES128."""
    ENCRYPTED_AES256 = 0x08
    """Encrypted using AES256."""
    NON_BOOTABLE = 0x10
    """Split image app."""
    RAM_LOAD = 0x20


@unique
class IMAGE_TLV(IntEnum):
    """Image trailer TLV types.

    Specification: https://docs.mcuboot.com/design.html#image-format
    """

    KEYHASH = 0x01
    """Hash of the public key"""
    PUBKEY = 0x02
    """Public key"""
    SHA256 = 0x10
    """SHA256 of image hdr and body"""
    SHA384 = 0x11
    """SHA384 of image hdr and body"""
    SHA512 = 0x12
    """SHA512 of image hdr and body"""
    RSA2048_PSS = 0x20
    """RSA2048 of hash output"""
    ECDSA224 = 0x21
    """ECDSA of hash output - Not supported anymore"""
    ECDSA_SIG = 0x22
    """ECDSA of hash output"""
    RSA3072_PSS = 0x23
    """RSA3072 of hash output"""
    ED25519 = 0x24
    """ED25519 of hash output"""
    SIG_PURE = 0x25
    """Signature prepared over full image rather than digest"""
    ENC_RSA2048 = 0x30
    """Key encrypted with RSA-OAEP-2048"""
    ENC_KW = 0x31
    """Key encrypted with AES-KW-128 or 256"""
    ENC_EC256 = 0x32
    """Key encrypted with ECIES-P256"""
    ENC_X25519 = 0x33
    """Key encrypted with ECIES-X25519"""
    ENC_X25519_SHA512 = 0x34
    """Key exchange using X25519 with SHA512 MAC"""
    DEPENDENCY = 0x40
    """Image depends on other image"""
    SEC_CNT = 0x50
    """Security counter"""
    BOOT_RECORD = 0x60
    """Measured boot record"""
    DECOMP_SIZE = 0x70
    """Decompressed image size excluding header/TLVs"""
    DECOMP_SHA = 0x71
    """Decompressed image hash matching format of compressed slot"""
    DECOMP_SIGNATURE = 0x72
    """Decompressed image signature matching compressed format"""
    COMP_DEC_SIZE = 0x73
    """Compressed decrypted image size"""
    UUID_VID = 0x80
    """Vendor unique identifier"""
    UUID_CID = 0x81
    """Device class unique identifier"""


class VendorTLV(int):
    """Vendor-defined TLV type in reserved ranges (0xXXA0-0xXXFE).

    Vendor reserved TLVs occupy ranges from 0xXXA0 to 0xXXFE, where XX
    represents any upper byte value. Examples include ranges 0x00A0-0x00FF,
    0x01A0-0x01FF, and 0x02A0-0x02FF, continuing through 0xFFA0-0xFFFE.
    """

    def __new__(cls, value: int) -> 'VendorTLV':
        """Create a new VendorTLV, validating the range."""
        lower_byte = value & 0xFF
        if not (0xA0 <= lower_byte <= 0xFE):
            raise ValueError(
                f"VendorTLV 0x{value:02x} must have lower byte in range 0xA0-0xFE, "
                f"got 0x{lower_byte:02x}"
            )
        return int.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(value: int) -> VendorTLV:
            return cls(value)

        return core_schema.no_info_after_validator_function(
            validate,
            core_schema.int_schema(),
        )


ImageTLVType = Annotated[Union[IMAGE_TLV, VendorTLV, int], Field(union_mode="left_to_right")]
"""TLV type that accepts standard IMAGE_TLV enums, vendor-defined TLVs, or any integer.

This uses Pydantic's "left to right" union mode to:
1. First try to match against IMAGE_TLV enum values
2. Then try to validate as a VendorTLV (0xXXA0-0xXXFE ranges)
3. Finally accept any integer as a fallback

This ensures backward compatibility and supports future TLV types without validation errors.
"""


@dataclass(frozen=True)
class ImageVersion:
    """An MCUBoot image_version struct."""

    major: int
    minor: int
    revision: int
    build_num: int

    @staticmethod
    def loads(data: bytes) -> 'ImageVersion':
        """Load an `ImageVersion` from `bytes`."""
        return ImageVersion(*IMAGE_VERSION_STRUCT.unpack(data))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.revision}-build{self.build_num}"


@dataclass(frozen=True)
class ImageHeader:
    """An MCUBoot signed FW update header."""

    magic: int
    load_addr: int
    hdr_size: int
    protect_tlv_size: int
    img_size: int
    flags: IMAGE_F
    ver: ImageVersion

    @staticmethod
    def loads(data: bytes) -> 'ImageHeader':
        """Load an `ImageHeader` from `bytes`."""
        (
            magic,
            load_addr,
            hdr_size,
            protect_tlv_size,
            img_size,
            flags,
            *ver,
        ) = IMAGE_HEADER_STRUCT.unpack(data)
        return ImageHeader(
            magic=magic,
            load_addr=load_addr,
            hdr_size=hdr_size,
            protect_tlv_size=protect_tlv_size,
            img_size=img_size,
            flags=flags,
            ver=ImageVersion(*ver),
        )

    def __post_init__(self) -> None:
        """Do initial validation of the header."""
        if self.magic != IMAGE_MAGIC:
            raise MCUBootImageError(f"Magic is {hex(self.magic)}, expected {hex(IMAGE_MAGIC)}")

    @staticmethod
    def load_from(file: BytesIO | BufferedReader) -> 'ImageHeader':
        """Load an `ImageHeader` from an open file."""
        return ImageHeader.loads(file.read(IMAGE_HEADER_STRUCT.size))

    @staticmethod
    def load_file(path: str) -> 'ImageHeader':
        """Load an `ImageHeader` the file at `path`."""
        with open(path, 'rb') as f:
            return ImageHeader.load_from(f)


@dataclass(frozen=True)
class ImageTLVInfo:
    """An image Type-Length-Value (TLV) region header."""

    magic: int
    tlv_tot: int
    """size of TLV area (including tlv_info header)"""

    def __post_init__(self) -> None:
        """Do initial validation of the header."""
        if self.magic != IMAGE_TLV_INFO_MAGIC:
            raise MCUBootImageError(
                f"TLV info magic is {hex(self.magic)}, expected {hex(IMAGE_TLV_INFO_MAGIC)}"
            )

    @staticmethod
    def loads(data: bytes) -> 'ImageTLVInfo':
        """Load an `ImageTLVInfo` from bytes."""
        return ImageTLVInfo(*IMAGE_TLV_INFO_STRUCT.unpack(data))

    @staticmethod
    def load_from(file: BytesIO | BufferedReader) -> 'ImageTLVInfo':
        """Load an `ImageTLVInfo` from a file."""
        return ImageTLVInfo.loads(file.read(IMAGE_TLV_INFO_STRUCT.size))


@dataclass(frozen=True)
class ImageTLV:
    """A TLV header - type and length."""

    type: ImageTLVType
    len: int
    """Data length (not including TLV header)."""

    @staticmethod
    def load_from(file: BytesIO | BufferedReader) -> 'ImageTLV':
        """Load an `ImageTLV` from a file."""
        return ImageTLV(*IMAGE_TLV_STRUCT.unpack_from(file.read(IMAGE_TLV_STRUCT.size)))


@dataclass(frozen=True)
class ImageTLVValue:
    header: ImageTLV
    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != self.header.len:
            raise MCUBootImageError(f"TLV requires length {self.header.len}, got {len(self.value)}")

    def __str__(self) -> str:
        type_name = (
            self.header.type.name
            if isinstance(self.header.type, IMAGE_TLV)
            else f"0x{self.header.type:02x}"
        )
        return f"{type_name}={self.value.hex()}"


@dataclass(frozen=True)
class ImageInfo:
    """A summary of an MCUBoot FW update image."""

    header: ImageHeader
    tlv_info: ImageTLVInfo
    tlvs: List[ImageTLVValue]
    file: str | None = None

    def get_tlv(self, tlv: ImageTLVType) -> ImageTLVValue:
        """Get a TLV from the image or raise `TLVNotFound`."""
        if tlv in self._map_tlv_type_to_value:
            return self._map_tlv_type_to_value[tlv]
        else:
            raise TLVNotFound(f"{tlv} not found in image.")

    @staticmethod
    def load_file(path: str) -> 'ImageInfo':
        """
        Load MCUBoot `ImageInfo` from the file at `path`.

        Files with the `.hex` extension are treated as Intel HEX format.
        All other file extensions are treated as binary.
        """
        file_path = pathlib.Path(path)

        if file_path.suffix != ".hex":
            with open(file_path, 'rb') as _f:
                f = BytesIO(_f.read())
        else:
            f = BytesIO()
            ret = hex2bin(str(file_path), f)
            if ret != 0:
                raise MCUBootImageError(f"hex2bin() ret: {ret}")

        f.seek(0)  # move to the start of the image
        image_header = ImageHeader.load_from(f)

        tlv_offset = image_header.hdr_size + image_header.img_size

        f.seek(tlv_offset)  # move to the start of the TLV area
        tlv_info = ImageTLVInfo.load_from(f)

        tlvs: List[ImageTLVValue] = []
        while f.tell() < tlv_offset + tlv_info.tlv_tot:
            tlv_header = ImageTLV.load_from(f)
            tlvs.append(ImageTLVValue(header=tlv_header, value=f.read(tlv_header.len)))

        return ImageInfo(file=path, header=image_header, tlv_info=tlv_info, tlvs=tlvs)

    @cached_property
    def _map_tlv_type_to_value(self) -> Dict[int, ImageTLVValue]:
        return {tlv.header.type: tlv for tlv in self.tlvs}

    def __str__(self) -> str:
        rep = (
            f"{self.__class__.__name__}{': ' + self.file if self.file is not None else ''}\n"
            f"{self.header}\n"
            f"{self.tlv_info}\n"
        )

        for tlv in self.tlvs:
            rep += f"  {str(tlv)}\n"

        return rep


def mcuimg() -> int:
    """A minimal CLI for getting info about an MCUBoot compatible FW image."""

    parser = argparse.ArgumentParser(
        prog="mcuimg",
        description=(
            "Inspect an MCUBoot compatible firmware update image."
            "\nCopyright (C) 2023-2024 Intercreate, Inc. | github.com/intercreate/smpclient"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file")

    try:
        image_info = ImageInfo.load_file(parser.parse_args().file)
    except FileNotFoundError as e:
        print(e)
        return -1

    print(str(image_info))

    return 0
