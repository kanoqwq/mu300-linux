"""The zip: customize.sh in a fake Magisk installer environment, tools/make-magisk-zips.sh and, on a release's real
zips (MU300_MAGISK_ZIPS), the allow-list, the manifest against the payload and a dry run of each zip's installer.
A zip is also an installer for a computer once unpacked (computer/, START-HERE.md, the launchers): what that adds, and
that the Magisk side of a zip installs exactly as it does without it."""
import hashlib
import io
import os
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from fakedevice import FakeDevice
from helpers import TOP, ShellTest, shells
from test_android_boot_image import MAGISKBOOT, cpio_all, fake_android_root, leaves, ramdisk_of
from test_boot_image import unlz4_legacy
from test_magisk_installer import MU300_FILES, generic_ramdisk, tar_gz

COMPUTER_DIR = TOP / 'android/magisk/computer'
LAUNCHERS = ('START-HERE.md', 'start.sh', 'start.cmd', 'start-gui.sh', 'start-gui.cmd', 'uninstall-linux.sh',
             'uninstall-linux.cmd')


def computer_files():
    """The project's files that go into a zip as computer/<path> (android/magisk/computer/files.txt, a directory
    standing for its files, never a cache of Python's or a hidden file), as make-magisk-zips.sh expands it."""
    out = set()
    for line in (COMPUTER_DIR / 'files.txt').read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        p = TOP / line
        if p.is_dir():
            for f in p.rglob('*'):
                rel = f.relative_to(TOP).as_posix()
                if f.is_file() and not f.name.startswith('.') and not f.name.endswith('.pyc') and '__pycache__' not in rel:
                    out.add(rel)
        else:
            out.add(line)
    return out


# what a zip holds for a computer, beside what it holds for Magisk
COMPUTER = set(LAUNCHERS) | {'computer/.mu300-package'} | {'computer/' + f for f in computer_files()}

MAGISK_ENV = r'''
ui_print() { echo "$1"; }
abort() { echo "ABORT $1"; rm -rf "$MODPATH"; exit 1; }
set_perm() { chmod "$4" "$1"; }
set_perm_recursive() { chmod -R u+rwX "$1"; }
BOOTMODE=true; MAGISK_VER_CODE=30700
'''

BUSYBOX = shutil.which('busybox')


