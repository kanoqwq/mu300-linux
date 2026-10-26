"""A release's Magisk zip unpacked on a computer (tools/package.sh, android/magisk/computer/START-HERE.md): install.sh
installs that zip's system and kernel from its own payload and downloads nothing. The manifest is read as the zip's
own installer reads it, the payload is checked against it and against the release's SHA256SUMS, and an install from a
real unpacked zip (tools/make-magisk-zips.sh) asks no question about the system, the kernel or the release - against
the fake adb of tests/fakeadb.py, as far as the first pull. The graphical installer's side: package.py, and the
choices and answers of a package."""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import TOP, ShellTest
from test_answers import Device, events
from test_magisk_zip import fake_release

sys.path.insert(0, str(TOP / 'gui'))
from mu300gui import model, package  # noqa: E402

KB = b'kernel bundle'
ROOTFS = b'openwrt system'


def manifest(**kw):
    m = dict(TAG='v2026.10.27', SYSTEM='openwrt', OS='openwrt', UBUNTU='', KERNEL='6.18',
             KERNEL_ASSET='mu300-kernel-6.18.tar.gz', ROOTFS_ASSET='mu300-openwrt-rootfs.tar.gz',
             SHA256_KERNEL=hashlib.sha256(KB).hexdigest(), SHA256_ROOTFS=hashlib.sha256(ROOTFS).hexdigest())
    m.update(kw)
    return ''.join(f'{k}={v}\n' for k, v in m.items())


def make_package(d, text=None, sums=None, kernel=KB, rootfs=ROOTFS):
    """D laid out like an unpacked zip: mu300/manifest, mu300/SHA256SUMS, payload/"""
    (d / 'mu300').mkdir(parents=True, exist_ok=True)
    (d / 'payload').mkdir(exist_ok=True)
    (d / 'mu300/manifest').write_text(manifest() if text is None else text)
    if sums is None:
        sums = (f'{hashlib.sha256(KB).hexdigest()}  mu300-kernel-6.18.tar.gz\n'
                f'{hashlib.sha256(ROOTFS).hexdigest()}  mu300-openwrt-rootfs.tar.gz\n')
    (d / 'mu300/SHA256SUMS').write_text(sums)
    if kernel is not None:
        (d / 'payload/mu300-kernel-6.18.tar.gz').write_bytes(kernel)
    if rootfs is not None:
        (d / 'payload/mu300-openwrt-rootfs.tar.gz').write_bytes(rootfs)
    return d


