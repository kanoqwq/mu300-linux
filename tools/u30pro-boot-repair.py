#!/usr/bin/env python3
"""Build a device-bound initramfs repair from verified local installation records.

Offline only. No device connection, flashing, GPT/rootfs or NV operations.
The wrapped kernel, device DTB, DTBO and Android boot slot stay unchanged.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import sys

TOP = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(transaction, output, package=None):
    transaction, output = Path(transaction), Path(output)
    record = json.loads((transaction/'backup/native-before.json').read_text())
    state = json.loads((transaction/'transaction.json').read_text())
    slot = record['android_slot']
    if slot not in ('a', 'b') or state['device']['slot'] != '_'+slot:
        raise ValueError('Unconfirmed Android slot')
    package = Path(package or state['package']['path'])
    if not adapter_enabled(package):
        raise ValueError('Select the original U30 Pro package directory')
    fw = load('repair_firmware', TOP/'devices/u30pro/native/tools/firmware.py')
    ota = load('repair_ramdisk', TOP/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/boot.py')
    cpio = load('repair_cpio', TOP/'boot/build-boot-image.py')
    adapter = load('repair_adapter', TOP/'desktop/native_u30pro.py')
    templates = {}
    for name in ('boot', 'vendor_boot', 'init_boot'):
        part = name+'_'+slot
        raw = (transaction/'backup'/(part+'.bin')).read_bytes()
        if digest(raw) != record['files'][part]:
            raise ValueError('Invalid Android backup: '+part)
        templates[name] = raw
    current = {}
    for name in ('boot', 'vendor_boot', 'init_boot', 'dtbo'):
        raw = (transaction/'native-boot'/(name+'.img')).read_bytes()
        if digest(raw) != record['planned'][name]:
            raise ValueError('Invalid installed-image record: '+name)
        current[name] = raw
    kernel, ram = fw.boot(current['boot'])
    if fw.vendor_boot(current['vendor_boot'])[0] != ram:
        raise ValueError('Installed boot/vendor ramdisks disagree')
    entries = {}
    for archive in ota.archives(ram):
        entries.update(fw.cpio_read(archive))
    if entries.get('etc/mu300-android-slot', (0, b''))[1] != (slot+'\n').encode():
        raise ValueError('Installed initramfs belongs to another Android slot')
    for name, source in {
        'init': 'devices/u30pro/init',
        'etc/u30pro-boot-wait.sh': 'devices/u30pro/boot-wait.sh',
        'usr/sbin/u30pro-usb': 'devices/u30pro/native/ui/native-control/overlay/usr/sbin/u30pro-usb',
    }.items():
        entries[name] = (stat.S_IFREG | 0o755, (TOP/source).read_bytes().replace(b'\r\n', b'\n'))
    dirs = {n for n, (mode, _) in entries.items() if stat.S_ISDIR(mode)}
    files = {n: (data, mode) for n, (mode, data) in entries.items()
             if not stat.S_ISDIR(mode) and n != 'dev/console'}
    new_ram = cpio.lz4_legacy(cpio.cpio_archive(dirs, files))
    header = bytearray(current['boot'][:4096])
    struct.pack_into('<I', header, 12, len(new_ram))
    canonical = fw.retain_avb_container(current['boot'], bytes(header)+kernel+bytes(-len(kernel)%4096)+new_ram)
    images = adapter.images(package, canonical, templates)
    if fw.boot(images['boot'])[0] != kernel:
        raise ValueError('Kernel changed during initramfs-only repair')
    if fw.vendor_boot(images['vendor_boot'])[1:] != fw.vendor_boot(current['vendor_boot'])[1:]:
        raise ValueError('Device DTB/bootconfig changed')
    manifest = dict(android_slot=slot, linux_slot='b' if slot == 'a' else 'a',
                    kernel_sha256=digest(kernel), device_writes=False,
                    android_before={name:record['files'][name+'_'+slot] for name in images},
                    linux_before=record['planned'], images={},
                    source={n:digest((TOP/n).read_bytes()) for n in (
                        'devices/u30pro/init', 'devices/u30pro/boot-wait.sh',
                        'devices/u30pro/native/ui/native-control/overlay/usr/sbin/u30pro-usb')})
    output.mkdir(parents=True, exist_ok=False)
    for name, data in images.items():
        if len(data) != len(current[name]):
            raise ValueError('Partition size changed: '+name)
        if data == current[name]:
            continue
        (output/(name+'.img')).write_bytes(data)
        manifest['images'][name] = dict(sha256=digest(data), bytes=len(data))
    # Exactly the two ramdisk-containing Linux partitions may change.
    if set(manifest['images']) != {'boot', 'vendor_boot'}:
        raise ValueError('Unexpected repair partition set')
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


def adapter_enabled(package):
    manifest = package/'desktop/manifest.json'
    return manifest.is_file() and json.loads(manifest.read_text()).get('platform') == 'u30pro'


def kernel_trial(current_boot, wrapped_kernel, output):
    """Repack one captured Linux boot; keep its verified working ramdisk.

    The wrapper must match the registered kernel build receipt. Does not read
    or write any device; the caller separately verifies device/slot ownership.
    """
    current_boot, wrapped_kernel, output = map(Path, (current_boot, wrapped_kernel, output))
    proof = json.loads(wrapped_kernel.with_suffix('.manifest.json').read_text())
    layout = json.loads((TOP/'tools/build-layout.json').read_text())
    objects = Path(layout['kernel_objects'].replace('{workspace}', layout['workspace']))
    report = json.loads((objects/'u30pro-build.json').read_text())
    new_kernel = wrapped_kernel.read_bytes()
    if (digest(new_kernel) != proof['output_sha256'] or
            proof['input_sha256'] != report['image_sha256'] or
            digest((objects/'arch/arm64/boot/Image').read_bytes()) != report['image_sha256'] or
            digest((objects/'.config').read_bytes()) != report['config_sha256'] or
            proof.get('test_only')):
        raise ValueError('Kernel wrapper/build receipt mismatch')
    fw = load('kernel_trial_codec', TOP/'devices/u30pro/native/tools/firmware.py')
    before = current_boot.read_bytes()
    old_kernel, ramdisk = fw.boot(before)
    header = bytearray(before[:4096])
    struct.pack_into('<I', header, 8, len(new_kernel))
    after = fw.retain_avb_container(before, bytes(header)+new_kernel+
                                  bytes(-len(new_kernel)%4096)+ramdisk)
    if len(after) != len(before) or fw.boot(after) != (new_kernel, ramdisk):
        raise ValueError('Boot repack did not preserve partition size/ramdisk')
    output.mkdir(parents=True, exist_ok=False)
    (output/'boot.img').write_bytes(after)
    manifest = {'device_writes': False, 'mode': 'kernel-only-trial',
                'before_sha256': digest(before), 'after_sha256': digest(after),
                'old_kernel_sha256': digest(old_kernel), 'new_kernel_sha256': digest(new_kernel),
                'ramdisk_sha256': digest(ramdisk), 'kernel_build': report}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='kernel-trial':
        parser=argparse.ArgumentParser(description=kernel_trial.__doc__)
        parser.add_argument('current_boot', type=Path)
        parser.add_argument('wrapped_kernel', type=Path)
        parser.add_argument('output', type=Path)
        args=parser.parse_args(sys.argv[2:])
        kernel_trial(args.current_boot, args.wrapped_kernel, args.output)
        sys.exit(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('transaction', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--package', type=Path, help='Original package path on this host (e.g. WSL path)')
    args = parser.parse_args()
    build(args.transaction, args.output, args.package)