@unittest.skipUnless(BUSYBOX, 'no busybox')
class Customize(ShellTest):
    def setUp(self):
        super().setUp()
        (self.tmp / 'magisk').mkdir()
        os.symlink(BUSYBOX, self.tmp / 'magisk' / 'busybox')

    def zip_with(self, install_sh, name='m.zip', body='#!/system/bin/sh\n'):
        z = self.tmp / name
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('mu300/install.sh', install_sh + '\n')
            f.writestr('module.prop', 'id=x\n')
            f.writestr('action.sh', body)
            f.writestr('switch.sh', body)
            f.writestr('bootchain.sh', body)
            f.writestr('system/bin/mu300-linux', body)
        return z

    def run_customize(self, shell, install_sh, bootmode='true'):
        z = self.zip_with(install_sh)
        mod = self.tmp / 'modpath'
        mod.mkdir(exist_ok=True)
        code = (MAGISK_ENV + f'BOOTMODE={bootmode}; ZIPFILE="{z}"; MODPATH="{mod}"; TMPDIR="{self.tmp}/t"; '
                f'MAGISKBIN="{self.tmp}/magisk"; mkdir -p "$TMPDIR"; set +e +u; '
                f'. "{TOP}/android/magisk/installer/customize.sh"; echo "after opts=$-"')
        return self.sh(shell, code), mod

    def test_success_installs_the_switch(self):
        for shell in self.each_shell():
            r, mod = self.run_customize(shell, 'echo installing; exit 0')
            self.assertIn('installing', r.stdout)
            self.assertTrue((mod / 'switch.sh').exists() and (mod / 'module.prop').exists())
            opts = r.stdout.split('after opts=')[1].split('\n')[0]   # dash prints it empty
            self.assertFalse(set('eu') & set(opts), opts)             # Magisk's options untouched

    def test_failure_and_dry_run_abort(self):
        for shell in self.each_shell():
            for rc, word in ((1, 'not installed'), (3, 'Dry run')):
                r, mod = self.run_customize(shell, f'exit {rc}')
                self.assertIn('ABORT', r.stdout)
                self.assertIn(word, r.stdout)
                self.assertFalse(mod.exists())

    def test_module_files_come_from_the_zip_as_it_was_checked(self):
        # magisk --install-module /sdcard/...: any app can replace the zip while the installation runs, and
        # system/* runs as root. The module's files are taken in the first unzip, with the installer.
        evil = self.zip_with('exit 0', name='evil.zip', body='#!/system/bin/sh\ntouch /pwned\n')
        for shell in self.each_shell():
            r, mod = self.run_customize(shell, f'cp "{evil}" "$ZIPFILE"; exit 0')
            self.assertNotIn('ABORT', r.stdout, r.stdout + r.stderr)
            for f in ('switch.sh', 'action.sh', 'bootchain.sh', 'system/bin/mu300-linux'):
                self.assertEqual((mod / f).read_text(), '#!/system/bin/sh\n', f)
            self.assertEqual(sorted(p.name for p in (self.tmp / 't').iterdir()), [])   # nothing left behind

    def test_a_conf_at_the_top_of_the_zip_reaches_the_installer(self):
        # zip mu300-magisk-....zip mu300-install.conf: the only way to tell an update about the password; a zip
        # without one installs as before
        show = 'echo "conf=[${MU300_ZIP_CONF:-}]"; [ -z "${MU300_ZIP_CONF:-}" ] || cat "$MU300_ZIP_CONF"; exit 0'
        for shell in self.each_shell():
            r, mod = self.run_customize(shell, show)
            self.assertIn('conf=[]', r.stdout, r.stdout + r.stderr)
            self.assertNotIn('ABORT', r.stdout)
            z = self.zip_with(show)
            with zipfile.ZipFile(z, 'a') as f:
                f.writestr('mu300-install.conf', 'MU300_KEEP_PASSWORD=yes\n')
            m = self.tmp / 'modpath2'
            m.mkdir(exist_ok=True)
            code = (MAGISK_ENV + f'BOOTMODE=true; ZIPFILE="{z}"; MODPATH="{m}"; TMPDIR="{self.tmp}/t"; '
                    f'MAGISKBIN="{self.tmp}/magisk"; mkdir -p "$TMPDIR"; set +e +u; '
                    f'. "{TOP}/android/magisk/installer/customize.sh"; echo "after"')
            r = self.sh(shell, code)
            self.assertIn(f'conf=[{self.tmp}/t/mu300-zip/mu300-install.conf]', r.stdout, r.stdout + r.stderr)
            self.assertIn('MU300_KEEP_PASSWORD=yes', r.stdout)
            self.assertNotIn('ABORT', r.stdout)
            self.assertEqual(sorted(p.name for p in (self.tmp / 't').iterdir()), [])   # nothing left behind

    def test_recovery_is_refused(self):
        for shell in self.each_shell():
            r, _ = self.run_customize(shell, 'echo ran; exit 0', bootmode='false')
            self.assertIn('ABORT', r.stdout)
            self.assertNotIn('ran', r.stdout)


MAGISK = {'META-INF/com/google/android/update-binary', 'META-INF/com/google/android/updater-script',
          'module.prop', 'customize.sh', 'action.sh', 'switch.sh', 'bootchain.sh', 'system/bin/mu300-linux',
          # the release's own checksums: what an update from the zip is checked against (mu300-update apply --from)
          'mu300/SHA256SUMS'} | set(MU300_FILES)
ALLOWED = MAGISK | COMPUTER
# a signed release's zips carry its signature too
SIGNATURE = 'mu300/SHA256SUMS.sig'
PWCHECK = 'mu300/mu300-pwcheck'       # the password checker, in the zips of releases whose kernel bundle has it
ASSETS = ('mu300-kernel.tar.gz', 'mu300-kernel-6.18.tar.gz', 'mu300-kernel-7.2.tar.gz', 'mu300-kernel-7.3.tar.gz',
          'mu300-openwrt-rootfs.tar.gz',
          'mu300-openwrt-luci-rootfs.tar.gz', 'mu300-ubuntu-rootfs.tar.gz', 'mu300-ubuntu-26.04-rootfs.tar.gz')


