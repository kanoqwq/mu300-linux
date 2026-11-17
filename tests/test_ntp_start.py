"""Ordered startup and stock sysntpd integration; no clock or network changes."""
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from helpers import TOP, ShellTest

STOCK = ' '.join(f'{i}.openwrt.pool.ntp.org' for i in range(4))
NUMERIC = '162.159.200.1 162.159.200.123 2606:4700:f1::1 2606:4700:f1::123'
WRAPPER = TOP / 'openwrt/overlay/opt/mu300/bin/mu300-ntpd'


class NtpStartup(ShellTest):
    def setUp(self):
        super().setUp()
        busybox = shutil.which('busybox')
        if not busybox:
            self.skipTest('native BusyBox required for real timeout')
        self.stub('ntpd', '''printf '<%s>' "$@" >> "$STUBLOG/ntp"
printf '\\n' >> "$STUBLOG/ntp"
for arg in "$@"; do
    if [ "$arg" = -q ]; then
        printf '%s' "$$" > "$STUBLOG/probe-pid"
        [ "$PROBE_HANG" != 1 ] || exec sleep 30
        exit "$PROBE_RESULT"
    fi
done
''')
        self.wrapper = self.tmp / 'wrapper'
        self.wrapper.write_text(WRAPPER.read_text().replace('/usr/sbin/ntpd', str(self.stubs / 'ntpd'))
                                .replace('/opt/mu300/bin/busybox', busybox))

    def start(self, shell, peers=STOCK, result=0, hang=0):
        (self.tmp / 'ntp').unlink(missing_ok=True)
        p = self.script(shell, self.wrapper, '-n', '-N', '-S', 'hook with spaces', '-l', '-I', 'br-lan',
                        MU300_NTP_SERVERS=peers, PROBE_RESULT=result, PROBE_HANG=hang)
        self.assertEqual(p.returncode, 0, p.stderr)
        return (self.tmp / 'ntp').read_text().splitlines()

    def test_success_uses_numeric_peers_and_preserves_service_options(self):
        for shell in self.each_shell():
            calls = self.start(shell)
            self.assertEqual(len(calls), 2)
            self.assertIn('<-q>', calls[0])
            self.assertNotIn('<-l>', calls[0])
            self.assertIn('<-S><hook with spaces><-l><-I><br-lan>', calls[1])
            self.assertNotIn('<-q>', calls[1])
            self.assertNotIn('pool.ntp.org', ''.join(calls))
            for ip in NUMERIC.split():
                self.assertIn(f'<-p><{ip}>', calls[1])

    def test_failure_uses_original_dns_peers(self):
        for shell in self.each_shell():
            for rc in (1, 124, 143):
                calls = self.start(shell, result=rc)
                self.assertEqual(len(calls), 2)
                for peer in STOCK.split():
                    self.assertIn(f'<-p><{peer}>', calls[1])
                self.assertNotIn('162.159.', calls[1])

    def test_custom_empty_and_dhcp_peer_lists_skip_bootstrap(self):
        for shell in self.each_shell():
            for peers in ('', 'time.example.net', '192.0.2.123', STOCK + ' 192.0.2.42'):
                calls = self.start(shell, peers)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('<-q>', calls[0])
                for peer in peers.split():
                    self.assertIn(f'<-p><{peer}>', calls[0])

    def test_actual_timeout_reaches_dns(self):
        # Only shorten the test deadline; production uses 20 seconds.
        self.wrapper.write_text(self.wrapper.read_text().replace('timeout -s TERM 20', 'timeout -s TERM 1'))
        for shell in self.each_shell():
            started = time.monotonic()
            calls = self.start(shell, hang=1)
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(len(calls), 2)
            self.assertIn('<-p><0.openwrt.pool.ntp.org>', calls[-1])

    def test_stopping_service_stops_probe_without_starting_dns(self):
        for shell in self.each_shell():
            (self.tmp / 'ntp').unlink(missing_ok=True)
            pidfile = self.tmp / 'probe-pid'
            pidfile.unlink(missing_ok=True)
            proc = subprocess.Popen(shell + [str(self.wrapper), '-n'],
                                    env=self.env(MU300_NTP_SERVERS=STOCK, PROBE_HANG=1, PROBE_RESULT=0),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pidfile.exists())
                child = int(pidfile.read_text())
                proc.send_signal(signal.SIGTERM)
                out, err = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, out + err)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
                self.assertEqual(len((self.tmp / 'ntp').read_text().splitlines()), 1)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=5)


@unittest.skipUnless(os.environ.get('MU300_TEST_OPENWRT_IMAGE'), 'existing OpenWrt base image required')
class StockIntegration(unittest.TestCase):
    def test_stock_service_keeps_policy_and_mounts_dependencies(self):
        result = subprocess.run([
            'docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
            '--platform', 'linux/arm64', '--entrypoint', '/bin/sh',
            os.environ['MU300_TEST_OPENWRT_IMAGE'], '-c', 'cat /etc/init.d/sysntpd',
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        stage = runpy.run_path(str(TOP / 'tools/stage-ntp.py'))['stage']
        with tempfile.TemporaryDirectory(prefix='mu300-ntp-stage-') as tmp:
            root = Path(tmp)
            service = root / 'etc/init.d/sysntpd'
            service.parent.mkdir(parents=True)
            service.write_text(result.stdout)
            binary = root / 'opt/mu300/bin/busybox'
            binary.parent.mkdir(parents=True)
            binary.touch()
            wrapper = binary.with_name('mu300-ntpd')
            wrapper.write_text(WRAPPER.read_text())
            config = root / 'etc/config/system'
            config.parent.mkdir()
            config.write_text('user configuration must stay unchanged\n')
            stage(root)
            patched = service.read_text()
            for required in (
                'procd_set_param env "MU300_NTP_SERVERS=$server"',
                'procd_add_jail_mount /usr/sbin/ntpd /opt/mu300/bin/busybox',
                '[ $enabled = 0 ] && return', 'get_dhcp_ntp_servers "$dhcp_interface"',
                'procd_set_param capabilities /etc/capabilities/ntpd.json',
                'procd_set_param user ntp',
            ):
                self.assertIn(required, patched)
            self.assertEqual(config.read_text(), 'user configuration must stay unchanged\n')
            self.assertTrue(wrapper.stat().st_mode & 0o111)
            subprocess.run(['sh', '-n', str(service)], check=True)
            stage(root)
            self.assertEqual(service.read_text(), patched)
            corrupted = patched.replace('PROG=/opt/mu300/bin/mu300-ntpd', 'PROG=/other/service')
            service.write_text(corrupted)
            with self.assertRaises(ValueError):
                stage(root)
            self.assertEqual(service.read_text(), corrupted)


if __name__ == '__main__':
    unittest.main()
