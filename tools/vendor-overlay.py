#!/usr/bin/env python3
"""Pack the proprietary files pulled from the user's own device into an overlay tarball for a prebuilt root filesystem.

The published images contain no vendor files; install.sh builds this overlay on the host and android-install.sh
unpacks it over the system. The layout matches rootfs/assemble.sh and openwrt/build-rootfs.sh:
  firmware/            -> usr/lib/firmware (Ubuntu) or lib/firmware (OpenWrt)
  android-subset/      -> opt/mu300/android (dev/__properties__ becomes dev-properties)
  android-gpu-subset/  -> opt/mu300/android (files already taken from android-subset win)

A subset pulled on Windows holds a file called windows-source.tar.gz (android-vendor/extract_subset.py): the
property area is a set of files named u:object_r:<context>:s0, which that file system cannot store, so the whole
device archive is kept there instead. When it is present the subset is taken from it, not from the tree, so the
installed system gets the same names and modes a Linux host would have extracted.

A tree is never trusted for its modes (host_free_mode): what a file system reports for a file it cannot keep a mode
for differs from host to host - 0700 for everything on FAT/exFAT under macOS - and the vendor daemons do not run as
root.
"""
import argparse
import copy
import io
import os
import tarfile
from pathlib import Path

# written by android-vendor/extract_subset.py on a host whose file system cannot hold every name from the device
WINDOWS_TAR = 'windows-source.tar.gz'


def add_bytes(tar, arc, data, seen, mode=0o644):
    if arc in seen:
        return
    seen.add(arc)
    info = tarfile.TarInfo(arc)
    info.size, info.mode, info.uid, info.gid, info.uname, info.gname = len(data), mode, 0, 0, 'root', 'root'
    tar.addfile(info, io.BytesIO(data))


def add_dir(tar, arc, seen, keep):
    """directory entries for arc, but none for its first `keep` components: those exist in the system already, and
    an entry for one of them could replace it - Ubuntu's lib is a link to usr/lib"""
    parts = arc.split('/')
    for i in range(keep + 1, len(parts) + 1):
        d = '/'.join(parts[:i])
        if d in seen:
            continue
        seen.add(d)
        info = tarfile.TarInfo(d)
        info.type, info.mode, info.uid, info.gid, info.uname, info.gname = tarfile.DIRTYPE, 0o755, 0, 0, 'root', 'root'
        tar.addfile(info)


def bundle_files(bundle: Path):
    """the files of a kernel bundle, name -> bytes: from the mu300-kernel-6.18.tar.gz itself, or from a tree it was
    unpacked into. Take the tarball: a mainline bundle holds modules whose names differ only in case (xt_HL.ko and
    xt_hl.ko, xt_TCPMSS.ko and xt_tcpmss.ko, ...), and a tree on a host whose file system ignores case (macOS, Windows)
    keeps one file of each pair - under one name with the other's contents (FINDINGS 212)"""
    if bundle.is_file():
        files = {}
        with tarfile.open(bundle) as t:
            for m in t:
                name = m.name[2:] if m.name.startswith('./') else m.name
                if m.isfile() and (name.startswith('modules/') or name in BUNDLE_FILES):
                    files[name] = t.extractfile(m).read()
        return files
    files = {f: (bundle / f).read_bytes() for f in BUNDLE_FILES if (bundle / f).is_file()}
    files.update({f'modules/{ko.name}': ko.read_bytes() for ko in (bundle / 'modules').glob('*.ko')})
    return files


BUNDLE_FILES = ('kernel.release', 'modules.builtin', 'modules.builtin.modinfo')


def add_kernel_bundle(tar, bundle: Path, os_name, seen):
    """the modules of another kernel (mu300-kernel-6.18.tar.gz, or a tree it was unpacked into: bundle_files), laid
    out as mu300-update installs them: Ubuntu under lib/modules/<release>/extra (depmod on the device indexes them),
    OpenWrt flat (it loads them by path)"""
    files = bundle_files(bundle)
    krel = files['kernel.release'].decode().strip()
    kos = sorted(n for n in files if n.startswith('modules/') and n.endswith('.ko') and n.count('/') == 1)
    # the real path: in Ubuntu lib is a link to usr/lib, in OpenWrt a directory
    parent = 'usr/lib/modules' if os_name == 'ubuntu' else 'lib/modules'
    base = f'{parent}/{krel}'
    sub = f'{base}/extra' if os_name == 'ubuntu' else base
    add_dir(tar, sub, seen, keep=len(parent.split('/')))
    for ko in kos:
        add_bytes(tar, f'{sub}/{ko[len("modules/"):]}', files[ko], seen)
    if os_name == 'ubuntu':
        for f in ('modules.builtin', 'modules.builtin.modinfo'):
            if f in files:
                add_bytes(tar, f'{base}/{f}', files[f], seen)
        # the index (modules.dep): wifi-start runs depmod early in every boot
        add_bytes(tar, f'{base}/modules.order', b'', seen)
    return krel, len(kos)


