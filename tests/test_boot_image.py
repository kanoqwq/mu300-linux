"""boot/build-boot-image.py: the generic ramdisk segment (what every release and mu300-update ship) holds init,
the modules of boot/module-order.txt and, for another device, its own modules, order and kernel release."""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from helpers import TOP

try:
    import lz4.block
except ImportError:
    lz4 = None
HAVE_LZ4 = lz4 is not None or shutil.which('lz4') is not None

BOOT = TOP / 'boot'
RELEASE = '5.4.254-gtest'


def unlz4_legacy(data):
    """Concatenated LZ4 legacy frames (magic 0x184C2102, then blocks of <u32 size><data>) -> bytes."""
    if lz4 is None:
        return subprocess.run(['lz4', '-dc'], input=data, capture_output=True, check=True).stdout
    out, i = b'', 0
    while i < len(data):
        assert data[i:i + 4] == b'\x02\x21\x4c\x18', 'not an LZ4 legacy frame'
        i += 4
        while i + 4 <= len(data) and data[i:i + 4] != b'\x02\x21\x4c\x18':
            n = int.from_bytes(data[i:i + 4], 'little')
            i += 4
            out += lz4.block.decompress(data[i:i + n], uncompressed_size=8 << 20)
            i += n
    return out


def cpio_files(data):
    """newc cpio -> {name: (mode, data)}"""
    files, i = {}, 0
    while True:
        h = data[i:i + 110]
        assert h[:6] == b'070701', 'not newc'
        mode = int(h[14:22], 16)
        size = int(h[54:62], 16)
        nlen = int(h[94:102], 16)
        name = data[i + 110:i + 110 + nlen - 1].decode()
        i = (i + 110 + nlen + 3) & ~3
        body = data[i:i + size]
        i = (i + size + 3) & ~3
        if name == 'TRAILER!!!':
            return files
        files[name] = (mode, body)


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-bootimg-'))
        self.mods = self.tmp / 'modules'
        self.u30 = self.tmp / 'modules-u30air'
        for d in (self.mods, self.u30):
            d.mkdir()
        base = (BOOT / 'module-order.txt').read_text().split()
        u30 = (BOOT / 'module-order-u30air.txt').read_text().split()
        for n in set(base) | set(u30):
            (self.mods / n).write_bytes(b'\x7fELF base ' + n.encode() + b'\0vermagic=' + RELEASE.encode() + b' SMP\0')
        # the U30 Air set: the modules it builds differently plus the ones the F50 order does not have
        self.u30_only = sorted(set(u30) - set(base)) + ['sprd-charger-manager.ko']
        for n in self.u30_only:
            (self.u30 / n).write_bytes(b'\x7fELF u30 ' + n.encode() + b'\0vermagic=' + RELEASE.encode() + b' SMP\0')
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, *extra):
        out = self.tmp / 'ramdisk-generic.lz4'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--generic-ramdisk',
                            '--modules', str(self.mods), '--busybox', str(self.tmp / 'busybox'),
                            '--logdw', str(self.tmp / 'logdw'),
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'),
                            '--out', str(out), *extra], capture_output=True, text=True)
        return r, out


