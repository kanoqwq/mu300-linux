#!/usr/bin/env python3
"""Compile only U30 Pro hardware helpers/panel with the existing musl SDK."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

TOP = Path(__file__).resolve().parents[1]
NATIVE = TOP / 'devices/u30pro/native'
WORKSPACE = Path('/home/kano/openwrt-build')
OUT = WORKSPACE / 'artifacts/u30pro-runtime'
TOOLCHAIN = '/build/openwrt/staging_dir/toolchain-aarch64_generic_gcc-14.3.0_musl'
TARGET = '/build/openwrt/staging_dir/target-aarch64_generic_musl'
IMAGE = 'e5-modem-build:20261017'
VOLUME = 'e5-openwrt-src'
CP_FILES = ('main', 'boot', 'layout', 'util', 'secure', 'hw_linux', 'nvglue', 'nv', 'mon', 'logchan')
SOURCES = {
    'u30-modemd': ['modem-native-re/src/' + p + '.c' for p in CP_FILES],
    'u30pro-nvsyncd': ['tools/u30pro_nvsyncd.c'],
    'u30pro-refnotifyd': ['tools/u30pro_refnotifyd.c'],
    'u30pro-nodes': ['kernel/ums9632/native/stage0_linux.c'],
    'u30pro-watchdog-keeper': ['kernel/ums9632/native/u30pro_watchdog_keeper.c'],
    'u30pro-panel': ['tools/u30pro_ui.c'],
    'u30pro-nfc': ['tools/u30pro_nfc.c'],
    'sml-voltage-check': [],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_elf(path, dynamic=False):
    data = path.read_bytes()
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
        raise ValueError('Not a little-endian AArch64 executable: ' + str(path))
    headers = subprocess.check_output(['readelf', '-l', '-d', str(path)], text=True)
    if dynamic:
        if '/lib/ld-musl-aarch64.so.1' not in headers:
            raise ValueError('Panel must use the target musl loader')
    elif 'INTERP' in headers or '(NEEDED)' in headers:
        raise ValueError('Hardware helper must be static: ' + str(path))


def build():
    if os.environ.get('U30PRO_BUILD_LOCKED') != '1':
        raise ValueError('Use tools/build-release.py run')
    OUT.mkdir(parents=True, exist_ok=True)
    metadata = OUT / 'runtime.json'
    previous = json.loads(metadata.read_text()) if metadata.exists() else {}
    result = {'format': 1, 'files': {}}
    for name, sources in SOURCES.items():
        dynamic = name == 'u30pro-panel'
        dependencies = [NATIVE / p for p in sources]
        if name == 'sml-voltage-check': dependencies = [TOP/'tools/sml-voltage/sml-voltage-check.c']
        dependencies += sorted((NATIVE / ('tools/ui' if dynamic else 'modem-native-re/src')).glob('*.h'))
        inputs = {str(p.relative_to(TOP)): digest(p) for p in dependencies}
        identity = {'sources': inputs, 'toolchain': TOOLCHAIN, 'image': IMAGE,
                    'volume': VOLUME, 'dynamic': dynamic,
                    'builder': digest(Path(__file__))}
        old = previous.get('files', {}).get(name, {})
        target = OUT / name
        if old.get('inputs') == identity and target.is_file() and digest(target) == old.get('sha256'):
            verify_elf(target, dynamic)
            result['files'][name] = old
            continue
        pending = OUT / (name + '.building')
        if pending.exists():
            raise ValueError('Failed compiler output retained: ' + str(pending))
        flags = ['-std=gnu11', '-Os', '-Wall', '-Wextra', '-Wno-error=misleading-indentation', '-s']
        flags += ['-I' + TARGET + '/usr/include', '-L' + TARGET + '/usr/lib'] if dynamic else ['-static']
        if name == 'u30-modemd':
            flags += ['-pthread', '-Wno-unused-parameter']
        command = ['docker', 'run', '--rm', '-v', VOLUME + ':/build:ro',
                   '-v', str(NATIVE) + ':/source:ro', '-v', str(TOP/'tools/sml-voltage') + ':/voltage:ro', '-v', str(OUT) + ':/output',
                   '--entrypoint', '/bin/sh', IMAGE, '-ec',
                   'export STAGING_DIR=/build/openwrt/staging_dir; exec "$@"', 'compile',
                   TOOLCHAIN + '/bin/aarch64-openwrt-linux-musl-gcc', *flags,
                   *(['/voltage/sml-voltage-check.c'] if name == 'sml-voltage-check' else ['/source/' + p for p in sources]), '-o', '/output/' + pending.name,
                   *(['-lubus', '-lubox', '-luci', '-lm'] if dynamic else [])]
        subprocess.run(command, check=True)
        verify_elf(pending, dynamic)
        pending.replace(target)
        result['files'][name] = {'inputs': identity, 'sha256': digest(target), 'bytes': target.stat().st_size}
        metadata.write_text(json.dumps(result, indent=2) + '\n')
    metadata.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'runtime': str(OUT), 'binaries': len(result['files']), 'hardware_accessed': False}))


if __name__ == '__main__':
    build()
