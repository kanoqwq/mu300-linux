"""tools/android-web.sh: the device side of the web installer, on a fake device (tests/fakedevice.py, as for the
Magisk installer). What it tells the page is JSON and marked lines; a zip is checked and moved to root's directory
before it is used; an installation is the zip's own installer, run as a job that does not need its watcher; the
uninstaller writes misc, then the boot partition, then erases, each verified; a backup never takes a partition with
the unit's identity."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
import unittest
import zipfile
from pathlib import Path

import test_magisk_installer as tmi
from helpers import TOP, ShellTest
from test_boot_image import LIVE_A, fake_misc, with_slots
from test_magisk_installer import BUSYBOX, InstallerCase, sha

WEB = TOP / 'tools/android-web.sh'
SWITCH = TOP / 'android/magisk/mu300-linux-switch'
STAGED = {'android-web.sh': WEB, 'storage.sh': TOP / 'tools/storage.sh',
          'android-boot-image.sh': TOP / 'tools/android-boot-image.sh',
          'android-mount-mu300root.sh': TOP / 'tools/android-mount-mu300root.sh', 'switch.sh': SWITCH / 'switch.sh'}
# the device's shell for storage.sh: device paths point into the fake device, and what comes back names the device's
# own paths again (sd_probe's /dev/block/mmcblk1 is checked by its name before it is erased)
DEVSH = r'''#!/bin/sh
[ "$1" = -c ] && shift
sh -c "$(printf '%s' "$1" | sed -e "s|/sys/|$FAKE/sys/|g" -e "s|/dev/block/|$FAKE/dev/block/|g" -e "s|$FAKE$FAKE/|$FAKE/|g")" | sed -e "s|$FAKE/dev/block/|/dev/block/|g"
'''
MIB = 1 << 20


def setUpModule():
    tmi.setUpModule()


def tearDownModule():
    tmi.tearDownModule()


class NoBusybox(ShellTest):
    def test_without_a_root_solutions_busybox_it_says_so(self):
        for shell in self.each_shell():
            r = self.script(shell, WEB, 'status')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout.strip(), 'MU300-WEB-ERROR no-busybox')

    def test_the_site_stages_what_the_script_sources(self):
        # site/tools/stage.sh copies the device scripts next to the page; what android-web.sh reads from its own
        # directory must be among them
        stage = (TOP / 'site/tools/stage.sh').read_text()
        text = WEB.read_text()
        import re
        for name in set(re.findall(r'"\$S/([a-z0-9.-]+\.sh)"', text)):
            self.assertIn(name, stage, name)


@unittest.skipIf(not BUSYBOX, 'no busybox (the script runs under Magisk\'s busybox)')
class WebCase(InstallerCase):
    """a FakeDevice, the staged scripts as the page pushes them, and a whole zip (mu300/, the switch module, the
    payload)"""

    def setUp(self):
        super().setUp()
        self.stage_dir()

    def stage_dir(self):
        self.stage = self.tmp / 'stage'
        shutil.rmtree(self.stage, ignore_errors=True)
        (self.stage / 'in').mkdir(parents=True)
        for name, src in STAGED.items():
            shutil.copy(src, self.stage / name)

    def device(self, **kw):
        super().device(**kw)
        r = self.fake.root
        (r / 'devsh').write_text(DEVSH)
        (r / 'sys/block/mmcblk0/mmcblk0p1/uevent').write_text('MAJOR=179\nPARTNAME=userdata\n')
        shutil.copy(self.stubs / 'magiskboot', r / 'magisk/magiskboot')
        (r / 'data/adb/modules').mkdir(parents=True, exist_ok=True)
        (r / 'drop').write_text('')
        self.stage_dir()

    def small_emmc(self, disk_mib=192, parts_mib=32, fs_mib=100, label='mu300root'):
        """a small eMMC whose bytes are all there: partitions up to PARTS_MIB, then a Linux filesystem of FS_MIB
        (its superblock only). Returns (offset, size) of the filesystem in bytes."""
        r = self.fake.root
        sysb = r / 'sys/block/mmcblk0'
        (sysb / 'mmcblk0p1/size').write_text(f'{parts_mib * 2048 - 2048}\n')
        (sysb / 'size').write_text(f'{disk_mib * 2048}\n')
        last_end = parts_mib * 2048
        off = (last_end // 4096 + 1) * 4096 * 512
        with open(r / 'dev/block/mmcblk0', 'wb') as f:
            f.truncate(disk_mib * MIB)
            f.seek(MIB); f.write(b'ANDROID-PARTITION')                 # inside the partitions
            if fs_mib:
                f.seek(off); f.write(os.urandom(MIB))                  # file data that an erase must remove
                f.seek(off + 1028); f.write((fs_mib * MIB // 4096).to_bytes(4, 'little'))
                f.seek(off + 1080); f.write(b'\x53\xef')
                f.seek(off + 1144); f.write(label.encode().ljust(16, b'\0'))
                f.seek(off + 70 * MIB); f.write(b'DATA-AT-70-MIB')
                f.seek(off + fs_mib * MIB - 16); f.write(b'LAST-OF-THE-FS!!')
                f.seek(off + fs_mib * MIB); f.write(b'BEHIND-THE-FS')
        return off, fs_mib * MIB

    def emmc(self, off, n):
        with open(self.fake.root / 'dev/block/mmcblk0', 'rb') as f:
            f.seek(off)
            return f.read(n)

    def web_env(self, **extra):
        r = self.fake.root
        env = self.fake.env(self.mu300, self.magiskboot)
        env.update({'MU300_WEB_BB': BUSYBOX, 'MU300_INSTALL_BB': BUSYBOX, 'MU300_WEB_ROOT': r / 'data/adb/mu300-web',
                    'MU300_SYSFS': r / 'sys', 'MU300_EMMC': r / 'dev/block/mmcblk0', 'MU300_MODULES': r / 'data/adb/modules',
                    'MU300_LOCAL_TMP': r / 'tmp', 'MU300_DROP_CACHES': r / 'drop', 'MU300_WEB_POLL': '0.1'})
        env.pop('MU300_DIR', None)
        env.update(extra)
        return self.env(**env)

    def web(self, *args, stdin=None, timeout=180, **extra):
        return subprocess.run([BUSYBOX, 'sh', str(self.stage / 'android-web.sh')] + [str(a) for a in args],
                              input=stdin, capture_output=True, text=True, env=self.web_env(**extra), timeout=timeout)

    def status(self, *args, **extra):
        r = self.web('status', *args, **extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [l for l in r.stdout.splitlines() if l.startswith('MU300-WEB-STATUS ')]
        self.assertEqual(len(lines), 1, r.stdout + r.stderr)
        return json.loads(lines[0][len('MU300-WEB-STATUS '):])

    def full_zip(self, name='mu300-magisk-v2026.10.06-openwrt-k6.18.zip', old=False):
        """the zip as a release has it: mu300/, the switch module's files and the payload; pushed to in/NAME"""
        out = self.stage / 'in' / name
        out.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(self.zipfile) as payload, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(self.mu300.rglob('*')):
                if f.is_file():
                    data = f.read_bytes()
                    if old and f.name == 'install.sh':
                        data = data.replace(b'MU300_ANSWERS', b'MU300_NOTHING')
                    z.writestr(zipfile.ZipInfo('mu300/' + str(f.relative_to(self.mu300))), data)
            for f in ('module.prop', 'action.sh', 'switch.sh', 'bootchain.sh', 'system/bin/mu300-linux'):
                z.writestr(f, (SWITCH / f).read_bytes())
            for n in payload.namelist():
                z.writestr(n, payload.read(n), compress_type=zipfile.ZIP_STORED)
        return name, sha(out)

    def taken(self, **kw):
        name, digest = self.full_zip(**kw)
        r = self.web('take', name, digest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return name

    def answers(self, text=''):
        (self.stage / 'in').mkdir(exist_ok=True)
        (self.stage / 'in/answers.conf').write_text(text)
        r = self.web('answers')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def follow(self, **kw):
        r = self.web('follow', **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        last = [l for l in r.stdout.splitlines() if l.startswith('MU300-WEB-EXIT ')]
        self.assertEqual(len(last), 1, r.stdout)
        return last[0].split()[1], r.stdout

    def parts(self):
        by = self.fake.root / 'dev/block/by-name'
        return {n: sha(by / n) for n in ('boot_a', 'boot_b', 'misc')}

    def root(self):
        return self.fake.root / 'data/adb/mu300-web'


class Status(WebCase):
    def test_a_device_without_linux(self):
        s = self.status()
        self.assertEqual((s['v'], s['device'], s['android_slot'], s['linux_slot'], s['slots_ok']), (1, 'f50', 'a', 'b', 1))
        self.assertEqual(s['linux_boot'], 'android-copy')
        self.assertEqual(s['misc'], {'valid': 1, 'bc': LIVE_A.hex()})
        last_end = 2048 + (8 << 21)
        start = (last_end // 4096 + 1) * 4096
        end = (((16 << 21) - 34) // 4096 - 1) * 4096
        self.assertEqual(s['disk'], 16 << 21)
        self.assertEqual(s['last_end'], last_end)
        self.assertEqual(s['region'], {'off': start * 512, 'size': (end - start) * 512, 'existing': 'no', 'dirty': -1,
                                       'part': 0, 'part_fs': 'none', 'part_count': 0, 'foreign_image': 0})
        self.assertEqual(s['parts'], [['userdata', 2048, 8 << 21]])
        self.assertEqual(s['sd'], {'dev': '', 'bytes': 0, 'small': 0, 'existing': 'no'})
        self.assertEqual(s['job'], {'state': 'none', 'kind': '', 'arg': '', 'rc': -1, 'step': 0, 'more': 0})
        self.assertEqual((s['magiskboot'], s['root_kind'], s['module'], s['stalled']), (1, 'magisk', 0, 0))
        self.assertGreater(s['data_free'], 0)
        self.assertNotIn('linux', s)
        # a plain status makes nothing, not even its own directory
        self.assertFalse(self.root().exists())

    def test_deep_samples_the_region(self):
        self.assertEqual(self.status('deep')['region']['dirty'], 0)
        off, _ = self.small_emmc(fs_mib=0)
        with open(self.fake.root / 'dev/block/mmcblk0', 'r+b') as f:
            f.seek(off + 9 * MIB); f.write(b'someone wrote here')     # where the second of 16 samples is taken
        s = self.status('deep')
        self.assertEqual(s['region']['existing'], 'no')
        self.assertGreaterEqual(s['region']['dirty'], 1)

    def test_names_that_need_escaping_stay_json(self):
        self.device(model='ZTE MU300 "quoted" \\ back\tslash')
        s = self.status()
        self.assertEqual(s['model'], 'ZTE MU300 "quoted" \\ backslash')
        self.assertEqual(s['device'], 'f50')

    def test_v50_exactly(self):
        # the ZTE V50 (FINDINGS 95): V50 / MU3351 on ums9620; any other V50 is no device this page knows
        for kw, want in ((dict(model='V50', product='MU3351', platform='ums9620'), 'v50'),
                         (dict(model='V50', product='MU3351', platform='mt6789'), ''),
                         (dict(model='V50', product='MU3351'), ''),
                         (dict(model='ZTE Blade V50', product='P671F60', platform='ums9620'), ''),
                         (dict(model='U30 Air'), 'u30air'), (dict(), 'f50')):
            self.device(**kw)
            self.assertEqual(self.status()['device'], want, kw)

    def test_another_device_and_no_slots(self):
        self.device(model='Pixel 9')
        s = self.status()
        self.assertEqual(s['device'], '')
        (self.fake.root / 'props').write_text('ro.product.model=ZTE MU300\nro.product.device=mu300\n')
        s = self.status()
        self.assertEqual((s['android_slot'], s['slots_ok'], s['linux_boot']), ('', 0, 'unknown'))

    def test_an_installation_is_looked_into_read_only(self):
        self.existing_filesystem(systems=('ubuntu', 'openwrt-luci'), ubuntu='24.04')
        fs = self.fake.root / 'fs'
        (fs / 'ubuntu/etc/mu300').mkdir(parents=True)
        (fs / 'ubuntu/etc/mu300/image-version').write_text('v2026.10.20\n')
        (fs / 'ubuntu/etc/mu300/default-boot').write_text('linux\n')
        (fs / '.mu300').mkdir()
        (fs / '.mu300/boot-os').write_text('ubuntu\n')
        (fs / '.mu300/boot-attempts').write_text('3\n')
        (fs / 'boot').mkdir()
        (fs / 'boot/kernel').write_text('6.18\n')
        s = self.status()
        self.assertEqual(s['region']['existing'], 'yes')
        self.assertNotIn('linux', s)
        s = self.status('deep')
        self.assertEqual(s['region']['dirty'], -1)
        self.assertEqual(s['linux'], {'where': 'internal', 'mounted': 1, 'systems': {'ubuntu': 'v2026.10.20', 'openwrt-luci': ''},
                                      'boot_os': 'ubuntu', 'kernel': '6.18', 'ubuntu': '24.04', 'default_linux': 1, 'attempts': 3})
        log = (self.fake.root / 'android-sh.log').read_text()
        self.assertIn('MU300_RO=1', log)
        self.assertNotIn('MU300_RO= ', log.split('android-mount-mu300root.sh')[1].split('\n')[0])
        self.assertEqual((self.fake.root / 'mounts').read_text(), '')
        self.assertEqual((fs / 'home/precious').read_text(), 'a user file')
        self.assertFalse((self.root() / 'mnt').exists())

    def test_what_the_linux_slot_holds(self):
        by = self.fake.root / 'dev/block/by-name'
        img = bytearray((by / 'boot_b').read_bytes())
        img[44:55] = b'loglevel=5\0'
        (by / 'boot_b').write_bytes(bytes(img))
        self.assertEqual(self.status()['linux_boot'], 'linux')
        img[44:55] = bytes(11); img[5000] ^= 1
        (by / 'boot_b').write_bytes(bytes(img))
        self.assertEqual(self.status()['linux_boot'], 'other')
        (by / 'boot_b').write_bytes(b'\0' * 8192)
        self.assertEqual(self.status()['linux_boot'], 'none')

    def test_a_card_and_a_stalled_run(self):
        self.device(card=tmi.fake_ext4_bytes('mu300sd'))
        (self.fake.root / 'data/adb/mu300-install.stalled').write_text('11111111-2222-3333-4444-555555555555\nstage unpack\n')
        s = self.status()
        self.assertEqual(s['sd'], {'dev': '/dev/block/mmcblk1', 'bytes': 32 << 30, 'small': 0, 'existing': 'yes'})
        self.assertEqual(s['stalled'], 1)
        (self.fake.root / 'boot_id').write_text('another-boot\n')
        self.assertEqual(self.status()['stalled'], 0)

    def test_not_root(self):
        r = self.web('status', MU300_SKIP_ROOT_CHECK='')
        if os.getuid() != 0:
            self.assertEqual(r.stdout.strip(), 'MU300-WEB-ERROR not-root')
        r = self.web('nonsense')
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-ERROR bad-command')


class Take(WebCase):
    def test_a_good_zip_is_moved_checked_and_unpacked(self):
        name, digest = self.full_zip()
        r = self.web('take', name, digest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        z = json.loads(r.stdout.split('MU300-WEB-ZIP ', 1)[1])
        self.assertEqual(z, {'name': name, 'web': 1, 'tag': 'v2026.10.06', 'system': 'openwrt', 'os': 'openwrt',
                             'ubuntu': '', 'kernel': '6.18'})
        self.assertFalse((self.stage / 'in' / name).exists())
        kept = self.root() / 'zips' / name
        self.assertEqual(sha(kept), digest)
        self.assertEqual(kept.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root().stat().st_mode & 0o777, 0o700)
        x = self.root() / 'x' / name[:-4]
        self.assertTrue((x / 'mu300/install.sh').is_file())
        self.assertTrue((x / 'switch.sh').is_file())
        self.assertTrue((x / 'system/bin/mu300-linux').is_file())
        self.assertFalse((x / 'payload').exists())

    def test_a_zip_from_before_the_web_installer(self):
        name, digest = self.full_zip(old=True)
        r = self.web('take', name, digest)
        self.assertEqual(json.loads(r.stdout.split('MU300-WEB-ZIP ', 1)[1])['web'], 0)

    def test_refusals_leave_nothing_behind(self):
        name, digest = self.full_zip()
        wrong = '0' * 64
        r = self.web('take', name, wrong)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), f'MU300-WEB-ERROR sha-mismatch {digest}')
        self.assertFalse((self.root() / 'zips' / name).exists())
        self.assertFalse((self.stage / 'in' / name).exists())
        for bad_name in ('../evil.zip', 'mu300-magisk-x/../y.zip', 'other.zip', 'mu300-magisk-a b.zip', 'mu300-magisk-$(id).zip', ''):
            self.assertIn('MU300-WEB-ERROR bad-name', self.web('take', bad_name, digest).stdout, bad_name)
        for bad_sha in ('', 'abc', 'Z' * 64, digest.upper(), digest + '0'):
            self.assertIn('MU300-WEB-ERROR bad-sha', self.web('take', name, bad_sha).stdout, bad_sha)
        self.assertIn('MU300-WEB-ERROR no-file', self.web('take', name, digest).stdout)
        # a link in the adb user's directory is not followed
        target = self.tmp / 'elsewhere.zip'
        target.write_bytes(b'x')
        (self.stage / 'in' / name).symlink_to(target)
        self.assertIn('MU300-WEB-ERROR no-file', self.web('take', name, sha(target)).stdout)
        self.assertTrue(target.exists())
        # not a zip of this project
        (self.stage / 'in' / name).unlink(missing_ok=True)      # (the link went with the move, as a link)
        with zipfile.ZipFile(self.stage / 'in' / name, 'w') as z:
            z.writestr('hello.txt', 'hi')
        r = self.web('take', name, sha(self.stage / 'in' / name))
        self.assertIn('MU300-WEB-ERROR not-a-zip', r.stdout)
        self.assertFalse((self.root() / 'x' / name[:-4]).exists())

    def test_extras(self):
        f = self.stage / 'in/mu300-linux-vpn.tar.gz'
        f.write_bytes(tmi.tar_gz({'VERSION': b'2.0.1\n'}))
        digest = sha(f)
        self.assertIn('MU300-WEB-ERROR bad-name', self.web('extra', 'lang', f.name, digest).stdout)
        self.assertIn('MU300-WEB-ERROR bad-name', self.web('extra', 'vpn', '../x', digest).stdout)
        self.assertIn('MU300-WEB-ERROR sha-mismatch', self.web('extra', 'vpn', f.name, 'f' * 64).stdout)
        self.assertFalse((self.root() / 'extras/mu300-extra-vpn.tar.gz').exists())
        f.write_bytes(tmi.tar_gz({'VERSION': b'2.0.1\n'}))
        r = self.web('extra', 'vpn', f.name, sha(f))
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-EXTRA vpn')
        self.assertEqual((self.root() / 'extras/mu300-extra-vpn.tar.gz').stat().st_mode & 0o777, 0o600)


class Answers(WebCase):
    def test_answers_become_roots_file_and_the_password_comes_on_stdin(self):
        (self.stage / 'in/answers.conf').write_text('MU300_BOOT=android\n MU300_PASSWORD = in-a-readable-file\nMU300_LANG=tr\n')
        r = self.web('answers')
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-ANSWERS ok')
        a = self.root() / 'answers.conf'
        self.assertEqual(a.read_text(), 'MU300_BOOT=android\nMU300_LANG=tr\n')
        self.assertEqual(a.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.stage / 'in/answers.conf').exists())
        self.assertIn('MU300-WEB-ERROR short-password', self.web('password', stdin='12345\n').stdout)
        self.assertIn('MU300-WEB-ERROR no-stdin', self.web('password', stdin='').stdout)
        r = self.web('password', stdin="pa ss'w\"or$d\\x\r\n")
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-PASSWORD ok')
        self.assertEqual(a.read_text(), 'MU300_BOOT=android\nMU300_LANG=tr\nMU300_PASSWORD=pa ss\'w"or$d\\x\n')
        self.assertEqual(a.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('or$d', r.stdout + r.stderr)

    def test_no_answers(self):
        self.assertIn('MU300-WEB-ERROR no-file', self.web('answers').stdout)
        self.assertIn('MU300-WEB-ERROR no-answers', self.web('password', stdin='long enough\n').stdout)
        name = self.taken()
        self.assertIn('MU300-WEB-ERROR no-answers', self.web('run', 'plan', name).stdout)
        self.assertIn('MU300-WEB-ERROR no-zip', self.web('run', 'plan', 'mu300-magisk-other.zip').stdout)
        self.assertIn('MU300-WEB-ERROR bad-argument', self.web('run', 'format-everything').stdout)
        self.assertIn('MU300-WEB-ERROR no-job', self.web('follow').stdout)
        self.assertIn('MU300-WEB-ERROR no-job', self.web('cancel').stdout)


class Jobs(WebCase):
    def test_a_plan_writes_nothing_and_is_read_as_lines(self):
        name = self.taken()
        self.answers('MU300_BOOT_ATTEMPTS=3\n')
        before = self.parts()
        r = self.web('run', 'plan', name)
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB started plan')
        rc, out = self.follow()
        self.assertEqual(rc, '3', out)
        self.assertIn('MU300-STEP 3/9 plan', out)
        self.assertIn('MU300-PLAN boot_attempts=3', out)
        self.assertIn('MU300-PLAN dry_run=1', out)
        self.assertEqual(self.parts(), before)
        s = self.status()
        self.assertEqual(s['job'], {'state': 'done', 'kind': 'plan', 'arg': name, 'rc': 3, 'step': 3, 'more': 0})
        # the answers went with the run that used them, and the installer left no work directory
        self.assertFalse((self.root() / 'answers.conf').exists())
        self.assertEqual(self.work_dirs(), [])
        # the same log again for a page that comes back later
        self.assertEqual(self.follow()[1], out)

    def test_a_run_that_another_system_was_to_follow_says_so_to_whoever_finds_it(self):
        # two systems are two zips, and only the page can send the second: when the page is gone, the job's record
        # tells the next one that the installation it finds finished is not all that was asked for
        name = self.taken()
        self.answers()
        self.assertIn('MU300-WEB-ERROR bad-argument', self.web('run', 'plan', name, 'more;id').stdout)
        self.assertEqual(self.web('run', 'plan', name, 'more').stdout.strip(), 'MU300-WEB-JOB started plan')
        self.follow()
        self.assertEqual(self.status()['job']['more'], 1)
        self.answers()
        self.web('run', 'plan', name)
        self.follow()
        self.assertEqual(self.status()['job']['more'], 0)

    def test_an_installation_with_an_extra_and_the_switch_module(self):
        name = self.taken()
        f = self.stage / 'in/mu300-linux-vpn.tar.gz'
        f.write_bytes(tmi.tar_gz({'VERSION': b'2.0.1\n'}))
        self.web('extra', 'vpn', f.name, sha(f))
        self.answers('MU300_LANG=en\n')
        self.assertEqual(self.web('password', stdin='correct horse\n').stdout.strip(), 'MU300-WEB-PASSWORD ok')
        self.fake_install_hook('ls "$MU300_DEVICE_WORK" > "$FAKE/work-ls"')
        self.web('run', 'install', name)
        rc, out = self.follow()
        self.assertEqual(rc, '0', out)
        self.assertIn('MU300-STEP 9/9 done', out)
        self.assertIn('MU300-PLAN extras=vpn', out)
        self.assertIn('MU300-PLAN password=given', out)
        self.assertNotIn('correct horse', out)
        self.assertIn('mu300-extra-vpn.tar.gz', (self.fake.root / 'work-ls').read_text())
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual((by / 'boot_b').read_bytes()[44:55], b'loglevel=5\0')
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f))
        mod = self.fake.root / 'data/adb/modules/mu300_linux_switch'
        for f, mode in (('module.prop', 0o644), ('switch.sh', 0o755), ('action.sh', 0o755), ('bootchain.sh', 0o755),
                        ('system/bin/mu300-linux', 0o755)):
            self.assertEqual((mod / f).stat().st_mode & 0o777, mode, f)
        s = self.status()
        self.assertEqual((s['linux_boot'], s['module'], s['job']['rc'], s['job']['step']), ('linux', 1, 0, 9))
        self.assertFalse((self.root() / 'answers.conf').exists())
        self.assertFalse((self.root() / 'extras').exists())
        self.assertFalse((self.root() / 'x' / name[:-4] / 'mu300/extras').exists())

    def test_a_job_does_not_need_its_watcher(self):
        name = self.taken()
        self.answers()
        self.fake_install_hook('sleep 3')
        t0 = time.time()
        r = self.web('run', 'install', name)
        self.assertLess(time.time() - t0, 2.5, 'run returns while the job goes on')
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB started install')
        time.sleep(0.5)
        s = self.status('deep')
        self.assertEqual(s['job']['state'], 'running')
        self.assertNotIn('linux', s)                    # nothing is mounted beside a running job
        # no second job, no new zip, and no way to stop it once it writes
        self.assertIn('MU300-WEB-ERROR job-running', self.web('run', 'plan', name).stdout)
        n2, d2 = self.full_zip(name='mu300-magisk-v2026.10.06-openwrt-k7.2.zip')
        self.assertIn('MU300-WEB-ERROR job-running', self.web('take', n2, d2).stdout)
        self.assertIn('CLEAN job-running', self.web('clean', 'all').stdout)
        for _ in range(40):
            if self.status()['job']['step'] >= 6:
                break
            time.sleep(0.1)
        self.assertIn('MU300-WEB-ERROR too-late', self.web('cancel').stdout)
        # a watcher that is killed (the tab closed, the cable pulled) changes nothing for the job
        w = subprocess.Popen([BUSYBOX, 'sh', str(self.stage / 'android-web.sh'), 'follow'], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self.web_env())
        time.sleep(0.3)
        w.kill(); w.wait()
        rc, out = self.follow()
        self.assertEqual(rc, '0', out)
        self.assertEqual(self.status()['linux_boot'], 'linux')

    def test_cancel_before_anything_is_written(self):
        name = self.taken()
        self.answers()
        before = self.parts()
        # slow down the stage that builds the boot image (magiskboot), long before the systems are written
        real = (self.stubs / 'magiskboot').read_text()
        (self.stubs / 'magiskboot').write_text('#!/bin/sh\nsleep 5\n' + real.split('\n', 1)[1])
        self.web('run', 'install', name)
        for _ in range(60):
            if self.status()['job']['step'] >= 4:
                break
            time.sleep(0.1)
        r = self.web('cancel')
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB cancelled', r.stderr)
        s = self.status()
        self.assertEqual((s['job']['state'], s['job']['rc']), ('done', 143))
        time.sleep(1.2)
        self.assertEqual(self.parts(), before)
        self.assertEqual(self.follow()[0], '143')
        self.assertFalse((self.root() / 'answers.conf').exists())

    def test_a_job_cut_off_by_a_restart_is_called_interrupted(self):
        name = self.taken()
        self.answers('MU300_DRY_RUN=1\n')
        self.web('run', 'plan', name)
        self.follow()
        job = self.root() / 'job'
        # the job's process ends a moment after it wrote its status: wait for that, or it still counts as running
        pid = int((job / 'pid').read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        (job / 'rc').unlink()
        self.assertEqual(self.status()['job']['state'], 'interrupted')
        self.assertEqual(self.follow()[0], 'interrupted')
        # a pid of this boot that is someone else's by now does not make it "running" after a restart
        (job / 'pid').write_text(f'{os.getpid()}\n')
        self.assertEqual(self.status()['job']['state'], 'running')
        (self.fake.root / 'boot_id').write_text('the-next-boot\n')
        self.assertEqual(self.status()['job']['state'], 'interrupted')
        # and a new job may start
        self.answers('')
        self.assertIn('JOB started plan', self.web('run', 'plan', name).stdout)
        self.assertEqual(self.follow()[0], '3')


class Uninstall(WebCase):
    def installed(self, **kw):
        """Linux on boot_b and armed, a filesystem with data in the region, the switch module"""
        off, size = self.small_emmc(**kw)
        by = self.fake.root / 'dev/block/by-name'
        img = bytearray((by / 'boot_b').read_bytes())
        img[44:55] = b'loglevel=5\0'; img[9000:9005] = b'linux'
        (by / 'boot_b').write_bytes(bytes(img))
        fake_misc(by / 'misc', with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f))
        mod = self.fake.root / 'data/adb/modules/mu300_linux_switch'
        mod.mkdir(parents=True, exist_ok=True)
        (mod / 'switch.sh').write_text('#!/system/bin/sh\n')
        tmp = self.fake.root / 'tmp'
        (tmp / 'mu300-ubuntu.tar.gz').write_text('left by install.sh')
        (tmp / 'android-install.sh').write_text('left by install.sh')
        (tmp / 'mu300-web').mkdir(exist_ok=True)
        (tmp / 'mu300-web/keep').write_text('the stage directory of the page')
        return off, size

    def uninstall(self, wipe, sd='keep'):
        r = self.web('run', 'uninstall', wipe, sd)
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB started uninstall', r.stderr)
        return self.follow()

    def check_android_again(self, slot='a'):
        by = self.fake.root / 'dev/block/by-name'
        other = 'b' if slot == 'a' else 'a'
        want = with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e) if slot == 'a' else with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f)
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], want)
        self.assertEqual(sha(by / f'boot_{other}'), sha(by / f'boot_{slot}'))
        self.assertEqual((by / f'boot_{slot}').read_bytes()[44:55], bytes(11))

    def test_secure_erases_the_whole_filesystem_and_nothing_else(self):
        off, size = self.installed()
        before_a = sha(self.fake.root / 'dev/block/by-name/boot_a')
        rc, out = self.uninstall('secure')
        self.assertEqual(rc, '0', out)
        steps = [l.split()[2] for l in out.splitlines() if l.startswith('MU300-STEP ')]
        self.assertEqual(steps, ['check', 'misc', 'boot', 'erase', 'card', 'cleanup', 'done'])
        self.assertLess(out.index('MU300-STEP 2/7 misc'), out.index('MU300-STEP 3/7 boot'))
        self.assertIn('MU300-PROGRESS 100 100', out)
        self.check_android_again()
        self.assertEqual(sha(self.fake.root / 'dev/block/by-name/boot_a'), before_a)
        self.assertEqual(self.emmc(off, size), bytes(size))
        self.assertEqual(self.emmc(off + size, 13), b'BEHIND-THE-FS')
        self.assertEqual(self.emmc(MIB, 17), b'ANDROID-PARTITION')
        self.assertIn('erased and verified', out)
        self.assertTrue((self.fake.root / 'data/adb/modules/mu300_linux_switch/remove').exists())
        tmp = self.fake.root / 'tmp'
        self.assertFalse((tmp / 'mu300-ubuntu.tar.gz').exists())
        self.assertFalse((tmp / 'android-install.sh').exists())
        self.assertTrue((tmp / 'mu300-web/keep').exists())
        s = self.status()
        self.assertEqual((s['linux_boot'], s['region']['existing'], s['module']), ('android-copy', 'no', 0))

    def test_quick_erases_the_headers_only(self):
        off, size = self.installed()
        rc, out = self.uninstall('quick')
        self.assertEqual(rc, '0', out)
        self.check_android_again()
        self.assertEqual(self.emmc(off, 64 * MIB), bytes(64 * MIB))
        self.assertEqual(self.emmc(off + 70 * MIB, 14), b'DATA-AT-70-MIB')
        self.assertIn('headers only', out)

    def test_a_small_filesystem_is_never_overrun_by_the_quick_erase(self):
        off, size = self.installed(fs_mib=20)
        rc, out = self.uninstall('quick')
        self.assertEqual(rc, '0', out)
        self.assertEqual(self.emmc(off, size), bytes(size))
        self.assertEqual(self.emmc(off + size, 13), b'BEHIND-THE-FS')

    def test_keep_leaves_the_filesystem_and_clears_the_card_marker(self):
        off, size = self.installed()
        (self.fake.root / 'fs/.mu300').mkdir(parents=True)
        (self.fake.root / 'fs/.mu300/root-on-sd').write_text('')
        # the fake's unmount writes a read-write mount back by copying over: here it has to carry a removal too
        ash = self.fake.root / 'android-sh'
        ash.write_text(ash.read_text().replace('cp -R "$3/." "$FAKE/fs/"', 'rm -rf "$FAKE/fs"; mkdir "$FAKE/fs"; cp -R "$3/." "$FAKE/fs/"'))
        data = self.emmc(off, size)
        rc, out = self.uninstall('keep')
        self.assertEqual(rc, '0', out)
        self.check_android_again()
        self.assertEqual(self.emmc(off, size), data)
        self.assertFalse((self.fake.root / 'fs/.mu300/root-on-sd').exists())
        self.assertEqual((self.fake.root / 'mounts').read_text(), '')

    def test_android_on_slot_b(self):
        self.device(slot='_b')
        off, size = self.small_emmc()
        by = self.fake.root / 'dev/block/by-name'
        img = bytearray((by / 'boot_a').read_bytes())
        img[44:55] = b'loglevel=5\0'
        (by / 'boot_a').write_bytes(bytes(img))
        before_b = sha(by / 'boot_b')
        rc, out = self.uninstall('quick')
        self.assertEqual(rc, '0', out)
        self.check_android_again('b')
        self.assertEqual(sha(by / 'boot_b'), before_b)

    def test_already_android_is_said_and_not_rewritten(self):
        self.small_emmc(fs_mib=0)
        before = self.parts()
        rc, out = self.uninstall('secure')
        self.assertEqual(rc, '0', out)
        self.assertIn('is the boot slot already', out)
        self.assertIn('is a copy of boot_a already', out)
        self.assertIn('no mu300root filesystem on the eMMC', out)
        self.assertEqual(self.parts(), before)

    def test_refused_before_anything_is_written(self):
        self.installed()
        before = self.parts()
        (self.fake.root / 'dev/block/by-name/misc').write_bytes(bytes(4096))
        rc, out = self.uninstall('secure')
        self.assertEqual(rc, '1')
        self.assertIn('misc holds no valid boot control block; nothing was changed', out)
        self.assertEqual(self.parts()['boot_b'], before['boot_b'])
        self.device(model='Pixel 9')
        self.installed()
        before = self.parts()
        rc, out = self.uninstall('secure')
        self.assertEqual(rc, '1')
        self.assertIn('nothing was changed', out)
        self.assertEqual(self.parts(), before)
        for args in (('wipe-everything', 'keep'), ('secure', 'format'), ('secure',)):
            self.assertIn('MU300-WEB-ERROR bad-argument', self.web('run', 'uninstall', *args).stdout)

    def test_a_region_still_attached_is_not_erased(self):
        off, size = self.installed()
        loop = self.fake.root / 'sys/block/loop3/loop'
        loop.mkdir(parents=True)
        (loop / 'offset').write_text(f'{off}\n')
        data = self.emmc(off, size)
        rc, out = self.uninstall('secure')
        self.assertEqual(rc, '1')
        self.assertIn('still attached', out)
        self.assertEqual(self.emmc(off, size), data)
        # misc and the boot partition came first, so the device boots Android all the same
        self.check_android_again()

    def lib(self, code, **extra):
        return subprocess.run([BUSYBOX, 'sh', '-c', f'MU300_LIB=1; . "{self.stage}/android-web.sh"; ' + code],
                              capture_output=True, text=True, env=self.web_env(MU300_WEB_STAGE=self.stage, **extra), timeout=60)

    def test_a_job_that_ends_while_it_is_looked_at_is_done(self):
        # the job writes its status (rc) and ends; a look that found no status yet and then no process saw it end
        # in between: done, never "interrupted" (follow then printed "EXIT interrupted" for an install that went well)
        job = self.root() / 'job'
        job.mkdir(parents=True)
        (job / 'kind').write_text('install\n')
        dead = subprocess.Popen(['true']); dead.wait()
        (job / 'pid').write_text(f'{dead.pid}\n')
        code = ('boot_now > "$JOB/boot"; '
                'kill() { [ "$1" = -0 ] && [ ! -e "$JOB/rc.once" ] && : > "$JOB/rc.once" && echo 0 > "$JOB/rc"; command kill "$@"; }; '
                'job_state; rm -f "$JOB/rc"; job_state')
        r = self.lib(code)
        self.assertEqual(r.stdout.split(), ['done', 'interrupted'], r.stderr)

    def test_misc_that_does_not_verify_gets_its_old_block_back(self):
        self.installed()
        misc = self.fake.root / 'dev/block/by-name/misc'
        live = misc.read_bytes()[0x800:0x820]
        new = with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e)
        dd = ('dd() { case "$*" in *"of=$MISC"*) case "$MODE:$*" in garble:*bc.bin\\ *|stuck:*) '
              'printf garbage | command dd of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null; return 0 ;; esac ;; esac; command dd "$@"; }; ')
        for mode, text, after in (('garble', 'written back and verified', live), ('stuck', 'Do not reboot', None)):
            fake_misc(misc, live)
            r = self.lib(f'detect; mkdir -p "$R"; UN_LIVE={live.hex()}; UN_NEW={new.hex()}; {dd} MODE={mode}; un_misc; echo SURVIVED')
            self.assertNotIn('SURVIVED', r.stdout)
            self.assertIn(text, r.stdout, r.stderr)
            if after:
                self.assertEqual(misc.read_bytes()[0x800:0x820], after)
            else:
                self.assertTrue((self.root() / 'bc.bin.live').read_bytes() == live)

    def test_a_boot_copy_that_does_not_verify_stops_before_the_erase(self):
        off, size = self.installed()
        data = self.emmc(off, size)
        dd = 'dd() { case "$*" in *"of=$BOOT_LINUX"*) return 0 ;; esac; command dd "$@"; }; '
        r = self.lib(f'{dd} uninstall secure keep; echo SURVIVED')
        self.assertNotIn('SURVIVED', r.stdout)
        self.assertIn('boot_b verify failed (misc already points to slot a: Android keeps booting)', r.stdout)
        self.assertEqual(self.emmc(off, size), data)
        self.assertEqual((self.fake.root / 'dev/block/by-name/misc').read_bytes()[0x800:0x820], with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e))

    def test_the_card_is_erased_only_when_asked_and_only_when_it_is_ours(self):
        for label, sdw, erased in (('mu300sd', 'erase', True), ('mu300sd', 'keep', False), ('other-linux', 'erase', False)):
            self.device(card=tmi.fake_ext4_bytes(label) + bytes(MIB))
            self.small_emmc(fs_mib=0)
            rc, out = self.uninstall('keep', sdw)
            self.assertEqual(rc, '0', out)
            head = (self.fake.root / 'dev/block/mmcblk1').read_bytes()[1080:1082]
            self.assertEqual(head == b'\0\0', erased, (label, sdw, out))