def host_free_mode(arc, is_dir):
    """the mode of an entry, from where it goes and never from what the host reports. A tree extracted on a file
    system that keeps no modes says nothing about them: Windows reports 0666/0777, and macOS shows every file and
    directory of a FAT or exFAT volume as 0700 - stored like that, the installed vendor daemons (uid 1000, or
    mu300vnd) could not read their own properties and configuration and the modem never started (issue #127).
    Directories 0755, the programs (whatever lies in a bin directory) 0755, the property area read-only as
    Android has it, every other file 0644 - what android-vendor-start's modes block keeps them at on the device."""
    if is_dir:
        return 0o755
    parts = arc.split('/')
    if 'dev-properties' in parts[:-1]:
        return 0o444
    return 0o755 if parts[-2:-1] == ['bin'] else 0o644


def add_tree(tar, src: Path, dest: str, seen: set, rename=None):
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        rel_root = Path(root).relative_to(src)
        entries = [(d, True) for d in dirs] + [(f, False) for f in sorted(files)]
        # symlinks to directories show up in dirs but must be stored as links
        for name, _ in entries:
            if name == WINDOWS_TAR and rel_root == Path('.'):
                continue
            path = Path(root) / name
            rel = (rel_root / name).as_posix()
            if rename:
                rel = rename(rel)
                if rel is None:
                    continue
            arc = f'{dest}/{rel}'
            if arc in seen:
                continue
            seen.add(arc)
            info = tar.gettarinfo(str(path), arcname=arc)
            info.mode = 0o777 if info.issym() else host_free_mode(arc, info.isdir())
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            if info.isfile():
                with open(path, 'rb') as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]


def add_members(tar, src: Path, dest: str, seen: set, rename=None):
    """the same, for a subset whose tree is not complete on this host: everything comes out of
    src/windows-source.tar.gz, with the names, modes and links the file system could not keep"""
    side = src / WINDOWS_TAR
    if not side.is_file():
        return 0
    n = 0
    with tarfile.open(side, 'r:*') as stored:
        for m in stored.getmembers():
            rel = m.name.lstrip('./')
            if rename:
                rel = rename(rel)
                if rel is None:
                    continue
            arc = f'{dest}/{rel}'
            if arc in seen:
                continue
            seen.add(arc)
            info = copy.copy(m)
            info.name = arc
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            tar.addfile(info, stored.extractfile(m) if m.isreg() else None)
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--os', required=True, choices=['ubuntu', 'openwrt', 'openwrt-luci'])
    ap.add_argument('--firmware', type=Path)
    ap.add_argument('--android-subset', type=Path, required=True)
    ap.add_argument('--gpu-subset', type=Path)
    ap.add_argument('--kernel-bundle', type=Path,
                    help='mu300-kernel-6.18.tar.gz (or a tree it was unpacked into): its modules go into the system too')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()

    def android(rel):
        # the property area snapshot moves out of dev/ (a devtmpfs is mounted there on the device)
        if rel == 'dev/__properties__' or rel.startswith('dev/__properties__/'):
            return 'dev-properties' + rel[len('dev/__properties__'):]
        if rel == 'dev' or rel.startswith('dev/'):
            return None
        return rel

    def gpu(rel):
        return None if rel == 'dev' or rel.startswith('dev/') else rel

    seen = set()
    with tarfile.open(a.out, 'w:gz', format=tarfile.GNU_FORMAT) as tar:
        if a.firmware and a.firmware.is_dir():
            add_tree(tar, a.firmware, 'usr/lib/firmware' if a.os == 'ubuntu' else 'lib/firmware', seen)
        # a subset built on Windows keeps the device data in windows-source.tar.gz: its tree is missing
        # every name that file system cannot store (the property area) and every file mode, so take the
        # whole subset from the archive instead of walking it
        if (a.android_subset / WINDOWS_TAR).is_file():
            add_members(tar, a.android_subset, 'opt/mu300/android', seen, android)
        else:
            add_tree(tar, a.android_subset, 'opt/mu300/android', seen, android)
        if a.gpu_subset and a.gpu_subset.is_dir():
            add_tree(tar, a.gpu_subset, 'opt/mu300/android', seen, gpu)
        if a.kernel_bundle:
            krel, n = add_kernel_bundle(tar, a.kernel_bundle, a.os, seen)
            print(f'{a.out}: {n} modules for {krel}')
    print(f'{a.out}: {len(seen)} entries')


if __name__ == '__main__':
    main()
