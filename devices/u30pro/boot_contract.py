"""Pure U30 Pro boot-layout planning; no I/O, writes or device commands.

GPT LBAs use the disk's reported logical sector size. The native SDHCI write
window ABI uses Linux 512-byte sectors. Never copy reference-device LBAs.
"""
import re

IMAGES = ('boot', 'vendor_boot', 'init_boot')
BCAB_OFFSET = 2048
BCAB_SIZE = 32


def slot(value):
    if value not in ('a', 'b', '_a', '_b'):
        raise ValueError('A verified Android slot is required')
    return value.removeprefix('_')


def plan(android_slot, logical_sector_bytes, partitions):
    android = slot(android_slot)
    linux = 'b' if android == 'a' else 'a'
    if type(logical_sector_bytes) is not int or logical_sector_bytes not in (512, 4096):
        raise ValueError('Unsupported or unconfirmed logical sector size')
    if not isinstance(partitions, dict) or not partitions:
        raise ValueError('Verified GPT partition geometry is required')
    geometry = {}
    for name, row in partitions.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,36}', name):
            raise ValueError('Invalid GPT partition name')
        if not isinstance(row, dict):
            raise ValueError('Invalid GPT partition record: ' + name)
        first, count = row.get('first_lba'), row.get('sectors')
        if (type(first) is not int or type(count) is not int or first < 1 or count < 1 or
                (first + count) * logical_sector_bytes >= 2**64):
            raise ValueError('Invalid GPT partition geometry: ' + name)
        geometry[name] = (first * logical_sector_bytes, count * logical_sector_bytes)
    ordered = sorted((start, start + length, name) for name, (start, length) in geometry.items())
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0]:
            raise ValueError('Overlapping GPT partitions: ' + left[2] + ', ' + right[2])
    required = {f'{name}_{side}' for name in IMAGES for side in (android, linux)} | {'misc'}
    missing = required - geometry.keys()
    if missing:
        raise ValueError('Missing boot partitions: ' + ', '.join(sorted(missing)))
    if geometry['misc'][1] < BCAB_OFFSET + BCAB_SIZE:
        raise ValueError('Misc partition is too small for boot control')
    # Grant only the boot-control sector, not the whole misc partition. Other
    # windows belong to this Linux slot's three images and the MU300 container.
    windows = [(geometry['misc'][0] // 512 + BCAB_OFFSET // 512, 1)]
    images = []
    for name in IMAGES:
        target = f'{name}_{linux}'
        start, size = geometry[target]
        windows.append((start // 512, size // 512))
        images.append({'image': name, 'target': target,
                       'android_template': f'{name}_{android}', 'partition_bytes': size})
    if 'mu300root' in geometry:
        start, size = geometry['mu300root']
        windows.append((start // 512, size // 512))
    return {
        'format': 1, 'profile': 'u30pro', 'android_slot': android, 'linux_slot': linux,
        'logical_sector_bytes': logical_sector_bytes, 'images': images,
        'root': 'mu300-container-ext4',
        'root_partition': 'mu300root' if 'mu300root' in geometry else None,
        'firmware_slot': android, 'write_windows_512': sorted(windows),
        'cmdline_window': 'u30pro_rwx=' + ','.join(f'{start}+{count}' for start, count in sorted(windows)),
        'nv_write_windows': [],
    }
