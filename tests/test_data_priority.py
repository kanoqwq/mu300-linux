"""Data preferences run independently of dialing, including failing/slow AT."""
import os
import time

from helpers import ShellTest, TOP, BIN


class DataPriority(ShellTest):
    def setUp(self):
        super().setUp()
        self.source = (BIN / 'mobile-data').read_text()
        self.mutex = self.tmp / 'radio-mutex'
        self.pending = self.tmp / 'early-pending'
        self.code = self.source[self.source.index('data_priority() {'):
                                self.source.index('\n# 取 PDP 地址')]
        self.code = self.code.replace('/run/mu300-radio-on.owner', str(self.mutex))
        self.code = self.code.replace('/run/mu300-data-priority-', str(self.tmp / 'priority-'))
        self.code = self.code.replace('/run/unisoc-modem-early-hook-pending', str(self.pending))
        self.code = self.code.replace('/opt/mu300/bin/mu300-at', str(self.stubs / 'client'))
        self.stub('client', r'''
cmd=$3
printf '%s|%s|%s|%s\n' "$cmd" "$MU300_AT_DIR" "$MU300_AT_DAEMON_ONLY" "$MU300_AT_LOCK_WAIT" >> "$STUBLOG/commands"
case "$cmd" in *CEUS=1)
    if [ "${HOLD:-0}" = 1 ]; then
        touch "$STUBLOG/started"
        n=0
        while [ ! -e "$STUBLOG/release" ] && [ "$n" -lt 100 ]; do
            sleep 0.05; n=$((n + 1))
        done
    fi ;;
esac
fail=0
case "$cmd" in *"${FAIL_CMD:-never-match}"*) fail=1 ;; esac
[ "${FAIL_CMD:-}" != all ] || fail=1
if [ -n "${FAIL_FIRST:-}" ]; then
    count=$(grep -Fc "$cmd|" "$STUBLOG/commands")
    [ "$count" -le "$FAIL_FIRST" ] || fail=0
fi
echo FINISH >> "$STUBLOG/finished"
if [ "$fail" = 1 ]; then
    case ${FAIL_MODE:-error} in
        error) printf 'ERROR\n' ;;
        cme) printf '+CME ERROR: 3\n' ;;
        mixed) printf 'OK\n+CMS ERROR: 500\n' ;;
        stale) printf 'OK\npartial reply\n' ;;
        empty) : ;;
        transport) printf 'OK\n'; exit 1 ;;
    esac
else
    printf '\r\nOK\r\n'
fi
''')
        self.fixture = self.tmp / 'mobile-data-fixture'
        self.fixture.write_text('#!/bin/bash\nset -eu\n' + self.code + r'''
radio_say() { printf '%s\n' "$*" >> "$STUBLOG/radio.log"; }
radio_on_locked() { echo RADIO >> "$STUBLOG/commands"; return "${RADIO_RC:-0}"; }
case "$1" in
    data-priority) data_priority ;;
    start) radio_on; echo PDP >> "$STUBLOG/commands"; echo DIAL_RETURNED ;;
esac
''')
        self.fixture.chmod(0o755)

    def run_fixture(self, mode, slot='0', **env):
        return self.script(['bash'], self.fixture, mode, MU300_SIM_SLOT=slot,
                           MU300_SIM_SHARED='/run/mu300-at' + ('4' if slot == '1' else ''), **env)

    def commands(self):
        p = self.tmp / 'commands'
        return p.read_text().splitlines() if p.exists() else []

    def wait_until(self, predicate):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('background worker did not reach expected state')

    def test_order_and_shared_channel_for_both_sims_without_persistent_skip(self):
        for slot in ('0', '1'):
            (self.tmp / 'commands').write_text('')
            for _ in range(2):
                r = self.run_fixture('data-priority', slot)
                self.assertEqual(r.returncode, 0, r.stderr)
            lane = '/run/mu300-at' + ('4' if slot == '1' else '')
            expected = [f'AT+SPACTCARD={slot};{cmd}|{lane}|1|1'
                        for cmd in ('+CEUS=1', '+CEMODE=2')]
            self.assertEqual(self.commands(), expected * 2)
            self.assertFalse((self.tmp / f'priority-{slot}.lock').exists())

    def test_every_failure_still_attempts_second_command_and_returns_success(self):
        for cmd in ('CEUS=1', 'CEMODE=2', 'all'):
            for mode in ('error', 'cme', 'mixed', 'stale', 'empty', 'transport'):
                (self.tmp / 'commands').write_text('')
                r = self.run_fixture('data-priority', FAIL_CMD=cmd, FAIL_MODE=mode)
                self.assertEqual(r.returncode, 0, (cmd, mode, r.stderr))
                expected = []
                for at in ('CEUS=1', 'CEMODE=2'):
                    expected += [at] * (4 if cmd in (at, 'all') else 1)
                self.assertEqual([line.split(';')[1].split('|')[0][1:]
                                  for line in self.commands()], expected)
                self.assertIn('dial unaffected', (self.tmp / 'radio.log').read_text())
                self.assertIn('exhausted:', (self.tmp / 'radio.log').read_text())
                self.assertNotIn('data priority accepted:', (self.tmp / 'radio.log').read_text())

    def test_retries_stop_at_ok_including_success_on_last_retry(self):
        for slot in ('0', '1'):
            for failures in range(4):
                for mode in ('error', 'empty', 'transport'):
                    (self.tmp / 'commands').write_text('')
                    (self.tmp / 'radio.log').write_text('')
                    r = self.run_fixture('data-priority', slot, FAIL_CMD='all',
                                         FAIL_FIRST=str(failures), FAIL_MODE=mode)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    expected = []
                    for cmd in ('CEUS=1', 'CEMODE=2'):
                        expected += [cmd] * (failures + 1)
                    self.assertEqual([line.split(';')[1].split('|')[0][1:]
                                      for line in self.commands()], expected)
                    log = (self.tmp / 'radio.log').read_text()
                    self.assertIn('data priority accepted:', log)
                    self.assertNotIn('exhausted:', log)
                    self.assertEqual(log.count('data priority confirmed:'), 2)

    def test_dial_returns_while_at_still_blocked_then_both_fail_without_affecting_dial(self):
        self.pending.touch()
        lock = self.tmp / 'priority-0.lock'
        try:
            r = self.run_fixture('start', HOLD=1, FAIL_CMD='all', FAIL_MODE='transport')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('DIAL_RETURNED', r.stdout)
            self.assertIn('PDP', self.commands())
            self.wait_until(lambda: (self.tmp / 'started').exists())
            self.assertTrue(lock.exists())
            self.assertFalse((self.tmp / 'finished').exists())
            self.assertFalse(self.mutex.exists())
            self.assertFalse(self.pending.exists())
        finally:
            (self.tmp / 'release').touch()
            self.wait_until(lambda: not lock.exists())
        self.assertEqual(sum('AT+SPACTCARD' in cmd for cmd in self.commands()), 8)

    def test_concurrent_worker_skipped_and_dead_worker_reclaimed(self):
        lock = self.tmp / 'priority-0.lock'
        lock.mkdir()
        (lock / 'pid').write_text(str(os.getpid()))
        self.assertEqual(self.run_fixture('data-priority').returncode, 0)
        self.assertEqual(self.commands(), [])
        (lock / 'pid').write_text('99999999')
        self.assertEqual(self.run_fixture('data-priority').returncode, 0)
        self.assertEqual(len(self.commands()), 2)
        self.assertFalse(lock.exists())

    def test_scheduling_failure_does_not_gate_dial(self):
        self.stub('setsid', 'exit 1')
        r = self.run_fixture('start')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('PDP', self.commands())

    def test_actual_radio_failure_still_releases_mutex(self):
        for rc in ('1', '4'):
            (self.tmp / 'commands').write_text('')
            self.pending.touch()
            r = self.run_fixture('start', RADIO_RC=rc)
            self.assertEqual(r.returncode, int(rc), r.stderr)
            self.assertEqual(self.commands(), ['RADIO'])
            self.assertFalse(self.mutex.exists())
            self.assertTrue(self.pending.exists())

    def test_daemon_only_client_never_opens_tty_when_daemon_absent(self):
        r = self.script(['bash'], BIN / 'mu300-at', '-t', '3', 'AT',
                        MU300_AT_DIR=self.tmp / 'absent', MU300_AT_DEV='/dev/null',
                        MU300_AT_DAEMON_ONLY='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('direct tty fallback disabled', r.stderr)
        self.assertNotIn('using /dev/null directly', r.stderr)

    def test_shared_build_inputs_and_no_new_startup_sleep(self):
        helper = self.code[:self.code.index('\nqueue_data_priority() {')]
        self.assertNotIn('sleep ', helper)
        self.assertNotIn('SFUN=', helper)
        self.assertIn('$TOP/rootfs/overlay/opt/mu300', (TOP / 'openwrt/build-rootfs.sh').read_text())
        self.assertIn('cp -a /w/overlay/. $R/', (TOP / 'rootfs/assemble.sh').read_text())
        for path in ('tools/build-openwrt-tf-magisk.sh', 'tools/make-release.sh'):
            self.assertIn('./opt/mu300/bin/mobile-data ', (TOP / path).read_text())

    def test_manual_reset_reapplies_without_redial_and_preserves_failures(self):
        code = self.source[self.source.index('sim_reset() {'):
                           self.source.index('\n# The modem firmware occasionally resets')]
        code = code.replace('/run/mu300/sim-managed', str(self.tmp / 'managed'))
        self.stub('allowed', 'exit "${DENY_RADIO:-0}"')
        self.stub('sleep', ':')
        fixture = self.tmp / 'reset-fixture'
        fixture.write_text('#!/bin/bash\nset -euo pipefail\n' + code + r'''
tty_setup() { :; }
at() {
    echo "$1" >> "$STUBLOG/reset-commands"
    [ "${AT_FAIL:-}" != "$1" ] || return 9
    case "$1" in *CPIN*) printf '+CPIN: READY\nOK\n';; *) echo OK;; esac
}
queue_data_priority() {
    echo PRIORITY >> "$STUBLOG/reset-commands"
    return "${QUEUE_RC:-0}"
}
sim_reset
''')
        expected = ['AT+SFUN=5', 'AT+SFUN=3', 'AT+SFUN=2', 'AT+SFUN=4',
                    'AT+CPIN?', 'AT+CPIN?', 'PRIORITY']
        for slot in ('0', '1'):
            for shell in (['bash'],):  # mobile-data requires bash/pipefail
                log = self.tmp / 'reset-commands'
                log.write_text('')
                env = dict(MU300_SIM_SLOT=slot, MU300_RADIO_ALLOWED=self.stubs / 'allowed')
                # Every reset schedules again; there is no permanent done flag.
                for _ in range(2):
                    r = self.script(shell, fixture, **env, QUEUE_RC=1)
                    self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(log.read_text().splitlines(), expected * 2)
                log.write_text('')
                for command in expected[:4]:
                    log.write_text('')
                    r = self.script(shell, fixture, **env, AT_FAIL=command)
                    self.assertEqual(r.returncode, 9, r.stderr)
                    self.assertEqual(log.read_text().splitlines(), expected[:4])
                log.write_text('')
                r = self.script(shell, fixture, **env, DENY_RADIO=1)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(log.read_text(), '')
                (self.tmp / 'managed').touch()
                r = self.script(shell, fixture, **env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(log.read_text().splitlines(), expected)
                (self.tmp / 'managed').unlink()


class AtomicModemLocks(ShellTest):
    def test_concurrent_stale_owner_recovery_never_enters_twice(self):
        import subprocess
        source=(BIN/'mobile-data').read_text()
        code=source[source.index('RADIOLOCK='):source.index('radio_locked() {')]
        code=code.replace('/opt/mu300/bin/busybox sleep 0.2 2>/dev/null || sleep 1','sleep 0.05')
        script=self.tmp/'mobile-data-lock-fixture'
        script.write_text('#!/bin/bash\nset -eu\n'+code+'\nlock_take "$TEST_LOCK" 8 || exit 91\ntrap \'lock_drop "$TEST_LOCK"\' EXIT\nmkdir "$TEST_CRITICAL" || exit 92\nsleep 0.04\nrmdir "$TEST_CRITICAL"\nlock_drop "$TEST_LOCK"\n')
        lock=self.tmp/'owner';lock.symlink_to('99999999')
        procs=[subprocess.Popen(['bash',str(script)],env=self.env(TEST_LOCK=lock,TEST_CRITICAL=self.tmp/'critical'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(8)]
        results=[(p.communicate(timeout=12),p.returncode) for p in procs]
        self.assertTrue(all(rc==0 for _,rc in results),results)
        self.assertFalse(lock.is_symlink())
        self.assertFalse((self.tmp/'critical').exists())
