#!/usr/bin/env python3
"""Build one C file from tools/ as an aarch64-musl binary with the cached Zig 0.15.2.
usage: build_static.py <tools/file.c> <new-out-dir> [--ubus]   (the binary is named after the source: u30pro_foo.c -> u30pro-foo)

Without --ubus the binary is static.  With --ubus it is linked dynamically against the image's libubus, libubox and libuci (headers
and link libraries from tools/ubus_sysroot.py, build/ubus-sysroot, made when missing), like every OpenWrt program; the manifest
records the libraries it needs."""
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

from build_native_panel import cached_zig, verify_static_aarch64

REPO = Path(__file__).resolve().parents[1]
SYSROOT = REPO / 'build' / 'ubus-sysroot'
UBUS_LIBS = ('ubus', 'ubox', 'uci')


def verify_dynamic_aarch64(data):
    """an ELF64 AArch64 executable that uses the musl loader; returns its NEEDED libraries"""
    if len(data) < 64 or data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1 or struct.unpack_from('<H', data, 18)[0] != 183:
        raise SystemExit('output is not a little-endian ELF64 AArch64 binary')
    phoff, phentsize, phnum = struct.unpack_from('<Q', data, 32)[0], struct.unpack_from('<H', data, 54)[0], struct.unpack_from('<H', data, 56)[0]
    interp, dynamic, loads = None, None, []
    for i in range(phnum):
        off = phoff + i * phentsize
        ptype, = struct.unpack_from('<I', data, off)
        poff, vaddr, filesz = struct.unpack_from('<Q', data, off + 8)[0], struct.unpack_from('<Q', data, off + 16)[0], struct.unpack_from('<Q', data, off + 32)[0]
        if ptype == 3:
            interp = data[poff:poff + filesz].split(b'\0', 1)[0].decode()
        elif ptype == 2:
            dynamic = (poff, filesz)
        elif ptype == 1:
            loads.append((vaddr, poff, filesz))
    if interp != '/lib/ld-musl-aarch64.so.1' or not dynamic:
        raise SystemExit(f'output must use the musl loader /lib/ld-musl-aarch64.so.1 (has {interp})')
    needed, strtab = [], None
    for i in range(dynamic[1] // 16):
        tag, val = struct.unpack_from('<qQ', data, dynamic[0] + i * 16)
        if tag == 0:
            break
        if tag == 1:
            needed.append(val)
        elif tag == 5:
            strtab = next(poff + val - vaddr for vaddr, poff, filesz in loads if vaddr <= val < vaddr + filesz)
    names = [data[strtab + n:data.index(b'\0', strtab + n)].decode() for n in needed]
    return {'elf_class': 64, 'machine': 'AArch64', 'program_headers': phnum, 'has_interpreter': True, 'interpreter': interp, 'needed': names}


def main():
    args = [a for a in sys.argv[1:] if a != '--ubus']
    ubus = '--ubus' in sys.argv[1:]
    if len(args) != 2:
        raise SystemExit(__doc__)
    src = Path(args[0]).resolve()
    out = Path(args[1]).resolve()
    if out.exists():
        raise SystemExit(f'output already exists; choose a new directory: {out}')
    zig = cached_zig()
    if ubus and not (SYSROOT / 'lib' / 'libubus.so').is_file():
        subprocess.run([sys.executable, str(REPO / 'tools' / 'ubus_sysroot.py'), str(SYSROOT)], check=True)
    out.mkdir(parents=True)
    name = src.stem.replace('_', '-')
    binary = out / name
    link = ['-dynamic', '-I', str(SYSROOT / 'include'), '-L', str(SYSROOT / 'lib')] if ubus else ['-static']
    cmd = [str(zig), 'cc', '-target', 'aarch64-linux-musl', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O2',
           *link, '-s', str(src), '-o', str(binary), *(['-l' + lib for lib in UBUS_LIBS] if ubus else [])]
    result = subprocess.run(cmd, capture_output=True, timeout=180)
    (out / 'compile.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        sys.stderr.write((out / 'compile.log').read_text(errors='replace'))
        raise SystemExit('build failed')
    data = binary.read_bytes()
    elf = verify_dynamic_aarch64(data) if ubus else verify_static_aarch64(data)
    man = {'source': src.relative_to(REPO).as_posix() if src.is_relative_to(REPO) else str(src), 'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(), 'binary': name,
           'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data), 'libc': 'musl',
           'target': 'aarch64-linux-musl', 'elf': elf}
    if ubus:
        man['linkage'] = 'dynamic'
        man['needed'] = elf['needed']
    else:
        man['static_verified'] = True
    (out / 'manifest.json').write_text(json.dumps(man, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(binary), 'sha256': man['binary_sha256'], 'bytes': len(data)}))


if __name__ == '__main__':
    main()
