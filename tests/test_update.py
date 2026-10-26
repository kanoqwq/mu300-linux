"""mu300-update, sourced for its functions (MU300_LIB=1) against a fake Linux partition (MU300_DISK): which files of a
release it takes, the kernel choice, the byte helpers it edits boot image headers with, and whether a kernel bundle
may go onto this device."""
import io
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import time
import unittest

from helpers import BIN, TOP, ShellTest
from test_boot_image import HAVE_LZ4, fake_stock, fake_misc, BOOT


class UpdateBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        (self.disk / 'ubuntu' / 'etc').mkdir(parents=True)
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        self.device('f50')

    def device(self, name):
        (self.root / 'run/mu300/device').write_text(name + '\n')

    def up(self, shell, code, **env):
        return self.sh(shell, f'. "{BIN}/mu300-update"; {code}', MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN,
                       MU300_SYSROOT=self.root, **env)

    def busybox_applets(self, shell):
        """For busybox sh, {'PATH': ...} that runs the applets the update uses from busybox, as on OpenWrt: busybox sh
        on Ubuntu runs /usr/bin/cp, GNU's, which hides what busybox's own cp does. {} for the other shells."""
        if os.path.basename(shell[0]) != 'busybox':
            return {}
        d = self.tmp / 'busybox-applets'
        if not d.exists():
            d.mkdir()
            for a in ('cp', 'find', 'mkdir', 'rm', 'mv', 'ln', 'cat', 'dirname', 'tar', 'gzip', 'awk', 'sed', 'grep'):
                (d / a).write_text(f'#!/bin/sh\nexec "{shell[0]}" {a} "$@"\n')
                (d / a).chmod(0o755)
        return {'PATH': f'{d}{os.pathsep}{self.stubs}{os.pathsep}{os.environ.get("PATH", "")}'}


