#!/usr/bin/env python3
"""Prepare a local USB3 diagnostic boot from the device's verified backup.

Preserve the installed init, storage/slot settings and every cpio entry's
metadata; replace only its existing kernel modules and kernel. Never flash.
"""
import argparse
import hashlib
from pathlib import Path
import runpy
import re
import struct
import subprocess

TOP = Path(__file__).resolve().parents[1]
PAGE = 4096
LIMIT = 48 << 20


def replace_modules(raw, modules):
    result = bytearray()
    position = 0
    replaced = set()
    target_release = re.search(rb'vermagic=([^ \0]+)', (modules/'wcn_bsp.ko').read_bytes())[1]
    while position < len(raw):
        if raw[position] == 0:
            end = position
            while end < len(raw) and raw[end] == 0:
                end += 1
            result.extend(raw[position:end])
            position = end
            continue
        header = bytearray(raw[position:position+110])
        if len(header) != 110 or header[:6] != b'070701':
            raise ValueError('unsupported installed cpio record')
        size = int(header[54:62], 16)
        name_length = int(header[94:102], 16)
        name_end = position + 110 + name_length
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        if name_length < 1 or data_end > len(raw):
            raise ValueError('truncated installed ramdisk')
        name = raw[position+110:name_end-1].decode()
        body = raw[data_start:data_end]
        mode = int(header[14:22], 16)
        if name.endswith('.ko') and mode & 0o170000 == 0o100000:
            release = re.search(rb'vermagic=([^ \0]+)', body)
            if not release:
                raise ValueError('module has no vermagic: ' + name)
            # Installed boot images can retain an earlier stock 5.4 segment;
            # only the running mainline kernel's module entries are replaced.
            if release[1] != target_release:
                result.extend(raw[position:(data_end+3)&~3])
                position = (data_end+3)&~3
                continue
            candidate = modules / Path(name).name
            if not candidate.is_file():
                raise ValueError('missing matching module: ' + name)
            body = candidate.read_bytes()
            header[54:62] = f'{len(body):08x}'.encode()
            replaced.add(name)
        result.extend(header)
        result.extend(raw[position+110:data_start])
        result.extend(body)
        result.extend(b'\0' * (-len(body) % 4))
        position = (data_end + 3) & ~3
    if not replaced:
        raise ValueError('installed ramdisk contained no kernel modules')
    return bytes(result), sorted(replaced)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--kernel-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    base = args.backup.read_bytes()
    if base[:8] != b'ANDROID!' or struct.unpack_from('<I', base, 40)[0] != 4 or base[-64:-60] != b'AVBf':
        raise ValueError('expected installed v4/AVB boot backup')
    kernel_size, ram_size = struct.unpack_from('<II', base, 8)
    ram_offset = PAGE + ((kernel_size + PAGE-1) // PAGE) * PAGE
    ram = base[ram_offset:ram_offset+ram_size]
    unpacked = subprocess.run(['lz4', '-dc'], input=ram, capture_output=True, check=True).stdout
    updated, replaced = replace_modules(unpacked, args.kernel_dir/'modules')
    boot = runpy.run_path(str(TOP/'boot/build-boot-image.py'))
    if updated != unpacked:
        ram = boot['lz4_legacy'](updated)
    raw_kernel = (args.kernel_dir/'Image').read_bytes()
    if raw_kernel[56:60] != b'ARMd':
        raise ValueError('expected mainline arm64 Image')
    gap = 0x180000
    image_size, flags = struct.unpack_from('<QQ', raw_kernel, 16)
    stub = struct.pack('<IIQQQQQQ', 0x14000000 | gap//4, 0, 0x80000, gap+image_size,
                       flags, 0, 0, 0) + b'ARMd' + bytes(4)
    kernel = stub + bytes(gap-len(stub)) + raw_kernel
    header = bytearray(base[:PAGE])
    struct.pack_into('<II', header, 8, len(kernel), len(ram))
    struct.pack_into('<I', header, 1580, 0)
    body = bytes(header) + kernel + bytes(-len(kernel) % PAGE) + ram
    body += bytes(-len(body) % PAGE)
    vb_offset, vb_size = struct.unpack_from('>QQ', base, len(base)-44)
    if vb_offset+vb_size > len(base)-64 or len(body)+vb_size > LIMIT:
        raise ValueError('boot payload exceeds its reserved region')
    footer = bytearray(base[-64:])
    struct.pack_into('>QQQ', footer, 12, len(body), len(body), vb_size)
    result = bytearray(base)
    result[:LIMIT] = body + base[vb_offset:vb_offset+vb_size] + bytes(LIMIT-len(body)-vb_size)
    result[-64:] = footer
    args.out.write_bytes(result)
    print('Backup SHA256:', hashlib.sha256(base).hexdigest())
    print('Candidate SHA256:', hashlib.sha256(result).hexdigest())
    print('Replaced existing ramdisk modules:', len(replaced))
    print('Preserved init, cpio metadata, cmdline and persistent-log region; no device writes')


if __name__ == '__main__':
    main()