class Backup(WebCase):
    def test_what_an_installation_writes_and_nothing_else(self):
        self.small_emmc(fs_mib=0)
        by = self.fake.root / 'dev/block/by-name'
        for n in ('prodnv', 'nr_fixnv1_a', 'nr_fixnv1_b', 'persist', 'miscdata', 'l_modem_a'):
            (by / n).write_bytes(b'IMEI 123456789012345')
        r = self.web('backup')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = {l.split()[1]: l.split()[2:] for l in r.stdout.splitlines() if l.startswith('MU300-WEB-FILE ')}
        self.assertEqual(sorted(files), ['boot_b.img', 'gpt-head.bin', 'gpt-tail.bin', 'misc-head.bin', 'partitions.txt',
                                         'sml_b.img', 'teecfg_b.img', 'trustos_b.img', 'uboot_b.img', 'vbmeta_b.img'])
        # the copies are root's: nothing was made where the adb user can write
        self.assertFalse((self.stage / 'out').exists())
        out = self.root() / 'out'
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(files))
        for name, (size, digest) in files.items():
            self.assertEqual(int(size), (out / name).stat().st_size, name)
            self.assertEqual(digest, sha(out / name), name)
            # ... and the page reads each one as text
            c = self.web('cat', name)
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertEqual(hashlib.sha256(base64.b64decode(c.stdout)).hexdigest(), digest, name)
        for bad in ('../answers.conf', 'answers.conf', 'boot_c.img', 'prodnv.img', '/etc/passwd', 'boot_b.img/../../x', '', 'job'):
            self.assertIn('MU300-WEB-ERROR bad-name', self.web('cat', bad).stdout, bad)
        self.assertEqual(files['boot_b.img'][1], sha(by / 'boot_b'))
        self.assertEqual(int(files['misc-head.bin'][0]), 4096)
        self.assertEqual((int(files['gpt-head.bin'][0]), int(files['gpt-tail.bin'][0])), (34 * 512, 33 * 512))
        self.assertEqual((out / 'partitions.txt').read_text(), f'mmcblk0p1 userdata 2048 {32 * 2048 - 2048}\n')
        self.assertIn('MU300-WEB-BACKUP android_slot=a linux_slot=b', r.stdout)
        for p in out.iterdir():
            self.assertNotIn(b'IMEI', p.read_bytes())
        # a name of the list that is a link there (nobody but root could have made it) is not read through
        (out / 'boot_a.img').symlink_to(by / 'prodnv')
        r = self.web('cat', 'boot_a.img')
        self.assertIn('MU300-WEB-ERROR no-file', r.stdout)
        self.assertNotIn(base64.b64encode(b'IMEI 123456789012345').decode(), r.stdout)

    def test_identity_and_calibration_partitions_are_refused_by_name(self):
        r = subprocess.run([BUSYBOX, 'sh', '-c', f'MU300_LIB=1; MU300_WEB_STAGE="{self.stage}"; . "{self.stage}/android-web.sh"; '
                            'for p in prodnv nr_fixnv1_a nr_runtimenv2 nr_deltanv_b nr_phy_a persist miscdata calinv isedata '
                            'ztecfg ztepersist userdata super l_modem_a pm_sys_a metadata splloader dtbo_a vendor_boot_a; do '
                            'backup_allowed $p && echo "ALLOWED $p"; done; '
                            'for p in boot_b uboot_a sml_b teecfg_a trustos_b vbmeta_a misc; do backup_allowed $p || echo "REFUSED $p"; done'],
                           capture_output=True, text=True, env=self.web_env())
        self.assertEqual(r.stdout, '', r.stderr)

    def test_a_partition_that_cannot_be_read_gives_no_backup(self):
        self.small_emmc(fs_mib=0)
        (self.fake.root / 'dev/block/by-name/trustos_b').unlink()
        (self.fake.root / 'dev/block/by-name/trustos_b').symlink_to(self.tmp / 'missing')
        r = self.web('backup')
        # a link to nothing is "not there" (skipped); the files that are there still come
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('trustos_b.img', r.stdout)
        self.assertIn('MU300-WEB-FILE uboot_b.img', r.stdout)


