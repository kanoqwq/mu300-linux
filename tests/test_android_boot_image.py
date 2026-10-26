"""tools/android-boot-image.sh: the parts of the Linux boot image only the device can make - its misc blocks, its
Android files, the device ramdisk segment - checked against boot/build-boot-image.py on the same inputs."""
import importlib.util
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest
from test_boot_image import HAVE_LZ4, LIVE_A, cpio_files, fake_misc, fake_stock, unlz4_legacy

LIB = TOP / 'tools' / 'android-boot-image.sh'
spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
bbi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bbi)

# magiskboot compress=lz4_legacy IN OUT and decompress IN OUT, done by the builder's own lz4_legacy and the tests'
# unlz4_legacy (the lz4 command or module)
MAGISKBOOT = f'''#!/bin/sh
case $1 in
    compress=lz4_legacy) f='m.lz4_legacy' ;;
    decompress) f='unlz4_legacy' ;;
    *) exit 1 ;;
esac
exec "{sys.executable}" -c "import importlib.util,sys; sys.path.insert(0,'{TOP}/tests'); from test_boot_image import unlz4_legacy; s=importlib.util.spec_from_file_location('b','{TOP}/boot/build-boot-image.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); open(sys.argv[2],'wb').write($f(open(sys.argv[1],'rb').read()))" "$2" "$3"
'''

# the same, but compress ends the frame with the uncompressed size, as Magisk's lz4_lg does
MAGISKBOOT_SIZE_WORD = f'''"$STUBLOG/stubs/magiskboot" "$@" || exit 1
case $1 in compress=*) exec "{sys.executable}" -c "import os,struct,sys; open(sys.argv[2],'ab').write(struct.pack('<I', os.path.getsize(sys.argv[1])))" "$2" "$3" ;; esac
'''


def cpio_all(data):
    """every newc archive of a byte string (as the kernel reads concatenated segments), later files win"""
    files, i = {}, 0
    while i < len(data):
        if data[i:i + 6] != b'070701':
            i += 1
            continue
        part = cpio_files(data[i:])
        files.update(part)
        i = data.index(b'TRAILER!!!', i) + 10
    return files


def subset_paths():
    lines = (TOP / 'android-vendor' / 'subset-files.txt').read_text().splitlines()
    return [l for l in lines if l.strip() and not l.startswith('#')]


def fake_android_root(root):
    """a / with every file of the subset list (dirs get one file inside) and the firmware"""
    for p in subset_paths():
        f = root / p
        if p.endswith(('bionic', '__properties__')):
            f.mkdir(parents=True, exist_ok=True)
            (f / 'u:object_r:x:s0' if '__properties__' in p else f / 'libc.so').write_bytes(b'data ' + p.encode())
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'data ' + p.encode())
    os.chmod(root / 'vendor/bin/modem_control', 0o755)
    fw = root / 'vendor/firmware'
    fw.mkdir(parents=True, exist_ok=True)
    for n in ('wcnmodem.bin', 'gnssmodem.bin', 'wifi_board_config.ini', 'wifi_board_config_ab.ini',
              'bt_configure_pskey.ini', 'bt_configure_rf.ini'):
        (fw / n).write_bytes(b'fw ' + n.encode())


def ramdisk_of(img):
    ksz, rsz = struct.unpack_from('<II', img, 8)
    roff = 4096 + (ksz + 4095) // 4096 * 4096
    return img[roff:roff + rsz]


def leaves(files):
    """the regular files and symlinks of cpio_all's result: kind, executable or not, contents"""
    return {n: (stat.S_IFMT(m), bool(m & 0o111), d) for n, (m, d) in files.items()
            if stat.S_ISREG(m) or stat.S_ISLNK(m)}


class Lists(unittest.TestCase):
    def test_subset_list_is_extract_subsets(self):
        sh = (TOP / 'android-vendor' / 'extract-subset.sh').read_text()
        m = re.search(r'tar -cf - (apex/.*?) \| tar -xf -', sh, re.S)
        self.assertEqual(m.group(1).replace('\\\n', ' ').split(), subset_paths())


