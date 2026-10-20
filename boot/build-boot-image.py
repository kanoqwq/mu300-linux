#!/usr/bin/env python3
"""Build a ZTE F50 / MU300 (Unisoc T760, UMS9620) Linux boot image for slot b.

The image reuses the stock boot image header and AVB footer layout (the device runs a
boot-verification bypass, the AVB descriptor is kept as-is), replaces the kernel and the
boot ramdisk, and adds a bootloader_control block that init writes back to misc so the
next reboot returns to slot a (Android).

Inputs are the user's own dumps; nothing device-specific is embedded in this script.

Example:
  build-boot-image.py \
    --stock-boot dumps/boot_a.img --misc-head dumps/misc-head.bin \
    --kernel out/Image --modules out/modules --busybox busybox-static-arm64 \
    --logdw tools/logdw/logdw --android-subset android-subset \
    --ueventd-perms android-vendor/ueventd-perms.sh --out boot-linux-slotb.img
"""
import argparse
import re
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
PAGE = 4096
BOOT_CMDLINE = b'loglevel=5'
MISC_BC_OFFSET = 0x800
# persistent init log lives at 48 MiB inside the Linux boot partition (8 MiB); the image must end before it
PERSIST_LOG_OFFSET = 48 << 20
# written by android-vendor/extract_subset.py on a host whose file system cannot hold every name from the
# device (Windows: the property area's u:object_r:<context>:s0); while it exists it holds the whole subset
WINDOWS_TAR = 'windows-source.tar.gz'


def cpio_record(name, data, mode, ino, rdev=(0, 0)):
    nb = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(nb), 0]
    h = b'070701' + b''.join(('%08x' % v).encode() for v in fields)
    x = h + nb
    x += b'\0' * ((-len(x)) % 4)
    x += data
    x += b'\0' * ((-len(data)) % 4)
    return x


def lz4_legacy(data):
    """LZ4 legacy frame, the format vendor_boot uses. Prefers the lz4 command, falls back to the lz4 Python module
    (pip install lz4) so the installer also works on hosts without the command line tool, e.g. Windows."""
    try:
        return subprocess.run(['lz4', '-l', '-12', '-c'], input=data, capture_output=True, check=True).stdout
    except FileNotFoundError:
        pass
    try:
        import lz4.block
    except ImportError:
        sys.exit("need the 'lz4' command or the lz4 Python module (pip install lz4)")
    # legacy frame: magic, then for every 8 MiB of input a little-endian block length followed by the LZ4 block
    out = bytearray(bytes.fromhex('02214c18'))
    for i in range(0, len(data), 8 << 20):
        block = lz4.block.compress(data[i:i + (8 << 20)], mode='high_compression', compression=12, store_size=False)
        out += struct.pack('<I', len(block)) + block
    return bytes(out)


def cpio_archive(dirs, files):
    dirs = set(dirs)
    for name in list(files) + list(dirs):
        dirs.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
    cpio = bytearray()
    ino = 1
    for d in sorted(dirs, key=lambda x: (x.count('/'), x)):
        cpio += cpio_record(d, b'', stat.S_IFDIR | 0o755, ino)
        ino += 1
    for name, (data, mode) in files.items():
        cpio += cpio_record(name, data, mode, ino)
        ino += 1
    # the kernel opens /dev/console before running /init
    cpio += cpio_record('dev/console', b'', stat.S_IFCHR | 0o600, ino, (5, 1))
    ino += 1
    cpio += cpio_record('TRAILER!!!', b'', 0, ino)
    return bytes(cpio)


