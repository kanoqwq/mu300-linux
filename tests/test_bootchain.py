"""tools/android-bootchain.sh: the boot chain of Android's slot (uboot, sml, teecfg, trustos, vbmeta) copied to the
Linux slot, on a fake by-name directory of files. Only the Linux slot's images change, a backup with the right hashes
exists before the first write, a write that does not read back is undone, Android's slot is never written, and
nothing outside the five names is ever touched. Also its callers: android-install.sh's block, the plan line
(storage.sh's bootchain_line) and the switch module's copy and subcommand."""
import hashlib
import re
import shutil
import subprocess
import unittest

from helpers import TOP, ShellTest
from test_boot_image import LIVE_A, fake_misc, fake_stock, with_slots

SCRIPT = TOP / 'tools' / 'android-bootchain.sh'
MODULE_COPY = TOP / 'android' / 'magisk' / 'mu300-linux-switch' / 'bootchain.sh'
CHAIN = ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta')
# never to be written by this, whatever happens (backup-device.sh's CRITICAL list and more)
NEVER = ('nr_fixnv1', 'nr_fixnv2', 'nr_runtimenv1', 'nr_deltanv', 'prodnv', 'miscdata', 'persist', 'calinv', 'isedata',
         'ztecfg', 'ztepersist', 'boot', 'init_boot', 'vendor_boot', 'super', 'userdata', 'misc', 'nr_modem', 'nr_phy',
         'pm_sys', 'metadata')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def linux_image(path):
    """a boot image as ours start: loglevel=5 as the header's command line (offset 44)"""
    fake_stock(path, size=1 << 20, vbmeta_offset=1 << 19)
    with open(path, 'r+b') as f:
        f.seek(44)
        f.write(b'loglevel=5\0')


class Fixture(ShellTest):
    def setUp(self):
        super().setUp()
        self.by = self.tmp / 'by-name'
        self.by.mkdir()
        self.props = self.tmp / 'props'
        self.stub('getprop', f'sed -n "s/^$1=//p" "{self.props}"')
        self.device()

    def device(self, slot='_a', model='ZTE MU300', product='mu300', differ=(), meta=None, platform=''):
        """Android on SLOT; the five images identical on both slots except DIFFER (Android's slot holds the newer
        build); META: (slot a, slot b) metadata bytes of misc instead of the ones of Android booted on SLOT"""
        shutil.rmtree(self.by)
        self.by.mkdir()
        self.props.write_text(f'ro.product.model={model}\nro.product.device={product}\nro.boot.slot_suffix={slot}\n'
                              f'ro.board.platform={platform}\n')
        a, b = meta or ((0x9f, 0x1e) if slot == '_a' else (0x1e, 0x9f))
        fake_misc(self.by / 'misc', with_slots(LIVE_A, (slot or '_a').encode() + b'\0\0', a, b))
        fake_stock(self.by / 'boot_a', size=1 << 20, vbmeta_offset=1 << 19)
        fake_stock(self.by / 'boot_b', size=1 << 20, vbmeta_offset=1 << 19)
        android = (slot or '_a')[1]
        for p in CHAIN:
            for s in 'ab':
                build = b'new' if (p in differ and s == android) else b'old'
                (self.by / f'{p}_{s}').write_bytes((p.encode() + b':' + build + b';') * 4096)
        # partitions of the same names that must never change: NV, calibration, Android's data
        for p in NEVER:
            for s in ('_a', '_b', ''):
                if not (self.by / f'{p}{s}').exists():
                    (self.by / f'{p}{s}').write_bytes(p.encode() * 512)

    def snapshot(self):
        return {p.name: sha(p) for p in sorted(self.by.iterdir()) if p.is_file()}

    def run_bc(self, shell, *args, **env):
        e = dict(MU300_BY_NAME=self.by, MU300_SKIP_ROOT_CHECK=1, MU300_DROP_CACHES=self.tmp / 'no-drop-caches')
        e.update(env)
        return self.script(shell, SCRIPT, *args, **e)