@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class GenericRamdisk(Fixtures):
    def test_base(self):
        r, out = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['init'][1], (BOOT / 'init').read_bytes())
        self.assertTrue(files['init'][0] & 0o111, 'init is not executable')
        self.assertEqual(files['etc/module-order'][1], (BOOT / 'module-order.txt').read_bytes())
        for n in (BOOT / 'module-order.txt').read_text().split():
            self.assertIn('linux-modules/' + n, files)
        self.assertNotIn('etc/mu300-device', files)
        # an empty trial guard: disarms one an experiment left in the device segment that mu300-update keeps
        self.assertEqual(files['etc/mu300-trial-guard'][1], b'')
        self.assertFalse([f for f in files if f.startswith('linux-modules/u30air/')])

    def test_e2fsck(self):
        # init's e2fsck for a root filesystem whose superblock records an error (FINDINGS 182); none without it
        self.assertNotIn('sbin/e2fsck', cpio_files(unlz4_legacy(self.build()[1].read_bytes())))
        (self.tmp / 'e2fsck').write_bytes(b'\x7fELF e2fsck')
        r, out = self.build('--e2fsck', str(self.tmp / 'e2fsck'))
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['sbin/e2fsck'][1], b'\x7fELF e2fsck')
        self.assertTrue(files['sbin/e2fsck'][0] & 0o111, 'e2fsck is not executable')

    def test_device_modules(self):
        r, out = self.build('--device-modules', f'u30air={self.u30}')
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['etc/module-order-u30air'][1], (BOOT / 'module-order-u30air.txt').read_bytes())
        for n in self.u30_only:
            self.assertEqual(files['linux-modules/u30air/' + n][1], (self.u30 / n).read_bytes())
        # the U30 Air's modules are used only with the kernel they were built for
        self.assertEqual(files['linux-modules/u30air/kernel.release'][1].decode().strip(), RELEASE)
        # every module the U30 Air order names is in the image, in its own set or the base one
        for n in (BOOT / 'module-order-u30air.txt').read_text().split():
            self.assertTrue('linux-modules/u30air/' + n in files or 'linux-modules/' + n in files, n)

    def test_crlf_checkout(self):
        # a Windows clone with core.autocrlf=true (issue #7): the device's shell must still get LF
        crlf = {}
        for name, src in (('init', BOOT / 'init'), ('perms', TOP / 'android-vendor' / 'ueventd-perms.sh'),
                          ('order', BOOT / 'module-order.txt')):
            crlf[name] = self.tmp / name
            crlf[name].write_bytes(src.read_bytes().replace(b'\n', b'\r\n'))
        r, out = self.build('--init', str(crlf['init']), '--ueventd-perms', str(crlf['perms']),
                            '--module-order', str(crlf['order']))
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['init'][1], (BOOT / 'init').read_bytes())
        self.assertEqual(files['etc/ueventd-perms.sh'][1], (TOP / 'android-vendor' / 'ueventd-perms.sh').read_bytes())
        self.assertEqual(files['etc/module-order'][1], (BOOT / 'module-order.txt').read_bytes())

    def test_generic_refuses_device_settings(self):
        # the generic segment is the same for every device: it must not name one, nor carry a trial guard
        for extra in (['--device', 'u30air'], ['--trial-guard', '300']):
            r, _ = self.build(*extra)
            self.assertNotEqual(r.returncode, 0, extra)

    def test_missing_module(self):
        (self.mods / (BOOT / 'module-order.txt').read_text().split()[0]).unlink()
        r, _ = self.build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('missing module', r.stderr)


def fake_stock(path, size=4 << 20, vbmeta_offset=1 << 20, vbmeta=b'V' * 2304):
    """An Android boot image header v4 with an AVB footer, as far as build-boot-image.py and mu300-update read it."""
    img = bytearray(size)
    img[0:8] = b'ANDROID!'
    struct.pack_into('<I', img, 40, 4)
    img[4096:8192] = b'k' * 4096
    img[vbmeta_offset:vbmeta_offset + len(vbmeta)] = vbmeta
    footer = bytearray(64)
    footer[0:4] = b'AVBf'
    struct.pack_into('>IIQQQ', footer, 4, 1, 0, vbmeta_offset, vbmeta_offset, len(vbmeta))
    img[-64:] = footer
    path.write_bytes(bytes(img))


# the slot-a block of the F50 test board: AOSP bootloader_control, nothing device-specific in it
LIVE_A = bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146')


def fake_misc(path, live=LIVE_A):
    path.write_bytes(bytes(0x800) + live + bytes(4096 - 0x800 - 32))


def with_slots(live, suffix, a, b):
    x = bytearray(live)
    x[0:4] = suffix
    x[12] = a
    x[14] = b
    x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
    return bytes(x)