class Library(ShellTest):
    PRE = (f'TOP="{TOP}"; t() {{ printf "%s" "$1"; shift; for a; do printf " [%s]" "$a"; done; }}; '
           'die() { echo "DIE: $*"; exit 1; }; progress() { echo "EVENT $*"; }; . "$TOP/tools/package.sh"; ')

    def lib(self, shell, code, **env):
        return self.sh(shell, self.PRE + code, **env)

    def test_where_the_package_is(self):
        top = self.tmp / 'pkg' / 'computer'
        top.mkdir(parents=True)
        for shell in self.each_shell():
            r = self.sh(shell, f'TOP="{top}"; . "{TOP}/tools/package.sh"; echo "[$(package_dir)]"')
            self.assertIn('[]', r.stdout)                                    # a copy of the project
            (top / '.mu300-package').write_text('x\n')
            r = self.sh(shell, f'TOP="{top}"; . "{TOP}/tools/package.sh"; echo "[$(package_dir)]"')
            self.assertIn(f'[{top}/..]', r.stdout)                           # an unpacked zip: the directory above
            r = self.sh(shell, f'TOP="{top}"; . "{TOP}/tools/package.sh"; echo "[$(package_dir)]"', MU300_PACKAGE='/else')
            self.assertIn('[/else]', r.stdout)                               # MU300_PACKAGE names another
            (top / '.mu300-package').unlink()

    def test_a_valid_manifest(self):
        d = make_package(self.tmp / 'p')
        for shell in self.each_shell():
            r = self.lib(shell, f'package_load "{d}/mu300/.."; echo "$PKG|$PKG_TAG|$PKG_SYSTEM|$PKG_OS|$PKG_UBUNTU|$PKG_KERNEL|$PKG_KERNEL_ASSET|$PKG_ROOTFS_ASSET"')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f'{d}|v2026.10.27|openwrt|openwrt||6.18|mu300-kernel-6.18.tar.gz|mu300-openwrt-rootfs.tar.gz', r.stdout)
        # CRLF (a zip unpacked and edited on Windows) reads the same
        (d / 'mu300/manifest').write_text(manifest().replace('\n', '\r\n'))
        for shell in self.each_shell():
            r = self.lib(shell, f'package_load "{d}"; echo "[$PKG_KERNEL][$PKG_SHA256_ROOTFS]"')
            self.assertIn(f'[6.18][{hashlib.sha256(ROOTFS).hexdigest()}]', r.stdout)

    def test_every_value_must_be_one_a_release_has(self):
        bad = [dict(TAG='2026.10.27'), dict(TAG='v1/../x'), dict(TAG='v1..2'), dict(TAG='v1;rm'),
               dict(SYSTEM='ubuntu'), dict(OS='ubuntu'), dict(ROOTFS_ASSET='../../etc/passwd'),
               dict(ROOTFS_ASSET='mu300-ubuntu-rootfs.tar.gz'), dict(KERNEL='6.19'), dict(KERNEL_ASSET='mu300-kernel.tar.gz'),
               dict(SHA256_KERNEL='x' * 64), dict(SHA256_ROOTFS='ab'),
               dict(SYSTEM='ubuntu-26.04', OS='ubuntu', UBUNTU='26.04', ROOTFS_ASSET='mu300-ubuntu-26.04-rootfs.tar.gz',
                    KERNEL='5.4', KERNEL_ASSET='mu300-kernel.tar.gz')]
        for kw in bad:
            d = make_package(self.tmp / 'bad', text=manifest(**kw))
            for shell in self.each_shell():
                r = self.lib(shell, f'package_load "{d}"; echo LOADED')
                self.assertEqual(r.returncode, 1, kw)
                self.assertIn('DIE: the package is incomplete or damaged', r.stdout, kw)
                self.assertNotIn('LOADED', r.stdout)
        # a manifest is read, never run
        d = make_package(self.tmp / 'run', text=manifest() + '$(touch pwned)=1\nTAG=v1`touch pwned`\n')
        for shell in self.each_shell():
            r = self.lib(shell, f'cd "{self.tmp}"; package_load "{d}"; echo LOADED')
            self.assertEqual(r.returncode, 1)
            self.assertFalse((self.tmp / 'pwned').exists())

    def test_not_a_package(self):
        for shell in self.each_shell():
            r = self.lib(shell, f'package_load "{self.tmp}/nothing"')
            self.assertIn('DIE: no unpacked Magisk zip at', r.stdout)
            r = self.lib(shell, f'package_load "{self.tmp}"')
            self.assertIn('is not an unpacked Magisk zip of a release', r.stdout)

    def test_the_payload_is_checked_against_both(self):
        good = make_package(self.tmp / 'good')
        for shell in self.each_shell():
            r = self.lib(shell, f'package_load "{good}"; package_verify; echo VERIFIED')
            self.assertIn('VERIFIED', r.stdout, r.stdout + r.stderr)
            self.assertIn('EVENT verify file mu300-kernel-6.18.tar.gz result ok', r.stdout)
            self.assertIn('EVENT verify file mu300-openwrt-rootfs.tar.gz result ok', r.stdout)
        cases = {
            'a payload file that is not what the manifest says': dict(rootfs=b'another system'),
            'SHA256SUMS that does not name it': dict(sums=f'{hashlib.sha256(KB).hexdigest()}  mu300-kernel-6.18.tar.gz\n'),
            'SHA256SUMS that says otherwise': dict(sums=f'{hashlib.sha256(KB).hexdigest()}  mu300-kernel-6.18.tar.gz\n'
                                                        f'{"0" * 64}  mu300-openwrt-rootfs.tar.gz\n'),
        }
        for why, kw in cases.items():
            d = make_package(self.tmp / why.replace(' ', '-'), **kw)
            for shell in self.each_shell():
                r = self.lib(shell, f'package_load "{d}"; package_verify; echo VERIFIED')
                self.assertEqual(r.returncode, 1, why)
                self.assertIn('EVENT verify file mu300-openwrt-rootfs.tar.gz result mismatch', r.stdout, why)
                self.assertIn('DIE: checksum mismatch for {1} [mu300-openwrt-rootfs.tar.gz]', r.stdout, why)
        d = make_package(self.tmp / 'missing', kernel=None)
        for shell in self.each_shell():
            r = self.lib(shell, f'package_load "{d}"; package_verify; echo VERIFIED')
            self.assertIn('DIE: {1} is missing from the package (payload/) [mu300-kernel-6.18.tar.gz]', r.stdout)


