"""Not a test: the storage of a fake F50 as a directory, for tests/fakeadb.py's "emmc" mode - the make-room path's
writes (tools/resume.sh) and the checks of the installation that resumes after the reset, on what was written.

    dev/block/mmcblk0          the whole eMMC: a sparse file of the layout's size, with its partition table
                               (tests/fakegpt.py) in its first 34 and last 33 sectors
    dev/block/mmcblk0pN        userdata's node (a small file: its first bytes are all that is read)
    dev/block/by-name/NAME     misc (its A/B block valid, slot a), boot_a/b, the boot chain of both slots;
                               userdata a link to its node, as readlink -f resolves it on the device
    sys/block/mmcblk0/...      start, size and PARTNAME of every partition, the disk's size
    sys/class/block/...        userdata's start and size, the eMMC's CID
    proc/mounts, props         what /data is mounted from; the properties getprop answers
    bin/                       getprop, dd (never truncates a file it writes into, takes no conv=fsync), sha256sum
    tmp/                       /data/local/tmp

reset(d) does what the device does on the next start after the make-room path: the kernel reads the partition table
again (sysfs follows the table in the eMMC), and Android's recovery, asked by the bootloader message in misc,
formats userdata and clears the message. Nothing here runs on, or reaches, a real device.
"""
import os
import shutil
import stat
import struct
import sys
import zlib
from pathlib import Path

import fakegpt

SECTOR = 512
CID = '15010053454d36344720c1a6b7d2e900'
CHAIN = ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta')


def _misc(slot='a'):
    bc = bytearray(32)
    bc[0:4] = ('_' + slot).encode() + b'\0\0'
    bc[4:8] = b'BCAB'
    bc[8] = 1
    bc[12], bc[14] = (0x9f, 0x1e) if slot == 'a' else (0x1e, 0x9f)
    bc[28:32] = struct.pack('<I', zlib.crc32(bytes(bc[:28])))
    return bytes(2048) + bytes(bc) + bytes((1 << 20) - 2048 - 32)


