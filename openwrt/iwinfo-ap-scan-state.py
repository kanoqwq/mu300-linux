#!/usr/bin/env python3
"""Source and artifact checks for the SC2355 AP discovery backend."""
import hashlib
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SOURCES = ('build-iwinfo-ap-scan.sh', 'build-iwinfo-ap-scan-inner.sh',
           'patch-iwinfo-ap-scan.py', 'iwinfo-ap-scan-state.py', 'hostapd.Dockerfile')


def build_id():
    return hashlib.sha256(b''.join((HERE/n).read_bytes() for n in SOURCES)).hexdigest()


def inspect(directory):
    directory = Path(directory)
    if (directory/'build-id').read_text().strip() != build_id():
        raise ValueError('AP scan library is stale; run openwrt/build-iwinfo-ap-scan.sh')
    rows = (directory/'SHA256SUMS').read_text().splitlines()
    if len(rows) != 1:
        raise ValueError('Expected exactly one AP scan library APK')
    checksum, name = rows[0].split()
    name = name.removeprefix('./')
    if not re.fullmatch(r'libiwinfo20230701-2026\.01\.14~f5dd57a8-r1001\.apk', name):
        raise ValueError('Unexpected AP scan library APK')
    if not re.fullmatch('[a-f0-9]{64}', checksum) or hashlib.sha256((directory/name).read_bytes()).hexdigest() != checksum:
        raise ValueError('AP scan library checksum mismatch')
    if {p.name for p in directory.glob('*.apk')} != {name}:
        raise ValueError('Unexpected extra AP scan APKs')
    return {'build_id': build_id(), 'apks': {name: checksum}}


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path)
    print(inspect(p.parse_args().directory)['build_id'])