class FromAnUnpackedZip(Device):
    """computer/install.sh of a real zip (tools/make-magisk-zips.sh, the fake release of test_magisk_zip), unpacked,
    in answers mode against the fake adb: it finds the payload next to it by itself."""

    @classmethod
    def setUpClass(cls):
        cls.zips = Path(tempfile.mkdtemp(prefix='mu300-package-'))
        fake_release(cls.zips / 'v2026.10.06')
        r = subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(cls.zips / 'v2026.10.06'), str(cls.zips / 'out')],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.zips, ignore_errors=True)

    def unpack(self, name):
        d = self.tmp / 'unpacked' / name
        shutil.rmtree(d, ignore_errors=True)
        with zipfile.ZipFile(self.zips / 'out' / f'mu300-magisk-v2026.10.06-{name}.zip') as z:
            z.extractall(d)
        return d

    def install(self, shell, pkg, answers, **env):
        f = self.tmp / 'answers.txt'
        f.write_text(''.join(f'{k}={v}\n' for k, v in answers.items()))
        f.chmod(0o600)
        e = dict(MU300_PROGRESS='1')
        e.update(env)
        r = self.run_script(shell, str(pkg / 'computer/install.sh'), '--answers', str(f), **e)
        ev = events(r.stdout)
        asked = [e['id'] for e in ev if e['event'] == 'question' and 'state' not in e]
        return r, ev, asked

    ANSWERS = dict(MU300_ANS_DEFAULT_LINUX='yes', MU300_ANS_BOOT_ATTEMPTS='5', MU300_ANS_HOTSPOT='yes', MU300_ANS_GPU='no',
                   MU300_ANS_VPN='no', MU300_ANS_ADB='no', MU300_ANS_PASSWORD='hunter2secret', MU300_ANS_CONFIRM='INSTALL')

    def test_the_package_answers_the_system_the_kernel_and_the_release(self):
        pkg = self.unpack('ubuntu-26.04-k7.2')
        for shell in self.each_shell():
            self.device(pull=False)
            # no MU300_RELEASE: a package is its own release, and nothing asks GitHub (no curl here works)
            r, ev, asked = self.install(shell, pkg, self.ANSWERS, MU300_RELEASE='')
            self.assertEqual(asked, ['MU300_ANS_DEFAULT_LINUX', 'MU300_ANS_BOOT_ATTEMPTS', 'MU300_ANS_HOTSPOT', 'MU300_ANS_GPU',
                                     'MU300_ANS_VPN', 'MU300_ANS_ADB', 'MU300_ANS_PASSWORD'], r.stdout + r.stderr)
            facts = {e['key']: e['value'] for e in ev if e['event'] == 'info'}
            self.assertEqual(facts['package'], str(pkg))
            self.assertEqual((facts['package.system'], facts['package.kernel'], facts['package.release'], facts['release']),
                             ('ubuntu-26.04', '7.2', 'v2026.10.06', 'v2026.10.06'))
            self.assertIn('This package installs ubuntu-26.04 with kernel 7.2 (release v2026.10.06); nothing is downloaded.', r.stdout)
            self.assertIn('It is not part of this package.', r.stdout)            # the web panel
            self.assertNotIn('Updating the installer', r.stdout)                  # no self-update
            self.assertIn('could not read /dev/block/by-name/boot_a from the device', ev[-1]['message'])
            self.assert_nothing_written()
            self.assertEqual([c['argv'] for c in self.calls() if 'MU300_ANS_PASSWORD' in c['answers']], [])
            self.assertNotIn('hunter2secret', r.stdout + r.stderr)

    def test_another_package_another_system(self):
        pkg = self.unpack('openwrt-luci-k5.4')
        for shell in self.each_shell():
            self.device(pull=False)
            r, ev, asked = self.install(shell, pkg, self.ANSWERS)
            self.assertNotIn('MU300_ANS_SYSTEMS', asked)
            self.assertNotIn('MU300_OPENWRT', asked)
            self.assertNotIn('MU300_ANS_KERNEL', asked)
            self.assertIn('This package installs openwrt-luci with kernel 5.4', r.stdout)
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])

    def test_the_v50_and_a_54_package(self):
        pkg = self.unpack('openwrt-k5.4')
        for shell in self.each_shell():
            self.device(pull=False, model='V50', device='MU3351', platform='ums9620')
            r, ev, asked = self.install(shell, pkg, self.ANSWERS)
            self.assertEqual(ev[-1]['event'], 'error')
            self.assertIn('take the zip for kernel 6.18, 7.2 or 7.3 of this release', ev[-1]['message'])
            self.assertEqual(self.calls('pull'), [])
            self.assert_nothing_written()

    def test_a_damaged_manifest_stops_before_the_device(self):
        pkg = self.unpack('openwrt-k6.18')
        (pkg / 'mu300/manifest').write_text((pkg / 'mu300/manifest').read_text().replace('KERNEL=6.18', 'KERNEL=9.9'))
        for shell in self.each_shell():
            self.device(pull=False)
            r, ev, asked = self.install(shell, pkg, self.ANSWERS)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'the package is incomplete or damaged (its manifest is not valid)'})
            self.assertEqual(self.calls(), [])                 # not even adb devices

    def test_build_is_refused(self):
        pkg = self.unpack('openwrt-k6.18')
        for shell in self.each_shell():
            self.device(pull=False)
            r = self.run_script(shell, str(pkg / 'computer/install.sh'), '--build', MU300_NONINTERACTIVE='1', MU300_PROGRESS='1')
            self.assertIn('--build builds the systems itself', events(r.stdout)[-1]['message'])

    def test_the_launchers_find_it_too(self):
        # start.sh sets MU300_PACKAGE to its own folder (and puts platform-tools/ on the PATH): the same package
        pkg = self.unpack('openwrt-k6.18')
        (pkg / 'computer/.mu300-package').unlink()             # only the launcher says where it is now
        for shell in self.each_shell():
            self.device(pull=False)
            f = self.tmp / 'answers.txt'
            f.write_text(''.join(f'{k}={v}\n' for k, v in self.ANSWERS.items()))
            f.chmod(0o600)
            r = self.run_script(shell, str(pkg / 'start.sh'), '--answers', str(f), MU300_PROGRESS='1')
            facts = {e['key']: e['value'] for e in events(r.stdout) if e['event'] == 'info'}
            self.assertEqual(facts.get('package'), str(pkg), r.stdout + r.stderr)