def _exe(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make(d, layout='vendor64', crypto=('encrypted', 'file'), source='/dev/block/dm-6', cid=CID, slot='a'):
    """The directory D standing for a device with this partition table; returns D."""
    d = Path(d)
    disk, usable, parts = fakegpt.layout(layout)
    for sub in ('dev/block/by-name', 'sys/block/mmcblk0', 'sys/class/block/mmcblk0/device', 'proc', 'bin', 'tmp'):
        (d / sub).mkdir(parents=True, exist_ok=True)
    head, tail = fakegpt.make(disk, usable, parts)
    with open(d / 'dev/block/mmcblk0', 'wb') as f:
        f.truncate(disk * SECTOR)
        f.write(head)
        f.seek((disk - 33) * SECTOR)
        f.write(tail)
    (d / 'sys/block/mmcblk0/size').write_text('%d\n' % disk)
    (d / 'sys/class/block/mmcblk0/device/cid').write_text(cid + '\n')
    sysfs(d, parts)
    for i, (name, _first, _last) in enumerate(parts, 1):
        if name == 'userdata':
            node = d / 'dev/block' / ('mmcblk0p%d' % i)
            node.write_bytes(bytes(1024) + bytes.fromhex('1020f5f2') + bytes(4096 - 1028))
            os.symlink('../mmcblk0p%d' % i, d / 'dev/block/by-name/userdata')
    (d / 'dev/block/by-name/misc').write_bytes(_misc(slot))
    for s in ('a', 'b'):
        (d / 'dev/block/by-name' / ('boot_' + s)).write_bytes(('boot_%s ' % s).encode() * 100000)
        for p in CHAIN:
            (d / 'dev/block/by-name' / ('%s_%s' % (p, s))).write_bytes(('%s_%s ' % (p, s)).encode() * 3000)
    (d / 'proc/mounts').write_text('%s /data f2fs rw,lazytime 0 0\n/dev/block/dm-3 /metadata ext4 ro 0 0\n' % source)
    (d / 'props').write_text('ro.crypto.state=%s\nro.crypto.type=%s\n' % crypto)
    _exe(d / 'bin/getprop', '#!/bin/sh\nsed -n "s/^$1=//p" "$FAKE_EMMC/props"\n')
    real_dd = shutil.which('dd') or '/bin/dd'
    _exe(d / 'bin/dd', '#!%s\n' % sys.executable + r'''import os, sys
# the device's dd on a directory: a write never cuts a file short (a block device has no length to lose), and
# BSD dd knows no conv=fsync
args = [a for a in sys.argv[1:] if not a.startswith('conv=')]
conv = [c for a in sys.argv[1:] if a.startswith('conv=') for c in a[5:].split(',') if c and c != 'fsync']
if any(a.startswith('of=') for a in args):
    conv.append('notrunc')
if conv:
    args.append('conv=' + ','.join(dict.fromkeys(conv)))
''' + 'os.execv(%r, ["dd"] + args)\n' % real_dd)
    if not shutil.which('sha256sum'):
        _exe(d / 'bin/sha256sum', '#!/bin/sh\nexec shasum -a 256 "$@"\n')
    return d


def sysfs(d, parts):
    """sysfs as the kernel shows the table PARTS"""
    d = Path(d)
    for p in (d / 'sys/block/mmcblk0').glob('mmcblk0p*'):
        shutil.rmtree(p)
    for p in (d / 'sys/class/block').glob('mmcblk0p*'):
        shutil.rmtree(p)
    for i, (name, first, last) in enumerate(parts, 1):
        for base in (d / 'sys/block/mmcblk0' / ('mmcblk0p%d' % i), d / 'sys/class/block' / ('mmcblk0p%d' % i)):
            base.mkdir(parents=True)
            (base / 'start').write_text('%d\n' % first)
            (base / 'size').write_text('%d\n' % (last - first + 1))
            (base / 'uevent').write_text('MAJOR=179\nMINOR=%d\nDEVNAME=mmcblk0p%d\nDEVTYPE=partition\nPARTN=%d\nPARTNAME=%s\n'
                                         % (i, i, i, name))


def table(d):
    """the partition table in the fake eMMC, as [(name, first, last)] (both copies must agree)"""
    d = Path(d)
    disk = int((d / 'sys/block/mmcblk0/size').read_text())
    with open(d / 'dev/block/mmcblk0', 'rb') as f:
        head = f.read(34 * SECTOR)
        f.seek((disk - 33) * SECTOR)
        tail = f.read(33 * SECTOR)
    nents, esize = struct.unpack_from('<II', head, SECTOR + 80)
    parts, back = [], []
    for i in range(nents):
        for buf, base, out in ((head, 2 * SECTOR, parts), (tail, 0, back)):
            e = buf[base + i * esize:base + (i + 1) * esize]
            if e[:16] != bytes(16):
                first, last = struct.unpack_from('<QQ', e, 32)
                out.append((e[56:128].decode('utf-16-le').rstrip('\0'), first, last))
    assert parts == back, 'the two copies of the table differ'
    return parts


def bcb(d):
    return (Path(d) / 'dev/block/by-name/misc').read_bytes()[:2048]


def reset(d, wipe=True):
    """the next start: the kernel reads the table again; recovery, when misc asks it to, formats userdata and clears
    the request (WIPE=False: a recovery that did not run)"""
    d = Path(d)
    sysfs(d, table(d))
    misc = d / 'dev/block/by-name/misc'
    data = misc.read_bytes()
    if wipe and data.startswith(b'boot-recovery\0') and b'--wipe_data' in data[64:832]:
        node = Path(os.path.realpath(d / 'dev/block/by-name/userdata'))
        node.write_bytes(bytes(1024) + bytes.fromhex('1020f5f2') + b'freshly formatted'.ljust(4096 - 1028, b'\0'))
        misc.write_bytes(bytes(2048) + data[2048:])


def read(d, sector, count):
    with open(Path(d) / 'dev/block/mmcblk0', 'rb') as f:
        f.seek(sector * SECTOR)
        return f.read(count * SECTOR)
