#!/usr/bin/env python3
"""Wrap a mainline arm64 Image for the Unisoc LK on the MU300.

LK always copies the kernel to 0x80080000 (text_offset 0x80000 of pre-5.8 kernels) and jumps there. Kernels since 5.8
have text_offset 0 and must start on a 2 MiB boundary. This prepends a stub with an arm64 Image header whose first
instruction branches 0x180000 bytes forward, so the real Image runs from 0x80200000; x0 (the DTB) is untouched.

usage: wrap-image.py Image Image.lk
"""
import struct, sys

GAP = 0x180000                      # 0x80080000 + GAP = 0x80200000
src, dst = sys.argv[1], sys.argv[2]
img = open(src, 'rb').read()
if img[56:60] != b'ARMd':
    sys.exit('not an arm64 Image')
_, _, text_offset, image_size, flags = struct.unpack_from('<IIQQQ', img, 0)
branch = 0x14000000 | (GAP // 4)   # b #GAP
hdr = struct.pack('<IIQQQQQQ', branch, 0, 0x80000, GAP + image_size, flags, 0, 0, 0) + b'ARMd' + struct.pack('<I', 0)
stub = hdr + b'\0' * (GAP - len(hdr))
open(dst, 'wb').write(stub + img)
print(f'{dst}: stub {GAP:#x} + Image {len(img):#x} (image_size {image_size:#x}, text_offset was {text_offset:#x})')
