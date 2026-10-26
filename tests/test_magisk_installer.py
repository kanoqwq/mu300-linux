"""android/magisk/installer/mu300-install.sh: the installer a Magisk zip runs on the device. mu300-install.conf is
read, never run; on a fake device (tests/fakedevice.py) it finds the model, the slots, where Linux goes and what is
already there, and a refusal or a dry run writes nothing; an installation writes boot_<linux slot>, verifies it
and arms misc last."""
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from fakedevice import UBB, FakeDevice
from helpers import TOP, ShellTest
from test_android_boot_image import MAGISKBOOT, cpio_all, fake_android_root, ramdisk_of
from test_boot_image import HAVE_LZ4, unlz4_legacy

# the zip's mu300/ directory, entry -> source in this repository (None: made per zip). Task 10's zip builder copies
# exactly these; its test compares the two lists.
MU300_FILES = {
    'mu300/install.sh': 'android/magisk/installer/mu300-install.sh',
    'mu300/android-boot-image.sh': 'tools/android-boot-image.sh',
    'mu300/mu300-update': 'rootfs/overlay/opt/mu300/bin/mu300-update',
    'mu300/android-install.sh': 'tools/android-install.sh',
    'mu300/android-mount-mu300root.sh': 'tools/android-mount-mu300root.sh',
    'mu300/android-pwcheck.sh': 'tools/android-pwcheck.sh',
    'mu300/android-bootchain.sh': 'tools/android-bootchain.sh',
    'mu300/storage.sh': 'tools/storage.sh',
    'mu300/i18n.sh': 'tools/i18n.sh',
    'mu300/i18n/tr.tsv': 'i18n/tr.tsv',
    'mu300/i18n/zh.tsv': 'i18n/zh.tsv',
    'mu300/subset-files.txt': 'android-vendor/subset-files.txt',
    'mu300/gpu-files.txt': 'android-vendor/gpu-files.txt',
    'mu300/busybox': None,      # the 5.4 bundle's static busybox; here the fake device's mkpasswd
    'mu300/manifest': None,     # what the zip carries, written by InstallerCase.zip()
}

BUSYBOX = shutil.which('busybox')
INSTALLER_DIR = INSTALLER = None
_staged = None


def stage_mu300(dest):
    """DEST laid out like the zip's mu300/ (without its manifest)"""
    for entry, src in MU300_FILES.items():
        out = dest / entry[len('mu300/'):]
        out.parent.mkdir(parents=True, exist_ok=True)
        if src:
            shutil.copy(TOP / src, out)
    (dest / 'busybox').write_text(UBB)
    (dest / 'busybox').chmod(0o755)


def setUpModule():
    global INSTALLER_DIR, INSTALLER, _staged
    _staged = tempfile.mkdtemp(prefix='mu300-magisk-stage-')
    INSTALLER_DIR = Path(_staged) / 'mu300'
    stage_mu300(INSTALLER_DIR)
    INSTALLER = INSTALLER_DIR / 'install.sh'


def tearDownModule():
    shutil.rmtree(_staged, ignore_errors=True)


def busybox_has(applet):
    if not BUSYBOX:
        return False
    r = subprocess.run([BUSYBOX, '--list'], capture_output=True, text=True)
    return applet in r.stdout.split()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_ext4_bytes(label):
    """the start of an ext4 filesystem as sd_existing reads it: the magic and the label"""
    data = bytearray(4096)
    data[1080:1082] = b'\x53\xef'
    data[1144:1144 + len(label)] = label.encode()
    return bytes(data)


def tar_gz(files):
    """{name: bytes} -> a .tar.gz as the release makes them (./ names). The same files give the same bytes: the gzip
    header holds no time (with the time in it, two zips made a second apart had other checksums - a test that made
    one zip, then another, and installed the first against the second's manifest failed now and then)"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz, tarfile.open(fileobj=gz, mode='w') as t:
        for name, data in files.items():
            info = tarfile.TarInfo('./' + name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_generic = {}


def generic_ramdisk():
    """the bundle's ramdisk-generic.lz4, built once from fake modules with build-boot-image.py --generic-ramdisk"""
    if 'data' not in _generic:
        tmp = tempfile.mkdtemp(prefix='mu300-generic-')
        try:
            t = Path(tmp)
            (t / 'mods').mkdir()
            for n in (TOP / 'boot' / 'module-order.txt').read_text().split():
                (t / 'mods' / n).write_bytes(b'\x7fELF base ' + n.encode())
            for f in ('busybox', 'logdw'):
                (t / f).write_bytes(b'\x7fELF ' + f.encode())
            r = subprocess.run([sys.executable, str(TOP / 'boot' / 'build-boot-image.py'), '--generic-ramdisk',
                                '--modules', str(t / 'mods'), '--busybox', str(t / 'busybox'),
                                '--logdw', str(t / 'logdw'),
                                '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'),
                                '--out', str(t / 'ramdisk-generic.lz4')], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            _generic['data'] = (t / 'ramdisk-generic.lz4').read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _generic['data']


ROOTFS_ASSET = {'openwrt': 'mu300-openwrt-rootfs.tar.gz', 'openwrt-luci': 'mu300-openwrt-luci-rootfs.tar.gz',
                'ubuntu-24.04': 'mu300-ubuntu-rootfs.tar.gz', 'ubuntu-26.04': 'mu300-ubuntu-26.04-rootfs.tar.gz'}