@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class SlotBlocks(Fixtures):
    def image(self, *extra):
        fake_stock(self.tmp / 'stock.img')
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 100)
        out = self.tmp / 'boot.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(self.tmp / 'stock.img'),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(self.mods), '--busybox', str(self.tmp / 'busybox'),
                            '--logdw', str(self.tmp / 'logdw'), '--device', 'f50',
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'),
                            '--out', str(out), *extra], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        return cpio_files(unlz4_legacy(img[roff:roff + rsz]))

    def test_all_four_blocks(self):
        files = self.image()
        want = {'etc/misc-bc-slot-a.bin': with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e),
                'etc/misc-bc-slot-b-trial.bin': with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f),
                'etc/misc-bc-slot-b.bin': with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f),
                'etc/misc-bc-slot-a-trial.bin': with_slots(LIVE_A, b'_a\0\0', 0x2f, 0x9e)}
        for name, data in want.items():
            self.assertEqual(files[name][1], data, name)

    def test_slot_default_and_choice(self):
        self.assertEqual(self.image()['etc/mu300-linux-slot'][1], b'b\n')
        self.assertEqual(self.image('--linux-slot', 'a')['etc/mu300-linux-slot'][1], b'a\n')

    def test_trial_block_next_to_the_image_is_the_one_of_its_slot(self):
        # install.sh writes the block beside the image to misc: for Linux on a it must arm a, and no block that
        # arms b (the slot Android runs from then) may lie next to it
        for slot, other, block in (('b', 'a', with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f)),
                                   ('a', 'b', with_slots(LIVE_A, b'_a\0\0', 0x2f, 0x9e))):
            for f in self.tmp.glob('boot.misc-slot-*'):
                f.unlink()
            self.image('--linux-slot', slot)
            self.assertEqual((self.tmp / f'boot.misc-slot-{slot}-trial.bin').read_bytes(), block, slot)
            self.assertFalse((self.tmp / f'boot.misc-slot-{other}-trial.bin').exists(), slot)
            self.assertEqual(json.loads((self.tmp / 'boot.json').read_text())['linux_slot'], slot)

    def test_header_says_linux(self):
        # loglevel=5 in the header's command line is how the Magisk switch tells our image from another Android
        # (after an OTA the other slot holds the previous one); mu300-update keeps the header page as it is
        for extra in ((), ('--linux-slot', 'a')):
            self.image(*extra)
            hdr = (self.tmp / 'boot.img').read_bytes()[:4096]
            self.assertEqual(hdr[44:55], b'loglevel=5\0', extra)

    def test_device_modules_and_the_manifest(self):
        # every install from a computer passes the U30 Air's set (the 5.4 bundle has it): the manifest's module
        # count must be the base order's, not trip over the device loop's own order file (2026-10-16, F50-C:
        # "TypeError: object of type 'PosixPath' has no len()" at "Building the boot image")
        files = self.image('--device-modules', f'u30air={self.u30}')
        self.assertIn('etc/module-order-u30air', files)
        manifest = json.loads((self.tmp / 'boot.json').read_text())
        self.assertEqual(manifest['modules'], len((BOOT / 'module-order.txt').read_text().split()))

    def test_generic_has_no_slot(self):
        r, _ = self.build('--linux-slot', 'a')
        self.assertNotEqual(r.returncode, 0)


