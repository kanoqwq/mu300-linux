"""adb over USB to Linux: the adbd build (tools/adbd), the gadget side (mu300-adbd, against a fake configfs gadget
whose UDC is a plain file) and its switch, its two ways of letting computers in and its keys (mu300-usb adb), the
kernel option and the images. On by default and open to every computer (FINDINGS 70); what a device was set to
before that stays."""
import os
import re
import signal
import subprocess
import time
import unittest

from helpers import BIN, TOP, ShellTest

ADBD_DIR = TOP / 'tools/adbd'
PATCH = (ADBD_DIR / 'mu300-adbd.patch').read_text()
KEY = 'QAAAA' + 'A' * 694 + '='          # the shape of an Android RSA-2048 public key (adbkey.pub)
KEY2 = 'QAAAA' + 'B' * 694 + '='


def added(patch, path):
    """The lines PATCH adds to PATH (a/PATH b/PATH), joined."""
    out, cur = [], None
    for line in patch.splitlines():
        if line.startswith('+++ '):
            cur = line[6:]
        elif cur == path and line.startswith('+') and not line.startswith('+++'):
            out.append(line[1:])
    return '\n'.join(out)


class AdbdBuild(unittest.TestCase):
    """What tools/adbd builds: the security of the daemon is in its patch, so the patch is checked line by line."""

    def test_source_is_pinned(self):
        b = (ADBD_DIR / 'build.sh').read_text()
        self.assertIn('https://github.com/nmeum/android-tools/releases/download/$VER/android-tools-$VER.tar.xz', b)
        self.assertRegex(b, r'\nSHA=[0-9a-f]{64}\n')
        self.assertIn("sha256sum -c -", b)
        self.assertIn('patch -p1 < /adbd/mu300-adbd.patch', b)

    def test_authentication_has_one_switch(self):
        main = added(PATCH, 'adb/daemon/main.cpp')
        # open only for exactly MU300_ADB_AUTH=open, which mu300-adbd sets; a missing variable or any other value
        # keeps the keys
        self.assertIn('const char* mu300_adb_auth = getenv("MU300_ADB_AUTH");', main)
        self.assertIn('auth_required = !(mu300_adb_auth != nullptr && std::string(mu300_adb_auth) == "open");', main)
        # and nothing else in the patch decides it (Debian's adbd sets it false on Linux for good)
        self.assertEqual(1, len(re.findall(r'\n\+.*\bauth_required\s*=[^=]', PATCH)))
        self.assertEqual(1, PATCH.count('getenv('))
        self.assertNotRegex(PATCH, r'\n\+.*auth_required = false')

    def test_the_daemon_is_started_with_the_mode(self):
        s = (BIN / 'mu300-adbd').read_text()
        self.assertIn('MU300_ADB_AUTH=$auth "$ADBD" </dev/null &', s)
        self.assertEqual(1, s.count('"$ADBD" </dev/null &'))

    def test_keys_only_from_root_owned_files(self):
        auth = added(PATCH, 'native/libs/adbd_auth/adbd_auth.cpp')
        self.assertIn('{"/etc/mu300/adb_keys", "/root/.android/adb_keys"}', auth)
        # no Android framework socket that could approve a key (it would come from the environment): with keys
        # required and no key in those files, nobody gets in
        self.assertIn('sock_fd_.reset(-1);', auth)

    def test_usb_only_never_tcp(self):
        main = added(PATCH, 'adb/daemon/main.cpp')
        self.assertIn('#if defined(__linux__)', main)        # FunctionFS on Linux
        self.assertIn('return 1;', main)                    # no FunctionFS: exit, no TCP fallback
        self.assertIn('(void)setup_adb;', main)
        # the TCP/vsock listeners stay inside #else (Android only): the patch opens that #else ...
        self.assertRegex(PATCH, r'\(void\)setup_adb;\n\+#else\n')
        # ... whichever way adbd lets computers in: the switch for that is read before it and opens nothing else
        self.assertLess(main.index('MU300_ADB_AUTH'), main.index('(void)setup_adb;'))
        # "adb tcpip" (Android: service.adb.tcp.port, and adbd comes back listening on it) is not built at all
        self.assertNotIn('restart_service', (ADBD_DIR / 'CMakeLists.txt').read_text())

    def test_files_and_shell_are_not_world_writable(self):
        self.assertIn('umask(022);', added(PATCH, 'adb/daemon/main.cpp'))
        self.assertIn('mode &= ~022;', added(PATCH, 'adb/daemon/file_sync_service.cpp'))
        sh = added(PATCH, 'adb/daemon/shell_service.cpp')
        self.assertIn('env["TMPDIR"] = "/tmp";', sh)
        self.assertIn('login_shell', sh)

    def test_left_out_services(self):
        stubs = (ADBD_DIR / 'stubs.cpp').read_text()
        for f in ('init_jdwp', 'create_jdwp_service_socket', 'adbd_wifi_init', 'setup_mdns', 'b64_pton'):
            self.assertIn(f, stubs)
        cm = (ADBD_DIR / 'CMakeLists.txt').read_text()
        self.assertIn('-static', cm)
        for f in ('daemon/adb_wifi.cpp', 'daemon/jdwp_service.cpp', 'daemon/mdns.cpp', 'protobuf'):
            self.assertNotIn(f, cm)

    def test_binary_is_not_tracked(self):
        self.assertIn('\ntools/adbd/adbd\n', (TOP / '.gitignore').read_text())