def tar_with(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for name, data in members.items():
            i = tarfile.TarInfo(name)
            i.size = len(data)
            t.addfile(i, io.BytesIO(data))
    return buf.getvalue()


def fake_release(rel, pwcheck=False):
    """REL laid out like a release's assets: tiny tarballs with the right names (the 5.4 one holds ./busybox, and with
    PWCHECK ./mu300-pwcheck, as the bundles from FINDINGS 126 on)"""
    rel.mkdir(parents=True)
    sums = []
    for a in ASSETS:
        k = {'./busybox': b'#!busybox\n', './x': a.encode()}
        if pwcheck:
            k['./mu300-pwcheck'] = b'\x7fELF pwcheck\n'
        data = tar_with(k if a == 'mu300-kernel.tar.gz' else {'./x': a.encode()})
        (rel / a).write_bytes(data)
        sums.append(f'{hashlib.sha256(data).hexdigest()}  {a}\n')
    upd = b'#!/bin/sh\n'
    (rel / 'mu300-update').write_bytes(upd)
    sums.append(f'{hashlib.sha256(upd).hexdigest()}  mu300-update\n')
    (rel / 'SHA256SUMS').write_text(''.join(sums))


class Builder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-zips-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rel = self.tmp / 'v2026.10.06'
        fake_release(self.rel)

    def build(self, *extra, rel=None):
        return subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(rel or self.rel), str(self.tmp / 'out'),
                               *extra], capture_output=True, text=True)

    def test_eleven_zips(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        names = sorted(p.name for p in (self.tmp / 'out').glob('*.zip'))
        # four systems on four kernels, less Ubuntu 26.04 on 5.4
        self.assertEqual(len(names), 15)
        self.assertNotIn('mu300-magisk-v2026.10.06-ubuntu-26.04-k5.4.zip', names)
        self.assertIn('mu300-magisk-v2026.10.06-openwrt-k6.18.zip', names)
        # OpenWrt with the control panel, on every kernel
        for k in ('5.4', '6.18', '7.2', '7.3'):
            self.assertIn(f'mu300-magisk-v2026.10.06-openwrt-luci-k{k}.zip', names)
        self.assertIn('mu300-magisk-v2026.10.06-ubuntu-26.04-k7.3.zip', names)
        sums = (self.tmp / 'out/SHA256SUMS-magisk').read_text().split('\n')
        self.assertEqual(len([s for s in sums if s]), 15)
        for line in sums:
            if line:
                h, n = line.split('  ')
                self.assertEqual(h, hashlib.sha256((self.tmp / 'out' / n).read_bytes()).hexdigest())

    def test_the_version_code_grows_with_the_release(self):
        # Magisk's versionCode: the date of a three-field tag, the date and N of a four-field one (FINDINGS 125)
        for tag, code in (('v2026.10.27', '20261027'), ('v2026.10.16.1', '2026101601'), ('v2026.10.16.10', '2026101610'),
                          ('v2026.10.23-fix1', '202610231')):
            shutil.rmtree(self.tmp / 'out', ignore_errors=True)
            r = self.build(tag)
            self.assertEqual(r.returncode, 0, r.stderr)
            z = zipfile.ZipFile(self.tmp / f'out/mu300-magisk-{tag}-openwrt-k5.4.zip')
            self.assertIn(f'versionCode={code}\n', z.read('module.prop').decode(), tag)
            self.assertIn(f'TAG={tag}\n', z.read('mu300/manifest').decode())

    def test_layout_manifest_and_stored_payload(self):
        self.build()
        z = zipfile.ZipFile(self.tmp / 'out/mu300-magisk-v2026.10.06-ubuntu-24.04-k7.2.zip')
        names = set(z.namelist())
        self.assertEqual(names - ALLOWED, {'payload/mu300-kernel-7.2.tar.gz', 'payload/mu300-ubuntu-rootfs.tar.gz'})
        self.assertEqual(ALLOWED - names, set())
        for i in z.infolist():
            if i.filename.startswith('payload/'):
                self.assertEqual(i.compress_type, zipfile.ZIP_STORED)
        m = dict(l.split('=', 1) for l in z.read('mu300/manifest').decode().split('\n') if '=' in l)
        self.assertEqual((m['OS'], m['UBUNTU'], m['KERNEL']), ('ubuntu', '24.04', '7.2'))
        self.assertEqual(m['SHA256_ROOTFS'], hashlib.sha256(z.read('payload/mu300-ubuntu-rootfs.tar.gz')).hexdigest())
        self.assertEqual(m['SHA256_KERNEL'], hashlib.sha256(z.read('payload/mu300-kernel-7.2.tar.gz')).hexdigest())
        self.assertIn('versionCode=20261006', z.read('module.prop').decode())
        self.assertNotIn('@', z.read('module.prop').decode())
        self.assertEqual(z.read('mu300/busybox'), b'#!busybox\n')
        self.assertEqual(z.read('mu300/SHA256SUMS'), (self.rel / 'SHA256SUMS').read_bytes())
        self.assertNotIn(SIGNATURE, names)

    def test_the_password_checker_of_the_bundle_goes_into_every_zip(self):
        # tools/pwcheck: in the 5.4 bundle of the releases that have it, and then in every zip; older bundles: none
        self.build()
        self.assertNotIn(PWCHECK, zipfile.ZipFile(self.tmp / 'out/mu300-magisk-v2026.10.06-openwrt-k6.18.zip').namelist())
        rel = self.tmp / 'v2026.10.07'
        fake_release(rel, pwcheck=True)
        shutil.rmtree(self.tmp / 'out', ignore_errors=True)
        r = self.build(rel=rel)
        self.assertEqual(r.returncode, 0, r.stderr)
        zips = sorted((self.tmp / 'out').glob('*.zip'))
        self.assertEqual(len(zips), 15)
        for zp in zips:
            self.assertEqual(zipfile.ZipFile(zp).read(PWCHECK), b'\x7fELF pwcheck\n', zp.name)

    def test_a_signed_release_puts_its_signature_into_every_zip(self):
        (self.rel / 'SHA256SUMS.sig').write_text('untrusted comment: signed by key 0102030405060708\nRWQx\n')
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        zips = sorted((self.tmp / 'out').glob('*.zip'))
        self.assertEqual(len(zips), 15)
        for zp in zips:
            z = zipfile.ZipFile(zp)
            self.assertEqual(z.read(SIGNATURE), (self.rel / 'SHA256SUMS.sig').read_bytes(), zp.name)
            self.assertEqual(z.read('mu300/SHA256SUMS'), (self.rel / 'SHA256SUMS').read_bytes())
        for entry, src in MU300_FILES.items():     # the helper that stages the installer for its tests agrees
            if src:
                self.assertEqual(z.read(entry), (TOP / src).read_bytes(), entry)

    def test_every_manifest_is_accepted_by_the_installer(self):
        self.build()
        for zp in sorted((self.tmp / 'out').glob('*.zip')):
            d = self.tmp / 'unz' / zp.stem
            with zipfile.ZipFile(zp) as z:
                z.extractall(d, [n for n in z.namelist() if n.startswith('mu300/')])
            for shell in shells():
                r = subprocess.run(shell + ['-c', f'MU300_LIB=1 MU300_DIR="{d}/mu300" . "{d}/mu300/install.sh"; '
                                          f'manifest_load "{d}/mu300/manifest" && echo "ok $TAG $SYSTEM $KERNEL"'],
                                   capture_output=True, text=True)
                self.assertIn('ok v2026.10.06', r.stdout, f'{zp.name} {shell}: {r.stdout}{r.stderr}')

    def test_checksum_mismatch_stops(self):
        with open(self.rel / 'mu300-openwrt-rootfs.tar.gz', 'ab') as f:
            f.write(b'x')
        r = self.build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('mu300-openwrt-rootfs.tar.gz', r.stderr)
        self.assertEqual(list((self.tmp / 'out').glob('*.zip')), [])

    def test_a_tag_the_installer_would_refuse_stops_before_anything_is_built(self):
        for tag in ('2026.10.06', 'v', 'vx1', 'v1..2', 'v1/2', 'v1;x', 'v1 2', 'v1&2'):
            r = self.build(tag)
            self.assertEqual(r.returncode, 1, tag)
            self.assertIn('v[0-9]', r.stderr, tag)
            self.assertEqual(list((self.tmp / 'out').glob('*')), [], tag)
        bad = self.tmp / 'release-2026'                  # the default tag is the directory's name
        shutil.copytree(self.rel, bad)
        self.assertEqual(self.build(rel=bad).returncode, 1)
        self.assertEqual(list((self.tmp / 'out').glob('*')), [])
        self.assertEqual(self.build('v2026.10.06-rc1').returncode, 0)

    def test_no_conf_in_a_zip(self):
        self.assertEqual(self.build().returncode, 0)
        for zp in (self.tmp / 'out').glob('*.zip'):
            self.assertFalse([n for n in zipfile.ZipFile(zp).namelist() if n.endswith('mu300-install.conf')])



class ComputerSide(unittest.TestCase):
    """Unpacked on a computer a zip is an installer: computer/ is the project's installers, START-HERE.md and the
    launchers are at the top, and computer/.mu300-package tells install.sh that the payload is next to it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='mu300-zips-computer-'))
        fake_release(cls.tmp / 'v2026.10.06')
        r = subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(cls.tmp / 'v2026.10.06'), str(cls.tmp / 'out')],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.zip = zipfile.ZipFile(cls.tmp / 'out/mu300-magisk-v2026.10.06-ubuntu-26.04-k7.2.zip')

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_installers_of_this_checkout(self):
        names = set(self.zip.namelist())
        self.assertEqual(COMPUTER - names, set())
        for f in computer_files():
            data = self.zip.read('computer/' + f)
            want = (TOP / f).read_bytes()
            if f.endswith('.cmd'):           # for cmd.exe: CRLF, whatever the checkout has
                want = want.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            self.assertEqual(data, want, f)
        # the essentials, by name: a list that lost one would not be noticed until a computer ran it
        for f in ('install.sh', 'install.ps1', 'uninstall.sh', 'uninstall.ps1', 'tools/package.sh', 'tools/answers.sh',
                  'boot/build-boot-image.py', 'boot/init', 'gui/mu300-gui.py', 'gui/mu300gui/package.py',
                  'android/magisk/mu300-linux-switch/switch.sh', 'i18n/tr.tsv'):
            self.assertIn('computer/' + f, names)
        self.assertFalse([n for n in names if '__pycache__' in n or n.endswith('.pyc')])

    def test_the_payload_is_there_once(self):
        # the computer's installers use payload/ where it is: no second copy of a system or a kernel
        tars = [n for n in self.zip.namelist() if n.endswith('.tar.gz')]
        self.assertEqual(sorted(tars), ['payload/mu300-kernel-7.2.tar.gz', 'payload/mu300-ubuntu-26.04-rootfs.tar.gz'])

    def test_start_here_names_this_zip(self):
        text = self.zip.read('START-HERE.md').decode()
        self.assertIn('ubuntu-26.04', text)
        self.assertIn('7.2', text)
        self.assertIn('v2026.10.06', text)
        self.assertNotRegex(text, r'@[A-Z]+@')
        for word in ('Türkçe', '中文', 'start.cmd', 'start-gui.sh', 'platform-tools', 'magisk --install-module'):
            self.assertIn(word, text)

    def test_launchers(self):
        for name in LAUNCHERS:
            info = self.zip.getinfo(name)
            data = self.zip.read(name)
            if name.endswith('.cmd'):
                self.assertNotIn(b'\n', data.replace(b'\r\n', b''), name)
                self.assertTrue(data.startswith(b'@echo off\r\n'), name)
            if name.endswith('.sh'):
                self.assertEqual(stat.S_IMODE(info.external_attr >> 16) & 0o111, 0o111, name)
                self.assertNotIn(b'\r', data, name)
        for name in ('start.sh', 'start-gui.sh', 'start.cmd', 'start-gui.cmd'):
            self.assertIn(b'MU300_PACKAGE', self.zip.read(name), name)
        for name in ('start-gui.sh', 'start-gui.cmd'):
            self.assertIn(b'MU300_GUI=1', self.zip.read(name), name)
        self.assertEqual(stat.S_IMODE(self.zip.getinfo('computer/install.sh').external_attr >> 16) & 0o111, 0o111)
        self.assertIn('computer/.mu300-package', self.zip.namelist())

    def test_no_platform_tools(self):
        # Google's SDK licence (3.4) does not allow passing them on: START-HERE.md says where to get them
        self.assertFalse([n for n in self.zip.namelist() if re.search(r'(^|/)(adb|adb\.exe|AdbWinApi\.dll|fastboot)$', n)])

    def test_the_magisk_side_unpacks_none_of_it(self):
        # customize.sh unpacks named entries only: a file of the computer's side never reaches the device's module
        src = (TOP / 'android/magisk/installer/customize.sh').read_text()
        m = re.search(r"unzip -o -q \"\$ZIPFILE\" (.+?) -d", src)
        self.assertEqual(m.group(1), "'mu300/*' module.prop action.sh switch.sh bootchain.sh 'system/*'")
        self.assertIn('SKIPUNZIP=1', src)
        for n in COMPUTER:
            self.assertFalse(n.startswith(('mu300/', 'system/')) or n in MAGISK, n)


class ComputerFilesList(unittest.TestCase):
    """android/magisk/computer/files.txt holds every file the computer's installers name ($TOP/... in the shell
    scripts, $Top\\... and Join-Path $Top in the PowerShell ones), except what only --build or the release uses."""
    BUILD_ONLY = ('out', 'work', 'release/', 'kernel', 'openwrt/', 'rootfs', 'upstream/', 'tools/adbd/', 'tools/gpu',
                  'tools/make-extra.sh', 'tools/cockpit-i18n.py', 'tools/sign-release.sh', 'platform-tools', '.git',
                  '.mu300-keep', '.mu300-source', 'tools/', 'tools', 'i18n/', 'android/magisk/installer',
                  'rootfs/overlay/opt/mu300/bin/mu300-update', 'rootfs/overlay/opt/mu300/lib/release.pub',
                  'rootfs/panel/', 'gui', 'tools/android-boot-image.sh')

    def test_every_file_named_is_listed(self):
        listed = computer_files()
        listed_dirs = {f.rsplit('/', 1)[0] for f in listed}
        scripts = [f for f in sorted(listed) if f.endswith(('.sh', '.ps1')) and not f.startswith('android/magisk/mu300-linux-switch')]
        missing = []
        for f in scripts:
            text = (TOP / f).read_text(encoding='utf-8')
            refs = re.findall(r'\$TOP/([A-Za-z0-9_./-]+)', text)
            refs += [r.replace('\\', '/') for r in re.findall(r'\$Top\\([A-Za-z0-9_.\\-]+)', text)]
            refs += [r.replace('\\', '/') for r in re.findall(r"Join-Path \$Top '([A-Za-z0-9_.\\-]+)", text)]
            for ref in refs:
                ref = ref.rstrip('/.')
                if not ref or ref in self.BUILD_ONLY or any(ref.startswith(b) for b in self.BUILD_ONLY if b.endswith('/')):
                    continue
                if not (TOP / ref).exists():
                    continue                  # a name put together at run time (openwrt/mu300-$os...)
                if ref not in listed and ref not in listed_dirs:
                    missing.append(f'{f}: {ref}')
        self.assertEqual(missing, [])


def full_fake_release(rel):
    """REL like a release, with kernel bundles and systems the zip's own installer can install on the fake device of
    tests/fakedevice.py (test_magisk_installer.InstallerCase.zip makes them the same way)"""
    rel.mkdir(parents=True)
    sums = []
    for a in ASSETS:
        if a.startswith('mu300-kernel'):
            krel = '5.4.254-mu300' if a == 'mu300-kernel.tar.gz' else a[len('mu300-kernel-'):-len('.tar.gz')] + '.0-mu300'
            files = {'Image': b'\x7fkernel' * 1000, 'ramdisk-generic.lz4': generic_ramdisk(),
                     'modules/mu300-test.ko': b'\x7fELF test module', 'kernel.release': (krel + '\n').encode(),
                     'devices': b'f50 u30air\n', 'features': b'sdcard\n'}
            if a == 'mu300-kernel.tar.gz':
                files['busybox'] = b'#!busybox\n'
            data = tar_gz(files)
        else:
            os_ = 'ubuntu' if 'ubuntu' in a else ('openwrt-luci' if 'luci' in a else 'openwrt')
            data = tar_gz({'etc/os-release': f'ID={os_}\n'.encode()})
        (rel / a).write_bytes(data)
        sums.append(f'{hashlib.sha256(data).hexdigest()}  {a}\n')
    (rel / 'mu300-update').write_bytes(b'#!/bin/sh\n')
    sums.append(f'{hashlib.sha256(b"#!/bin/sh" + chr(10).encode()).hexdigest()}  mu300-update\n')
    (rel / 'SHA256SUMS').write_text(''.join(sums))


@unittest.skipUnless(BUSYBOX, 'no busybox')
class MagiskPathUnchanged(ShellTest):
    """The Magisk side of a zip built with its computer side installs exactly as the same zip without it (the layout
    of the zips before computer/ was added): customize.sh, in a fake Magisk installer environment, runs the zip's own
    installer on the fake device - a dry run and a whole installation - and both zips give the same output, the
    same boot partitions, misc and module."""

    @classmethod
    def setUpClass(cls):
        cls.rel_tmp = Path(tempfile.mkdtemp(prefix='mu300-zips-magisk-'))
        full_fake_release(cls.rel_tmp / 'v2026.10.06')
        r = subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(cls.rel_tmp / 'v2026.10.06'), str(cls.rel_tmp / 'out')],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.rel_tmp, ignore_errors=True)

    def without_computer_side(self, src, dst):
        with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, 'w') as zo:
            for i in zi.infolist():
                if i.filename not in COMPUTER:
                    zo.writestr(i, zi.read(i.filename))
        return dst

    def run_zip(self, zp, conf):
        shutil.rmtree(self.tmp / 'fake', ignore_errors=True)
        fake = FakeDevice(self.tmp, self.stubs)
        fake_android_root(fake.root / 'android')
        (fake.root / 'magisk/busybox').symlink_to(BUSYBOX)
        if conf:
            (fake.root / 'sdcard/mu300-install.conf').write_text(conf)
        mod = self.tmp / 'modpath'
        shutil.rmtree(mod, ignore_errors=True)
        shutil.rmtree(self.tmp / 't', ignore_errors=True)
        (self.tmp / 't').mkdir()
        env = {k: str(v) for k, v in fake.env(self.tmp / 'unused', self.stubs / 'magiskboot').items()}
        code = (MAGISK_ENV + f'ZIPFILE="{zp}"; MODPATH="{mod}"; TMPDIR="{self.tmp}/t"; set +e +u; '
                f'. "{TOP}/android/magisk/installer/customize.sh"; echo "customize returned"')
        r = subprocess.run([BUSYBOX, 'sh', '-c', code], capture_output=True, text=True, env=self.env(**env), timeout=300)
        by = fake.root / 'dev/block/by-name'
        state = {n: hashlib.sha256((by / n).read_bytes()).hexdigest() for n in ('boot_a', 'misc')}
        # boot_b: what it holds, not its bytes - the device segment's cpio carries the times its files were made
        img = (by / 'boot_b').read_bytes()
        if img[44:54] == b'loglevel=5':
            ksz = struct.unpack_from('<I', img, 8)[0]
            state['boot_b'] = (img[44:44 + 1536], hashlib.sha256(img[4096:4096 + ksz]).hexdigest(),
                               {n: hashlib.sha256(d).hexdigest() + str(x) for n, (k, x, d) in
                                leaves(cpio_all(unlz4_legacy(ramdisk_of(img)))).items()})
        else:
            state['boot_b'] = hashlib.sha256(img).hexdigest()
        state['module'] = sorted(str(p.relative_to(mod)) for p in mod.rglob('*')) if mod.exists() else None
        out = re.sub(r'mu300-magisk\.\w+', 'mu300-magisk.X', r.stdout)
        out = re.sub(r'\$6\$[^$\s]+\$\S+', '$6$salt$hash', out)
        return r, out, state

    def test_dry_run_and_install_as_before(self):
        self.stub('magiskboot', MAGISKBOOT)
        for name in ('mu300-magisk-v2026.10.06-openwrt-k6.18.zip', 'mu300-magisk-v2026.10.06-ubuntu-24.04-k5.4.zip'):
            now = self.rel_tmp / 'out' / name
            before = self.without_computer_side(now, self.tmp / ('before-' + name))
            self.assertTrue(set(zipfile.ZipFile(now).namelist()) - set(zipfile.ZipFile(before).namelist()))
            for conf, rc_word in (('MU300_DRY_RUN=1\n', 'Dry run'), ('', None)):
                r1, out1, st1 = self.run_zip(before, conf)
                r2, out2, st2 = self.run_zip(now, conf)
                if rc_word:
                    self.assertIn(rc_word, out1)
                    self.assertIsNone(st1['module'])
                else:
                    self.assertNotIn('ABORT', out1, out1 + r1.stderr)
                    self.assertIn('switch.sh', st1['module'])
                    self.assertIsInstance(st1['boot_b'], tuple)              # a Linux boot image was written
                    self.assertIn('etc/mu300-device', st1['boot_b'][2])
                self.assertEqual(out2, out1, name)
                self.assertEqual(st2, st1, name)


class Workflow(unittest.TestCase):
    def test_package_lists_are_fresh_before_the_tools_are_installed(self):
        # a runner image's lists go stale; apt-get install then asks for package versions the mirror dropped
        steps = (TOP / '.github/workflows/magisk.yml').read_text()
        self.assertIn('sudo apt-get update -qq', steps)
        self.assertLess(steps.index('apt-get update'), steps.index('apt-get install'))


BUSYBOX_PATH = shutil.which('busybox')


@unittest.skipUnless(os.environ.get('MU300_MAGISK_ZIPS'), 'MU300_MAGISK_ZIPS not set (the Magisk workflow sets it)')
class ReleaseZips(ShellTest):
    """the real zips of a release: the allow-list, the manifest against the payload, and a dry run of each zip's own
    installer on the fake device that must print the plan and write nothing"""

    def zips(self):
        zs = sorted(Path(os.environ['MU300_MAGISK_ZIPS']).glob('*.zip'))
        self.assertEqual(len(zs), 15, zs)          # 4 OpenWrt, 4 OpenWrt with the panel, 4 + 3 Ubuntu
        return zs

    def test_allow_list_and_manifest(self):
        for zp in self.zips():
            with zipfile.ZipFile(zp) as z:
                names = set(z.namelist())
                m = dict(l.split('=', 1) for l in z.read('mu300/manifest').decode().split('\n') if '=' in l)
                payload = {f'payload/{m["KERNEL_ASSET"]}', f'payload/{m["ROOTFS_ASSET"]}'}
                self.assertEqual(names - {SIGNATURE, PWCHECK}, ALLOWED | payload, zp.name)
                for n, key in ((m['KERNEL_ASSET'], 'SHA256_KERNEL'), (m['ROOTFS_ASSET'], 'SHA256_ROOTFS')):
                    h = hashlib.sha256()
                    with z.open(f'payload/{n}') as f:
                        for chunk in iter(lambda: f.read(1 << 20), b''):
                            h.update(chunk)
                    self.assertEqual(h.hexdigest(), m[key], f'{zp.name} {n}')
                self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()
                                    if i.filename.startswith('payload/')), zp.name)

    @unittest.skipUnless(BUSYBOX_PATH, 'no busybox')
    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        self.stub('magiskboot', MAGISKBOOT)
        for zp in self.zips():
            shutil.rmtree(self.tmp / 'fake', ignore_errors=True)
            unz = self.tmp / 'unz'
            shutil.rmtree(unz, ignore_errors=True)
            with zipfile.ZipFile(zp) as z:
                z.extractall(unz, [n for n in z.namelist() if n.startswith('mu300/')])
            fake = FakeDevice(self.tmp, self.stubs)
            fake_android_root(fake.root / 'android')
            (fake.root / 'magisk/busybox').symlink_to(BUSYBOX_PATH)
            (fake.root / 'sdcard/mu300-install.conf').write_text('MU300_DRY_RUN=1\n')
            env = fake.env(unz / 'mu300', self.stubs / 'magiskboot')
            env['ZIPFILE'] = str(zp)
            before = {n: hashlib.sha256((fake.root / 'dev/block/by-name' / n).read_bytes()).hexdigest()
                      for n in ('boot_a', 'boot_b', 'misc')}
            r = subprocess.run([BUSYBOX_PATH, 'sh', str(unz / 'mu300/install.sh')], capture_output=True, text=True,
                               env=self.env(**env), timeout=600)
            self.assertEqual(r.returncode, 3, f'{zp.name}\n{r.stdout}{r.stderr}')
            self.assertIn('Plan', r.stdout, zp.name)
            after = {n: hashlib.sha256((fake.root / 'dev/block/by-name' / n).read_bytes()).hexdigest() for n in before}
            self.assertEqual(before, after, zp.name)
            self.assertEqual(list((fake.root / 'data/adb').iterdir()), [], zp.name)


if __name__ == '__main__':
    unittest.main()
