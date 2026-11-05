#!/usr/bin/env python3
"""Install the common RS-only lifetime watcher in an offline OpenWrt root."""
import argparse
import ast
import os
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]


def stage(root, overlay=None):
    root = Path(root).resolve()
    overlay = Path(overlay) if overlay else TOP/'openwrt/overlay'
    if root == Path('/') or not root.is_dir():
        raise ValueError('Expected an existing offline root')
    paths = ('usr/sbin/unisoc-ra-renew', 'etc/init.d/unisoc-ra-renew')
    sources = {name: (overlay/name).read_bytes().replace(b'\r\n', b'\n') for name in paths}
    ast.parse(sources[paths[0]])
    links = ('S98unisoc-ra-renew', 'K10unisoc-ra-renew')
    for name in paths:
        if (root/name).is_symlink():
            raise ValueError('Unexpected RA watcher destination symlink: '+name)
    for name in links:
        path = root/'etc/rc.d'/name
        if path.is_symlink():
            if os.readlink(path) != '../init.d/unisoc-ra-renew':
                raise ValueError('Unexpected RA service link: '+name)
        elif path.exists():
            raise ValueError('RA service link occupied: '+name)
    for name, data in sources.items():
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755)
    for name in links:
        path = root/'etc/rc.d'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_symlink():
            path.symlink_to('../init.d/unisoc-ra-renew')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--overlay', type=Path)
    args = parser.parse_args()
    stage(args.root, args.overlay)
