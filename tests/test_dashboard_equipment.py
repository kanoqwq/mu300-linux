"""Run the equipment collector against a fake slot-aware AT owner, never hardware."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/dashboard-equipment'


class Equipment(unittest.TestCase):
    def test_boot_cache_slot_scope_rejection_and_single_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            boot = root/'boot'; boot.write_text('boot-one\n')
            owner = root/'at'
            owner.write_text('''#!/bin/sh
printf '%s %s\\n' "$MU300_SIM_SLOT" "$*" >> "$TEST_LOG"
echo AT+CGSN
case "$MU300_SIM_SLOT" in
0) echo 866100000000408 ;;
1) if [ -f "$TEST_BAD" ]; then echo ERROR; echo 12345; else echo 866100000000409; fi ;;
esac
echo OK
''')
            owner.chmod(0o700)
            env = dict(os.environ, MU300_EQUIPMENT_DIR=str(root/'cache'), MU300_AT=str(owner),
                       MU300_BOOT_ID_FILE=str(boot), TEST_LOG=str(root/'calls'), TEST_BAD=str(root/'bad'))
            def run():
                subprocess.run(['sh', str(SCRIPT)], env=env, check=True, capture_output=True, timeout=5)
            (root/'bad').touch(); run()
            self.assertEqual((root/'cache/imei0').read_text().strip(), '866100000000408')
            self.assertFalse((root/'cache/imei1').exists())
            (root/'bad').unlink(); run()
            self.assertEqual((root/'cache/imei1').read_text().strip(), '866100000000409')
            calls = (root/'calls').read_text(); run()
            self.assertEqual((root/'calls').read_text(), calls, 'successful reads must not poll AT again')
            self.assertEqual(calls.splitlines(), ['0 -t 2 AT+CGSN', '1 -t 2 AT+CGSN', '1 -t 2 AT+CGSN'])
            boot.write_text('boot-two\n'); env['MU300_EQUIPMENT_SLOTS']='1'; run()
            self.assertFalse((root/'cache/imei1').exists(), 'never reuse a previous boot or query an unsupported SIM slot')
            self.assertEqual((root/'calls').read_text().splitlines()[-1], '0 -t 2 AT+CGSN')


if __name__ == '__main__':
    unittest.main()
