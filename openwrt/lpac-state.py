#!/usr/bin/env python3
"""Check the exact source and file identity of the shared patched lpac APK."""
import hashlib
import json
import os
from pathlib import Path
import sys

TOP = Path(__file__).resolve().parents[1]
FILES = ('openwrt/build-lpac.sh', 'openwrt/build-lpac-inner.sh', 'openwrt/patch-lpac.py',
         'openwrt/lpac-stdio.patch', 'openwrt/lpac-state.py', 'openwrt/hostapd.Dockerfile')


def fingerprint():
    digest=hashlib.sha256()
    for name in FILES:
        digest.update(name.encode()+b'\0'+(TOP/name).read_bytes().replace(b'\r\n',b'\n')+b'\0')
    return digest.hexdigest()


def inspect(directory):
    directory=Path(directory)
    if (directory/'build-id').read_text().strip()!=fingerprint():
        raise ValueError('lpac source changed; rebuild with openwrt/build-lpac.sh')
    sums=(directory/'SHA256SUMS').read_text().splitlines()
    if len(sums)!=1:
        raise ValueError('Expected one patched lpac APK')
    digest,name=sums[0].split()
    name=name.removeprefix('./')
    if name!='lpac-2.3.0-r1001.apk':
        raise ValueError('Unexpected patched lpac artifact')
    if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=digest:
        raise ValueError('lpac artifact checksum mismatch')
    return {'file':name,'sha256':digest,'source_fingerprint':fingerprint()}


if __name__=='__main__':
    if sys.argv[1:] == ['--path']:
        data = json.loads((TOP/'tools/build-layout.json').read_text())
        workspace = os.environ.get('UNISOC_WORKSPACE', str(Path.home()/'openwrt-build'))
        print(data['lpac_apks'].format(workspace=workspace, repo=str(TOP)))
    else:
        print(json.dumps(inspect(sys.argv[1])))