class Clean(WebCase):
    def test_clean(self):
        name = self.taken()
        self.answers('MU300_DRY_RUN=1\n')
        (self.stage / 'out').mkdir()
        (self.stage / 'out/boot_b.img').write_text('x')
        (self.stage / 'in/half-pushed.zip').write_text('x')
        adb = self.fake.root / 'data/adb'
        dead, alive = adb / 'mu300-magisk.DEAD01', adb / 'mu300-magisk.ALIVE1'
        for w in (dead, alive):
            (w / 'mu300root/ubuntu').mkdir(parents=True)
            (w / 'mu300root/ubuntu/precious').write_text('a user file')
        (self.fake.root / 'mounts').write_text(f'/dev/block/loop7 {alive}/mu300root ext4 rw 0 0\n')
        (self.root() / 'out').mkdir()
        (self.root() / 'out/boot_b.img').write_text('x')
        r = self.web('clean')
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-CLEAN ok')
        # root's own copies go; the adb user's directory is the page's to clean, root deletes nothing in it
        self.assertFalse((self.root() / 'out').exists())
        self.assertTrue((self.stage / 'out/boot_b.img').exists())
        self.assertTrue((self.stage / 'in/half-pushed.zip').exists())
        self.assertFalse((self.root() / 'answers.conf').exists())
        self.assertFalse(dead.exists())
        self.assertEqual((alive / 'mu300root/ubuntu/precious').read_text(), 'a user file')
        self.assertTrue((self.root() / 'zips' / name).exists())
        self.web('clean', 'all')
        self.assertFalse((self.root() / 'zips').exists())
        self.assertFalse((self.root() / 'x').exists())
        # an unreadable mount table: nothing that could hold a mounted filesystem is removed
        (dead / 'mu300root').mkdir(parents=True)
        self.web('clean', MU300_MOUNTS=self.tmp / 'no-such-file')
        self.assertTrue(dead.exists())