class AdbImages(unittest.TestCase):
    def test_kernel_has_functionfs(self):
        self.assertIn('\nCONFIG_USB_CONFIGFS_F_FS=y\n', (TOP / 'upstream/mu300-mainline.config').read_text())

    def test_ubuntu_image(self):
        a = (TOP / 'rootfs/assemble.sh').read_text()
        self.assertIn('install -m755 /adbd $R/opt/mu300/bin/adbd', a)
        self.assertIn('mu300-adbd.service:multi-user.target', a)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-adbd.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-adbd run', unit)
        self.assertIn('ExecStopPost=/opt/mu300/bin/mu300-adbd stop', unit)
        self.assertIn('KillMode=mixed', unit)
        self.assertIn('Before=mu300-lan.service', unit)

    def test_openwrt_image(self):
        b = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('$(opt tools/adbd/adbd adbd)', b)
        self.assertIn('cp /in/adbd $R/opt/mu300/bin/adbd', b)
        self.assertRegex(b, r'for s in mu300-accounts [^\n]* mu300-adbd; do')
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-adbd').read_text()
        # on unless turned off, as mu300-adbd run decides it
        self.assertIn('[ "$(cat /etc/mu300/adb 2>/dev/null)" != off ] || return 0', init)
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-adbd run', init)
        self.assertNotIn('respawn', init)   # the loop restarts adbd; a respawned loop would rebind the gadget again

    def test_release_builds_it(self):
        r = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('tools/adbd/build.sh', r)
        self.assertIn('/adbd:ro', r)


class GadgetOsDescTable(unittest.TestCase):
    """The oops of 2026-10-22 on an adbd restart (FINDINGS 213): f_ncm and f_rndis freed os_desc_table at unbind and
    kept the pointer, so a bind without OS descriptors left it for the next unbind to free again. Patch 0027 forgets
    it in both; mu300-adbd keeps the descriptors on, for kernels without the patch."""

    KPATCH = TOP / 'upstream/patches/0027-usb-gadget-ncm-rndis-os-desc-table-double-free.patch'

    def test_both_unbinds_forget_the_table_they_freed(self):
        p = self.KPATCH.read_text()
        for f in ('f_ncm.c', 'f_rndis.c'):
            hunk = p.split(f'+++ b/drivers/usb/gadget/function/{f}\n', 1)[1].split('\n--- ', 1)[0]
            self.assertIn('\n \tkfree(f->os_desc_table);\n+\tf->os_desc_table = NULL;\n \tf->os_desc_n = 0;\n', hunk)

    def test_one_patch_with_its_number(self):
        names = [q.name for q in (TOP / 'upstream/patches').rglob('0027-*.patch')]
        self.assertEqual([self.KPATCH.name], names)

    def test_adbd_never_turns_the_descriptors_off(self):
        s = (BIN / 'mu300-adbd').read_text()
        self.assertNotIn('echo 0 > "$G/os_desc/use"', s)
        self.assertNotIn('rm -f "$G/os_desc/c.1"', s)