class DeviceSide(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('magiskboot', MAGISKBOOT)

    def lib(self, shell, code, magiskboot='magiskboot', **env):
        pre = f'MU300_LIB=1 . "{BIN}/mu300-update"; . "{LIB}"; '
        return self.sh(shell, pre + code, MAGISKBOOT=self.stubs / magiskboot, **env)

    def test_blocks_equal_the_python_builders(self):
        want = dict(zip(('slot-a', 'slot-b-trial', 'slot-b', 'slot-a-trial'),
                        bbi.bootloader_control(bytes(0x800) + LIVE_A)))
        for shell in self.each_shell():
            d = self.tmp / 'etc'
            shutil.rmtree(d, ignore_errors=True); d.mkdir()
            r = self.lib(shell, f'bc_valid {LIVE_A.hex()} && bc_files {LIVE_A.hex()} "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            for n, data in want.items():
                self.assertEqual((d / f'misc-bc-{n}.bin').read_bytes(), data, (shell, n))

    def test_invalid_live_block(self):
        bad = bytearray(LIVE_A); bad[20] ^= 1
        for shell in self.each_shell():
            self.assertNotEqual(self.lib(shell, f'bc_valid {bytes(bad).hex()}').returncode, 0)
            self.assertNotEqual(self.lib(shell, 'bc_valid 00').returncode, 0)

    def test_subset_and_firmware_from_the_device(self):
        root = self.tmp / 'root'
        fake_android_root(root)
        for shell in self.each_shell():
            s, fw = self.tmp / 'subset', self.tmp / 'fw'
            r = self.lib(shell, f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{s}" && collect_firmware "{fw}"',
                         MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/odm/firmware {root}/vendor/firmware')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((s / 'vendor/bin/modem_control').is_file())
            self.assertEqual(os.readlink(s / 'system/bin/linker64'), '/apex/com.android.runtime/bin/linker64')
            self.assertEqual((s / 'linkerconfig/ld.config.txt').read_bytes(), b'')
            self.assertEqual(sorted(os.listdir(fw)), sorted(os.listdir(root / 'vendor/firmware')))
            shutil.rmtree(fw)
            (root / 'vendor/firmware/gnssmodem.bin').rename(root / 'gnss.bak')
            r = self.lib(shell, f'collect_firmware "{fw}"', MU300_FW_DIRS=f'{root}/vendor/firmware')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('gnssmodem.bin', r.stdout)
            (root / 'gnss.bak').rename(root / 'vendor/firmware/gnssmodem.bin')
            shutil.rmtree(s); shutil.rmtree(fw, ignore_errors=True)

    def test_vendor_overlay_layout(self):
        # the layout of tools/vendor-overlay.py: firmware, the subset with its property area moved out of dev/
        root = self.tmp / 'root'
        fake_android_root(root)
        for shell in self.each_shell():
            for os_name, fwdir in (('ubuntu', 'usr/lib/firmware'), ('openwrt', 'lib/firmware')):
                out = self.tmp / f'v-{os_name}.tar.gz'
                r = self.lib(shell, f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{self.tmp}/s" && '
                                    f'collect_firmware "{self.tmp}/fw" && vendor_overlay {os_name} "{self.tmp}/fw" "{self.tmp}/s" "" "{out}"',
                             MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/vendor/firmware')
                self.assertEqual(r.returncode, 0, r.stderr)
                names = subprocess.run(['tar', '-tzf', str(out)], capture_output=True, text=True).stdout.split()
                names = [n.lstrip('./') for n in names]
                self.assertIn(f'{fwdir}/wcnmodem.bin', names)
                self.assertIn('opt/mu300/android/vendor/bin/modem_control', names)
                self.assertTrue(any(n.startswith('opt/mu300/android/dev-properties/') for n in names))
                self.assertFalse([n for n in names if n.startswith('opt/mu300/android/dev/')])
                # no entry for a directory the system already has: Ubuntu's lib is a link to usr/lib
                self.assertFalse([n for n in names if n.rstrip('/') in ('lib', 'usr', 'usr/lib', 'opt', 'opt/mu300')])
                shutil.rmtree(self.tmp / 's'); shutil.rmtree(self.tmp / 'fw')

    def bundle_inputs(self):
        """one set of inputs: Android's root, its boot image and misc, a kernel bundle (Image and generic segment)
        built from fake modules and tools; returns the builder's command line arguments they share"""
        self.root = self.tmp / 'root'
        fake_android_root(self.root)
        self.stock, self.misc = self.tmp / 'stock.img', self.tmp / 'misc.bin'
        fake_stock(self.stock)
        fake_misc(self.misc)
        mods, self.bundle = self.tmp / 'modules', self.tmp / 'bundle'
        mods.mkdir(); self.bundle.mkdir()
        for n in (TOP / 'boot' / 'module-order.txt').read_text().split():
            (mods / n).write_bytes(b'\x7fELF base ' + n.encode())
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())
        (self.bundle / 'Image').write_bytes(b'\x7fkernel' * 1000)
        self.common = ['--modules', str(mods), '--busybox', str(self.tmp / 'busybox'),
                       '--logdw', str(self.tmp / 'logdw'), '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh')]
        self.builder = [sys.executable, str(TOP / 'boot' / 'build-boot-image.py')]
        r = subprocess.run(self.builder + ['--generic-ramdisk', '--out', str(self.bundle / 'ramdisk-generic.lz4')] +
                           self.common, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def device_image(self, shell, magiskboot='magiskboot'):
        """the device side, as the installer does it: work/dev.lz4 and work/out/{new,vbmeta,newfooter}"""
        work = self.tmp / 'work'
        shutil.rmtree(work, ignore_errors=True)
        seg, out = work / 'seg', work / 'out'
        seg.mkdir(parents=True); out.mkdir()
        r = self.lib(shell, f'mkdir -p "{seg}/etc" && echo f50 > "{seg}/etc/mu300-device" && '
                            f'echo b > "{seg}/etc/mu300-linux-slot" && bc_files {LIVE_A.hex()} "{seg}/etc" && '
                            f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{seg}/android" && '
                            f'device_segment "{seg}" "{work}/dev.lz4" && '
                            f'linux_boot_image "{self.stock}" "{self.bundle}" "{work}/dev.lz4" "{out}"',
                     magiskboot=magiskboot, MU300_ANDROID_ROOT=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((work / 'dev.lz4.cpio').exists())
        return work

    @unittest.skipIf(not HAVE_LZ4, 'no lz4')
    def test_whole_image_holds_what_the_python_builder_puts_in(self):
        self.bundle_inputs()
        bundle = self.bundle
        for shell in self.each_shell():
            work = self.device_image(shell)
            seg, out = work / 'seg', work / 'out'
            # the same on a computer, with the subset the device collected
            py = work / 'py.img'
            r = subprocess.run(self.builder + ['--stock-boot', str(self.stock), '--misc-head', str(self.misc),
                                               '--kernel', str(bundle / 'Image'),
                                               '--append-ramdisk', str(bundle / 'ramdisk-generic.lz4'),
                                               '--device', 'f50', '--linux-slot', 'b',
                                               '--android-subset', str(seg / 'android'), '--out', str(py)] + self.common,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

            pyimg, new = py.read_bytes(), (out / 'new').read_bytes()
            got = leaves(cpio_all(unlz4_legacy(ramdisk_of(new))))
            self.assertEqual(got, leaves(cpio_all(unlz4_legacy(ramdisk_of(pyimg)))))
            self.assertTrue(got['android/vendor/bin/modem_control'][1], 'modem_control is not executable')
            self.assertEqual(got['etc/mu300-device'][2], b'f50\n')

            # the kernel does not create the parents of what it unpacks: the device segment names every one
            devseg = cpio_files(unlz4_legacy((work / 'dev.lz4').read_bytes()))
            dirs = {n for n, (m, _) in devseg.items() if stat.S_ISDIR(m)}
            for n in devseg:
                parts = n.split('/')
                for i in range(1, len(parts)):
                    self.assertIn('/'.join(parts[:i]), dirs, n)

            # the header page: Android's, with this kernel's and ramdisk's sizes and the marker the switch looks for
            ksz, rsz = struct.unpack_from('<II', new, 8)
            self.assertEqual(ksz, (bundle / 'Image').stat().st_size)
            self.assertEqual(rsz, (work / 'dev.lz4').stat().st_size + (bundle / 'ramdisk-generic.lz4').stat().st_size)
            self.assertEqual(new[44:44 + 1536], b'loglevel=5'.ljust(1536, b'\0'))
            self.assertEqual(new[:8] + new[16:4096], pyimg[:8] + pyimg[16:4096])
            self.assertEqual(len(new) % 4096, 0)
            # Android's vbmeta and its footer, with the sizes of this image
            osz, _, vbs = struct.unpack_from('>QQQ', pyimg, len(pyimg) - 64 + 12)
            self.assertEqual((out / 'vbmeta').read_bytes(), pyimg[osz:osz + vbs])
            foot = (out / 'newfooter').read_bytes()
            self.assertEqual(struct.unpack_from('>QQQ', foot, 12), (len(new), len(new), vbs))
            self.assertEqual(foot[:12] + foot[36:], pyimg[-64:-52] + pyimg[-28:])

    @unittest.skipIf(not HAVE_LZ4, 'no lz4')
    def test_a_size_word_behind_the_frame_does_not_hide_the_generic_segment(self):
        # in front of the generic segment, the kernel would read a trailing size word as a chunk size
        self.stub('magiskboot-lg', MAGISKBOOT_SIZE_WORD)
        self.bundle_inputs()
        generic = cpio_files(unlz4_legacy((self.bundle / 'ramdisk-generic.lz4').read_bytes()))
        for shell in self.each_shell():
            work = self.device_image(shell, 'magiskboot-lg')
            files = cpio_all(unlz4_legacy(ramdisk_of((work / 'out' / 'new').read_bytes())))
            self.assertEqual(files['init'][1], (TOP / 'boot' / 'init').read_bytes())
            for n in generic:
                self.assertIn(n, files)
            self.assertIn('android/vendor/bin/modem_control', files)
            self.assertEqual(files['etc/mu300-device'][1], b'f50\n')

    def test_device_segment_refuses_a_bad_frame_and_leaves_no_copy(self):
        d = self.tmp / 'd'
        (d / 'etc').mkdir(parents=True)
        (d / 'etc' / 'x').write_bytes(b'proprietary')
        # no whole chunk behind the magic; a frame that does not give back the cpio
        self.stub('mb-short', 'case $1 in compress=*) printf "\\002\\041\\114\\030\\377\\000\\000\\000ab" > "$3" ;; esac')
        self.stub('mb-wrong', 'case $1 in compress=*) printf "\\002\\041\\114\\030\\002\\000\\000\\000ab" > "$3" ;; '
                              'decompress) printf other > "$3" ;; esac')
        # magiskboot's own reason, kept: on the device it is the only clue
        self.stub('mb-err', 'echo "magiskboot: No space left on device" >&2; exit 1')
        for shell in self.each_shell():
            for mb, why in (('mb-short', 'no whole LZ4 chunk'), ('mb-wrong', 'does not decompress'),
                            ('mb-err', 'No space left on device')):
                r = self.lib(shell, f'device_segment "{d}" "{self.tmp}/seg.lz4"', magiskboot=mb)
                self.assertNotEqual(r.returncode, 0, mb)
                self.assertIn(why, r.stderr)
                self.assertEqual([p.name for p in self.tmp.glob('seg.lz4*')], [], mb)

    def gpu_root(self):
        root = self.tmp / 'groot'
        paths = ['vendor/lib64/libOpenCL.so', 'vendor/lib64/egl/libGLES_mali.so', 'system/lib64/libc.so']
        for p in paths:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_bytes(b'gpu ' + p.encode())
        lst = self.tmp / 'gpu-files.txt'
        lst.write_text('# a header\n' + '\n'.join(paths) + '\n')
        return root, lst, paths

    def test_collect_gpu_all_or_nothing(self):
        root, lst, paths = self.gpu_root()
        g = self.tmp / 'gpu'
        for shell in self.each_shell():
            r = self.lib(shell, f'collect_gpu "{lst}" "{g}"', MU300_ANDROID_ROOT=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            for p in paths:
                self.assertEqual((g / p).read_bytes(), b'gpu ' + p.encode())
            (root / paths[1]).rename(self.tmp / 'gone')
            r = self.lib(shell, f'collect_gpu "{lst}" "{g}"', MU300_ANDROID_ROOT=root)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(g.exists())
            (self.tmp / 'gone').rename(root / paths[1])

    def test_vendor_overlay_gpu_files(self):
        root = self.tmp / 'root'
        fake_android_root(root)
        g = self.tmp / 'gpu'
        (g / 'vendor/lib64').mkdir(parents=True)
        (g / 'vendor/bin').mkdir(parents=True)
        (g / 'vendor/lib64/libOpenCL.so').write_bytes(b'opencl')
        (g / 'vendor/bin/modem_control').write_bytes(b'not the subset one')
        out = self.tmp / 'v.tar.gz'
        pre = (f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{self.tmp}/s" && '
               f'collect_firmware "{self.tmp}/fw" && ')
        for shell in self.each_shell():
            r = self.lib(shell, pre + f'vendor_overlay ubuntu "{self.tmp}/fw" "{self.tmp}/s" "{g}" "{out}"',
                         MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/vendor/firmware')
            self.assertEqual(r.returncode, 0, r.stderr)
            with tarfile.open(out) as t:
                files = {m.name.lstrip('./'): t.extractfile(m).read() for m in t.getmembers() if m.isreg()}
            self.assertEqual(files['opt/mu300/android/vendor/lib64/libOpenCL.so'], b'opencl')
            # the subset's file wins
            self.assertEqual(files['opt/mu300/android/vendor/bin/modem_control'], b'data vendor/bin/modem_control')
            self.assertFalse(list(self.tmp.glob('v.tar.gz.*')))
            # a GPU file that cannot be copied fails the overlay (here its directory is a file of the subset), and
            # leaves neither the archive nor the copies behind
            bad = self.tmp / 'gpu-bad'
            (bad / 'vendor/bin/modem_control').mkdir(parents=True)
            (bad / 'vendor/bin/modem_control/x').write_bytes(b'x')
            r = self.lib(shell, pre + f'vendor_overlay ubuntu "{self.tmp}/fw" "{self.tmp}/s" "{bad}" "{out}"',
                         MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/vendor/firmware')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(list(self.tmp.glob('v.tar.gz*')))
            shutil.rmtree(bad); shutil.rmtree(self.tmp / 's'); shutil.rmtree(self.tmp / 'fw')

    def test_the_import_does_not_take_the_callers_umask(self):
        # only the files are named in the list, so tar makes vendor/etc and the others itself: under a root shell
        # with umask 077 they were 0700, and modem_control (uid 1000) could not reach its XML files in them
        root = self.tmp / 'root'
        fake_android_root(root)
        g = self.tmp / 'gpu'
        (g / 'vendor/lib64/egl').mkdir(parents=True)
        (g / 'vendor/lib64/egl/libGLES_mali.so').write_bytes(b'mali')
        # the device's own modes, whatever umask the tests run under: what is asserted below is what the import
        # adds to them
        for top in (root, g):
            for f in top.rglob('*'):
                if not f.is_symlink():
                    f.chmod(0o755 if f.is_dir() or f.name == 'modem_control' else 0o644)
        if os.getuid() == 0:
            # as on Android: bionic is system's, the programs are group shell's (only root can set that up)
            os.chown(root / 'apex/com.android.runtime/lib64/bionic/libc.so', 1000, 1000)
            os.chown(root / 'vendor/bin/modem_control', 0, 2000)
        out = self.tmp / 'v.tar.gz'
        for shell in self.each_shell():
            r = self.lib(shell, f'umask 077; collect_subset "{TOP}/android-vendor/subset-files.txt" "{self.tmp}/s" && '
                                f'collect_firmware "{self.tmp}/fw" && '
                                f'vendor_overlay openwrt "{self.tmp}/fw" "{self.tmp}/s" "{g}" "{out}" && umask',
                         MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/vendor/firmware')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(r.stdout.split()[-1], ('0077', '077'), shell)      # the caller's own is not changed
            for d in ('vendor', 'vendor/etc', 'vendor/bin', 'system/lib64', 'system/bin', 'linkerconfig',
                      'apex/com.android.runtime/lib64'):
                self.assertEqual(stat.S_IMODE((self.tmp / 's' / d).stat().st_mode), 0o755, (shell, d))
            self.assertEqual(stat.S_IMODE((self.tmp / 's/linkerconfig/ld.config.txt').stat().st_mode), 0o644, shell)
            with tarfile.open(out) as t:
                modes = {m.name.lstrip('./').rstrip('/'): m.mode & 0o7777 for m in t.getmembers() if not m.issym()}
                owners = {(m.uid, m.gid) for m in t.getmembers()}
            if os.getuid() == 0:
                self.assertEqual(owners, {(0, 0)}, shell)
            dirs = {n: m for n, m in modes.items() if n in (
                'opt/mu300/android', 'opt/mu300/android/vendor', 'opt/mu300/android/vendor/etc',
                'opt/mu300/android/vendor/lib64/egl', 'opt/mu300/android/linkerconfig', 'lib/firmware')}
            self.assertEqual(set(dirs.values()), {0o755}, (shell, dirs))
            self.assertGreaterEqual(len(dirs), 6, (shell, dirs))
            self.assertEqual(modes['opt/mu300/android/vendor/etc/modem_cp_info.xml'], 0o644, shell)
            self.assertEqual(modes['opt/mu300/android/linkerconfig/ld.config.txt'], 0o644, shell)
            self.assertEqual(modes['opt/mu300/android/vendor/lib64/egl/libGLES_mali.so'], 0o644, shell)
            self.assertEqual(modes['lib/firmware/wcnmodem.bin'], 0o644, shell)
            shutil.rmtree(self.tmp / 's'); shutil.rmtree(self.tmp / 'fw')

    def test_the_overlay_is_packed_as_roots(self):
        # tar stores the owner each copy has: on Android bionic is system's (uid 1000, the first login account of
        # the installed system) and the programs are group shell's. Only root can give files away, so here the
        # line is read; the installer always is root.
        text = LIB.read_text()
        body = text[text.index('\n_vendor_overlay() {'):]
        self.assertLess(body.index('chown -hR 0:0 "$_o"'), body.index('tar -czf "$5"'))
        self.assertIn('[ "$(id -u)" != 0 ] || chown -hR 0:0 "$_o" || return 1', body)


class HostOverlay(unittest.TestCase):
    """tools/vendor-overlay.py: the modes it stores come from where a file goes, not from the file system the
    subset was extracted on (issue #127: a work directory on FAT/exFAT under macOS shows everything as 0700)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-test-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def subset(self):
        s, g, fw = self.tmp / 'android-subset', self.tmp / 'android-gpu-subset', self.tmp / 'firmware'
        for d in (s, g, fw):
            shutil.rmtree(d, ignore_errors=True)
        files = {'dev/__properties__/property_info': 0o444, 'dev/__properties__/u:object_r:radio_prop:s0': 0o444,
                 'vendor/bin/modem_control': 0o755, 'vendor/bin/cp_diskserver': 0o755,
                 'vendor/etc/modem_cp_info.xml': 0o644, 'vendor/lib64/lib_crypto.so': 0o644,
                 'system/lib64/libc++.so': 0o644, 'apex/com.android.runtime/bin/linker64': 0o755,
                 'apex/com.android.runtime/lib64/bionic/libc.so': 0o644, 'linkerconfig/ld.config.txt': 0o644}
        for rel, mode in files.items():
            (s / rel).parent.mkdir(parents=True, exist_ok=True)
            (s / rel).write_text(rel)
            (s / rel).chmod(mode)
        (s / 'system/bin').mkdir()
        os.symlink('/apex/com.android.runtime/bin/linker64', s / 'system/bin/linker64')
        (s / 'dev/__properties__').chmod(0o711)
        (g / 'vendor/lib64/egl').mkdir(parents=True)
        (g / 'vendor/lib64/egl/libGLES_mali.so').write_text('mali')
        fw.mkdir()
        (fw / 'wcnmodem.bin').write_text('fw')
        return s, g, fw

    def pack(self, os_name, s, g, fw):
        out = self.tmp / f'v-{os_name}.tar.gz'
        r = subprocess.run([sys.executable, str(TOP / 'tools' / 'vendor-overlay.py'), '--os', os_name, '--firmware',
                            str(fw), '--android-subset', str(s), '--gpu-subset', str(g), '--out', str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with tarfile.open(out) as t:
            return {m.name: ('l' if m.issym() else 'd' if m.isdir() else 'f', m.mode & 0o7777, m.uid, m.gid,
                             m.uname, m.gname) for m in t.getmembers()}

    WANT = {'opt/mu300/android/dev-properties': ('d', 0o755), 'opt/mu300/android/dev-properties/property_info': ('f', 0o444),
            'opt/mu300/android/dev-properties/u:object_r:radio_prop:s0': ('f', 0o444),
            'opt/mu300/android/vendor': ('d', 0o755), 'opt/mu300/android/vendor/etc': ('d', 0o755),
            'opt/mu300/android/vendor/bin/modem_control': ('f', 0o755),
            'opt/mu300/android/vendor/bin/cp_diskserver': ('f', 0o755),
            'opt/mu300/android/vendor/etc/modem_cp_info.xml': ('f', 0o644),
            'opt/mu300/android/vendor/lib64/lib_crypto.so': ('f', 0o644),
            'opt/mu300/android/vendor/lib64/egl': ('d', 0o755),
            'opt/mu300/android/vendor/lib64/egl/libGLES_mali.so': ('f', 0o644),
            'opt/mu300/android/system/lib64/libc++.so': ('f', 0o644),
            'opt/mu300/android/system/bin/linker64': ('l', 0o777),
            'opt/mu300/android/apex/com.android.runtime/bin/linker64': ('f', 0o755),
            'opt/mu300/android/apex/com.android.runtime/lib64/bionic/libc.so': ('f', 0o644),
            'opt/mu300/android/linkerconfig': ('d', 0o755),
            'opt/mu300/android/linkerconfig/ld.config.txt': ('f', 0o644)}

    def check(self, got, fwdir):
        for name, want in dict(self.WANT, **{f'{fwdir}/wcnmodem.bin': ('f', 0o644)}).items():
            self.assertIn(name, got)
            self.assertEqual(got[name][:2], want, name)
        for name, m in got.items():
            self.assertEqual(m[2:], (0, 0, 'root', 'root'), name)
            if m[0] != 'l':
                # nothing only root can read or enter, nothing anyone else can write
                self.assertEqual(m[1] & 0o444, 0o444, name)
                self.assertEqual(m[1] & 0o7022, 0, name)
                if m[0] == 'd':
                    self.assertEqual(m[1], 0o755, name)

    def test_modes_of_a_subset_extracted_on_a_host_that_keeps_them(self):
        s, g, fw = self.subset()
        self.check(self.pack('ubuntu', s, g, fw), 'usr/lib/firmware')
        self.check(self.pack('openwrt-luci', s, g, fw), 'lib/firmware')

    def test_modes_of_a_subset_on_a_file_system_without_modes(self):
        # macOS on FAT/exFAT: every file and directory 0700 (measured: -rwx------ even for the empty
        # ld.config.txt, drwx------ for vendor/etc; the tarball stored exactly that, as root:root)
        s, g, fw = self.subset()
        for top in (s, g, fw):
            for p in sorted(top.rglob('*'), reverse=True):
                if not p.is_symlink():
                    p.chmod(0o700)
        self.check(self.pack('openwrt-luci', s, g, fw), 'lib/firmware')
        # and the other way a host can lie: everything readable, writable and executable by all (drvfs, ntfs-3g)
        for top in (s, g, fw):
            for p in sorted(top.rglob('*'), reverse=True):
                if not p.is_symlink():
                    p.chmod(0o777)
        self.check(self.pack('ubuntu', s, g, fw), 'usr/lib/firmware')

    def test_the_same_rule_as_the_device(self):
        # android-vendor-start's modes block sets these at boot; a tarball this tool packs must already be so,
        # or every first boot would report a repair
        text = (BIN / 'android-vendor-start').read_text()
        for rule in ("-type d ! -perm 755 ! -perm 711 -exec chmod 755", "-path '*/bin/*' ! -perm 755 -exec chmod 755",
                     '-path "$MA/dev-properties/*" ! -perm 444 -exec chmod 444',
                     "! -path '*/bin/*' ! -path \"$MA/dev-properties/*\" ! -perm 644 -exec chmod 644"):
            self.assertIn(rule, text)


if __name__ == '__main__':
    unittest.main()
