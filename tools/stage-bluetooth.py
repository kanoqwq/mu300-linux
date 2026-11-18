#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Shared full-image/diff delivery for the Bluetooth web gateway."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import shutil

TOP = Path(__file__).resolve().parents[1]
DIRS = ('usr/lib/unisoc-bluetooth', 'www/luci-static/resources/unisoc-bluetooth',
        'www/luci-static/resources/view/unisoc-bluetooth')
FILES = ('usr/sbin/unisoc-bluetooth', 'usr/libexec/rpcd/unisoc-bluetooth',
         'www/cgi-bin/unisoc-bluetooth-events', 'etc/init.d/unisoc-bluetooth',
         'usr/share/unisoc-bluetooth/templates.json',
         'etc/init.d/unisoc-bt-transport', 'usr/share/rpcd/acl.d/unisoc-bluetooth.json',
         'usr/share/luci/menu.d/luci-app-unisoc-bluetooth.json')

def stage(root, platform, overlay=None, decoder_artifacts=None, decoder_sources=None):
    root = Path(root)
    overlay = Path(overlay) if overlay else TOP/'openwrt/overlay'
    if platform not in ('mu300-pcie', 'u30pro-aic-uart'):
        raise ValueError('Explicit Bluetooth platform required')
    if decoder_artifacts is None:raise ValueError('Registered Bluetooth decoder artifacts are required')
    decoder_sources=Path(decoder_sources) if decoder_sources else TOP/'tools/ble-decoder'
    runpy.run_path(str(decoder_sources/'build.py'))['check'](decoder_artifacts)
    for name in ('usr/bin/bluetoothd', 'usr/bin/rfcomm', 'usr/bin/btattach', 'usr/bin/sdptool',
                 'etc/init.d/dbus', 'etc/init.d/bluetoothd', 'usr/bin/python3',
                 'usr/bin/ucode', 'usr/lib/ucode/socket.so', 'usr/lib/uhttpd_ubus.so'):
        if not (root/name).exists():
            raise ValueError('Missing Bluetooth dependency: '+name)
    manifest = json.loads((overlay/'usr/lib/unisoc-bluetooth/vendor/MANIFEST.json').read_text())
    for name, record in manifest.items():
        file = overlay/'usr/lib/unisoc-bluetooth/vendor'/name
        if hashlib.sha256(file.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Bluetooth dependency checksum mismatch: '+name)
    sources = list(FILES)
    for directory in DIRS:
        sources.extend(str(p.relative_to(overlay)).replace('\\','/') for p in (overlay/directory).rglob('*')
                       if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for name in sources:
        target, source = root/name, overlay/name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data if source.suffix == '.whl' else data.replace(b'\r\n', b'\n'))
        target.chmod(0o755 if name in FILES and name.startswith(('usr/sbin/', 'usr/libexec/', 'www/cgi-bin/', 'etc/init.d/')) else 0o644)
    binary=root/'usr/bin/unisoc-ble-decode'
    binary.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(Path(decoder_artifacts)/'unisoc-ble-decode',binary);binary.chmod(0o755)
    share=root/'usr/share/unisoc-bluetooth';share.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(Path(decoder_artifacts)/'SOURCE.tar.gz',share/'decoder-source.tar.gz')
    shutil.copyfile(decoder_sources/'vendor/theengs/COPYING',share/'Theengs-COPYING')
    # A broad overlay copy may have included host-test bytecode. It is not a target build input.
    for cache in (root/'usr/lib/unisoc-bluetooth').rglob('*.pyc'):
        if '__pycache__' in cache.parts:
            cache.unlink()
    config = root/'etc/unisoc-bluetooth'
    config.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config/'platform').write_text(platform+'\n')
    modules = root/'etc/modules.d/90-unisoc-bluetooth'
    modules.parent.mkdir(parents=True, exist_ok=True)
    modules.write_text('btusb\n')
    # BlueZ's packaged storage path is /etc/bluetooth on OpenWrt. Preserve it and our config.
    keep = root/'lib/upgrade/keep.d/unisoc-bluetooth'
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text('/etc/unisoc-bluetooth/\n/etc/bluetooth/\n')
    main = root/'etc/bluetooth/main.conf'
    content = main.read_text()
    if re.search(r'^\s*AutoEnable\s*=', content, re.M):
        content = re.sub(r'^\s*AutoEnable\s*=.*$', 'AutoEnable=false', content, flags=re.M)
    elif re.search(r'^\[Policy\]\s*$', content, re.M):
        content = re.sub(r'^\[Policy\]\s*$', '[Policy]\nAutoEnable=false', content, flags=re.M)
    else:
        content += '\n[Policy]\nAutoEnable=false\n'
    main.write_text(content)
    rc = root/'etc/rc.d'
    rc.mkdir(exist_ok=True)
    for service in ('dbus', 'bluetoothd', 'unisoc-bluetooth'):
        text = (root/'etc/init.d'/service).read_text()
        for tag, field in (('S','START'), ('K','STOP')):
            match = re.search(r'^'+field+r'=(\d+)\s*$', text, re.M)
            if match:
                link = rc/(tag+match[1].zfill(2)+service)
                if not link.is_symlink():
                    link.symlink_to('../init.d/'+service)
    print('Bluetooth gateway staged: '+platform)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--overlay', type=Path)
    parser.add_argument('--decoder-artifacts', type=Path, required=True)
    parser.add_argument('--decoder-sources',type=Path)
    args = parser.parse_args()
    stage(args.root, args.platform, args.overlay,args.decoder_artifacts,args.decoder_sources)
