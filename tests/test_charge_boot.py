"""Execute the boot gate with safe command stubs; no disks or devices touched."""
from helpers import BIN, TOP, ShellTest
import hashlib
import os
from pathlib import Path
import shutil
from test_modem_loader import build_host, compiler


class ChargeBoot(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root'
        for p in ('proc/device-tree/chosen', 'run/mu300', 'tmp'):
            (self.root/p).mkdir(parents=True,exist_ok=True)
        (self.root/'proc/cmdline').write_text('console=ttyS1\n')
        self.args=self.root/'proc/device-tree/chosen/bootargs'
        self.args.write_bytes(b'androidboot.mode=charger\0')
        for name in ('mu300-led','extra-modules','logger','mount','sync','mu300-next-boot'):
            self.stub(name, 'echo "'+name+' $*" >> "$STUBLOG/calls"')
        self.stub('mu300-device','echo "${TEST_MODEL:-u30air}"')
        self.stub('uci','echo "${TEST_POWER_ON-0}"')
        self.stub('mu300-keys','echo "keys $*" >> "$STUBLOG/calls"; exit "${TEST_KEYS:-0}"')
        self.stub('mountpoint','exit 0')
        self.stub('poweroff','echo "poweroff $*" >> "$STUBLOG/calls"; kill -TERM "$PPID"')

    def call(self,shell,**env):
        (self.tmp/'calls').unlink(missing_ok=True)
        return self.script(shell,BIN/'mu300-charge-mode',MU300_BIN=self.stubs,MU300_SYSROOT=self.root,**env)

    def calls(self):
        path=self.tmp/'calls'
        return path.read_text() if path.exists() else ''

    def test_only_explicit_u30air_charger_opt_out_waits(self):
        for shell in self.each_shell():
            for model in ('f50','v50','unknown'):
                self.assertEqual(self.call(shell,TEST_MODEL=model).returncode,0)
                self.assertEqual(self.calls(),'')
            for enabled in ('1', 'invalid'):
                self.assertEqual(self.call(shell,TEST_POWER_ON=enabled).returncode,0)
                self.assertEqual(self.calls(),'')
            self.args.write_bytes(b'androidboot.mode=normal\0')
            self.assertEqual(self.call(shell).returncode,0)
            self.assertEqual(self.calls(),'')
            self.args.write_bytes(b'androidboot.mode=charger\0')

    def test_power_key_and_input_failure_resume_without_radio_commands(self):
        for shell in self.each_shell():
            for result in ('0','1','2'):
                self.assertEqual(self.call(shell,TEST_KEYS=result).returncode,0)
                calls=self.calls()
                self.assertIn('keys -c -l 1500',calls)
                self.assertLess(calls.index('mu300-led boot off\n'),calls.index('mu300-led battery\n'))
                self.assertLess(calls.index('mu300-led battery\n'),calls.index('keys -c -l 1500\n'))
                self.assertGreater(calls.index('mu300-led boot on\n'),calls.index('keys -c -l 1500\n'))
                self.assertNotIn('poweroff',calls)
                self.assertNotIn('AT+',calls)
                self.assertFalse((self.root/'run/mu300/charging-boot').exists())

    def test_unplug_syncs_root_before_backing_mount_and_powers_off(self):
        for shell in self.each_shell():
            # Some BusyBox builds prefer their own poweroff/mount applets to
            # PATH stubs. Never exercise shutdown stubs under standalone ash.
            if Path(shell[0]).name == 'busybox':
                continue
            result=self.call(shell,TEST_KEYS='3')
            self.assertNotEqual(result.returncode,0)  # Stub terminates the wait, no actual shutdown.
            calls=self.calls()
            self.assertIn('mu300-next-boot --rearm',calls)
            self.assertLess(calls.index('mount -o remount,ro /\n'),calls.index('mount -o remount,ro /mnt/mu300-disk'))
            self.assertIn('poweroff -f',calls)
            self.assertNotIn('reboot',calls)
            self.assertIn('mu300-led boot off\n',calls)
            self.assertNotIn('mu300-led boot on\n',calls)

    def test_boot_order_and_enablement(self):
        init=(TOP/'openwrt/overlay/etc/init.d/mu300-charge-mode').read_text()
        self.assertIn('START=09',init)
        self.assertIn('mu300-vendor mu300-charge-mode mu300-hw',(TOP/'openwrt/build-rootfs.sh').read_text())
        (self.root/'tmp/.failsafe').touch()
        for shell in self.each_shell():
            self.assertEqual(self.call(shell).returncode,0)
            self.assertEqual(self.calls(),'')


class ColdModem(ShellTest):
    def setUp(self):
        super().setUp()
        self.shells=[s for s in self.shells if Path(s[0]).name != 'busybox']
        self.root=self.tmp/'root';(self.root/'dev').mkdir(parents=True)
        self.android=self.tmp/'android';(self.android/'vendor/bin').mkdir(parents=True)
        self.loader=self.android/'vendor/bin/modem_control'
        self.loader.write_bytes(b'unknown stock firmware')
        self.args=self.tmp/'args'
        self.args.write_text('androidboot.mode=charger modem=shutdown\n')
        self.stub('mu300-device','echo "${TEST_MODEL:-u30air}"')
        self.stub('mountpoint','exit 1')
        self.stub('mount','echo "mount $*" >> "$STUBLOG/calls"')
        if not compiler():
            self.skipTest('host C compiler unavailable')
        build_host(self.stubs/'modem-loader-patch')

    def call(self,shell,**env):
        return self.script(shell,BIN/'mu300-modem-coldboot',self.android,self.args,
                           MU300_SYSROOT=self.root,MU300_BIN=self.stubs,**env)

    def test_other_boards_and_normal_boot_do_not_probe_or_patch(self):
        for shell in self.each_shell():
            for model in ('f50','v50'):
                result=self.call(shell,TEST_MODEL=model)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'/vendor/bin/modem_control')
            self.args.write_text('androidboot.mode=normal modem=shutdown\n')
            self.assertEqual(self.call(shell).stdout.strip(),'/vendor/bin/modem_control')
            self.assertFalse((self.root/'run').exists())
            self.args.write_text('androidboot.mode=charger modem=shutdown\n')

    def test_live_cp_uses_original_loader_without_byte_io(self):
        (self.root/'dev/stty_nr1').write_bytes(b'untouched AT bytes')
        for shell in self.each_shell():
            result=self.call(shell)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'/vendor/bin/modem_control')
            self.assertEqual((self.root/'dev/stty_nr1').read_bytes(),b'untouched AT bytes')
            self.assertFalse((self.root/'run').exists())

    def test_unknown_binary_and_ambiguous_cp_state_rejected(self):
        for shell in self.each_shell():
            result=self.call(shell)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('ELF64 AArch64',result.stderr)
            self.assertEqual(self.loader.read_bytes(),b'unknown stock firmware')
            self.args.write_text('androidboot.mode=charger modem=booting\n')
            self.assertNotEqual(self.call(shell).returncode,0)
            self.args.write_text('androidboot.mode=charger modem=shutdown\n')

    def test_real_recipient_loader_patched_only_in_ram(self):
        source=os.environ.get('MU300_COLD_LOADER_TEST_FILE')
        if not source:self.skipTest('actual stock U30 Air loader is not distributed in the repository')
        shutil.copyfile(source,self.loader)
        original=self.loader.read_bytes()
        for shell in self.each_shell():
            result=self.call(shell)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'/tmp/mu300-coldboot/modem_control')
            patched=(self.root/'run/mu300/coldboot/modem_control').read_bytes()
            self.assertEqual(hashlib.sha256(patched).hexdigest(),'9e3490f222a312e649dd5273df216b87ca9880a621ea68a0a73df0aa6b37c2ae')
            self.assertEqual(patched[0xcf0c:0xcf10],bytes.fromhex('f3031f2a'))
            self.assertEqual(patched[0xcf50:0xcf54],bytes.fromhex('40048052'))
            self.assertEqual(self.loader.read_bytes(),original)