class Update(UpdateBase):
    def test_version_lists_what_is_installed_without_the_network(self):
        # mu300-toolkit's summary and its update screen when offline: each system with its release, the boot image,
        # the extras; no download is tried
        (self.disk / 'ubuntu/etc/mu300').mkdir(parents=True, exist_ok=True)
        (self.disk / 'ubuntu/etc/mu300/image-version').write_text('v2026.10.18\n')
        (self.disk / 'openwrt/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt/etc/mu300/image-version').write_text('v2026.10.17\n')
        (self.disk / 'boot').mkdir()
        (self.disk / 'boot/installed.tag').write_text('v2026.10.18\n')
        (self.disk / 'extra/vpn/bin').mkdir(parents=True)
        (self.disk / 'extra/vpn/release').write_text('v1.0.0\n')
        self.stub('curl', 'echo "network used" >&2; exit 7')
        self.stub('wget', 'echo "network used" >&2; exit 7')
        for shell in self.each_shell():
            r = self.up(shell, 'mounted_disk() { :; }; version')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('network used', r.stderr)
            self.assertRegex(r.stdout, r'systems on disk +.*ubuntu v2026\.10\.18')
            self.assertRegex(r.stdout, r'openwrt v2026\.10\.17')
            self.assertIn('boot image: v2026.10.18', r.stdout)
            self.assertIn('vpn (v1.0.0)', r.stdout)

    def test_sourcing_does_nothing(self):
        for shell in self.each_shell():
            r = self.up(shell, 'echo loaded')
            self.assertEqual((r.returncode, r.stdout), (0, 'loaded\n'), r.stderr)

    def test_linux_slot(self):
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            f.unlink(missing_ok=True)
            self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), 'b')
            for v, want in (('a\n', 'a'), ('b\n', 'b'), ('x\n', 'b')):
                f.write_text(v)
                self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), want, v)

    def test_linux_slot_without_the_file_is_the_booted_one(self):
        # an initramfs that did not publish it (or a garbled file): the slot LK booted, from the command line or the
        # bootargs in the device tree; b only when neither says
        f = self.root / 'run/mu300/linux-slot'
        cmdline = self.root / 'proc/cmdline'
        bootargs = self.root / 'proc/device-tree/chosen/bootargs'
        bootargs.parent.mkdir(parents=True)
        cases = [(None, 'console=x androidboot.slot_suffix=_a quiet', None, 'a'),
                 ('x\n', 'loglevel=5', b'console=x\0androidboot.slot_suffix=_a\0', 'a'),
                 (None, 'loglevel=5', None, 'b'),
                 (None, 'androidboot.slot_suffix=_b', b'androidboot.slot_suffix=_a\0', 'b'),
                 ('b\n', 'androidboot.slot_suffix=_a', None, 'b')]       # what the initramfs published wins
        for shell in self.each_shell():
            for v, cl, ba, want in cases:
                for p, data in ((f, v), (cmdline, cl), (bootargs, ba)):
                    p.unlink(missing_ok=True)
                    if data is not None:
                        p.write_bytes(data if isinstance(data, bytes) else data.encode())
                self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), want, (v, cl, ba))

    def test_rollback_stays_on_its_slot(self):
        # the kept image was taken from the slot Linux ran from then (no prev.slot: b); after Linux moved to a,
        # rolling back would put an init that knows only slot b onto a - refused before anything is written
        boot = self.disk / 'boot'
        boot.mkdir()
        (boot / 'prev.body').write_bytes(b'old image')
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            for prev, now, refused in ((None, 'a', True), ('b', 'a', True), ('a', 'b', True), ('a', 'a', False),
                                       (None, 'b', False), ('b', 'b', False)):
                (boot / 'prev.slot').unlink(missing_ok=True)
                if prev:
                    (boot / 'prev.slot').write_text(prev + '\n')
                f.write_text(now + '\n')
                r = self.up(shell, 'part_dev() { echo "PART $1" >&2; return 1; }; rollback_boot')
                self.assertNotEqual(r.returncode, 0)
                if refused:
                    self.assertIn('was taken from slot', r.stderr, (prev, now))
                    self.assertNotIn('PART', r.stderr)
                else:
                    self.assertIn(f'PART boot_{now}', r.stderr, (prev, now))

    def test_a_boot_image_already_in_place_is_named_after_the_release(self):
        # the kernel did not change between two releases: the image in place is the new release's too, or check and
        # the Update page offer that release again and again (U30 Air, FINDINGS 86)
        src = (BIN / 'mu300-update').read_text()
        bu = src[src.index('boot_update_run() {'):src.index('\n}\n', src.index('boot_update_run() {'))]
        same = bu[bu.index('if [ "$cur" = "$(cat "$BOOTDIR/written.sha256"'):]
        same = same[:same.index('\n    fi\n')]
        self.assertIn('echo "$tag" > "$BOOTDIR/installed.tag"', same)
        self.assertLess(same.index('installed.tag'), same.index('return 0'))

    def test_a_boot_partition_that_is_a_plain_file_is_not_written(self):
        # on the U30 Air /dev/mmcblk0p38 (boot_b) was once a 50 MB file in RAM, made by the early recorder while procd
        # replaced /dev: an image written there reads back as written (FINDINGS 86). Neither writer takes it.
        boot = self.disk / 'boot'
        boot.mkdir()
        for name, data in (('prev.body', b'old image'), ('prev.vbmeta', b'v'), ('prev.footer', b'f' * 64)):
            (boot / name).write_bytes(data)
        (boot / 'prev.slot').write_text('b\n')
        (self.root / 'run/mu300/linux-slot').write_text('b\n')
        fake = self.tmp / 'mmcblk0p38'
        for shell in self.each_shell():
            fake.write_bytes(bytes(8192))
            stub = f'part_dev() {{ echo "{fake}"; }}; '
            r = self.up(shell, stub + 'boot_dev boot_b; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertIn(f'{fake} is not a block device', r.stderr)
            r = self.up(shell, stub + 'unlock_for_trial() { :; }; rollback_boot')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nothing was written', r.stderr)
            self.assertEqual(fake.read_bytes(), bytes(8192))
            r = self.up(shell, stub + 'boot_update v2026.11.01')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('the boot image was not updated', r.stderr)
            self.assertEqual(fake.read_bytes(), bytes(8192))
        # a block device is passed on as it is
        import stat
        for dev in ('/dev/disk0', '/dev/sda', '/dev/vda', '/dev/loop0'):
            if os.path.exists(dev) and stat.S_ISBLK(os.stat(dev).st_mode):
                r = self.up(shell, f'part_dev() {{ echo {dev}; }}; boot_dev boot_b')
                self.assertEqual(r.stdout, dev + '\n', r.stderr)
                break

    def test_boot_update_keeps_the_slot_of_the_image(self):
        src = (BIN / 'mu300-update').read_text()
        self.assertIn('linux_slot > "$BOOTDIR/prev.slot"', src)
        self.assertLess(src.index('mv -f "$tmp/body" "$BOOTDIR/prev.body"'),
                        src.index('linux_slot > "$BOOTDIR/prev.slot"'))

    def test_slot_a_needs_a_slot_aware_bundle(self):
        # an older bundle's init knows only slot b: on Linux-on-a it would restore the wrong block and log into
        # Android's boot partition
        old = self.bundle('old.tar.gz', 'f50\n', 'sdcard\n')
        new = self.bundle('new.tar.gz', 'f50\n', 'sdcard\nlinux-slot\n')
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            for slot, b, ok in (('a', old, False), ('a', new, True), ('b', old, True), ('b', new, True)):
                f.write_text(slot + '\n')
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                r = self.up(shell, f'tar -tzf "{b}" > "{d}/list"; '
                                   f'bundle_fits_slot "{b}" "{d}/list" && echo OK || echo REFUSE')
                self.assertEqual(r.stdout.strip(), 'OK' if ok else 'REFUSE', (slot, b.name, r.stderr))

    def test_fetch_retries_a_network_error(self):
        # right after boot the VPN is up some seconds after mobile data, and a request in between is reset
        # (curl 35/56): "mu300-update check" then said "could not reach GitHub". Network errors are retried a few
        # times; an HTTP error (22: 404 and the like) is an answer and is not, and neither is a timeout (28: curl's
        # own --retry has retried it already, and four more rounds of 120 s would hold "check" for half an hour).
        for shell in self.each_shell():
            for rc, fails, want_out, want_calls in ((56, 2, 'body', 3), (35, 1, 'body', 2), (22, 1, '', 1),
                                                     (7, 9, '', 4), (28, 1, '', 1), (6, 1, 'body', 2)):
                n = self.tmp / 'n'
                n.write_text('0')
                self.stub('curl', f'c=$(($(cat "$STUBLOG/n") + 1)); echo $c > "$STUBLOG/n"; '
                                  f'[ $c -gt {fails} ] && {{ echo body; exit 0; }}; exit {rc}')
                r = self.up(shell, 'fetch_stdout https://example.invalid/x; echo "rc=$?"', MU300_FETCH_DELAY=0)
                self.assertEqual(int(n.read_text()), want_calls, (rc, fails))
                self.assertEqual(r.stdout.replace('rc=0\n', '').replace('rc=1\n', '').strip(), want_out, (rc, r.stdout))
                self.assertIn('rc=0' if want_out else 'rc=1', r.stdout)

    def test_notice(self):
        # a note for the login message and mu300-toolkit when a newer release is out; nothing else happens
        note, motd = self.tmp / 'run/update-available', self.tmp / 'motd.d'
        env = dict(MU300_NOTICE=str(note), MU300_MOTD_DIR=str(motd))
        for shell in self.each_shell():
            for installed, latest, noted in (('v2026.10.06', 'v2026.10.08', True), ('v2026.10.08', 'v2026.10.08', False),
                                             ('v2026.10.09', 'v2026.10.08', False),    # a test release: no note
                                             # the new names (FINDINGS 125): newer than every three-field tag
                                             ('v2026.10.27', 'v2026.10.16.1', True), ('v2026.10.16.1', 'v2026.10.27', False),
                                             ('v2026.10.16.9', 'v2026.10.16.10', True), ('v2026.10.16.10', 'v2026.10.16.9', False),
                                             ('unknown', 'v2026.10.16.1', True), ('v2026.10.27', 'test-issue-102', False)):
                r = self.up(shell, f'latest_release() {{ echo {latest}; }}; installed_version() {{ echo {installed}; }}; '
                                   'notice; echo rc=$?', **env)
                self.assertIn('rc=0', r.stdout, r.stderr)
                self.assertEqual(note.exists(), noted, (installed, latest))
                self.assertEqual((motd / '60-mu300-update').exists(), noted)
                if noted:
                    self.assertEqual(note.read_text(), f'mu300-linux {latest} is out (this is {installed}): '
                                                       'sudo mu300-update apply\n')
            # offline: the note stays, and the caller hears it (to ask again sooner)
            self.up(shell, 'latest_release() { echo v2026.10.08; }; installed_version() { echo v2026.10.06; }; notice', **env)
            r = self.up(shell, 'latest_release() { :; }; notice; echo rc=$?', **env)
            self.assertIn('rc=1', r.stdout)
            self.assertTrue(note.exists())

    def test_check_and_version_ok_know_the_new_names(self):
        # vYYYY.MM.DD.N (FINDINGS 125) is newer than every vYYYY.MM.DD: check offers it, a module's requires is met by it
        for shell in self.each_shell():
            for installed, latest, says in (('v2026.10.27', 'v2026.10.16.1', 'Run: mu300-update apply'),
                                            ('v2026.10.16.2', 'v2026.10.16.1', 'newer than the newest release'),
                                            ('v2026.10.16.9', 'v2026.10.16.10', 'Run: mu300-update apply')):
                r = self.up(shell, f'latest_release() {{ echo {latest}; }}; installed_version() {{ echo {installed}; }}; check')
                self.assertIn(says, r.stdout, (installed, latest, r.stderr))
            for need, have, ok in (('v2026.10.16', 'v2026.10.15.1', True), ('v2026.10.16', 'v2026.10.15', False),
                                   ('v2026.10.16.2', 'v2026.10.16.10', True), ('v2026.10.16.2', 'v2026.10.16.1', False),
                                   ('v2026.10.16.1', 'v2026.10.27', False), ('v2026.10.16', 'dev', True), ('', 'v2026.10.27', True)):
                r = self.up(shell, f'version_ok "{need}" "{have}"; echo "rc=$?"')
                self.assertIn('rc=0' if ok else 'rc=1', r.stdout, (need, have))

    def test_rootfs_asset(self):
        osr = self.disk / 'ubuntu' / 'etc' / 'os-release'
        cases = [('24.04', {}, 'mu300-ubuntu-rootfs.tar.gz'),
                 ('26.04', {}, 'mu300-ubuntu-26.04-rootfs.tar.gz'),
                 ('26.04', {'MU300_UBUNTU': '24.04'}, 'mu300-ubuntu-rootfs.tar.gz'),
                 ('24.04', {'MU300_UBUNTU': '26.04'}, 'mu300-ubuntu-26.04-rootfs.tar.gz'),
                 (None, {}, 'mu300-ubuntu-rootfs.tar.gz')]
        for shell in self.each_shell():
            for ver, env, want in cases:
                if ver:
                    osr.write_text(f'NAME="Ubuntu"\nVERSION_ID="{ver}"\n')
                else:
                    osr.unlink(missing_ok=True)
                self.assertEqual(self.up(shell, 'rootfs_asset ubuntu', **env).stdout.strip(), want, (ver, env))
            self.assertEqual(self.up(shell, 'rootfs_asset openwrt').stdout.strip(), 'mu300-openwrt-rootfs.tar.gz')

    def test_os_kind(self):
        for shell in self.each_shell():
            for name, kind in [('ubuntu', 'ubuntu'), ('openwrt', 'openwrt'), ('openwrt-luci', 'openwrt')]:
                self.assertEqual(self.up(shell, f'os_kind {name}').stdout.strip(), kind)
            self.assertEqual(self.up(shell, 'os_kind arch').returncode, 1)

    def test_installed_systems_three(self):
        for name in ('openwrt', 'openwrt-luci', 'openwrt-luci.old'):
            (self.disk / name).mkdir()
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu', 'openwrt', 'openwrt-luci'])

    def test_lists_by_kind(self):
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'keep_list openwrt-luci').stdout, self.up(shell, 'keep_list openwrt').stdout)
            # the panel's saved locks (FINDINGS 64)
            self.assertIn('etc/unisoc-modem', self.up(shell, 'keep_list openwrt-luci').stdout.split())
            # the jobs of System > Scheduled Tasks: the image ships /etc/crontabs empty, so without this an update
            # dropped them and cron's own init refused to start (FINDINGS 83)
            self.assertIn('etc/crontabs', self.up(shell, 'keep_list openwrt-luci').stdout.split())
            # the installer on Android carries the same things over (its own copy of the lists)
            ai = (TOP / 'tools/android-install.sh').read_text()
            for kind, pattern in (('ubuntu', r'ubuntu\) keep="([^"]*)"'), ('openwrt', r'openwrt\|openwrt-\*\) keep="([^"]*)"')):
                self.assertEqual(re.search(pattern, ai).group(1), self.up(shell, f'keep_list {kind}').stdout.strip())
            self.assertEqual(self.up(shell, 'vendor_list openwrt-luci').stdout, self.up(shell, 'vendor_list openwrt').stdout)
            self.assertEqual(self.up(shell, 'rootfs_asset openwrt-luci').stdout.strip(), 'mu300-openwrt-luci-rootfs.tar.gz')

    def test_running_os_by_directory(self):
        (self.disk / 'openwrt-luci' / 'etc').mkdir(parents=True)
        (self.disk / 'openwrt-luci' / 'etc' / 'openwrt_release').write_text('x')
        for shell in self.each_shell():
            r = self.up(shell, 'running_os', MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.stdout.strip(), 'openwrt-luci')

    def test_running_os_after_apply_is_the_kept_copy(self):
        (self.disk / 'openwrt-luci.old' / 'etc').mkdir(parents=True)
        (self.disk / 'openwrt-luci.old' / 'etc' / 'openwrt_release').write_text('x')
        for shell in self.each_shell():
            r = self.up(shell, 'running_os', MU300_ROOT=self.disk / 'openwrt-luci.old')
            self.assertEqual(r.stdout.strip(), 'openwrt-luci')

    def test_running_os_after_rollback_is_the_broken_copy(self):
        # between rollback and the reboot / is <os>.broken: it was "openwrt" for an openwrt-luci (U30 Air, FINDINGS 86)
        (self.disk / 'openwrt-luci').mkdir()
        (self.disk / 'openwrt-luci.broken' / 'etc').mkdir(parents=True)
        (self.disk / 'openwrt-luci.broken' / 'etc' / 'openwrt_release').write_text('x')
        for shell in self.each_shell():
            r = self.up(shell, 'running_os', MU300_ROOT=self.disk / 'openwrt-luci.broken')
            self.assertEqual(r.stdout.strip(), 'openwrt-luci')

    def test_clean_knows_the_third_system(self):
        (self.disk / 'openwrt-luci.old').mkdir()
        for shell in self.each_shell():
            self.up(shell, 'STAGE=$MU300_DISK/.stage; df() { :; }; clean')
            self.assertFalse((self.disk / 'openwrt-luci.old').exists())
            (self.disk / 'openwrt-luci.old').mkdir()

    def test_kernel_choice(self):
        boot = self.disk / 'boot'
        boot.mkdir()
        cases = [(None, '5.4', 'mu300-kernel.tar.gz'), ('6.18', '6.18', 'mu300-kernel-6.18.tar.gz'),
                 ('7.2', '7.2', 'mu300-kernel-7.2.tar.gz'), ('7.3', '7.3', 'mu300-kernel-7.3.tar.gz'),
                 ('5.4', '5.4', 'mu300-kernel.tar.gz'), ('7.4', '5.4', 'mu300-kernel.tar.gz'),
                 ('6.1', '5.4', 'mu300-kernel.tar.gz'), ('', '5.4', 'mu300-kernel.tar.gz')]
        for shell in self.each_shell():
            for c, choice, asset in cases:
                f = boot / 'kernel'
                if c is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(c + '\n')
                self.assertEqual(self.up(shell, 'kernel_choice; kernel_asset').stdout.split(), [choice, asset], c)

    def test_byte_helpers(self):
        # the boot image header is edited with these: sizes little endian, the AVB footer big endian
        f = self.tmp / 'bytes.bin'
        for shell in self.each_shell():
            for v in (0, 1, 255, 256, 0x12345678, 0xFFFFFFFF):
                r = self.up(shell, f'bytes {v} 4 le > "{f}"; u32 "{f}" 0')
                self.assertEqual(f.read_bytes(), struct.pack('<I', v), v)
                self.assertEqual(r.stdout.strip(), str(v))
            for v in (0, 0x1234, 0x0000000100000000, 0x7FFFFFFF12345678):
                r = self.up(shell, f'bytes {v} 8 be > "{f}"; be64 "{f}" 0')
                self.assertEqual(f.read_bytes(), struct.pack('>Q', v), v)
                self.assertEqual(r.stdout.strip(), str(v))
            # poke: overwrite in place, the rest untouched
            f.write_bytes(bytes(16))
            self.up(shell, f'bytes 3735928559 4 le | poke "{f}" 4')
            self.assertEqual(f.read_bytes(), bytes(4) + struct.pack('<I', 0xDEADBEEF) + bytes(8))

    def test_pad_page(self):
        f = self.tmp / 'img'
        for shell in self.each_shell():
            for n, want in ((0, 0), (1, 4096), (4095, 4096), (4096, 4096), (4097, 8192)):
                f.write_bytes(b'x' * n)
                self.up(shell, f'pad_page "{f}"')
                self.assertEqual(f.stat().st_size, want, n)

    def test_installed_systems(self):
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu'])
            (self.disk / 'openwrt').mkdir(exist_ok=True)
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu', 'openwrt'])
            (self.disk / 'openwrt').rmdir()

    def test_stale_broken_copies_are_removed(self):
        # a rollback leaves the system it left as <os>.broken (it may still be running until the reboot); nothing
        # removed it after that but a second rollback or "clean" - 655 MB stayed on the second F50 through an update
        for shell in self.each_shell():
            for os_ in ('ubuntu', 'openwrt'):
                (self.disk / f'{os_}.broken' / 'etc').mkdir(parents=True, exist_ok=True)
                (self.disk / f'{os_}.old' / 'etc').mkdir(parents=True, exist_ok=True)
            r = self.up(shell, 'clean_broken')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((self.disk / 'ubuntu.broken').exists())
            self.assertFalse((self.disk / 'openwrt.broken').exists())
            self.assertIn('ubuntu: removed the copy left by a rollback (ubuntu.broken)', r.stdout)
            self.assertTrue((self.disk / 'ubuntu.old').is_dir())          # the rollback copy stays
            self.assertTrue((self.disk / 'ubuntu').is_dir())
            # the running system (rolled back from, not rebooted yet) is never removed
            (self.disk / 'ubuntu.broken' / 'etc').mkdir(parents=True)
            r = self.up(shell, f'is_root() {{ [ "$1" = "{self.disk}/ubuntu.broken" ]; }}; clean_broken')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((self.disk / 'ubuntu.broken').is_dir())
            self.assertEqual(self.up(shell, 'clean_broken; echo rc=$?').stdout.strip().splitlines()[-1], 'rc=0')
            for d in ('ubuntu.broken', 'ubuntu.old', 'openwrt.old'):
                import shutil
                shutil.rmtree(self.disk / d, ignore_errors=True)

    def test_apply_cleans_before_it_counts_free_space(self):
        src = (BIN / 'mu300-update').read_text()
        body = src[src.index('\napply() {'):src.index('\nrollback() {')]
        self.assertIn('clean_broken', body)
        self.assertLess(body.index('clean_broken'), body.index('not enough free space'))

    def bundle(self, name, devices, features=None):
        p = self.tmp / name
        with tarfile.open(p, 'w:gz') as t:
            for fn, data in [('./Image', b'kernel'), ('./ramdisk-generic.lz4', b'rd')] + (
                    [('./devices', devices.encode())] if devices is not None else []) + (
                    [('./features', features.encode())] if features is not None else []):
                ti = tarfile.TarInfo(fn)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
        return p

    def test_bundle_runs_here(self):
        new = self.bundle('new.tar.gz', 'f50 u30air\n')
        old = self.bundle('old.tar.gz', None)
        f50only = self.bundle('f50.tar.gz', 'f50\n')
        withv50 = self.bundle('v50.tar.gz', 'f50 u30air v50\n')
        # the ZTE V50 (FINDINGS 95): only a kernel that names it (a mainline one with its LED driver), never the 5.4
        # bundle nor one from before; the F50 and the U30 Air take that bundle as any other
        cases = [('f50', new, True), ('f50', old, True), ('u30air', new, True), ('u30air', old, False),
                 ('u30air', f50only, False), ('v50', withv50, True), ('v50', new, False), ('v50', old, False),
                 ('v50', f50only, False), ('f50', withv50, True), ('u30air', withv50, True)]
        for shell in self.each_shell():
            for dev, b, ok in cases:
                self.device(dev)
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                r = self.up(shell, f'tar -tzf "{b}" > "{d}/list"; bundle_runs_here "{b}" "{d}/list" "{d}" && echo YES || echo NO')
                self.assertEqual(r.stdout.strip(), 'YES' if ok else 'NO', (dev, b.name, r.stderr))
                (d / 'devices').unlink(missing_ok=True)

    def test_card_installation_needs_an_sd_capable_kernel(self):
        rootdev = self.tmp / 'root-dev'
        plain = self.bundle('plain.tar.gz', 'f50\n')
        empty = self.bundle('empty.tar.gz', 'f50\n', '')
        sd = self.bundle('sd.tar.gz', 'f50\n', 'other\nsdcard\n')
        for shell in self.each_shell():
            def run(dev, b):
                if dev is None:
                    rootdev.unlink(missing_ok=True)
                else:
                    rootdev.write_text(dev + '\n')
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                code = (f'MU300_ROOT_DEV_FILE="{rootdev}"; tar -tzf "{b}" > "{d}/list"; '
                        f'if root_on_sd && ! bundle_has_sd "{b}" "{d}/list"; then echo REFUSE; else echo OK; fi')
                r = self.up(shell, code)
                return r.stdout.strip()
            self.assertEqual(run('/dev/mmcblk1p1', plain), 'REFUSE')
            self.assertEqual(run('/dev/mmcblk1p1', empty), 'REFUSE')
            self.assertEqual(run('/dev/mmcblk1p1', sd), 'OK')
            self.assertEqual(run('mmcblk0@27762098176', plain), 'OK')
            # the internal region on an eMMC that came up as mmcblk1 (FINDINGS 31l)
            self.assertEqual(run('mmcblk1@27762098176', plain), 'OK')
            self.assertEqual(run(None, plain), 'OK')
            # another installer's init on the card names only the device it mounted (kanoqwq's 5161859d)
            (self.root / 'run/mu300').mkdir(parents=True, exist_ok=True)
            (self.root / 'run/mu300/disk-dev').write_text('/dev/mmcblk1p1\n')
            (self.root / 'sys/class/block/mmcblk1/device').mkdir(parents=True, exist_ok=True)
            (self.root / 'sys/class/block/mmcblk1/device/type').write_text('SD\n')
            self.assertEqual(run(None, plain), 'REFUSE')
            self.assertEqual(run(None, sd), 'OK')
            (self.root / 'run/mu300/disk-dev').unlink()

    def test_a_disk_another_installer_put_elsewhere_keeps_the_boot_image(self):
        # issue #290: a V50 installed with kanoqwq's desktop installer ran from a GPT partition named mu300root (or
        # from an image file in userdata); the release's boot image went in, its init found no system, waited 5 minutes
        # in the rescue shell and went back to Android. That init leaves no /run/mu300-root-dev, only
        # /run/mu300/disk-dev
        rootdev = self.tmp / 'root-dev'
        plain = self.bundle('plain.tar.gz', 'f50\n', 'sdcard\nlinux-slot\n')
        gpt = self.bundle('gpt.tar.gz', 'f50\n', 'sdcard\nlinux-slot\ngpt-root\n')
        sysb = self.root / 'sys/class/block'
        for name, uevent in (('mmcblk0p60', 'PARTNAME=mu300root'), ('mmcblk0p5', 'PARTNAME=cache'),
                             ('mmcblk1p1', '')):
            (sysb / name).mkdir(parents=True, exist_ok=True)
            (sysb / name / 'uevent').write_text(f'DEVTYPE=partition\n{uevent}\n')
        for name, backing in (('loop0', '/dev/mmcblk0'), ('loop1', '/mu300-data/mu300linux/openwrt.ext4'),
                              ('loop2', '')):
            (sysb / name / 'loop').mkdir(parents=True, exist_ok=True)
            if backing:
                (sysb / name / 'loop/backing_file').write_text(backing + '\n')
        diskdev = self.root / 'run/mu300/disk-dev'
        for shell in self.each_shell():
            def run(root_dev, disk_dev, b):
                rootdev.unlink(missing_ok=True)
                diskdev.unlink(missing_ok=True)
                if root_dev:
                    rootdev.write_text(root_dev + '\n')
                if disk_dev:
                    diskdev.write_text(disk_dev + '\n')
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                r = self.up(shell, f'MU300_ROOT_DEV_FILE="{rootdev}"; tar -tzf "{b}" > "{d}/list"; '
                                   f'printf "%s " "$(root_layout)"; '
                                   f'if bundle_finds_root "{b}" "{d}/list"; then echo OK; else echo REFUSE; fi')
                self.assertEqual(r.stderr, '', (root_dev, disk_dev))
                return r.stdout.strip()
            # this project's init says where: always found as before
            self.assertEqual(run('mmcblk0@27762098176', '/dev/loop0', plain), 'region OK')
            self.assertEqual(run('/dev/mmcblk1p1', '/dev/mmcblk1p1', plain), 'sd OK')
            self.assertEqual(run('mmcblk0@mu300root', '/dev/mmcblk0p60', plain), 'part REFUSE')
            self.assertEqual(run('mmcblk0@mu300root', '/dev/mmcblk0p60', gpt), 'part OK')
            # another init: only the device it mounted
            self.assertEqual(run(None, '/dev/mmcblk0p60', plain), 'part REFUSE')
            self.assertEqual(run(None, '/dev/mmcblk0p60', gpt), 'part OK')
            self.assertEqual(run(None, '/dev/loop1', plain), 'image REFUSE')
            self.assertEqual(run(None, '/dev/loop1', gpt), 'image REFUSE')
            self.assertEqual(run(None, '/dev/loop0', plain), 'region OK')
            # what cannot be told stays as before: never a refusal on a guess
            self.assertEqual(run(None, '/dev/loop2', plain), 'unknown OK')
            self.assertEqual(run(None, '/dev/mmcblk0p5', plain), 'unknown OK')
            self.assertEqual(run(None, '/dev/mmcblk1p1', plain), 'unknown OK')
            self.assertEqual(run(None, None, plain), 'unknown OK')
            # another init's TF installation: a device of a disk the kernel calls an SD card (kanoqwq's 5161859d)
            (sysb / 'mmcblk1/device').mkdir(parents=True, exist_ok=True)
            (sysb / 'mmcblk1/device/type').write_text('SD\n')
            self.assertEqual(run(None, '/dev/mmcblk1p1', plain), 'sd OK')
            (sysb / 'mmcblk1/device/type').unlink()
        src = (BIN / 'mu300-update').read_text()
        # boot_update refuses before it writes, and apply before it changes a system
        bu = src[src.index('boot_update_run() {'):src.index('\n}\n', src.index('boot_update_run() {'))]
        self.assertLess(bu.index('bundle_finds_root'), bu.index('write_boot'))
        ap = src[src.index('\napply() {'):src.index('\n}\n', src.index('\napply() {'))]
        self.assertLess(ap.index('bundle_finds_root'), ap.index('all files are here'))
        self.assertLess(ap.index('all files are here'), ap.index('apply_one'))


