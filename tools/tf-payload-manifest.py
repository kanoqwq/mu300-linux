#!/usr/bin/env python3
"""Validate a TF payload at packaging time; emit size/inode and integrity data."""
import hashlib
import posixpath
import sys
import tarfile
from pathlib import Path


def manifest(stage):
    stage = Path(stage)
    members = {}
    kib = 0
    with tarfile.open(stage / 'mu300-openwrt.tar.gz', 'r:gz') as archive:
        for m in archive:
            name = posixpath.normpath(m.name)
            if name.startswith('/') or name == '..' or name.startswith('../'):
                raise ValueError(f'unsafe archive path: {m.name}')
            # Do not extract through a parent symlink, whatever archive order.
            members[name] = m
            kib += ((m.size + 4095) // 4096) * 4 if m.isfile() else 4
        for name in members:
            parent = posixpath.dirname(name)
            while parent and parent != '.':
                if parent in members and members[parent].issym():
                    raise ValueError(f'archive entry traverses symlink: {name}')
                parent = posixpath.dirname(parent)
        # network is generated on first boot; its defaults, not an already
        # materialized /etc/config/network, must exist in a fresh rootfs.
        for name in ('bin/busybox', 'sbin/procd', 'etc/uci-defaults/90-mu300', 'etc/init.d/mu300-vendor'):
            if name not in members or not members[name].isfile() or not members[name].size:
                raise ValueError(f'missing boot-critical file: {name}')
        if 'sbin/init' not in members:
            raise ValueError('missing sbin/init')
        # Readback checks cover the boot-critical regular files, unaffected by
        # device-specific hotspot/password/fstab edits during installation.
        with (stage / 'rootfs-critical.sha256').open('w', newline='\n') as checks:
            for name in ('bin/busybox', 'sbin/procd', 'etc/init.d/mu300-vendor'):
                digest = hashlib.sha256(archive.extractfile(members[name]).read()).hexdigest()
                checks.write(f'{digest}  {name}\n')
    # Space for vendor runtime, metadata and configuration; inode count is
    # explicit at format time, so a small card need not be limited to 1 inode/MiB.
    (stage / 'rootfs.requirements').write_text(
        f'{kib + max(kib // 4, 262144)} {len(members) + 16384}\n', encoding='ascii', newline='\n')
    names = ['mu300-openwrt.tar.gz', 'boot-linux.img', 'rootfs.requirements', 'rootfs-critical.sha256',
             'android-bootchain.sh', 'bootchain.sh']
    if (stage/'u30pro-map.tsv').is_file():names.append('u30pro-map.tsv')
    with (stage / 'payload.sha256').open('w', newline='\n') as checks:
        for name in names:
            with (stage / name).open('rb') as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            checks.write(f'{digest.hexdigest()}  {name}\n')


if __name__ == '__main__':
    manifest(sys.argv[1])
