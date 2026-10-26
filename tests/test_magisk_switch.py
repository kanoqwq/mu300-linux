"""android/magisk/mu300-linux-switch/switch.sh: the block it arms goes to the slot Android is not on, made from the
live one with the CRC the bootloader checks, and it refuses when that slot holds a copy of Android's boot image.
Run with --dry-run against files standing in for the partitions; awk and md5sum come from busybox, as on the device."""
import importlib.util
import shutil

from helpers import TOP, ShellTest
from test_boot_image import fake_misc

SWITCH = TOP / 'android' / 'magisk' / 'mu300-linux-switch' / 'switch.sh'


class Switch(ShellTest):
    def setUp(self):
        super().setUp()
        self.busybox = shutil.which('busybox')
        if not self.busybox:
            self.skipTest('no busybox')
        spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
        bbi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bbi)
        self.parts = self.tmp / 'by-name'
        self.parts.mkdir()
        fake_misc(self.parts / 'misc')
        self.blocks = bbi.bootloader_control((self.parts / 'misc').read_bytes())   # android_a, linux_b, android_b, linux_a
        self.stub('id', 'echo 0')
        # the kernel's word that by-name/misc is the misc partition (switch.sh checks it on the resolved device)
        self.sysfs = self.tmp / 'sys'
        self.uevent('misc', 'misc')

    def uevent(self, dev, partname):
        d = self.sysfs / 'class/block' / dev
        d.mkdir(parents=True, exist_ok=True)
        (d / 'uevent').write_text(f'MAJOR=259\nMINOR=1\nDEVNAME={dev}\nDEVTYPE=partition\nPARTNAME={partname}\n')

    @staticmethod
    def boot(fill, cmdline=b''):
        # a header v4 page: the command line at 44 (our images carry loglevel=5 there, boot/build-boot-image.py)
        hdr = bytearray(b'ANDROID!' + bytes(4088))
        hdr[44:44 + len(cmdline)] = cmdline
        return bytes(hdr) + fill * (64 << 10)

    def images(self, same=False, linux=True, linux_on='b'):
        android_on = 'a' if linux_on == 'b' else 'b'
        stock = self.boot(b'\x11')
        (self.parts / f'boot_{android_on}').write_bytes(stock)
        other = self.boot(b'\x22', b'loglevel=5' if linux else b'')
        (self.parts / f'boot_{linux_on}').write_bytes(stock if same else other)

    def switch(self, shell, suffix, *args, **env):
        return self.script(shell, SWITCH, *args, MU300_BY_NAME=self.parts, MU300_BUSYBOX=self.busybox,
                           MU300_SLOT_SUFFIX=suffix, MU300_SYSFS=self.sysfs, **env)

    def new_block(self, r):
        lines = [l for l in r.stdout.splitlines() if l.startswith('misc  new:')]
        self.assertEqual(len(lines), 1, r.stdout + r.stderr)
        return bytes.fromhex(lines[0].split(':', 1)[1].strip())

    def test_arms_the_slot_android_is_not_on(self):
        _, linux_b, _, linux_a = self.blocks
        before = (self.parts / 'misc').read_bytes()
        for shell in self.each_shell():
            for suffix, want in (('_a', linux_b), ('_b', linux_a)):
                self.images(linux_on='b' if suffix == '_a' else 'a')
                r = self.switch(shell, suffix, '--dry-run')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.new_block(r), want, suffix)
                self.assertIn('dry run', r.stdout)
        self.assertEqual((self.parts / 'misc').read_bytes(), before)

    def test_refuses_a_copy_of_android(self):
        self.images(same=True)
        for shell in self.each_shell():
            for suffix in ('_a', '_b'):
                r = self.switch(shell, suffix, '--dry-run')
                self.assertNotEqual(r.returncode, 0, suffix)
                self.assertNotIn('misc  new:', r.stdout)
                self.assertIn('same image', r.stderr)

    def test_refuses_another_android(self):
        # after an OTA the other slot holds the previous Android: a boot image, not a copy of this one, not Linux
        _, linux_b, _, _ = self.blocks
        for shell in self.each_shell():
            for linux, ok in ((False, False), (True, True)):
                self.images(linux=linux)
                (self.parts / 'boot_a').write_bytes(self.boot(b'\x11', b'loglevel=5' if not linux else b''))
                r = self.switch(shell, '_a', '--dry-run')
                if ok:
                    self.assertEqual(self.new_block(r), linux_b)
                else:
                    self.assertNotEqual(r.returncode, 0)
                    self.assertNotIn('misc  new:', r.stdout)
                    self.assertIn('no Linux on boot_b', r.stderr)
                r = self.switch(shell, '_a', 'status')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('boot_b', r.stdout)
                self.assertIn('Linux' if linux else 'not Linux', r.stdout)

    def test_status_with_an_unknown_slot(self):
        self.images()
        for shell in self.each_shell():
            r = self.switch(shell, '_c', 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('running slot   _c', r.stdout)

    def test_unknown_slot(self):
        self.images()
        for shell in self.each_shell():
            r = self.switch(shell, '_c', '--dry-run')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("'_c'", r.stderr)

    def armed(self, shell, **env):
        self.stub('reboot', 'echo reboot >> "$STUBLOG/reboot.log"')
        self.stub('sleep', 'exit 0')
        return self.switch(shell, '_a', **env)

    def test_no_file_on_the_way_to_misc(self):
        # The block used to go through ${TMPDIR:-/data/local/tmp}/mu300-bc.bin, a fixed name in the adb shell user's
        # directory: a symlink planted there took root's write elsewhere. Now printf goes straight into dd
        # (FINDINGS 101): nothing is created in TMPDIR, the planted link and its target stay as they were.
        _, linux_b, _, _ = self.blocks
        shared = self.tmp / 'shared'
        shared.mkdir()
        victim = self.tmp / 'victim'
        for shell in self.each_shell():
            self.images()
            fake_misc(self.parts / 'misc')
            victim.write_bytes(b'keep')
            planted = shared / 'mu300-bc.bin'
            planted.unlink(missing_ok=True)
            planted.symlink_to(victim)
            r = self.armed(shell, TMPDIR=shared)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('armed for one boot', r.stdout)
            self.assertEqual(victim.read_bytes(), b'keep')
            misc = (self.parts / 'misc').read_bytes()
            self.assertEqual(misc[0x800:0x820], linux_b)
            self.assertEqual(len(misc), 4096)
            self.assertEqual(sorted(p.name for p in shared.iterdir()), ['mu300-bc.bin'])
            self.assertIn('reboot', (self.tmp / 'reboot.log').read_text())

    def test_misc_is_resolved_once_and_must_be_the_misc_partition(self):
        # by-name/misc is followed once, to a device whose sysfs entry says PARTNAME=misc; a link to anything else
        # (here: another partition's file) is refused before anything is written, and the target stays as it was
        _, linux_b, _, _ = self.blocks
        real = self.tmp / 'mmcblk0p9'
        for shell in self.each_shell():
            self.images()
            fake_misc(real)
            (self.parts / 'misc').unlink(missing_ok=True)
            (self.parts / 'misc').symlink_to(real)
            before = real.read_bytes()
            self.uevent('mmcblk0p9', 'boot_a')          # the link leads to a partition that is not misc
            r = self.armed(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('is not the misc partition', r.stderr)
            self.assertEqual(real.read_bytes(), before)
            (self.sysfs / 'class/block/mmcblk0p9/uevent').unlink()   # no word from sysfs at all
            r = self.armed(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(real.read_bytes(), before)
            self.uevent('mmcblk0p9', 'misc')            # the real misc behind the link: read and written there
            r = self.armed(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(real.read_bytes()[0x800:0x820], linux_b)
            self.assertTrue((self.parts / 'misc').is_symlink())

    def test_on_a_device_misc_must_be_a_block_device_under_dev_block(self):
        self.images()
        before = (self.parts / 'misc').read_bytes()
        for shell in self.each_shell():
            r = self.script(shell, SWITCH, MU300_BY_NAME=self.parts, MU300_BUSYBOX=self.busybox,
                            MU300_SLOT_SUFFIX='_a')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('outside /dev/block', r.stderr)
            self.assertEqual((self.parts / 'misc').read_bytes(), before)
