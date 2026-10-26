"""tools/android-mount-mu300root.sh -u: the unmount, with stubs for Android's umount and losetup, a mount table
(MU300_MOUNTS) and a sysfs tree (MU300_SYSFS) standing in for the device's."""
from helpers import TOP, ShellTest

HELPER = TOP / 'tools' / 'android-mount-mu300root.sh'


class Unmount(ShellTest):
    def setUp(self):
        super().setUp()
        self.mp = self.tmp / 'mnt'
        self.mp.mkdir()
        self.loop = self.tmp / 'sys' / 'block' / 'loop32' / 'loop'
        (self.tmp / 'mounts').write_text(f'/dev/block/loop32 {self.mp} ext4 ro,seclabel,relatime 0 0\n')
        # losetup -d fails the way Android's does on a loop that is no longer attached
        self.stub('losetup', 'echo "losetup $*" >> "$STUBLOG/calls"\n'
                             '[ -d "$STUBLOG/sys/block/${2##*/}/loop" ] || '
                             '{ echo "losetup: $2: No such device or address" >&2; exit 1; }\n'
                             '[ "${FAKE_DETACH_FAILS:-0}" = 0 ] || exit 1\n'
                             'rm -rf "$STUBLOG/sys/block/${2##*/}/loop"')

    def umount_stub(self, frees_loop, fails=False):
        body = 'echo "umount $*" >> "$STUBLOG/calls"\n'
        if fails:
            body += 'echo "umount: $1: Device or resource busy" >&2; exit 1\n'
        if frees_loop:   # toybox umount detaches the loop device of what it unmounts (umount -D would keep it)
            body += 'rm -rf "$STUBLOG/sys/block/loop32/loop"\n'
        self.stub('umount', body)

    def run_u(self, shell, **env):
        self.loop.mkdir(parents=True, exist_ok=True)
        (self.loop / 'backing_file').write_text('/dev/block/mmcblk0\n')
        r = self.script(shell, HELPER, '-u', self.mp, MU300_MOUNTS=self.tmp / 'mounts',
                        MU300_SYSFS=self.tmp / 'sys', **env)
        calls = (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else ''
        (self.tmp / 'calls').unlink(missing_ok=True)
        return r, calls

    def test_umount_that_frees_the_loop_succeeds(self):
        # Android's umount (toybox) frees the loop by itself: a losetup -d after it fails, and the unmount, which
        # worked, must not be reported as failed (the Magisk installer stops on it)
        self.umount_stub(frees_loop=True)
        for shell in self.each_shell():
            r, calls = self.run_u(shell)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn('UNMOUNTED', r.stdout)
            self.assertIn(f'umount {self.mp}', calls)
            self.assertNotIn('losetup', calls)

    def test_loop_left_attached_is_detached(self):
        self.umount_stub(frees_loop=False)
        for shell in self.each_shell():
            r, calls = self.run_u(shell)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn('losetup -d /dev/block/loop32', calls)
            self.assertFalse(self.loop.exists())

    def test_failed_umount_fails(self):
        self.umount_stub(frees_loop=False, fails=True)
        for shell in self.each_shell():
            r, calls = self.run_u(shell)
            self.assertNotEqual(r.returncode, 0, shell)
            self.assertNotIn('UNMOUNTED', r.stdout)
            self.assertNotIn('losetup', calls)

    def test_failed_detach_fails(self):
        self.umount_stub(frees_loop=False)
        for shell in self.each_shell():
            r, _ = self.run_u(shell, FAKE_DETACH_FAILS=1)
            self.assertNotEqual(r.returncode, 0, shell)
            self.assertNotIn('UNMOUNTED', r.stdout)