class Gadget(ShellTest):
    """mu300-adbd run/stop on a fake gadget: NCM (or RNDIS) + ACM in configs/c.1, bound to 25100000.dwc3."""

    UDC = '25100000.dwc3'

    def setUp(self):
        super().setUp()
        g = self.g = self.tmp / 'gadget'
        for d in ('functions/ncm.usb0', 'functions/acm.GS0', 'functions/rndis.rn0', 'configs/c.1', 'os_desc'):
            (g / d).mkdir(parents=True)
        (g / 'configs/c.1/f1').symlink_to(g / 'functions/ncm.usb0')
        (g / 'configs/c.1/f2').symlink_to(g / 'functions/acm.GS0')
        (g / 'UDC').write_text(self.UDC + '\n')
        (g / 'bcdDevice').write_text('0x0301\n')
        (g / 'os_desc/use').write_text('0\n')
        self.ffs = self.tmp / 'ffs'
        self.mounts = self.tmp / 'mounts'
        self.mounts.write_text('')
        self.conf = self.tmp / 'adb'           # not there: a device that was never asked, so adb is on
        self.auth = self.tmp / 'adb_auth'
        self.keys = self.tmp / 'adb_keys'
        self.role = self.tmp / 'role'
        self.role.write_text('device\n')
        udc = self.tmp / 'udc'
        (udc / self.UDC).mkdir(parents=True)
        self.builtin = self.tmp / 'modules.builtin'
        self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')
        # adbd: writes its descriptors (ep1 appears), then waits to be stopped
        self.adbd = self.tmp / 'adbd'
        self.adbd.write_text('#!/bin/sh\necho "start-$MU300_ADB_AUTH" >> "$STUBLOG/adbd.log"\n: > "$MU300_FFS/ep1"\n'
                             'trap \'rm -f "$MU300_FFS/ep1"; echo stop >> "$STUBLOG/adbd.log"; exit 0\' TERM\n'
                             'while :; do sleep 0.05; done\n')
        self.adbd.chmod(0o755)
        self.stub('mount', 'echo "mount $*" >> "$STUBLOG/calls"; '
                           '[ "$2" = functionfs ] && echo "adb $4 functionfs rw 0 0" >> "$MU300_PROC_MOUNTS"; :')
        self.stub('umount', 'echo "umount $*" >> "$STUBLOG/calls"; : > "$MU300_PROC_MOUNTS"')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/calls"')
        self.stub('id', 'echo 0')
        self.envs = dict(MU300_GADGET=self.g, MU300_FFS=self.ffs, MU300_ADBD_BIN=self.adbd, MU300_ADB_CONF=self.conf,
                         MU300_ADB_AUTH_CONF=self.auth, MU300_ADB_KEYS=self.keys,
                         MU300_ADB_ROOT_KEYS=self.tmp / 'root_adb_keys', MU300_ADB_OWNER=os.getuid(),
                         MU300_PROC_MOUNTS=self.mounts, MU300_PROC_FILESYSTEMS=self.tmp / 'nofs',
                         MU300_MODULES_BUILTIN=self.builtin, MU300_UDC_CLASS=udc, MU300_USB_ROLE=self.role,
                         MU300_USB_HOOK=self.tmp / 'nohook', MU300_ADBD_TICK='0.1', MU300_ADBD_GRACE='3',
                         # no waiting for a FunctionFS that comes late, except in the test of that
                         MU300_ADB_WAIT='0')

    def start(self, shell, **env):
        e = dict(self.envs)
        e.update(env)
        return subprocess.Popen(shell + [str(BIN / 'mu300-adbd'), 'run'], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env(**e))

    def until(self, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def stop(self, p):
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=20)
        return p.returncode, out + err

    def read(self, name):
        return (self.g / name).read_text().strip()

    def linked(self):
        return os.path.islink(self.g / 'configs/c.1/f3')

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def test_off_leaves_the_gadget_alone(self):
        self.conf.write_text('off\n')
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'run', **self.envs)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('adb is off', r.stdout)
            self.assertFalse((self.g / 'functions/ffs.adb').exists())
            self.assertEqual('', self.calls())

    def test_no_adbd_or_no_functionfs_leaves_the_gadget_alone(self):
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'run', **dict(self.envs, MU300_ADBD_BIN=self.tmp / 'none'))
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('no adbd in this system', r.stderr)
            # a kernel without FunctionFS: not in the filesystems, not built in, and the function cannot be made
            # (here: something in its way, which stops root too)
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_ncm.ko\n')
            (self.g / 'functions/ffs.adb').write_text('')
            try:
                r = self.script(shell, BIN / 'mu300-adbd', 'run', **self.envs)
            finally:
                (self.g / 'functions/ffs.adb').unlink()
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('no FunctionFS', r.stderr)
            self.assertEqual(self.UDC, self.read('UDC'))
            self.assertFalse(self.linked())
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')

    def test_a_functionfs_that_comes_late_in_the_boot_is_waited_for(self):
        """OpenWrt starts the service 13 s after power-on (S19), when the FunctionFS module cannot be had yet: making
        the function failed, the service said "no FunctionFS" and ended for good - with adb on by default, no
        OpenWrt had adb after a boot (v2026.10.24 pre-release on an F50, docs/FINDINGS.md 82). It asks again for a
        minute now."""
        for shell in self.each_shell():
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_ncm.ko\n')
            block = self.g / 'functions/ffs.adb'
            block.write_text('')
            p = self.start(shell, MU300_ADB_WAIT='20')
            try:
                time.sleep(1.5)
                self.assertIsNone(p.poll(), 'the service gave up at the first no')
                block.unlink()
                self.assertTrue(self.until(self.linked, 15), 'adb never joined the gadget')
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertNotIn('no FunctionFS', out)
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')

    def test_joins_the_gadget_and_leaves_it_whole(self):
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                self.assertTrue(self.until(self.linked), 'adb never joined the gadget')
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC))
                self.assertEqual(str(self.g / 'functions/ffs.adb'), os.readlink(self.g / 'configs/c.1/f3'))
                # f1/f2 untouched: the network and the console keep their interface numbers
                self.assertEqual(str(self.g / 'functions/ncm.usb0'), os.readlink(self.g / 'configs/c.1/f1'))
                self.assertEqual('0x0303', self.read('bcdDevice'))
                self.assertEqual('1', self.read('os_desc/use'))
                self.assertEqual('0xcd', self.read('os_desc/b_vendor_code'))
                self.assertTrue(os.path.islink(self.g / 'os_desc/c.1'))
                self.assertIn(f'mount -t functionfs adb {self.ffs}', self.calls())
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertFalse(self.linked())
            self.assertEqual(self.UDC, self.read('UDC'))
            self.assertEqual('0x0301', self.read('bcdDevice'))
            # left on: a bind without them would leave f_ncm's freed os_desc_table for the next unbind to free again
            self.assertEqual('1', self.read('os_desc/use'))
            self.assertTrue(os.path.islink(self.g / 'os_desc/c.1'))
            self.assertFalse((self.g / 'functions/ffs.adb').exists())
            self.assertIn(f'umount {self.ffs}', self.calls())
            # nothing was ever set on this device: on, and open to every computer
            self.assertEqual(['start-open', 'stop'], (self.tmp / 'adbd.log').read_text().split())
            self.assertFalse(self.conf.exists())
            self.assertIn('auth open: every computer on the USB cable gets a root shell', out)
            (self.tmp / 'adbd.log').unlink()
            (self.tmp / 'calls').unlink()

    def test_lan_recovery_hook_does_not_queue_a_second_rebind(self):
        hook = self.tmp / 'hook'
        hook.write_text('#!/bin/sh\n'
                        'echo "hook $ACTION $INTERFACE" >> "$STUBLOG/hooks"\n'
                        '[ "$MU300_USB_SKIP_REBIND" = 1 ] || echo rebind >> "$STUBLOG/queued"\n'
                        'exit 0\n')
        hook.chmod(0o755)
        for shell in self.each_shell():
            p = self.start(shell, MU300_USB_HOOK=hook)
            try:
                self.assertTrue(self.until(lambda: (self.tmp / 'hooks').exists()))
                self.assertFalse((self.tmp / 'queued').exists(), 'ADB recovery requested another USB rebind')
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertEqual(['hook ifup lan', 'hook ifup lan'], (self.tmp / 'hooks').read_text().splitlines())
            self.assertFalse((self.tmp / 'queued').exists(), 'ADB teardown requested another USB rebind')
            (self.tmp / 'hooks').unlink()

    def test_rndis_keeps_its_ms_os_descriptors(self):
        (self.g / 'configs/c.1/f1').unlink()
        (self.g / 'configs/c.1/f1').symlink_to(self.g / 'functions/rndis.rn0')
        (self.g / 'bcdDevice').write_text('0x0302\n')
        (self.g / 'os_desc/use').write_text('1\n')
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                self.assertTrue(self.until(self.linked))
                self.assertEqual('0x0304', self.read('bcdDevice'))
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertEqual('0x0302', self.read('bcdDevice'))
            self.assertEqual('1', self.read('os_desc/use'))   # RNDIS needs them: boot/init's, left as they were

    def test_binds_again_when_functionfs_unbound_the_gadget(self):
        for shell in self.each_shell():
            p = self.start(shell, MU300_ADBD_GRACE='20')   # 20 ticks of 0.1 s: a 2 s grace, a margin under load
            try:
                # in the gadget and bound (linked comes first, inside the rebind: its bind must be over)
                self.assertTrue(self.until(self.linked))
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC))
                time.sleep(0.3)
                # an adb connection ended: FunctionFS takes the gadget off the controller
                (self.g / 'UDC').write_text('\n')
                time.sleep(0.5)
                self.assertEqual('', self.read('UDC'))   # not at once (the grace for mu300-usb-reset)
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC, timeout=15), 'never bound again')
                self.assertTrue(self.linked())
            finally:
                self.stop(p)

    def test_unready_adbd_never_costs_the_network(self):
        # adbd that never writes its descriptors: the gadget stays as it is, bound, without adb
        self.adbd.write_text('#!/bin/sh\ntrap "exit 0" TERM\nwhile :; do sleep 0.05; done\n')
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                time.sleep(1)
                self.assertFalse(self.linked())
                self.assertEqual(self.UDC, self.read('UDC'))
                (self.g / 'UDC').write_text('\n')    # and when the gadget is unbound, it is bound without adb
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC, timeout=15))
                self.assertFalse(self.linked())
            finally:
                self.stop(p)

    def test_host_role_is_left_alone(self):
        self.role.write_text('host\n')
        for shell in self.each_shell():
            (self.g / 'UDC').write_text('\n')
            p = self.start(shell)
            try:
                time.sleep(1.5)
                self.assertEqual('', self.read('UDC'))
                self.assertFalse(self.linked())
            finally:
                self.stop(p)

    def test_status(self):
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'status', **self.envs)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('adb: on', r.stdout)
            self.assertIn('auth: open (every computer on the USB cable gets a root shell, no key asked)', r.stdout)
            self.assertIn('gadget: without adb', r.stdout)
            self.conf.write_text('off\n')
            self.auth.write_text('keys\n')
            r = self.script(shell, BIN / 'mu300-adbd', 'status', **self.envs)
            self.assertIn('adb: off', r.stdout)
            self.assertIn('auth: keys', r.stdout)
            self.conf.unlink()
            self.auth.unlink()

    def mode(self, shell, what='auth'):
        r = self.script(shell, BIN / 'mu300-adbd', what, **self.envs)
        self.assertEqual(0, r.returncode, r.stderr)
        return r.stdout.strip()

    def test_who_gets_in(self):
        # (/etc/mu300/adb, /etc/mu300/adb_auth, a key authorised) -> auth, state
        cases = [
            (None, None, False, 'open', 'open'),      # never set: a new installation, or an update of one
            ('off', None, False, 'open', 'off'),      # turned off by somebody: stays off
            ('on', None, False, 'keys', 'keys'),      # turned on under v2026.10.20-23, when on meant keys only
            ('off', None, True, 'keys', 'off'),       # a key authorised then: keys when it is turned on again
            (None, None, True, 'keys', 'keys'),
            ('on', 'open', True, 'open', 'open'),     # what "mu300-usb adb auth" wrote decides
            (None, 'keys', False, 'keys', 'keys'),
            ('on', 'keys', True, 'keys', 'keys'),
            (None, 'yes', False, 'keys', 'keys'),     # a file nobody here wrote: the closed one
            (None, 'OPEN', False, 'keys', 'keys'),
        ]
        for conf, auth, key, want_auth, want_state in cases:
            for f, v in ((self.conf, conf), (self.auth, auth), (self.keys, f'{KEY} me@pc' if key else None)):
                f.unlink(missing_ok=True)
                if v is not None:
                    f.write_text(v + '\n')
            for shell in self.each_shell():
                self.assertEqual(want_auth, self.mode(shell), (conf, auth, key))
                self.assertEqual(want_state, self.mode(shell, 'state'), (conf, auth, key))
        # a key in root's own file counts as well
        for f in (self.conf, self.auth, self.keys):
            f.unlink(missing_ok=True)
        (self.tmp / 'root_adb_keys').write_text(f'{KEY} me@pc\n')
        for shell in self.each_shell():
            self.assertEqual('keys', self.mode(shell))

    def test_open_only_from_a_state_that_is_whole(self):
        """No state at all is open (the decision). A state that is there and damaged never is."""
        def reset():
            for f in (self.conf, self.auth, self.keys):
                if f.is_symlink() or f.exists():
                    f.chmod(0o644) if not f.is_symlink() else None
                    f.unlink()
            self.tmp.chmod(0o700)

        def check(want_auth, want_state, why=None):
            for shell in self.each_shell():
                self.assertEqual(want_auth, self.mode(shell))
                self.assertEqual(want_state, self.mode(shell, 'state'))
                if why:
                    r = self.script(shell, BIN / 'mu300-adbd', 'status', **self.envs)
                    self.assertIn(why, r.stdout)

        reset()
        check('open', 'open')
        # adb_auth cut to nothing (a power cut), or holding half a word
        self.auth.write_bytes(b'')
        check('keys', 'keys', 'empty, unreadable or says neither open nor keys')
        for junk in (b'op', b'open keys\n', b'\n\n'):
            self.auth.write_bytes(junk)
            check('keys', 'keys')
        # unreadable (root reads everything: nothing to see there)
        if os.getuid() != 0:
            self.auth.write_text('open\n')
            self.auth.chmod(0)
            check('keys', 'keys')
            self.auth.chmod(0o644)
        # "open" in a file somebody else could have written, or behind a symlink
        self.auth.write_text('open\n')
        check('open', 'open')
        for mode in (0o664, 0o646, 0o666):
            self.auth.chmod(mode)
            check('keys', 'keys', "not a file of root's alone")
        reset()
        (self.tmp / 'elsewhere').write_text('open\n')
        self.auth.symlink_to(self.tmp / 'elsewhere')
        check('keys', 'keys')
        # the directory itself writable by others, or owned by somebody else: not even the default
        reset()
        self.tmp.chmod(0o777)
        check('keys', 'keys', "missing or not root's alone")
        self.tmp.chmod(0o700)
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'auth', **dict(self.envs, MU300_ADB_OWNER=os.getuid() + 1))
            self.assertEqual('keys', r.stdout.strip())
            r = self.script(shell, BIN / 'mu300-adbd', 'auth',
                            **dict(self.envs, MU300_ADB_AUTH_CONF=self.tmp / 'not-mounted' / 'adb_auth'))
            self.assertEqual('keys', r.stdout.strip())
        # on or off cut to nothing: off, never the default (on)
        for junk in (b'', b'o', b'of\n', b'yes\n'):
            reset()
            self.conf.write_bytes(junk)
            check('keys', 'off', 'says neither on nor off')
            for shell in self.each_shell():
                r = self.script(shell, BIN / 'mu300-adbd', 'run', **self.envs)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn('says neither on nor off: adb stays off', r.stderr)
                self.assertFalse((self.g / 'functions/ffs.adb').exists())
                self.assertFalse((self.tmp / 'adbd.log').exists())
        reset()

    def test_keys_mode_without_a_key_lets_nobody_in(self):
        # adbd is started with keys, never with open, and the log says that nobody gets in; that adbd then refuses
        # every computer is its own code (no key to match, no socket to approve one: AdbdBuild) and was measured
        # on a device (FINDINGS 43: "unauthorized" with the key file taken away)
        self.auth.write_text('keys\n')
        for keys in (None, '', '\n', 'ssh-rsa AAAA not-an-adb-key\n'):
            self.keys.unlink(missing_ok=True)
            if keys is not None:
                self.keys.write_text(keys)
            for shell in self.each_shell():
                (self.tmp / 'adbd.log').unlink(missing_ok=True)
                p = self.start(shell)
                try:
                    self.assertTrue(self.until(self.linked), 'adb never joined the gadget')
                finally:
                    rc, out = self.stop(p)
                self.assertEqual(['start-keys', 'stop'], (self.tmp / 'adbd.log').read_text().split())
                self.assertIn('no key is authorised: nobody gets in', out)
        self.auth.unlink()

    def test_state_where_adb_cannot_run(self):
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'state', **dict(self.envs, MU300_ADBD_BIN=self.tmp / 'none'))
            self.assertEqual('unavailable', r.stdout.strip())

    def test_adbd_is_told_the_mode(self):
        for conf, auth, want in (('on', None, 'keys'), (None, 'keys', 'keys'), ('on', 'open', 'open')):
            for f, v in ((self.conf, conf), (self.auth, auth)):
                f.unlink(missing_ok=True)
                if v is not None:
                    f.write_text(v + '\n')
            for shell in self.each_shell():
                (self.tmp / 'adbd.log').unlink(missing_ok=True)
                p = self.start(shell)
                try:
                    self.assertTrue(self.until(self.linked), 'adb never joined the gadget')
                finally:
                    rc, out = self.stop(p)
                self.assertEqual(0, rc, out)
                self.assertEqual([f'start-{want}', 'stop'], (self.tmp / 'adbd.log').read_text().split())
                if want == 'keys':
                    self.assertIn('auth keys: only the authorised keys get in', out)