@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class WithoutTheFirstSegmentsBinaries(Fixtures):
    """A Magisk zip of a mainline kernel, unpacked on a computer (tools/package.sh), has no 5.4 bundle: the boot image's
    first segment then carries no busybox, logdw or modules, and the kernel's generic segment - unpacked after it,
    replacing what the first one has of those anyway - must bring every one of them."""

    def image(self, appended, *drop):
        fake_stock(self.tmp / 'stock.img')
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 100)
        out = self.tmp / 'boot.img'
        args = {'--modules': str(self.mods), '--busybox': str(self.tmp / 'busybox'), '--logdw': str(self.tmp / 'logdw')}
        argv = [sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(self.tmp / 'stock.img'),
                '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'), '--device', 'f50',
                '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'), '--out', str(out)]
        for k, v in args.items():
            if k not in drop:
                argv += [k, v]
        if appended:
            argv += ['--append-ramdisk', str(appended)]
        return subprocess.run(argv, capture_output=True, text=True), out

    def segment(self, files):
        """an appended segment holding FILES (names), made with the builder's own functions"""
        import importlib.util
        spec = importlib.util.spec_from_file_location('bbi', BOOT / 'build-boot-image.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        seg = self.tmp / 'segment.lz4'
        seg.write_bytes(mod.lz4_legacy(mod.cpio_archive({'bin', 'etc'}, {n: (b'x', 0o100644) for n in files})))
        return seg

    def test_the_generic_segment_brings_them(self):
        r, generic = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        r, out = self.image(generic, '--modules', '--busybox', '--logdw')
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        ram = img[roff:roff + rsz]
        first = cpio_files(unlz4_legacy(ram))            # (the first archive: cpio_files stops at its trailer)
        for f in ('bin/busybox', 'bin/logdw', 'etc/module-order'):
            self.assertNotIn(f, first)
        self.assertFalse([f for f in first if f.startswith('linux-modules/') and f.endswith('.ko')])
        for f in ('init', 'etc/mu300-device', 'etc/mu300-linux-slot', 'etc/misc-bc-slot-b-trial.bin', 'bin/sh'):
            self.assertIn(f, first)
        # behind it the generic segment, unchanged, and then this checkout's init
        self.assertIn(generic.read_bytes(), ram)
        self.assertEqual(json.loads((self.tmp / 'boot.json').read_text())['modules'], 0)

    def test_what_the_generic_segment_lacks_is_refused(self):
        for files, drop, word in ((['init', 'etc/module-order', 'bin/busybox'], '--logdw', 'bin/logdw'),
                                  (['init', 'etc/module-order', 'bin/logdw'], '--busybox', 'bin/busybox'),
                                  (['init', 'bin/busybox', 'bin/logdw'], '--modules', 'etc/module-order')):
            r, out = self.image(self.segment(files), drop)
            self.assertNotEqual(r.returncode, 0, drop)
            self.assertIn(word, r.stderr)
            self.assertFalse(out.exists())

    def test_only_with_an_appended_segment(self):
        for drop in ('--modules', '--busybox', '--logdw'):
            r, out = self.image(None, drop)
            self.assertNotEqual(r.returncode, 0, drop)
            self.assertIn('only with --append-ramdisk', r.stderr)


class CpioParents(unittest.TestCase):
    """every entry's parent directories come before it: the kernel's unpacker does not create them (kanoqwq's
    b8d4f222); an appended segment that names only a file gets its directory too"""

    def test_parents_first(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = mod.cpio_archive({'dev'}, {'etc/mu300/linux-slot': (b'a\n', 0o100644), 'init': (b'#!/bin/sh\n', 0o100755)})
        order, i = [], 0
        while True:
            h = raw[i:i + 110]
            self.assertEqual(h[:6], b'070701')
            mode, size, nlen = int(h[14:22], 16), int(h[54:62], 16), int(h[94:102], 16)
            name = raw[i + 110:i + 110 + nlen - 1].decode()
            i = (i + 110 + nlen + 3) & ~3
            i = (i + size + 3) & ~3
            if name == 'TRAILER!!!':
                break
            order.append((name, mode))
        names = [n for n, _ in order]
        for name in names:
            if '/' in name:
                self.assertIn(name.rsplit('/', 1)[0], names[:names.index(name)], name)
        self.assertEqual(dict(order)['etc/mu300'] & 0o170000, 0o040000)
        self.assertEqual(len(names), len(set(names)))

class ModuleOrder(unittest.TestCase):
    def test_u30air_order(self):
        base = (BOOT / 'module-order.txt').read_text().split()
        u30 = (BOOT / 'module-order-u30air.txt').read_text().split()
        self.assertEqual(len(u30), len(set(u30)), 'a module is listed twice')
        # the F50's charger driver is replaced, not loaded next to the SQC one
        self.assertNotIn('bq2560x-charger.ko', u30)
        for n in ('sqc_bq2560x.ko', 'sqc_charger.ko', 'charger_policy_service.ko', 'zte_power_supply.ko'):
            self.assertIn(n, u30)
        # dependencies first: the fuel gauge needs the charger manager, which needs the SQC stack
        for a, b in (('zte_power_supply.ko', 'charger_policy_service.ko'), ('sqc_charger.ko', 'sqc_bq2560x.ko'),
                     ('sprd-charger-manager.ko', 'sc27xx_fuel_gauge.ko'), ('sprd_tcpm.ko', 'sc27xx_pd.ko')):
            self.assertLess(u30.index(a), u30.index(b), f'{a} must load before {b}')
        # everything else keeps the F50's order
        common = [n for n in base if n in u30]
        self.assertEqual(common, [n for n in u30 if n in common])


if __name__ == '__main__':
    unittest.main()
