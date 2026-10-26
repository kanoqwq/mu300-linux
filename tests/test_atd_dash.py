"""K19: the dashboard's own AT daemons on nr6 and nr7 (openwrt-luci only), and the adapters that use them."""
import re
import unittest

from helpers import GIT_CHECKOUT, TOP

INIT = TOP / 'openwrt' / 'luci-overlay' / 'etc' / 'init.d' / 'mu300-atd-dash'
BUILD = TOP / 'openwrt' / 'build-rootfs.sh'
CELL = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'cell'


class AtdDash(unittest.TestCase):
    def test_two_instances_on_nr6_and_nr7(self):
        f = INIT.read_text()
        self.assertTrue(f.startswith('#!/bin/sh /etc/rc.common\n'))
        self.assertRegex(f, r'(?m)^START=19$')   # same ordering as mu300-atd: before the dial at S20
        self.assertRegex(f, r'(?m)^USE_PROCD=1$')
        self.assertEqual(re.findall(r'procd_open_instance (\S+)', f), ['atd$n'])
        self.assertIn('wait_and_exec /dev/stty_nr$n /opt/mu300/bin/mu300-atd', f)
        self.assertIn('MU300_AT_DIR=/run/mu300-at$n', f)
        self.assertIn('MU300_AT_URC_CHANNELS=\n', f)      # nr1's daemon reads the URCs
        self.assertIn('MU300_AT_DEV=/dev/stty_nr$n', f)
        self.assertRegex(f, r'for n in 6 7; do')
        self.assertNotRegex(f, r'stty_nr[0-5]\b')          # nr1/nr2 stay with mu300-atd
        self.assertIn('/opt/mu300/bin/mu300-atd', f)
        self.assertIn('respawn 3600 10 0', f)

    @unittest.skipUnless(GIT_CHECKOUT, 'no git checkout here')
    def test_executable_and_luci_only(self):
        import subprocess
        mode = subprocess.run(['git', 'ls-files', '-s', str(INIT)], cwd=TOP, capture_output=True,
                              text=True).stdout.split()[:1]
        self.assertEqual(mode, ['100755'])
        b = BUILD.read_text()
        self.assertIn('mu300-atd-dash', b)
        start = b.rindex('if [ -d /in/luci-plugin ]; then', 0, b.index('mu300-atd-dash'))
        block = b[start:b.index('\nfi\n', start)]
        self.assertIn('S${n}mu300-atd-dash', block)       # enabled only inside the luci block, like mu300-smsd
        self.assertIn('S${n}mu300-smsd', block)
        self.assertEqual(b.count('S${n}mu300-atd-dash'), 1)
        self.assertNotIn('mu300-atd-dash', b[:start])

    def test_managed_boot_does_not_start_optional_pool(self):
        import os, subprocess, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'managed'; marker.touch()
            src=INIT.read_text().replace('/run/mu300/sim-managed',str(marker))
            harness='procd_open_instance() { echo OPEN; }; procd_set_param() { :; }; procd_close_instance() { :; };\n'
            r=subprocess.run(['sh','-c',harness+src+'\nstart_service'],env={**os.environ,'MU300_SIM_SLOT':'0'},capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('OPEN',r.stdout)

    def test_adapter_uses_the_pool_and_falls_back_to_nr1(self):
        c = CELL.read_text()
        self.assertIn('/run/mu300-at6 /run/mu300-at7 /run/mu300-at', c)
        self.assertIn('[ -p "$d/cmd" ] || continue', c)   # an absent dash daemon is skipped, nr1 serves


if __name__ == '__main__':
    unittest.main()
