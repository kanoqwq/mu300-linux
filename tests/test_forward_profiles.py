"""Forwarding superset: shell portability, packaging, and opt-in real OpenWrt fixtures.

Run the real jshn/ucode suite with MU300_TEST_OPENWRT_IMAGE set to an image
containing the plugin dependencies. It never contacts any external recipient.
"""
import os
import shutil
import subprocess
import unittest
from helpers import ShellTest, TOP

LIB = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem'


class ForwardProfiles(ShellTest):
    def test_template_helper_is_required_by_both_release_paths(self):
        for name in ('build-openwrt-tf-magisk.sh', 'make-release.sh'):
            text = (TOP / 'tools' / name).read_text()
            for path in ('sms-forward', 'forward-template.uc', 'call-notify', 'call-notify-events.uc'):
                self.assertIn('./usr/libexec/unisoc-modem/' + path + ' ', text)

    def test_mode_defaults_and_invalid_mode_fail_closed(self):
        source = (LIB / 'sms-forward').read_text()
        code = source[source.index('read_mode() {'):source.index('select_profile() {')]
        for shell in self.each_shell():
            base = self.tmp / '_'.join(shell).replace('/', '_')
            base.mkdir(exist_ok=True)
            r = self.sh(shell, code + '\nread_mode && echo "$forward_mode"', BASE=base)
            self.assertEqual(r.stdout.strip(), 'shared')
            for mode in ('shared', 'per_sim', 'bad'):
                (base / 'mode').write_text(mode + '\n')
                r = self.sh(shell, code + '\nread_mode && echo "$forward_mode"', BASE=base)
                self.assertEqual(r.returncode == 0, mode != 'bad')

    @unittest.skipUnless(os.environ.get('MU300_TEST_OPENWRT_IMAGE') and shutil.which('docker'),
                         'Set MU300_TEST_OPENWRT_IMAGE for real OpenWrt integration')
    def test_real_openwrt_isolated_integration(self):
        run = subprocess.run([
            'docker', 'run', '--rm', '--platform', 'linux/arm64', '-v', f'{TOP}:/src:ro',
            os.environ['MU300_TEST_OPENWRT_IMAGE'], 'sh', '/src/tests/forward_profiles_fixture.sh',
            '/src/openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/sms-forward',
        ], capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('PASS isolated real delivery', run.stdout)