class BootLinux(WebCase):
    def test_only_with_linux_on_the_other_slot(self):
        self.assertIn('MU300-WEB-ERROR no-linux', self.web('boot-linux').stdout)
        by = self.fake.root / 'dev/block/by-name'
        img = bytearray((by / 'boot_b').read_bytes())
        img[44:55] = b'loglevel=5\0'; img[9000:9005] = b'linux'
        (by / 'boot_b').write_bytes(bytes(img))
        # switch.sh takes misc only where sysfs names it so (FINDINGS 101)
        misc = os.path.realpath(by / 'misc')
        ue = self.fake.root / 'sys/class/block' / os.path.basename(misc)
        ue.mkdir(parents=True, exist_ok=True)
        (ue / 'uevent').write_text('PARTNAME=misc\n')
        before = self.parts()
        r = self.web('boot-linux', 'dry', MU300_BUSYBOX=BUSYBOX, MU300_SLOT_SUFFIX='_a')
        if os.getuid() != 0:
            # the switch script itself wants root (it has no test switch for that): reaching it is the test here
            self.assertIn('mu300-linux: run as root', r.stderr)
            return
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('misc  new: ' + with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f).hex(), r.stdout)
        self.assertIn('dry run: nothing was written', r.stdout)
        self.assertEqual(self.parts(), before)


