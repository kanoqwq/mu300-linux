"""Run the production boot adapter in a private filesystem with fake insmod.

No host sysfs, firmware devices or module-loading commands are used.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'POSIX shell and flock')
class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('bin', 'modules', 'run', 'sys/module', 'sys/class/block',
                     'sys/devices/system/cpu/cpufreq', 'sys/firmware/devicetree/base', 'dev'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.write('sys/firmware/devicetree/base/compatible', 'sprd,ums9632\0')
        self.write('modules/sprd-cpufreq-v2.ko', 'parm=voltage_offset_uv:offsets\0')
        self.write('modules/u30pro-sprd-scufreq.ko', 'companion')
        self.executable('helper', '#!/bin/sh\necho "${OFFSETS:-0,0}"\nexit "${HELPER_ERROR:-0}"\n')
        self.executable('checker', '#!/bin/sh\necho "check $*" >> "$FIXTURE/log"\n[ "${BAD_SML:-0}" = 0 ]\n')
        self.executable('bin/insmod', '''#!/bin/sh
echo "insmod $*" >> "$FIXTURE/log"
case "$1" in
  *scufreq.ko) [ "${SCU_FAIL:-0}" = 0 ] || exit 1
    mkdir -p "$FIXTURE/sys/module/u30pro_sprd_scufreq" ;;
  *) mkdir -p "$FIXTURE/sys/module/sprd_cpufreq_v2" ;;
esac
''')
        self.executable('bin/sync', '#!/bin/sh\necho sync >> "$FIXTURE/log"\n')
        for slot in ('a', 'b'):
            self.write('sys/class/block/part_' + slot + '/uevent', 'PARTNAME=sml_' + slot + '\n')
            self.write('dev/part_' + slot, 'synthetic firmware')
        source = (TOP / 'devices/u30pro/overlay/opt/mu300/bin/cpu-voltage-platform').read_text()
        source = source.replace('/lib/modules/$(uname -r)', str(self.root / 'modules'))
        source = source.replace('/opt/mu300/bin/sml-voltage-check', str(self.root / 'checker'))
        source = source.replace('/usr/libexec/unisoc-modem/cpu-voltage', str(self.root / 'helper'))
        source = source.replace('/sys/', str(self.root / 'sys') + '/')
        source = source.replace('/run', str(self.root / 'run'))
        source = source.replace('part=/dev/', 'part=' + str(self.root / 'dev') + '/')
        # Synthetic firmware inputs are regular files; production requires block devices.
        source = source.replace('[ -b "$part" ]', '[ -f "$part" ]')
        self.executable('loader', source)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def executable(self, name, source):
        self.write(name, source)
        (self.root / name).chmod(0o755)

    def run_loader(self, action='load', **env):
        return subprocess.run([str(self.root / 'loader'), action], capture_output=True, text=True,
            env={**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                 'FIXTURE': str(self.root), **env})

    def log(self):
        path = self.root / 'log'
        return path.read_text().splitlines() if path.exists() else []

    def test_nonzero_profile_verifies_both_slots_before_single_initialization(self):
        result = self.run_loader(OFFSETS='-3125,6250')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.log()
        self.assertIn('--domains 2', lines[0])
        self.assertTrue(lines[0].endswith('/dev/part_a'))
        self.assertTrue(lines[1].endswith('/dev/part_b'))
        self.assertEqual(lines[2], 'sync')
        self.assertIn('voltage_offset_uv=-3125,6250', lines[3])
        self.assertTrue(lines[4].endswith('u30pro-sprd-scufreq.ko'))
        self.assertEqual(self.run_loader().returncode, 0)
        self.assertEqual(self.log(), lines, 'must not repeat firmware initialization')

    def test_unproven_firmware_uses_factory_offsets(self):
        result = self.run_loader(OFFSETS='-3125,6250', BAD_SML='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        cpu = [line for line in self.log() if 'voltage_offset_uv=' in line]
        self.assertEqual(len(cpu), 1)
        self.assertTrue(cpu[0].endswith('voltage_offset_uv=0,0'))

    def test_missing_companion_does_not_partially_initialize_cpu(self):
        (self.root / 'modules/u30pro-sprd-scufreq.ko').unlink()
        result = self.run_loader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(line.startswith('insmod') for line in self.log()))

    def test_old_builtin_driver_does_not_initialize_again(self):
        self.write('sys/devices/system/cpu/cpufreq/policy0/scaling_driver', 'sprd-cpufreq-v2\n')
        self.assertEqual(self.run_loader().returncode, 0)
        self.assertEqual(self.log(), [])

    def test_failed_policy_read_loads_factory_offsets(self):
        result = self.run_loader(OFFSETS='-3125,6250', HELPER_ERROR='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.log()[0].endswith('voltage_offset_uv=0,0'))

    def test_ambiguous_partition_or_wrong_board_cannot_enable_offsets(self):
        self.write('sys/class/block/duplicate/uevent', 'PARTNAME=sml_a\n')
        self.assertNotEqual(self.run_loader('check').returncode, 0)
        self.write('sys/firmware/devicetree/base/compatible', 'sprd,ums9620\0')
        self.assertNotEqual(self.run_loader('check').returncode, 0)
        self.assertEqual(self.log(), [])


if __name__ == '__main__':
    unittest.main()
