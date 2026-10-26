"""mu300-zram: compressed swap in RAM, on by default (FINDINGS 27 and 162). A fake /sys/block/zram0 and /proc (meminfo,
swaps, vm sysctls) and stub mkswap/swapon/swapoff; under each shell. The wiring: the boot runs it on every system,
the setting survives as /etc/mu300/zram.conf, and the mainline kernels have zram with both compressors."""
import re
import unittest

from helpers import BIN, TOP, ShellTest

MEMTOTAL_KB = 1473000


class ZramBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.sys = self.tmp / 'sys'
        self.proc = self.tmp / 'proc'
        self.conf = self.tmp / 'etc' / 'zram.conf'
        self.stub('id', 'echo 0')
        self.stub('logger', 'exit 0')
        self.stub('modprobe', 'echo "$*" >> "$STUBLOG/modprobe.log"; exit 1')
        self.stub('mkswap', 'echo "$*" >> "$STUBLOG/mkswap.log"')
        # swapon -p PRIO DEV: the device shows up in /proc/swaps as the kernel lists it
        self.stub('swapon', 'echo "$*" >> "$STUBLOG/swapon.log"; '
                            'printf "%s partition 700000 0 %s\\n" "$3" "$2" >> "$STUBLOG/proc/swaps"')
        self.stub('swapoff', 'echo "$*" >> "$STUBLOG/swapoff.log"; '
                             'grep -v "^$1 " "$STUBLOG/proc/swaps" > "$STUBLOG/swaps.new"; '
                             'mv "$STUBLOG/swaps.new" "$STUBLOG/proc/swaps"')

    def fresh(self, algorithms='lzo lzo-rle [lz4] zstd', conf=None, active=False, zram=True):
        for f in ('modprobe.log', 'mkswap.log', 'swapon.log', 'swapoff.log'):
            (self.tmp / f).unlink(missing_ok=True)
        z = self.sys / 'zram0'
        if z.exists():
            for f in z.iterdir():
                f.unlink()
            z.rmdir()
        if zram:
            z.mkdir(parents=True)
            (z / 'comp_algorithm').write_text(algorithms + '\n')
            (z / 'disksize').write_text('0\n')
            (z / 'reset').write_text('')
            (z / 'mm_stat').write_text('0 0 0 0 0 0 0 0 0\n')
        vm = self.proc / 'sys' / 'vm'
        vm.mkdir(parents=True, exist_ok=True)
        (vm / 'swappiness').write_text('60\n')
        (vm / 'page-cluster').write_text('3\n')
        (self.proc / 'meminfo').write_text(f'MemTotal:        {MEMTOTAL_KB} kB\nMemFree:          500000 kB\n')
        swaps = 'Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n'
        if active:
            swaps += '/dev/zram0 partition 736500 0 100\n'
        (self.proc / 'swaps').write_text(swaps)
        self.conf.parent.mkdir(parents=True, exist_ok=True)
        self.conf.unlink(missing_ok=True)
        if conf is not None:
            self.conf.write_text(conf)

    def zram(self, shell, *args):
        return self.script(shell, BIN / 'mu300-zram', *args, MU300_ZRAM_CONF=self.conf, MU300_ZRAM_SYS=self.sys,
                           MU300_ZRAM_PROC=self.proc, MU300_ZRAM_DEV='/dev/zram0')

    def read(self, name):
        p = self.tmp / name
        return p.read_text() if p.exists() else ''

    def z(self, name):
        return (self.sys / 'zram0' / name).read_text().strip()

    def vm(self, name):
        return (self.proc / 'sys' / 'vm' / name).read_text().strip()


