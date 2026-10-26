"""mu300-modem-coldboot and its two callers (FINDINGS 196, issue #253): the CP of a U30 Air that LK started for its
charger. The stock modem_control is not in this repository: a stand-in of its size carries the two instructions at
their offsets, and the script is told its checksums (MU300_COLDBOOT_STOCK / _PATCHED); the real ones are checked
against the device's file in FINDINGS 196."""
import hashlib
import re
import unittest

from helpers import ShellTest, BIN

COLD = BIN / 'mu300-modem-coldboot'
SIZE = 0x14000
OFF_PM, OFF_LOAD = 0xcf0c, 0xcf50
ORIG_PM, ORIG_LOAD = bytes.fromhex('f303002a'), bytes.fromhex('e0078052')       # mov w19,w0 / mov w0,#63
NEW_PM, NEW_LOAD = bytes.fromhex('f3031f2a'), bytes.fromhex('40048052')         # mov w19,wzr / mov w0,#34
CHARGER = 'console=ttyS1 androidboot.slot_suffix=_a androidboot.mode=charger modem=shutdown'


class ColdBoot(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        (self.root / 'dev').mkdir()
        self.android = self.tmp / 'android'
        (self.android / 'vendor/bin').mkdir(parents=True)
        (self.android / 'tmp').mkdir()
        img = bytearray(i * 7 % 251 for i in range(SIZE))
        img[OFF_PM:OFF_PM + 4] = ORIG_PM
        img[OFF_LOAD:OFF_LOAD + 4] = ORIG_LOAD
        self.stock = bytes(img)
        img[OFF_PM:OFF_PM + 4] = NEW_PM
        img[OFF_LOAD:OFF_LOAD + 4] = NEW_LOAD
        self.patched = bytes(img)
        (self.android / 'vendor/bin/modem_control').write_bytes(self.stock)
        self.stub('mu300-device', 'echo "${DEVICE:-u30air}"')
        self.stub('mountpoint', 'exit 1')
        self.stub('mount', 'echo "mount $*" >> "$STUBLOG/calls"')
        (self.root / 'etc/mu300').mkdir(parents=True)
        (self.root / 'etc/mu300/modem-coldboot').touch()     # opt-in (off by default)

    def cold(self, shell, cmdline=CHARGER, **env):
        args = self.tmp / 'cmdline'
        args.write_bytes(cmdline.encode() + b'\0')
        e = dict(MU300_SYSROOT=self.root, MU300_BIN=self.stubs, MU300_KMSG='/dev/null',
                 MU300_COLDBOOT_STOCK=hashlib.sha256(self.stock).hexdigest(),
                 MU300_COLDBOOT_PATCHED=hashlib.sha256(self.patched).hexdigest())
        e.update(env)
        return self.script(shell, COLD, self.android, args, **e)

    def test_charger_boot_with_the_cp_down_gets_the_copy(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.cold(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), '/tmp/mu300-coldboot/modem_control')
            copy = self.root / 'run/mu300/coldboot/modem_control'
            self.assertEqual(copy.read_bytes(), self.patched)
            self.assertEqual((self.android / 'vendor/bin/modem_control').read_bytes(), self.stock)  # untouched
            self.assertIn(f'mount --bind {self.root}/run/mu300/coldboot {self.android}/tmp/mu300-coldboot',
                          (self.tmp / 'calls').read_text())
            self.assertEqual([p.name for p in copy.parent.iterdir()], ['modem_control'])   # no temporary left

    def test_every_other_case_is_the_stock_loader(self):
        for shell in self.each_shell():
            cases = {
                'normal boot': dict(cmdline='androidboot.mode=normal'),
                'no modem=shutdown': dict(cmdline='androidboot.mode=charger'),
                'f50': dict(DEVICE='f50'),
                'v50': dict(DEVICE='v50'),
                'another loader': dict(MU300_COLDBOOT_STOCK='0' * 64),
                'not opted in (the default)': dict(optout=True),
                'cp up': dict(cp_up=True),
                'cannot tell': dict(cp_odd=True),
            }
            for name, c in cases.items():
                with self.subTest(case=name):
                    self.setUp()
                    if c.pop('optout', False):
                        (self.root / 'etc/mu300/modem-coldboot').unlink()
                    if c.pop('cp_up', False):
                        (self.root / 'dev/stty_nr1').write_text('')
                    if c.pop('cp_odd', False):
                        (self.root / 'dev/stty_nr1').mkdir()
                    r = self.cold(shell, **c)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout, '', r.stderr)
                    self.assertFalse((self.root / 'run/mu300/coldboot/modem_control').exists())

    def test_a_copy_that_does_not_check_out_is_not_used(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.cold(shell, MU300_COLDBOOT_PATCHED='0' * 64)
            self.assertEqual((r.returncode, r.stdout), (0, ''), r.stderr)
            self.assertIn('not the one expected', r.stderr)
            self.assertFalse((self.root / 'run/mu300/coldboot/modem_control').exists())
            self.assertEqual(list((self.root / 'run/mu300/coldboot').iterdir()), [])

    def test_the_real_checksums_and_offsets(self):
        text = COLD.read_text()
        self.assertIn('43a3bc8c4730b87431d4fc8d90a3652b2458ef8bc9f59772685d4016cc7819a3', text)
        self.assertIn('9e3490f222a312e649dd5273df216b87ca9880a621ea68a0a73df0aa6b37c2ae', text)
        self.assertIn(f'seek={OFF_PM} count=4', text); self.assertIn(f'seek={OFF_LOAD} count=4', text)
        self.assertIn(r"printf '\363\003\037\052'", text); self.assertIn(r"printf '\100\004\200\122'", text)
        self.assertEqual(bytes([0o363, 0o3, 0o37, 0o52]), NEW_PM)
        self.assertEqual(bytes([0o100, 0o4, 0o200, 0o122]), NEW_LOAD)


class Callers(ShellTest):
    def test_vendor_start_asks_and_never_fails_on_it(self):
        text = (BIN / 'android-vendor-start').read_text()
        m = re.search(r'MU300_MODEM_CONTROL=\$\(/opt/mu300/bin/mu300-modem-coldboot [^)]*\) \|\| MU300_MODEM_CONTROL=', text)
        self.assertIsNotNone(m)
        self.assertLess(text.index('mount --bind /run/mu300-cmdline.android'), m.start())
        self.assertLess(m.start(), text.index('exec /opt/mu300/bin/android-run modem_control'))

    def run_android(self, shell, name, value):
        a = self.tmp / 'a'
        (a / 'dev').mkdir(parents=True, exist_ok=True)
        self.stub('mountpoint', 'exit 0')
        self.stub('chroot', 'echo "chroot $*" > "$STUBLOG/calls"')
        self.stub('setpriv', 'exit 1')
        etc = self.tmp / 'etc'; etc.mkdir(exist_ok=True); (etc / 'passwd').write_text('root:x:0:0::/:/bin/sh\n')
        r = self.script(shell, BIN / 'android-run', name, MU300_ANDROID=a, MU300_ETC=etc, MU300_MODEM_CONTROL=value)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().split()[-1]

    def test_android_run_takes_only_the_coldboot_copy(self):
        for shell in self.each_shell():
            self.assertEqual(self.run_android(shell, 'modem_control', '/tmp/mu300-coldboot/modem_control'),
                             '/tmp/mu300-coldboot/modem_control')
            self.assertEqual(self.run_android(shell, 'modem_control', ''), '/vendor/bin/modem_control')
            self.assertEqual(self.run_android(shell, 'modem_control', '/bin/sh'), '/vendor/bin/modem_control')
            self.assertEqual(self.run_android(shell, 'refnotify', '/tmp/mu300-coldboot/modem_control'),
                             '/vendor/bin/refnotify')


if __name__ == '__main__':
    unittest.main()