def bootloader_control(misc_head):
    """Return Android A/B and opposite-slot Linux trial blocks derived from misc."""
    bc = misc_head[MISC_BC_OFFSET:MISC_BC_OFFSET + 32]
    if bc[4:8] != b'BCAB':
        sys.exit('misc head has no bootloader_control magic at 0x800')
    if zlib.crc32(bc[:28]) != struct.unpack('<I', bc[28:])[0]:
        sys.exit('misc bootloader_control CRC mismatch')

    def with_slots(suffix, a, b):
        x = bytearray(bc)
        x[0:4] = suffix
        x[12] = a
        x[14] = b
        x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
        return bytes(x)

    # slot_info byte: priority (4 bits) | tries_remaining (3 bits) | successful_boot (1 bit)
    android_a = with_slots(b'_a\0\0', 0x9f, 0x1e)       # a: prio 15, successful
    # Unisoc LK treats tries==1 && !successful as an already failed boot, so the one-shot
    # trial needs tries=2 (LK decrements to 1; a failed boot then rolls back to slot a).
    linux_b_trial = with_slots(b'_b\0\0', 0x9e, 0x2f)  # Android a, Linux b
    android_b = with_slots(b'_b\0\0', 0x1e, 0x9f)       # b: prio 15, successful
    linux_a_trial = with_slots(b'_a\0\0', 0x2f, 0x9e)  # Android b, Linux a
    return android_a, linux_b_trial, android_b, linux_a_trial


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stock-boot', type=Path, help='stock boot_a.img dump (64 MiB, header v4)')
    ap.add_argument('--misc-head', type=Path, help='first 4 KiB of the misc partition')
    ap.add_argument('--kernel', type=Path, help='arm64 Image built from kernel/')
    ap.add_argument('--generic-ramdisk', action='store_true',
                    help='write only the device-independent ramdisk segment (init, busybox, modules) to --out, for '
                         'mu300-update to put behind the ramdisk of the boot image already on the device')
    ap.add_argument('--modules', required=True, type=Path, help='flat directory with the built .ko files')
    ap.add_argument('--module-order', type=Path, default=HERE / 'module-order.txt')
    ap.add_argument('--device-modules', action='append', default=[], metavar='NAME=DIR',
                    help='modules built for another device of this family (u30air=out/u30air): they go to '
                         'linux-modules/NAME/ with boot/module-order-NAME.txt, and init loads them in place of the '
                         'ones of the same name when it runs on that device')
    ap.add_argument('--device', choices=['f50', 'v50', 'u30air'],
                    help='fallback board identity in /etc/mu300-device; the live device tree takes priority')
    ap.add_argument('--root-target', choices=['sd'],
                    help='TF-only device segment: never probe an internal eMMC root when TF fails')
    ap.add_argument('--trial-guard', type=int, metavar='SECONDS',
                    help='for experiments only: reboot SECONDS after switch_root unless /run/stay exists (with a '
                         'one-shot trial, that is back to Android when the kernel boots without USB)')
    ap.add_argument('--init', type=Path, default=HERE / 'init')
    ap.add_argument('--busybox', required=True, type=Path, help='static arm64 busybox')
    ap.add_argument('--logdw', required=True, type=Path, help='tools/logdw build (static arm64)')
    ap.add_argument('--ueventd-perms', required=True, type=Path)
    ap.add_argument('--android-subset', type=Path, help='directory produced by android-vendor/extract-subset.sh')
    ap.add_argument('--append-ramdisk', type=Path,
                    help='another LZ4 legacy ramdisk segment to put behind this one (the ramdisk-generic.lz4 of a '
                         'kernel bundle such as mu300-kernel-6.18.tar.gz: its init and modules replace these, which '
                         'is what mu300-update writes on the device when it installs that kernel)')
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    if not a.generic_ramdisk and not (a.stock_boot and a.misc_head and a.kernel):
        ap.error('--stock-boot, --misc-head and --kernel are required unless --generic-ramdisk is given')

    def text(path):  # scripts and lists for the device's shell, LF whatever the checkout did (issue #7)
        return path.read_bytes().replace(b'\r\n', b'\n')

    files = {
        'init': (text(a.init), stat.S_IFREG | 0o755),
        'bin/busybox': (a.busybox.read_bytes(), stat.S_IFREG | 0o755),
        'bin/sh': (b'busybox', stat.S_IFLNK | 0o777),
        'bin/logdw': (a.logdw.read_bytes(), stat.S_IFREG | 0o755),
        'etc/ueventd-perms.sh': (text(a.ueventd_perms), stat.S_IFREG | 0o755),
        'etc/module-order': (text(a.module_order), stat.S_IFREG | 0o644),
        'etc/mu300-internal-ext4.sh': (text(HERE / 'internal-ext4.sh'), stat.S_IFREG | 0o644),
    }
    dirs = {'bin', 'sbin', 'etc', 'proc', 'sys', 'dev', 'run', 'tmp', 'root', 'config', 'linux-modules'}
    for name in a.module_order.read_text().split():
        ko = a.modules / name
        if not ko.exists():
            sys.exit(f'missing module {ko}')
        files['linux-modules/' + name] = (ko.read_bytes(), stat.S_IFREG | 0o644)
    for spec in a.device_modules:
        dev, _, ddir = spec.partition('=')
        order = HERE / f'module-order-{dev}.txt'
        files[f'etc/module-order-{dev}'] = (text(order), stat.S_IFREG | 0o644)
        dirs.add('linux-modules/' + dev)
        # the kernel these were built for: a mainline kernel's generic segment replaces the modules and the order
        # of the base set, but not these, and init must not load them into a kernel they were not built for
        vermagic = re.search(rb'vermagic=(\S+)', next(Path(ddir).glob('*.ko')).read_bytes()).group(1)
        files[f'linux-modules/{dev}/kernel.release'] = (vermagic + b'\n', stat.S_IFREG | 0o644)
        for name in order.read_text().split():
            ko = Path(ddir) / name
            if ko.exists():
                files[f'linux-modules/{dev}/{name}'] = (ko.read_bytes(), stat.S_IFREG | 0o644)
            elif 'linux-modules/' + name not in files:
                if not (a.modules / name).exists():
                    sys.exit(f'missing module {name} for {dev} (neither {ko} nor in --modules)')
                files['linux-modules/' + name] = ((a.modules / name).read_bytes(), stat.S_IFREG | 0o644)
    if a.trial_guard and a.generic_ramdisk:
        ap.error('--trial-guard does not go into a generic ramdisk')
    if a.root_target:
        if a.generic_ramdisk:
            ap.error('--root-target does not go into a generic ramdisk')
        # Deliberately absent from generic updates: keep the install target
        # from the device segment when concatenating a new generic ramdisk.
        files['etc/mu300-root-target'] = (a.root_target.encode() + b'\n', stat.S_IFREG | 0o644)
    if a.device:
        # only in the device segment: a generic segment is the same for every device
        if a.generic_ramdisk:
            ap.error('--device does not go into a generic ramdisk')
        files['etc/mu300-device'] = (a.device.encode() + b'\n', stat.S_IFREG | 0o644)
    if a.generic_ramdisk:
        # The kernel unpacks concatenated ramdisk segments in turn and a later file replaces an earlier one of the
        # same name, so this segment behind the device's own ramdisk updates everything that is not the device's.
        # That includes a trial guard an experiment left in the device segment: mu300-update keeps that segment,
        # and a guard in it restarted an installed system every ten minutes whenever it booted as a trial (from
        # Android with mu300-linux). An empty file here disarms it; an experiment's guard goes behind this.
        files['etc/mu300-trial-guard'] = (b'', stat.S_IFREG | 0o644)
        ram = lz4_legacy(cpio_archive(dirs, files))
        a.out.write_bytes(ram)
        manifest = {
            'ramdisk': a.out.name,
            'sha256': hashlib.sha256(ram).hexdigest(),
            'size': len(ram),
            'modules': len(a.module_order.read_text().split()),
            'init_sha256': hashlib.sha256(files['init'][0]).hexdigest(),
        }
        a.out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest, indent=2))
        return

    base = a.stock_boot.read_bytes()
    if base[:8] != b'ANDROID!' or struct.unpack_from('<I', base, 40)[0] != 4:
        sys.exit('stock boot is not an Android boot image header v4')
    android_a_bc, linux_b_bc, android_b_bc, linux_a_bc = bootloader_control(a.misc_head.read_bytes())
    files['etc/misc-bc-slot-a.bin'] = (android_a_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b-trial.bin'] = (linux_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b.bin'] = (android_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-a-trial.bin'] = (linux_a_bc, stat.S_IFREG | 0o644)
    if a.android_subset:
        dirs.add('android')
        side = a.android_subset / WINDOWS_TAR
        if side.is_file():
            # a subset pulled on Windows: the tree there cannot hold the property area (its names contain
            # ':') and it cannot hold the file modes either, so the whole subset comes from this archive
            with tarfile.open(side, 'r:*') as stored:
                for m in stored.getmembers():
                    rel = 'android/' + m.name.lstrip('./')
                    if m.isdir():
                        dirs.add(rel)
                    if m.issym():
                        files[rel] = (m.linkname.encode(), stat.S_IFLNK | 0o777)
                    elif m.isreg():
                        files[rel] = (stored.extractfile(m).read(), stat.S_IFREG | (m.mode & 0o777))
                    # the kernel does not create parents for the entries it unpacks, so add them all
                    parts = rel.split('/')
                    for i in range(2, len(parts)):
                        dirs.add('/'.join(parts[:i]))
        else:
            for f in sorted(a.android_subset.rglob('*')):
                if f.name == WINDOWS_TAR and f.parent == a.android_subset:
                    continue
                # the names must use '/': str() of a Windows path gives '\', and the kernel would take
                # that as part of the file name and unpack one flat file instead of a directory tree
                rel = 'android/' + str(f.relative_to(a.android_subset)).replace(os.sep, '/')
                if f.is_symlink():
                    files[rel] = (os.readlink(f).encode(), stat.S_IFLNK | 0o777)
                elif f.is_dir():
                    dirs.add(rel)
                else:
                    files[rel] = (f.read_bytes(), stat.S_IFREG | (0o755 if os.access(f, os.X_OK) else 0o644))

    # must match vendor_boot's LZ4 legacy framing: a gzip segment makes this 5.4 kernel fall
    # back to the /dev/ram0 image path and panic "Unable to mount root fs on unknown-block(1,0)"
    ram = lz4_legacy(cpio_archive(dirs, files))
    assert ram[:4] == bytes.fromhex('02214c18')
    if a.append_ramdisk:
        extra = a.append_ramdisk.read_bytes()
        if extra[:4] != bytes.fromhex('02214c18'):
            sys.exit(f'{a.append_ramdisk} is not an LZ4 legacy ramdisk segment')
        ram += extra
        # The appended segment carries an init of its own, and a later segment's file replaces an earlier one of
        # the same name. When that bundle is older than this checkout - a release is usually older than the copy
        # of the project that installs from it - its init is older too, and one from before v2026.09.29 knows
        # only the default ROOT_OFFSET: a device whose Linux region is elsewhere then mounted an empty offset and
        # booted into standalone mode (issue #5). Our init is the one built for this device (the installer writes
        # its offset in) and searches for the region as well, so it goes behind as a third segment, unpacked last.
        ram += lz4_legacy(cpio_archive(set(), {'init': files['init']}))
    if a.trial_guard:
        # last of all, behind the generic segment and its empty guard
        ram += lz4_legacy(cpio_archive(set(), {'etc/mu300-trial-guard': (b'%d\n' % a.trial_guard,
                                                                         stat.S_IFREG | 0o644)}))

    kern = a.kernel.read_bytes()
    hdr = bytearray(base[:PAGE])
    struct.pack_into('<I', hdr, 8, len(kern))
    struct.pack_into('<I', hdr, 12, len(ram))
    hdr[44:44 + 1536] = BOOT_CMDLINE + b'\0' * (1536 - len(BOOT_CMDLINE))
    struct.pack_into('<I', hdr, 1580, 0)  # signature_size
    body = bytes(hdr) + kern + b'\0' * ((-len(kern)) % PAGE) + ram
    body += b'\0' * ((-len(body)) % PAGE)
    original_size = len(body)

    avb_off, avb_size = struct.unpack_from('>QQ', base, len(base) - 44)
    body += base[avb_off:avb_off + avb_size]
    if len(body) > PERSIST_LOG_OFFSET:
        sys.exit(f'image data ({len(body)} bytes) overlaps the persistent log area at 48 MiB')
    body += b'\0' * (len(base) - 64 - len(body))
    footer = bytearray(base[-64:])
    struct.pack_into('>QQQ', footer, 12, original_size, original_size, avb_size)
    image = body + bytes(footer)
    assert len(image) == len(base)

    a.out.write_bytes(image)
    a.out.with_suffix('.misc-slot-b-trial.bin').write_bytes(linux_b_bc)
    a.out.with_suffix('.misc-slot-a-trial.bin').write_bytes(linux_a_bc)
    manifest = {
        'image': a.out.name,
        'sha256': hashlib.sha256(image).hexdigest(),
        # the Linux boot partition's tail holds the persistent init log; compare only the first 48 MiB
        'sha256_head48m': hashlib.sha256(image[:PERSIST_LOG_OFFSET]).hexdigest(),
        'kernel_sha256': hashlib.sha256(kern).hexdigest(),
        'ramdisk_size': len(ram),
        'modules': len(a.module_order.read_text().split()),
        'misc_slot_b_trial_hex': linux_b_bc.hex(),
        'misc_slot_a_trial_hex': linux_a_bc.hex(),
        'misc_slot_a_hex': android_a_bc.hex(),
        'misc_slot_b_hex': android_b_bc.hex(),
    }
    a.out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