class Check(Fixture):
    def test_identical(self):
        for shell in self.each_shell():
            before = self.snapshot()
            r = self.run_bc(shell, 'check')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], 'BOOTCHAIN-PLAN a b')
            self.assertIn('boot chain: identical on both slots', r.stdout)
            self.assertEqual(self.snapshot(), before)

    def test_differing_names_in_chain_order(self):
        for shell in self.each_shell():
            self.device(differ=('trustos', 'sml'))
            before = self.snapshot()
            r = self.run_bc(shell, 'check')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], 'BOOTCHAIN-PLAN a b sml trustos')
            self.assertIn("boot chain: sml, trustos differ between the slots; Android's slot a is copied to slot b "
                          '(backed up first)', r.stdout)
            self.assertEqual(self.snapshot(), before)

    def test_android_on_b(self):
        for shell in self.each_shell():
            self.device(slot='_b', differ=('vbmeta',))
            r = self.run_bc(shell, 'check')
            self.assertEqual(r.stdout.splitlines()[0], 'BOOTCHAIN-PLAN b a vbmeta', r.stdout + r.stderr)


class Refusals(Fixture):
    def refused(self, shell, why, *args):
        before = self.snapshot()
        for cmd in (('check',), ('sync', self.tmp / 'backup')):
            r = self.run_bc(shell, *cmd, *args)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertRegex(r.stdout.splitlines()[0], '^BOOTCHAIN-REFUSED: .*' + why)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.tmp / 'backup').exists())

    def test_size_mismatch(self):
        for shell in self.each_shell():
            self.device(differ=('trustos',))
            with open(self.by / 'trustos_b', 'ab') as f:
                f.write(b'x')
            self.refused(shell, 'differ in size')

    def test_missing_link(self):
        for shell in self.each_shell():
            self.device(differ=('sml',))
            (self.by / 'teecfg_b').unlink()
            self.refused(shell, 'teecfg_a or teecfg_b is missing')

    def test_both_slots_the_same_partition(self):
        for shell in self.each_shell():
            self.device()
            (self.by / 'uboot_b').unlink()
            (self.by / 'uboot_b').symlink_to(self.by / 'uboot_a')
            self.refused(shell, 'the same partition')

    def test_unknown_slot(self):
        for shell in self.each_shell():
            self.device(slot='', meta=(0x9f, 0x1e))
            self.refused(shell, 'which slot')

    def test_not_an_f50(self):
        for shell in self.each_shell():
            self.device(model='Pixel 7', product='panther', differ=('trustos',))
            self.refused(shell, 'not a ZTE F50')

    def test_u30_air_is_one(self):
        for shell in self.each_shell():
            self.device(model='U30 Air', product='U30Air')
            self.assertEqual(self.run_bc(shell, 'check').returncode, 0)

    def test_v50_only_exactly(self):
        # the ZTE V50 (FINDINGS 95): V50 / MU3351 on ums9620, nothing less (a Blade V50 phone is no V50)
        for shell in self.each_shell():
            self.device(model='V50', product='MU3351', platform='ums9620')
            self.assertEqual(self.run_bc(shell, 'check').returncode, 0)
            self.device(model='V50', product='MU3351', platform='mt6789')
            self.refused(shell, 'not on the UMS9620 platform')
            self.device(model='ZTE Blade V50', product='P671F60', platform='ums9620')
            self.refused(shell, 'not a ZTE F50')

    def test_android_slot_not_marked_successful(self):
        # right after an update: the other slot is still the one to fall back to
        for shell in self.each_shell():
            self.device(differ=('trustos',), meta=(0x1f, 0x9e))
            self.refused(shell, 'not marked as booted successfully')

    def test_other_slot_set_to_boot_next_without_linux(self):
        # an Android update waiting for its restart
        for shell in self.each_shell():
            self.device(differ=('trustos',), meta=(0x9e, 0x7f))
            self.refused(shell, 'set to boot next and holds no Linux')

    def test_armed_linux_slot_is_fine(self):
        for shell in self.each_shell():
            self.device(differ=('trustos',), meta=(0x9e, 0x2f))
            linux_image(self.by / 'boot_b')
            r = self.run_bc(shell, 'check')
            self.assertEqual(r.stdout.splitlines()[0], 'BOOTCHAIN-PLAN a b trustos', r.stdout + r.stderr)

    def test_never_from_linux(self):
        # Linux has no getprop; and a running slot whose boot partition holds Linux is not Android's
        for shell in self.each_shell():
            self.device(differ=('trustos',))
            (self.stubs / 'getprop').rename(self.tmp / 'getprop')
            try:
                self.refused(shell, 'not Android')
            finally:
                (self.tmp / 'getprop').rename(self.stubs / 'getprop')
            linux_image(self.by / 'boot_a')
            self.refused(shell, 'holds Linux')

    def test_no_boot_control_block(self):
        for shell in self.each_shell():
            self.device(differ=('trustos',))
            (self.by / 'misc').write_bytes(bytes(4096))
            self.refused(shell, 'no bootloader_control')


