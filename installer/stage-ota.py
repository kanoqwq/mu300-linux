#!/usr/bin/env python3
"""Stage the shared OTA plugin into a release root. No downloads or device I/O."""
import argparse
from pathlib import Path
import shutil

TOP = Path(__file__).resolve().parents[1]


def stage(root):
    root = Path(root)
    source = TOP / 'openwrt/luci-app-unisoc-ota'
    for folder, destination in (('root', ''), ('htdocs', 'www')):
        for path in (source / folder).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            name = (Path(destination) / path.relative_to(source / folder)).as_posix()
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            target.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
            target.chmod(0o755 if name.startswith(('etc/init.d/', 'usr/libexec/')) else 0o644)
    (root / 'etc/unisoc-ota').mkdir(mode=0o700, exist_ok=True)
    rc = root / 'etc/rc.d/S06unisoc-ota'
    rc.parent.mkdir(parents=True, exist_ok=True)
    if rc.is_symlink():
        rc.unlink()
    rc.symlink_to('../init.d/unisoc-ota')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    stage(p.parse_args().root)