class FetchCase(WebCase):
    """what the tests of the device's own download share: a wget that is a script, and the job around it"""

    NAME = 'mu300-magisk-v2026.11.01-openwrt-k6.18.zip'
    # what busybox wget prints while it follows GitHub's redirect (measured on an F50, FINDINGS 72)
    SAYS = ('echo "Connecting to github.com (140.82.121.3:443)" >&2; echo "wget: note: TLS certificate validation not implemented" >&2; '
            'echo "Connecting to release-assets.githubusercontent.com (185.199.108.133:443)" >&2; ')
    OUT = 'for a in "$@"; do [ "$p" = -O ] && out=$a; p=$a; done; '

    def wget(self, body):
        self.stub('fake-wget', body)
        return self.stubs / 'fake-wget'

    def served(self):
        name, digest = self.full_zip(name=self.NAME)
        src = self.tmp / 'served.zip'
        shutil.move(self.stage / 'in' / name, src)
        return name, digest, src

    def fetch(self, w, size, name=None, **env):
        r = self.web('run', 'fetch', name or self.NAME, size, MU300_WGET=w, **env)
        self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB started fetch', r.stdout + r.stderr)
        return self.follow()


class Fetch(FetchCase):
    """the device downloads a release's zip itself: from an address made of the zip's name, watched (busybox wget
    cannot check a certificate: where it connects is read from what it prints), continued where it broke, and with
    an exit status that says why it failed"""

    def test_the_address_is_this_projects_release_of_the_tag_in_the_name(self):
        name, digest, src = self.served()
        size = src.stat().st_size
        shutil.rmtree(self.stage / 'in')                  # as after "clean": the download makes the directory itself
        w = self.wget(f'echo "$@" > "$STUBLOG/wget-args"; {self.SAYS}{self.OUT}sleep 0.5; cp "{src}" "$out"')
        rc, out = self.fetch(w, size)
        self.assertEqual(rc, '0', out)
        self.assertIn(f'MU300-PROGRESS {size} {size}', out)
        args = (self.tmp / 'wget-args').read_text().split()
        self.assertEqual(args[-1], f'https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.01/{name}')
        self.assertIn('-c', args)                         # a start that is there is continued
        # the download is root's: nothing was made in the adb user's directory (a directory root made there
        # refused the page's next push on a device)
        self.assertFalse((self.stage / 'in').exists())
        self.assertNotIn('--no-check-certificate', args)
        # only a file until it is checked: a wrong checksum deletes it, the right one takes it
        self.assertIn('sha-mismatch', self.web('take', name, '0' * 64).stdout)
        self.assertFalse((self.root() / 'dl' / name).exists())
        self.fetch(w, size)
        self.assertIn('MU300-WEB-ZIP', self.web('take', name, digest).stdout)
        self.assertFalse((self.root() / 'fetch.log').exists())

    def test_every_name_a_release_has_is_one_the_devices_own_shell_takes(self):
        # in the shell's own patterns: Android's busybox (bionic's regex) has no \| in a basic expression, and the
        # sed that parsed the name refused every release zip on a device
        text = WEB.read_text()
        tag = text[text.index('zip_tag() {'):text.index('# wget_strangers LOG')]
        code = '\n'.join(l for l in tag.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotIn('sed', code)
        self.assertNotIn(r'\|', code)
        w = self.wget(f'{self.SAYS}exit 1')
        for system in ('openwrt', 'openwrt-luci', 'ubuntu-24.04', 'ubuntu-26.04'):
            for k in ('5.4', '6.18', '7.2'):
                for t in ('v2026.10.25', 'v2026.11.01_2', 'v1'):
                    r = self.web('run', 'fetch', f'mu300-magisk-{t}-{system}-k{k}.zip', 10, MU300_WGET=w)
                    self.assertEqual(r.stdout.strip(), 'MU300-WEB-JOB started fetch', f'{t} {system} {k}: {r.stdout}')
                    self.follow()

    def test_names_that_are_not_a_release_zip_are_never_downloaded(self):
        w = self.wget('echo called >> "$STUBLOG/wget-args"')
        for bad in ('mu300-magisk-v1-openwrt-k5.4.zip/../x', 'evil.zip', 'mu300-magisk-v1-arch-k5.4.zip', 'mu300-magisk-v1-openwrt-k9.9.zip',
                    'mu300-magisk-x-openwrt-k5.4.zip', 'mu300-magisk-v1-openwrt-k5.4.zip?x=1', 'mu300-magisk-v1/evil-openwrt-k5.4.zip', '',
                    'mu300-magisk-v1-evil-openwrt-k5.4.zip', 'mu300-magisk-v1..2-openwrt-k5.4.zip', 'mu300-magisk-v1-ubuntu-25.04-k5.4.zip',
                    'mu300-magisk-v1-openwrt-k5.4.zip.zip', 'mu300-magisk--openwrt-k5.4.zip', 'mu300-magisk-v1-openwrt-luci-luci-k5.4.zip'):
            self.assertIn('MU300-WEB-ERROR bad-name', self.web('run', 'fetch', bad, 10, MU300_WGET=w).stdout, bad)
            if bad:
                self.assertIn('MU300-WEB-ERROR bad-name', self.web('net', bad, MU300_WGET=w).stdout, bad)
        self.assertIn('MU300-WEB-ERROR bad-argument', self.web('run', 'fetch', self.NAME, '1;id', MU300_WGET=w).stdout)
        self.assertFalse((self.tmp / 'wget-args').exists())

    def test_a_download_that_breaks_is_continued_where_it_stopped(self):
        name, digest, src = self.served()
        size = src.stat().st_size
        # the first call brings the first 3000 bytes and fails; the next one must be asked to continue, and does
        w = self.wget(f'{self.SAYS}{self.OUT}echo "$@" >> "$STUBLOG/wget-args"; '
                      f'if [ ! -s "$out" ]; then head -c 3000 "{src}" > "$out"; echo "wget: connection closed prematurely" >&2; exit 1; fi; '
                      f'tail -c +3001 "{src}" >> "$out"')
        rc, out = self.fetch(w, size)
        self.assertEqual(rc, '0', out)
        self.assertIn(f'- the connection broke at 3000 of {size} bytes (wget: connection closed prematurely); continuing', out)
        self.assertEqual(len((self.tmp / 'wget-args').read_text().splitlines()), 2)
        self.assertIn('MU300-WEB-ZIP', self.web('take', name, digest).stdout)

    def test_a_start_left_by_an_earlier_run_is_continued_by_the_next(self):
        name, digest, src = self.served()
        size = src.stat().st_size
        broke = self.wget(f'{self.SAYS}{self.OUT}n=$(cat "$out" 2>/dev/null | wc -c); tail -c +$((n + 1)) "{src}" | head -c 5000 >> "$out"; '
                          'echo "wget: connection closed prematurely" >&2; exit 1')
        rc, out = self.fetch(broke, size, MU300_FETCH_TRIES='2')
        self.assertEqual(rc, '12', out)                   # failed, not "no connection": bytes did arrive
        self.assertIn('! the connection broke 2 times; 10000 of', out)
        self.assertEqual((self.root() / 'dl' / name).stat().st_size, 10000)
        rest = self.wget(f'{self.SAYS}{self.OUT}tail -c +10001 "{src}" >> "$out"')
        rc, out = self.fetch(rest, size)
        self.assertEqual(rc, '0', out)
        self.assertIn('- continuing after the 10000 bytes that are here already', out)
        self.assertIn('MU300-WEB-ZIP', self.web('take', name, digest).stdout)

    def test_no_connection_is_said_as_that(self):
        for says in ("wget: bad address 'github.com'", "wget: can't connect to remote host (140.82.121.3): Network is unreachable",
                     'Connecting to github.com (140.82.121.3:443)\nwget: download timed out',
                     'Connecting to github.com (140.82.121.3:443)\nwget: error getting response: Connection reset by peer'):
            w = self.wget(f'cat >&2 <<\'EOF\'\n{says}\nEOF\nexit 1')
            rc, out = self.fetch(w, 100)
            self.assertEqual(rc, '10', says + out)
            self.assertIn('! the device has no connection to GitHub (wget: ', out)
            self.assertFalse((self.root() / 'dl' / self.NAME).exists())
            n = json.loads(self.web('net', self.NAME, MU300_WGET=w).stdout.split('MU300-WEB-NET ', 1)[1])
            self.assertEqual((n['ok'], n['why']), (0, 'no-network'), says)
            self.assertTrue(n['detail'].startswith('wget: '), n)
        # the release has no such file: a failure, but not "no connection"
        w = self.wget(f'{self.SAYS}echo "wget: server returned error: HTTP/1.1 404 Not Found" >&2; exit 1')
        rc, out = self.fetch(w, 100)
        self.assertEqual(rc, '12', out)
        self.assertIn('! the download failed: wget: server returned error: HTTP/1.1 404 Not Found', out)

    def test_a_download_sent_on_to_another_host_is_stopped_and_nothing_is_kept(self):
        # busybox wget cannot check a certificate, so where it connects is the one thing that can be watched
        for where in ('Connecting to evil.example (203.0.113.9:443)', 'Connecting to githubusercontent.com.evil.example (203.0.113.9:443)',
                      'Connecting to evilgithubusercontent.com (203.0.113.9:443)', 'Connecting to release-assets.githubusercontent.com (203.0.113.9:80)',
                      'Connecting to github.com:8443 (203.0.113.9:8443)', 'Connecting to 203.0.113.9 (203.0.113.9:443)'):
            w = self.wget(f'echo "Connecting to github.com (140.82.121.3:443)" >&2; echo "{where}" >&2; {self.OUT}echo payload > "$out"; sleep 3; echo late >> "$STUBLOG/late"')
            rc, out = self.fetch(w, 8)
            self.assertEqual(rc, '11', where + out)
            self.assertIn('which is not GitHub', out)
            self.assertFalse((self.root() / 'dl' / self.NAME).exists(), where)
            self.assertFalse((self.tmp / 'late').exists(), 'the download went on after it was sent elsewhere')
            n = json.loads(self.web('net', self.NAME, MU300_WGET=self.wget(f'echo "{where}" >&2')).stdout.split('MU300-WEB-NET ', 1)[1])
            self.assertEqual((n['ok'], n['why']), (0, 'host'), where)
        # ... also when the whole file was there before anyone looked
        w = self.wget(f'echo "Connecting to evil.example (203.0.113.9:443)" >&2; {self.OUT}printf 12345678 > "$out"')
        rc, out = self.fetch(w, 8)
        self.assertEqual(rc, '11', out)
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())
        # a wget that does not say where it connects cannot be watched: its file is not kept
        w = self.wget(f'{self.OUT}printf 12345678 > "$out"')
        rc, out = self.fetch(w, 8)
        self.assertEqual(rc, '12', out)
        self.assertIn('does not say where it connects', out)
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())

    def test_a_file_of_another_size_is_not_kept(self):
        w = self.wget(f'{self.SAYS}{self.OUT}printf 123456789012 > "$out"')
        rc, out = self.fetch(w, 8)
        self.assertEqual(rc, '13', out)
        self.assertIn('nothing kept', out)
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())
        # short and no more comes: a failure that keeps the start for the next try
        w = self.wget(f'{self.SAYS}{self.OUT}[ -s "$out" ] || printf 1234 > "$out"')
        rc, out = self.fetch(w, 8)
        self.assertEqual(rc, '12', out)
        self.assertEqual((self.root() / 'dl' / self.NAME).read_text(), '1234')

    def test_net_says_whether_github_answers_and_over_what(self):
        ok = self.wget(f'echo "$@" > "$STUBLOG/wget-args"; {self.SAYS}')
        for dumpsys, kind in (('Active default network: none', 'none'),
                              ('Active default network: 100\n  NetworkAgentInfo{network{100}  handle{432902426637}  ni{MOBILE[LTE] CONNECTED extra: internet} nc{[ Transports: CELLULAR Capabilities: INTERNET]}}', 'mobile'),
                              ('NetworkAgentInfo{network{101} ni{WIFI CONNECTED} nc{[ Transports: WIFI ]}}\nActive default network: 101', 'wifi'),
                              ('Active default network: 102\nNetworkAgentInfo{network{102} nc{[ Transports: ETHERNET ]}}', 'ethernet'),
                              ('Active default network: 7\nNetworkAgentInfo{network{7} nc{[ Transports: VPN ]}}', 'other'),
                              ('Active default network: 5; rm -rf /', 'none'), ('', 'none')):
            self.stub('dumpsys', f"cat <<'EOF'\n{dumpsys}\nEOF")
            r = self.web('net', self.NAME, MU300_WGET=ok, MU300_ANDROID_PATH=f"{self.stubs}:{os.environ['PATH']}")
            n = json.loads(r.stdout.split('MU300-WEB-NET ', 1)[1])
            self.assertEqual(n, {'ok': 1, 'why': '', 'uplink': kind, 'detail': ''}, dumpsys)
        args = (self.tmp / 'wget-args').read_text().split()
        self.assertIn('--spider', args)                   # headers only: nothing is downloaded to ask
        self.assertEqual(args[-1], f'https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.01/{self.NAME}')
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())
        self.assertFalse((self.root() / 'fetch.log').exists())
        self.web('net', MU300_WGET=ok)
        self.assertEqual((self.tmp / 'wget-args').read_text().split()[-1], 'https://github.com/')

    def test_the_page_can_cancel_a_download_and_what_arrived_stays(self):
        w = self.wget(f'{self.SAYS}{self.OUT}printf 1234 > "$out"; sleep 30')
        self.web('run', 'fetch', self.NAME, 8, MU300_WGET=w)
        time.sleep(0.5)
        self.assertIn('MU300-WEB-JOB cancelled', self.web('cancel').stdout)
        self.assertEqual(self.status()['job']['state'], 'done')
        self.assertEqual((self.root() / 'dl' / self.NAME).read_text(), '1234')