class Start(ZramBase):
    def test_defaults_half_the_ram_lz4_priority_100(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.z('disksize'), f'{MEMTOTAL_KB // 2}K')
            self.assertEqual(self.z('comp_algorithm'), 'lz4')
            self.assertEqual(self.read('mkswap.log').strip(), '/dev/zram0')
            self.assertEqual(self.read('swapon.log').strip(), '-p 100 /dev/zram0')
            self.assertEqual(self.vm('swappiness'), '100')
            self.assertEqual(self.vm('page-cluster'), '0')
            self.assertEqual(self.read('modprobe.log'), '', 'zram is there: nothing to load')

    def test_settings_from_the_file(self):
        conf = ('ZRAM_SIZE_PERCENT=25\nZRAM_ALGORITHM=zstd\nZRAM_PRIORITY=5\nZRAM_SWAPPINESS=150\n'
                '# a comment\nZRAM_ENABLE=1\n')
        for shell in self.each_shell():
            self.fresh(conf=conf)
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.z('disksize'), f'{MEMTOTAL_KB * 25 // 100}K')
            self.assertEqual(self.z('comp_algorithm'), 'zstd')
            self.assertEqual(self.read('swapon.log').strip(), '-p 5 /dev/zram0')
            self.assertEqual(self.vm('swappiness'), '150')

    def test_a_compressor_the_kernel_lacks_falls_back_to_lz4(self):
        for shell in self.each_shell():
            self.fresh(algorithms='[lz4]', conf='ZRAM_ALGORITHM=zstd\n')
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.z('comp_algorithm'), 'lz4')
            self.assertIn('zstd not offered', r.stderr)
            self.assertIn('-p 100 /dev/zram0', self.read('swapon.log'))

    def test_values_out_of_range_or_not_numbers_take_the_defaults(self):
        conf = 'ZRAM_SIZE_PERCENT=999\nZRAM_PRIORITY=-3\nZRAM_SWAPPINESS=abc\nZRAM_ALGORITHM=$(reboot)\n'
        for shell in self.each_shell():
            self.fresh(conf=conf)
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.z('disksize'), f'{MEMTOTAL_KB // 2}K')
            self.assertEqual(self.read('swapon.log').strip(), '-p 100 /dev/zram0')
            self.assertEqual(self.vm('swappiness'), '100')
            self.assertEqual(self.z('comp_algorithm'), 'lz4')

    def test_the_last_line_of_a_key_wins(self):
        for shell in self.each_shell():
            self.fresh(conf='ZRAM_SIZE_PERCENT=25\nZRAM_SIZE_PERCENT=75\n')
            self.assertEqual(self.zram(shell, 'start').returncode, 0)
            self.assertEqual(self.z('disksize'), f'{MEMTOTAL_KB * 75 // 100}K')

    def test_switched_off_does_nothing(self):
        for shell in self.each_shell():
            self.fresh(conf='ZRAM_ENABLE=0\n')
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.read('swapon.log'), '')
            self.assertEqual(self.z('disksize'), '0')
            self.assertEqual(self.vm('swappiness'), '60', 'the kernel default stays without zram')

    def test_already_on_is_left_as_it_is(self):
        for shell in self.each_shell():
            self.fresh(active=True)
            r = self.zram(shell, 'start')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.read('mkswap.log') + self.read('swapon.log'), '')
            self.assertEqual(self.z('disksize'), '0', 'no reset or new size under a live swap')
            self.assertEqual(self.vm('page-cluster'), '0')

    def test_no_zram_in_the_kernel_is_an_error_after_trying_the_module(self):
        for shell in self.each_shell():
            self.fresh(zram=False)
            r = self.zram(shell, 'start')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('zram num_devices=1', self.read('modprobe.log'))
            self.assertIn('no zram in this kernel', r.stderr)