class Window(unittest.TestCase):
    """The graphical installer's side: the package it was started from, the choices it makes and the answers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-package-gui-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_found_by_the_mark_or_named(self):
        top = self.tmp / 'pkg' / 'computer'
        top.mkdir(parents=True)
        self.assertIsNone(package.load(str(top), {}))
        make_package(self.tmp / 'pkg')
        (top / '.mu300-package').write_text('x\n')
        pkg = package.load(str(top), {})
        self.assertEqual((pkg['dir'], pkg['tag'], pkg['system'], pkg['os'], pkg['kernel']),
                         (str(self.tmp / 'pkg'), 'v2026.10.27', 'openwrt', 'openwrt', '6.18'))
        self.assertEqual(pkg['bytes'], len(KB) + len(ROOTFS))
        other = make_package(self.tmp / 'other', text=manifest(SYSTEM='ubuntu-24.04', OS='ubuntu', UBUNTU='24.04',
                                                              ROOTFS_ASSET='mu300-ubuntu-rootfs.tar.gz'))
        self.assertEqual(package.load(str(top), {'MU300_PACKAGE': str(other)})['system'], 'ubuntu-24.04')
        make_package(self.tmp / 'bad', text=manifest(TAG='x'))
        self.assertEqual(package.load(str(top), {'MU300_PACKAGE': str(self.tmp / 'bad')}).get('error'), 'manifest')

    def test_the_same_rules_as_the_installers(self):
        self.assertIsNotNone(package.parse(manifest()))
        for kw in (dict(TAG='v1..2'), dict(SYSTEM='ubuntu'), dict(KERNEL='5.4'), dict(SHA256_ROOTFS='0' * 63),
                   dict(SYSTEM='ubuntu-26.04', OS='ubuntu', UBUNTU='26.04', ROOTFS_ASSET='mu300-ubuntu-26.04-rootfs.tar.gz',
                        KERNEL='5.4', KERNEL_ASSET='mu300-kernel.tar.gz')):
            self.assertIsNone(package.parse(manifest(**kw)), kw)

    def test_choices_and_answers(self):
        make_package(self.tmp / 'pkg')
        pkg = package.load(str(self.tmp), {'MU300_PACKAGE': str(self.tmp / 'pkg')})
        c = model.Choices()
        c.apply_package(pkg)
        self.assertEqual((c.systems(), c.kernel, c.release, c.panel), (['openwrt'], '6.18', 'v2026.10.27', False))
        c.password = c.password2 = 'secret123'
        facts = model.Facts({'region.size': str(30 << 30), 'region.existing': 'no', 'device.kind': 'f50', 'root': 'yes'})
        a = model.answers(c, facts, 'en', '')
        for key in ('MU300_RELEASE', 'MU300_RELEASE_URL', 'MU300_ANS_BETA', 'MU300_ANS_SYSTEMS', 'MU300_OPENWRT',
                    'MU300_ANS_BOOT_OS', 'MU300_ANS_UBUNTU', 'MU300_ANS_KERNEL', 'MU300_ANS_PANEL'):
            self.assertNotIn(key, a)
        for key in ('MU300_ANS_DEFAULT_LINUX', 'MU300_ANS_HOTSPOT', 'MU300_ANS_GPU', 'MU300_ANS_VPN', 'MU300_ANS_PASSWORD'):
            self.assertIn(key, a)
        self.assertEqual(model.download_plan(c, {'mu300-kernel.tar.gz': 1000}), ([], 0))
        self.assertGreater(model.work_need(c, {}), 400 << 20)
        self.assertIn(('Release', 'v2026.10.27'), model.summary(c, facts, ''))
        self.assertEqual(model.problems(c, facts), [])
        # a V50 and a 5.4 package: said where it is chosen, as the installer says it
        c2 = model.Choices()
        make_package(self.tmp / 'p54', text=manifest(KERNEL='5.4', KERNEL_ASSET='mu300-kernel.tar.gz'))
        c2.apply_package(package.load(str(self.tmp), {'MU300_PACKAGE': str(self.tmp / 'p54')}))
        c2.password = c2.password2 = 'secret123'
        c2.device = 'v50'
        self.assertIn('take the zip for kernel 6.18, 7.2 or 7.3', ' '.join(m for _f, m in model.problems(c2, facts)))


if __name__ == '__main__':
    unittest.main()