class Sync(Fixture):
    def test_identical_writes_nothing(self):
        for shell in self.each_shell():
            self.device()
            before = self.snapshot()
            r = self.run_bc(shell, 'sync', self.tmp / 'backup')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], 'BOOTCHAIN-OK a b')
            self.assertIn('identical on both slots, nothing written', r.stdout)
            self.assertEqual(self.snapshot(), before)
            self.assertFalse((self.tmp / 'backup').exists())

    def check_synced(self, shell, android, linux, differ):
        self.device(slot='_' + android, differ=differ)
        before = self.snapshot()
        backup = self.tmp / f'backup-{android}'
        shutil.rmtree(backup, ignore_errors=True)
        r = self.run_bc(shell, 'sync', backup)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-2], f'BOOTCHAIN-OK {android} {linux} ' + ' '.join(
            p for p in CHAIN if p in differ))
        after = self.snapshot()
        changed = sorted(n for n in after if after[n] != before[n])
        self.assertEqual(changed, sorted(f'{p}_{linux}' for p in differ))
        for p in CHAIN:
            self.assertEqual(after[f'{p}_{linux}'], before[f'{p}_{android}'], p)
        # the backup: both slots' images as they were, and SHA256SUMS saying so
        sums = dict(reversed(line.split('  ')) for line in (backup / 'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sorted(sums), sorted(f'{p}_{s}.img' for p in differ for s in 'ab'))
        for name, h in sums.items():
            self.assertEqual(h, before[name[:-4]], name)
            self.assertEqual(sha(backup / name), h, name)

    def test_android_on_a_copies_to_b(self):
        for shell in self.each_shell():
            self.check_synced(shell, 'a', 'b', ('trustos', 'sml'))

    def test_android_on_b_copies_to_a(self):
        for shell in self.each_shell():
            self.check_synced(shell, 'b', 'a', ('uboot', 'teecfg', 'trustos', 'vbmeta', 'sml'))

    def test_skip_root_check_is_needed_off_device(self):
        # as a user, not root: refused before anything (the tests' MU300_SKIP_ROOT_CHECK stands in for su)
        if subprocess.run(['id', '-u'], capture_output=True, text=True).stdout.strip() == '0':
            self.skipTest('running as root')
        for shell in self.each_shell():
            self.device(differ=('trustos',))
            r = self.script(shell, SCRIPT, 'check', MU300_BY_NAME=self.by)
            self.assertIn('BOOTCHAIN-REFUSED: this needs root', r.stdout)

    def corrupting_dd(self, pattern, source):
        """dd that writes, then spoils the target when it is PATTERN and the input SOURCE (a glob)"""
        real = shutil.which('dd')
        self.stub('dd', f'''"{real}" "$@" || exit $?
of=; src=
for a; do case $a in of=*) of=${{a#of=}} ;; if=*) src=${{a#if=}} ;; esac; done
case $of in {pattern}) case $src in {source}) printf X | "{real}" of="$of" bs=1 conv=notrunc 2>/dev/null ;; esac ;; esac
exit 0''')

    def test_read_back_mismatch_restores_and_fails(self):
        for shell in self.each_shell():
            self.device(differ=('sml', 'trustos', 'vbmeta'))
            before = self.snapshot()
            self.corrupting_dd('*/trustos_b', '*_a.img')
            try:
                r = self.run_bc(shell, 'sync', self.tmp / 'backup')
            finally:
                (self.stubs / 'dd').unlink()
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('BOOTCHAIN-FAILED: trustos_b did not verify after writing; slot b was restored', r.stdout)
            # sml_b had been written and verified: it is back as well; vbmeta_b was never reached
            self.assertEqual(self.snapshot(), before)
            self.assertTrue((self.tmp / 'backup' / 'trustos_b.img').exists())
            shutil.rmtree(self.tmp / 'backup')

    def test_restore_that_fails_too(self):
        for shell in self.each_shell():
            self.device(differ=('trustos',))
            before = self.snapshot()
            self.corrupting_dd('*/trustos_b', '*')
            try:
                r = self.run_bc(shell, 'sync', self.tmp / 'backup')
            finally:
                (self.stubs / 'dd').unlink()
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn('Do not boot slot b', r.stdout)
            after = self.snapshot()
            # Android's slot and everything else as before; only the Linux slot's trustos is not
            self.assertEqual({k: v for k, v in after.items() if k != 'trustos_b'},
                             {k: v for k, v in before.items() if k != 'trustos_b'})
            self.assertEqual(sha(self.tmp / 'backup' / 'trustos_b.img'), before['trustos_b'])
            shutil.rmtree(self.tmp / 'backup')

    def test_android_slot_never_written(self):
        # whatever the hashes say, every dd writes to the Linux slot or the backup
        for shell in self.each_shell():
            self.device(differ=CHAIN)
            real = shutil.which('dd')
            self.stub('dd', f'for a; do case $a in of=*) echo "${{a#of=}}" >> "$STUBLOG/dd-targets" ;; esac; done\n'
                            f'exec "{real}" "$@"')
            try:
                r = self.run_bc(shell, 'sync', self.tmp / 'backup')
            finally:
                (self.stubs / 'dd').unlink()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            targets = (self.tmp / 'dd-targets').read_text().split()
            (self.tmp / 'dd-targets').unlink()
            parts = sorted(t for t in targets if t.startswith(str(self.by)))
            self.assertEqual(parts, sorted(f'{self.by}/{p}_b' for p in CHAIN))
            others = [t for t in targets if not t.startswith(str(self.by)) and t != '/dev/null']
            self.assertTrue(all(t.startswith(str(self.tmp / 'backup')) for t in others), others)
            shutil.rmtree(self.tmp / 'backup')


class TheList(Fixture):
    def lib(self, shell, code, **env):
        return self.sh(shell, f'MU300_LIB=1 . "{SCRIPT}"\n' + code, MU300_BY_NAME=self.by, MU300_SKIP_ROOT_CHECK=1,
                       **env)

    def test_exactly_the_five(self):
        for shell in self.each_shell():
            r = self.lib(shell, 'echo "$BOOTCHAIN_PARTS"')
            self.assertEqual(r.stdout.split(), list(CHAIN))

    def test_nothing_else_is_allowed(self):
        for shell in self.each_shell():
            r = self.lib(shell, 'for p in ' + ' '.join(NEVER + CHAIN) +
                         '; do bc_allowed $p && echo $p; done')
            self.assertEqual(r.stdout.split(), list(CHAIN))
            # an NV name put into the list by mistake is still refused, before anything is read or written
            r = self.lib(shell, 'BOOTCHAIN_PARTS="uboot nr_fixnv1"; for p in nr_fixnv1 prodnv; do bc_allowed $p && echo $p; done; true')
            self.assertEqual(r.stdout, '')
            r = self.lib(shell, 'BOOTCHAIN_PARTS="uboot nr_fixnv1"; bc_setup && bc_scan; echo "rc=$?"')
            self.assertIn('BOOTCHAIN-REFUSED: nr_fixnv1 is not part of the boot chain', r.stdout)

    def test_backup_device_knows_every_one(self):
        # tools/backup-device.sh saves both slots of each, so a full backup can undo this too
        text = (TOP / 'tools' / 'backup-device.sh').read_text()
        for p in CHAIN:
            for s in 'ab':
                self.assertIn(f'{p}_{s}', text)


class Copies(unittest.TestCase):
    def test_module_copy_is_identical(self):
        self.assertEqual(SCRIPT.read_bytes(), MODULE_COPY.read_bytes(),
                         'android/magisk/mu300-linux-switch/bootchain.sh must be a copy of tools/android-bootchain.sh')

    def test_shipped_with_every_installer(self):
        self.assertIn('android-bootchain.sh', (TOP / 'install.sh').read_text())
        self.assertIn('android-bootchain.sh', (TOP / 'install.ps1').read_text())
        mk = (TOP / 'tools' / 'make-magisk-zips.sh').read_text()
        self.assertIn('mu300/android-bootchain.sh', mk)
        self.assertIn(' bootchain.sh ', mk)
        self.assertIn('bootchain.sh', (TOP / 'android/magisk/installer/customize.sh').read_text())
        self.assertIn('bootchain.sh', (TOP / 'tools/install-magisk-module.sh').read_text())
        ps = (TOP / 'install.ps1').read_text()
        self.assertIn("'switch.sh', 'action.sh', 'bootchain.sh'", ps)
        self.assertIn('$Mod/bootchain.sh', ps)


class AndroidInstall(ShellTest):
    """android-install.sh's bootchain block: SYNC_BOOTCHAIN=1 runs the sync into the Linux filesystem and stops
    the installation when it fails"""

    def setUp(self):
        super().setUp()
        src = (TOP / 'tools' / 'android-install.sh').read_text()
        m = re.search(r'# --- bootchain begin\n(.*?)# --- bootchain end', src, re.S)
        self.assertIsNotNone(m)
        self.block = m.group(1)
        self.T = self.tmp / 'T'
        self.M = self.T / 'mu300root'
        (self.M / '.mu300').mkdir(parents=True)

    def fake_sync(self, rc, out):
        (self.T / 'android-bootchain.sh').write_text(
            f'echo "$@" > "{self.tmp}/called"\nmkdir -p "$2" && echo x > "$2/SHA256SUMS"\nprintf "%s\\n" "{out}"\nexit {rc}\n')

    def run_block(self, shell, **env):
        code = (f'set -e\nsay() {{ echo "[device] $*"; }}\nT="{self.T}"; M="{self.M}"\n' + self.block +
                'echo REACHED-INSTALL\n')
        return self.sh(shell, code, **env)

    def test_off_by_default(self):
        for shell in self.each_shell():
            self.fake_sync(0, 'BOOTCHAIN-OK a b')
            r = self.run_block(shell)
            self.assertIn('REACHED-INSTALL', r.stdout)
            self.assertFalse((self.tmp / 'called').exists())

    def test_sync_into_the_linux_filesystem(self):
        for shell in self.each_shell():
            self.fake_sync(0, 'BOOTCHAIN-OK a b trustos')
            r = self.run_block(shell, SYNC_BOOTCHAIN=1)
            self.assertIn('REACHED-INSTALL', r.stdout, r.stderr)
            args = (self.tmp / 'called').read_text().split()
            self.assertEqual(args[0], 'sync')
            self.assertRegex(args[1], '^' + re.escape(str(self.M)) + r'/\.mu300/bootchain/\d{8}-\d{6}$')
            # for the host installers: a copy they pull
            self.assertTrue((self.T / 'mu300-bootchain-backup' / 'SHA256SUMS').exists())
            (self.tmp / 'called').unlink()
            shutil.rmtree(self.T / 'mu300-bootchain-backup')
            # the Magisk installer's run keeps it on the Linux filesystem only
            self.run_block(shell, SYNC_BOOTCHAIN=1, MU300_DEVICE_WORK=self.T)
            self.assertFalse((self.T / 'mu300-bootchain-backup').exists())

    def test_failure_stops_before_the_systems(self):
        for shell in self.each_shell():
            self.fake_sync(1, 'BOOTCHAIN-FAILED: trustos_b did not verify after writing; slot b was restored')
            r = self.run_block(shell, SYNC_BOOTCHAIN=1)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn('REACHED-INSTALL', r.stdout)
            self.assertIn('[device] trustos_b did not verify', r.stdout)
            self.assertIn('no system was installed', r.stdout)


class PlanLine(ShellTest):
    """storage.sh's bootchain_line: what install.sh and the Magisk installer show"""

    # a t() that fills in {1}.. as tools/i18n.sh does for English
    T = ('t() { _s=$1; shift; i=1; for a; do _s=$(printf %s "$_s" | sed "s|{$i}|$a|g"); i=$((i + 1)); done; '
         'printf %s "$_s"; }\n')

    def line(self, shell, out):
        code = (self.T + f'. "{TOP}/tools/storage.sh"\n'
                'if bootchain_line "$1"; then echo "LINE=$BC_LINE"; else echo "REFUSED=$BC_REFUSED"; fi')
        return subprocess.run(shell + ['-c', code, 'x', out], capture_output=True, text=True,
                              env=self.env()).stdout.strip()

    def test_lines(self):
        for shell in self.each_shell():
            self.assertEqual(self.line(shell, 'BOOTCHAIN-PLAN a b\nboot chain: identical on both slots'),
                             'LINE=boot chain:     identical on both slots')
            self.assertEqual(self.line(shell, 'BOOTCHAIN-PLAN b a sml trustos\nx'),
                             "LINE=boot chain:     sml, trustos differ between the slots; Android's slot b is copied "
                             'to slot a (backed up first)')
            self.assertEqual(self.line(shell, 'BOOTCHAIN-REFUSED: uboot_a and uboot_b differ in size (1 and 2 bytes)'),
                             'REFUSED=uboot_a and uboot_b differ in size (1 and 2 bytes)')
            self.assertEqual(self.line(shell, ''), 'REFUSED=no answer from the device')


class Installers(unittest.TestCase):
    def test_install_sh_plans_and_pulls(self):
        src = (TOP / 'install.sh').read_text()
        # checked right after the slots, before anything is downloaded or written; shown in the plan; the backup
        # pulled after android-install.sh and before the boot image is written
        self.assertLess(src.index('\nslot_setup\n'), src.index('\nbootchain_plan\n'))
        self.assertLess(src.index('\nbootchain_plan\n'), src.index("say \"$(t 'Ready to install')\""))
        self.assertIn('echo "  $BC_LINE"', src)
        self.assertLess(src.index('\nbootchain_pull\n'), src.index('\nwrite_linux_boot "$WORK'))
        self.assertIn('MU300_SYNC_BOOTCHAIN', src)

    def test_install_ps1(self):
        src = (TOP / 'install.ps1').read_text()
        self.assertIn('[switch]$NoBootchainSync', src)
        self.assertIn('SYNC_BOOTCHAIN = $SYNC_BOOTCHAIN', src)
        self.assertLess(src.index('BootchainLine $bcOut'), src.index("Say (T 'Ready to install')"))
        self.assertLess(src.index('mu300-bootchain-backup ] && echo y'), src.index("Say (T 'Writing boot_{1} and arming slot {1}'"))

    def test_switch_module_subcommand(self):
        sw = (TOP / 'android/magisk/mu300-linux-switch/switch.sh').read_text()
        self.assertIn('exec sh "${0%/*}/bootchain.sh" check', sw)
        self.assertIn('exec sh "${0%/*}/bootchain.sh" sync', sw)


if __name__ == '__main__':
    unittest.main()