class OnOff(ZramBase):
    def test_off_swaps_off_frees_and_stays_off(self):
        for shell in self.each_shell():
            self.fresh(active=True, conf='ZRAM_ALGORITHM=zstd\n')
            r = self.zram(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.read('swapoff.log').strip(), '/dev/zram0')
            self.assertEqual(self.z('reset'), '1')
            self.assertEqual(self.conf.read_text(), 'ZRAM_ALGORITHM=zstd\nZRAM_ENABLE=0\n', 'other settings kept')
            self.assertIn('off (', r.stdout)
            # the next boot leaves it off
            self.assertEqual(self.zram(shell, 'start').returncode, 0)
            self.assertNotIn('/dev/zram0 ', (self.proc / 'swaps').read_text())

    def test_on_writes_the_setting_and_starts(self):
        for shell in self.each_shell():
            self.fresh(conf='ZRAM_ENABLE=0\nZRAM_PRIORITY=7\n')
            r = self.zram(shell, 'on')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'ZRAM_PRIORITY=7\nZRAM_ENABLE=1\n')
            self.assertEqual(self.read('swapon.log').strip(), '-p 7 /dev/zram0')
            self.assertIn('zram swap: on', r.stdout)

    def test_a_failed_swapoff_is_reported(self):
        self.stub('swapoff', 'echo "swapoff: Cannot allocate memory" >&2; exit 1')
        for shell in self.each_shell():
            self.fresh(active=True)
            r = self.zram(shell, 'off')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not enough free RAM', r.stderr)
            self.assertEqual(self.z('reset'), '', 'no reset of a device still in use')

    def test_status_and_brief(self):
        for shell in self.each_shell():
            self.fresh(active=True)
            (self.sys / 'zram0' / 'disksize').write_text(str(736500 * 1024) + '\n')
            (self.sys / 'zram0' / 'mm_stat').write_text(f'{300 << 20} {100 << 20} {120 << 20} 0 0 0 0 0 0\n')
            r = self.zram(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('zram swap: on, 719 MiB, lz4, priority 100', r.stdout)
            self.assertIn('holds 300 MiB of swapped-out memory in 120 MiB of RAM (2.5:1)', r.stdout)
            self.assertEqual(self.zram(shell, 'brief').stdout.strip(), 'zram on, 719 MiB lz4, 300 MiB used')
            self.fresh(conf='ZRAM_ENABLE=0\n')
            self.assertIn('ZRAM_ENABLE=0', self.zram(shell, 'status').stdout)
            self.assertEqual(self.zram(shell, 'brief').stdout.strip(), 'zram off')


class Wiring(unittest.TestCase):
    def test_every_system_starts_it_at_boot(self):
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-zram.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-zram start', unit)
        self.assertIn('ExecStop=-/opt/mu300/bin/mu300-zram stop', unit)
        for f in ('rootfs/assemble.sh', 'arch/build-rootfs.sh'):
            self.assertIn('mu300-zram.service:swap.target', (TOP / f).read_text(), f)
        # OpenWrt: mu300-hw runs the old name, which hands over to mu300-zram
        self.assertIn('/opt/mu300/bin/zram-start', (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text())
        self.assertIn('exec /opt/mu300/bin/mu300-zram start', (BIN / 'zram-start').read_text())
        self.assertIn('mu300-zram', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())

    def test_no_second_zram_setup_in_the_images(self):
        # Ubuntu's zram-tools / zram-config / systemd-zram-generator would set up zram0 a second time
        for f in ('rootfs/Dockerfile', 'rootfs/assemble.sh', 'arch/build-rootfs.sh', 'openwrt/build-rootfs.sh'):
            src = (TOP / f).read_text()
            for pkg in ('zram-tools', 'zram-config', 'zram-generator', 'zram-swap', 'kmod-zram'):
                self.assertNotIn(pkg, src, f'{f}: {pkg}')

    def test_the_settings_example_names_every_key_the_script_reads(self):
        keys = set(re.findall(r'conf (ZRAM_[A-Z_]+)', (BIN / 'mu300-zram').read_text()))
        example = (TOP / 'rootfs/overlay/opt/mu300/lib/zram.conf.example').read_text()
        self.assertEqual(keys, {'ZRAM_ENABLE', 'ZRAM_SIZE_PERCENT', 'ZRAM_ALGORITHM', 'ZRAM_PRIORITY',
                                'ZRAM_SWAPPINESS'})
        for k in keys:
            self.assertIn(f'#{k}=', example)

    def test_mainline_has_zram_with_lz4_and_zstd(self):
        cfg = (TOP / 'upstream/mu300-mainline.config').read_text().splitlines()
        for opt in ('SWAP', 'ZRAM', 'ZSMALLOC', 'ZRAM_BACKEND_LZ4', 'ZRAM_BACKEND_ZSTD', 'ZRAM_DEF_COMP_LZ4',
                    'CRYPTO_LZ4', 'CRYPTO_ZSTD'):
            self.assertIn(f'CONFIG_{opt}=y', cfg)

    def test_vendor_54_has_zram_and_both_compressors(self):
        # 5.4 is left as it is: zram a module (the fragment's =y is refused), the crypto API compressors built in
        cfg = (TOP / 'kernel/linux.config').read_text().splitlines()
        for line in ('CONFIG_ZRAM=m', 'CONFIG_ZSMALLOC=m', 'CONFIG_CRYPTO_LZ4=y', 'CONFIG_CRYPTO_ZSTD=y'):
            self.assertIn(line, cfg)

    def test_toolkit_offers_it(self):
        tk = (BIN / 'mu300-toolkit').read_text()
        self.assertIn('"Compressed swap (zram)"', tk)
        self.assertIn('8) menu_zram ;;', tk)
        self.assertIn('mu300-zram brief', tk)


if __name__ == '__main__':
    unittest.main()