# Update's setUp, device() and up() live in UpdateBase, which has no tests; Update and FromStock both derive from it
# (deriving from Update would run its tests twice).
@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class FromStock(UpdateBase):
    """bootimg_from_stock gives the bytes boot/build-boot-image.py gives, from the same stock image, kernel and
    ramdisk: the header page of Android's image with the new sizes, loglevel=5 and no signature, its vbmeta, its
    footer with the new sizes."""

    def python_image(self, stock):
        mods = self.tmp / 'mods'; mods.mkdir(exist_ok=True)
        for n in (BOOT / 'module-order.txt').read_text().split():
            (mods / n).write_bytes(b'\x7fELF ' + n.encode())
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 12345)
        out = self.tmp / 'py.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(stock),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(mods), '--busybox', str(self.tmp / 'busybox'), '--logdw', str(self.tmp / 'logdw'),
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'), '--out', str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        (self.tmp / 'ramdisk').write_bytes(img[roff:roff + rsz])
        osz, _, vbs = struct.unpack_from('>QQQ', img, len(img) - 64 + 12)
        return img[:osz + vbs], img[-64:]

    def check(self, **stock_args):
        stock = self.tmp / 'stock.img'
        fake_stock(stock, **stock_args)
        head, footer = self.python_image(stock)
        d = self.tmp / 'out'
        for shell in self.each_shell():
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/Image" "{self.tmp}/ramdisk" "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / 'new').read_bytes() + (d / 'vbmeta').read_bytes(), head, shell)
            self.assertEqual((d / 'newfooter').read_bytes(), footer, shell)

    def test_same_bytes_as_the_python_builder(self):
        self.check()

    def test_unaligned_vbmeta(self):
        self.check(vbmeta_offset=(1 << 20) + 100)

    def test_cmdline_is_loglevel_5_whatever_the_stock_one_is(self):
        # the Android-side switch arms a slot only when it sees this marker; stock headers have it empty, but a
        # stock image with a command line of its own must not leak it into the Linux image
        stock = self.tmp / 'stock.img'
        fake_stock(stock)
        raw = bytearray(stock.read_bytes())
        raw[44:44 + 1536] = b'console=ttyMSM0 androidboot.x=1'.ljust(1536, b'\0')
        stock.write_bytes(bytes(raw))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        d = self.tmp / 'out'
        for shell in self.each_shell():
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/k" "{self.tmp}/r" "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / 'new').read_bytes()[44:44 + 1536], b'loglevel=5'.ljust(1536, b'\0'), shell)

    def test_refuses_what_is_not_a_v4_image(self):
        bad = self.tmp / 'bad.img'
        bad.write_bytes(bytes(1 << 20))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        for shell in self.each_shell():
            r = self.up(shell, f'bootimg_from_stock "{bad}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not hold a boot image header v4', r.stderr)

    def test_refuses_a_v4_image_without_an_avb_footer(self):
        stock = self.tmp / 'stock.img'
        fake_stock(stock)
        raw = bytearray(stock.read_bytes())
        raw[-64:-60] = b'XXXX'
        stock.write_bytes(bytes(raw))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        for shell in self.each_shell():
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('has no AVB footer', r.stderr)

    def test_refuses_a_result_that_reaches_the_persistent_log(self):
        stock = self.tmp / 'stock.img'
        fake_stock(stock)
        (self.tmp / 'k').write_bytes(b'k' * 4096); (self.tmp / 'r').write_bytes(b'r')
        for shell in self.each_shell():
            r = self.up(shell, f'PERSIST_LOG=8192; bootimg_from_stock "{stock}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('persistent log area', r.stderr)
            # the sizes it is made of, so a doubled file (two updaters in one directory) shows as one
            self.assertIn('(12288 bytes: kernel 4096, ramdisks 1, with vbmeta', r.stderr)

    def test_refuses_a_file_another_run_appended_to(self):
        # two mu300-updates in the same $STAGE/boot each appended their kernel to new (a 56 MB "image" refused by the
        # persistent log check); a new file that is not what was just assembled is refused as that, and never written
        stock = self.tmp / 'stock.img'
        fake_stock(stock)
        (self.tmp / 'k').write_bytes(b'k' * 4096); (self.tmp / 'r').write_bytes(b'r')
        grow = 'pad_page() { p=$(( (4096 - $(fsize "$1") % 4096) % 4096 )); [ "$p" = 0 ] || head -c "$p" /dev/zero >> "$1"; ' \
               'head -c 4096 /dev/zero >> "$1"; }'
        for shell in self.each_shell():
            r = self.up(shell, f'{grow}; bootimg_from_stock "{stock}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not the image just assembled', r.stderr)
            self.assertIn('another mu300-update', r.stderr)
            self.assertFalse((self.tmp / 'newfooter').exists())

    def test_apply_keeps_the_vendor_files_the_image_has_a_directory_for(self):
        # the images ship opt/mu300/android/system/bin/cltest and a few firmware files, so the vendor directories
        # exist in the new system; busybox's `cp -an old/. new/` then skips them as existing and copies nothing (an
        # update run from OpenWrt left every system without the Android userspace: no modem, no Wi-Fi)
        img = self.tmp / 'img'
        for f, data in [('sbin/init', b'#!/bin/sh\n'), ('opt/mu300/android/system/bin/cltest', b'image'),
                        ('lib/firmware/regulatory.db', b'image'), ('etc/mu300/image-version', b'v2\n')]:
            (img / f).parent.mkdir(parents=True, exist_ok=True)
            (img / f).write_bytes(data)
        (img / 'sbin/init').chmod(0o755)
        old = self.disk / 'openwrt'
        for f, data in [('sbin/init', b'#!/bin/sh\n'), ('opt/mu300/android/system/bin/cltest', b'old'),
                        ('opt/mu300/android/system/bin/cp_diskserver', b'vendor'),
                        ('opt/mu300/android/vendor/lib/libril.so', b'vendor'),
                        ('opt/mu300/android/dev-properties/property_info', b'props'),
                        ('lib/firmware/wcnmodem.bin', b'fw'), ('lib/firmware/regulatory.db', b'old'),
                        ('lib/modules/5.4.254/x.ko', b'ko'), ('etc/mu300/vpn.conf', b'mine'),
                        # adb: off, keys only, and the keys - somebody's choice, which an update must not turn
                        # back into the default (on, and open to every computer)
                        ('etc/mu300/adb', b'off\n'), ('etc/mu300/adb_auth', b'keys\n'),
                        ('etc/mu300/adb_keys', b'QAAAAkey me@pc\n')]:
            (old / f).parent.mkdir(parents=True, exist_ok=True)
            (old / f).write_bytes(data)
        (old / 'etc/mu300/adb_keys').chmod(0o600)
        (old / 'opt/mu300/android/system/lib').symlink_to('../vendor/lib')
        stage = self.disk / '.mu300-update'
        for shell in self.each_shell():
            for d in ('openwrt.old', 'openwrt.new'):
                shutil.rmtree(self.disk / d, ignore_errors=True)
            stage.mkdir(exist_ok=True)
            subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
            r = self.up(shell, 'is_root() { false; }; apply_one openwrt v2', **self.busybox_applets(shell))
            self.assertEqual(r.returncode, 0, r.stderr)
            new = self.disk / 'openwrt'
            self.assertEqual((new / 'opt/mu300/android/system/bin/cp_diskserver').read_bytes(), b'vendor')
            self.assertEqual((new / 'opt/mu300/android/vendor/lib/libril.so').read_bytes(), b'vendor')
            self.assertEqual((new / 'opt/mu300/android/dev-properties/property_info').read_bytes(), b'props')
            self.assertEqual(os.readlink(new / 'opt/mu300/android/system/lib'), '../vendor/lib')
            self.assertEqual((new / 'lib/firmware/wcnmodem.bin').read_bytes(), b'fw')
            self.assertEqual((new / 'lib/modules/5.4.254/x.ko').read_bytes(), b'ko')
            # what the image has stays the image's
            self.assertEqual((new / 'opt/mu300/android/system/bin/cltest').read_bytes(), b'image')
            self.assertEqual((new / 'lib/firmware/regulatory.db').read_bytes(), b'image')
            self.assertEqual((new / 'etc/mu300/vpn.conf').read_bytes(), b'mine')
            self.assertEqual((new / 'etc/mu300/adb').read_bytes(), b'off\n')
            self.assertEqual((new / 'etc/mu300/adb_auth').read_bytes(), b'keys\n')
            self.assertEqual((new / 'etc/mu300/adb_keys').read_bytes(), b'QAAAAkey me@pc\n')
            self.assertEqual((new / 'etc/mu300/adb_keys').stat().st_mode & 0o777, 0o600)
            # the next shell updates from this one (disk/openwrt again)

    def test_apply_keeps_the_settings_and_adds_the_images_new_config_files(self):
        # the old etc/config is kept, but a package the new image brings has its own file there: copied whole, the
        # old directory dropped /etc/config/sqm and LuCI's SQM page failed ("uci/get: Resource not found")
        img = self.tmp / 'img-cfg'
        for f, data in [('sbin/init', b'#!/bin/sh\n'), ('etc/config/network', b'image'), ('etc/config/sqm', b'image sqm'),
                        ('etc/mu300/image-version', b'v2\n')]:
            (img / f).parent.mkdir(parents=True, exist_ok=True)
            (img / f).write_bytes(data)
        (img / 'sbin/init').chmod(0o755)
        (img / 'etc/crontabs').mkdir(parents=True)     # the image ships the directory, empty
        old = self.disk / 'openwrt'
        for f, data in [('sbin/init', b'#!/bin/sh\n'), ('etc/config/network', b'mine'), ('etc/config/tailscale', b'mine too'),
                        ('etc/unisoc-modem/lock-state.d/lte_band', b'3,7'), ('etc/unisoc-modem/panel-ops.log', b'log'),
                        ('etc/crontabs/root', b'0 4 * * * reboot\n')]:
            (old / f).parent.mkdir(parents=True, exist_ok=True)
            (old / f).write_bytes(data)
        stage = self.disk / '.mu300-update'
        for shell in self.each_shell():
            for d in ('openwrt.old', 'openwrt.new'):
                shutil.rmtree(self.disk / d, ignore_errors=True)
            stage.mkdir(exist_ok=True)
            subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
            r = self.up(shell, 'is_root() { false; }; apply_one openwrt v2', **self.busybox_applets(shell))
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = self.disk / 'openwrt/etc/config'
            self.assertEqual((cfg / 'network').read_bytes(), b'mine')          # the device's settings win
            self.assertEqual((cfg / 'tailscale').read_bytes(), b'mine too')    # and stay
            self.assertEqual((cfg / 'sqm').read_bytes(), b'image sqm')         # the new package's file is added
            self.assertFalse((self.disk / 'openwrt/etc/config.image').exists())
            # the panel's saved locks come along (they are replayed at boot, and the modem holds them anyway)
            state = self.disk / 'openwrt/etc/unisoc-modem'
            self.assertEqual((state / 'lock-state.d/lte_band').read_bytes(), b'3,7')
            self.assertEqual((state / 'panel-ops.log').read_bytes(), b'log')
            # and the jobs of System > Scheduled Tasks come along (the image's own /etc/crontabs is empty)
            self.assertEqual((self.disk / 'openwrt/etc/crontabs/root').read_bytes(), b'0 4 * * * reboot\n')

    def test_copy_missing_never_writes_through_a_link_of_the_target(self):
        a, b, outside = self.tmp / 'a', self.tmp / 'b', self.tmp / 'outside'
        (a / 'd/x').mkdir(parents=True)
        (a / 'd/x/f').write_text('a')
        (a / 'name with space').write_text('s')
        b.mkdir(); outside.mkdir()
        (b / 'd').symlink_to(outside)
        for shell in self.each_shell():
            r = self.up(shell, f'copy_missing "{a}" "{b}"', **self.busybox_applets(shell))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual((b / 'name with space').read_text(), 's')
            (b / 'name with space').unlink()
            r = self.up(shell, f'copy_missing "{self.tmp}/missing" "{b}"', **self.busybox_applets(shell))
            self.assertNotEqual(r.returncode, 0)

    def test_apply_takes_the_vendor_files_only_the_previous_old_copy_still_has(self):
        # a device updated by an updater with the busybox cp bug: the system in place has the image's vendor
        # directories but none of the device's files, and the only copy is in openwrt.old, which the update replaces
        img = self.tmp / 'img'
        for f, data in [('sbin/init', b'#!/bin/sh\n'), ('opt/mu300/android/system/bin/cltest', b'image'),
                        ('lib/firmware/regulatory.db', b'image')]:
            (img / f).parent.mkdir(parents=True, exist_ok=True)
            (img / f).write_bytes(data)
        (img / 'sbin/init').chmod(0o755)
        stage = self.disk / '.mu300-update'
        for shell in self.each_shell():
            for d in ('openwrt', 'openwrt.old', 'openwrt.new'):
                shutil.rmtree(self.disk / d, ignore_errors=True)
            hit, good = self.disk / 'openwrt', self.disk / 'openwrt.old'
            for f, data in [('sbin/init', b'#!/bin/sh\n'), ('opt/mu300/android/system/bin/cltest', b'image'),
                            ('lib/firmware/regulatory.db', b'image'), ('lib/firmware/mine.bin', b'newer')]:
                (hit / f).parent.mkdir(parents=True, exist_ok=True)
                (hit / f).write_bytes(data)
            for f, data in [('sbin/init', b'#!/bin/sh\n'), ('opt/mu300/android/system/bin/cp_diskserver', b'vendor'),
                            ('opt/mu300/android/vendor/lib/libril.so', b'vendor'),
                            ('lib/firmware/wcnmodem.bin', b'fw'), ('lib/firmware/mine.bin', b'older'),
                            ('lib/firmware/regulatory.db', b'old')]:
                (good / f).parent.mkdir(parents=True, exist_ok=True)
                (good / f).write_bytes(data)
            stage.mkdir(exist_ok=True)
            subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
            r = self.up(shell, 'is_root() { false; }; apply_one openwrt v2', **self.busybox_applets(shell))
            self.assertEqual(r.returncode, 0, r.stderr)
            new = self.disk / 'openwrt'
            self.assertEqual((new / 'opt/mu300/android/system/bin/cp_diskserver').read_bytes(), b'vendor')
            self.assertEqual((new / 'opt/mu300/android/vendor/lib/libril.so').read_bytes(), b'vendor')
            self.assertEqual((new / 'lib/firmware/wcnmodem.bin').read_bytes(), b'fw')
            # gaps only: the system in place and the image come first
            self.assertEqual((new / 'lib/firmware/mine.bin').read_bytes(), b'newer')
            self.assertEqual((new / 'lib/firmware/regulatory.db').read_bytes(), b'image')

    def test_modules_into_every_system(self):
        b = self.tmp / 'bundle'
        (b / 'modules').mkdir(parents=True)
        (b / 'modules' / 'a.ko').write_bytes(b'a')
        (b / 'kernel.release').write_text('6.18.55-mu300\n')
        (b / 'modules.builtin').write_text('kernel/x.ko\n')
        (self.disk / 'openwrt').mkdir()
        for shell in self.each_shell():
            # a module of an earlier bundle of this release that this one no longer has goes
            for d in ('ubuntu/lib/modules/6.18.55-mu300/extra', 'openwrt/lib/modules/6.18.55-mu300'):
                (self.disk / d).mkdir(parents=True, exist_ok=True)
                (self.disk / d / 'gone.ko').write_bytes(b'old')
            r = self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1)
            self.assertFalse((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/extra/gone.ko').exists())
            self.assertFalse((self.disk / 'openwrt/lib/modules/6.18.55-mu300/gone.ko').exists())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/extra/a.ko').read_bytes(), b'a')
            self.assertTrue((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.builtin').exists())
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.order').read_bytes(), b'')
            self.assertEqual((self.disk / 'openwrt/lib/modules/6.18.55-mu300/a.ko').read_bytes(), b'a')
        (b / 'kernel.release').write_text('../evil\n')
        for shell in self.each_shell():
            self.assertNotEqual(self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1).returncode, 0)

    def test_a_new_boot_image_unlocks_for_its_trial(self):
        src = (BIN / 'mu300-update').read_text()
        bu = src[src.index('boot_update_run() {'):src.index('\n}\n', src.index('boot_update_run() {'))]
        self.assertLess(bu.index('unlock_for_trial'), bu.index('write_boot "$dev" "$psize" "$tmp/new"'))
        rb = src[src.index('rollback_boot() {'):src.index('\n}\n', src.index('rollback_boot() {'))]
        self.assertLess(rb.index('unlock_for_trial'), rb.index('write_boot'))
        nb = self.tmp / 'nb'
        (nb / 'mu300-next-boot').parent.mkdir(parents=True, exist_ok=True)
        (nb / 'mu300-next-boot').write_text(f'#!/bin/sh\necho "$@" >> "{self.tmp}/nb.log"\n')
        (nb / 'mu300-next-boot').chmod(0o755)
        for shell in self.each_shell():
            r = self.sh(shell, f'. "{BIN}/mu300-update"; unlock_for_trial', MU300_LIB=1, MU300_BIN=nb,
                        MU300_DISK=self.disk, MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set((self.tmp / 'nb.log').read_text().split()), {'--trial'})

    def test_a_next_boot_from_before_the_trial_hands_it_to_the_new_system(self):
        # issue #290: an update from a system whose mu300-next-boot has no --trial (usage, exit 2) wrote the boot image
        # unarmed. The copy in the system the update has just installed arms it instead
        nb = self.tmp / 'nb'
        nb.mkdir(parents=True, exist_ok=True)
        old = nb / 'mu300-next-boot'
        old.write_text(f'#!/bin/sh\necho "old $@" >> "{self.tmp}/nb.log"\n'
                       'echo "usage: $0 linux|android|attempts [N]|status" >&2\nexit 2\n')
        old.chmod(0o755)
        new = self.disk / 'openwrt/opt/mu300/bin/mu300-next-boot'
        new.parent.mkdir(parents=True, exist_ok=True)
        for shell in self.each_shell():
            for new_knows, want_err in ((True, ''), (False, 'could not arm the Linux slot')):
                (self.tmp / 'nb.log').unlink(missing_ok=True)
                if new_knows:
                    new.write_text(f'#!/bin/sh\necho "new $@" >> "{self.tmp}/nb.log"\nexit 0\n')
                else:
                    new.write_text(old.read_text())
                new.chmod(0o755)
                r = self.sh(shell, f'. "{BIN}/mu300-update"; unlock_for_trial; echo "rc=$?"', MU300_LIB=1, MU300_BIN=nb,
                            MU300_DISK=self.disk, MU300_SYSROOT=self.root)
                self.assertIn('rc=0', r.stdout)
                log = (self.tmp / 'nb.log').read_text().splitlines()
                if new_knows:
                    self.assertEqual(log, ['old --trial', 'new --trial'])
                    self.assertEqual(r.stderr, '')
                else:
                    self.assertIn(want_err, r.stderr)
                    self.assertIn('usage:', r.stderr)
        # a running mu300-next-boot that knows --trial is the only one asked
        (self.tmp / 'nb.log').unlink(missing_ok=True)
        old.write_text(f'#!/bin/sh\necho "running $@" >> "{self.tmp}/nb.log"\nexit 0\n')
        new.write_text(f'#!/bin/sh\necho "new $@" >> "{self.tmp}/nb.log"\nexit 0\n')
        for shell in self.each_shell():
            r = self.sh(shell, f'. "{BIN}/mu300-update"; unlock_for_trial', MU300_LIB=1, MU300_BIN=nb,
                        MU300_DISK=self.disk, MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set((self.tmp / 'nb.log').read_text().splitlines()), {'running --trial'})

    def test_the_trial_arming_says_what_it_could_not_do(self):
        # a boot image written while Android is the default boot is not tried at all, and one whose slot block cannot
        # be rebuilt gets a single boot instead of N: both are said (2026-10-12: silence about the first is how an
        # update ended in Android while its last line promised a reboot). boot_update keeps going - the image is
        # written either way - so unlock_for_trial itself never fails
        nb = self.tmp / 'nb'
        (nb / 'mu300-next-boot').parent.mkdir(parents=True, exist_ok=True)
        for shell in self.each_shell():
            for code, line, want in (('0', '', None),
                                     ('3', 'default boot is not linux: nothing armed', 'the default boot is Android'),
                                     ('1', 'arming with 1 attempt, not 5', 'arming with 1 attempt, not 5')):
                (nb / 'mu300-next-boot').write_text(
                    '#!/bin/sh\n' + (f'echo "{line}" >&2\n' if line else '') + f'exit {code}\n')
                (nb / 'mu300-next-boot').chmod(0o755)
                r = self.sh(shell, f'. "{BIN}/mu300-update"; unlock_for_trial; echo "rc=$?"', MU300_LIB=1, MU300_BIN=nb,
                            MU300_DISK=self.disk, MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('rc=0', r.stdout, code)
                if want:
                    self.assertIn(want, r.stderr, code)
                else:
                    self.assertEqual(r.stderr, '', code)

    def test_the_reboot_line_knows_android_is_the_default_boot(self):
        # what apply, kernel, rollback and boot print last: with Android as the default boot a plain reboot starts
        # Android, so the way to Linux takes the place of "Reboot to start it" (2026-10-12)
        f = self.tmp / 'default-boot'
        for shell in self.each_shell():
            for value in ('linux\n', 'android\n', None):
                if value is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(value)
                r = self.sh(shell, f'. "{BIN}/mu300-update"; reboot_goes_to_android || echo REBOOT-LINE',
                            MU300_LIB=1, MU300_BIN=BIN, MU300_DISK=self.disk, MU300_SYSROOT=self.root,
                            MU300_DEFAULT_BOOT=f)
                self.assertEqual(r.returncode, 0, r.stderr)
                if value == 'linux\n':
                    self.assertEqual(r.stdout, 'REBOOT-LINE\n')
                else:
                    self.assertIn('The default boot is Android', r.stdout, value)
                    self.assertIn('mu300-next-boot linux', r.stdout, value)
                    self.assertNotIn('REBOOT-LINE', r.stdout, value)

    def test_rollback_boot_brings_the_modules_of_its_image_back(self):
        # two builds of one kernel release share lib/modules/<release>: the set in place is kept with the image it
        # runs with, and goes back with it (seen on F50-B: rollback-boot to a 6.18.55 image with the other
        # release's 6.18.55 modules did not come up)
        b = self.tmp / 'bundle'
        (b / 'modules').mkdir(parents=True)
        (b / 'modules' / 'a.ko').write_bytes(b'new')
        (b / 'modules' / 'b.ko').write_bytes(b'new')
        (b / 'kernel.release').write_text('6.18.55\n')
        (self.disk / 'openwrt').mkdir()
        dirs = ('ubuntu/lib/modules/6.18.55/extra', 'openwrt/lib/modules/6.18.55')
        for shell in self.each_shell():
            shutil.rmtree(self.disk / 'boot', ignore_errors=True)
            for d in dirs:
                shutil.rmtree(self.disk / d, ignore_errors=True)
                (self.disk / d).mkdir(parents=True)
                (self.disk / d / 'a.ko').write_bytes(b'old')
            r = self.up(shell, f'BOOTDIR="{self.disk}/boot"; mkdir -p "$BOOTDIR"; prev_modules_save "{b}" && '
                               f'kernel_modules_into_systems "{b}" >/dev/null && prev_modules_commit', MU300_NO_DEPMOD=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            for d in dirs:
                self.assertEqual(sorted(f.name for f in (self.disk / d).glob('*.ko')), ['a.ko', 'b.ko'])
            r = self.up(shell, f'BOOTDIR="{self.disk}/boot"; prev_modules_restore', MU300_NO_DEPMOD=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('modules of the previous image (6.18.55) are back', r.stdout)
            for d in dirs:
                self.assertEqual({f.name: f.read_bytes() for f in (self.disk / d).glob('*.ko')}, {'a.ko': b'old'})
            # a system that had no set of this release then has none after the rollback
            shutil.rmtree(self.disk / dirs[1])
            r = self.up(shell, f'BOOTDIR="{self.disk}/boot"; prev_modules_save "{b}" && '
                               f'kernel_modules_into_systems "{b}" >/dev/null && prev_modules_commit && '
                               f'prev_modules_restore >/dev/null',
                        MU300_NO_DEPMOD=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list((self.disk / dirs[1]).glob('*.ko')), [])
            # a 5.4 bundle (no kernel.release) leaves the mainline sets alone, and keeps nothing to restore
            (b / 'kernel.release').rename(b / 'kr')
            r = self.up(shell, f'BOOTDIR="{self.disk}/boot"; prev_modules_save "{b}"; prev_modules_commit; prev_modules_restore; echo done')
            (b / 'kr').rename(b / 'kernel.release')
            self.assertEqual(r.stdout, 'done\n', r.stderr)
            self.assertFalse((self.disk / 'boot/prev.modules').exists())

    def test_boot_update_keeps_the_modules_before_it_replaces_them(self):
        src = (BIN / 'mu300-update').read_text()
        bu = src[src.index('boot_update_run() {'):src.index('\n}\n', src.index('boot_update_run() {'))]
        # the replacing set goes in after the old one is kept, and before the image is written
        self.assertLess(bu.index('prev_modules_save "$tmp"'), bu.index('! kernel_modules_into_systems "$tmp"'))
        self.assertLess(bu.index('! kernel_modules_into_systems "$tmp"'), bu.index('write_boot "$dev" "$psize" "$tmp/new"'))
        # the kept set becomes the previous image's together with prev.body, before the write
        self.assertLess(bu.index('! kernel_modules_into_systems "$tmp"'), bu.index('prev_modules_commit\n'))
        self.assertLess(bu.index('prev_modules_commit\n'), bu.index('mv -f "$tmp/body" "$BOOTDIR/prev.body"'))
        self.assertLess(bu.index('prev_modules_commit\n'), bu.index('write_boot "$dev" "$psize" "$tmp/new"'))
        # a failed write puts the old modules back with the old image
        fail = bu[bu.index('failed verification; restoring'):]
        self.assertIn('prev_modules_restore', fail[:fail.index('return 1')])
        rb = src[src.index('rollback_boot() {'):src.index('\n}\n', src.index('rollback_boot() {'))]
        self.assertLess(rb.index('write_boot'), rb.index('prev_modules_restore'))


class KernelSetOptionalArgs(UpdateBase):
    def test_optional_release_under_nounset(self):
        boot = self.disk / 'boot'
        boot.mkdir()
        args = self.tmp / 'args'
        self.stub('uname', 'echo 7.2.10')
        for shell in self.each_shell():
            for kept in (False, True):
                for new in ('', '7.2.11'):
                    prev = boot / 'prev.body'
                    prev.unlink(missing_ok=True)
                    args.unlink(missing_ok=True)
                    if kept:
                        prev.write_bytes(b'kept image')
                    r = self.up(shell, f"""set -u
BOOTDIR=\"$T_BOOT\"
BOOT_SLOT_KREL=7.2.10 BOOT_KEPT_KREL=7.2.9
fsize() {{ echo 1024; }}
kernel_sets() {{ printf 'call' >> \"$T_ARGS\"; printf ' <%s>' \"$@\" >> \"$T_ARGS\"; echo >> \"$T_ARGS\"; }}
kernel_sets_kb() {{ echo 0; }}
kernel_sets_remove() {{ :; }}
boot_sets_stale {new}
n=$(boot_kept_kb {new})
[ \"$n\" = {1 if kept else 0} ] || exit 90
boot_kept_drop {new}
""", T_BOOT=boot, T_ARGS=args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stderr, '')
                    tail = ['7.2.10'] + ([new] if new else [])
                    calls = [['7.2.10'] + (['7.2.9'] if kept else []) + tail]
                    if kept:
                        calls.append(['7.2.10'] + tail)
                    calls.append(['7.2.10'] + tail)
                    self.assertEqual(args.read_text().splitlines(), ['call' + ''.join(f' <{a}>' for a in c) for c in calls])


class KernelStep(UpdateBase):
    """boot_update as a whole against a boot partition that is a file (FINDINGS 177): since every module is built a
    kernel update wanted 500 MB free at once and left its unpacked bundle behind when it failed. The step now unpacks
    once, removes the bundle once unpacked, gives module sets more names instead of copying them, keeps the image it
    read by moving it, and leaves nothing of itself behind however it ends - with the one-boot fallback's needs kept:
    the image before and the set it runs with, together."""

    KREL = '6.18.57'
    PSIZE = 4 << 20

    def setUp(self):
        super().setUp()
        (self.disk / 'openwrt').mkdir()
        (self.root / 'run/mu300/linux-slot').write_text('b\n')
        self.part = self.tmp / 'boot_b.img'
        self.src = self.tmp / 'src'
        self.src.mkdir()
        (self.disk / 'boot').mkdir()
        (self.disk / 'boot/kernel').write_text('6.18\n')

    def old_image(self, krel=None):
        """the image in the slot: header v4 with a 4 KiB kernel (with the banner of KREL) and an LZ4-legacy ramdisk,
        vbmeta at 1 MiB, footer (and the kernel choice 6.18: a rollback-boot sets it back)"""
        (self.disk / 'boot/kernel').write_text('6.18\n')
        img = bytearray(self.PSIZE)
        img[0:8] = b'ANDROID!'
        struct.pack_into('<III', img, 8, 4096, 16, 0)
        struct.pack_into('<I', img, 40, 4)
        img[4096:8192] = (f'Linux version {krel} (b@h) #1 SMP'.encode() if krel else b'').ljust(4096, b'o')
        img[8192:8208] = b'\x02\x21\x4c\x18' + b'd' * 12
        vbo, vbmeta = 1 << 20, b'V' * 2304
        img[vbo:vbo + len(vbmeta)] = vbmeta
        footer = bytearray(64)
        footer[0:4] = b'AVBf'
        struct.pack_into('>IIQQQ', footer, 4, 1, 0, vbo, vbo, len(vbmeta))
        img[-64:] = footer
        self.part.write_bytes(bytes(img))
        return bytes(img)

    def make_bundle(self, mods, krel=None):
        p = self.src / 'mu300-kernel-6.18.tar.gz'
        files = [('./Image', b'N' * 100 + f'Linux version {krel or self.KREL} (b@h)'.encode() + b'N' * 8000), ('./ramdisk-generic.lz4', b'\x02\x21\x4c\x18generic'),
                 ('./devices', b'f50 u30air\n'), ('./features', b'sdcard\nlinux-slot\n'),
                 ('./kernel.release', ((krel or self.KREL) + '\n').encode()), ('./modules.builtin', b'kernel/x.ko\n'),
                 ('./kernel.config', b'CONFIG_X=y\n' * 100)]
        files += [(f'./modules/{n}', d) for n, d in mods.items()]
        with tarfile.open(p, 'w:gz') as t:
            # the directory as the release's bundles have it (busybox tar extracts ./modules only by that entry)
            ti = tarfile.TarInfo('./modules')
            ti.type, ti.mode = tarfile.DIRTYPE, 0o755
            t.addfile(ti)
            for fn, data in files:
                ti = tarfile.TarInfo(fn)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
        return p

    def systems(self, mods):
        for d in self.mod_dirs():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
            for n, data in mods.items():
                (d / n).write_bytes(data)

    def mod_dirs(self, krel=None):
        k = krel or self.KREL
        return [self.disk / f'ubuntu/lib/modules/{k}/extra', self.disk / f'openwrt/lib/modules/{k}']

    def kept(self, mods):
        """the set kept with the image before the one in the slot (an update before this one)"""
        pm = self.disk / 'boot/prev.modules'
        shutil.rmtree(pm, ignore_errors=True)
        for os_ in ('ubuntu', 'openwrt'):
            (pm / os_).mkdir(parents=True)
            for n, data in mods.items():
                (pm / os_ / n).write_bytes(data)
        (pm / 'kernel.release').write_text(self.KREL + '\n')
        (self.disk / 'boot/prev.body').write_bytes(b'image before')

    def ko(self, d):
        return {f.name: f.read_bytes() for f in sorted(d.glob('*.ko'))}

    STUBS = ('boot_dev() { echo "$PART"; }; part_bytes() { echo %d; }; unlock_for_trial() { :; }; '
             'fetch_verified() { cp "$SRC/$2" "$STAGE/$2"; }; asset_kb() { echo 100; }; ')

    def run_step(self, shell, extra='', **env):
        return self.up(shell, (self.STUBS % self.PSIZE) + extra + 'boot_update v2026.10.18.1; echo "rc=$?"',
                       PART=self.part, SRC=self.src, MU300_NO_DEPMOD=1, **env)

    def leftovers(self):
        stage = self.disk / '.mu300-update'
        return sorted(str(p.relative_to(self.disk)) for p in
                      [*stage.glob('boot*'), *stage.glob('mu300-kernel*'), *(self.disk / 'boot').glob('prev.modules.new')])

    def test_a_kernel_update_links_the_sets_and_keeps_the_image_before_with_its_own(self):
        old = {'a.ko': b'old a', 'gone.ko': b'old gone'}
        new = {'a.ko': b'new a', 'b.ko': b'new b'}
        for shell in self.each_shell():
            img = self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            self.make_bundle(new)
            (self.disk / 'boot/prev.vbmeta').unlink(missing_ok=True)
            r = self.run_step(shell, **self.busybox_applets(shell))
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('boot: updated to v2026.10.18.1', r.stdout)
            # the new image is in the slot; the one that was is kept whole (moved from the stage, not copied)
            part = self.part.read_bytes()
            self.assertEqual(part[4096:4196], b'N' * 100)
            self.assertEqual((self.disk / 'boot/prev.body').read_bytes(), img[:1 << 20])
            self.assertEqual((self.disk / 'boot/prev.vbmeta').read_bytes(), b'V' * 2304)
            # the systems have the new set, one file for both systems and the stage's unpacked copy gone
            u, o = self.mod_dirs()
            self.assertEqual(self.ko(u), new)
            self.assertEqual(self.ko(o), new)
            self.assertEqual((u / 'a.ko').stat().st_ino, (o / 'a.ko').stat().st_ino)
            self.assertEqual((u / 'a.ko').stat().st_nlink, 2)
            # the set the image before runs with is kept with it, as the same files (no copy)
            pm = self.disk / 'boot/prev.modules'
            self.assertEqual(self.ko(pm / 'ubuntu'), old)
            self.assertEqual(self.ko(pm / 'openwrt'), old)
            self.assertEqual((pm / 'kernel.release').read_text().strip(), self.KREL)
            # nothing of the step is left: no unpacked bundle, no download, no half-kept set
            self.assertEqual(self.leftovers(), [])
            # and rollback-boot puts both back, keeping them kept
            r = self.up(shell, (self.STUBS % self.PSIZE) + 'rollback_boot; echo "rc=$?"', PART=self.part,
                        MU300_NO_DEPMOD=1, **self.busybox_applets(shell))
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.part.read_bytes()[:1 << 20], img[:1 << 20])
            self.assertEqual(self.ko(u), old)
            self.assertEqual(self.ko(o), old)
            self.assertEqual(self.ko(pm / 'ubuntu'), old)

    def test_too_little_room_changes_nothing_and_leaves_nothing(self):
        old = {'a.ko': b'old a'}
        for shell in self.each_shell():
            img = self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            self.make_bundle({'a.ko': b'new a' * 1000})
            # before the download (boot, kernel): the estimate; after it (apply has it): the bundle's own numbers
            for staged in (False, True):
                if staged:
                    (self.disk / '.mu300-update').mkdir(exist_ok=True)
                    shutil.copy(self.src / 'mu300-kernel-6.18.tar.gz', self.disk / '.mu300-update/')
                r = self.run_step(shell, 'df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\n'
                                         'x 800000 790000 10 99%% /\\n"; }; ')
                self.assertIn('rc=1', r.stdout, r.stderr)
                self.assertIn('not enough free space on the Linux partition for the kernel', r.stderr)
                self.assertRegex(r.stderr, r'\d+ MB needed .*0 MB free')
                self.assertEqual(self.part.read_bytes(), img)
                for d in self.mod_dirs():
                    self.assertEqual(self.ko(d), old)
                self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), {'a.ko': b'older a'})
                self.assertEqual(self.leftovers(), [])

    def test_short_of_room_the_image_kept_from_before_goes_first(self):
        # after one kernel update F50-C keeps the image before with its own set (120 MB): the next update would not
        # fit beside it. It goes - image and set together - when that makes the room, and the update goes on.
        old = {'a.ko': b'old a'}
        df = 'df() { printf "F 1 2 3 4 5\\nx 800000 790000 5000 99%% /\\n"; }; '
        for shell in self.each_shell():
            img = self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            (self.disk / 'boot/prev.tag').write_text('v2026.10.01.1\n')
            with open(self.disk / 'boot/prev.body', 'wb') as f:
                f.truncate(200 << 20)
            self.make_bundle({'a.ko': b'new a'})
            r = self.run_step(shell, df)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('the boot image kept from before the one in place (v2026.10.01.1) and its modules are given up',
                          r.stdout)
            self.assertEqual((self.disk / 'boot/prev.body').read_bytes(), img[:1 << 20])
            self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), old)
            self.assertEqual(self.leftovers(), [])
            # without the room even then: nothing is given up, nothing changes
            self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            r = self.run_step(shell, df.replace("5000", "10"))
            self.assertIn('rc=1', r.stdout, r.stderr)
            self.assertIn('in the boot image kept for rollback-boot', r.stderr)
            self.assertEqual((self.disk / 'boot/prev.body').read_bytes(), b'image before')
            self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), {'a.ko': b'older a'})
            self.assertEqual(self.leftovers(), [])

    def test_sets_no_boot_image_runs_go(self):
        # every kernel release a bundle brought left its set (120 MB) behind: F50-C had 6.18.56's beside 6.18.57's.
        # Only the image in the slot, the kept image and the running kernel need theirs; the vendor kernel's sets and
        # directories without modules are the systems' own.
        old = {'a.ko': b'old a'}
        u, o = self.disk / 'ubuntu/lib/modules', self.disk / 'openwrt/lib/modules'
        for shell in self.each_shell():
            for banner in (None, self.KREL):
                self.old_image(banner)
                self.systems(old)
                self.kept({'a.ko': b'older a'})
                kept = bytearray(8192)
                struct.pack_into('<I', kept, 8, 4096)
                kept[4096:4096 + 40] = b'Linux version 6.18.56 (b@h) #1 SMP'.ljust(40, b'k')
                (self.disk / 'boot/prev.body').write_bytes(bytes(kept))
                for d, n in ((u / '6.18.56/extra', 'k.ko'), (o / '6.18.56', 'k.ko'), (o / '6.18.40', 's.ko'),
                             (u / '6.18.40/extra', 's.ko'), (o / '5.4.254-gtest', 'v.ko'), (o / '6.12.94', None)):
                    d.mkdir(parents=True, exist_ok=True)
                    if n:
                        (d / n).write_bytes(b'x')
                self.make_bundle({'a.ko': b'new a'})
                r = self.run_step(shell)
                self.assertIn('rc=0', r.stdout, r.stderr)
                left = sorted(p.name for p in o.iterdir()), sorted(p.name for p in u.iterdir())
                if banner is None:
                    # the image in the slot does not say its release: nothing is removed
                    self.assertNotIn('removed', r.stdout)
                    self.assertEqual(left, (['5.4.254-gtest', '6.12.94', '6.18.40', '6.18.56', self.KREL],
                                            ['6.18.40', '6.18.56', self.KREL]))
                else:
                    # 6.18.40 before the download; 6.18.56 once its image is no longer kept
                    gone = [ln.split(': ')[-1].split() for ln in r.stdout.splitlines() if 'no boot image here runs removed' in ln]
                    self.assertEqual([sorted(g) for g in gone], [['openwrt:6.18.40', 'ubuntu:6.18.40'],
                                                                 ['openwrt:6.18.56', 'ubuntu:6.18.56']])
                    self.assertEqual(left, (['5.4.254-gtest', '6.12.94', self.KREL], [self.KREL]))
                    self.assertEqual(self.ko(o / self.KREL), {'a.ko': b'new a'})
                for d in (u, o):
                    shutil.rmtree(d)

    def test_a_system_just_replaced_gets_the_set_of_the_image_in_the_slot(self):
        # Ubuntu's sets are never carried over by a system update: after "apply" replaced Ubuntu, the image in the
        # slot - kept for rollback-boot once the new one is written - had no modules in the new system, and
        # rollback-boot brought it back without them (F50-B, 2026-10-17). The previous system ran with it: its set
        # is taken over, as second names.
        old = {'a.ko': b'old a', 'b.ko': b'old b'}
        for shell in self.each_shell():
            for krel in (self.KREL, '7.2.11'):
                self.old_image(self.KREL)
                self.systems(old)
                shutil.rmtree(self.disk / 'ubuntu.old', ignore_errors=True)
                (self.disk / 'ubuntu').rename(self.disk / 'ubuntu.old')
                (self.disk / 'ubuntu/etc').mkdir(parents=True)
                (self.disk / f'ubuntu.old/lib/modules/{self.KREL}/modules.builtin').write_text('kernel/x.ko\n')
                self.make_bundle({'a.ko': b'new a'}, krel=krel)
                r = self.run_step(shell)
                self.assertIn('rc=0', r.stdout, r.stderr)
                self.assertIn(f'the modules of {self.KREL} (the image in the slot) taken over from ubuntu.old into ubuntu',
                              r.stdout)
                u = self.disk / f'ubuntu/lib/modules/{self.KREL}/extra'
                if krel == self.KREL:
                    # the kept set is the old system's, and rollback-boot puts it back
                    self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), old)
                    self.assertEqual(self.ko(u), {'a.ko': b'new a'})
                    r = self.up(shell, (self.STUBS % self.PSIZE) + 'rollback_boot; echo "rc=$?"', PART=self.part,
                                MU300_NO_DEPMOD=1)
                    self.assertIn('rc=0', r.stdout, r.stderr)
                    self.assertEqual(self.ko(u), old)
                else:
                    # another release: the slot image's set stays in the new system for a rollback-boot
                    self.assertEqual(self.ko(u), old)
                    self.assertEqual((u / 'a.ko').stat().st_ino,
                                     (self.disk / f'ubuntu.old/lib/modules/{self.KREL}/extra/a.ko').stat().st_ino)
                    self.assertTrue((u.parent / 'modules.builtin').exists())
                    self.assertEqual(self.ko(self.disk / 'ubuntu/lib/modules/7.2.11/extra'), {'a.ko': b'new a'})
                shutil.rmtree(self.disk / 'ubuntu.old')

    def test_the_kept_set_counts_only_its_own_files(self):
        # after a rollback-boot the systems' set is the kept set's files: giving it up frees nothing of those
        pm = self.disk / 'boot/prev.modules/openwrt'
        pm.mkdir(parents=True)
        (pm / 'own.ko').write_bytes(b'x' * 10000)
        (pm / 'shared.ko').write_bytes(b'y' * 50000)
        os.link(pm / 'shared.ko', self.tmp / 'other-name')
        (self.disk / 'boot/prev.body').write_bytes(b'z' * 4096 * 10)
        for shell in self.each_shell():
            r = self.up(shell, 'boot_kept_kb')
            self.assertEqual(r.stdout.strip(), str(40 + 12), r.stderr)

    def test_a_failed_write_puts_the_image_and_its_set_back(self):
        old = {'a.ko': b'old a'}
        for shell in self.each_shell():
            img = self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            self.make_bundle({'a.ko': b'new a'})
            # the new image does not read back; the one put back does
            r = self.run_step(shell, 'write_boot() { [ "$3" = "$BOOTDIR/prev.body" ]; }; ')
            self.assertIn('rc=1', r.stdout, r.stderr)
            self.assertIn('failed verification; restoring', r.stderr)
            self.assertEqual(self.part.read_bytes(), img)
            for d in self.mod_dirs():
                self.assertEqual(self.ko(d), old)
            # the image in the slot is the kept one now, with its own set
            self.assertEqual((self.disk / 'boot/prev.body').read_bytes(), img[:1 << 20])
            self.assertEqual(self.ko(self.disk / 'boot/prev.modules/openwrt'), old)
            self.assertEqual(self.leftovers(), [])

    @unittest.skipIf(os.geteuid() == 0, 'root writes into a read-only directory')
    def test_modules_that_do_not_go_in_leave_the_kept_image_and_its_set_alone(self):
        old = {'a.ko': b'old a'}
        for shell in self.each_shell():
            img = self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            self.make_bundle({'a.ko': b'new a'})
            o = self.mod_dirs()[1]
            o.chmod(0o555)
            try:
                r = self.run_step(shell)
            finally:
                o.chmod(0o755)
            self.assertIn('rc=1', r.stdout, r.stderr)
            self.assertIn('installing the modules into openwrt failed', r.stderr)
            self.assertEqual(self.part.read_bytes(), img)
            for d in self.mod_dirs():
                self.assertEqual(self.ko(d), old)
            # the image kept before this run, and the set it runs with, are as they were
            self.assertEqual((self.disk / 'boot/prev.body').read_bytes(), b'image before')
            self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), {'a.ko': b'older a'})
            self.assertEqual(self.leftovers(), [])

    def test_a_run_stopped_between_the_modules_and_the_image_puts_the_set_back(self):
        # the EXIT trap (unpack_abort): ^C or a kill after the new set went in and before the image was kept
        old = {'a.ko': b'old a'}
        for shell in self.each_shell():
            self.old_image()
            self.systems(old)
            self.kept({'a.ko': b'older a'})
            self.make_bundle({'a.ko': b'new a'})
            r = self.run_step(shell, 'trap unpack_abort EXIT; prev_modules_commit() { exit 130; }; ')
            self.assertEqual(r.returncode, 130, r.stderr)
            for d in self.mod_dirs():
                self.assertEqual(self.ko(d), old)
            self.assertEqual(self.ko(self.disk / 'boot/prev.modules/ubuntu'), {'a.ko': b'older a'})
            self.assertEqual(self.leftovers(), [])

    def test_a_bundle_of_the_callers_is_never_removed(self):
        for shell in self.each_shell():
            self.old_image()
            self.systems({'a.ko': b'old a'})
            b = self.make_bundle({'a.ko': b'new a'})
            for df in ('', 'df() { printf "F 1 2 3 4 5\\nx 800000 790000 10 99%% /\\n"; }; '):
                r = self.run_step(shell, df, MU300_KERNEL_BUNDLE=b)
                self.assertIn('rc=1' if df else 'rc=0', r.stdout, r.stderr)
                self.assertTrue(b.exists())
                self.assertEqual(self.leftovers(), [])

    def test_the_room_a_kernel_wants(self):
        # before the download: three times the bundle unpacked, and the bundle or two images, whichever is more;
        # a bundle that is here already takes its room, and only what it adds is counted
        st = self.disk / '.mu300-update'
        for shell in self.each_shell():
            shutil.rmtree(st, ignore_errors=True)
            r = self.up(shell, 'asset_kb() { echo 51445; }; kernel_need_kb v1', MU300_RELEASE='v1')
            self.assertEqual(int(r.stdout), 3 * 51445 + 65536 + 16384, r.stderr)
            r = self.up(shell, 'asset_kb() { echo 80000; }; kernel_need_kb v1')
            self.assertEqual(int(r.stdout), 3 * 80000 + 80000 + 16384, r.stderr)
            st.mkdir()
            (st / 'mu300-kernel-6.18.tar.gz').write_bytes(b'x' * (20 << 20))
            r = self.up(shell, 'asset_kb() { echo 1; }; kernel_need_kb v1')
            kd = (20 << 20) // 1024 + 1
            self.assertEqual(int(r.stdout), 3 * kd + (65536 - kd) + 16384, r.stderr)
        # what a kernel update wants is what the F50-C's 804 MiB region has after OpenWrt (345 MB): the release's
        # 6.18 bundle (51 MB) before its download
        self.assertLess((3 * 51445 + 65536 + 16384) // 1024, 300)

    def test_apply_with_only_the_kernel_to_do_asks_for_the_kernels_room(self):
        # space_needed (1 GB for a whole system) refused the kernel alone on a region that had the room for it
        src = (BIN / 'mu300-update').read_text()
        ap = src[src.index('\napply() {'):src.index('\n}\n', src.index('\napply() {'))]
        branch = ap[ap.index('if [ -z "$todo" ]; then'):ap.index('elif need=$(update_need')]
        self.assertIn('need=$(kernel_need_kb "$tag")', branch)
        un = src[src.index('update_need() {'):src.index('\n}\n', src.index('update_need() {'))]
        self.assertIn('kernel_need_kb', un)

class Lock(UpdateBase):
    """One mu300-update at a time: the second says who holds the lock and exits 1 before it touches anything.
    With flock where the machine has it (busybox's applet on OpenWrt, util-linux on Ubuntu), with a pid directory
    without; NO_FLOCK forces the directory on a machine that has flock."""
    NO_FLOCK = 'have() { [ "$1" != flock ] && command -v "$1" >/dev/null 2>&1; }'

    def setUp(self):
        super().setUp()
        # the updater makes its own directory under /run (there on both systems), never /run itself
        (self.tmp / 'locks').mkdir(mode=0o700)
        self.lock = self.tmp / 'locks' / 'mu300-update'
        self.holders = []

    def tearDown(self):
        self.end_holders()
        super().tearDown()

    def lock_env(self):
        return self.env(MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)

    def hold(self, shell, prelude=':'):
        """A separate process that takes the lock and keeps it; its pid, once it has the lock."""
        mark = self.tmp / 'held'
        mark.unlink(missing_ok=True)
        # its own process group: the lock is the shell's and its children's (sleep inherits the flock descriptor), as
        # a run's is while its unpacking or download still runs - ending the holder means the whole group. The sleep
        # is not the last command: bash (5.1 and later) execs the last command of a -c string in the shell's own
        # process, and the holder would then be a "sleep" in /proc, not a mu300-update - a run that is gone
        h = subprocess.Popen(shell + ['-c', f'. "{BIN}/mu300-update"; {prelude}; lock_take || exit 9; echo $$ > "{mark}"; sleep 30; :'],
                             env=self.lock_env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
        self.holders.append(h)
        for _ in range(100):
            if mark.exists() and mark.read_text().strip():
                return int(mark.read_text())
            if h.poll() is not None:
                self.fail(f'the holder ended: {h.stderr.read()}')
            time.sleep(0.05)
        self.fail('the holder never took the lock')

    def take(self, shell, prelude=':'):
        """lock_take in a fresh process, dropped at its end as the dispatch does: (exit status, stderr, stdout)."""
        r = self.sh(shell, f'. "{BIN}/mu300-update"; {prelude}; lock_take; trap lock_drop EXIT; echo taken', MU300_LIB=1,
                    MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)
        return r.returncode, r.stderr, r.stdout

    def end_holders(self):
        for h in self.holders:
            if h.poll() is None:
                os.killpg(h.pid, signal.SIGKILL)
            h.wait(); h.stderr.close()
        self.holders = []

    def check_second_run(self, shell, prelude):
        pid = self.hold(shell, prelude)
        rc, err, out = self.take(shell, prelude)
        self.assertEqual(rc, 1, err)
        self.assertEqual(err.strip(), f'another mu300-update is running (pid {pid})')
        self.assertEqual(out, '')
        # free again once the holder is gone - killed, so without its own cleanup
        self.end_holders()
        rc, err, out = self.take(shell, prelude)
        self.assertEqual((rc, out), (0, 'taken\n'), err)

    def test_a_run_continued_with_the_releases_updater_keeps_its_lock(self):
        """apply execs the release's mu300-update when it differs: the same process, and the lock is its own. What
        the updater before the exec left behind - its keep-alive's ping or sleep, started after lock_take with the
        lock's descriptor - holds the flock for a few seconds more, and the continued run then said "another
        mu300-update is running" with its own pid and ended: every update to a release with another updater
        (v2026.10.23 to v2026.10.24 on a device, docs/FINDINGS.md 81). A stranger's lock is still refused."""
        for shell in self.each_shell():
            with self.subTest(shell=shell[0]):
                if not shutil.which('flock'):
                    self.skipTest('no flock here')
                # the first half takes the lock, leaves a child with its descriptor, and execs the second half
                second = f'. "{BIN}/mu300-update"; lock_take; trap lock_drop EXIT; echo taken'
                r = self.sh(shell, f'. "{BIN}/mu300-update"; lock_take; sleep 3 & '
                                   f'MU300_SELF_UPDATED=1 exec {" ".join(shell)} -c \'{second}\'',
                            MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)
                self.assertEqual((r.returncode, r.stdout), (0, 'taken\n'), r.stderr)
                # a run that is not the lock's owner is refused as before, continued or not
                pid = self.hold(shell)
                r = self.sh(shell, f'. "{BIN}/mu300-update"; lock_take; echo taken', MU300_LIB=1, MU300_SELF_UPDATED=1,
                            MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)
                self.assertEqual((r.returncode, r.stdout), (1, ''), r.stderr)
                self.assertIn(f'another mu300-update is running (pid {pid})', r.stderr)
                self.end_holders()

    def test_the_keep_alive_does_not_hold_the_lock(self):
        """keepalive_start's loop runs without the lock's descriptor: a ping or sleep of it that outlives the run
        (kill ends the subshell, not its child) must not keep the next run out."""
        for shell in self.each_shell():
            with self.subTest(shell=shell[0]):
                if not shutil.which('flock'):
                    self.skipTest('no flock here')
                r = self.sh(shell, f'. "{BIN}/mu300-update"; lock_take; keepalive_start; sleep 0.3; keepalive_stop; '
                                   f'exec 9>&-; . "{BIN}/mu300-update"; lock_take; echo taken',
                            MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)
                self.assertEqual((r.returncode, r.stdout), (0, 'taken\n'), r.stderr)

    def test_second_run_is_refused_with_the_holders_pid(self):
        for shell in self.each_shell():
            self.check_second_run(shell, ':')

    def test_without_flock_a_pid_directory(self):
        for shell in self.each_shell():
            self.check_second_run(shell, self.NO_FLOCK)
            d = self.lock / 'held'
            self.assertFalse(d.exists())    # lock_drop ran at the end of take
            # a directory left by a holder that is gone (a pid nothing has) is taken over, and says so
            d.mkdir(); (d / 'pid').write_text('999999\n')
            rc, err, out = self.take(shell, self.NO_FLOCK)
            self.assertEqual((rc, out), (0, 'taken\n'), err)
            self.assertIn('taking over the lock of a mu300-update that is gone (pid 999999)', err)
            self.assertFalse(d.exists())
            # what is not a pid (never run, never echoed) is a run that is gone too
            d.mkdir(); (d / 'pid').write_text('12; rm -rf / # $(reboot)\n')
            rc, err, out = self.take(shell, self.NO_FLOCK)
            self.assertEqual((rc, out), (0, 'taken\n'), err)
            self.assertIn('taking over the lock of a mu300-update that is gone\n', err)
            self.assertNotIn('rm -rf', err)
            # one with no pid at all (its holder went between making it and writing its pid) is given a second
            # to write it, then taken over too: never a denial for good
            d.mkdir()
            t = time.monotonic()
            rc, err, out = self.take(shell, self.NO_FLOCK)
            self.assertEqual((rc, err.strip(), out), (0, 'taking over the lock of a mu300-update that is gone', 'taken\n'))
            self.assertGreater(time.monotonic() - t, 0.9)
            self.assertFalse(d.exists())
            # a live process that is not a mu300-update (this test run) is a reused pid, not a holder - where
            # /proc can tell; without it (macOS) only "alive" is known, and it counts as a holder
            d.mkdir(); (d / 'pid').write_text(f'{os.getpid()}\n')
            rc, err, out = self.take(shell, self.NO_FLOCK)
            if os.path.exists('/proc/self/cmdline'):
                self.assertEqual((rc, out), (0, 'taken\n'), err)
                self.assertFalse(d.exists())
            else:
                self.assertEqual((rc, err.strip(), out), (1, f'another mu300-update is running (pid {os.getpid()})', ''))
                shutil.rmtree(d)

    def test_the_pid_file_names_the_holder_with_flock(self):
        if not shutil.which('flock'):
            self.skipTest('no flock here')
        for shell in self.each_shell():
            pid = self.hold(shell)
            self.assertEqual((self.lock / 'lock').read_text().strip(), str(pid))
            self.end_holders()
            # the file is only informative once the holder is gone: the kernel released the lock
            rc, err, out = self.take(shell)
            self.assertEqual((rc, out), (0, 'taken\n'), err)
            # a lock file that is not a regular file is refused, the run does not go on unlocked
            (self.lock / 'lock').unlink(); (self.lock / 'lock').symlink_to(self.tmp / 'elsewhere')
            rc, err, out = self.take(shell)
            self.assertEqual((rc, out), (1, ''))
            self.assertIn('is not a regular file; not running', err)
            (self.lock / 'lock').unlink()

    def test_refuses_a_lock_path_that_is_not_its_own_directory(self):
        # nothing runs unlocked: a link at the path (to anywhere), a directory anyone else can write, or one that
        # cannot be made stop the run with exit 1 before it touches anything
        elsewhere = self.tmp / 'elsewhere'
        elsewhere.mkdir(mode=0o700)
        for shell in self.each_shell():
            for prelude in (':', self.NO_FLOCK):
                shutil.rmtree(self.lock, ignore_errors=True)
                self.lock.symlink_to(elsewhere)
                rc, err, out = self.take(shell, prelude)
                self.assertEqual((rc, out), (1, ''), err)
                self.assertIn(f'cannot lock: {self.lock} is a symbolic link; not running', err)
                self.assertEqual(list(elsewhere.iterdir()), [])
                self.lock.unlink()
                self.lock.mkdir(mode=0o777)
                rc, err, out = self.take(shell, prelude)
                self.assertEqual((rc, out), (1, ''), err)
                self.assertIn('is not a directory of mode 700; not running', err)
                self.assertEqual(list(self.lock.iterdir()), [])
                self.lock.rmdir()
                self.lock.write_text('')
                rc, err, out = self.take(shell, prelude)
                self.assertEqual((rc, out), (1, ''), err)
                self.assertIn('is not a directory of mode 700; not running', err)
                self.lock.unlink()
                if os.geteuid() != 0:
                    self.lock.parent.chmod(0o500)
                    rc, err, out = self.take(shell, prelude)
                    self.lock.parent.chmod(0o700)
                    self.assertEqual((rc, out), (1, ''), err)
                    self.assertIn(f'cannot lock: cannot make {self.lock}; not running', err)
            # made by the run itself: mode 700
            rc, err, out = self.take(shell)
            self.assertEqual((rc, out), (0, 'taken\n'), err)
            self.assertEqual(self.lock.stat().st_mode & 0o777, 0o700)

    def test_what_changes_the_disk_locks_and_what_only_looks_does_not(self):
        src = (BIN / 'mu300-update').read_text()
        main = src[src.index('[ "$(id -u)" = 0 ]'):]
        m = re.search(r'^\s*([a-z|-]+)\)\n\s*lock_take;', main, re.M)
        self.assertIsNotNone(m, 'no command locks')
        self.assertEqual(set(m.group(1).split('|')), {'apply', 'boot', 'kernel', 'rollback', 'rollback-boot', 'clean', 'accounts'})
        self.assertLess(main.index('lock_take'), main.index('keepalive_start'))
        self.assertIn("lock_drop' EXIT", main)
        # under /run (root's own, so no login user can plant a link or a lock there), not in $STAGE (clean deletes
        # it) nor /tmp or /var/lock (anyone's)
        self.assertIn('LOCK=${MU300_LOCK:-/run/mu300-update}', src)
        self.assertNotIn('/var/lock/mu300-update', src)
        self.assertNotIn('going on without', src)


if __name__ == '__main__':
    unittest.main()