class Bounds(FetchCase):
    """/data is Android's own: what root writes there has an end, set before it starts - a download cannot grow
    past the release's size or run for ever, a zip is not unpacked beyond a bound, a job's log stops growing"""

    def test_a_server_that_sends_more_than_the_release_names_cannot_fill_the_storage(self):
        # 5 MB for a file of 100 000 bytes: the downloader's own file size limit stops the write, and what the file
        # had grown to when the stub looked is at most the size rounded up to a block. (The stub outlives the watch's
        # kill, which may come first - the file is past the size once the limit stopped it - so that it can look.)
        w = self.wget(f'trap "" TERM; {self.SAYS}{self.OUT}head -c 5000000 /dev/zero >> "$out" 2>/dev/null; wc -c < "$out" > "$STUBLOG/grew"')
        t0 = time.time()
        rc, out = self.fetch(w, 100000)
        self.assertEqual(rc, '13', out)
        self.assertIn('nothing kept', out)
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())
        self.assertLessEqual(int((self.tmp / 'grew').read_text()), 100000 + 512)
        self.assertLess(time.time() - t0, 20)

    def test_a_server_that_never_ends_is_ended(self):
        # more and more, slowly, for ever: ended when it passes the size (here by the watch), nothing kept
        w = self.wget(f'{self.SAYS}{self.OUT}while :; do printf "%0200d" 0 >> "$out" || exit 1; echo beat >> "$STUBLOG/beat"; sleep 0.05; done')
        t0 = time.time()
        rc, out = self.fetch(w, 1000)
        self.assertEqual(rc, '13', out)
        self.assertFalse((self.root() / 'dl' / self.NAME).exists())
        self.assertLess(time.time() - t0, 20)
        beats = (self.tmp / 'beat').stat().st_size
        time.sleep(0.5)
        self.assertEqual((self.tmp / 'beat').stat().st_size, beats, 'the downloader went on after the job ended')
        # a little, and then nothing for ever, under the size: ended by the clock; what arrived stays for the next try
        w = self.wget(f'{self.SAYS}{self.OUT}printf 1234 > "$out"; sleep 60')
        t0 = time.time()
        rc, out = self.fetch(w, 1000, MU300_FETCH_MAX_SECONDS='1')
        self.assertEqual(rc, '12', out)
        self.assertIn('did not finish in time; 4 of 1000 bytes are here', out)
        self.assertLess(time.time() - t0, 20)
        self.assertEqual((self.root() / 'dl' / self.NAME).read_text(), '1234')
        # the question "can you reach GitHub" has an end as well
        w = self.wget(f'{self.SAYS}sleep 60')
        t0 = time.time()
        n = json.loads(self.web('net', self.NAME, MU300_WGET=w, MU300_NET_MAX_SECONDS='2').stdout.split('MU300-WEB-NET ', 1)[1])
        self.assertEqual(n['ok'], 0)
        self.assertLess(time.time() - t0, 20)

    def test_a_download_that_would_not_leave_the_reserve_free_is_not_started(self):
        w = self.wget('echo called >> "$STUBLOG/wget-args"')
        rc, out = self.fetch(w, 1000, MU300_DATA_RESERVE=str(1 << 60))
        self.assertEqual(rc, '14', out)
        self.assertIn("! Android's storage has", out)
        self.assertFalse((self.tmp / 'wget-args').exists())
        # a size no release zip has is not asked for at all
        rc, out = self.fetch(w, 400 * 1024 * 1024)
        self.assertEqual(rc, '13', out)
        self.assertFalse((self.tmp / 'wget-args').exists())
        # the reserve is the 512 MiB the Magisk installer insists on
        self.assertIn('DATA_RESERVE=${MU300_DATA_RESERVE:-536870912}', WEB.read_text())

    def test_a_zip_is_not_unpacked_beyond_a_bound_and_a_pushed_file_has_a_largest_size(self):
        name, digest = self.full_zip()
        r = self.web('take', name, digest, MU300_UNPACK_MAX='1000')
        self.assertIn('MU300-WEB-ERROR too-large', r.stdout)
        self.assertFalse((self.root() / 'zips' / name).exists())
        self.assertFalse((self.root() / 'x' / name[:-4]).exists())
        name, digest = self.full_zip()
        self.assertIn('MU300-WEB-ERROR no-space', self.web('take', name, digest, MU300_DATA_RESERVE=str(1 << 60)).stdout)
        self.assertFalse((self.root() / 'x' / name[:-4]).exists())
        name, digest = self.full_zip()
        self.assertIn('MU300-WEB-ERROR too-large', self.web('take', name, digest, MU300_ZIP_MAX='1000').stdout)
        self.assertEqual(list((self.root()).glob('incoming*')), [])
        (self.stage / 'in/answers.conf').write_text('X=1\n' * 30000)
        self.assertIn('MU300-WEB-ERROR too-large', self.web('answers').stdout)
        self.assertFalse((self.root() / 'answers.conf').exists())

    def test_a_jobs_log_stops_growing_and_the_job_goes_on(self):
        name = self.taken()
        self.answers('MU300_BOOT_ATTEMPTS=3\n')
        self.web('run', 'plan', name, MU300_JOB_LOG_MAX='600')
        rc, out = self.follow()
        self.assertEqual(rc, '3', out)                     # the plan ran to its end
        log = (self.root() / 'job/log').read_text()
        self.assertLess(len(log), 1000)
        self.assertIn('! the log is full; the job goes on', log)
        self.assertNotIn('Dry run: nothing was written', log)