class UsbAdb(ShellTest):
    """mu300-usb adb: the setting, the service (a stub), the keys."""

    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc/mu300/adb'
        self.auth = self.tmp / 'etc/mu300/adb_auth'
        self.keys = self.tmp / 'etc/mu300/adb_keys'
        self.conf.parent.mkdir(parents=True)       # /etc/mu300: every image has it (its version is in there)
        self.stub('id', 'echo 0')
        # the real mu300-adbd says who gets in (auth, status); only whether adbd can run here is the test's
        self.stub('mu300-adbd', 'case $1 in usable) [ -e "$STUBLOG/unusable" ] && { echo "no adbd"; exit 1; }; exit 0 ;; '
                                f'*) exec sh "{BIN / "mu300-adbd"}" "$@" ;; esac')
        self.svc = self.tmp / 'svc'
        self.svc.write_text('#!/bin/sh\necho "$1" >> "$STUBLOG/svc.log"\n')
        self.svc.chmod(0o755)

    def usb(self, shell, *args, stdin=None):
        return self.script(shell, BIN / 'mu300-usb', 'adb', *args, stdin=stdin, MU300_ADB_CONF=self.conf,
                           MU300_ADB_AUTH_CONF=self.auth, MU300_ADB_KEYS=self.keys,
                           MU300_ADB_ROOT_KEYS=self.tmp / 'root_adb_keys', MU300_ADB_SERVICE=self.svc,
                           MU300_GADGET=self.tmp / 'nogadget', MU300_ADB_OWNER=os.getuid())

    def clear(self):
        for f in (self.conf, self.auth, self.keys, self.tmp / 'svc.log'):
            f.unlink(missing_ok=True)

    def svc_log(self):
        p = self.tmp / 'svc.log'
        return p.read_text().split() if p.exists() else []

    def test_on_and_off(self):
        for shell in self.each_shell():
            self.clear()
            r = self.usb(shell, 'on')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('on\n', self.conf.read_text())
            # open, and said so: what it means and the two commands that close it
            self.assertEqual('open\n', self.auth.read_text())
            self.assertIn('every computer plugged into the USB port gets a root shell without a password', r.stdout)
            self.assertIn('sudo mu300-usb adb auth keys', r.stdout)
            self.assertIn('sudo mu300-usb adb off', r.stdout)
            r = self.usb(shell, 'off')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('off\n', self.conf.read_text())
            self.assertEqual('open\n', self.auth.read_text())
            self.assertEqual(['restart', 'stop'], self.svc_log())

    def test_a_device_set_up_for_keys_stays_so(self):
        # v2026.10.20-23: "adb on" meant keys only, and there was no adb_auth
        for shell in self.each_shell():
            self.clear()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('on\n')
            self.assertEqual('keys', self.usb(shell, 'auth').stdout.strip())
            # turned off and on again: still keys, and now written down
            self.assertEqual(0, self.usb(shell, 'off').returncode)
            self.assertEqual('keys\n', self.auth.read_text())
            r = self.usb(shell, 'on')
            self.assertEqual('keys\n', self.auth.read_text())
            self.assertIn('adb takes authorised keys only', r.stdout)
            self.assertIn('no key is authorised yet', r.stdout)
            self.assertNotIn('without a password', r.stdout)
            # off with a key authorised, as such a device may have been left: keys when it comes on
            self.clear()
            self.conf.write_text('off\n')
            self.keys.write_text(f'{KEY} me@pc\n')
            self.assertEqual(0, self.usb(shell, 'on').returncode)
            self.assertEqual('keys\n', self.auth.read_text())

    def test_auth(self):
        for shell in self.each_shell():
            self.clear()
            self.assertEqual('open', self.usb(shell, 'auth').stdout.strip())       # the default, shown to anyone
            self.assertFalse(self.auth.exists())
            r = self.usb(shell, 'auth', 'keys')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('keys\n', self.auth.read_text())
            self.assertIn('no key is authorised yet', r.stdout)
            self.assertEqual(['restart'], self.svc_log())                          # adbd reads it when it starts
            self.assertFalse(self.conf.exists())                                   # on or off is not its business
            r = self.usb(shell, 'auth', 'open')
            self.assertEqual('open\n', self.auth.read_text())
            self.assertIn('root shell without a password', r.stdout)
            self.assertEqual(['restart', 'restart'], self.svc_log())
            # adb off: written for later, nothing to restart
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('off\n')
            r = self.usb(shell, 'auth', 'keys')
            self.assertEqual('keys\n', self.auth.read_text())
            self.assertIn('adb is off', r.stdout)
            self.assertEqual(['restart', 'restart'], self.svc_log())
            self.assertEqual('off\n', self.conf.read_text())
            for bad in ('none', 'OPEN', 'off'):
                r = self.usb(shell, 'auth', bad)
                self.assertEqual(1, r.returncode, bad)
                self.assertIn('usage: mu300-usb adb auth open|keys', r.stderr)
                self.assertEqual('keys\n', self.auth.read_text())

    def test_the_state_is_written_whole_and_never_through_a_symlink(self):
        s = (BIN / 'mu300-usb').read_text()
        # every write of the two state files is adb_write (a new file, renamed over the old one)
        self.assertNotRegex(s, r'>\s*"\$ADB_(CONF|AUTH)"')
        self.assertIn('mv -f "$_t" "$1"', s)
        for shell in self.each_shell():
            self.clear()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            other = self.tmp / 'somewhere-else'
            other.write_text('keys\n')
            self.auth.symlink_to(other)
            self.assertEqual(0, self.usb(shell, 'auth', 'open').returncode)
            self.assertFalse(self.auth.is_symlink())
            self.assertEqual('open\n', self.auth.read_text())
            self.assertEqual('keys\n', other.read_text())                 # not written through the link
            self.assertEqual(0o644, self.auth.stat().st_mode & 0o777)
            self.assertEqual(0, self.usb(shell, 'off').returncode)
            self.assertEqual(0o644, self.conf.stat().st_mode & 0o777)
            self.assertEqual(sorted(['adb', 'adb_auth']), sorted(p.name for p in self.conf.parent.iterdir()))
            # an adb_auth cut to nothing reads as keys, and "adb on" does not write the default over it
            self.auth.write_bytes(b'')
            r = self.usb(shell, 'on')
            self.assertEqual(b'', self.auth.read_bytes())
            self.assertIn('adb takes authorised keys only', r.stdout)

    def test_a_key_does_not_close_an_open_device(self):
        pub = self.tmp / 'adbkey.pub'
        pub.write_text(f'{KEY} me@my-mac.local')
        for shell in self.each_shell():
            self.clear()
            r = self.usb(shell, 'key', pub)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('open\n', self.auth.read_text())
            self.assertEqual('open', self.usb(shell, 'auth').stdout.strip())
            self.assertIn('adb is open, so every computer gets in with or without a key', r.stdout)
            self.assertEqual(0, self.usb(shell, 'auth', 'keys').returncode)
            r = self.usb(shell, 'key', pub)
            self.assertNotIn('adb is open', r.stdout)

    def test_status_of_the_port_shows_adb(self):
        self.stub('mu300-device', 'echo f50')
        for shell in self.each_shell():
            self.clear()
            r = self.script(shell, BIN / 'mu300-usb', MU300_ADB_CONF=self.conf, MU300_ADB_AUTH_CONF=self.auth,
                            MU300_ADB_KEYS=self.keys, MU300_ADB_ROOT_KEYS=self.tmp / 'root_adb_keys',
                            MU300_GADGET=self.tmp / 'nogadget', MU300_ADB_OWNER=os.getuid())
            self.assertIn('adb: on\nauth: open (every computer', r.stdout)
            self.assertIn('only the U30 Air can be a USB host', r.stderr)      # the rest is the U30 Air's port role

    def test_on_refused_where_it_cannot_run(self):
        (self.tmp / 'unusable').write_text('')
        for shell in self.each_shell():
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('off\n')
            r = self.usb(shell, 'on')
            self.assertEqual(1, r.returncode)
            self.assertIn('no adbd', r.stderr)
            self.assertEqual('off\n', self.conf.read_text())
            self.assertEqual([], self.svc_log())

    def test_root_only(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            for a in (['on'], ['off'], ['key', '-'], ['keys'], ['auth', 'keys'], ['auth', 'open']):
                r = self.usb(shell, *a, stdin='')
                self.assertEqual(1, r.returncode, a)
            self.assertFalse(self.conf.exists())
            self.assertFalse(self.auth.exists())
            self.assertFalse(self.keys.exists())

    def test_keys(self):
        pub = self.tmp / 'adbkey.pub'
        pub.write_text(f'{KEY} me@my-mac.local')        # adbkey.pub has no newline at the end
        for shell in self.each_shell():
            self.keys.unlink(missing_ok=True)
            r = self.usb(shell, 'key', pub)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual(f'{KEY} me@my-mac.local\n', self.keys.read_text())
            self.assertEqual(0o600, self.keys.stat().st_mode & 0o777)
            # once only
            r = self.usb(shell, 'key', pub)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('already authorised', r.stdout)
            self.assertEqual(1, self.keys.read_text().count('\n'))
            # from stdin; a comment cannot carry anything but a name
            r = self.usb(shell, 'key', '-', stdin=f'{KEY2} evil$(reboot);`x`\n')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual(f'{KEY2} evilrebootx', self.keys.read_text().splitlines()[1])
            r = self.usb(shell, 'keys')
            self.assertIn('me@my-mac.local', r.stdout)

    def test_not_a_key(self):
        for shell in self.each_shell():
            self.keys.unlink(missing_ok=True)
            for bad in ('ssh-rsa AAAAB3NzaC1yc2E user', 'QAAAA' + 'A' * 100 + '=', KEY + 'x', KEY[:-1],
                        'QAAAA' + 'A' * 693 + '+/'):
                r = self.usb(shell, 'key', '-', stdin=bad + '\n')
                self.assertEqual(1, r.returncode, bad)
                self.assertIn('no key added', r.stderr)
            self.assertFalse(self.keys.exists() and self.keys.read_text().strip())


if __name__ == '__main__':
    unittest.main()
