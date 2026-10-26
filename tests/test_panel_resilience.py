"""One stuck AT command must not wedge the control panel (the v2026.10.18 report: a page's AT command hung, the whole
panel went blank, even across a reboot, and came back by itself later).

* `mu300-at`'s lock wait is seconds on the clock. It counted 0.1 s naps, and OpenWrt's /bin/sleep refuses a fraction,
  so each nap was a whole second: the panel's 5 s wait was 50 s, past rpcd's 30 s, and the client went on waiting
  after its call had been killed. A holder past the deadline it wrote for itself is ended (`AtLock`).
* The dashboard's collector stops at a deadline instead of waiting out every command on every channel
  (`CellDeadline`).
* The backend (`Backend`): the lock state is never read inside a request; AT commands that take minutes or change the
  radio, the SIM or the modem's stored settings need a confirmation, and a confirmed scan runs as a background job;
  a log of the panel's calls (name, time taken, outcome, never a value) is kept across one reboot.
* The pages (`Pages`, under Node): every call on its own (never batched behind a slow one), polls back off after
  failures, the SMS watcher never stacks requests, the AT terminal asks before such commands, the dashboard says
  when its cellular readings are old.
Stubs throughout: a modem that never answers is an AT client that waits out the budget it is given."""
import json
import os
import shutil
import subprocess
import threading
import time
import unittest
from pathlib import Path

import test_luci_i18n as lui
import test_mu300dash_security as sec
from helpers import BIN, ShellTest

HAVE_PROC = Path('/proc/self/cmdline').exists()