class Links(WebCase):
    """the adb user's directory is not root's: whoever can write there can put a link where a file is expected. Root
    moves a pushed file out of it by its name (a rename follows no link) and judges it in its own directory, and
    never creates, writes, chmods or deletes anything there. A link planted at every name root touches: the target
    is untouched, and the run refuses or ignores it."""

    def setUp(self):
        super().setUp()
        self.victim = self.tmp / 'victim'
        self.victim.mkdir()
        (self.victim / 'secret').write_text('MU300_PASSWORD=root-only\n')
        (self.victim / 'keep').write_text('precious')
        self.before = self.snapshot()

    def snapshot(self):
        return {str(p.relative_to(self.victim)): (p.read_bytes(), p.stat().st_mode) for p in sorted(self.victim.rglob('*')) if p.is_file()}

    def untouched(self, why=''):
        self.assertEqual(self.snapshot(), self.before, why)
        self.assertEqual(self.victim.stat().st_mode & 0o777, self.victim.stat().st_mode & 0o777)

    def test_a_link_where_a_pushed_file_is_expected(self):
        name, digest = self.full_zip()
        real = self.tmp / 'real.zip'
        shutil.move(self.stage / 'in' / name, real)
        for what, target in (('a file', self.victim / 'secret'), ('a directory', self.victim), ('the right zip, elsewhere', real),
                             ('nothing', self.tmp / 'missing')):
            (self.stage / 'in' / name).symlink_to(target)
            r = self.web('take', name, digest)
            self.assertIn('MU300-WEB-ERROR no-file', r.stdout, what)
            self.assertFalse((self.root() / 'zips' / name).exists(), what)
            self.assertFalse((self.root() / 'incoming').exists(), what)
            self.untouched(what)
            self.assertTrue(real.exists())
            (self.stage / 'in' / name).unlink(missing_ok=True)
        # a directory under the file's name is not a file either
        (self.stage / 'in' / name).mkdir()
        ((self.stage / 'in' / name) / 'x').write_text('x')
        self.assertIn('MU300-WEB-ERROR no-file', self.web('take', name, digest).stdout)
        self.assertFalse((self.root() / 'zips' / name).exists())
        # the answers and an extra: the same
        for target in (self.victim / 'secret', self.victim):
            (self.stage / 'in/answers.conf').symlink_to(target)
            self.assertIn('MU300-WEB-ERROR no-file', self.web('answers').stdout)
            # root's answers never hold what the link pointed at
            self.assertFalse((self.root() / 'answers.conf').exists())
            (self.stage / 'in/answers.conf').unlink(missing_ok=True)
            (self.stage / 'in/extra-vpn.tar.gz').symlink_to(target)
            self.assertIn('MU300-WEB-ERROR no-file', self.web('extra', 'vpn', 'extra-vpn.tar.gz', '0' * 64).stdout)
            self.assertEqual(list((self.root() / 'extras').glob('*')), [])
            (self.stage / 'in/extra-vpn.tar.gz').unlink(missing_ok=True)
            self.untouched()

    def test_the_directory_for_pushed_files_is_a_link(self):
        # in/ itself points somewhere else: nothing is taken out of that place, and nothing is made or removed in it
        name, digest = self.full_zip()
        shutil.move(self.stage / 'in' / name, self.victim / name)
        (self.victim / 'answers.conf').write_text('MU300_LANG=en\n')
        self.before = self.snapshot()
        shutil.rmtree(self.stage / 'in')
        (self.stage / 'in').symlink_to(self.victim)
        self.assertIn('MU300-WEB-ERROR no-file', self.web('take', name, digest).stdout)
        self.assertIn('MU300-WEB-ERROR no-file', self.web('answers').stdout)
        self.untouched('take and answers')
        self.assertEqual(self.web('clean').stdout.strip(), 'MU300-WEB-CLEAN ok')
        self.web('clean', 'all')
        self.untouched('clean')
        self.assertTrue((self.stage / 'in').is_symlink())
        # the device's own download does not go there either
        w = self.stubs / 'fake-wget'
        self.stub('fake-wget', Fetch.SAYS + Fetch.OUT + 'printf 12345678 > "$out"')
        self.web('run', 'fetch', Fetch.NAME, 8, MU300_WGET=w)
        self.assertEqual(self.follow()[0], '0')
        self.assertEqual((self.root() / 'dl' / Fetch.NAME).read_text(), '12345678')
        self.untouched('fetch')

    def test_a_link_where_the_backup_and_the_download_would_be_written(self):
        self.small_emmc(fs_mib=0)
        (self.stage / 'out').symlink_to(self.victim)
        r = self.web('backup')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.untouched('backup')
        self.assertEqual(sorted(p.name for p in self.victim.iterdir()), ['keep', 'secret'])
        self.assertTrue((self.root() / 'out/boot_b.img').is_file())
        self.web('clean')
        self.untouched('clean')
        self.assertTrue((self.stage / 'out').is_symlink())
        # a link planted in root's own download directory cannot be planted by the adb user; one left there by
        # anything is not written through
        (self.root() / 'dl').mkdir(exist_ok=True)
        (self.root() / 'dl' / Fetch.NAME).symlink_to(self.victim / 'keep')
        self.stub('fake-wget', Fetch.SAYS + Fetch.OUT + 'printf 12345678 > "$out"')
        self.web('run', 'fetch', Fetch.NAME, 8, MU300_WGET=self.stubs / 'fake-wget')
        self.follow()
        self.untouched('fetch')

    def test_roots_directory_is_roots_or_nothing_runs(self):
        # the name of root's directory is there as a link, or as a directory of another mode that cannot be made 700
        self.root().parent.mkdir(parents=True, exist_ok=True)
        if self.root().exists():
            shutil.rmtree(self.root())
        self.root().symlink_to(self.victim)
        name, digest = self.full_zip()
        for cmd in (['take', name, digest], ['answers'], ['backup'], ['run', 'uninstall', 'quick', 'keep'], ['net']):
            self.assertIn('MU300-WEB-ERROR no-root-dir', self.web(*cmd).stdout, cmd)
        self.assertEqual(self.web('clean').stdout.strip(), 'MU300-WEB-CLEAN ok')
        self.untouched()
        self.assertEqual(sorted(p.name for p in self.victim.iterdir()), ['keep', 'secret'])

    def test_uninstall_removes_single_names_in_the_adb_users_tmp_and_walks_no_tree(self):
        off, size = self.small_emmc()
        tmp = self.fake.root / 'tmp'
        tmp.mkdir(exist_ok=True)
        (tmp / 'mu300root').symlink_to(self.victim)              # where install.sh mounts: a link to a directory
        (tmp / 'mu300-rootfs.tar.gz').symlink_to(self.victim / 'keep')
        (tmp / 'mu300-dir').mkdir()
        (tmp / 'mu300-dir/inside').write_text('not ours to walk')
        (tmp / 'android-install.sh').symlink_to(self.victim / 'secret')
        (tmp / 'mu300-kernel.tar.gz').write_text('left by the installer')
        self.web('run', 'uninstall', 'quick', 'keep')
        rc, out = self.follow()
        self.assertEqual(rc, '0', out)
        self.untouched()
        self.assertFalse((tmp / 'mu300root').is_symlink())       # the links went, as links
        self.assertFalse((tmp / 'mu300-rootfs.tar.gz').is_symlink())
        self.assertFalse((tmp / 'android-install.sh').is_symlink())
        self.assertFalse((tmp / 'mu300-kernel.tar.gz').exists())
        self.assertEqual((tmp / 'mu300-dir/inside').read_text(), 'not ours to walk')

    def test_nothing_in_the_script_writes_into_the_adb_users_directory(self):
        # every use of $S is a script that is read or run, or the one rename out of it
        import re
        uses = [l.strip() for l in WEB.read_text().splitlines() if re.search(r'\$S(/|"|\b)', l) and not l.lstrip().startswith('#')]
        for l in uses:
            ok = (re.search(r'\. "\$S/[a-z-]+\.sh"', l) or re.search(r'"\$S/(android-mount-mu300root|switch)\.sh"', l) or
                  l.startswith('S=${MU300_WEB_STAGE') or l.startswith('[ ! -L "$S" ] && [ ! -L "$S/in" ]') or
                  l.startswith('mv "$S/in/$1" "$_b"'))
            self.assertTrue(ok, l)
        for never in ('> "$S', '>> "$S', 'rm -rf "$S', 'rm -f "$S', 'mkdir -p "$S', 'chmod 755 "$S', 'chmod 644 "$S', '-O "$S', 'of="$S', 'chown "$(stat'):
            self.assertNotIn(never, WEB.read_text(), never)


