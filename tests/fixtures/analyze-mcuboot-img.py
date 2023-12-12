# pragma: no cover
# noqa
# type: ignore

# NOTE: copied from https://gist.github.com/mbolivar/285309cca792f746d6c698f56941041a

# Copyright (c) 2018 Foundries.io
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import struct
from collections import namedtuple

# Field names for mcuboot header, with struct image_version inlined,
# as well as struct module format string and reprs format strings for
# each.
IMG_HDR_FIELDS = [
    'magic',
    'load_addr',
    'hdr_size',
    'img_size',
    'flags',
    'ver_major',
    'ver_minor',
    'ver_revision',
    'ver_build_num',
]
IMG_HDR_FMT = '<IIHxxIIbbhIxxxx'
IMG_HDR_MAGIC = 0x96F3B83D

IMAGE_F_RAM_LOAD = 0x00000020

TLV_INFO_FIELDS = ['magic', 'tlv_size']
TLV_INFO_FMT = '<HH'
TLV_INFO_SIZE = 4
TLV_INFO_MAGIC = 0x6907

TLV_HDR_FIELDS = ['type', 'len']
TLV_HDR_FMT = '<bxH'
TLV_HDR_SIZE = 4
TLV_HDR_TYPES = {
    0x01: 'IMAGE_TLV_KEYHASH',
    0x10: 'IMAGE_TLV_SHA256',
    0x20: 'IMAGE_TLV_RSA2048_PSS',
    0x21: 'IMAGE_TLV_ECDSA224',
    0x22: 'IMAGE_TLV_ECDSA256',
}


class ImageHeader(namedtuple('ImageHeader', IMG_HDR_FIELDS)):
    def __repr__(self):
        return (
            'ImageHeader(magic={}/0x{:08X}, load_addr={}/0x{:08X}, '
            'hdr_size=0x{:04X}, img_size={}/0x{:08X}, flags=0x{:08X}, '
            'version="{}.{}.{}-build{}")'
        ).format(
            'OK' if self._magic_ok() else 'BAD',
            self.magic,
            'VALID' if self._load_addr_valid() else 'IGNORED',
            self.load_addr,
            self.hdr_size,
            self.img_size,
            self.img_size,
            self.flags,
            self.ver_major,
            self.ver_minor,
            self.ver_revision,
            self.ver_build_num,
        )

    def _magic_ok(self):
        return self.magic == IMG_HDR_MAGIC

    def _load_addr_valid(self):
        return bool(self.flags & IMAGE_F_RAM_LOAD)


class TLVInfo(namedtuple('TLVInfo', TLV_INFO_FIELDS)):
    def __repr__(self):
        return 'TLVInfo(magic={}/0x{:04X}, tlv_size={})'.format(
            'OK' if self._magic_ok() else 'BAD', self.magic, self.tlv_size
        )

    def _magic_ok(self):
        return self.magic == TLV_INFO_MAGIC


class TLVHeader(namedtuple('TLVHeader', TLV_HDR_FIELDS)):
    def __repr__(self):
        return 'TLVHeader(type={}/0x{:02X}, len={})'.format(
            TLV_HDR_TYPES[self.type], self.type, self.len
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    args = parser.parse_args()

    with open(args.image, 'rb') as f:
        contents = f.read()

    img_header = ImageHeader(*struct.unpack_from(IMG_HDR_FMT, contents))
    print(img_header)

    print('Initial image bytes:')
    start = img_header.hdr_size
    end = start + min(20, img_header.img_size)
    print('\t' + ' '.join('{:02x}'.format(b) for b in contents[start:end]))

    tlv_info_offset = img_header.hdr_size + img_header.img_size
    tlv_info = TLVInfo(*struct.unpack_from(TLV_INFO_FMT, contents, offset=tlv_info_offset))
    print(tlv_info)
    tlv_end = tlv_info_offset + tlv_info.tlv_size
    tlv_off = tlv_info_offset + TLV_INFO_SIZE
    tlv_num = 0
    while tlv_off < tlv_end:
        tlv_hdr = TLVHeader(*struct.unpack_from(TLV_HDR_FMT, contents, offset=tlv_off))
        print('TLV {}:'.format(tlv_num), tlv_hdr)
        if tlv_hdr.len <= 32:
            start = tlv_off + TLV_HDR_SIZE
            end = start + tlv_hdr.len
            print('\t' + ' '.join('{:02x}'.format(b) for b in contents[start:end]))
        tlv_off += TLV_HDR_SIZE + tlv_hdr.len
        tlv_num += 1


if __name__ == '__main__':
    main()