class AtLock(ShellTest):
    """mu300-at's client lock against a fake daemon directory (a FIFO `cmd`; a thread answers like mu300-atd)."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / 'at'
        self.dir.mkdir()
        os.mkfifo(self.dir / 'cmd')
        # OpenWrt's /bin/sleep: whole seconds only, "invalid number" for a fraction
        real = shutil.which('sleep')
        self.stub('sleep', f'case $1 in *.*) echo "sleep: invalid number \'$1\'" >&2; exit 1 ;; esac\nexec {real} "$1"')
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        super().tearDown()

    def holder(self, name='mu300-at', due=None):
        """A live process holding the client lock, its pid in lock/pid (and lock/until when DUE is given)."""
        p = subprocess.Popen(['sh', '-c', 'sleep 60; :', name])
        self.procs.append(p)
        (self.dir / 'lock').mkdir()
        (self.dir / 'lock' / 'pid').write_text(f'{p.pid}\n')
        if due is not None:
            (self.dir / 'lock' / 'until').write_text(f'{due}\n')
        return p

    def daemon(self, reply='OK\r\n', delay=0.0):
        def run():
            line = ''
            while not line:
                with open(self.dir / 'cmd') as f:
                    line = f.readline().rstrip('\n')
            _t, answer, _cmd = line.split(' ', 2)
            time.sleep(delay)
            with open(answer, 'w', newline='') as a:
                a.write(reply)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th

    def at(self, shell, *args, **env):
        # MU300_BUSYBOX: none, so every nap goes through the whole-second sleep above, as on a device without it
        return self.script(shell, BIN / 'mu300-at', *args, MU300_AT_DIR=self.dir,
                           MU300_BUSYBOX=self.tmp / 'no-busybox', **env)

    def test_the_lock_wait_is_seconds_on_the_clock(self):
        # 2 s means about 2 s even where every 0.1 s nap takes a whole second (it took 20 s)
        self.holder()
        for shell in self.each_shell():
            t0 = time.monotonic()
            r = self.at(shell, '-t', '1', 'AT', MU300_AT_LOCK_WAIT='2')
            self.assertEqual((r.returncode, r.stderr.strip()), (1, 'mu300-at: busy'))
            self.assertLess(time.monotonic() - t0, 4.5)
            # (the clock counts whole seconds: 2 s from x.99 ends at x+2, a second and a bit later)
            self.assertGreaterEqual(time.monotonic() - t0, 0.9)

    def test_wait_zero_is_one_try(self):
        self.holder()
        for shell in self.each_shell():
            t0 = time.monotonic()
            r = self.at(shell, '-t', '1', 'AT', MU300_AT_LOCK_WAIT='0')
            self.assertEqual(r.stderr.strip(), 'mu300-at: busy')
            self.assertLess(time.monotonic() - t0, 2.5)

    def test_junk_in_the_wait_is_the_default_not_an_error(self):
        # a non-number falls back to the default; the lock is free here, so the command goes through at once
        for shell in self.each_shell():
            self.daemon()
            r = self.at(shell, '-t', '2', 'AT', MU300_AT_LOCK_WAIT='x')
            self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(HAVE_PROC, 'needs /proc')
    def test_a_holder_past_its_own_deadline_is_ended(self):
        # a client that wrote "done by T" and is still there after T is stuck (stopped, blocked): it is ended and the
        # lock taken, instead of every later command saying busy until a reboot
        for shell in self.each_shell():
            p = self.holder(due=int(time.time()) - 1)
            self.daemon()
            r = self.at(shell, '-t', '2', 'AT', MU300_AT_LOCK_WAIT='3')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('held the AT lock past its own deadline', r.stderr)
            self.assertIsNotNone(p.wait(timeout=5))
            self.assertFalse((self.dir / 'lock').exists())

    @unittest.skipUnless(HAVE_PROC, 'needs /proc')
    def test_a_holder_within_its_deadline_or_not_a_client_is_left_alone(self):
        for label, name, due in (('in time', 'mu300-at', int(time.time()) + 100), ('no deadline', 'mu300-at', None),
                                 ('not mu300-at', 'mobile-data', int(time.time()) - 100)):
            with self.subTest(case=label):
                p = self.holder(name=name, due=due)
                for shell in self.each_shell():
                    r = self.at(shell, '-t', '1', 'AT', MU300_AT_LOCK_WAIT='1')
                    self.assertEqual(r.stderr.strip(), 'mu300-at: busy')
                self.assertIsNone(p.poll())
                p.kill()
                p.wait()
                shutil.rmtree(self.dir / 'lock')

    def test_a_dead_holder_is_taken_over(self):
        (self.dir / 'lock').mkdir()
        (self.dir / 'lock' / 'pid').write_text('999999\n')
        for shell in self.each_shell():
            self.daemon()
            r = self.at(shell, '-t', '2', 'AT', MU300_AT_LOCK_WAIT='1')
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_lock_the_caller_holds_is_borrowed(self):
        # a client told that its caller holds the lock (MU300_AT_LOCK_HELD: mu300-sms, around a whole send) runs at
        # once under it and leaves it in place - only while lock/pid names that live process; any other value, and
        # it waits for the lock like every client
        p = self.holder(name='mu300-sms')
        for shell in self.each_shell():
            self.daemon()
            r = self.at(shell, '-t', '2', 'AT', MU300_AT_LOCK_HELD=str(p.pid), MU300_AT_LOCK_WAIT='1')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'OK'), r.stderr)
            self.assertEqual((self.dir / 'lock' / 'pid').read_text().strip(), str(p.pid))
            for wrong in ('1', str(os.getpid()), 'x', ''):
                with self.subTest(held=wrong):
                    r = self.at(shell, '-t', '1', 'AT', MU300_AT_LOCK_HELD=wrong, MU300_AT_LOCK_WAIT='1')
                    self.assertEqual((r.returncode, r.stderr.strip()), (1, 'mu300-at: busy'))
            self.assertTrue((self.dir / 'lock').exists())

    def test_the_holder_says_when_it_will_be_done(self):
        # no live daemon (no owner/pid): the budget is T + 2, so "until" is about now + T + 2 + 10
        for shell in self.each_shell():
            th = self.daemon(delay=1.0)
            p = subprocess.Popen(shell + [str(BIN / 'mu300-at'), '-t', '3', 'AT'], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=self.env(MU300_AT_DIR=self.dir))
            due = None
            for _ in range(100):
                try:
                    due = int((self.dir / 'lock' / 'until').read_text())
                    break
                except (OSError, ValueError):
                    time.sleep(0.02)
            p.wait(timeout=20)
            th.join(timeout=5)
            self.assertIsNotNone(due)
            self.assertLessEqual(abs(due - (int(time.time()) + 15)), 3)


@unittest.skipUnless(shutil.which('bash'), 'needs bash')
class AtdShell(ShellTest):
    """mu300-atd under a shell whose read -t takes whole seconds only (OpenWrt's /bin/sh: "read: invalid timeout")
    runs itself again under the static busybox's ash, which takes fractions: every drain waited a whole second, so
    every AT command took one. Stood in for by bash with an exported read that refuses a fraction."""

    def setUp(self):
        super().setUp()
        self.bb = self.tmp / 'busybox'
        self.bb.write_text('#!/bin/sh\n[ "$1" = ash ] && [ "$2" = -c ] && exit 0\n'
                           'printf "%s\\n" "$*" > "$STUBLOG/exec"\n')
        self.bb.chmod(0o755)

    def run_atd(self, whole_seconds, **env):
        code = ('read() { case "$2" in *.*) echo "read: invalid timeout" >&2; return 1 ;; esac; builtin read "$@"; }\n'
                'export -f read\n' if whole_seconds else '')
        return subprocess.run(['bash', '-c', code + f'exec bash "{BIN / "mu300-atd"}"'], capture_output=True,
                              text=True, timeout=30, env=self.env(MU300_BUSYBOX=self.bb, MU300_AT_DEV=self.tmp / 'none',
                                                                   MU300_AT_DIR=self.tmp / 'run', **env))

    def test_whole_seconds_only_runs_it_again_under_the_static_busybox(self):
        self.run_atd(True)
        self.assertEqual((self.tmp / 'exec').read_text().strip(), f'ash {BIN / "mu300-atd"}')

    def test_a_shell_that_takes_fractions_stays(self):
        if subprocess.run(['bash', '-c', 'read -t 0.1 x </dev/null'], capture_output=True, text=True).stderr:
            self.skipTest('this bash takes whole seconds only (3.2)')
        r = self.run_atd(False)
        self.assertFalse((self.tmp / 'exec').exists())
        self.assertIn('is not a character device', r.stderr)

    def test_once_only(self):
        r = self.run_atd(True, MU300_ATD_SHELL='1')
        self.assertFalse((self.tmp / 'exec').exists())
        self.assertIn('is not a character device', r.stderr)


CELL = sec.ADAPTERS / 'cell'


class CellDeadline(ShellTest):
    """The collector against a modem that never answers: the AT client waits out whatever budget it is given."""

    def setUp(self):
        super().setUp()
        self.stub('uci', 'exit 1')
        self.at = self.tmp / 'at'
        self.at.write_text('#!/bin/sh\necho "wait=$MU300_AT_LOCK_WAIT budget=$MU300_AT_BUDGET" >> "$STUBLOG/at.log"\n'
                           'sleep "${MU300_AT_BUDGET:-60}"\necho "mu300-at: no answer from the daemon" >&2\nexit 1\n')
        self.at.chmod(0o755)

    def run_cell(self, shell, mode, **env):
        d = self.tmp / 'dash'
        shutil.rmtree(d, ignore_errors=True)
        (self.tmp / 'at.log').unlink(missing_ok=True)
        t0 = time.monotonic()
        r = self.script(shell, CELL, mode, MU300_AT=self.at, MU300_DASH_DIR=d, MU300_DASH_IDENT_DIR=self.tmp / 'id',
                        MU300_DASH_POOL_DIR=self.tmp / 'pool', **env)
        return r, time.monotonic() - t0, d

    def test_a_silent_modem_ends_the_round_at_its_deadline(self):
        for shell in self.each_shell():
            for mode in ('fast', 'full'):
                with self.subTest(mode=mode):
                    r, took, d = self.run_cell(shell, mode, MU300_DASH_DEADLINE='3')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertLess(took, 6)
                    sig = json.loads((d / 'sig.json').read_text())
                    self.assertIn('AT backend did not answer', sig.get('error', ''))
                    # never a wait for anybody's lock, never a budget past the deadline
                    for line in (self.tmp / 'at.log').read_text().split('\n'):
                        if line:
                            wait, budget = (int(kv.split('=')[1]) for kv in line.split())
                            self.assertEqual(wait, 0)
                            self.assertLessEqual(budget, 3)

    def test_the_default_deadlines(self):
        # the page's live signal read has 14 s (mu300dash; a normal round is about 1 s, and about 7 s where mu300-atd
        # drains a whole second per command), a background fast round 12, a full one 25
        src = CELL.read_text()
        self.assertIn('[ "$MODE" = fast ] && MU300_DASH_DEADLINE=12 || MU300_DASH_DEADLINE=25', src)
        dash = sec.DASH.read_text()
        self.assertIn('SIGNAL_DEADLINE=14', dash)
        self.assertIn('MU300_DASH_DEADLINE=$SIGNAL_DEADLINE', dash)


class Backend(sec.Mu300Dash):
    CLASSES = {
        None: ['AT', 'AT+COPS?', 'AT+COPS=3,2', 'AT+CFUN?', 'AT+CFUN=?', 'AT+SPLBAND=0', 'AT+SPLBAND=3',
               'AT+SPFORCEFRQ=12,3', 'AT+SPFORCEFRQ=16,3', 'AT+CLCK="SC",2', 'AT+CPIN?', 'AT+SPTESTMODE?',
               'AT+SPTESTMODE=?', 'AT+CGDCONT?', 'AT+CGACT?', 'AT+SPENGMD=0,6,0', 'AT+CSQ'],
        'scan': ['AT+COPS=?', 'at+cops = ?', 'AT+SPFREQSCAN=1', 'AT+SPFREQSCAN'],
        'nv': ['AT+SPTESTMODE=3,134,0', 'AT+SPLBAND=1,0,0,0,5,0', 'AT+SPLBAND = 2,1,0,2,0', 'at+splband=01,0',
               'AT+SPFORCEFRQ=16,6,627264,393', 'AT+SPFORCEFRQ=12,0', 'AT+SP5GRAN=0', 'AT+SPENDC=2'],
        'radio': ['AT+CFUN=0', 'ATE0+CFUN=1,1', 'AT+SFUN=4', 'AT+COPS=2', 'AT+COPS=1,2,"46000"', 'ATD10086',
                  'AT+CGACT=0,1', 'AT+CGATT=0', 'AT+CGDATA="M-ETHER",1', 'AT+SPATASSERT=1'],
        'apn': ['AT+CGDCONT=1,"IP","internet"', 'AT+CGAUTH=1,1,"u","p"'],
        'sim': ['AT+CLCK="SC",1,"1234"', 'AT+CLCK="SC",0,"1234"', 'AT+CPIN="1234"', 'AT+CPWD="SC","1","2"'],
    }

    def test_commands_that_do_more_than_answer_need_a_confirmation(self):
        jobs = [(c, 'at', {'cmd': c}) for cmds in self.CLASSES.values() for c in cmds]
        want = {c: k for k, cmds in self.CLASSES.items() for c in cmds}
        for shell in self.each_shell():
            for label, r, recs, _ in self.calls_parallel(shell, jobs):
                with self.subTest(cmd=label):
                    obj = self.reply(r)
                    if want[label] is None:
                        self.assertEqual(obj.get('ok'), 1, r.stdout)
                        self.assertEqual(recs, [['at', '-t', '8', label]])
                    else:
                        self.assertEqual(obj, {'ok': 0, 'confirm': 1, 'kind': want[label],
                                               'error': 'This command needs confirmation'})
                        self.assertEqual(recs, [], 'sent without a confirmation')

    def test_a_confirmed_command_is_sent_and_a_confirmed_scan_starts_a_job(self):
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'at', {'cmd': 'AT+CFUN=0', 'confirm': '1'})
            self.assertEqual(self.reply(r).get('ok'), 1)
            self.assertEqual(recs, [['at', '-t', '8', 'AT+CFUN=0']])
            r, recs, _ = self.call(shell, 'at', {'cmd': 'AT+COPS=?', 'confirm': '1'})
            self.assertEqual(self.reply(r), {'ok': 1, 'started': 1, 'job': 1, 'cmd': 'AT+COPS=?', 'limit': 99})
            self.assertEqual(recs, [['setsid', 'mu300dash', 'at-job']])
            r, recs, _ = self.call(shell, 'at', {'cmd': 'AT', 'confirm': 'yes'})
            self.assertEqual(self.reply(r)['error'], 'The confirm parameter must be 0 or 1')
            self.assertEqual(recs, [])

    def test_the_job_holds_the_terminal_until_it_is_done(self):
        d = self.tmp / 'shared'
        for shell in self.each_shell():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir()
            self.output('at', '+COPS: (2,"A","A","46000",7),,(0,1,2,3,4),(0,1,2)\nOK')
            self.reply(self.call(shell, 'at', {'cmd': 'AT+COPS=?', 'confirm': '1'}, MU300_DASH_DIR=d)[0])
            # started a moment ago and not yet running: it counts as running, and the terminal says why it waits
            obj = self.reply(self.call(shell, 'at_job', {}, MU300_DASH_DIR=d)[0])
            self.assertEqual((obj['running'], obj['cmd'], obj['limit']), (1, 'AT+COPS=?', 99))
            r, recs, _ = self.call(shell, 'at', {'cmd': 'AT'}, MU300_DASH_DIR=d)
            obj = self.reply(r)
            self.assertEqual((obj['busy'], obj['job'], recs), (1, 1, []))
            self.assertEqual(obj['error'],
                             'A long AT command is still running in the background; try again when it has finished')
            # the detached half (what setsid would have started), with the at stub as the modem
            calls = self.tmp / 'jobcalls'
            calls.mkdir(exist_ok=True)
            j = subprocess.run(shell + [str(sec.DASH), 'at-job'], capture_output=True, timeout=60, env=self.env(
                STUB_CALLS=calls, STUB_OUT=self.out, MU300_DASH_BIN=self.adapters, MU300_DASH_DIR=d,
                MU300_AT=self.adapters / 'at'))
            self.assertEqual(j.returncode, 0, j.stderr)
            rec = [a.decode() for a in next(calls.glob('call.*')).read_bytes().split(b'\0')[:-1]]
            self.assertEqual(rec, ['at', '-t', '99', 'AT+COPS=?'])
            obj = self.reply(self.call(shell, 'at_job', {}, MU300_DASH_DIR=d)[0])
            self.assertEqual((obj['done'], obj['ok'], obj['running']), (1, 1, 0))
            self.assertIn('+COPS: (2,"A"', obj['reply'])
            r, recs, _ = self.call(shell, 'at', {'cmd': 'AT'}, MU300_DASH_DIR=d)
            self.assertEqual(self.reply(r).get('ok'), 1)
            for f in calls.iterdir():
                f.unlink()
            self.assertEqual(self.reply(self.call(shell, 'at_job', {})[0]), {'none': 1})

    def test_a_send_runs_in_the_background_and_its_result_waits(self):
        # mu300-sms waits up to 40 s for the modem's +CMGS, and rpcd ends a call at 30 s: a slow send was killed with
        # the call and its result lost (#176). sms_send only starts the job; sms_job says running, then how it went
        # - the reply sms_send used to give - and a second send meanwhile is busy, never a second submission.
        d = self.tmp / 'shared'
        for shell in self.each_shell():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir()
            self.output('sms', 'sent (reference 9)')
            r, recs, _ = self.call(shell, 'sms_send', {'num': '10099', 'text': 'CXYL'}, MU300_DASH_DIR=d)
            self.assertEqual(self.reply(r), {'ok': 1, 'started': 1, 'job': 1, 'limit': 200})
            self.assertEqual(recs, [['setsid', 'mu300dash', 'sms-job']])
            # started a moment ago and not yet running: it counts as running, and another send is not started
            obj = self.reply(self.call(shell, 'sms_job', {}, MU300_DASH_DIR=d)[0])
            self.assertEqual((obj['running'], obj['limit']), (1, 200))
            r, recs, _ = self.call(shell, 'sms_send', {'num': '10099', 'text': 'again'}, MU300_DASH_DIR=d)
            obj = self.reply(r)
            self.assertEqual((obj['ok'], obj['busy'], obj['job'], recs), (0, 1, 1, []))
            self.assertEqual((d / 'sms-job' / 'text').read_text(), 'CXYL')
            # the detached half (what setsid would have started), with the sms stub as mu300-sms
            calls = self.tmp / 'jobcalls'
            shutil.rmtree(calls, ignore_errors=True)
            calls.mkdir()
            j = subprocess.run(shell + [str(sec.DASH), 'sms-job'], capture_output=True, timeout=60, env=self.env(
                STUB_CALLS=calls, STUB_OUT=self.out, MU300_DASH_BIN=self.adapters, MU300_DASH_DIR=d,
                MU300_SMS_BIN=self.adapters / 'sms', MU300_DASH_OPS_DIR=self.tmp / 'flash'))
            self.assertEqual(j.returncode, 0, j.stderr)
            rec = next(f for f in calls.glob('call.*') if f.suffix != '.stdin')
            self.assertEqual([a.decode() for a in rec.read_bytes().split(b'\0')[:-1]], ['sms', 'send', '--stdin', '10099'])
            self.assertEqual(rec.with_name(rec.name + '.stdin').read_text(), 'CXYL')
            obj = self.reply(self.call(shell, 'sms_job', {}, MU300_DASH_DIR=d)[0])
            self.assertEqual((obj['done'], obj['running'], obj['ok'], obj['reply']), (1, 0, 1, 'sent (reference 9)'))
            self.assertFalse((d / 'sms-job' / 'text').exists())   # no copy of the message is kept
            # the operations log: the request, and how the send went (from the job); never the number or the text
            ops = (d / 'ops.log').read_text()
            self.assertRegex(ops, r' sms_send \d+\.\d\ds ok\n')
            self.assertRegex(ops, r' sms_send result \d+\.\d\ds ok\n')
            self.assertNotIn('10099', ops)
            self.assertNotIn('CXYL', ops)
            # a job that died under the send (no result): not sent again by itself, and said so
            (d / 'sms-job' / 'reply').unlink()
            obj = self.reply(self.call(shell, 'sms_job', {}, MU300_DASH_DIR=d)[0])
            self.assertEqual((obj['done'], obj['ok'], obj['error']), (1, 0, 'Sending failed'))
            self.assertIn('without a result', obj['detail'])
            # the next send starts afresh
            r, recs, _ = self.call(shell, 'sms_send', {'num': '10099', 'text': 'next'}, MU300_DASH_DIR=d)
            self.assertEqual(self.reply(r).get('started'), 1)
            self.assertEqual(recs, [['setsid', 'mu300dash', 'sms-job']])
            self.assertEqual(self.reply(self.call(shell, 'sms_job', {})[0]), {'none': 1})

    def test_lock_get_never_reads_the_modem_in_the_request(self):
        d = self.tmp / 'shared'
        d.mkdir()
        (d / 'lock.json').write_text('{"ts":1,"mode":{"label":"auto"}}\n')
        for shell in self.each_shell():
            t0 = time.monotonic()
            r, recs, _ = self.call(shell, 'lock_get', {}, MU300_DASH_DIR=d)
            self.assertEqual(recs, [['lock', 'get', 'cached']])
            self.assertLess(time.monotonic() - t0, 3)

    def test_lock_get_carries_the_reset_result(self):
        # the verified reset (unisoc-modem/lock reset) runs detached and leaves its result in the lock state; the
        # page polls lock_get for it, so the result object comes through as the adapter wrote it
        d = self.tmp / 'shared'
        d.mkdir()
        (d / 'lock.json').write_text('{"ts":1,"mode":{"label":"4g"}}\n')
        reset = {'state': 'failed', 'ts': 5, 'end': 9, 'error': 'The modem refused a write', 'cmd': 'AT+SPLBAND=2,0,0,0,0'}
        self.output('lock', json.dumps({'ts': 1, 'mode': {'label': '4g'}, 'auto_apply': 1, 'reset': reset}))
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'lock_get', {}, MU300_DASH_DIR=d)
            self.assertEqual(recs, [['lock', 'get', 'cached']])
            self.assertEqual(self.reply(r)['reset'], reset)
            r, recs, _ = self.call(shell, 'lock_set', {'kind': 'reset', 'val': 'auto'}, MU300_DASH_DIR=d)
            self.assertEqual(self.reply(r).get('started'), 1)
            self.assertEqual(recs, [['setsid', 'lock', 'reset']])

    @unittest.skipUnless(HAVE_PROC, 'needs /proc')
    def test_without_a_reading_yet_lock_get_says_busy_in_time(self):
        d = self.tmp / 'shared'
        d.mkdir()
        reader = subprocess.Popen(['sh', '-c', 'sleep 60; :', '/usr/libexec/unisoc-modem/lock'])
        try:
            (d / 'lock-read.lck').mkdir()
            (d / 'lock-read.lck' / 'pid').write_text(f'{reader.pid}\n')
            for shell in self.each_shell():
                t0 = time.monotonic()
                r, recs, _ = self.call(shell, 'lock_get', {}, MU300_DASH_DIR=d, MU300_DASH_LOCK_GET_WAIT='2')
                self.assertLess(time.monotonic() - t0, 5)
                self.assertEqual(self.reply(r), {'busy': 1, 'error':
                                                 'Reading the locks from the modem; try again in a few seconds'})
                self.assertEqual(sorted(recs), [['lock', 'get', 'cached'], ['setsid', 'lock', 'refresh']])
        finally:
            reader.kill()
            reader.wait()

    def test_the_signal_read_has_a_deadline(self):
        (self.adapters / 'cell').write_text('#!/bin/sh\necho "$MU300_DASH_DEADLINE" > "$STUB_OUT/deadline"\n')
        for shell in self.each_shell():
            self.reply(self.call(shell, 'signal', {})[0])
            self.assertEqual((self.out / 'deadline').read_text().strip(), '14')

    def test_the_terminal_waits_less_than_the_browser(self):
        # LuCI's rpc gives up after 20 s (L.env.rpctimeout): the lock wait and the answer budget fit in that
        at = self.tmp / 'modem'
        at.write_text('#!/bin/sh\necho "lock=$MU300_AT_LOCK_WAIT budget=$MU300_AT_BUDGET" > "$STUBLOG/env"\necho OK\n')
        at.chmod(0o755)
        for shell in self.each_shell():
            self.reply(self.call(shell, 'at', {'cmd': 'ATI'}, MU300_AT=at)[0])
            env = dict(kv.split('=') for kv in (self.tmp / 'env').read_text().split())
            self.assertLessEqual(int(env['lock']) + int(env['budget']), 17)


class OpsLog(sec.Mu300Dash):
    """The panel's calls of this boot (RAM) and the one before (flash): names, times, outcomes - never a value."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / 'run-shared'
        self.flash = self.tmp / 'flash'

    def go(self, shell, method, params=None, **env):
        self.dir.mkdir(exist_ok=True)
        return self.call(shell, method, params, MU300_DASH_DIR=self.dir, MU300_DASH_OPS_DIR=self.flash, **env)

    def lines(self):
        return [line for line in (self.dir / 'ops.log').read_text().split('\n') if line]

    def reboot(self):
        shutil.rmtree(self.dir)

    def test_what_a_line_holds(self):
        for shell in self.each_shell():
            self.reboot() if self.dir.exists() else None
            self.go(shell, 'at', {'cmd': 'AT+CPIN="1234"', 'confirm': '1'})
            self.go(shell, 'at', {'cmd': 'ATD+905551234567', 'confirm': '1'})
            self.go(shell, 'at', {'cmd': 'AT+CFUN=0'})                       # not confirmed
            self.go(shell, 'act', {'op': 'radio', 'arg': 'off'})
            self.go(shell, 'lock_set', {'kind': 'lte', 'val': '1,3'})
            self.go(shell, 'status', {})                                     # a quick, good read: no line
            (self.adapters / 'cell').write_text('#!/bin/sh\nprintf \'{"ts":5,"error":"x"}\' > "$MU300_DASH_DIR/sig.json"\n')
            self.go(shell, 'signal', {})                                     # the modem did not answer: a line
            self.output('sms', '')
            self.go(shell, 'sms_list', {})                                   # a failed read: a line
            (self.out / 'sms').unlink()
            self.go(shell, 'sms_send', {'num': '+905551234567', 'text': 'secret code 4711'})
            got = self.lines()
            text = '\n'.join(got)
            for secret in ('1234', '905551234567', '4711', 'secret', 'off', '1,3'):
                self.assertNotIn(secret, text)
            words = [line.split()[2:] for line in got]
            self.assertEqual([w[:-2] for w in words], [['at', 'AT+CPIN='], ['at', 'ATD'], ['at', 'AT+CFUN='],
                                                       ['act', 'radio'], ['lock_set', 'lte'], ['signal'], ['sms_list'],
                                                       ['sms_send']])
            self.assertEqual([w[-1] for w in words], ['ok', 'ok', 'confirm', 'ok', 'ok', 'error', 'error', 'ok'])
            for w in words:
                self.assertRegex(w[-2], r'^\d+\.\d\ds$')

    def test_a_killed_call_is_found_afterwards(self):
        # rpcd kills a call at 30 s (SIGKILL): its marker stays, and the next call writes it as killed
        for shell in self.each_shell():
            self.reboot() if self.dir.exists() else None
            self.dir.mkdir()
            (self.dir / 'ops.run.999999').write_text('10-11 12:00:00 signal\n')
            self.go(shell, 'act', {'op': 'wifi', 'arg': 'on'})
            self.assertEqual(self.lines()[0], '10-11 12:00:00 signal - killed')
            self.assertEqual(list(self.dir.glob('ops.run.*')), [])

    def test_flash_keeps_this_boot_and_the_one_before(self):
        for shell in self.each_shell():
            self.reboot() if self.dir.exists() else None
            shutil.rmtree(self.flash, ignore_errors=True)
            self.go(shell, 'act', {'op': 'wifi', 'arg': 'on'})
            first = (self.flash / 'panel-ops.log').read_text()
            self.assertIn(' act wifi ', first)
            # within the flush interval nothing more is written
            self.go(shell, 'act', {'op': 'data', 'arg': 'up'})
            self.assertEqual((self.flash / 'panel-ops.log').read_text(), first)
            # the service's stop writes it now
            self.dir.mkdir(exist_ok=True)
            r = self.script(shell, sec.DASH, 'flush-ops', MU300_DASH_DIR=self.dir, MU300_DASH_OPS_DIR=self.flash)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(' act data ', (self.flash / 'panel-ops.log').read_text())
            # a reboot: RAM is empty; the first call of the new boot keeps the old copy as the previous boot's
            self.reboot()
            self.go(shell, 'act', {'op': 'wifi', 'arg': 'off'})
            self.assertIn(' act data ', (self.flash / 'panel-ops.prev.log').read_text())
            self.assertNotIn(' act data ', (self.flash / 'panel-ops.log').read_text())
            obj = self.reply(self.go(shell, 'ops_log', {})[0])
            self.assertEqual(obj['ok'], 1)
            self.assertIn(' act wifi ', obj['now'])
            self.assertIn(' act data ', obj['prev'])
            # reading the log is not itself logged
            self.assertNotIn('ops_log', obj['now'])

    def test_the_log_stays_small(self):
        for shell in self.each_shell():
            self.reboot() if self.dir.exists() else None
            self.dir.mkdir()
            (self.dir / 'ops.log').write_text(''.join(f'10-11 12:00:00 act wifi 0.01s ok {i}\n' for i in range(260)))
            self.go(shell, 'act', {'op': 'wifi', 'arg': 'on'})
            self.assertEqual(len(self.lines()), 200)


COMMON = lui.COMMON
VIEWS = lui.VIEWS


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the LuCI JS tests')
class Pages(unittest.TestCase):
    HARNESS = lui.Views.HARNESS

    def node(self, body):
        """common.js alone, with the rpc declarations recorded and calls answered by `answer(method)`"""
        harness = r'''
const fs = require('fs');
String.prototype.format = function() { const a = arguments; let i = 0; return this.replace(/%(s|d)/g, () => String(a[i++])); };
const _ = (s) => s;
const decl = [], calls = [];
let answer = () => Promise.resolve({});
const rpc = { declare: (o) => { decl.push(o); return function() { calls.push(o.method); return answer(o.method); }; } };
let tick;
const window = { setInterval: (fn) => { tick = fn; } };
const document = { getElementById: () => null, createElement: () => ({}), head: { appendChild: () => {} } };
const M = new Function('rpc', 'baseclass', '_', 'document', 'getComputedStyle', 'window', 'setTimeout', fs.readFileSync(process.argv[2], 'utf8'))(
    rpc, { extend: (o) => o }, _, document, () => ({ getPropertyValue: () => '' }), window, () => 0);
const flush = () => new Promise((r) => setImmediate(r));
(async function() { __BODY__ })().then((r) => process.stdout.write(JSON.stringify(r)), (e) => { console.error(e); process.exit(1); });
'''.replace('__BODY__', body)
        r = subprocess.run(['node', '-', str(COMMON)], input=harness, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_call_goes_on_its_own(self):
        # LuCI batches calls made together into one request that uhttpd answers call by call: the status snapshot
        # (battery, CPU) then waited behind a lock reading, and a browser timeout failed them all
        r = self.node("return decl.map((o) => [ o.object, o.method, o.nobatch === true ]);")
        self.assertGreater(len(r), 30)
        self.assertEqual([m for o, m, nb in r if not nb], [])
        self.assertIn(['mu300dash', 'at_job', True], r)
        self.assertIn(['mu300dash', 'ops_log', True], r)

    def test_backoff(self):
        r = self.node("return [0, 1, 2, 3, 4, 9].map((n) => M.backoff(1500, n)).concat([ M.backoff(1, 2) ]);")
        self.assertEqual(r, [1500, 3000, 6000, 12000, 18000, 18000, 4])

    def test_the_sms_watcher_never_stacks_requests_and_backs_off(self):
        r = self.node('''
M.watchSms();
let release;
answer = () => new Promise((res) => { release = res; });
tick(); tick(); tick(); await flush();
const whileOut = calls.length;                       // one request, however many ticks
answer = () => Promise.reject(new Error('timeout'));
release({ msgs: [] }); await flush(); await flush();
calls.length = 0;
for (let i = 0; i < 8; i++) { tick(); await flush(); await flush(); }
return [ whileOut, calls.length ];''')
        self.assertEqual(r[0], 1)
        self.assertLess(r[1], 8)          # failures in a row: fewer reads than ticks
        self.assertGreater(r[1], 0)

    def run_view(self, name, body):
        return lui.Views.run_view(self, name, None, body)

    def test_the_terminal_asks_before_a_command_that_changes_the_modem(self):
        body = '''
const sent = [];
let replies = [];
M.callAt = (cmd, confirm) => { sent.push([ cmd, confirm ]); return Promise.resolve(replies.shift()); };
M.callAtHist = () => Promise.resolve({ history: '' });
M.callAtJob = () => Promise.resolve({ none: 1 });
let asked = [], answerYes = true;
M.confirmBox = (title, msg) => { asked.push(msg); return Promise.resolve(answerYes); };
V.render(); await flush();
const out = () => get('mud-at-out').children.map((c) => c.textContent.trim());
replies = [ { ok: 0, confirm: 1, kind: 'nv', error: 'This command needs confirmation' }, { ok: 1, reply: 'OK' } ];
get('mud-at-cmd').value = 'AT+SPLBAND=1,0,0,0,5,0'; V.send(); for (let i = 0; i < 6; i++) await flush();
const yes = { sent: sent.splice(0), out: out(), asked: asked.splice(0) };
get('mud-at-out').children = [];
answerYes = false;
replies = [ { ok: 0, confirm: 1, kind: 'radio', error: 'This command needs confirmation' } ];
get('mud-at-cmd').value = 'AT+CFUN=0'; V.send(); for (let i = 0; i < 6; i++) await flush();
return { yes: yes, no: { sent: sent, out: out(), asked: asked } };'''
        r = self.run_view('at', body)['result']
        self.assertEqual(r['yes']['sent'], [['AT+SPLBAND=1,0,0,0,5,0', ''], ['AT+SPLBAND=1,0,0,0,5,0', '1']])
        self.assertIn('stays in force after a reboot, and in Android too', r['yes']['asked'][0])
        self.assertIn('OK', r['yes']['out'])
        self.assertEqual(r['no']['sent'], [['AT+CFUN=0', '']])
        self.assertIn('Not sent', r['no']['out'])
        self.assertIn('mobile data drops', r['no']['asked'][0])

    def test_a_scan_is_followed_in_the_background(self):
        body = '''
let jobs = [ { none: 1 } ];
M.callAt = () => Promise.resolve({ ok: 1, started: 1, job: 1, cmd: 'AT+COPS=?', limit: 180 });
M.callAtHist = () => Promise.resolve({ history: '' });
M.callAtJob = () => Promise.resolve(jobs.shift() || { none: 1 });
M.confirmBox = () => Promise.resolve(true);
V.render(); for (let i = 0; i < 4; i++) await flush();
jobs = [ { running: 1, cmd: 'AT+COPS=?', elapsed: 3, limit: 180 },
         { running: 0, done: 1, ok: 1, elapsed: 95, reply: '+COPS: (2,"A","A","46000",7)\\nOK' } ];
get('mud-at-cmd').value = 'AT+COPS=?'; V.send(); for (let i = 0; i < 6; i++) await flush();
while (timers.length) { timers.shift()(); for (let i = 0; i < 6; i++) await flush(); }
return get('mud-at-out').children.map((c) => c.textContent.trim());'''
        out = self.run_view('at', body)['result']
        self.assertTrue(any(line.startswith('Running in the background (at most 180 s)') for line in out), out)
        self.assertIn('+COPS: (2,"A","A","46000",7)', out)
        self.assertIn('—— 95 s in the background', out)

    def test_the_dashboard_says_when_its_cellular_readings_are_old(self):
        body = '''
V.render(); await flush();
const st = (age, err) => ({ info: { ts: 10, host: 'mu300' }, sig_age: age,
    cell: err ? { ts: 5, error: 'AT backend did not answer' } : { ts: 5, cfun: 1, sig: { rsrp: -95 } } });
V.update(st(3)); const fresh = [ text('stale'), get('mud-stale').style.display ];
V.update(st(42)); const old = [ text('stale'), get('mud-stale').style.display ];
V.update(st(600)); const older = text('stale');
V.update(st(600, true)); const dead = get('mud-stale').style.display;
return { fresh: fresh, old: old, older: older, dead: dead };'''
        r = self.run_view('home', body)['result']
        self.assertEqual(r['fresh'], ['', 'none'])
        self.assertEqual(r['old'], ['Modem busy: cellular readings from 42 s ago', ''])
        self.assertEqual(r['older'], 'Modem busy: cellular readings from 10 min ago')
        self.assertEqual(r['dead'], 'none')   # no answer at all: the hero says "No response" already

    def test_channel_analysis_asks_before_scanning_with_an_access_point(self):
        # LuCI's page scans at once and on every poll, and a scan drops the hotspot's clients on this driver: with an
        # access point it waits for a click, without one it opens LuCI's page directly
        body = '''
const required = [];
L.require = (m) => { required.push(m); return Promise.resolve(); };
let ifaces = [ { mode: 'ap' } ];
uci.sections = () => ifaces;
const root = V.render(); await flush();
const before = required.slice();
root.querySelector('#mud-ca-go').onclick(); await flush();
const ap = { before: before, after: required.slice(), text: root.querySelector('#mud-ca-note').textContent };
required.length = 0;
ifaces = [ { mode: 'sta' }, { mode: 'ap', disabled: '1' } ];
V.render(); await flush();
return { ap: ap, sta: required.slice() };'''
        r = self.run_view('channel_analysis', body)['result']
        self.assertEqual(r['ap']['before'], [])
        self.assertEqual(r['ap']['after'], ['view.status.channel_analysis'])
        self.assertIn('may not reconnect by themselves', r['ap']['text'])
        self.assertEqual(r['sta'], ['view.status.channel_analysis'])

    def test_the_status_overrides_come_after_luci_and_keep_its_pages(self):
        menu_d = sec.APP / 'usr' / 'share' / 'luci' / 'menu.d'
        f = menu_d / 'luci-mu300-status-overrides.json'
        # the dispatcher reads menu.d in name order and a later file's action wins
        self.assertGreater(f.name, 'luci-mod-status.json')
        menu = json.loads(f.read_text())
        self.assertEqual(menu['admin/status/channel_analysis']['action']['path'], 'mu300/channel_analysis')
        self.assertEqual(menu['admin/status/channel_analysis']['depends']['acl'],
                         ['luci-mod-status-channel_analysis'])
        self.assertEqual(menu['admin/status/logs/syslog']['action']['path'], 'mu300/syslog')
        for m in menu.values():
            self.assertNotIn('title', m)        # LuCI's titles and translations stay
        log = (VIEWS / 'syslog.js').read_text()
        self.assertIn("'require tools.views as views'", log)
        self.assertIn('fetchMaxRows: 250', log)
        self.assertIn('EVERY_MS = 15000', log)
        self.assertIn("this.super('pollLog', arguments)", log)

    def test_the_device_page_shows_the_operations(self):
        body = '''
M.callUsbGet = () => Promise.resolve({ ok: 1, role: 'device' });
M.callOpsLog = () => Promise.resolve({ ok: 1, now: 'a 1\\nb 2', prev: '' });
const root = V.render({ ok: 1, role: 'device' }); await flush();
get('mud-usb-ops-show').on.click[0](); for (let i = 0; i < 4; i++) await flush();
return get('mud-usb-ops').children.map((c) => c.textContent);'''
        r = self.run_view('device', body)['result']
        self.assertEqual(r, ['This boot', 'b 2\na 1', 'Previous boot', '(empty)'])


if __name__ == '__main__':
    unittest.main()