NODE = shutil.which('node')


def node_major():
    if not NODE:
        return 0
    try:
        return int(subprocess.run([NODE, '--version'], capture_output=True, text=True).stdout.strip().lstrip('v').split('.')[0])
    except ValueError:
        return 0


@unittest.skipIf(not BUSYBOX, 'no busybox (the script runs under Magisk\'s busybox)')
@unittest.skipIf(node_major() < 20, 'Node.js 20 or newer is needed for the page\'s code')
class EndToEnd(WebCase):
    """the page's own JavaScript (device.js, flow.js, the adb client) through a scripted adbd whose shell runs the
    real android-web.sh and the Magisk installer on the fake device: tests/web/e2e-android.mjs"""

    def e2e(self, **extra):
        site = self.tmp / 'site'
        r = subprocess.run(['sh', str(TOP / 'site/tools/stage.sh'), str(site)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        name, digest = self.full_zip()
        zip_path = self.tmp / name
        shutil.move(self.stage / 'in' / name, zip_path)
        # the page pushes the scripts itself: the device has nothing of the web installer yet
        shutil.rmtree(self.stage)
        self.stage.mkdir()
        # su: the command runs as this user; only the question "am I root" is answered as a root manager would
        self.stub('su', '[ "$1" = -c ] && shift\n[ "$1" != "id -u" ] || { echo 0; exit 0; }\nexec sh -c "$1"')
        env = self.web_env(E2E_STAGE=self.stage, E2E_SITE=site / 'assets/web-installer/device', E2E_BUSYBOX=BUSYBOX,
                           E2E_ZIP=zip_path, E2E_ZIP_SHA256=digest, **extra)
        r = subprocess.run([NODE, str(TOP / 'tests/web/e2e-android.mjs')], capture_output=True, text=True, env=env, timeout=300)
        lines = [l for l in r.stdout.splitlines() if l.startswith('E2E-RESULT ')]
        self.assertEqual(len(lines), 1, r.stdout[-3000:] + r.stderr[-3000:])
        return json.loads(lines[0][len('E2E-RESULT '):])

    def test_backup_plan_install_with_the_watcher_gone_in_the_middle(self):
        (self.fake.root / 'props').write_text((self.fake.root / 'props').read_text() + 'ro.boot.verifiedbootstate=orange\nsys.boot_completed=1\n')
        before_a = sha(self.fake.root / 'dev/block/by-name/boot_a')
        before_b = sha(self.fake.root / 'dev/block/by-name/boot_b')
        self.fake_install_hook('sleep 2')
        vpn = self.tmp / 'vpn.tar.gz'
        vpn.write_bytes(tmi.tar_gz({'VERSION': b'2.0.1\n'}))
        o = self.e2e(E2E_VPN=vpn)
        self.assertEqual((o['kind'], o['model'], o['lock'], o['su'], o['root']), ('android', 'ZTE MU300', 'unlocked', True, 'granted'))
        self.assertEqual(o['blockers'], [])
        self.assertEqual(o['before']['linux_boot'], 'android-copy')
        # the backup: the Linux slot's images as they were before the installation
        self.assertIn('boot_b.img', o['backup'])
        self.assertTrue(o['backupMatches'])
        self.assertEqual(o['backupSha']['boot_b.img'], before_b)
        self.assertEqual(o['inspected']['web'], True)
        self.assertEqual(o['taken']['web'], 1)
        self.assertEqual(o['errors'], [])
        # the plan wrote nothing and said what the page asked for
        self.assertEqual(o['planRc'], 3)
        self.assertEqual((o['plan']['boot_attempts'], o['plan']['hotspot'], o['plan']['password'], o['plan']['adb'], o['plan']['dry_run']),
                         ('4', '0', 'given', '1', '1'))
        self.assertEqual(o['afterPlan']['linux_boot'], 'android-copy')
        # the first watcher went away when the systems were being written; the job went on
        self.assertTrue(o['watcherCut'])
        self.assertEqual(o['stepsBeforeCut'][-1], 'systems')
        self.assertEqual(o['installRc'], 0, o['installErrors'])
        self.assertEqual(o['steps'], ['check', 'unpack', 'plan', 'vendor', 'image', 'systems', 'boot', 'arm', 'done'])
        self.assertEqual((o['after']['linux_boot'], o['after']['module'], o['after']['job']['rc']), ('linux', 1, 0))
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_a'), before_a)
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f))
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('BOOT_ATTEMPTS=4', env)
        self.assertIn('IMPORT_HOTSPOT=0', env)
        self.assertIn('ADB=1', env)
        self.assertIn(' web-installer\'', env)
        # the password reached the installer (its hash is in the env file) and is in no line the page logged
        self.assertNotIn("PWHASH=''", env)
        self.assertFalse(o['passwordInLog'])
        self.assertFalse(o['keyInLog'])
        self.assertGreater(o['logLines'], 20)
        # clean all: nothing of the run is left where only root can look, and the stage holds only the scripts
        self.assertFalse((self.root() / 'zips').exists())
        self.assertFalse((self.root() / 'answers.conf').exists())
        self.assertFalse((self.stage / 'in').exists())
        self.assertFalse((self.stage / 'out').exists())

    def test_install_then_uninstall(self):
        off, size = self.small_emmc(disk_mib=1200, fs_mib=0)       # room for OpenWrt (a sparse file)
        # the fake android-install.sh does not make a filesystem: put one where the installer was told to
        self.fake_install_hook('printf "\\123\\357" | dd of="$FAKE/dev/block/mmcblk0" bs=1 seek=$((OFF + 1080)) conv=notrunc 2>/dev/null; '
                               'printf mu300root | dd of="$FAKE/dev/block/mmcblk0" bs=1 seek=$((OFF + 1144)) conv=notrunc 2>/dev/null; '
                               'printf "\\000\\100\\000\\000" | dd of="$FAKE/dev/block/mmcblk0" bs=1 seek=$((OFF + 1028)) conv=notrunc 2>/dev/null')
        o = self.e2e(E2E_UNINSTALL='secure')
        self.assertEqual(o['errors'], [])
        self.assertEqual(o['installRc'], 0, o['installErrors'])
        self.assertEqual(o['after']['existing'], 'yes')
        self.assertEqual(o['uninstallRc'], 0)
        self.assertEqual(o['uninstallSteps'], ['check', 'misc', 'boot', 'erase', 'card', 'cleanup', 'done'])
        self.assertEqual(o['final'], {'linux_boot': 'android-copy', 'existing': 'no', 'module': 0})
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e))
        self.assertEqual(sha(by / 'boot_b'), sha(by / 'boot_a'))
        self.assertEqual(self.emmc(off, 64 * MIB), bytes(64 * MIB))


if __name__ == '__main__':
    unittest.main()