class Conf(ShellTest):
    def lib(self, shell, code, **env):
        return self.sh(shell, f'MU300_LIB=1 MU300_DIR="{INSTALLER_DIR}" . "{INSTALLER}"; ' + code, **env)

    def load(self, shell, text, trust=''):
        f = self.tmp / 'mu300-install.conf'
        f.write_bytes(text.encode())
        # the installer sets -u: a key the file does not give stays unset
        return self.lib(shell, f'conf_load "{f}" {trust}; echo "S=${{MU300_STORAGE:-}} E=${{MU300_SD_ERASE:-}} '
                               'B=${MU300_BOOT:-} P=${MU300_PASSWORD:-} M=${MU300_MODE:-} D=${MU300_DEVICE:-}"')

    def test_values_quotes_crlf_comments(self):
        for shell in self.each_shell():
            r = self.load(shell, '# a comment\r\nMU300_STORAGE=sd\r\nMU300_SD_ERASE="yes"\n  MU300_BOOT = android\n'
                                 "MU300_PASSWORD='p a$s 1'\n", 'trusted')
            self.assertIn("S=sd E=yes B=android P=p a$s 1", r.stdout)

    def test_unknown_keys_are_reported_not_set(self):
        for shell in self.each_shell():
            r = self.load(shell, 'PATH=/nowhere\nMU300_STORAGE=internal\n')
            self.assertIn('S=internal', r.stdout)
            self.assertIn('PATH', r.stdout + r.stderr)   # reported
            self.assertNotEqual(r.returncode, 127)       # PATH was not replaced

    def test_conf_is_never_executed(self):
        for shell in self.each_shell():
            r = self.load(shell, f'MU300_BOOT=$(touch {self.tmp}/ran)\nMU300_STORAGE=`touch {self.tmp}/ran2`\n')
            self.assertFalse((self.tmp / 'ran').exists())
            self.assertFalse((self.tmp / 'ran2').exists())

    def test_check_refuses_bad_values(self):
        for shell in self.each_shell():
            for bad in ('MU300_STORAGE=usb', 'MU300_BOOT_ATTEMPTS=9', 'MU300_PASSWORD=short', 'MU300_MODE=maybe',
                        'MU300_PASSWORD_FILE=/etc/shadow'):
                f = self.tmp / 'c.conf'; f.write_text(bad + '\n')
                r = self.lib(shell, f'conf_load "{f}" trusted; conf_check')
                self.assertNotEqual(r.returncode, 0, bad)

    def test_values_are_checked_before_they_are_assigned(self):
        # MU300_LANG is part of t()'s file name: a later message must not be read from a file it points at
        evil = self.tmp / 'evil.tsv'
        evil.write_text('mu300-install.conf: {1} is not a setting of this installer; ignored\tPWNED\n')
        up = '../' * 12
        for shell in self.each_shell():
            for line in (f'MU300_LANG={up}{self.tmp}/evil', 'MU300_BOOT_OS=-rf', 'MU300_STORAGE=../sd',
                         'MU300_LANG=tr x'):
                r = self.load(shell, line + '\nBOGUS=1\n')
                self.assertNotEqual(r.returncode, 0, line)
                self.assertIn('is not valid', r.stdout, line)
                self.assertNotIn('PWNED', r.stdout + r.stderr, line)
            # from the environment too
            r = self.lib(shell, 'conf_check', MU300_BOOT_OS='../x')
            self.assertNotEqual(r.returncode, 0)

    def test_untrusted_file_chooses_nothing_destructive(self):
        # any app with storage access can write /sdcard: it may not erase, wipe, set the password or the model
        text = ('MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_PASSWORD=hunter22\nMU300_MODE=wipe\nMU300_DEVICE=u30air\n'
                'MU300_REGION_OVERWRITE=yes\nMU300_PASSWORD_FILE=sdcard\nMU300_PASSWORD_RESET=yes\n')
        for shell in self.each_shell():
            r = self.load(shell, text)
            self.assertIn('S=sd E= B= P= M= D=', r.stdout)
            for k in ('MU300_SD_ERASE', 'MU300_PASSWORD', 'MU300_MODE=wipe', 'MU300_DEVICE', 'MU300_REGION_OVERWRITE',
                      'MU300_PASSWORD_RESET'):
                self.assertIn(k, r.stdout)
            self.assertNotIn('hunter22', r.stdout)                       # a password's value is never shown
            self.assertIn("su -c 'cp ", r.stdout)                        # the way to make it trusted
            r = self.load(shell, 'MU300_MODE=update\n')
            self.assertIn('M=update', r.stdout)

    def test_which_file_is_trusted(self):
        f = self.tmp / 'adb.conf'
        f.write_text('x\n')
        uid = os.getuid()
        for shell in self.each_shell():
            for mode, owner, want in ((0o600, uid, 0), (0o644, uid, 0), (0o664, uid, 1), (0o646, uid, 1),
                                      (0o600, uid + 1, 1)):
                f.chmod(mode)
                r = self.lib(shell, f'conf_trusted "{f}"', MU300_TRUSTED_UID=owner)
                self.assertEqual(r.returncode, want, (oct(mode), owner))
            f.chmod(0o600)
            link = self.tmp / 'link.conf'
            link.unlink(missing_ok=True); link.symlink_to(f)
            self.assertNotEqual(self.lib(shell, f'conf_trusted "{link}"', MU300_TRUSTED_UID=uid).returncode, 0)

    def test_trusted_values_win(self):
        sd, adb = self.tmp / 'sdcard', self.tmp / 'adb'
        sd.mkdir(); adb.mkdir()
        (sd / 'mu300-install.conf').write_text('MU300_STORAGE=internal\nMU300_BOOT=android\n')
        (adb / 'mu300-install.conf').write_text('MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        (adb / 'mu300-install.conf').chmod(0o600)
        for shell in self.each_shell():
            for uid, want in ((os.getuid(), 'S=sd E=yes B=android'), (os.getuid() + 1, 'S=sd E= B=android')):
                r = self.lib(shell, 'conf_read; echo "S=$MU300_STORAGE E=${MU300_SD_ERASE:-} B=$MU300_BOOT"',
                             MU300_SDCARD=sd, MU300_TRUSTED_CONF=adb / 'mu300-install.conf', MU300_TRUSTED_UID=uid)
                self.assertIn(want, r.stdout)

    def test_every_example_line_loads_once_uncommented(self):
        # the documented way: copy the example, remove the # of a line, install again. An explanation on the key's
        # own line would become part of its value, and the value would be refused.
        sd = self.tmp / 'sd'
        sd.mkdir()
        for shell in self.each_shell():
            r = self.lib(shell, 'write_example', MU300_SDCARD=sd)
            self.assertEqual(r.returncode, 0, r.stderr)
            ex = (sd / 'mu300-install.conf.example').read_text()
            lines = ex.splitlines()
            keys = [l for l in lines if re.match(r'#MU300_[A-Z_]+=', l)]
            self.assertEqual(len(keys), 21, ex)
            for line in keys:
                i = lines.index(line)
                self.assertTrue(lines[i - 1].startswith('# '), line)          # its explanation, on a line of its own
                f = self.tmp / 'c.conf'
                f.write_text('\n'.join(lines[:i] + [line[1:]] + lines[i + 1:]) + '\n')
                r = self.lib(shell, f'conf_load "{f}" trusted; conf_check; echo LOADED')
                self.assertEqual(r.returncode, 0, (line, r.stdout + r.stderr))
                self.assertIn('LOADED', r.stdout, line)
                self.assertNotIn('not valid', r.stdout, line)

    def arm(self, shell, mode):
        """arm_linux on a fake misc whose block is LIVE, with a dd that, for MODE, fails or writes the wrong bytes
        when it writes to misc (stuck: every write to misc goes wrong, the restore too)"""
        w, adb = self.tmp / 'w', self.tmp / 'adb'
        shutil.rmtree(w, ignore_errors=True); shutil.rmtree(adb, ignore_errors=True)
        (w / 'ramdisk/etc').mkdir(parents=True); adb.mkdir()
        (w / 'ramdisk/etc/misc-bc-slot-b-trial.bin').write_bytes(b'TRIA' * 8)
        misc = self.tmp / 'misc'
        misc.write_bytes(b'\0' * 2048 + b'LIVE' * 8 + b'\0' * 2016)
        dd = ('dd() { case "$*" in *"of=$MISC"*) case "$MODE:$*" in fail:*trial*) return 1 ;; '
              'garble:*trial*|stuck:*) printf garbage | command dd of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null; '
              'return 0 ;; esac ;; esac; command dd "$@"; }; ')
        r = self.lib(shell, f'W="{w}"; MISC="{misc}"; LINUX_SLOT=b; LIVE_BC=$(hex_at "$MISC" 2048 32); misc_save; '
                            + dd + f'MODE={mode}; arm_linux; echo ARMED', MU300_WORK_PARENT=adb)
        return r, misc.read_bytes()[2048:2080]

    def test_misc_that_does_not_verify_gets_its_block_back(self):
        for shell in self.each_shell():
            r, bc = self.arm(shell, 'ok')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(bc, b'TRIA' * 8)
            for mode in ('fail', 'garble'):
                r, bc = self.arm(shell, mode)
                self.assertEqual(r.returncode, 1, (mode, r.stdout + r.stderr))
                self.assertEqual(bc, b'LIVE' * 8, mode)                       # the block it held, written back
                self.assertIn('written back', r.stdout, mode)
                self.assertIn('Android boots as before', r.stdout, mode)
            r, bc = self.arm(shell, 'stuck')
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotEqual(bc, b'LIVE' * 8)
            self.assertNotIn('Android boots as before', r.stdout)
            self.assertIn('Do not reboot', r.stdout)                           # said plainly, with what to do
            saved = self.tmp / 'adb/mu300-misc-bc-before.bin'
            self.assertEqual(saved.read_bytes(), b'LIVE' * 8)
            self.assertIn(f'if={saved} of={self.tmp}/misc', r.stdout)

    def test_cleanup_never_removes_a_mounted_filesystem(self):
        w = self.tmp / 'work'
        for shell in self.each_shell():
            for d in ('kernel', 'x/root', 'mnt'):
                (w / d).mkdir(parents=True, exist_ok=True)
            (w / 'kernel/Image').write_text('k')
            (w / 'x/root/home').write_text('precious')
            (w / 'mnt/home').write_text('precious')
            mounts = self.tmp / 'mounts'
            mounts.write_text(f'/dev/block/loop7 {w}/x/root ext4 rw 0 0\n')
            r = self.lib(shell, f'W="{w}"; SDCARD="{self.tmp}/none"; cleanup', MU300_MOUNTS=mounts)
            self.assertFalse((w / 'kernel').exists())
            self.assertEqual((w / 'x/root/home').read_text(), 'precious')
            self.assertEqual((w / 'mnt/home').read_text(), 'precious')    # a mount point is only ever rmdir'd
            self.assertIn('still mounted', r.stdout)
            # no mount table to read: nothing is removed at all
            (w / 'kernel').mkdir()
            r = self.lib(shell, f'W="{w}"; SDCARD="{self.tmp}/none"; cleanup', MU300_MOUNTS=self.tmp / 'missing')
            self.assertTrue((w / 'kernel').exists())
            # unmounted (the mount point empty again): all of it goes
            (w / 'mnt/home').unlink()
            mounts.write_text('')
            r = self.lib(shell, f'W="{w}"; SDCARD="{self.tmp}/none"; cleanup', MU300_MOUNTS=mounts)
            self.assertFalse(w.exists())
            self.assertNotIn('still mounted', r.stdout)


class AndroidSide(ShellTest):
    def test_mount_helper_read_only_on_request(self):
        card = self.tmp / 'card'
        card.write_bytes(fake_ext4_bytes('mu300sd'))
        self.stub('mount', 'echo "mount $*" >> "$STUBLOG/calls"')
        for shell in self.each_shell():
            for ro, opts in (('1', '-o ro,noload'), ('', '-o noatime')):
                (self.tmp / 'calls').unlink(missing_ok=True)
                r = self.script(shell, TOP / 'tools/android-mount-mu300root.sh', self.tmp / 'mp',
                                MU300_SD_DEV=card, MU300_RO=ro)
                self.assertIn('MOUNTED', r.stdout, r.stderr)
                self.assertIn(f'mount -t ext4 {opts} {card}', (self.tmp / 'calls').read_text())

    def test_android_install_takes_its_directory_from_the_installer(self):
        # the Magisk installer's work directory, only root can write it; /data/local/tmp for install.sh
        self.assertIn('\nT=${MU300_DEVICE_WORK:-/data/local/tmp}\n', (TOP / 'tools/android-install.sh').read_text())


@unittest.skipIf(not BUSYBOX, 'no busybox (the installer runs under Magisk\'s busybox)')
@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class InstallerCase(ShellTest):
    """a zip (mu300/ beside a real zip of payload/) and a FakeDevice; run_installer() runs install.sh as Magisk's
    busybox would"""

    def setUp(self):
        super().setUp()
        self.stub('magiskboot', MAGISKBOOT)
        self.reset()

    def reset(self):
        self.magiskboot = self.stubs / 'magiskboot'
        self.zip()
        self.device()

    def no_magiskboot(self):
        self.magiskboot = self.tmp / 'no-magiskboot'

    def fake_install_hook(self, line):
        """LINE runs inside the fake android-install.sh, before it says MU300-INSTALL-OK"""
        (self.fake.root / 'install-hook').write_text(line + '\n')

    def device(self, **kw):
        shutil.rmtree(self.tmp / 'fake', ignore_errors=True)
        self.fake = FakeDevice(self.tmp, self.stubs, **kw)
        fake_android_root(self.fake.root / 'android')
        (self.fake.root / 'magisk/busybox').symlink_to(BUSYBOX)

    IMAGE_SHADOW = 'root::19000:0:99999:7:::\nubuntu:$6$img$imagehash:19000:0:99999:7:::\n'

    def zip(self, system='openwrt', kernel='6.18', features=('sdcard',), devices='f50 u30air', corrupt=False,
            image_shadow=True, tag='v2026.10.06'):
        """mu300/ with its manifest, and the zip with the payload of SYSTEM and KERNEL"""
        self.mu300 = self.tmp / 'zip' / 'mu300'
        shutil.rmtree(self.tmp / 'zip', ignore_errors=True)
        shutil.copytree(INSTALLER_DIR, self.mu300, symlinks=True)
        self.zip_args = dict(system=system, kernel=kernel, features=features, devices=devices, image_shadow=image_shadow,
                             tag=tag)
        self.kernel_release = f'{kernel}.0-mu300' if kernel != '5.4' else '5.4.254-mu300'
        kasset = 'mu300-kernel.tar.gz' if kernel == '5.4' else f'mu300-kernel-{kernel}.tar.gz'
        rasset = ROOTFS_ASSET[system]
        os_, ubuntu = (system, '') if system.startswith('openwrt') else system.split('-')
        kb = tar_gz({'Image': b'\x7fkernel' * 1000, 'ramdisk-generic.lz4': generic_ramdisk(),
                     'modules/mu300-test.ko': b'\x7fELF test module', 'kernel.release': (self.kernel_release + '\n').encode(),
                     'devices': (devices + '\n').encode(), 'features': ''.join(f + '\n' for f in features).encode()})
        files = {'etc/os-release': f'ID={os_}\n'.encode()}
        if image_shadow:
            files['etc/shadow'] = self.IMAGE_SHADOW.encode()
        rootfs = tar_gz(files)
        (self.mu300 / 'manifest').write_text(
            f'TAG={tag}\nSYSTEM={system}\nOS={os_}\nUBUNTU={ubuntu}\nKERNEL={kernel}\nKERNEL_ASSET={kasset}\n'
            f'ROOTFS_ASSET={rasset}\nSHA256_KERNEL={hashlib.sha256(kb).hexdigest()}\n'
            f'SHA256_ROOTFS={hashlib.sha256(rootfs).hexdigest()}\n')
        self.zipfile = self.tmp / 'zip' / 'mu300-magisk.zip'
        with zipfile.ZipFile(self.zipfile, 'w') as z:
            z.writestr(f'payload/{kasset}', kb, compress_type=zipfile.ZIP_STORED)
            z.writestr(f'payload/{rasset}', rootfs + (b'x' if corrupt else b''), compress_type=zipfile.ZIP_STORED)

    def corrupt_payload(self):
        self.zip(**self.zip_args, corrupt=True)

    def existing_filesystem(self, systems=('ubuntu',), ubuntu=None):
        """an installed mu300root in the free eMMC region holding SYSTEMS (and Ubuntu's release)"""
        last_end = 2048 + (8 << 21)
        start = (last_end // 4096 + 1) * 4096
        end = (((16 << 21) - 34) // 4096 - 1) * 4096
        off = start * 512
        with open(self.fake.root / 'dev/block/mmcblk0', 'r+b') as f:
            f.truncate(16 << 30)
            f.seek(off + 1028); f.write(((end - start) * 512 // 4096).to_bytes(4, 'little'))
            f.seek(off + 1080); f.write(b'\x53\xef')
            f.seek(off + 1144); f.write(b'mu300root')
        if ubuntu and 'ubuntu' not in systems:
            systems = tuple(systems) + ('ubuntu',)
        for s in systems:
            (self.fake.root / 'fs' / s / 'etc').mkdir(parents=True, exist_ok=True)
        (self.fake.root / 'fs/home').mkdir(exist_ok=True)
        (self.fake.root / 'fs/home/precious').write_text('a user file')
        if ubuntu:
            rel = self.fake.root / 'fs/ubuntu/usr/lib/os-release'
            rel.parent.mkdir(parents=True, exist_ok=True)
            rel.write_text(f'NAME="Ubuntu"\nVERSION_ID="{ubuntu}"\n')

    def accounts(self, system, marked=False):
        """SYSTEM on the existing filesystem has accounts of its own; MARKED: still the image's
        (.mu300-accounts-from-image: an older mu300-update installed it and never carried them over)"""
        etc = self.fake.root / 'fs' / system / 'etc'
        etc.mkdir(parents=True, exist_ok=True)
        (etc / 'shadow').write_text('root:$6$theirs$root:19000::::::\nubuntu:$6$theirs$hash:19000::::::\n')
        if marked:
            (etc / '.mu300-accounts-from-image').write_text('')

    def snapshot(self):
        """what a run that writes nothing leaves as it was: the partitions, the disks, /data/local/tmp (never used)
        and /data/adb (the work directory is gone again)"""
        r = self.fake.root
        snap = {n: sha(r / 'dev/block/by-name' / n) for n in ('boot_a', 'boot_b', 'misc')}
        for d in ('mmcblk0', 'mmcblk1'):
            p = r / 'dev/block' / d
            if p.exists():
                st = p.stat()
                snap[d] = (st.st_size, st.st_mtime_ns)
        snap['local tmp'] = {str(p.relative_to(r)): sha(p) for p in sorted((r / 'tmp').rglob('*')) if p.is_file()}
        snap['data/adb'] = sorted(p.name for p in (r / 'data/adb').iterdir())
        return snap

    def nothing_written(self, except_fs=False):
        """EXCEPT_FS: the disks the Linux filesystem is on may have changed (the systems were installed)"""
        now, before = self.snapshot(), dict(self.before)
        if except_fs:
            for d in ('mmcblk0', 'mmcblk1'):
                now.pop(d, None); before.pop(d, None)
        return now == before

    def work_dirs(self):
        return sorted((self.fake.root / 'data/adb').glob('mu300-magisk.*'))

    def run_installer(self, conf=None, trusted=None, extra_env=None):
        """CONF: /sdcard/mu300-install.conf (any app can write it); TRUSTED: /data/adb/mu300-install.conf, mode 600"""
        for text, f in ((conf, self.fake.root / 'sdcard/mu300-install.conf'),
                        (trusted, self.fake.root / 'data/adb/mu300-install.conf')):
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text)
                f.chmod(0o600)
        self.before = self.snapshot()
        env = self.fake.env(self.mu300, self.magiskboot)
        env['ZIPFILE'] = self.zipfile
        env.update(extra_env or {})
        return subprocess.run([BUSYBOX, 'sh', str(self.mu300 / 'install.sh')], capture_output=True, text=True,
                              env=self.env(**env), timeout=120)


class Plan(InstallerCase):
    def test_defaults_internal_region(self):
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('internal', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_card_with_mu300sd_is_the_default(self):
        self.device(card=fake_ext4_bytes('mu300sd'))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('/mmcblk1', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_internal_while_a_mu300sd_card_is_in_the_slot_is_refused(self):
        # boot/init starts the card first: an internal installation would never start, and nobody can type the word
        self.device(card=fake_ext4_bytes('mu300sd'))
        r = self.run_installer(conf='MU300_STORAGE=internal\nMU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('mu300sd', r.stdout)
        self.assertIn('MU300_STORAGE=sd', r.stdout)      # the way out is named
        self.assertTrue(self.nothing_written())

    def test_card_is_never_erased_without_consent(self):
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(conf='MU300_STORAGE=sd\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_SD_ERASE=yes', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_hostile_sdcard_conf_erases_nothing(self):
        # any app with storage access can write /sdcard: an erase, a wipe or a password there is not taken
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(conf='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_PASSWORD=hunter22\nMU300_MODE=wipe\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('MU300_SD_ERASE', r.stdout)
        self.assertNotIn('hunter22', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_trusted_conf_allows_the_erase(self):
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(conf='MU300_DRY_RUN=1\n',
                               trusted='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_PASSWORD=hunter22\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('/mmcblk1', r.stdout)
        self.assertIn('CREATE new ext4', r.stdout)
        self.assertIn('data/adb/mu300-install.conf', r.stdout)      # the plan says where the values came from
        self.assertIn('password:       the one from ', r.stdout)
        self.assertIn('mu300-install.conf (not shown)', r.stdout)
        self.assertNotIn('hunter22', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_untrusted_storage_does_not_aim_a_standing_erase(self):
        # an erase allowed in /data/adb long ago, and a card someone put into the slot: /sdcard cannot pick it
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(conf='MU300_STORAGE=sd\nMU300_DRY_RUN=1\n', trusted='MU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('MU300_STORAGE=sd and MU300_SD_ERASE=yes', r.stdout)
        self.assertTrue(self.nothing_written())
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('CREATE new ext4', r.stdout)

    def test_untrusted_storage_does_not_aim_a_standing_region_overwrite(self):
        def dirty():
            with open(self.fake.root / 'dev/block/mmcblk0', 'r+b') as f:
                f.truncate(16 << 30)
                start = ((2048 + (8 << 21)) // 4096 + 1) * 4096 * 512
                f.seek(start + (512 << 10)); f.write(b'data' * 1024)
        for conf, trusted, rc in (('MU300_STORAGE=internal\nMU300_DRY_RUN=1\n', 'MU300_REGION_OVERWRITE=yes\n', 1),
                                  ('MU300_DRY_RUN=1\n', 'MU300_REGION_OVERWRITE=yes\n', 3),
                                  ('MU300_DRY_RUN=1\n', 'MU300_STORAGE=internal\nMU300_REGION_OVERWRITE=yes\n', 3),
                                  ('MU300_DRY_RUN=1\n', None, 1)):
            self.device(); dirty()
            r = self.run_installer(conf=conf, trusted=trusted)
            self.assertEqual(r.returncode, rc, (conf, trusted, r.stdout + r.stderr))
            if rc == 1:
                self.assertIn('MU300_STORAGE=internal and MU300_REGION_OVERWRITE=yes', r.stdout)
            self.assertTrue(self.nothing_written())

    def test_manifest_fields_are_checked_before_any_path(self):
        bad = {'ROOTFS_ASSET': '../../../etc/passwd', 'KERNEL_ASSET': '-mu300-kernel.tar.gz', 'OS': '../openwrt',
               'TAG': 'v2026/../..', 'SYSTEM': 'openwrt x', 'KERNEL': '6.18/..', 'SHA256_ROOTFS': 'A' * 64,
               'SHA256_KERNEL': '', 'UBUNTU': '24.04'}
        good = (self.mu300 / 'manifest').read_text()
        for k, v in bad.items():
            lines = [l for l in good.splitlines() if not l.startswith(k + '=')] + [f'{k}={v}']
            (self.mu300 / 'manifest').write_text('\n'.join(lines) + '\n')
            r = self.run_installer(conf='MU300_DRY_RUN=1\n')
            self.assertEqual(r.returncode, 1, (k, v, r.stdout + r.stderr))
            self.assertIn('manifest', r.stdout, k)
            self.assertNotIn('Unpacking', r.stdout, k)                    # refused before the payload is touched
            self.assertTrue(self.nothing_written(), k)
        (self.mu300 / 'manifest').write_text(good)
        self.assertEqual(self.run_installer(conf='MU300_DRY_RUN=1\n').returncode, 3)

    def test_conf_others_can_change_is_not_trusted(self):
        trusted = 'MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_DRY_RUN=1\n'
        for setup in (lambda: (self.fake.root / 'data/adb/mu300-install.conf').chmod(0o620),
                      lambda: (self.fake.root / 'data/adb/mu300-install.conf').chmod(0o606)):
            self.device(card=bytes(1 << 20), region=False)
            (self.fake.root / 'data/adb/mu300-install.conf').write_text(trusted)
            setup()
            self.before = self.snapshot()
            env = self.fake.env(self.mu300, self.stubs / 'magiskboot')
            env['ZIPFILE'] = self.zipfile
            r = subprocess.run([BUSYBOX, 'sh', str(self.mu300 / 'install.sh')], capture_output=True, text=True,
                               env=self.env(**env), timeout=120)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('not owned by root', r.stdout)
            self.assertTrue(self.nothing_written())
        # owned by someone else
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(trusted=trusted, extra_env={'MU300_TRUSTED_UID': os.getuid() + 1})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(self.nothing_written())

    def test_conf_inside_the_zip_is_trusted(self):
        self.device(card=bytes(1 << 20), region=False)
        (self.mu300 / 'mu300-install.conf').write_text('MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_DRY_RUN=1\n')
        r = self.run_installer()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('CREATE new ext4', r.stdout)

    def test_wiping_a_mu300sd_card_needs_the_erase_too(self):
        self.device(card=fake_ext4_bytes('mu300sd'))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_MODE=wipe\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('MU300_SD_ERASE=yes', r.stdout)
        self.assertTrue(self.nothing_written())
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_MODE=wipe\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('CREATE new ext4', r.stdout)

    def test_foreign_ext4_card_is_refused_even_with_consent(self):
        self.device(card=fake_ext4_bytes('photos'))
        r = self.run_installer(trusted='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('mu300sd', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_card_with_a_kernel_that_cannot_read_it(self):
        self.device(card=fake_ext4_bytes('mu300sd'))
        self.zip(features=())
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('SD card', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_no_room_no_card(self):
        self.device(region=False)
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_STORAGE=sd', r.stdout)      # the way out is named
        self.assertIn('no SD card', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_no_room_messages_point_the_right_way(self):
        # a foreign card is not offered for erasing
        self.device(region=False, card=fake_ext4_bytes('photos'))
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('never formats', r.stdout)
        self.assertNotIn('MU300_SD_ERASE', r.stdout)
        # internal chosen, too little room, a card there: the card is named, not "no SD card"
        self.device(region=False, card=bytes(1 << 20))
        r = self.run_installer(conf='MU300_STORAGE=internal\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('/mmcblk1', r.stdout)
        self.assertNotIn('no SD card', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_unknown_model(self):
        self.device(model='Pixel 7')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_DEVICE=f50', r.stdout)
        self.assertTrue(self.nothing_written())
        # from /sdcard it is not taken; from the trusted file it is
        self.assertEqual(self.run_installer(conf='MU300_DEVICE=f50\nMU300_DRY_RUN=1\n').returncode, 1)
        self.assertEqual(self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_DEVICE=f50\n').returncode, 3)

    def test_v50(self):
        """The ZTE V50 (FINDINGS 95): exactly V50 / MU3351 on ums9620, said to be experimental, only with a kernel
        that names it; never under another name, and nothing else is ever taken for one."""
        self.zip(devices='f50 u30air v50')
        self.device(model='V50', product='MU3351', platform='ums9620')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn('ZTE V50: EXPERIMENTAL', r.stdout)
        self.assertTrue(self.nothing_written())
        # a kernel that does not name it (5.4, a release from before): refused with the way out
        self.zip(devices='f50 u30air')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('The ZTE V50 runs only a mainline kernel', r.stdout)
        self.assertTrue(self.nothing_written())
        # it cannot be installed as another device
        self.zip(devices='f50 u30air v50')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_DEVICE=f50\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_DEVICE=f50 is not valid', r.stdout)
        # a V50 that is not all three, or no V50 at all with one of its names: refused outright
        for model, product, platform in (('V50', 'MU3351', 'mt6789'), ('V50', 'P671F60', 'ums9620'),
                                         ('ZTE MU3351', 'MU3351', 'ums9620'), ('V50', 'MU3351', None)):
            self.device(model=model, product=product, platform=platform)
            r = self.run_installer(conf='MU300_DRY_RUN=1\n')
            self.assertEqual(r.returncode, 1, (model, product, platform))
            self.assertIn('looks like a ZTE V50', r.stdout)
            self.assertTrue(self.nothing_written())
        # nothing is a v50 by the settings file either
        self.device(model='Pixel 7')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_DEVICE=v50\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_DEVICE=v50 is not valid', r.stdout)
        # the F50 and the U30 Air with a kernel that names the V50 too: as before
        for model in ('ZTE MU300', 'U30 Air'):
            self.device(model=model)
            r = self.run_installer(conf='MU300_DRY_RUN=1\n')
            self.assertEqual(r.returncode, 3, (model, r.stdout))
            self.assertNotIn('V50', r.stdout)

    def test_android_on_slot_b_puts_linux_on_a(self):
        self.device(slot='_b')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3)
        self.assertIn('boot_a', r.stdout)

    def test_corrupt_payload(self):
        self.corrupt_payload()
        self.assertEqual(self.run_installer().returncode, 1)
        self.assertTrue(self.nothing_written())

    def test_ubuntu_2604_and_a_54_zip(self):
        self.existing_filesystem(ubuntu='26.04')
        self.zip(kernel='5.4', system='openwrt')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('26.04', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_example_conf_written(self):
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        ex = (self.fake.root / 'sdcard/mu300-install.conf.example').read_text()
        for k in ('MU300_STORAGE', 'MU300_SD_ERASE', 'MU300_BOOT', 'MU300_PASSWORD', 'MU300_DRY_RUN'):
            self.assertIn(k, ex)
        self.assertRegex(ex, r'# \(root\)[^\n]*\n#MU300_SD_ERASE=yes\n')  # which keys need the trusted file
        self.assertIn('data/adb/mu300-install.conf', ex)

    def test_plan_names_the_source_of_every_value(self):
        self.existing_filesystem(systems=('openwrt',))
        conf = ('MU300_DRY_RUN=1\nMU300_MODE=update\nMU300_BOOT=linux\nMU300_BOOT_ATTEMPTS=3\nMU300_HOTSPOT=no\n'
                'MU300_GPU=no\nMU300_BOOT_OS=openwrt\nMU300_STORAGE=internal\n')
        r = self.run_installer(conf=conf)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        plan = r.stdout.split('Plan', 1)[1]
        for label in ('storage:', 'filesystem:', 'boots:', 'default boot:', 'boot attempts:', 'hotspot:', 'GPU files:'):
            [line] = [l for l in plan.splitlines() if l.strip().startswith(label)]
            self.assertIn('sdcard/mu300-install.conf', line, line)
        self.assertRegex(plan, r'boot attempts: +3 ')
        self.assertRegex(plan, r'hotspot: +not copied')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        plan = r.stdout.split('Plan', 1)[1]
        for label in ('default boot:', 'boot attempts:', 'hotspot:', 'GPU files:'):
            [line] = [l for l in plan.splitlines() if l.strip().startswith(label)]
            self.assertIn('(default)', line, line)

    def version(self, system, tag):
        d = self.fake.root / 'fs' / system / 'etc/mu300'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'image-version').write_text(tag + '\n')

    def test_an_update_says_what_it_keeps_and_what_it_leaves_alone(self):
        # the zip carries one system: an installed one of its kind is updated, with everything of the user's kept
        # (android-install.sh runs the new image's carry block); the others and the extras are not its to touch
        self.existing_filesystem(systems=('openwrt', 'ubuntu'))
        self.version('openwrt', 'v2026.10.01')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('keep: nothing is erased', r.stdout)
        self.assertRegex(r.stdout, r'update: +openwrt v2026\.10\.01 -> v2026\.10\.06: everything you added to it or changed in it is kept')
        self.assertIn('/etc/mu300/update-report', r.stdout)
        self.assertRegex(r.stdout, r'not touched: +ubuntu and the extras \(the VPN module\) stay as they are')
        self.assertTrue(self.nothing_written())
        # a system that is not there yet is installed beside the others: nothing to update, the others stay
        self.reset()
        self.zip(system='openwrt', kernel='6.18')
        self.existing_filesystem(systems=('ubuntu',))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotRegex(r.stdout, r'(?m)^ +update: ')
        self.assertRegex(r.stdout, r'not touched: +ubuntu and the extras')
        # and the example file says the same, and what wipe erases
        text = (self.fake.root / 'sdcard/mu300-install.conf.example').read_text()
        self.assertIn('everything you added to it or', text)
        self.assertIn('The other installed systems and the extras (the VPN module) are not touched.', text)
        self.assertIn('wipe (root): the Linux filesystem is made anew. Every installed system, every file in them and the extras', text)
        for key in ('#MU300_MODE=update', '#MU300_DOWNGRADE=yes', '#MU300_UPDATE_KEEP=all'):
            self.assertIn('\n' + key + '\n', text)

    def test_never_back_to_an_older_release_by_itself(self):
        # the zip is v2026.10.06, the installed system of its kind is newer: an update to an older release would be
        # done by the older image, which may keep only the list of places. Refused, with nothing written.
        self.existing_filesystem(systems=('openwrt',))
        self.version('openwrt', 'v2026.10.25')
        for conf in ('MU300_DRY_RUN=1\n', 'MU300_DRY_RUN=1\nMU300_DOWNGRADE=yes\n', ''):        # /sdcard cannot allow it
            r = self.run_installer(conf=conf)
            self.assertNotIn(r.returncode, (0, 3), r.stdout + r.stderr)
            self.assertIn('The installed openwrt is v2026.10.25, and this zip is v2026.10.06, an older release.', r.stdout + r.stderr)
            self.assertIn('MU300_DOWNGRADE=yes', r.stdout + r.stderr)
            self.assertTrue(self.nothing_written())
        # a file only root can change allows it, and the plan says what that means; it counts for this install only
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_DOWNGRADE=yes\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('v2026.10.06 is the OLDER release (MU300_DOWNGRADE=yes)', r.stdout)
        r = self.run_installer(trusted='MU300_DOWNGRADE=yes\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # android-install.sh checks the release order again before it writes anything (its downgrade block): it is
        # told that this one was allowed
        self.assertIn('DOWNGRADE=1\n', (self.fake.root / 'install.env').read_text())
        self.assertNotRegex((self.fake.root / 'data/adb/mu300-install.conf').read_text(), r'(?m)^\s*MU300_DOWNGRADE\s*=')
        # not an older release: the same one, a newer zip, a system that does not say, another system's version
        for system, tag in (('openwrt', 'v2026.10.06'), ('openwrt', 'v2026.10.05'), ('openwrt', 'dev'), ('ubuntu', 'v2026.10.25')):
            self.reset()
            self.zip(system='openwrt', kernel='6.18')
            self.existing_filesystem(systems=('openwrt', 'ubuntu'))
            self.version(system, tag)
            r = self.run_installer(conf='MU300_DRY_RUN=1\n')
            self.assertEqual(r.returncode, 3, (system, tag, r.stdout + r.stderr))
        # and a wipe is not an update
        self.version('openwrt', 'v2026.10.25')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted='MU300_MODE=wipe\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_the_release_order_is_the_tags_not_their_text(self):
        # docs/FINDINGS.md 125: every vYYYY.MM.DD.N is newer than every vYYYY.MM.DD, and N is a number - text order
        # gets both wrong (v2026.10.27 > v2026.10.16.1, v2026.10.16.2 > v2026.10.16.10)
        for zip_tag, installed, older in (('v2026.10.16.1', 'v2026.10.27', False), ('v2026.10.27', 'v2026.10.16.1', True),
                                          ('v2026.10.16.10', 'v2026.10.16.2', False), ('v2026.10.16.2', 'v2026.10.16.10', True),
                                          ('v2026.10.17.1', 'v2026.10.16.3', False), ('v2026.10.16.1', 'v2026.10.16.1', False)):
            self.reset()
            self.zip(system='openwrt', kernel='6.18', tag=zip_tag)
            self.existing_filesystem(systems=('openwrt',))
            self.version('openwrt', installed)
            r = self.run_installer(conf='MU300_DRY_RUN=1\n')
            if older:
                self.assertNotIn(r.returncode, (0, 3), (zip_tag, installed, r.stdout + r.stderr))
                self.assertIn(f'The installed openwrt is {installed}, and this zip is {zip_tag}, an older release.',
                              r.stdout + r.stderr)
                self.assertTrue(self.nothing_written())
            else:
                self.assertEqual(r.returncode, 3, (zip_tag, installed, r.stdout + r.stderr))
                self.assertNotIn('an older release', r.stdout + r.stderr)

    def test_keeping_only_the_list_is_for_root_to_ask(self):
        # MU300_UPDATE_KEEP=list (the update as it was before the first release with a four-field tag) gives up files of the user: only a file
        # root owns may say it; android-install.sh is told through its environment
        keep = self.fake.root / 'update-keep'
        self.fake_install_hook('echo "${MU300_UPDATE_KEEP:-unset}" > "$FAKE/update-keep"')
        self.existing_filesystem(systems=('openwrt',))
        r = self.run_installer(conf='MU300_UPDATE_KEEP=list\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(keep.read_text(), 'all\n')
        self.assertIn('everything you added to it or changed in it is kept', r.stdout)
        r = self.run_installer(trusted='MU300_UPDATE_KEEP=list\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(keep.read_text(), 'list\n')
        self.assertIn('only settings, users and home directories are kept (MU300_UPDATE_KEEP=list)', r.stdout)
        # for this install only
        self.assertNotRegex((self.fake.root / 'data/adb/mu300-install.conf').read_text(), r'(?m)^\s*MU300_UPDATE_KEEP\s*=')

    def test_plan_names_an_installed_openwrt_luci(self):
        # the third system (OpenWrt with the control panel) is one the existing filesystem can hold: it is counted
        # for the space check and named in the plan like the other two
        self.existing_filesystem(systems=('openwrt-luci', 'ubuntu'))
        r = self.run_installer(conf='MU300_DRY_RUN=1\nMU300_MODE=update\nMU300_STORAGE=internal\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        [line] = [l for l in r.stdout.splitlines() if 'already there:' in l]
        self.assertIn('openwrt-luci', line)
        self.assertIn('ubuntu', line)
        # and it may be the one that boots
        r = self.run_installer(conf='MU300_DRY_RUN=1\nMU300_MODE=update\nMU300_STORAGE=internal\nMU300_BOOT_OS=openwrt-luci\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'boots: +openwrt-luci')

    def test_example_conf_is_never_written_through_a_link(self):
        victim = self.tmp / 'victim'
        victim.write_text('not yours')
        ex = self.fake.root / 'sdcard/mu300-install.conf.example'
        ex.symlink_to(victim)
        self.assertEqual(self.run_installer(conf='MU300_DRY_RUN=1\n').returncode, 3)
        self.assertEqual(victim.read_text(), 'not yours')
        self.assertFalse(ex.is_symlink())
        self.assertIn('MU300_STORAGE', ex.read_text())
        self.assertEqual(sorted(p.name for p in ex.parent.iterdir()), ['mu300-install.conf', 'mu300-install.conf.example'])

    def test_example_conf_written_on_refusal(self):
        self.device(model='Pixel 7')
        self.assertEqual(self.run_installer().returncode, 1)
        self.assertIn('MU300_DEVICE', (self.fake.root / 'sdcard/mu300-install.conf.example').read_text())


class WorkDirectory(InstallerCase):
    """everything checked and then used lives in one directory of this run that only root can write (S1), and no
    filesystem mounted in it is ever deleted (C1)"""

    def test_local_tmp_is_never_used(self):
        # what the shell user could put into /data/local/tmp for root to run or install
        lt = self.fake.root / 'tmp'
        for n in ('android-install.sh', 'android-mount-mu300root.sh'):
            (lt / n).write_text(f'touch "{self.tmp}/ran-{n}"\n')
        (lt / 'mu300-openwrt.tar.gz').write_bytes(b'not the payload')
        (lt / 'mu300-install.env').write_text('FORMAT=1\n')
        self.existing_filesystem(systems=('openwrt',))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(self.nothing_written())                        # /data/local/tmp as it was, work dir gone
        self.assertEqual(list(self.tmp.glob('ran-*')), [])
        log = (self.fake.root / 'android-sh.log').read_text().splitlines()
        mount = [l for l in log if 'android-mount-mu300root.sh' in l and ' -u ' not in l]
        self.assertEqual(len(mount), 1, log)
        work = str(Path(os.path.realpath(self.fake.root / 'data/adb')) / 'mu300-magisk.')
        self.assertIn(f' {work}', mount[0])                           # the helper copied into the work directory
        self.assertTrue(mount[0].endswith(' drwx------'), mount[0])    # which only root can enter
        self.assertIn('MU300_RO=1', mount[0])                          # inspected read-only

    def test_failed_unmount_keeps_the_filesystem(self):
        self.existing_filesystem(systems=('openwrt',))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n', extra_env={'FAKE_UMOUNT_FAILS': '1'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('still mounted', r.stdout)
        [w] = self.work_dirs()
        self.assertEqual(sorted(p.name for p in w.iterdir()), ['mnt'])     # payload and helper gone, the mount kept
        self.assertEqual((w / 'mnt/home/precious').read_text(), 'a user file')

    def test_stale_mount_of_an_earlier_run_is_never_touched(self):
        # an earlier run killed while its filesystem was mounted (no EXIT trap ran)
        old = Path(os.path.realpath(self.fake.root / 'data/adb')) / 'mu300-magisk.EARLY1'
        (old / 'mnt/home').mkdir(parents=True)
        (old / 'mnt/home/precious').write_text('a user file')
        (self.fake.root / 'mounts').write_text(f'/dev/block/loop7 {old}/mnt ext4 rw 0 0\n')
        self.existing_filesystem(systems=('openwrt',))
        for extra, rc in (({'FAKE_MOUNT_FAILS': '1'}, 1), ({}, 3)):     # the helper refuses a second mount, or not
            r = self.run_installer(conf='MU300_DRY_RUN=1\n', extra_env=extra)
            self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
            self.assertEqual((old / 'mnt/home/precious').read_text(), 'a user file')
            self.assertTrue(self.nothing_written())


class Install(InstallerCase):
    """the whole installation: the systems, then boot_<linux slot> written and verified, then misc armed"""

    def linux_image(self, slot):
        img = (self.fake.root / 'dev/block/by-name' / f'boot_{slot}').read_bytes()
        self.assertEqual(img[:8], b'ANDROID!')
        # the marker the Android-side switch looks for before it arms a slot
        self.assertEqual(img[44:44 + 1536], b'loglevel=5'.ljust(1536, b'\0'))
        return cpio_all(unlz4_legacy(ramdisk_of(img)))

    # the images' well-known passwords (issue #167: a random one shown in nothing but a root-only file, which the next
    # zip overwrote, left systems nobody could log in to): what the report says, and the note in the password file
    UBUNTU_HASH = "$" + b'ubuntu'.hex() + "'"              # the fake mkpasswd's hash of "ubuntu" after the salt, as install.env quotes it
    SHOWN = {'ubuntu': 'password for ubuntu: "ubuntu"   (as in the images; change it after the first login: passwd)',
             'openwrt': 'password for root: none yet - set one with passwd, or in LuCI (System > Administration)'}
    NOTE = {'ubuntu': 'user: ubuntu\npassword: ubuntu (as in the images; change it after the first login: passwd)\n',
            'openwrt': 'user: root\npassword: none yet: set one with passwd, or in LuCI (System > Administration)\n'}
    PLAN = {'ubuntu': 'password:       ubuntu for user ubuntu, as in the images (MU300_PASSWORD in {conf} sets another)',
            'openwrt': 'password:       none: root logs in without one until one is set with passwd or in LuCI, System > Administration '
                       '(MU300_PASSWORD in {conf} sets one now)'}

    def conf_path(self):
        """the trusted conf as the installer names it (the fake device's path)"""
        return str(self.fake.root / 'data/adb/mu300-install.conf')

    def images_password(self, r, os_, pw_file='data/adb/mu300-linux-password.txt'):
        """R gave OS_ (ubuntu, openwrt) the images' password: ubuntu/ubuntu, OpenWrt's root without one. The hash (or
        none) goes to android-install.sh with PW_SET=1; the plan, the report and the file say so"""
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        kind = 'ubuntu' if os_ == 'ubuntu' else 'openwrt'
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('PW_SET=1\n', env)
        if kind == 'ubuntu':
            self.assertRegex(env, r"PWHASH='\$6\$[./0-9A-Za-z]{16}\$")
            self.assertIn(self.UBUNTU_HASH, env)
        else:
            self.assertIn("PWHASH=''", env)
        self.assertIn(self.PLAN[kind].format(conf=self.conf_path()), r.stdout)
        self.assertIn(self.SHOWN[kind], r.stdout)
        self.assertNotIn('the one from', r.stdout)
        f = self.fake.root / pw_file
        self.assertIn(self.NOTE[kind], f.read_text())
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    def test_v50_install(self):
        # the ZTE V50 (FINDINGS 95): its boot image names it, so init takes it as one; its own USB address
        self.zip(devices='f50 u30air v50')
        self.device(model='V50', product='MU3351', platform='ums9620')
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = self.linux_image('b')
        self.assertEqual(files['etc/mu300-device'][1], b'v50\n')
        self.assertIn('192.168.79.1', r.stdout)
        self.assertNotIn('192.168.77.1', r.stdout)

    def test_full_install_internal(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_a'), self.before['boot_a'])            # Android's partition untouched
        files = self.linux_image('b')
        self.assertEqual(files['etc/mu300-linux-slot'][1], b'b\n')
        self.assertEqual(files['etc/mu300-device'][1], b'f50\n')
        self.assertIn('android/vendor/bin/modem_control', files)
        self.assertIn('init', files)                                           # from the bundle's generic segment
        bc = (by / 'misc').read_bytes()[0x800:0x820]
        self.assertEqual(bc, files['etc/misc-bc-slot-b-trial.bin'][1])         # armed: Linux trial on b
        env = (self.fake.root / 'install.env').read_text()
        for line in ('FORMAT=1', 'OSES="openwrt"', 'SD_MODE=0', 'DEFAULT_LINUX=1', 'BOOT_ATTEMPTS=5', 'KERNEL=6.18'):
            self.assertIn(line, env)
        # OpenWrt's root without a password, as the image ships it; the note in root's file by default, mode 600,
        # nothing on the shared storage
        self.images_password(r, 'openwrt')
        self.assertFalse((self.fake.root / 'sdcard/mu300-linux-password.txt').exists())
        self.assertEqual(self.work_dirs(), [])                                 # proprietary staging gone

    def test_full_install_openwrt_with_the_control_panel(self):
        # the panel system has a zip of its own: installed under its own name, and the one that boots
        self.zip(system='openwrt-luci')
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = (self.fake.root / 'install.env').read_text()
        for line in ('OSES="openwrt-luci"', 'BOOT_OS=openwrt-luci', 'FORMAT=1'):
            self.assertIn(line, env)
        self.assertIn('android/vendor/bin/modem_control', self.linux_image('b'))

    def test_report_names_the_vpn_extra(self):
        # the zip carries no VPN (the module of dikeckaan/mu300-linux-vpn): the report says how to add it
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('The VPN (mu300-vpn, with Xray and sing-box) is a module of its own: '
                      'https://github.com/dikeckaan/mu300-linux-vpn.', r.stdout)
        self.assertIn('sudo mu300-extra install vpn', r.stdout)

    def test_slot_b_android(self):
        self.device(slot='_b')
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_b'), self.before['boot_b'])            # Android's partition untouched
        files = self.linux_image('a')
        self.assertEqual(files['etc/mu300-linux-slot'][1], b'a\n')
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], files['etc/misc-bc-slot-a-trial.bin'][1])

    def test_sd_card_install(self):
        self.device(card=bytes(1 << 20))
        r = self.run_installer(trusted='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_a'), self.before['boot_a'])
        files = self.linux_image('b')
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], files['etc/misc-bc-slot-b-trial.bin'][1])
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('SD_MODE=1', env)
        self.assertIn('SD_DEV=' + str(self.fake.root / 'dev/block/mmcblk1'), env)
        # OFF/SIZE stay the internal region, as install.sh writes them (the root-on-sd marker needs it)
        start = ((2048 + (8 << 21)) // 4096 + 1) * 4096
        end = (((16 << 21) - 34) // 4096 - 1) * 4096
        self.assertIn(f'OFF={start * 512}\nSIZE={(end - start) * 512}\n', env)
        self.assertIn('FORMAT=1', env)

    def test_android_side_runs_with_android_tools(self):
        self.assertEqual(self.run_installer().returncode, 0)
        for line in (self.fake.root / 'android-sh.log').read_text().splitlines():
            self.assertTrue(line.startswith('ASH_STANDALONE= '), line)

    def test_install_failure_leaves_boot_and_misc(self):
        r = self.run_installer(extra_env={'FAKE_INSTALL_FAILS': '1'})
        self.assertEqual(r.returncode, 1)
        self.assertIn('boot_b and misc were not changed', r.stdout)
        (self.fake.root / 'data/adb/mu300-linux-password.txt').unlink()      # saved first (the next test)
        (self.fake.root / 'data/adb/mu300-device-install.log').unlink()      # kept for a look (the test below)
        self.assertTrue(self.nothing_written(except_fs=True))

    def test_failed_device_install_keeps_its_log(self):
        # the work directory goes with the run: the device script's output is kept where root can read it, and the
        # message says where (a timed-out SD-card stage names itself there, FINDINGS 50)
        r = self.run_installer(extra_env={'FAKE_INSTALL_FAILS': '1'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        log = self.fake.root / 'data/adb/mu300-device-install.log'
        self.assertIn('[device] failing as asked', log.read_text())
        self.assertIn(f'the device log was kept at {log}', r.stdout)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        # a run that goes through leaves no log of a failure behind
        self.device()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(log.exists())

    def test_android_install_gets_the_lock_directory(self):
        # the lock of a timed-out SD-card stage must outlive the work directory of this run: it goes to /data/adb
        self.assertEqual(self.run_installer().returncode, 0)
        lines = [l for l in (self.fake.root / 'android-sh.log').read_text().splitlines() if 'android-install.sh' in l]
        self.assertTrue(lines)
        for l in lines:
            self.assertIn(f'MU300_LOCK_DIR={self.fake.root}/data/adb', l)

    def test_a_timed_out_run_in_this_boot_is_refused(self):
        # android-install.sh left the lock of this Android boot (an SD-card stage timed out, its request still stuck
        # in the kernel): refused before anything is built or written, with the stage and the way out
        lock = self.fake.root / 'data/adb/mu300-install.stalled'
        lock.write_text('11111111-2222-3333-4444-555555555555\nstage unpack openwrt, 2026-10-12 13:00: no I/O progress on mmcblk1 for 120 s\n')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('An earlier run of this installer timed out in this Android boot (stage unpack openwrt, 2026-10-12 13:00: '
                      'no I/O progress on mmcblk1 for 120 s)', r.stdout)
        self.assertIn('Reboot Android, then install the zip again', r.stdout)
        self.assertIn(str(lock), r.stdout)
        self.assertNotIn('Installing', r.stdout)
        self.assertNotIn('Plan', r.stdout)
        self.assertFalse((self.fake.root / 'install.env').exists())
        self.assertTrue(lock.exists())
        self.assertTrue(self.nothing_written())
        # Android has rebooted since (another boot id): the lock is stale, the installation goes through, the lock goes
        lock.write_text('99999999-0000-0000-0000-000000000000\nstage format\n')
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(lock.exists())

    def test_boot_readback_mismatch_is_not_armed(self):
        # the image is read back from the partition into a file (whole 4096-byte blocks, one dd whose status counts)
        # and compared by sha256: a difference refuses to arm Linux - misc stays, Android keeps booting
        real = shutil.which('dd')
        self.stub('dd', f'rb=; for a in "$@"; do case $a in of=*/boot/readback) rb=${{a#of=}} ;; esac; done\n'
                        f'"{real}" "$@" || exit $?\n'
                        f'[ -z "$rb" ] || printf X | "{real}" of="$rb" bs=1 seek=4100 conv=notrunc 2>/dev/null')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('boot_b read back from the eMMC is not the image that was written (sha256 differs); '
                      'Linux was not armed: misc was not changed, so Android keeps booting', r.stdout)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'misc'), self.before['misc'])
        self.assertEqual(sha(by / 'boot_a'), self.before['boot_a'])
        self.assertNotEqual(sha(by / 'boot_b'), self.before['boot_b'])        # written, verified wrong, never armed
        self.assertNotIn('Done. Reboot', r.stdout)
        self.assertEqual(self.work_dirs(), [])
        # a read that fails outright is the same refusal, with the reason
        self.device()
        self.stub('dd', f'for a in "$@"; do case $a in of=*/boot/readback) exit 1 ;; esac; done\nexec "{real}" "$@"')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('is not the image that was written (dd failed)', r.stdout)
        self.assertEqual(sha(by / 'misc'), self.before['misc'])

    def test_boot_readback_is_a_file_of_whole_blocks(self):
        # the shape of the read-back: exactly the image's length in 4096-byte blocks, into a file, no pipeline on the
        # partition; mu300-update's write_boot (its own check) is called first
        s = (TOP / 'android/magisk/installer/mu300-install.sh').read_text()
        fn = re.search(r'^boot_readback\(\) \{.*?^\}\n', s, re.S | re.M).group(0)
        self.assertIn('dd if="$1" of="$W/boot/readback" bs=4096 count="$_bn"', fn)
        self.assertNotRegex(fn, r'dd if="\$1"[^\n]*[^|]\|[^|]')          # a |, never a ||
        self.assertIn('echo 3 > /proc/sys/vm/drop_caches', fn)
        ib = re.search(r'^install_boot\(\) \{.*?^\}\n', s, re.S | re.M).group(0)
        self.assertLess(ib.index('write_boot '), ib.index('boot_readback '))
        main = s[s.index('main() {'):]
        self.assertLess(main.index('install_boot'), main.index('arm_linux'))

    def test_password_is_saved_before_anything_is_written(self):
        # android-install.sh puts the password into the systems: a run that fails or is killed after that must not
        # leave a Linux whose password nobody was told
        r = self.run_installer(extra_env={'FAKE_INSTALL_FAILS': '1'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(self.SHOWN['openwrt'], r.stdout)
        self.assertLess(r.stdout.index('password for'), r.stdout.index('Installing'))
        f = self.fake.root / 'data/adb/mu300-linux-password.txt'
        self.assertIn(self.NOTE['openwrt'], f.read_text())
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        # a password file that cannot be written stops the run before anything is installed
        self.device()
        (self.fake.root / 'sdcard/mu300-linux-password.txt').mkdir()
        r = self.run_installer(trusted='MU300_PASSWORD_FILE=sdcard\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('could not write', r.stdout)
        self.assertNotIn('Installing', r.stdout)
        self.assertFalse((self.fake.root / 'install.env').exists())
        self.assertTrue(self.nothing_written())

    def test_destructive_keys_are_used_once(self):
        # a standing wipe, erase or overwrite in /data/adb would apply to every later flash: once used, each becomes
        # a comment, the way a used password does
        conf = self.fake.root / 'data/adb/mu300-install.conf'
        self.existing_filesystem(systems=('openwrt',))
        r = self.run_installer(trusted='MU300_MODE=wipe\nMU300_BOOT_ATTEMPTS=3\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('FORMAT=1', (self.fake.root / 'install.env').read_text())
        text = conf.read_text()
        self.assertNotRegex(text, r'(?m)^\s*MU300_MODE')
        self.assertIn('# MU300_MODE was used by the installer and removed', text)
        self.assertIn('MU300_BOOT_ATTEMPTS=3\n', text)
        self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
        self.assertRegex(r.stdout, r'MU300_MODE.*removed|removed.*MU300_MODE')     # the report says so
        r = self.run_installer(trusted=text)                                  # the next flash is an update
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('FORMAT=0', (self.fake.root / 'install.env').read_text())
        # the card's erase: once
        self.device(card=bytes(1 << 20))
        r = self.run_installer(trusted='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = conf.read_text()
        self.assertIn('MU300_STORAGE=sd\n', text)
        self.assertNotRegex(text, r'(?m)^\s*MU300_SD_ERASE')
        self.assertIn('# MU300_SD_ERASE was used by the installer and removed', text)
        # an erase not used (an update of a mu300sd card) stays
        self.device(card=fake_ext4_bytes('mu300sd'))
        r = self.run_installer(trusted='MU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(conf.read_text(), 'MU300_SD_ERASE=yes\n')
        # the region overwrite: once
        self.device()
        with open(self.fake.root / 'dev/block/mmcblk0', 'r+b') as f:
            f.truncate(16 << 30)
            f.seek(((2048 + (8 << 21)) // 4096 + 1) * 4096 * 512 + (512 << 10)); f.write(b'data' * 1024)
        r = self.run_installer(trusted='MU300_REGION_OVERWRITE=yes\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('# MU300_REGION_OVERWRITE was used by the installer and removed', conf.read_text())

    def test_a_wipe_counts_for_one_install_whatever_the_target_held(self):
        # MU300_MODE=wipe in /data/adb is used by the install that formats, also when the target had no filesystem
        # yet (a blank region, a new card): left standing, the next flash (the second zip, an update) would wipe
        # the system this one installed
        conf = self.fake.root / 'data/adb/mu300-install.conf'
        cases = (  # name, card before, trusted conf, the target as the first install leaves it
            ('blank region', None, 'MU300_MODE=wipe\n', lambda: self.existing_filesystem(systems=('openwrt',))),
            ('installed region', 'region', 'MU300_MODE=wipe\n', lambda: None),
            ('new card', bytes(1 << 20), 'MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_MODE=wipe\n',
             lambda: self.device(card=fake_ext4_bytes('mu300sd'))),
            ('mu300sd card', fake_ext4_bytes('mu300sd'), 'MU300_SD_ERASE=yes\nMU300_MODE=wipe\n',
             lambda: self.device(card=fake_ext4_bytes('mu300sd'))))
        for name, card, trusted, formatted in cases:
            with self.subTest(name):
                self.reset()
                if card == 'region':
                    self.existing_filesystem(systems=('openwrt',))
                elif card is not None:
                    self.device(card=card)
                r = self.run_installer(trusted=trusted)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn('FORMAT=1', (self.fake.root / 'install.env').read_text())
                text = conf.read_text()
                for k in ('MU300_MODE', 'MU300_SD_ERASE'):
                    self.assertNotRegex(text, rf'(?m)^\s*{k}\s*=')
                self.assertIn('# MU300_MODE was used by the installer and removed', text)
                formatted()
                # the next flash, with the conf as the first one left it: an update, nothing formatted
                r = self.run_installer(conf='MU300_DRY_RUN=1\n', trusted=text)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertNotIn('CREATE new ext4', r.stdout)
                self.assertIn('keep: nothing is erased', r.stdout)
                self.assertTrue(self.nothing_written())

    def test_misc_changed_meanwhile_is_not_armed(self):
        # a stub android-install.sh that changes misc while "installing" (an OTA, another installer)
        self.fake_install_hook('printf X | dd of="$FAKE/dev/block/by-name/misc" bs=1 seek=2060 conv=notrunc 2>/dev/null')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('misc changed during the installation', r.stdout)
        self.assertNotEqual((self.fake.root / 'dev/block/by-name/misc').read_bytes()[0x800:0x802], b'_b')

    def test_refusals_write_nothing(self):
        for setup in (lambda: self.device(region=False), lambda: self.corrupt_payload(),
                      lambda: self.device(model='Pixel 7'), lambda: self.no_magiskboot()):
            self.reset(); setup()
            self.assertEqual(self.run_installer().returncode, 1)
            self.assertTrue(self.nothing_written())

    def test_password_from_conf_is_used_and_removed(self):
        conf = self.fake.root / 'data/adb/mu300-install.conf'
        r = self.run_installer(trusted='MU300_BOOT_ATTEMPTS=3\nMU300_PASSWORD=correct horse\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # hashed, blank and all; never shown, never written down: the report and the file name where it came from
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('correct horse'.encode().hex()[:20], env)
        self.assertIn('PW_SET=1\n', env)
        self.assertIn('DOWNGRADE=0\n', env)
        self.assertNotIn('correct horse', r.stdout)
        self.assertIn(f'password:       the one from {self.conf_path()} (not shown)', r.stdout)
        self.assertIn(f'password for root: the one from {self.conf_path()}', r.stdout)
        self.assertNotIn('none yet', r.stdout)
        f = self.fake.root / 'data/adb/mu300-linux-password.txt'
        self.assertNotIn('correct horse', f.read_text())
        self.assertIn(f'user: root\npassword: the one given as MU300_PASSWORD in {self.conf_path()}\n', f.read_text())
        text = conf.read_text()
        self.assertNotIn('correct horse', text)
        self.assertIn('MU300_BOOT_ATTEMPTS=3\n', text)                         # the other settings stay
        self.assertIn('# MU300_PASSWORD was used by the installer and removed', text)
        self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
        self.assertFalse(conf.is_symlink())
        self.assertEqual(sorted(p.name for p in conf.parent.iterdir()),
                         ['mu300-install.conf', 'mu300-linux-password.txt'])
        # a conf inside the zip is never edited
        (self.mu300 / 'mu300-install.conf').write_text('MU300_PASSWORD=battery staple\n')
        self.assertEqual(self.run_installer().returncode, 0)
        self.assertIn('battery staple', (self.mu300 / 'mu300-install.conf').read_text())

    def test_password_file_on_the_shared_storage_only_when_trusted(self):
        r = self.run_installer(conf='MU300_PASSWORD_FILE=sdcard\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.fake.root / 'sdcard/mu300-linux-password.txt').exists())
        self.assertTrue((self.fake.root / 'data/adb/mu300-linux-password.txt').exists())
        self.device()
        r = self.run_installer(trusted='MU300_PASSWORD_FILE=sdcard\n')
        self.images_password(r, 'openwrt', pw_file='sdcard/mu300-linux-password.txt')
        self.assertFalse((self.fake.root / 'data/adb/mu300-linux-password.txt').exists())

    def test_password_file_is_never_written_through_a_link(self):
        victim = self.tmp / 'victim'
        victim.write_text('not yours')
        f = self.fake.root / 'sdcard/mu300-linux-password.txt'
        f.symlink_to(victim)
        r = self.run_installer(trusted='MU300_PASSWORD_FILE=sdcard\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(victim.read_text(), 'not yours')
        self.assertFalse(f.is_symlink())
        self.assertIn(self.NOTE['openwrt'], f.read_text())

    def test_second_zip_keeps_the_first_systems_password(self):
        # Both systems are two zips. The second one installs beside the first and leaves that system's password as
        # it was, so the file must keep its note: the first zip's output may be gone, and the file is the only other
        # place (seen on the F50: the Ubuntu zip's file replaced the OpenWrt root password). Each gets the images'
        # password of its own (issue #167: a random one nobody saw).
        f = self.fake.root / 'data/adb/mu300-linux-password.txt'
        r = self.run_installer()
        self.images_password(r, 'openwrt')
        self.existing_filesystem(systems=('openwrt',))
        self.zip(system='ubuntu-24.04', kernel='5.4')
        r = self.run_installer()
        self.images_password(r, 'ubuntu')
        text = f.read_text()
        self.assertIn(self.NOTE['openwrt'], text)
        self.assertTrue(text.startswith('MU300 Linux '))                       # the newest first
        self.assertLess(text.index('user: ubuntu'), text.index('user: root'))
        # the first system again with its password given (an update: in the zip): the note replaces its old one, the
        # other one stays
        self.zip(system='openwrt')
        top = self.zip_conf('MU300_PASSWORD=correct horse\n')
        r = self.run_installer(extra_env={'MU300_ZIP_CONF': top})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = f.read_text()
        self.assertEqual(text.count('user: root\n'), 1, text)
        self.assertEqual(text.count('user: ubuntu\n'), 1, text)
        self.assertIn(f'user: root\npassword: the one given as MU300_PASSWORD in {self.ZIP_CONF}\n', text)
        self.assertIn(self.NOTE['ubuntu'], text)
        self.assertNotIn(self.NOTE['openwrt'], text)
        self.assertNotIn('correct horse', text)
        # a wipe leaves one system: no note of the erased one
        r = self.run_installer(trusted='MU300_MODE=wipe\n')
        self.images_password(r, 'openwrt')
        text = f.read_text()
        self.assertNotIn('user: ubuntu', text)
        self.assertEqual(text.count('user: root\n'), 1, text)

    def kept(self, r, users, checked=False):
        """R kept the password: no hash for android-install.sh, no password shown, the plan and report say so -
        CHECKED: the current one was confirmed (MU300_KEEP_PASSWORD=yes in the zip), else kept without a check"""
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn("PWHASH=''", env)
        self.assertIn('PW_SET=0\n', env)
        os_ = self.zip_args['system'].partition('-')[0]
        self.assertIn(f'KEEP_PW_OSES="{os_}"', env)
        self.assertNotRegex(r.stdout, r'password for \w+: "')
        self.assertNotIn('none yet', r.stdout)
        if checked:
            self.assertIn('password:       kept: the accounts and passwords of the installed system are not changed; '
                          'the current password was checked', r.stdout)
            self.assertNotIn('not checked', r.stdout)
        else:
            self.assertIn('password:       kept, not checked: the accounts and passwords of the installed system are not '
                          'changed (a mu300-install.conf inside the zip can check it or set a new one', r.stdout)
            self.assertIn('(not checked: a mu300-install.conf inside the zip can check it or set a new one', r.stdout)
        self.assertIn(f'password for {users}: unchanged', r.stdout)

    def test_update_keeps_the_password(self):
        # update (keep) mode: the accounts and their passwords stay as the device has them, as with mu300-update; no
        # new password is generated and the password file is left alone (seen on the U30 Air: the Ubuntu zip's
        # update replaced the user's own password with a generated one)
        f = self.fake.root / 'data/adb/mu300-linux-password.txt'
        for system, users in (('openwrt', 'root'), ('ubuntu-24.04', 'ubuntu')):
            with self.subTest(system):
                self.reset()
                self.zip(system=system, kernel='6.18')
                os_ = system.partition('-')[0]
                self.existing_filesystem(systems=(os_,))
                self.accounts(os_)
                f.write_text('MU300 Linux v2026.10.01\nuser: x\npassword: the earlier one\n')
                f.chmod(0o600)
                r = self.run_installer()
                self.kept(r, users)
                self.assertIn('keep: nothing is erased', r.stdout)
                self.assertEqual(f.read_text(), 'MU300 Linux v2026.10.01\nuser: x\npassword: the earlier one\n')
                # no file at all: none is made either
                self.reset()
                self.zip(system=system, kernel='6.18')
                self.existing_filesystem(systems=(os_,))
                self.accounts(os_)
                self.kept(self.run_installer(), users)
                self.assertFalse(f.exists())

    def test_the_images_password_for_a_fresh_install_a_wipe_or_a_system_added(self):
        # issue #167 (seen on the F50: Ubuntu first, openwrt-luci and openwrt added with zips): a system that gets a
        # new password got a random one, shown nowhere but in root's file, which the next zip overwrote. It gets
        # the images' well-known one instead: ubuntu/ubuntu, OpenWrt's root without a password
        cases = (  # name, the target, trusted conf
            ('blank region', lambda: None, None),
            ('wipe', lambda: (self.existing_filesystem(systems=('openwrt',)), self.accounts('openwrt')),
             'MU300_MODE=wipe\n'),
            # a system added beside another, whatever that one's accounts
            ('second system', lambda: (self.existing_filesystem(systems=('ubuntu',)), self.accounts('ubuntu')), None),
            ('third system', lambda: (self.zip(system='openwrt-luci'), self.existing_filesystem(systems=('openwrt', 'ubuntu')),
                                      self.accounts('ubuntu'), self.accounts('openwrt')), None),
            ('Ubuntu beside OpenWrt', lambda: (self.zip(system='ubuntu-24.04'), self.existing_filesystem(systems=('openwrt',)),
                                               self.accounts('openwrt')), None),
            ('Ubuntu on a blank region', lambda: self.zip(system='ubuntu-24.04'), None),
            # the system there still has the image's accounts (an older mu300-update never carried them over)
            ('image accounts', lambda: (self.existing_filesystem(systems=('openwrt',)),
                                        self.accounts('openwrt', marked=True)), None),
            # a system without an /etc/shadow (damaged), or without its account in it: nothing to keep
            ('no shadow', lambda: self.existing_filesystem(systems=('openwrt',)), None),
            ('no account', lambda: (self.existing_filesystem(systems=('openwrt',)),
                                    (self.fake.root / 'fs/openwrt/etc/shadow').write_text('daemon:*:1::\n')), None))
        for name, target, trusted in cases:
            with self.subTest(name):
                self.reset()
                target()
                r = self.run_installer(trusted=trusted)
                self.images_password(r, self.zip_args['system'].partition('-')[0])
        # the file does not hold a password any more: a plan that is only shown does not write it
        self.reset()
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn(self.PLAN['openwrt'].format(conf=self.conf_path()), r.stdout)
        self.assertFalse((self.fake.root / 'data/adb/mu300-linux-password.txt').exists())

    def test_password_reset_only_from_the_zip(self):
        conf = self.fake.root / 'data/adb/mu300-install.conf'
        f = self.fake.root / 'data/adb/mu300-linux-password.txt'
        # any app could write /sdcard: a reset asked for there is ignored, the password stays
        self.existing_filesystem(systems=('openwrt',)); self.accounts('openwrt')
        r = self.run_installer(conf='MU300_PASSWORD_RESET=yes\n')
        self.kept(r, 'root')
        self.assertIn('MU300_PASSWORD_RESET is taken only from', r.stdout)
        self.assertFalse(f.exists())
        # /data/adb neither, on an update: a line left there must not reset the password at every update. It is not
        # used, so it stays
        r = self.run_installer(trusted='MU300_PASSWORD_RESET=yes\nMU300_BOOT_ATTEMPTS=3\n')
        self.kept(r, 'root')
        self.assertIn(f'an update sets a password only from a mu300-install.conf inside the zip: MU300_PASSWORD_RESET in '
                      f'{self.conf_path()} is not used', r.stdout)
        self.assertEqual(conf.read_text(), 'MU300_PASSWORD_RESET=yes\nMU300_BOOT_ATTEMPTS=3\n')
        # from the zip: the images' password (OpenWrt's root without one), shown and noted
        top = self.zip_conf('MU300_PASSWORD_RESET=yes\n')
        r = self.run_installer(extra_env={'MU300_ZIP_CONF': top})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('PW_SET=1\n', (self.fake.root / 'install.env').read_text())
        self.assertIn("PWHASH=''", (self.fake.root / 'install.env').read_text())
        self.assertIn(self.SHOWN['openwrt'], r.stdout)
        self.assertIn(self.NOTE['openwrt'], f.read_text())

    ZIP_CONF = 'mu300-install.conf in mu300-magisk.zip'         # what the installer calls it (not the unpacked path)

    def zip_conf(self, text):
        """a mu300-install.conf at the top of the zip, as customize.sh hands it over (MU300_ZIP_CONF)"""
        top = self.tmp / 'zip' / 'top-mu300-install.conf'
        top.write_text(text)
        return str(top)

    def pw_shadow(self, os_, line):
        etc = self.fake.root / 'fs' / os_ / 'etc'
        etc.mkdir(parents=True, exist_ok=True)
        (etc / 'shadow').write_text(line + '\n' + 'daemon:*:19000::::::\n')

    @staticmethod
    def fake_hash(pw, salt='saltsalt'):
        """the hash the fake busybox's mkpasswd makes (tests/fakedevice.py: UBB)"""
        return f'$6${salt}$' + pw.encode().hex()[:20]

    def test_an_update_keeps_any_password_without_a_word(self):
        # no mu300-install.conf in the zip: an update keeps what the system has, whatever it is - the images'
        # well-known ubuntu (the computer's installer may have set it for root too), none, the image's own hash, a
        # locked account (the v2026.10.16.1 zip reset an OpenWrt root that had "ubuntu" to none, and said it kept it)
        cases = (('openwrt', 'root:' + self.fake_hash('ubuntu', 'abc') + ':19000::::::', 'root'),
                 ('openwrt', 'root::19000::::::', 'root'),
                 ('openwrt', 'root:!:19000::::::', 'root'),
                 ('ubuntu-24.04', 'ubuntu:$6$img$imagehash:19000::::::', 'ubuntu'),
                 ('ubuntu-24.04', 'ubuntu:$y$j9T$yozuPAQ4fDSQH5fJzaFQd/$Z6UKqnHQMcZqZNJ18QIV5mTrJ7raj2y7MDipRg8bUj.:19000::::::',
                  'ubuntu'))
        for system, line, users in cases:
            with self.subTest(line):
                self.reset()
                self.zip(system=system)
                os_ = system.partition('-')[0]
                self.existing_filesystem(systems=(os_,))
                self.pw_shadow(os_, line)
                self.kept(self.run_installer(), users)
                self.assertFalse((self.fake.root / 'data/adb/mu300-linux-password.txt').exists())

    def check_env(self):
        """nothing of the Linux system runs: the check takes the zip's own tools (here the fake busybox's mkpasswd)"""
        return {}

    def test_keep_with_the_current_password(self):
        # MU300_KEEP_PASSWORD=yes and the right current password in the zip: kept, and the plan says it was checked.
        # The password is shown nowhere and written nowhere.
        self.existing_filesystem(systems=('openwrt',))
        self.pw_shadow('openwrt', 'root:' + self.fake_hash('old root pw') + ':19000::::::')
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=old root pw\n')
        r = self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top))
        self.kept(r, 'root', checked=True)
        self.assertNotIn('old root pw', r.stdout + r.stderr)
        self.assertNotIn(b'old root pw'.hex()[:20], r.stdout + r.stderr)          # nor the hash
        for p in (self.fake.root / 'data/adb').rglob('*'):
            if p.is_file():
                self.assertNotIn(b'old root pw', p.read_bytes())
        self.assertIn('MU300_KEEP_PASSWORD MU300_CURRENT_PASSWORD', r.stdout)      # the settings named, not their values
        # the conf inside the zip's mu300/ does the same
        (self.mu300 / 'mu300-install.conf').write_text('MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=old root pw\n')
        self.kept(self.run_installer(extra_env=self.check_env()), 'root', checked=True)

    def test_keep_with_a_wrong_password_stops(self):
        self.existing_filesystem(systems=('openwrt',))
        self.pw_shadow('openwrt', 'root:' + self.fake_hash('old root pw') + ':19000::::::')
        for current in ('MU300_CURRENT_PASSWORD=guess1234\n', ''):        # no line: the empty password
            with self.subTest(current):
                top = self.zip_conf('MU300_KEEP_PASSWORD=yes\n' + current)
                r = self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top))
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(f'MU300_CURRENT_PASSWORD in {self.ZIP_CONF} is not the current password of "root" in the installed '
                              'openwrt. Nothing was changed.', r.stdout)
                self.assertNotIn('guess1234', r.stdout + r.stderr)
                self.assertTrue(self.nothing_written())
                self.assertFalse((self.fake.root / 'install.env').exists())

    def test_keep_an_empty_password(self):
        # OpenWrt's root as it ships: no password, confirmed by none
        self.existing_filesystem(systems=('openwrt',))
        self.pw_shadow('openwrt', 'root::19000::::::')
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\n')
        self.kept(self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top)), 'root', checked=True)
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=something\n')
        r = self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(self.nothing_written())

    def test_keep_a_locked_or_unverifiable_password_stops(self):
        self.existing_filesystem(systems=('openwrt',))
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=whatever1\n')
        for line, said in (('root:!:19000::::::', 'has no password login, so there is none to confirm'),
                           # a method nothing on the device computes (no openssl or perl in this system)
                           ('root:$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz012:19000::::::',
                            'could not be checked on the device (UNVERIFIABLE)')):
            with self.subTest(line):
                self.pw_shadow('openwrt', line)
                r = self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top))
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(said, r.stdout)
                self.assertTrue(self.nothing_written())

    def test_no_sets_the_new_password_from_the_zip(self):
        self.existing_filesystem(systems=('openwrt',))
        self.pw_shadow('openwrt', 'root:' + self.fake_hash('old root pw') + ':19000::::::')
        top = self.zip_conf('MU300_KEEP_PASSWORD=no\nMU300_PASSWORD=new secret 9\n')
        r = self.run_installer(extra_env={'MU300_ZIP_CONF': top})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('new secret 9'.encode().hex()[:20], env)
        self.assertIn('KEEP_PW_OSES=""', env)
        self.assertIn(f'password:       the one from {self.ZIP_CONF} (not shown)', r.stdout)
        self.assertNotIn('new secret 9', r.stdout + r.stderr)
        # no without a new one: the images' own (OpenWrt's root without one)
        top = self.zip_conf('MU300_KEEP_PASSWORD=no\n')
        r = self.run_installer(extra_env={'MU300_ZIP_CONF': top})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('PW_SET=1\n', (self.fake.root / 'install.env').read_text())
        self.assertIn(self.SHOWN['openwrt'], r.stdout)
        # yes and a new one at once: refused, nothing written
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\nMU300_PASSWORD=new secret 9\n')
        r = self.run_installer(extra_env=dict(self.check_env(), MU300_ZIP_CONF=top))
        self.assertEqual(r.returncode, 1)
        self.assertIn('do not go together', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_the_password_settings_of_an_update_come_from_the_zip_only(self):
        # MU300_KEEP_PASSWORD and MU300_CURRENT_PASSWORD are read from no other file; MU300_PASSWORD from /data/adb
        # is not used by an update
        self.existing_filesystem(systems=('openwrt',))
        self.pw_shadow('openwrt', 'root:' + self.fake_hash('old root pw') + ':19000::::::')
        r = self.run_installer(trusted='MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=wrong one\nMU300_PASSWORD=new secret 9\n',
                               extra_env=self.check_env())
        self.kept(r, 'root')
        self.assertIn('MU300_KEEP_PASSWORD is taken only from a mu300-install.conf inside the zip', r.stdout)
        self.assertIn('MU300_CURRENT_PASSWORD is taken only from a mu300-install.conf inside the zip', r.stdout)
        self.assertIn('MU300_PASSWORD in', r.stdout)
        self.assertNotIn('wrong one', r.stdout + r.stderr)
        self.assertNotIn('new secret 9'.encode().hex()[:20], (self.fake.root / 'install.env').read_text())
        # the zip's file wins over /data/adb
        top = self.zip_conf('MU300_BOOT_ATTEMPTS=2\n')
        r = self.run_installer(trusted='MU300_BOOT_ATTEMPTS=3\n', extra_env={'MU300_ZIP_CONF': top})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('BOOT_ATTEMPTS=2\n', (self.fake.root / 'install.env').read_text())

    def test_a_fresh_install_keeps_the_images_password(self):
        # nothing to keep: the images' own, or MU300_PASSWORD from /data/adb as before; a keep answer only warns
        top = self.zip_conf('MU300_KEEP_PASSWORD=yes\nMU300_CURRENT_PASSWORD=whatever1\n')
        r = self.run_installer(extra_env={'MU300_ZIP_CONF': top})
        self.images_password(r, 'openwrt')
        self.assertIn('MU300_KEEP_PASSWORD: the installed openwrt has no password of its own to keep', r.stdout)
        # a password of one's own in /data/adb, as before
        self.reset()
        r = self.run_installer(trusted='MU300_PASSWORD=correct horse\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('correct horse', r.stdout)
        self.assertIn('correct horse'.encode().hex()[:20], (self.fake.root / 'install.env').read_text())
        self.assertIn('password:       the one from ', r.stdout)
        self.assertIn(f'password for root: the one from {self.conf_path()}', r.stdout)

    def test_payload_replaced_after_verification_is_not_installed(self):
        # the zip changes under the installer once the payload is checked (magiskboot runs only after that): what
        # gets installed is the checked copy
        good = self.tmp / 'good.zip'
        shutil.copy(self.zipfile, good)
        manifest = (self.mu300 / 'manifest').read_text()          # the good zip's (corrupt_payload makes it anew)
        self.corrupt_payload()
        evil = self.tmp / 'evil.zip'
        shutil.copy(self.zipfile, evil)
        shutil.copy(good, self.zipfile)
        (self.mu300 / 'manifest').write_text(manifest)
        self.stub('magiskboot-swap', f'cp "{evil}" "$ZIPFILE"; exec "{self.stubs}/magiskboot" "$@"')
        self.magiskboot = self.stubs / 'magiskboot-swap'
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.zipfile), sha(evil))                         # the swap happened
        want = re.search(r'SHA256_ROOTFS=(\w+)', (self.mu300 / 'manifest').read_text()).group(1)
        installed = [l.split()[0] for l in (self.fake.root / 'installed.sha256').read_text().splitlines()]
        self.assertEqual(installed, [want])

    def test_mainline_modules_into_every_system(self):
        self.existing_filesystem(systems=('ubuntu',))
        r = self.run_installer()                                       # an OpenWrt 6.18 zip added to it
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        krel = self.kernel_release
        self.assertTrue((self.fake.root / f'fs/ubuntu/lib/modules/{krel}/extra/mu300-test.ko').is_file())
        self.assertTrue((self.fake.root / f'fs/openwrt/lib/modules/{krel}/mu300-test.ko').is_file())
        self.assertEqual((self.fake.root / 'fs/home/precious').read_text(), 'a user file')
        self.assertEqual((self.fake.root / 'mounts').read_text(), '')                 # unmounted again
        self.assertEqual(self.work_dirs(), [])


class WebCaller(InstallerCase):
    """what a caller other than Magisk gets (tools/android-web.sh, for the web installer): its answers from one
    file and no other, stages and the plan as lines a program reads, extras beside the scripts, an adb key"""

    ADB_KEY = json.loads((TOP / 'tests/web/fixtures/adbkey.json').read_text())['pub'] + ' web-browser'

    def answers(self, text, mode=0o600):
        f = self.fake.root / 'data/adb/answers.conf'
        f.write_text(text)
        f.chmod(mode)
        return f

    def web(self, text, mode=0o600, progress=True, **kw):
        env = {'MU300_ANSWERS': self.answers(text, mode)}
        if progress:
            env['MU300_PROGRESS'] = 1
        return self.run_installer(extra_env=env, **kw)

    @staticmethod
    def plan(out):
        return dict(l[len('MU300-PLAN '):].split('=', 1) for l in out.splitlines() if l.startswith('MU300-PLAN '))

    @staticmethod
    def steps(out):
        return [l[len('MU300-STEP '):] for l in out.splitlines() if l.startswith('MU300-STEP ')]

    def test_the_answers_file_is_the_only_one_read(self):
        r = self.web('MU300_DRY_RUN=1\nMU300_BOOT_ATTEMPTS=3\n', conf='MU300_BOOT=android\nMU300_HOTSPOT=no\n',
                     trusted='MU300_BOOT_ATTEMPTS=2\nMU300_GPU=no\nMU300_PASSWORD=from-data-adb\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        p = self.plan(r.stdout)
        self.assertEqual((p['boot_attempts'], p['default_linux'], p['hotspot'], p['gpu'], p['password']),
                         ('3', '1', '1', 'yes', 'image'))
        self.assertIn(f'settings from {self.fake.root}/data/adb/answers.conf: MU300_DRY_RUN MU300_BOOT_ATTEMPTS', r.stdout)
        self.assertNotIn('sdcard/mu300-install.conf:', r.stdout)
        self.assertNotIn('data/adb/mu300-install.conf:', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_an_answers_file_others_can_change_is_refused(self):
        for mode in (0o666, 0o620, 0o602):
            r = self.web('MU300_MODE=wipe\n', mode=mode)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('is not a file that only root can change; nothing was changed', r.stdout)
            self.assertTrue(self.nothing_written())
        r = self.run_installer(extra_env={'MU300_ANSWERS': self.fake.root / 'data/adb/missing.conf'})
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.nothing_written())
        # and one of another user (the fake root is this user: any other uid is "not root")
        r = self.run_installer(extra_env={'MU300_ANSWERS': self.answers('MU300_DRY_RUN=1\n'),
                                          'MU300_TRUSTED_UID': os.getuid() + 1})
        self.assertEqual(r.returncode, 1)
        self.assertIn('is not a file that only root can change', r.stdout)

    def test_the_answers_file_may_erase(self):
        self.device(card=bytes(1 << 20))
        r = self.web('MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        p = self.plan(r.stdout)
        self.assertEqual((p['storage'], p['format'], p['sd_dev']),
                         ('sd', '1', str(self.fake.root / 'dev/block/mmcblk1')))

    def test_dry_run_stages_and_plan(self):
        r = self.web('MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.steps(r.stdout), ['1/9 check', '2/9 unpack', '3/9 plan'])
        p = self.plan(r.stdout)
        self.assertEqual(p, {'tag': 'v2026.10.06', 'system': 'openwrt', 'os': 'openwrt', 'ubuntu': '', 'kernel': '6.18',
                             'device': 'f50', 'storage': 'internal', 'sd_dev': '', 'offset': p['offset'],
                             'size': p['size'], 'format': '1', 'update': '0', 'have': '', 'boot_os': 'openwrt',
                             'default_linux': '1', 'boot_attempts': '5', 'hotspot': '1', 'gpu': 'yes',
                             'password': 'image', 'password_checked': '0', 'extras': '', 'adb': '0', 'linux_slot': 'b',
                             'android_slot': 'a',
                             'sync_bootchain': '1', 'bootchain': '', 'dry_run': '1'})
        self.assertRegex(p['offset'], r'^\d+$')
        self.assertGreater(int(p['size']), 700 << 20)
        # the plan for people is still there, before the lines for the program
        self.assertLess(r.stdout.index('- Plan'), r.stdout.index('MU300-PLAN tag='))
        self.assertTrue(self.nothing_written())

    def test_no_machine_lines_unless_asked(self):
        r = self.web('MU300_DRY_RUN=1\n', progress=False)
        self.assertEqual(r.returncode, 3)
        self.assertNotIn('MU300-STEP', r.stdout)
        self.assertNotIn('MU300-PLAN', r.stdout)
        # and Magisk's own run (no MU300_ANSWERS) prints none either
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertNotIn('MU300-STEP', r.stdout)
        self.assertNotIn('MU300-PLAN', r.stdout)

    def test_an_install_names_every_stage_in_order(self):
        r = self.web('MU300_BOOT_ATTEMPTS=4\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.steps(r.stdout), ['1/9 check', '2/9 unpack', '3/9 plan', '4/9 vendor', '5/9 image',
                                                '6/9 systems', '7/9 boot', '8/9 arm', '9/9 done'])
        out = r.stdout
        # the stage lines stand where the work happens: boot_b is written after "boot", misc after "arm"
        self.assertLess(out.index('MU300-STEP 6/9 systems'), out.index('MU300-INSTALL-OK'))
        self.assertLess(out.index('MU300-INSTALL-OK'), out.index('MU300-STEP 7/9 boot'))
        self.assertLess(out.index('MU300-STEP 7/9 boot'), out.index('Writing and verifying boot_b'))
        self.assertLess(out.index('Writing and verifying boot_b'), out.index('MU300-STEP 8/9 arm'))
        self.assertEqual(self.plan(out)['dry_run'], '0')
        self.assertIn('BOOT_ATTEMPTS=4', (self.fake.root / 'install.env').read_text())

    def test_a_refusal_stops_at_its_stage(self):
        self.device(model='Pixel 9')
        r = self.web('')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.steps(r.stdout), ['1/9 check'])
        self.assertTrue(self.nothing_written())
        self.reset()
        self.fake_install_hook('exit 1')
        r = self.web('')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.steps(r.stdout)[-1], '6/9 systems')
        # the device's log is kept in /data/adb; the boot partitions and misc are as they were
        now = self.snapshot()
        for part in ('boot_a', 'boot_b', 'misc'):
            self.assertEqual(now[part], self.before[part], part)

    def test_extras_beside_the_scripts_reach_android_install(self):
        ex = self.mu300 / 'extras'
        ex.mkdir()
        (ex / 'mu300-extra-vpn.tar.gz').write_bytes(tar_gz({'VERSION': b'2.0.1\n'}))
        (ex / 'mu300-extra-other.tar.gz').write_bytes(b'not one of ours')
        (ex / 'mu300-extra-panel.tar.gz').symlink_to(ex / 'mu300-extra-vpn.tar.gz')
        self.fake_install_hook('ls "$MU300_DEVICE_WORK" > "$FAKE/work-ls"; sha256sum "$MU300_DEVICE_WORK/mu300-extra-vpn.tar.gz" > "$FAKE/extra.sha"')
        r = self.web('')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.plan(r.stdout)['extras'], 'vpn')
        self.assertIn('extras:         vpn', r.stdout)
        names = (self.fake.root / 'work-ls').read_text().split()
        self.assertIn('mu300-extra-vpn.tar.gz', names)
        self.assertNotIn('mu300-extra-other.tar.gz', names)
        self.assertNotIn('mu300-extra-panel.tar.gz', names)
        self.assertEqual((self.fake.root / 'extra.sha').read_text().split()[0], sha(ex / 'mu300-extra-vpn.tar.gz'))
        # none there: nothing is said and nothing is copied
        self.reset()
        r = self.web('MU300_DRY_RUN=1\n')
        self.assertEqual(self.plan(r.stdout)['extras'], '')
        self.assertNotIn('extras:', r.stdout)

    def test_adb_key_turns_adb_on_for_that_key(self):
        r = self.web(f'MU300_ADB_KEY={self.ADB_KEY}\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('\nADB=1\n', env)
        self.assertIn(f"\nADB_KEY='{self.ADB_KEY}'\n", env)
        self.assertIn('adb to Linux:   on, for web-browser', r.stdout)
        self.assertEqual(self.plan(r.stdout)['adb'], '1')
        # the key itself is in no line for people or programs
        self.assertNotIn(self.ADB_KEY[:40], r.stdout)
        # without one, adb is not touched
        self.reset()
        r = self.web('')
        env = (self.fake.root / 'install.env').read_text()
        self.assertIn('\nADB=0\n', env)
        self.assertIn("\nADB_KEY=''\n", env)
        self.assertNotIn('adb to Linux', r.stdout)

    def test_adb_key_only_from_a_trusted_file_and_only_a_key(self):
        r = self.run_installer(conf=f'MU300_ADB_KEY={self.ADB_KEY}\nMU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('MU300_ADB_KEY is taken only from', r.stdout)
        self.assertNotIn('adb to Linux', r.stdout)
        key = self.ADB_KEY.split()[0]
        for bad in (key, key + ' ', key[:-1] + ' name', 'X' + key[1:] + ' name', key + " na'me", key + ' a b',
                    key + ' $(reboot)', key.replace('A', "'", 1) + ' name', key + ' ' + 'n' * 65):
            r = self.web(f'MU300_ADB_KEY={bad}\nMU300_DRY_RUN=1\n')
            self.assertEqual(r.returncode, 1, (bad[-30:], r.stdout))
            self.assertIn('is not valid', r.stdout)
            self.assertNotIn(key[20:60], r.stdout)
            self.assertTrue(self.nothing_written())

    def test_a_password_is_in_no_machine_line(self):
        r = self.web('MU300_PASSWORD=hunter2-hunter2\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.plan(r.stdout)['password'], 'given')
        self.assertNotIn('hunter2', r.stdout + r.stderr)


class Bootchain(InstallerCase):
    """the boot chain of Android's slot for the Linux slot (tools/android-bootchain.sh, tests/test_bootchain.py): the
    plan says what happens, a refusal stops the installation before anything is written, and android-install.sh is
    told whether to sync"""

    def chain(self, name, slot, data):
        (self.fake.root / 'dev/block/by-name' / f'{name}_{slot}').write_bytes(data)

    def test_identical(self):
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('  boot chain:     identical on both slots', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_differing_images_are_named(self):
        # the same size, another build
        self.chain('trustos', 'a', b'TRUSTOS' * 1024)
        self.chain('vbmeta', 'a', b'VBMETA' * 1024)
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("  boot chain:     trustos, vbmeta differ between the slots; Android's slot a is copied to slot b "
                      '(backed up first)', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_refusal_stops_before_anything(self):
        (self.fake.root / 'dev/block/by-name/teecfg_b').unlink()
        r = self.run_installer()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('the boot chain cannot be copied to the Linux slot: teecfg_a or teecfg_b is missing', r.stdout)
        self.assertIn('MU300_SYNC_BOOTCHAIN=0', r.stdout)
        self.assertFalse((self.fake.root / 'install.env').exists())
        self.assertTrue(self.nothing_written())

    def test_install_tells_android_install_to_sync(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('SYNC_BOOTCHAIN=1', (self.fake.root / 'install.env').read_text())

    def test_skip_setting(self):
        (self.fake.root / 'dev/block/by-name/teecfg_b').unlink()
        r = self.run_installer(conf='MU300_SYNC_BOOTCHAIN=0\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('boot chain:     not synced (MU300_SYNC_BOOTCHAIN=0)', r.stdout)
        self.assertIn('SYNC_BOOTCHAIN=0', (self.fake.root / 'install.env').read_text())

    def test_unrecognised_model_is_not_synced(self):
        self.device(model='Generic Phone')
        r = self.run_installer(trusted='MU300_DEVICE=f50\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('boot chain:     not synced (the model is not recognised)', r.stdout)
        self.assertIn('SYNC_BOOTCHAIN=0', (self.fake.root / 'install.env').read_text())


class Password(ShellTest):
    def lib(self, shell, code):
        return self.sh(shell, f'MU300_LIB=1 MU300_DIR="{INSTALLER_DIR}" MU300_UBB="busybox" . "{INSTALLER}"; ' + code)

    @unittest.skipIf(not busybox_has('mkpasswd'), "this busybox has no mkpasswd")
    def test_hash_is_sha512crypt(self):
        # the bundled busybox's mkpasswd and tools/sha512crypt.py agree
        import importlib.util
        spec = importlib.util.spec_from_file_location('s', TOP / 'tools' / 'sha512crypt.py')
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        for shell in self.each_shell():
            r = self.lib(shell, 'password_hash "s3cret pw"')
            self.assertEqual(r.returncode, 0, r.stderr)
            h = r.stdout.strip()
            salt = h.split('$')[2]
            self.assertEqual(len(salt), 16)
            self.assertEqual(h, m.sha512_crypt('s3cret pw', salt))

    def test_random_chars(self):
        # the salt of password_hash (the password itself is never random any more: issue #167)
        for shell in self.each_shell():
            seen = set()
            for _ in range(20):
                r = self.lib(shell, 'random_chars "$CRYPT_ITOA" 16')
                self.assertRegex(r.stdout.strip(), r'^[./0-9A-Za-z]{16}$')
                seen.add(r.stdout)
            self.assertEqual(len(seen), 20)


# a yescrypt hash of "old secret 1" as Ubuntu 24.04's chpasswd made it
YESCRYPT = '$y$j9T$yozuPAQ4fDSQH5fJzaFQd/$Z6UKqnHQMcZqZNJ18QIV5mTrJ7raj2y7MDipRg8bUj.'
_PWBIN = None


def pwcheck_binary():
    """tools/pwcheck/mu300-pwcheck.c built with this machine's compiler and libcrypt (libxcrypt), when it can be and
    knows yescrypt; else None (macOS: no libxcrypt)"""
    global _PWBIN
    if _PWBIN is None:
        _PWBIN = ''
        cc = shutil.which('cc') or shutil.which('gcc')
        if cc:
            out = Path(tempfile.mkdtemp(prefix='mu300-pwcheck-')) / 'mu300-pwcheck'
            r = subprocess.run([cc, '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(TOP / 'tools/pwcheck/mu300-pwcheck.c'), '-lcrypt'],
                               capture_output=True)
            if r.returncode == 0:
                t = subprocess.run([str(out)], input=f'old secret 1\n{YESCRYPT}\n', capture_output=True, text=True)
                if t.stdout == 'MATCH\n':
                    _PWBIN = str(out)
    return _PWBIN or None


class PwCheck(ShellTest):
    """tools/android-pwcheck.sh: one word out, never a password or a hash; the hash computed by the project's own tools
    only (mu300-pwcheck, a busybox's mkpasswd), never by anything of the Linux system it looks at, which would run with
    Android's root"""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'sys'
        (self.root / 'etc').mkdir(parents=True)
        # programs of the "Linux system" that must never run: each leaves a mark when it does
        self.mark = self.tmp / 'ran'
        for d in ('usr/bin', 'bin', 'usr/sbin', 'sbin'):
            (self.root / d).mkdir(parents=True, exist_ok=True)
            for n in ('perl', 'openssl', 'busybox', 'sh', 'mkpasswd', 'chroot', 'env'):
                f = self.root / d / n
                f.write_text(f'#!/bin/sh\necho "$0" >> "{self.mark}"\necho MATCH\n')
                f.chmod(0o755)
        self.bb = self.tmp / 'bb'
        self.bb.write_text(UBB)
        self.bb.chmod(0o755)
        # a chroot on PATH that leaves a mark too
        self.path = self.tmp / 'path'
        self.path.mkdir()
        c = self.path / 'chroot'
        c.write_text(f'#!/bin/sh\necho chroot >> "{self.mark}"\n')
        c.chmod(0o755)

    def check(self, shell, line, pw=None, user='root', **env):
        (self.root / 'etc/shadow').write_text('daemon:*:1::\n' + line + '\n')
        args = ['-s'] if pw is None else []
        r = self.script(shell, TOP / 'tools/android-pwcheck.sh', *args, self.root, user,
                        stdin='' if pw is None else pw + '\n',
                        PATH=f'{self.path}:{os.environ["PATH"]}', **env)
        h = line.split(':')[1]
        if len(h) > 2:
            self.assertNotIn(h, r.stdout + r.stderr)
        if pw:
            self.assertNotIn(pw, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'^(SET|EMPTY|LOCKED|NOUSER|MATCH|NOMATCH|UNVERIFIABLE)\n$')
        self.assertFalse(self.mark.exists(), 'a program of the Linux system (or chroot) was run: ' +
                         (self.mark.read_text() if self.mark.exists() else ''))
        return r.stdout.strip(), r.returncode

    def test_runs_nothing_of_the_system_it_checks(self):
        # the script: no chroot, nothing started from ROOT
        src = (TOP / 'tools/android-pwcheck.sh').read_text()
        code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotIn('chroot', code)
        # a path under ROOT in a command's place (first word of a command, after | ; && || { ( then do else), or any
        # of its bin directories named at all
        self.assertNotRegex(code, r'(^|[;&|{(]|\bthen|\bdo|\belse|\bexec|\benv)\s*"?\$\{?ROOT')
        self.assertNotRegex(code, r'\$\{?ROOT\}?/(usr/)?s?bin\b|/lib/ld-|LD_')
        # and when it runs, with every program of the system set to say MATCH: none ran, nothing matched
        for shell in self.each_shell():
            for line in ('root:$6$saltsalt$abc:1::', f'root:{YESCRYPT}:1::', 'root:$1$abc$def:1::'):
                self.assertEqual(self.check(shell, line, 'x'), ('UNVERIFIABLE', 1), line)

    def test_links_are_not_followed(self):
        # a link is resolved on Android's side, where it could name any of Android's files
        secret = self.tmp / 'android-shadow'
        secret.write_text('root::1::\n')
        for shell in self.each_shell():
            (self.root / 'etc/shadow').unlink(missing_ok=True)
            (self.root / 'etc/shadow').symlink_to(secret)
            r = self.script(shell, TOP / 'tools/android-pwcheck.sh', '-s', self.root, 'root')
            self.assertEqual(r.stdout, 'NOUSER\n')
            (self.root / 'etc/shadow').unlink()

    def test_states(self):
        for shell in self.each_shell():
            self.assertEqual(self.check(shell, 'root::19000::::::'), ('EMPTY', 0))
            self.assertEqual(self.check(shell, 'root:!:19000::::::'), ('LOCKED', 0))
            self.assertEqual(self.check(shell, 'root:$6$x$y:19000::::::'), ('SET', 0))
            self.assertEqual(self.check(shell, 'other:$6$x$y:19000::::::'), ('NOUSER', 1))
            self.assertEqual(self.check(shell, 'root::1::', user='r;oot'), ('NOUSER', 1))

    def test_empty_and_locked(self):
        for shell in self.each_shell():
            self.assertEqual(self.check(shell, 'root::19000::::::', ''), ('MATCH', 0))
            self.assertEqual(self.check(shell, 'root::19000::::::', 'x'), ('NOMATCH', 1))
            self.assertEqual(self.check(shell, 'root:!$6$x$y:19000::::::', 'x'), ('LOCKED', 1))
            self.assertEqual(self.check(shell, 'root:$6$x$y:19000::::::', ''), ('NOMATCH', 1))

    def test_the_zips_busybox(self):
        # MU300_PWCHECK_BB: the zip's busybox mkpasswd (here the fake device's, tests/fakedevice.py), MD5- and
        # SHA-crypt only; a salt that is not crypt's alphabet is never handed to it
        h = '$6$saltsalt$' + b'right one'.hex()[:20]
        for shell in self.each_shell():
            self.assertEqual(self.check(shell, f'root:{h}:1::', 'right one', MU300_PWCHECK_BB=str(self.bb)), ('MATCH', 0))
            self.assertEqual(self.check(shell, f'root:{h}:1::', 'wrong one', MU300_PWCHECK_BB=str(self.bb)), ('NOMATCH', 1))
            self.assertEqual(self.check(shell, f'root:{YESCRYPT}:1::', 'x', MU300_PWCHECK_BB=str(self.bb)),
                             ('UNVERIFIABLE', 1))
            self.assertEqual(self.check(shell, 'root:$6$-P;x$abc:1::', 'x', MU300_PWCHECK_BB=str(self.bb)),
                             ('UNVERIFIABLE', 1))

    def test_the_projects_checker(self):
        # MU300_PWCHECK_BIN: tools/pwcheck/mu300-pwcheck.c (libxcrypt): yescrypt (Ubuntu's passwd), SHA- and MD5-crypt
        b = pwcheck_binary()
        if not b:
            self.skipTest('no C compiler with a libcrypt that knows yescrypt')
        sha = subprocess.run(['openssl', 'passwd', '-5', '-salt', 'abcdefgh', 'root pw 1'], capture_output=True,
                             text=True).stdout.strip() if shutil.which('openssl') else ''
        for shell in self.each_shell():
            self.assertEqual(self.check(shell, f'ubuntu:{YESCRYPT}:1::', 'old secret 1', user='ubuntu',
                                        MU300_PWCHECK_BIN=b), ('MATCH', 0))
            self.assertEqual(self.check(shell, f'ubuntu:{YESCRYPT}:1::', 'old secret 2', user='ubuntu',
                                        MU300_PWCHECK_BIN=b), ('NOMATCH', 1))
            if sha.startswith('$5$'):
                self.assertEqual(self.check(shell, f'root:{sha}:1::', 'root pw 1', MU300_PWCHECK_BIN=b), ('MATCH', 0))
                self.assertEqual(self.check(shell, f'root:{sha}:1::', 'root pw 2', MU300_PWCHECK_BIN=b), ('NOMATCH', 1))
            # a method libxcrypt does not know, a malformed hash
            self.assertEqual(self.check(shell, 'root:$9$abc$def:1::', 'x', MU300_PWCHECK_BIN=b), ('UNVERIFIABLE', 1))

    def test_the_checker_alone(self):
        b = pwcheck_binary()
        if not b:
            self.skipTest('no C compiler with a libcrypt that knows yescrypt')
        for stdin, out, rc in ((f'old secret 1\n{YESCRYPT}\n', 'MATCH\n', 0), (f'nope\n{YESCRYPT}\n', 'NOMATCH\n', 1),
                               ('x\n\n', 'UNVERIFIABLE\n', 2), ('x\n', 'UNVERIFIABLE\n', 2),
                               ('x\n' + 'a' * 5000 + '\n', 'UNVERIFIABLE\n', 2)):
            r = subprocess.run([b], input=stdin, capture_output=True, text=True)
            self.assertEqual((r.stdout, r.returncode), (out, rc), stdin[:20])


if __name__ == '__main__':
    unittest.main()
