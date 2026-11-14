"""Pre-modem routing fixtures; ordinary files, not GPIO/baseband proof."""
import errno
import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import BIN, TOP, ShellTest

HELPER = BIN / 'mu300-v50-sim-preboot'
WRITER = BIN / 'mu300-v50-selector-write.py'
LIB = TOP / 'rootfs/overlay/opt/mu300/lib/v50-card.sh'
VENDOR = (BIN / 'android-vendor-start').read_text()
HOOK_START = VENDOR.index('# Some V50 board-2 units')
HOOK_END = VENDOR.index('\nfi', HOOK_START) + len('\nfi')
HOOK = VENDOR[HOOK_START:HOOK_END]


class PrebootRouting(ShellTest):
    def setUp(self):
        super().setUp()
        if not shutil.which('busybox'):
            self.skipTest('device BusyBox timeout required for parent-process semantics')

    def fixture(self, *, saved=0, data=0, chip=1, psim=1, enabled='1', board=2, fault=None):
        self.case = self.tmp / ('case-' + str(len(list(self.tmp.glob('case-*')))))
        for name in ('bin', 'proc/self', 'proc/sys/kernel/random', 'sysroot/proc/driver',
                     'run', 'hw', 'state', 'identity/lock-state.d', 'cache'):
            (self.case / name).mkdir(parents=True, exist_ok=True)
        self.run = self.case / 'run'
        self.state = self.case / 'state'
        self.hw = self.case / 'hw'
        values = {'saved': saved, 'data': data, 'enabled': enabled,
                  'sysroot/proc/driver/board_id': board, 'hw/psim_select': psim,
                  'hw/chip_select': chip, 'proc/uptime': '8.0 0.0',
                  'proc/sys/kernel/random/boot_id': 'boot-a', 'proc/mounts': '',
                  'proc/self/status': '', 'identity/sim.json': 'old-slot-0',
                  'identity/lock-state.d/sim1.json': 'peer-identity',
                  'identity/lock-state.d/nr': 'keep-lock-policy', 'cache/sig.json': 'stale'}
        for name, value in values.items():
            (self.case / name).write_text(str(value) + '\n')
        self.stub('uci', '''case "$*" in
  '-q get unisoc_modem.main.v50_card') cat "$CASE/saved";;
  '-q get unisoc_modem.main.data_sim') cat "$CASE/data";;
  '-q get unisoc_modem.main.v50_preboot_sim1') cat "$CASE/enabled";;
  '-q get unisoc_modem.main.state_dir') echo "$CASE/identity/lock-state.d";;
  *) echo unexpected-uci >> "$CASE/forbidden"; exit 1;;
esac''')
        self.stub('pidof', '[ -e "$CASE/modem-running" ]')
        self.stub('id', 'printf "0\\n"')
        self.stub('sync', ':')
        self.stub('mu300-device', 'echo "${TEST_MODEL:-v50}"')
        shutil.copyfile(self.stubs / 'mu300-device', self.case / 'bin/mu300-device')
        (self.case / 'bin/mu300-device').chmod(0o755)
        # BusyBox may prefer built-in applets. Never call real pidof/id/sync.
        shims = '\n'.join(n + '() { "$STUBLOG/stubs/' + n + '" "$@"; }'
                          for n in ('id', 'pidof', 'sync'))
        source = HELPER.read_text().replace('#!/bin/sh\n', '#!/bin/sh\n' + shims + '\n', 1)
        if fault:
            start = source.index('write_psim() {')
            end = source.index('\nif [ "$before_psim" = 0 ]', start)
            bodies = {
                'error': ': > "$STATE/write-error.txt"; return 1',
                'mismatch': ': > "$STATE/write-error.txt"; return 0',
                'changed_error': 'printf "%s\\n" "$1" > "$HW/psim_select"; : > "$STATE/write-error.txt"; [ "$1" != 0 ]',
                'timeout': 'sleep 5',
            }
            source = source[:start] + 'write_psim() { ' + bodies[fault] + '; }\n' + source[end:]
        self.helper = self.case / 'bin/mu300-v50-sim-preboot'
        self.helper.write_text(source)
        self.helper.chmod(0o755)
        # Fixture-only copy: permit regular-file tests as an unprivileged user.
        # Production root and real /proc parent guards are left intact in source.
        writer = self.case / 'bin/mu300-v50-selector-write.py'
        writer.write_text(WRITER.read_text().replace('os.geteuid()', '0'))
        hook = HOOK.replace('/opt/mu300/bin/', str(self.case / 'bin') + '/')
        hook = hook.replace('/opt/mu300/busybox-bin/timeout -s KILL 5', 'busybox timeout -s KILL 1')
        hook = hook.replace(str(self.helper) + ' apply', '${TEST_SHELL} ' + str(self.helper) + ' apply')
        hook = hook.replace('/var/log/mu300-v50-sim-preboot.log', str(self.case / 'launcher.log'))
        self.parent = self.case / 'bin/android-vendor-start'
        self.parent.write_text('''#!/bin/sh
mkdir -p "$MU300_V50_PROC/$$"
printf '/bin/sh\\000%s\\000' "$MU300_BIN/android-vendor-start" > "$MU300_V50_PROC/$$/cmdline"
''' + hook + '\nprintf native_continued > "$CASE/native-continued"\n')
        self.extra = dict(CASE=self.case, MU300_BIN=self.case / 'bin',
                          MU300_SYSROOT=self.case / 'sysroot', MU300_V50_PROC=self.case / 'proc',
                          MU300_V50_RUN=self.run, MU300_V50_MUX=self.hw,
                          MU300_V50_PREBOOT_STATE=self.state, MU300_V50_CARD_LIB=LIB,
                          MU300_V50_SELECTOR_WRITER=writer, MU300_DASH_CACHE_ROOT=self.case / 'cache')

    def boot(self, shell, **extra):
        r = self.script(shell, self.parent, **dict(self.extra, TEST_SHELL=' '.join(shell), **extra))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.case / 'native-continued').read_text(), 'native_continued')
        self.assertFalse((self.case / 'forbidden').exists(), 'UCI state was mutated')
        self.assertEqual((self.case / 'identity/lock-state.d/sim1.json').read_text(), 'peer-identity\n')
        self.assertEqual((self.case / 'identity/lock-state.d/nr').read_text(), 'keep-lock-policy\n')

    def new_boot(self):
        shutil.rmtree(self.run)
        self.run.mkdir()
        (self.case / 'proc/sys/kernel/random/boot_id').write_text('boot-b\n')

    def test_both_internal_choices_change_only_psim_and_refresh_only_slot0(self):
        for shell in self.each_shell():
            for saved in (0, 1):
                for chip in (0, 1):
                    self.fixture(saved=saved, data=saved, chip=chip)
                    self.boot(shell)
                    self.assertEqual((self.hw / 'psim_select').read_text().strip(), '0')
                    self.assertEqual((self.hw / 'chip_select').read_text().strip(), str(chip))
                    self.assertEqual((self.state / 'status').read_text().strip(), 'selector_verified_waiting_for_modem')
                    self.assertFalse((self.state / 'inflight').exists())
                    self.assertFalse((self.case / 'identity/sim.json').exists())
                    self.assertEqual((self.state / 'sim0-identity.before.json').read_text(), 'old-slot-0\n')
                    self.assertFalse((self.case / 'cache/sig.json').exists())
                    self.assertEqual((self.case / 'saved').read_text().strip(), str(saved))
                    self.assertEqual((self.case / 'data').read_text().strip(), str(saved))

    def test_default_off_external_other_board_and_inconsistent_choices_do_not_write(self):
        for shell in self.each_shell():
            for kwargs in ({'enabled': ''}, {'enabled': '0'}, {'enabled': 'unexpected'},
                           {'saved': 3, 'data': 0}, {'saved': 1, 'data': 0}, {'board': 3}):
                self.fixture(**kwargs)
                self.boot(shell)
                self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')
                self.assertFalse((self.state / 'inflight').exists())
                self.assertFalse((self.run / 'mu300-v50-sim-preboot-claim').exists())
            self.fixture()
            self.boot(shell, TEST_MODEL='u30air')
            self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')

    def test_already_internal_does_not_write_or_discard_identity(self):
        for shell in self.each_shell():
            self.fixture(psim=0)
            self.boot(shell)
            self.assertEqual((self.state / 'status').read_text().strip(), 'already_internal')
            self.assertFalse((self.state / 'write-detail.json').exists())
            self.assertTrue((self.case / 'identity/sim.json').exists())

    def test_live_invocation_and_live_modem_are_rejected(self):
        for shell in self.each_shell():
            self.fixture()
            r = self.script(shell, self.helper, 'apply', **self.extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('boot_caller_required', r.stderr)
            for marker in ('run/mu300-ril-handshake', 'run/mu300-at/cmd', 'modem-running'):
                self.fixture()
                p = self.case / marker
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
                self.boot(shell)
                self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')
                self.assertIn('modem_already_started', (self.case / 'launcher.log').read_text())

    def test_one_attempt_per_boot_and_success_rearms_on_next_boot(self):
        for shell in self.each_shell():
            self.fixture()
            self.boot(shell)
            (self.hw / 'psim_select').write_text('1\n')
            self.boot(shell)
            self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')
            self.assertIn('already_attempted_this_boot', (self.case / 'launcher.log').read_text())
            self.new_boot()
            self.boot(shell)
            self.assertEqual((self.hw / 'psim_select').read_text().strip(), '0')
            self.assertTrue((self.state / 'previous-boot.log').exists())

    def test_failure_rolls_back_only_changed_psim_latches_and_preserves_native_start(self):
        for shell in self.each_shell():
            for fault in ('error', 'mismatch', 'changed_error', 'timeout'):
                self.fixture(fault=fault)
                self.boot(shell)
                self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')
                self.assertEqual((self.hw / 'chip_select').read_text().strip(), '1')
                self.assertTrue((self.state / 'inflight').exists())
                self.new_boot()
                self.boot(shell)
                self.assertIn('suspended_after_failed_or_incomplete_attempt', (self.case / 'launcher.log').read_text())
                self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')

    def test_plan_is_read_only_and_reports_suspension(self):
        for shell in self.each_shell():
            self.fixture()
            (self.state / 'inflight').touch()
            r = self.script(shell, self.helper, 'plan', **self.extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            result = json.loads(r.stdout)
            self.assertTrue(result['suspended'])
            self.assertFalse(result['writes_chip'])
            self.assertFalse((self.run / 'mu300-v50-sim-preboot-claim').exists())
            self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')

    def test_missing_helper_does_not_abort_vendor_start(self):
        for shell in self.each_shell():
            self.fixture()
            self.helper.unlink()
            self.boot(shell)
            self.assertEqual((self.hw / 'psim_select').read_text().strip(), '1')

    def test_hook_precedes_modem_and_follows_vendor_permissions(self):
        self.assertLess(VENDOR.index('mark vendor-perms'), HOOK_START)
        self.assertLess(HOOK_END, VENDOR.index('modem_control', HOOK_END))
        self.assertIn('timeout -s KILL 5', HOOK)


class SelectorWriter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('v50_selector', WRITER)
        cls.writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.writer)

    def test_success_requires_full_write_three_reads_and_unchanged_peer(self):
        good = {'errno': None, 'bytes_written': 1}
        samples = [{'psim': 0, 'chip': 1}] * 3
        self.assertTrue(self.writer.verify_result(good, samples, 0, 1)['accepted'])
        for bad in ({'errno': errno.EIO, 'bytes_written': None},
                    {'errno': None, 'bytes_written': 0}):
            self.assertFalse(self.writer.verify_result(bad, samples, 0, 1)['accepted'])
        for bad_samples in (samples[:2], samples + samples[:1],
                            [{'psim': 0, 'chip': 0}] * 3, [{'read_error': 'test'}] * 3):
            self.assertFalse(self.writer.verify_result(good, bad_samples, 0, 1)['accepted'])

    def test_one_unbuffered_byte_and_no_truncation(self):
        with patch.object(self.writer.os, 'open', return_value=42) as opening, \
             patch.object(self.writer.os, 'write', return_value=1) as writing, \
             patch.object(self.writer.os, 'close') as closing:
            result = self.writer.write_once(Path('/fixture'), 0)
        opening.assert_called_once_with('/fixture/psim_select', os.O_WRONLY | os.O_CLOEXEC)
        writing.assert_called_once_with(42, b'0')
        closing.assert_called_once_with(42)
        self.assertIsNone(result['errno'])

    def test_open_write_and_close_errors_are_recorded_not_suppressed(self):
        for phase in ('open', 'write', 'close'):
            with self.subTest(phase=phase), \
                 patch.object(self.writer.os, 'open', return_value=42) as opening, \
                 patch.object(self.writer.os, 'write', return_value=1) as writing, \
                 patch.object(self.writer.os, 'close') as closing:
                {'open': opening, 'write': writing, 'close': closing}[phase].side_effect = OSError(errno.EIO, 'fixture')
                result = self.writer.write_once(Path('/fixture'), 0)
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['errno'], errno.EIO)
                self.assertFalse(self.writer.verify_result(result, [{'psim': 0, 'chip': 1}] * 3, 0, 1)['accepted'])
