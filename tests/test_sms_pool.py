"""The SMS pool of openwrt-luci (K69, D11): mu300-sms keeps every message the SIM holds, and every message the panel
sends, in /etc/mu300/sms-pool, one file per message; mu300-smsd keeps it in step with the SIM. It is the panel's SMS
store only - the `sms` command stays the SMS tool of every system, with its own state in /etc/mu300/sms.

mu300-sms talks to the modem only through mu300-at, in PDU mode as `sms` does, and decodes with the same
/opt/mu300/lib/sms-pdu.awk. Here mu300-at is a stub that answers from a table and records every command, and the AT
daemon's command fifo is a fifo in the scratch directory. The panel side is mu300dash (luci-app-mu300's rpcd backend)
run on the real mu300-sms, with the jsonfilter stand-in of test_mu300dash_security."""
import json
import os
import re
import shutil
import signal
import subprocess
import time
import unittest

from helpers import TOP, ShellTest
from test_mu300dash_security import DASH, JSONFILTER

LUCI = TOP / 'openwrt' / 'luci-overlay'
SMS = LUCI / 'opt' / 'mu300' / 'bin' / 'mu300-sms'
SMSD = LUCI / 'opt' / 'mu300' / 'bin' / 'mu300-smsd'
INITD = LUCI / 'etc' / 'init.d' / 'mu300-smsd'
AWK = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'sms-pdu.awk'
BUILD = TOP / 'openwrt' / 'build-rootfs.sh'
APP = TOP / 'openwrt' / 'luci-app-mu300'

# Two SMS-DELIVER PDUs, checked against sms-pdu.awk: the GSM 7-bit textbook example (from +31641600986, unread on the
# SIM) and a UCS-2 one from an alphanumeric sender (ADANA BLD, the case text mode mangles - FINDINGS 26c), already read
PDU_GSM = '07911326040000F0040B911346610089F60000208062917314480CC8F71D14969741F977FD07'
PDU_UCS2 = ('000410D04162D0190409994400086290517181004024004B006100720067006F006E0075007A00200079006F006C006400610020011F'
            '00FC015F')
CMGL = f'+CMGL: 3,0,,40\n{PDU_GSM}\n+CMGL: 7,1,,56\n{PDU_UCS2}\nOK\n'

# The stub mu300-at: `mu300-at -t SECONDS COMMAND`. Appends the command to $STUBLOG/at.log (one per line, the CR and
# Ctrl-Z of a submit shown as ^ and ~), then answers from $STUBLOG/at/<name>: the first file whose name is a prefix
# of the command with the characters outside [A-Za-z0-9] dropped (CMGL4, CPMS, CMGS ...); OK when there is none.
# $STUBLOG/at/BUSY makes it the client that gave up on the lock. $STUBLOG/held.log gets, per call, the lock the client
# was told it holds (MU300_AT_LOCK_HELD) and who holds the lock directory (a send's transaction, FINDINGS 46).
AT_STUB = r'''
log=$STUBLOG/at.log
[ "${1:-}" = -t ] && shift 2
printf '%s\n' "$1" | tr '\r\032' '^~' >> "$log"
printf '%s %s\n' "${MU300_AT_LOCK_HELD:-none}" "$(cat "$MU300_AT_DIR/lock/pid" 2>/dev/null || echo none)" >> "$STUBLOG/held.log"
[ -f "$STUBLOG/at/BUSY" ] && { echo "mu300-at: busy" >&2; exit 1; }
key=$(printf '%s' "$1" | tr -cd 'A-Za-z0-9')
for f in $(ls "$STUBLOG/at" 2>/dev/null | sort -r); do
    case $key in AT"$f"*) cat "$STUBLOG/at/$f"; exit 0 ;; esac
done
echo OK
'''


class Pool(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        self.answer('CPMS', '+CPMS: 2,50,2,50,2,50\nOK\n')
        self.answer('CMGL4', CMGL)
        self.atdir = self.tmp / 'run-at'
        self.atdir.mkdir()
        os.mkfifo(self.atdir / 'cmd')
        self.pool = self.tmp / 'pool'
        self.run = self.tmp / 'run'   # where the pool lock goes (/run on the device)
        self.run.mkdir()

    def test_no_cksum_on_the_device(self):
        # OpenWrt's busybox has md5sum but no cksum: the pool lock is named without it, and nothing is printed
        self.stub('cksum', 'echo "cksum: not found" >&2; exit 127')
        for shell in self.each_shell():
            self.fresh()
            r = self.run_sms(shell, 'list')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('cksum', r.stderr, shell)
            self.assertNotIn('not found', r.stderr + r.stdout, shell)

    def fresh(self):
        """An empty pool, an empty AT log and the default answers (for the next shell)."""
        shutil.rmtree(self.pool, ignore_errors=True)
        (self.tmp / 'at.log').unlink(missing_ok=True)
        (self.tmp / 'held.log').unlink(missing_ok=True)
        for f in (self.tmp / 'at').iterdir():
            f.unlink()
        self.answer('CPMS', '+CPMS: 2,50,2,50,2,50\nOK\n')
        self.answer('CMGL4', CMGL)

    def answer(self, name, text):
        (self.tmp / 'at' / name).write_text(text)

    def at_log(self):
        p = self.tmp / 'at.log'
        return p.read_text().splitlines() if p.exists() else []

    def run_sms(self, shell, *args, stdin=None, **env):
        e = dict(MU300_SMS_POOL=self.pool, MU300_AT_DIR=self.atdir, MU300_SMS_AWK=AWK, MU300_SMS_RUN=self.run)
        e.update(env)
        return self.script(shell, SMS, *args, stdin=stdin, **e)

    def msgs(self):
        """{id: (headers dict, body)} of the pool."""
        out = {}
        for f in sorted((self.pool / 'msg').glob('[0-9]*')):
            head, _, body = f.read_text().partition('\n\n')
            out[f.name] = (dict(line.split(': ', 1) for line in head.splitlines()), body)
        return out

    def put(self, mid, status, direction, peer, scts, body, sim_index=''):
        """A message written into the pool directly, as mu300-sms writes it."""
        d = self.pool / 'msg'
        d.mkdir(parents=True, exist_ok=True)
        who = 'from' if direction == 'mt' else 'to'
        (d / f'{mid:06d}').write_text(
            f'id: {mid:06d}\ndir: {direction}\nstatus: {status}\n{who}: {peer}\nscts: {scts}\nreceived: 1790000000\n'
            f'sim_index: {sim_index}\ncoding: gsm\nfingerprint: fp{mid}\nlength: {len(body.encode())}\n\n{body}\n')
        nid = self.pool / 'next_id'
        if not nid.exists() or int(nid.read_text()) < mid:
            nid.write_text(f'{mid}\n')

    # ------------------------------------------------------------------ the pool path
    def test_the_pool_is_etc_mu300_sms_pool_everywhere(self):
        # D11: /etc/mu300/sms is the sms command's state directory, so the pool is never there
        for p in (SMS, SMSD):
            with self.subTest(p=p.name):
                self.assertIn('POOL=${MU300_SMS_POOL:-/etc/mu300/sms-pool}\n', p.read_text())
        src = INITD.read_text()
        self.assertIn('local pool=/etc/mu300/sms-pool\n', src)
        self.assertIn('case $pool in /etc/mu300/sms|/etc/mu300/sms/*) pool=/etc/mu300/sms-pool ;; esac', src)
        self.assertIn('procd_set_param env MU300_SMS_POOL="$pool"', src)
        self.assertIn("option sms_pool '/etc/mu300/sms-pool'",
                      (APP / 'root' / 'etc' / 'config' / 'unisoc_modem').read_text())
        self.assertIn("o.placeholder = '/etc/mu300/sms-pool'",
                      (APP / 'htdocs/luci-static/resources/view/mu300/settings.js').read_text())

    def test_programs_are_executable_and_english(self):
        for p in (SMS, SMSD, INITD):
            with self.subTest(p=p.name):
                self.assertTrue(p.stat().st_mode & 0o111, 'not executable')
                self.assertIsNone(re.search('[⺀-鿿豈-﫿＀-￯]', p.read_text()))
                if not shutil.which('git'):
                    continue
                mode = subprocess.run(['git', 'ls-files', '-s', str(p)], cwd=TOP, capture_output=True,
                                      text=True).stdout.split(' ')[0]
                if mode:   # tracked: the image gets the git mode
                    self.assertEqual(mode, '100755')

    # ------------------------------------------------------------------ list / show
    def test_list_on_a_pool(self):
        self.put(1, 'read', 'mt', '+905551112233', '2026-10-01 09:00:00', 'first')
        self.put(2, 'unread', 'mt', 'ADANA BLD', '2026-10-02 10:00:00', 'second line one\nline two')
        self.put(3, 'sent', 'mo', '10086', '2026-10-03 11:00:00', 'x' * 60)
        for shell in self.each_shell():
            r = self.run_sms(shell, 'list')
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = r.stdout.splitlines()
            self.assertEqual(lines[0], 'pool: 3 message(s), 1 unread - page 1/1 (10 per page)')
            # newest first, the columns mu300dash cuts at (1-7, 9-15, 17-19, 21-38, 40-60, 62-)
            self.assertEqual([(ln[0:7].strip(), ln[8:15].strip(), ln[16:19].strip(), ln[20:38].strip(),
                               ln[39:60].strip(), ln[61:]) for ln in lines[1:]],
                             [('000003', 'sent', 'mo', '10086', '2026-10-03 11:00:00', 'x' * 44),
                              ('000002', 'unread', 'mt', 'ADANA BLD', '2026-10-02 10:00:00', 'second line one'),
                              ('000001', 'read', 'mt', '+905551112233', '2026-10-01 09:00:00', 'first')])
            r = self.run_sms(shell, 'list', '2', MU300_SMS_PAGE=2)
            self.assertEqual(r.stdout.splitlines(), [
                'pool: 3 message(s), 1 unread - page 2/2 (2 per page)',
                '000001  read    mt  +905551112233      2026-10-01 09:00:00   first'])

    def test_list_on_an_empty_pool_creates_it(self):
        for shell in self.each_shell():
            r = self.run_sms(shell, 'list')
            self.assertEqual((r.returncode, r.stdout), (0, 'pool: 0 message(s), 0 unread - page 1/1 (10 per page)\n'))
            self.assertTrue((self.pool / 'msg').is_dir())

    def test_show_prints_the_whole_message_and_marks_it_read(self):
        for shell in self.each_shell():
            self.put(2, 'unread', 'mt', 'ADANA BLD', '2026-10-02 10:00:00', 'line one\nline two')
            r = self.run_sms(shell, 'show', '2')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('from:        ADANA BLD\n', r.stdout)
            self.assertTrue(r.stdout.endswith('\n---\nline one\nline two\n'), r.stdout)
            self.assertEqual(self.msgs()['000002'][0]['status'], 'read')
            # already read: still exit 0 (mu300dash reads a non-zero exit as a failed read)
            self.assertEqual(self.run_sms(shell, 'show', '000002').returncode, 0)
            r = self.run_sms(shell, 'show', '9')
            self.assertEqual((r.returncode, r.stdout, r.stderr), (1, '', 'mu300-sms: no message 9\n'))
            for bad in ('', '-1', '1x', '../1'):
                self.assertEqual(self.run_sms(shell, 'show', bad).returncode, 1, bad)

    # ------------------------------------------------------------------ sync
    def test_sync_pulls_the_sim_into_the_pool_in_pdu_mode(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('sync: 2 new, 2 on the SIM, 2 in the pool', r.stdout)
            log = self.at_log()
            self.assertIn('AT+CMGF=0', log)
            self.assertIn('AT+CMGL=4', log)
            self.assertLess(log.index('AT+CMGF=0'), log.index('AT+CMGL=4'))
            self.assertNotIn('AT+CMGF=1', log)          # PDU mode stays, as the sms command leaves it
            m = self.msgs()
            got = {h['from']: (h['dir'], h['status'], h['scts'], h['sim_index'], body) for h, body in m.values()}
            self.assertEqual(got, {
                '+31641600986': ('mt', 'unread', '2002-08-26 19:37:41', '3', 'How are you?\n'),
                'ADANA BLD': ('mt', 'read', '2026-09-15 17:18:00', '7', 'Kargonuz yolda ğüş\n')})
            # again: nothing new, and the SIM index of a message that moved is brought up to date
            self.answer('CMGL4', CMGL.replace('+CMGL: 7,', '+CMGL: 5,'))
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 0 new, 2 on the SIM, 2 in the pool', r.stdout)
            self.assertEqual(sorted(h['sim_index'] for h, _ in self.msgs().values()), ['3', '5'])
            # list on what sync wrote
            lines = self.run_sms(shell, 'list').stdout.splitlines()
            self.assertEqual(lines[0], 'pool: 2 message(s), 1 unread - page 1/1 (10 per page)')

    def test_sync_joins_a_long_message_and_waits_for_its_missing_parts(self):
        # two parts of one message (concatenation header, reference 0x42), and a third message with a missing part
        def part(ref, total, n, text):
            ud = bytes([5, 0, 3, ref, total, n]).hex().upper() + text.encode('utf-16-be').hex().upper()
            return f'00440B911346610089F6000820806291731448{len(ud) // 2:02X}{ud}'
        cmgl = (f'+CMGL: 1,1,,30\n{part(0x42, 2, 2, " world")}\n+CMGL: 2,1,,30\n{part(0x42, 2, 1, "hello")}\n'
                f'+CMGL: 4,1,,30\n{part(0x43, 2, 1, "half")}\nOK\n')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', cmgl)
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('sync: 1 new, 1 on the SIM, 1 in the pool, 1 waiting for its other parts', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['sim_index'], body), ('1,2', 'hello world\n'))

    # ------------------------------------------------------------------ off the SIM
    def cmgr(self, slot, pdu):
        """What AT+CMGR=slot answers: the slot still holding PDU."""
        self.answer(f'CMGR{slot}', f'+CMGR: 1,,{len(pdu) // 2}\n{pdu}\nOK\n')

    def cmgd(self):
        return [c for c in self.at_log() if c.startswith('AT+CMGD')]

    def test_sync_moves_what_it_pooled_off_the_sim(self):
        # a full SIM takes no more messages: what the pool holds leaves the SIM, after the pool has it on the disk
        for shell in self.each_shell():
            self.fresh()
            self.cmgr(3, PDU_GSM)
            self.cmgr(7, PDU_UCS2)
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('sync: 2 new, 2 on the SIM, 2 in the pool, 2 moved off the SIM\n', r.stdout)
            log = self.at_log()
            self.assertEqual(self.cmgd(), ['AT+CMGD=3', 'AT+CMGD=7'])
            # each slot read back right before it goes, after the listing
            self.assertLess(log.index('AT+CMGL=4'), log.index('AT+CMGR=3'))
            self.assertLess(log.index('AT+CMGR=3'), log.index('AT+CMGD=3'))
            self.assertEqual(sorted(h['from'] for h, _ in self.msgs().values()), ['+31641600986', 'ADANA BLD'])
            # the SIM is empty now: the pool keeps both, unread stays unread
            self.answer('CMGL4', 'OK\n')
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 0 new, 0 on the SIM, 2 in the pool, 0 moved off the SIM\n', r.stdout)
            self.assertEqual(sorted(h['status'] for h, _ in self.msgs().values()), ['read', 'unread'])

    def test_a_slot_that_changed_since_the_listing_stays(self):
        # slot 7 holds another message by the time it would go (deleted by `sms`, a new one landed): not touched
        for shell in self.each_shell():
            self.fresh()
            self.cmgr(3, PDU_GSM)
            self.cmgr(7, PDU_GSM)
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.cmgd(), ['AT+CMGD=3'])
            self.assertIn('1 moved off the SIM, 1 left there', r.stdout)
            # a slot that does not answer (busy channel, CMS error) stays too
            self.fresh()
            self.cmgr(3, PDU_GSM)
            self.answer('CMGR7', '+CMS ERROR: 321\n')
            self.run_sms(shell, 'sync')
            self.assertEqual(self.cmgd(), ['AT+CMGD=3'])

    def test_a_message_the_pool_could_not_take_stays_on_the_sim(self):
        if os.geteuid() == 0:
            self.skipTest('root writes into a read-only directory')
        for shell in self.each_shell():
            self.fresh()
            self.cmgr(3, PDU_GSM)
            self.cmgr(7, PDU_UCS2)
            self.run_sms(shell, 'list')                 # the pool directory, then made read-only
            os.chmod(self.pool / 'msg', 0o500)
            try:
                r = self.run_sms(shell, 'sync')
            finally:
                os.chmod(self.pool / 'msg', 0o700)
            self.assertIn('it stays on the SIM', r.stderr)
            self.assertEqual(self.cmgd(), [])
            self.assertEqual(self.msgs(), {})

    def test_the_parts_of_an_incomplete_message_stay_until_it_is_whole(self):
        def part(ref, total, n, text):
            ud = bytes([5, 0, 3, ref, total, n]).hex().upper() + text.encode('utf-16-be').hex().upper()
            return f'00440B911346610089F6000820806291731448{len(ud) // 2:02X}{ud}'
        p1, p2, half = part(0x42, 2, 1, 'hello'), part(0x42, 2, 2, ' world'), part(0x43, 2, 1, 'half')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', f'+CMGL: 1,1,,30\n{p2}\n+CMGL: 2,1,,30\n{p1}\n+CMGL: 4,1,,30\n{half}\nOK\n')
            for slot, pdu in ((1, p2), (2, p1), (4, half)):
                self.cmgr(slot, pdu)
            r = self.run_sms(shell, 'sync')
            self.assertIn('1 waiting for its other parts, 2 moved off the SIM', r.stdout)
            self.assertEqual(self.cmgd(), ['AT+CMGD=1', 'AT+CMGD=2'])

    def test_a_message_deleted_from_the_pool_goes_from_the_sim_too(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')                 # no CMGR answers: both stay on the SIM this time
            self.assertEqual(self.cmgd(), [])
            ids = {h['from']: i for i, (h, _) in self.msgs().items()}
            self.run_sms(shell, 'delete', ids['ADANA BLD'])
            self.cmgr(7, PDU_UCS2)
            self.run_sms(shell, 'sync')
            self.assertEqual(self.cmgd(), ['AT+CMGD=7'])
            self.assertEqual([h['from'] for h, _ in self.msgs().values()], ['+31641600986'])

    def test_a_new_message_runs_the_hook_once(self):
        hook = self.tmp / 'hook'
        out = self.tmp / 'hook.out'
        hook.write_text(f'#!/bin/sh\nprintf "%s|%s|%s|%s\\n" "$SMS_ID" "$SMS_FROM" "$SMS_DATE" "$SMS_TEXT" >> "{out}"\n')
        hook.chmod(0o755)
        for shell in self.each_shell():
            self.fresh()
            out.unlink(missing_ok=True)
            r = self.run_sms(shell, 'sync', MU300_SMS_HOOK=hook)
            self.assertEqual(r.returncode, 0, r.stderr)
            for _ in range(50):
                if out.exists() and len(out.read_text().splitlines()) == 2:
                    break
                time.sleep(0.1)
            got = sorted(out.read_text().splitlines())
            self.assertEqual([g.split('|', 1)[1] for g in got],
                             ['+31641600986|2002-08-26 19:37:41|How are you?', 'ADANA BLD|2026-09-15 17:18:00|Kargonuz yolda ğüş'])
            # a sync that finds nothing new runs it no more; a hook that is not executable is not run
            self.run_sms(shell, 'sync', MU300_SMS_HOOK=hook)
            time.sleep(0.3)
            self.assertEqual(len(out.read_text().splitlines()), 2)

    def test_a_deleted_message_stays_deleted(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')
            ids = {h['from']: i for i, (h, _) in self.msgs().items()}
            r = self.run_sms(shell, 'delete', ids['ADANA BLD'])
            self.assertEqual((r.returncode, r.stdout), (0, f'deleted {ids["ADANA BLD"]}\n'), r.stderr)
            self.run_sms(shell, 'sync')
            self.assertEqual([h['from'] for h, _ in self.msgs().values()], ['+31641600986'])
            # delete itself touches the pool only; a slot that does not read back (no CMGR answer here) stays
            self.assertFalse([c for c in self.at_log() if c.startswith('AT+CMGD')])
            # gone from the SIM too: its tombstone goes with it
            self.answer('CMGL4', f'+CMGL: 3,0,,40\n{PDU_GSM}\nOK\n')
            self.run_sms(shell, 'sync')
            self.assertEqual((self.pool / 'deleted').read_text().count('\n'), 0)

    def test_a_busy_sim_does_not_drop_the_tombstones(self):
        # AT+CPMS="SM" refused (SIM busy) and an empty ME listed instead: that says nothing about the SIM, so the
        # tombstones stay, and the deleted message does not come back on the next good listing
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')
            ids = {h['from']: i for i, (h, _) in self.msgs().items()}
            self.run_sms(shell, 'delete', ids['ADANA BLD'])
            self.answer('CPMSSM', '+CME ERROR: 14\n')            # both forms of AT+CPMS="SM"...
            self.answer('CPMSME', '+CPMS: 0,100,0,100,0,100\nOK\n')
            self.answer('CMGL4', 'OK\n')                         # ...and an empty ME
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('AT+CPMS="ME","ME","ME"', self.at_log())
            self.assertEqual((self.pool / 'deleted').read_text().count('\n'), 1)
            # the SIM answers again
            (self.tmp / 'at' / 'CPMSSM').unlink()
            (self.tmp / 'at' / 'CPMSME').unlink()
            self.answer('CMGL4', CMGL)
            self.run_sms(shell, 'sync')
            self.assertEqual([h['from'] for h, _ in self.msgs().values()], ['+31641600986'])
            # an SM that answers but holds nothing, and an ME that does not answer: not sure either
            self.answer('CPMS', '+CPMS: 0,50,0,50,0,50\nOK\n')
            self.answer('CPMSME', 'ERROR\n')
            self.answer('CMGL4', 'OK\n')
            self.run_sms(shell, 'sync')
            self.assertEqual((self.pool / 'deleted').read_text().count('\n'), 1)
            # both answer and neither holds it: now it is really gone, and so is the tombstone
            self.answer('CPMSME', '+CPMS: 0,100,0,100,0,100\nOK\n')
            self.run_sms(shell, 'sync')
            self.assertEqual((self.pool / 'deleted').read_text().count('\n'), 0)

    def test_delete_sim_removes_the_matching_slot(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')
            ids = {h['from']: i for i, (h, _) in self.msgs().items()}
            # the SIM was re-ordered since the sync: the slot is found by content, not by the index the pool knew
            self.answer('CMGL4', CMGL.replace('+CMGL: 7,', '+CMGL: 9,'))
            r = self.run_sms(shell, 'delete', ids['ADANA BLD'], '--sim')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([c for c in self.at_log() if c.startswith('AT+CMGD')], ['AT+CMGD=9'])
            self.assertEqual(len(self.msgs()), 1)
            r = self.run_sms(shell, 'delete', 'all')
            self.assertEqual((r.returncode, self.msgs()), (0, {}))
            self.assertEqual([c for c in self.at_log() if c.startswith('AT+CMGD')], ['AT+CMGD=9'])

    def test_no_at_daemon_no_modem_traffic(self):
        # without mu300-atd, mu300-at would open the tty itself, which can wedge the channel: refused instead
        for shell in self.each_shell():
            os.unlink(self.atdir / 'cmd')
            for args, stdin in ((('sync',), None), (('send', '--stdin', '123'), 'hi')):
                r = self.run_sms(shell, *args, stdin=stdin)
                self.assertEqual(r.returncode, 1, args)
                self.assertIn('mu300-atd is not running', r.stderr)
            self.assertEqual(self.at_log(), [])
            os.mkfifo(self.atdir / 'cmd')

    # ------------------------------------------------------------------ send
    # an SMSC as the modem shows it under CSCS "HEX" (the hexadecimal of a made-up "+1555010099") and its type
    SMSC_HEX = '"2B31353535303130303939",145'

    def test_send_reads_the_text_on_stdin(self):
        self.answer('CMGS', '+CMGS: 12\nOK\n')
        self.answer('CSCA', f'+CSCA: {self.SMSC_HEX}\nOK\n')
        for shell in self.each_shell():
            for text in ('Merhaba ğüş', '--help', '-x; $(reboot) `id`', 'two\nlines', 'back\\slash \\n'):
                with self.subTest(text=text):
                    (self.tmp / 'at.log').unlink(missing_ok=True)
                    r = self.run_sms(shell, 'send', '--stdin', '+905551112233', stdin=text)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue(r.stdout.endswith('sent (reference 12)\n'), r.stdout)
                    enc = subprocess.run(['awk', '-v', 'mode=encode', '-v', 'number=+905551112233', '-v',
                                          'text=' + text.replace('\\', '\\\\').replace('\n', '\\n'), '-f', str(AWK)],
                                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                         env=dict(os.environ, LC_ALL='C')).stdout.split()
                    # the modem's SMSC written back as it is, PDU mode, then the submission - and nothing else
                    self.assertEqual(self.at_log(), ['AT+CSCA?', f'AT+CSCA={self.SMSC_HEX}', 'AT+CMGF=0',
                                                     f'AT+CMGS={enc[0]}^{enc[1]}~'])
                    if 'ğüş' in text:   # UCS-2 of the letters, also under gawk in a UTF-8 locale
                        self.assertIn('011F00FC015F', enc[1])
                    sent = [(h, b) for h, b in self.msgs().values() if h['dir'] == 'mo']
                    self.assertEqual((sent[-1][0]['to'], sent[-1][0]['status'], sent[-1][1]),
                                     ('+905551112233', 'sent', text + '\n'))

    def test_send_takes_the_text_from_arguments_too(self):
        self.answer('CMGS', '+CMGS: 3\nOK\n')
        for shell in self.each_shell():
            r = self.run_sms(shell, 'send', '10086', '-n', 'hello')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(self.msgs().values())[-1][1], '-n hello\n')

    def test_the_smsc_is_written_back_unchanged_before_a_send(self):
        # The modem's own SMSC is set again, byte for byte as the modem shows it (hexadecimal under CSCS "HEX", or
        # plain) and with its type, before the submission (FINDINGS 46: on one network every send failed until
        # this was done). Never another address, and the character set is never touched.
        self.answer('CMGS', '+CMGS: 2\nOK\n')
        for shell in self.each_shell():
            for smsc in (self.SMSC_HEX, '"+1555010099",145', '"1555010099",129'):
                with self.subTest(smsc=smsc):
                    self.answer('CSCA', f'+CSCA: {smsc}\r\nOK\r\n')
                    (self.tmp / 'at.log').unlink(missing_ok=True)
                    r = self.run_sms(shell, 'send', '--stdin', '10099', stdin='CXYL')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    log = self.at_log()
                    self.assertEqual(log[:3], ['AT+CSCA?', f'AT+CSCA={smsc}', 'AT+CMGF=0'])
                    self.assertTrue(log[3].startswith('AT+CMGS='), log)
                    self.assertEqual(len(log), 4)
                    self.assertNotIn('CSCS', ''.join(log))
                    self.assertNotIn('refresh', r.stderr)

    def test_a_smsc_the_modem_does_not_give_is_left_alone(self):
        # No SMSC, one that cannot be read as "address",type, a query the modem refuses: nothing is written, the send
        # goes on and stderr says so. A write the modem refuses does not stop the send either: the SMSC in use is
        # the same either way, and the submission's own answer is the one that says what is wrong.
        self.answer('CMGS', '+CMGS: 2\nOK\n')
        for shell in self.each_shell():
            for label, csca, written in (('none', 'OK\n', 0), ('error', '+CME ERROR: 3\n', 0),
                                         ('no quotes', '+CSCA: 1555010099,145\nOK\n', 0),
                                         ('no type', '+CSCA: "+1555010099"\nOK\n', 0),
                                         ('empty', '+CSCA: "",145\nOK\n', 0),
                                         ('junk', '+CSCA: "+1555;AT+CFUN=0",145\nOK\n', 0),
                                         ('refused', '+CSCA: "+1555010099",145\nOK\n', 1)):
                with self.subTest(case=label):
                    self.answer('CSCA', csca)
                    # the write ("AT+CSCA=..." - a longer prefix than the query's) refused by the modem
                    (self.tmp / 'at' / 'CSCA1555').unlink(missing_ok=True)
                    if label == 'refused':
                        self.answer('CSCA1555', '+CMS ERROR: 302\n')
                    (self.tmp / 'at.log').unlink(missing_ok=True)
                    r = self.run_sms(shell, 'send', '--stdin', '10099', stdin='hi')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    log = self.at_log()
                    self.assertEqual(log[0], 'AT+CSCA?')
                    self.assertEqual([c for c in log if c.startswith('AT+CSCA=')],
                                     ['AT+CSCA="+1555010099",145'] * written)
                    self.assertEqual(log[-2:-1], ['AT+CMGF=0'])
                    self.assertTrue(log[-1].startswith('AT+CMGS='), log)
                    self.assertIn('refused to refresh' if written else 'without the refresh', r.stderr)
                    self.assertNotIn('AT+CFUN', ''.join(log))
            (self.tmp / 'at' / 'CSCA1555').unlink(missing_ok=True)

    def test_a_send_is_one_transaction_under_the_at_lock(self):
        # The SMSC refresh, PDU mode and the submission go out under one acquisition of mu300-at's client lock
        # ($ATDIR/lock), which the mu300-at calls under it are told they hold (MU300_AT_LOCK_HELD, with the lock's
        # own pid file naming the holder); it is released after the submission, taken or refused.
        self.answer('CSCA', f'+CSCA: {self.SMSC_HEX}\nOK\n')
        for shell in self.each_shell():
            for cmgs in ('+CMGS: 7\nOK\n', '+CMS ERROR: 500\n'):
                with self.subTest(cmgs=cmgs.split('\n')[0]):
                    self.answer('CMGS', cmgs)
                    (self.tmp / 'held.log').unlink(missing_ok=True)
                    r = self.run_sms(shell, 'send', '--stdin', '10099', stdin='hi')
                    self.assertEqual(r.returncode, 0 if '+CMGS' in cmgs else 1, r.stderr)
                    held = [line.split() for line in (self.tmp / 'held.log').read_text().splitlines()]
                    self.assertEqual(len(held), 4, held)
                    pids = {p for pair in held for p in pair}
                    self.assertEqual(len(pids), 1, held)      # one holder, the same for all four, and none "none"
                    self.assertNotIn('none', pids)
                    self.assertFalse((self.atdir / 'lock').exists())

    def test_a_held_at_lock_says_busy_before_anything_is_sent(self):
        # Another client holds mu300-at's lock: busy within the wait (in mu300-at's words, which the panel reads), with
        # not one command sent, and the holder's lock left alone. A holder that is gone is taken over.
        self.answer('CMGS', '+CMGS: 1\nOK\n')
        lock = self.atdir / 'lock'
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGS', '+CMGS: 1\nOK\n')
            self.answer('CSCA', f'+CSCA: {self.SMSC_HEX}\nOK\n')
            holder = subprocess.Popen(['sh', '-c', 'sleep 30; true', 'mu300-at'])
            try:
                lock.mkdir()
                (lock / 'pid').write_text(f'{holder.pid}\n')
                t0 = time.monotonic()
                r = self.run_sms(shell, 'send', '--stdin', '10099', stdin='hi', MU300_AT_LOCK_WAIT='1')
                self.assertEqual((r.returncode, r.stderr.strip()), (1, 'mu300-at: busy'))
                self.assertLess(time.monotonic() - t0, 5)
                self.assertEqual(self.at_log(), [])
                self.assertEqual((lock / 'pid').read_text().strip(), str(holder.pid))
            finally:
                holder.kill()
                holder.wait()
            (lock / 'pid').write_text('999999\n')
            r = self.run_sms(shell, 'send', '--stdin', '10099', stdin='hi', MU300_AT_LOCK_WAIT='1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(self.at_log()), 4)
            self.assertFalse(lock.exists())

    def test_send_refusals_never_read_as_success(self):
        # mu300dash takes any output with "sent" or "OK" in it for success and one with "busy" for a busy channel
        self.answer('CMGS', '+CMS ERROR: 500\n')
        for shell in self.each_shell():
            for args, stdin in ((('--stdin', '-1'), 'hi'), (('--stdin', '+'), 'hi'), (('--stdin', '12+3'), 'hi'),
                                (('--stdin', '1' * 21), 'hi'), (('--stdin', '--stdin'), 'hi'),
                                (('--stdin', '123'), ''), (('--stdin',), 'hi'), (('--stdin', '123'), 'a' * 161),
                                (('--stdin', '123'), 'ğ' * 71), (('--stdin', '123'), 'hi'), ('bare OK', 'hi')):
                with self.subTest(args=args, text=stdin[:8]):
                    if args == 'bare OK':   # the modem says OK without a +CMGS: not taken, and no OK in the refusal
                        self.answer('CMGS', 'OK\n')
                        args = ('--stdin', '123')
                    r = self.run_sms(shell, 'send', *args, stdin=stdin)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    out = r.stdout + r.stderr
                    self.assertNotRegex(out, 'sent|OK|busy')
                    self.assertTrue(r.stderr.startswith('mu300-sms: '), r.stderr)
            self.assertFalse([h for h, _ in self.msgs().values() if h['dir'] == 'mo'])
            # the panel's backend on the same refusal: Sending failed, not Sent
            r = self.dash_send(shell, '123', 'hi')
            self.assertEqual((r['ok'], r['error'], r['done']), (0, 'Sending failed', 1))
            self.assertEqual(r['detail'], 'mu300-sms: the modem did not take the message: no +CMGS confirmation')
            # one part's worth goes out: 160 GSM characters, 70 UCS-2 ones
            self.answer('CMGS', '+CMGS: 1\nOK\n')
            for text in ('a' * 160, 'ğ' * 70):
                self.assertEqual(self.run_sms(shell, 'send', '--stdin', '123', stdin=text).returncode, 0)
            (self.tmp / 'at' / 'CMGS').write_text('+CMS ERROR: 500\n')
            shutil.rmtree(self.pool)

    def stat_tree(self, *dirs):
        """{path: (mtime_ns, ctime_ns, size, mode)} of every file and directory under DIRS, the DIRS included."""
        out = {}
        for d in dirs:
            for p in [d] + sorted(d.rglob('*')):
                st = p.stat()
                out[str(p)] = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode)
        return out

    def test_an_unchanged_sim_writes_nothing_to_the_pool(self):
        # the pool is on flash and smsd syncs every 30 s: a sync of an unchanged SIM, a list, and a show of a read
        # message leave every file and directory of the pool as it was (no lock, no chmod, no tombstone rewrite)
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')
            ids = {h['from']: i for i, (h, _) in self.msgs().items()}
            self.run_sms(shell, 'delete', ids['ADANA BLD'])          # a tombstone that stays (still on the SIM)
            self.run_sms(shell, 'show', ids['+31641600986'])        # read now
            time.sleep(0.05)
            before = self.stat_tree(self.pool)
            time.sleep(0.05)
            for args in (('sync',), ('sync',), ('list',), ('show', ids['+31641600986'])):
                r = self.run_sms(shell, *args)
                self.assertEqual(r.returncode, 0, (args, r.stderr))
            self.assertEqual(self.stat_tree(self.pool), before)
            self.assertEqual(list(self.run.iterdir()), [])          # the lock is in RAM, and released
            # a tombstone that is no longer needed is the one change a sync of a changed SIM makes to the list
            self.answer('CMGL4', f'+CMGL: 3,0,,40\n{PDU_GSM}\nOK\n')
            self.run_sms(shell, 'sync')
            self.assertEqual((self.pool / 'deleted').read_text(), '')

    @unittest.skipUnless(os.path.isdir('/proc/self'), 'a live lock holder is told by /proc/PID/cmdline')
    def test_delete_waits_for_the_pool_lock(self):
        # tombstone and removal under the pool lock: a tombstone written while a sync rewrote the list was lost
        # named the way mu300-sms names it: md5sum where there is one (OpenWrt, Linux), else cksum
        key = subprocess.run(['md5sum' if shutil.which('md5sum') else 'cksum'], input=str(self.pool), capture_output=True,
                             text=True).stdout.split()[0]
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'sync')
            mid = sorted(self.msgs())[0]
            lock = self.run / f'mu300-sms-pool.{key}.lock'
            lock.mkdir()
            # a live holder named mu300-sms ("; true": the shell must not exec sleep and lose its command line)
            holder = subprocess.Popen(['sh', '-c', 'sleep 2; true', 'mu300-sms'])
            (lock / 'pid').write_text(f'{holder.pid}\n')
            try:
                d = subprocess.Popen(shell + [str(SMS), 'delete', mid], env=self.env(
                    MU300_SMS_POOL=self.pool, MU300_SMS_RUN=self.run), stdout=subprocess.PIPE, text=True)
                time.sleep(1)
                self.assertIsNone(d.poll(), 'delete did not wait for the lock')
                self.assertIn(mid, self.msgs())
                holder.wait()
                shutil.rmtree(lock)
                self.assertEqual(d.communicate(timeout=10)[0], f'deleted {mid}\n')
                self.assertNotIn(mid, self.msgs())
                self.assertEqual((self.pool / 'deleted').read_text().count('\n'), 1)
            finally:
                holder.kill()

    def test_a_busy_channel_says_busy(self):
        (self.tmp / 'at' / 'BUSY').write_text('')
        for shell in self.each_shell():
            r = self.run_sms(shell, 'send', '--stdin', '123', stdin='hi')
            self.assertEqual(r.returncode, 1)
            self.assertIn('busy', r.stdout + r.stderr)

    # ------------------------------------------------------------------ the panel on the pool
    def dash_env(self, **env):
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        self.stub('uci', '[ "$3" = unisoc_modem.main.sms_pool ] && [ -n "${UCI_POOL:-}" ] && '
                         'printf "%s\\n" "$UCI_POOL" && exit 0; exit 1')
        self.stub('logger', 'exit 0')
        # setsid: the send's job is started detached; here it is run by hand (dash_send), so nothing starts
        self.stub('setsid', 'exit 0')
        e = self.env(MU300_SMS_BIN=SMS, MU300_SMS_POOL=self.pool, MU300_AT_DIR=self.atdir, MU300_SMS_AWK=AWK,
                     MU300_SMS_RUN=self.run, MU300_DASH_DIR=self.tmp / 'dash', MU300_DASH_BIN=self.tmp / 'none',
                     MU300_DASH_OPS_DIR=self.tmp / 'flash')
        e.update({k: str(v) for k, v in env.items()})
        return {k: v for k, v in e.items() if v != ''}

    def dash(self, shell, method, params, **env):
        r = subprocess.run(shell + [str(DASH), 'call', method], input=json.dumps(params).encode(),
                           capture_output=True, env=self.dash_env(**env), timeout=60)
        return json.loads(r.stdout.decode())

    def dash_send(self, shell, num, text, **env):
        """The panel's send on the real mu300-sms: sms_send starts the job, the job runs here (what setsid would
        have started), and sms_job tells how it went - the reply sms_send used to give, with done:1."""
        r = self.dash(shell, 'sms_send', {'num': num, 'text': text}, **env)
        self.assertEqual((r.get('ok'), r.get('started'), r.get('job')), (1, 1, 1), r)
        j = subprocess.run(shell + [str(DASH), 'sms-job'], capture_output=True, env=self.dash_env(**env), timeout=60)
        self.assertEqual(j.returncode, 0, j.stderr)
        return self.dash(shell, 'sms_job', {}, **env)

    def test_the_panel_reads_the_pool(self):
        for shell in self.each_shell():
            shutil.rmtree(self.pool, ignore_errors=True)
            self.put(1, 'read', 'mt', '+905551112233', '2026-10-01 09:00:00', 'first "quoted" \\ text')
            self.put(2, 'unread', 'mt', 'ADANA BLD', '2026-10-02 10:00:00', 'Kargonuz yolda ğüş\nline two')
            self.assertEqual(self.dash(shell, 'sms_list', {}), {
                'total': 2, 'unread': 1, 'page': 1, 'pages': 1, 'msgs': [
                    {'id': '000002', 'status': 'unread', 'dir': 'mt', 'peer': 'ADANA BLD',
                     'time': '2026-10-02 10:00:00', 'preview': 'Kargonuz yolda ğüş'},
                    {'id': '000001', 'status': 'read', 'dir': 'mt', 'peer': '+905551112233',
                     'time': '2026-10-01 09:00:00', 'preview': 'first "quoted" \\ text'}]})
            r = self.dash(shell, 'sms_show', {'id': '2'})
            self.assertEqual(r['ok'], 1)
            self.assertEqual(r['text'].split('\n---\n', 1)[1], 'Kargonuz yolda ğüş\nline two')
            self.assertEqual(self.dash(shell, 'sms_show', {'id': '9'})['error'], 'Read failed')
            self.answer('CMGS', '+CMGS: 4\nOK\n')
            r = self.dash_send(shell, '10086', '-hi')
            self.assertEqual((r['ok'], r['done'], r['reply'].splitlines()[-1]), (1, 1, 'sent (reference 4)'))
            self.assertEqual(list(self.msgs().values())[-1][1], '-hi\n')
            # the job keeps no copy of the message once it is in the pool
            self.assertFalse((self.tmp / 'dash' / 'sms-job' / 'text').exists())
            (self.tmp / 'at' / 'CMGS').write_text('+CMS ERROR: 500\n')
            r = self.dash_send(shell, '10086', 'hi')
            self.assertEqual((r['ok'], r['error']), (0, 'Sending failed'))
            self.assertIn('+CMS ERROR: 500', r['detail'])
            # how each send went is in the operations log, from the job itself (the call only started it)
            ops = (self.tmp / 'dash' / 'ops.log').read_text()
            self.assertRegex(ops, r'sms_send result \d+\.\d\ds ok\n')
            self.assertRegex(ops, r'sms_send result \d+\.\d\ds error\n')
            self.assertNotIn('-hi', ops)   # never the message, nor the number
            self.assertNotIn('10086', ops)
            self.assertEqual(self.dash(shell, 'sms_delete', {'id': '1'}), {'ok': 1})
            r = self.dash(shell, 'sms_delete', {'id': '1'})
            self.assertEqual((r['error'], r['detail']), ('Delete failed', 'mu300-sms: no message 1'))

    def test_the_panel_never_puts_the_pool_in_the_sms_state_directory(self):
        # the pool the backend hands mu300-sms: the default, or a uci sms_pool under /etc/mu300 - but never
        # /etc/mu300/sms, which is the sms command's own state (D11)
        self.stub('mu300-sms-env', 'printf "%s\\n" "$MU300_SMS_POOL" > "$STUBLOG/pool"; '
                                   'echo "pool: 0 message(s), 0 unread - page 1/1 (10 per page)"')
        for shell in self.each_shell():
            for uci, want in (('', '/etc/mu300/sms-pool'), ('/etc/mu300/sms', '/etc/mu300/sms-pool'),
                              ('/etc/mu300/sms/x', '/etc/mu300/sms-pool'), ('/tmp/x', '/etc/mu300/sms-pool'),
                              ('/etc/mu300/other', '/etc/mu300/other')):
                with self.subTest(uci=uci):
                    (self.tmp / 'pool').unlink(missing_ok=True)
                    r = self.dash(shell, 'sms_list', {}, MU300_SMS_POOL='', MU300_SMS_BIN=self.stubs / 'mu300-sms-env',
                                  UCI_POOL=uci)
                    self.assertEqual(r['total'], 0)
                    self.assertEqual((self.tmp / 'pool').read_text(), want + '\n')

    # ------------------------------------------------------------------ durable before the SIM lets go
    def test_the_pool_is_fsynced_before_any_slot_leaves_the_sim_and_never_globally(self):
        # fsync (busybox's applet on OpenWrt) and a global sync both write into the AT log, so the order is one list
        self.stub('fsync', 'echo "FSYNC $*" >> "$STUBLOG/at.log"')
        self.stub('sync', 'echo "GLOBAL SYNC $*" >> "$STUBLOG/at.log"')
        for shell in self.each_shell():
            self.fresh()
            self.cmgr(3, PDU_GSM)
            self.cmgr(7, PDU_UCS2)
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            log = self.at_log()
            self.assertFalse([l for l in log if l.startswith('GLOBAL SYNC')], log)
            fs = [l for l in log if l.startswith('FSYNC')]
            msg = self.pool / 'msg'
            # each message file before its rename, then the directory entries and next_id
            self.assertEqual(sorted(fs[:2]), [f'FSYNC {msg}/000001.part', f'FSYNC {msg}/000002.part'])
            self.assertEqual(fs[2], f'FSYNC {msg} {self.pool}/next_id {self.pool}')
            self.assertLess(log.index(fs[2]), log.index('AT+CMGD=3'))
            # a sync that takes nothing new syncs nothing
            (self.tmp / 'at.log').unlink()
            self.run_sms(shell, 'sync')
            self.assertFalse([l for l in self.at_log() if 'SYNC' in l])

    def test_without_fsync_coreutils_sync_takes_the_files(self):
        # Ubuntu: no fsync applet, but coreutils' sync FILE...; busybox's sync (no FILE in its help) is never given files
        for shell in self.each_shell():
            for helptext, want in (('Usage: sync [OPTION] [FILE]...', 'SYNC -- '), ('Usage: sync', 'SYNC ')):
                self.fresh()
                self.stub('fsync', 'exit 127')
                self.stub('sync', f'[ "$1" = --help ] && {{ echo "{helptext}"; exit 0; }}; echo "SYNC $*" >> "$STUBLOG/at.log"')
                os.unlink(self.stubs / 'fsync')
                self.cmgr(3, PDU_GSM)
                self.run_sms(shell, 'sync')
                syncs = [l for l in self.at_log() if l.startswith('SYNC')]
                if want == 'SYNC ':
                    self.assertTrue(syncs and all(l == 'SYNC ' for l in syncs), syncs)
                else:
                    self.assertTrue(syncs and all(l.startswith('SYNC -- ') for l in syncs), syncs)

    def test_next_id_survives_an_empty_id_file(self):
        # a power cut that left next_id empty must not start the ids over and overwrite message 1
        for shell in self.each_shell():
            self.fresh()
            self.put(5, 'read', 'mt', '+905551112233', '26/10/01,10:00:00+12', 'old')
            (self.pool / 'next_id').write_text('')
            r = self.run_sms(shell, 'sync')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(self.msgs()), ['000005', '000006', '000007'])
            self.assertEqual(self.msgs()['000005'][1], 'old\n')
            self.assertEqual((self.pool / 'next_id').read_text(), '7\n')
            self.assertFalse((self.pool / 'next_id.part').exists())

    @staticmethod
    def part(ref, total, n, text, at='2002-08-26 19:37:41', header=None):
        """Part N of TOTAL of the long message REF from +31641600986, stamped AT by the service centre (UCS-2);
        HEADER: the user data header's bytes in place of the concatenation element."""
        udh = bytes([5, 0, 3, ref, total, n] if header is None else header)
        ud = udh.hex().upper() + text.encode('utf-16-be').hex().upper()
        scts = ''.join(d[1] + d[0] for d in re.findall('[0-9]{2}', at[2:])) + '48'
        return f'00440B911346610089F60008{scts}{len(ud) // 2:02X}{ud}'

    # ------------------------------------------------------------------ a reference number used again (FINDINGS 63)
    def test_a_second_message_under_a_waiting_reference_is_not_mixed_into_it(self):
        # part 1 of 3 of an old message still waits on the SIM; an hour later the same sender sends a two-part and a
        # three-part message under the same reference number
        old = self.part(0x42, 3, 1, 'old ', at='2026-10-13 08:00:00')
        a1, a2 = (self.part(0x42, 2, 1, 'new ', at='2026-10-13 09:00:01'),
                  self.part(0x42, 2, 2, 'two', at='2026-10-13 09:00:02'))
        b3, b1, b2 = (self.part(0x42, 3, 3, 'three', at='2026-10-13 09:30:02'),
                      self.part(0x42, 3, 1, 'one ', at='2026-10-13 09:30:00'),
                      self.part(0x42, 3, 2, 'two ', at='2026-10-13 09:30:01'))
        slots = ((1, old), (2, a1), (3, a2), (4, b3), (5, b1), (6, b2))
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', ''.join(f'+CMGL: {n},0,,30\n{pdu}\n' for n, pdu in slots) + 'OK\n')
            for n, pdu in slots:
                self.cmgr(n, pdu)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 2 new, 2 on the SIM, 2 in the pool, 1 waiting for its other parts, 5 moved', r.stdout)
            got = sorted((h['scts'], h['sim_index'], body) for h, body in self.msgs().values())
            self.assertEqual(got, [('2026-10-13 09:00:01', '2,3', 'new two\n'),
                                   ('2026-10-13 09:30:00', '4,5,6', 'one two three\n')])
            self.assertEqual(sorted(self.cmgd()), [f'AT+CMGD={n}' for n in (2, 3, 4, 5, 6)])   # the old part stays
            self.assertEqual((self.pool / 'partial').read_text().split('\t')[0],
                             'multi:+31641600986:66:3@2026-10-13 08:00:00')

    def test_a_later_message_under_the_reference_of_one_pooled_incomplete_waits_and_joins(self):
        first = self.part(0x43, 2, 1, 'the start', at='2026-10-12 10:00:00')
        late = self.part(0x43, 2, 2, ' the end', at='2026-10-12 10:00:03')
        n1, n2 = (self.part(0x43, 2, 1, 'another ', at='2026-10-13 10:00:00'),
                  self.part(0x43, 2, 2, 'message', at='2026-10-13 10:00:01'))
        for shell in self.each_shell():
            self.fresh()
            # pooled incomplete (no wait at all)
            self.run_sms(shell, 'import-pdu', self.spooled(first, 'cmt-1.a.ready'), MU300_SMS_PARTIAL_WAIT=0)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], body), ('1/2', 'the start[...]\n'))
            # the rest of that message, late: at once, as before
            r = self.run_sms(shell, 'import-pdu', self.spooled(late, 'cmt-2.a.ready'))
            self.assertIn('import: 1 new, 2 in the pool', r.stdout)
            self.assertNotIn('waiting', r.stdout)
            # the next day, the same reference number: a message of its own, which waits for its second part
            r = self.run_sms(shell, 'import-pdu', self.spooled(n1, 'cmt-3.a.ready'))
            self.assertIn('import: 0 new, 2 in the pool, 1 waiting for its other parts', r.stdout)
            r = self.run_sms(shell, 'import-pdu', self.spooled(n2, 'cmt-4.a.ready'))
            self.assertIn('import: 1 new, 3 in the pool', r.stdout)
            bodies = [body for _, body in self.msgs().values()]
            self.assertEqual(bodies, ['the start[...]\n', '[...] the end\n', 'another message\n'])
            self.assertNotIn('incomplete', self.msgs()['000003'][0])
            self.assertEqual(self.parts(), [])
            self.assertFalse((self.pool / 'partial').exists())

    def test_two_messages_of_one_reference_and_second_stay_two(self):
        # a sender that numbers every long message 0 and sends two in one second: a part number that is taken, with
        # another text, starts the other message
        a1, a2 = self.part(0, 2, 1, 'Dear A, '), self.part(0, 2, 2, 'your code is 1')
        b1, b2 = self.part(0, 2, 1, 'Dear B, '), self.part(0, 2, 2, 'your code is 2')
        for shell in self.each_shell():
            self.fresh()
            for i, pdu in enumerate((a1, b1, a2, b2, a2)):          # the last one a redelivery
                r = self.run_sms(shell, 'import-pdu', self.spooled(pdu, f'cmt-{i}.a.ready'))
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(body for _, body in self.msgs().values()),
                             ['Dear A, your code is 1\n', 'Dear B, your code is 2\n'])
            self.assertEqual(self.parts(), [])

    def test_a_part_delivered_again_is_known_as_part_of_a_message_the_pool_has(self):
        # the acknowledgement of the last part did not reach the network: mu300-smsd imports the file once more, and
        # the network delivers the part again later
        p1, p2 = self.part(0x47, 2, 1, 'hello'), self.part(0x47, 2, 2, ' world')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', 'OK\n')
            self.run_sms(shell, 'import-pdu', self.spooled(p1, 'cmt-1.a.ready'))
            last = self.spooled(p2, 'cmt-2.a.ready')
            self.run_sms(shell, 'import-pdu', last)
            [(h, body)] = self.msgs().values()
            self.assertEqual(body, 'hello world\n')
            self.assertRegex(h['part_ids'], r'^[0-9a-f]{32} [0-9a-f]{32}$|^[0-9]+ [0-9]+$')
            for again in (last, self.spooled(p1, 'cmt-3.a.ready')):
                r = self.run_sms(shell, 'import-pdu', again)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, 'import: 0 new, 1 in the pool'), r.stderr)
                self.assertEqual((len(self.msgs()), self.parts()), (1, []))
            self.assertFalse((self.pool / 'partial').exists())
            # on the SIM (direct delivery refused): the slot is freed
            self.answer('CMGL4', f'+CMGL: 6,0,,30\n{p2}\nOK\n')
            self.cmgr(6, p2)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 0 new, 0 on the SIM, 1 in the pool, 1 moved off the SIM', r.stdout)
            # deleted from the pool, and a part comes again before the next sync: it stays deleted
            self.answer('CMGL4', 'OK\n')
            self.run_sms(shell, 'delete', '1')
            r = self.run_sms(shell, 'import-pdu', self.spooled(p2, 'cmt-4.a.ready'))
            self.assertEqual((r.returncode, len(self.msgs()), self.parts()), (0, 0, []), r.stderr)
            # a message pooled incomplete knows its parts too
            self.fresh()
            self.run_sms(shell, 'import-pdu', self.spooled(p2, 'cmt-5.a.ready'), MU300_SMS_PARTIAL_WAIT=0)
            r = self.run_sms(shell, 'import-pdu', self.spooled(p2, 'cmt-6.a.ready'))
            self.assertEqual((r.stdout.strip(), len(self.msgs()), self.parts()), ('import: 0 new, 1 in the pool', 1, []))

    def test_a_waiting_message_keeps_its_key_and_its_first_seen_time(self):
        # part 2 comes first; part 1, stamped a second earlier, must not start the hour over
        p2, p1 = (self.part(0x46, 3, 2, 'b', at='2026-10-13 10:00:01'),
                  self.part(0x46, 3, 1, 'a', at='2026-10-13 10:00:00'))
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'import-pdu', self.spooled(p2, 'cmt-1.a.ready'))
            key, first, unread = (self.pool / 'partial').read_text().rstrip('\n').split('\t')
            self.assertEqual(key, 'multi:+31641600986:70:3@2026-10-13 10:00:01')
            (self.pool / 'partial').write_text(f'{key}\t{int(first) - 1800}\t{unread}\n')
            self.run_sms(shell, 'import-pdu', self.spooled(p1, 'cmt-2.a.ready'))
            self.assertEqual((self.pool / 'partial').read_text(), f'{key}\t{int(first) - 1800}\t{unread}\n')
            # and the hour ends when it would have
            (self.pool / 'partial').write_text(f'{key}\t{int(first) - 3600}\t{unread}\n')
            self.answer('CMGL4', 'OK\n')
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], h['scts'], body), ('2/3', '2026-10-13 10:00:00', 'ab[...]\n'))

    def test_the_waiting_state_of_the_version_before_is_understood(self):
        # $POOL/partial as it was written before the key carried a time: the message has waited its hour
        p1 = self.part(0x43, 3, 1, 'first ')
        for shell in self.each_shell():
            self.fresh()
            self.run_sms(shell, 'list')                             # makes the pool
            (self.pool / 'partial').write_text(f'multi:+31641600986:67:3\t{int(time.time()) - 3600}\t1\n')
            self.answer('CMGL4', f'+CMGL: 4,1,,30\n{p1}\nOK\n')
            self.cmgr(4, p1)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new, 0 on the SIM, 1 in the pool, 1 moved off the SIM', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], h['status'], body), ('1/3', 'unread', 'first [...][...]\n'))

    def test_a_concatenation_header_that_says_nothing_usable_is_a_message_of_its_own(self):
        # part 0 of 2, part 3 of 2, no parts at all, an element of the wrong length, one cut off by the header's
        # end: none can ever be made whole, so none waits; the text is shown
        cases = {'zero': [5, 0, 3, 9, 2, 0], 'beyond': [5, 0, 3, 9, 2, 3], 'none': [5, 0, 3, 9, 0, 1],
                 'short': [4, 0, 2, 9, 2], 'cut': [4, 0, 3, 9, 2], 'wide': [6, 8, 4, 0, 9, 2, 3]}
        for shell in self.each_shell():
            self.fresh()
            for i, (text, header) in enumerate(cases.items()):
                pdu = self.part(0, 0, 0, text, header=header)
                r = self.run_sms(shell, 'import-pdu', self.spooled(pdu, f'cmt-{i}.a.ready'))
                self.assertEqual(r.returncode, 0, (text, r.stderr))
                self.assertNotIn('waiting', r.stdout, text)
            self.assertEqual([body for _, body in self.msgs().values()], [t + '\n' for t in cases])
            self.assertEqual(self.parts(), [])
            self.assertFalse((self.pool / 'partial').exists())

    def test_parts_that_never_complete_are_pooled_after_the_wait(self):
        p1, p3 = self.part(0x43, 3, 1, 'first '), self.part(0x43, 3, 3, ' third')
        cmgl = f'+CMGL: 4,0,,30\n{p1}\n+CMGL: 5,1,,30\n{p3}\nOK\n'
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', cmgl)
            self.cmgr(4, p1)
            self.cmgr(5, p3)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 0 new, 0 on the SIM, 0 in the pool, 1 waiting for its other parts, 0 moved', r.stdout)
            key, first, unread = (self.pool / 'partial').read_text().rstrip('\n').split('\t')
            self.assertEqual((key, unread), ('multi:+31641600986:67:3@2002-08-26 19:37:41', '1'))
            # unchanged while it waits: the state file is not written again, though the modem now lists the parts
            # as read (AT+CMGL marks what it lists read)
            self.answer('CMGL4', cmgl.replace('+CMGL: 4,0,', '+CMGL: 4,1,'))
            before = self.stat_tree(self.pool)
            time.sleep(0.05)
            self.run_sms(shell, 'sync')
            self.assertEqual(self.stat_tree(self.pool), before)
            # an hour later (the state says so): pooled as it is, unread as first seen, its slots off the SIM, the
            # state gone
            (self.pool / 'partial').write_text(f'{key}\t{int(first) - 3600}\t1\n')
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new, 0 on the SIM, 1 in the pool, 2 moved off the SIM', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], h['sim_index'], h['status'], body),
                             ('2/3', '4,5', 'unread', 'first [...] third\n'))
            self.assertRegex(h['partial'], r'^[0-9a-f]{32}$|^[0-9]+$')
            self.assertFalse((self.pool / 'partial').exists())
            show = self.run_sms(shell, 'show', '1').stdout
            self.assertIn('\nincomplete:  2 of 3 parts arrived\n', show.split('\n---\n')[0] + '\n')
            self.assertEqual(show.split('\n---\n')[1], 'first [...] third\n')
            # the missing part, late: pooled at once, as a message of its own
            p2 = self.part(0x43, 3, 2, 'second')
            self.answer('CMGL4', f'+CMGL: 1,0,,30\n{p2}\nOK\n')
            self.cmgr(1, p2)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new, 0 on the SIM, 2 in the pool, 1 moved off the SIM', r.stdout)
            late = [m for m in self.msgs().values() if m[0]['sim_index'] == '1']
            self.assertEqual((late[0][0]['incomplete'], late[0][1]), ('1/3', '[...]second[...]\n'))

    def test_partial_wait_zero_pools_at_once(self):
        half = self.part(0x44, 2, 2, 'only the end')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', f'+CMGL: 9,1,,30\n{half}\nOK\n')
            r = self.run_sms(shell, 'sync', MU300_SMS_PARTIAL_WAIT=0)
            self.assertIn('1 new', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], h['status'], body), ('1/2', 'read', '[...]only the end\n'))

    def test_new_messages_go_to_the_forwarding(self):
        fwd = self.tmp / 'fwd'
        fwd.write_text('#!/bin/sh\necho "$*" >> "$STUBLOG/fwd.log"\n')
        fwd.chmod(0o755)
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            self.run_sms(shell, 'sync', MU300_SMS_FORWARD=fwd)
            self.run_sms(shell, 'sync', MU300_SMS_FORWARD=fwd)
            self.assertEqual((self.tmp / 'fwd.log').read_text().splitlines(), ['enqueue 000001 000002', 'kick'])

    # ------------------------------------------------------------------ delivered directly (import-pdu)
    def spooled(self, pdu, name='cmt-1.abcdef.ready'):
        """A PDU in the pool's spool, as mu300-cmt-spool writes it."""
        d = self.pool / 'incoming'
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(pdu + '\n')
        return d / name

    def parts(self):
        d = self.pool / 'parts'
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def test_import_pdu_pools_a_direct_message_without_the_modem(self):
        hook = self.tmp / 'hook'
        out = self.tmp / 'hook.out'
        hook.write_text(f'#!/bin/sh\nprintf "%s|%s|%s\\n" "$SMS_FROM" "$SMS_DATE" "$SMS_TEXT" >> "{out}"\n')
        hook.chmod(0o755)
        fwd = self.tmp / 'fwd'
        fwd.write_text('#!/bin/sh\necho "$*" >> "$STUBLOG/fwd.log"\n')
        fwd.chmod(0o755)
        for shell in self.each_shell():
            self.fresh()
            out.unlink(missing_ok=True)
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            f = self.spooled(PDU_GSM)
            r = self.run_sms(shell, 'import-pdu', f, MU300_SMS_HOOK=hook, MU300_SMS_FORWARD=fwd)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('import: 1 new, 1 in the pool', r.stdout)
            self.assertEqual(self.at_log(), [])                     # no AT command at all
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['dir'], h['status'], h['from'], h['scts'], h['sim_index'], h['coding'], body),
                             ('mt', 'unread', '+31641600986', '2002-08-26 19:37:41', '', 'gsm', 'How are you?\n'))
            self.assertTrue(f.exists(), 'the spool file is the daemon\'s to remove, after the acknowledgement')
            self.assertEqual(self.parts(), [])                      # parked, taken, gone
            for _ in range(50):
                if out.exists():
                    break
                time.sleep(0.1)
            self.assertEqual(out.read_text(), '+31641600986|2002-08-26 19:37:41|How are you?\n')
            self.assertEqual((self.tmp / 'fwd.log').read_text().splitlines(), ['enqueue 000001'])
            # the same PDU again (an acknowledgement that failed, and the file imported once more): nothing new
            r = self.run_sms(shell, 'import-pdu', f, MU300_SMS_HOOK=hook, MU300_SMS_FORWARD=fwd)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'import: 0 new, 1 in the pool'), r.stderr)
            self.assertEqual(len(self.msgs()), 1)
            self.assertEqual(self.parts(), [])
            # a message deleted from the pool stays deleted, however often the modem delivers it again
            self.run_sms(shell, 'delete', '1')
            r = self.run_sms(shell, 'import-pdu', f)
            self.assertEqual((r.returncode, len(self.msgs()), self.parts()), (0, 0, []), r.stderr)
            # the panel shows it as not on the SIM
            self.spooled(PDU_UCS2, 'cmt-2.abcdef.ready')
            self.run_sms(shell, 'import-pdu', self.pool / 'incoming' / 'cmt-2.abcdef.ready')
            show = self.run_sms(shell, 'show', '2').stdout
            self.assertIn('\nsim index:   not on the SIM\n', show)
            self.assertIn('\nfrom:        ADANA BLD\n', show)

    def test_import_pdu_joins_the_parts_of_a_long_message(self):
        p1, p2 = self.part(0x42, 2, 1, 'hello'), self.part(0x42, 2, 2, ' world')
        for shell in self.each_shell():
            self.fresh()
            r = self.run_sms(shell, 'import-pdu', self.spooled(p1, 'cmt-1.a.ready'))
            self.assertEqual(r.returncode, 0, r.stderr)          # safe on the disk: the modem may be acknowledged
            self.assertIn('import: 0 new, 0 in the pool, 1 waiting for its other parts', r.stdout)
            self.assertEqual(self.msgs(), {})
            self.assertEqual(len(self.parts()), 1)
            key, _first, unread = (self.pool / 'partial').read_text().rstrip('\n').split('\t')
            self.assertEqual((key, unread), ('multi:+31641600986:66:2@2002-08-26 19:37:41', '1'))
            r = self.run_sms(shell, 'import-pdu', self.spooled(p2, 'cmt-2.a.ready'))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('import: 1 new, 1 in the pool', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['from'], h['sim_index'], h['status'], body), ('+31641600986', '', 'unread', 'hello world\n'))
            self.assertEqual(self.parts(), [])
            self.assertFalse((self.pool / 'partial').exists())
            self.assertEqual(self.at_log(), [])

    def test_a_direct_part_whose_siblings_never_come_is_pooled_by_a_sync(self):
        half = self.part(0x44, 2, 2, 'only the end')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', 'OK\n')                         # nothing on the SIM
            self.run_sms(shell, 'import-pdu', self.spooled(half))
            self.assertEqual(len(self.parts()), 1)
            # a sync in between keeps it waiting (and its first-seen time)
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 0 new, 0 on the SIM, 0 in the pool, 1 waiting for its other parts, 0 moved', r.stdout)
            key, first, unread = (self.pool / 'partial').read_text().rstrip('\n').split('\t')
            self.assertEqual(len(self.parts()), 1)
            # an hour later: pooled as it is by the periodic sync, the parked part gone
            (self.pool / 'partial').write_text(f'{key}\t{int(first) - 3600}\t{unread}\n')
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new, 0 on the SIM, 1 in the pool, 0 moved off the SIM', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['incomplete'], h['sim_index'], body), ('1/2', '', '[...]only the end\n'))
            self.assertEqual(self.parts(), [])
            self.assertFalse([c for c in self.at_log() if c.startswith('AT+CMGD') or c.startswith('AT+CMGR')])
            # with no wait at all, import-pdu pools it at once
            self.fresh()
            r = self.run_sms(shell, 'import-pdu', self.spooled(half), MU300_SMS_PARTIAL_WAIT=0)
            self.assertIn('import: 1 new, 1 in the pool', r.stdout)
            self.assertEqual(self.parts(), [])

    def test_the_parts_of_one_message_join_across_the_sim_and_the_air(self):
        # a class 2 part on the SIM and its sibling delivered directly are one message
        p1, p2 = self.part(0x45, 2, 1, 'sim'), self.part(0x45, 2, 2, ' and air')
        for shell in self.each_shell():
            self.fresh()
            self.answer('CMGL4', f'+CMGL: 3,0,,30\n{p1}\nOK\n')
            self.cmgr(3, p1)
            self.run_sms(shell, 'import-pdu', self.spooled(p2))
            r = self.run_sms(shell, 'sync')
            self.assertIn('sync: 1 new, 1 on the SIM, 1 in the pool, 1 moved off the SIM', r.stdout)
            [(h, body)] = self.msgs().values()
            self.assertEqual((h['sim_index'], body), ('3', 'sim and air\n'))
            self.assertEqual(self.cmgd(), ['AT+CMGD=3'])
            self.assertEqual(self.parts(), [])

    def test_import_pdu_refuses_what_is_not_a_message(self):
        for shell in self.each_shell():
            for bad in ('', 'zz', 'ABC', '00', 'ABCD', '07911326040000F0', 'F' * 1026):
                with self.subTest(pdu=bad[:16]):
                    self.fresh()
                    f = self.spooled(bad)
                    r = self.run_sms(shell, 'import-pdu', f)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    self.assertIn('mu300-sms:', r.stderr)
                    self.assertFalse((self.pool / 'msg').exists(), 'the pool was touched')
                    self.assertFalse((self.pool / 'parts').exists(), 'the PDU was parked')
                    self.assertTrue(f.exists(), 'the spool file is not this command\'s to remove')
            r = self.run_sms(shell, 'import-pdu', self.tmp / 'nowhere')
            self.assertEqual(r.returncode, 1)
            r = self.run_sms(shell, 'import-pdu')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.at_log(), [])

    def test_a_parked_part_is_on_the_disk_before_the_pool_is_touched(self):
        self.stub('fsync', 'echo "FSYNC $*" >> "$STUBLOG/at.log"')
        self.stub('sync', 'echo "GLOBAL SYNC $*" >> "$STUBLOG/at.log"')
        for shell in self.each_shell():
            self.fresh()
            r = self.run_sms(shell, 'import-pdu', self.spooled(PDU_GSM))
            self.assertEqual(r.returncode, 0, r.stderr)
            log = self.at_log()
            self.assertFalse([l for l in log if l.startswith('GLOBAL SYNC')], log)
            parts, msg = self.pool / 'parts', self.pool / 'msg'
            self.assertRegex(log[0], rf'^FSYNC {re.escape(str(parts))}/\.part\.[A-Za-z0-9]+$')   # mktemp's name, then the final one
            self.assertEqual(log[1], f'FSYNC {parts}')
            self.assertEqual(log[2], f'FSYNC {msg}/000001.part')
            self.assertEqual(log[3], f'FSYNC {msg} {self.pool}/next_id {self.pool}')

    def test_import_pdu_follows_no_link(self):
        """import-pdu runs as root: a spool entry that is a link, a parts directory or a pool that is one, are refused
        and nothing is written through them."""
        for shell in self.each_shell():
            # the spool entry
            self.fresh()
            target = self.tmp / 'elsewhere.pdu'
            target.write_text(PDU_GSM + '\n')
            (self.pool / 'incoming').mkdir(parents=True)
            link = self.pool / 'incoming' / 'cmt-1.link.ready'
            link.symlink_to(target)
            r = self.run_sms(shell, 'import-pdu', link)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn('symbolic link', r.stderr)
            self.assertFalse((self.pool / 'msg').exists())
            # the parts directory, pointing somewhere else
            self.fresh()
            elsewhere = self.tmp / 'elsewhere'
            elsewhere.mkdir(exist_ok=True)
            self.pool.mkdir()
            (self.pool / 'parts').symlink_to(elsewhere)
            r = self.run_sms(shell, 'import-pdu', self.spooled(PDU_GSM))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn('symbolic link', r.stderr)
            self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [])
            self.assertFalse((self.pool / 'msg').exists())
            # the pool itself
            self.fresh()
            shutil.rmtree(elsewhere, ignore_errors=True); elsewhere.mkdir()
            self.pool.symlink_to(elsewhere)
            f = self.tmp / 'spooled.ready'
            f.write_text(PDU_GSM + '\n')
            r = self.run_sms(shell, 'import-pdu', f)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [])
            self.pool.unlink()
            # a link among the parked parts is never read; a parts directory that is not mode 700 is made so
            self.fresh()
            (self.pool / 'parts').mkdir(parents=True, mode=0o755)
            (self.pool / 'parts' / 'plink').symlink_to(target)
            r = self.run_sms(shell, 'import-pdu', self.spooled(PDU_UCS2))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([h['from'] for h, _ in self.msgs().values()], ['ADANA BLD'])
            self.assertEqual((self.pool / 'parts').stat().st_mode & 0o777, 0o700)
            self.assertTrue((self.pool / 'parts' / 'plink').is_symlink())


class Spool(ShellTest):
    """mu300-cmt-spool (every system): the two lines of a +CMT frame on stdin, the PDU into the pool's spool."""

    SPOOLER = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-cmt-spool'

    def setUp(self):
        super().setUp()
        self.pool = self.tmp / 'pool'
        self.stub('fsync', 'echo "FSYNC $*" >> "$STUBLOG/fs.log"')
        self.stub('sync', 'echo "GLOBAL SYNC $*" >> "$STUBLOG/fs.log"')

    def spool(self, shell, frame):
        shutil.rmtree(self.pool, ignore_errors=True)
        (self.tmp / 'fs.log').unlink(missing_ok=True)
        return self.script(shell, self.SPOOLER, stdin=frame, MU300_SMS_POOL=self.pool)

    def files(self):
        d = self.pool / 'incoming'
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def test_a_frame_is_written_whole_fsynced_and_root_only(self):
        for shell in self.each_shell():
            for header in ('+CMT: ,40', '+CMT: "ADANA BLD",56', '+CMT: ,40\r'):
                with self.subTest(header=header):
                    r = self.spool(shell, f'{header}\n{PDU_GSM}\r\n')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    [name] = self.files()
                    self.assertRegex(name, r'^cmt-[0-9]+\.[A-Za-z0-9]+\.ready$')
                    f = self.pool / 'incoming' / name
                    self.assertEqual(f.read_text(), PDU_GSM + '\n')
                    self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                    self.assertEqual((self.pool / 'incoming').stat().st_mode & 0o777, 0o700)
                    fs = (self.tmp / 'fs.log').read_text().splitlines()
                    # the file (under its name before the rename), then the directory entry; never everything
                    self.assertEqual(fs, [f'FSYNC {str(f)[:-len(".ready")]}', f'FSYNC {self.pool}/incoming'])

    def test_anything_but_a_message_is_refused_and_nothing_written(self):
        for shell in self.each_shell():
            for frame in (f'+CMTI: "SM",4\n{PDU_GSM}\n', f'+CMT: ,40\n{PDU_GSM[:-1]}\n', '+CMT: ,40\nnot hex\n',
                          '+CMT: ,40\n\n', '+CMT: ,40\n', '', f'{PDU_GSM}\n+CMT: ,40\n', '+CMT: ,40\n' + 'A' * 1026 + '\n'):
                with self.subTest(frame=frame[:24]):
                    r = self.spool(shell, frame)
                    self.assertEqual(r.returncode, 1)
                    self.assertEqual(self.files(), [])
                    self.assertFalse((self.tmp / 'fs.log').exists())

    def test_a_spool_or_pool_that_is_a_link_is_refused(self):
        for shell in self.each_shell():
            elsewhere = self.tmp / 'elsewhere'
            shutil.rmtree(elsewhere, ignore_errors=True); elsewhere.mkdir()
            shutil.rmtree(self.pool, ignore_errors=True)
            self.pool.mkdir(mode=0o700)
            (self.pool / 'incoming').symlink_to(elsewhere)
            r = self.script(shell, self.SPOOLER, stdin=f'+CMT: ,40\n{PDU_GSM}\n', MU300_SMS_POOL=self.pool)
            self.assertEqual(r.returncode, 1)
            self.assertIn('symbolic link', r.stderr)
            self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [])
            shutil.rmtree(self.pool)
            self.pool.symlink_to(elsewhere)
            r = self.script(shell, self.SPOOLER, stdin=f'+CMT: ,40\n{PDU_GSM}\n', MU300_SMS_POOL=self.pool)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(sorted(x.name for x in elsewhere.iterdir()), [])
            self.pool.unlink()
            # a pool somebody left mode 755 is made 700, and the spool is written
            self.pool.mkdir(mode=0o755)
            r = self.script(shell, self.SPOOLER, stdin=f'+CMT: ,40\n{PDU_GSM}\n', MU300_SMS_POOL=self.pool)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.pool.stat().st_mode & 0o777, 0o700)
            self.assertEqual(len(self.files()), 1)

    def test_two_frames_in_one_second_are_two_files(self):
        for shell in self.each_shell():
            self.spool(shell, f'+CMT: ,40\n{PDU_GSM}\n')
            self.script(shell, self.SPOOLER, stdin=f'+CMT: ,56\n{PDU_UCS2}\n', MU300_SMS_POOL=self.pool)
            self.assertEqual(len(self.files()), 2)


class Codec(ShellTest):
    """sms-pdu.awk's group and near modes: which decoded records are the parts of one long message."""

    def awk(self, *args, stdin=''):
        return subprocess.run(['awk', *args, '-f', str(AWK)], input=stdin, capture_output=True, text=True, timeout=30,
                              env=self.env(LC_ALL='C'))

    def group(self, rows, known=None):
        """rows: (sender, timestamp, ref, total, part, text) -> the key of each, in order."""
        args = ['-v', 'mode=group']
        if known is not None:
            (self.tmp / 'known').write_text(known)
            args += ['-v', f'known={self.tmp / "known"}']
        text = ''.join(f'{i}\t' + '\t'.join(str(f) for f in row) + '\n' for i, row in enumerate(rows))
        r = self.awk(*args, stdin=text)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        out = [line.split('\t') for line in r.stdout.splitlines()]
        self.assertEqual(['\t'.join(f[:7]) + '\n' for f in out], text.splitlines(True))   # the records as they came
        return [f[7] for f in out]

    def test_near_is_ten_minutes_on_the_calendar(self):
        import datetime
        fmt = '%Y-%m-%d %H:%M:%S'
        for a, seconds in (('2026-10-13 09:41:07', 600), ('2026-10-13 23:55:00', 600), ('2026-12-31 23:59:59', 1),
                           ('2024-02-28 23:59:00', 600), ('2024-02-29 23:59:00', 600), ('2025-02-28 23:59:00', 600),
                           ('2000-01-01 00:00:00', 0), ('2099-12-31 23:50:00', 599)):
            t = datetime.datetime.strptime(a, fmt)
            for delta, near in ((seconds, True), (-seconds, True), (601, False), (-601, False), (86400, False)):
                try:
                    b = (t + datetime.timedelta(seconds=delta)).strftime(fmt)
                except OverflowError:
                    continue
                if not b.startswith('20'):
                    continue
                r = self.awk('-v', 'mode=near', '-v', f'a={a}', '-v', f'b={b}')
                self.assertEqual(r.returncode == 0, near, (a, b, r.stderr))
        # a timestamp that cannot be read is near everything, and decides nothing
        for bad in ('', '2026-13-01 00:00:00', '20:6-10-13 09:41:07', 'x'):
            self.assertEqual(self.awk('-v', 'mode=near', '-v', f'a={bad}', '-v', 'b=2026-10-13 09:41:07').returncode, 0)

    def test_groups(self):
        t = '2026-10-13 09:00:0'
        self.assertEqual(self.group([
            ('A', t + '1', 7, 2, 1, 'a1'), ('B', t + '1', 7, 2, 1, 'b1'),      # another sender
            ('A', t + '2', '', 1, 1, 'single'),
            ('A', t + '2', 7, 3, 1, 'c1'),                                      # another total
            ('A', t + '3', 7, 2, 2, 'a2'), ('A', t + '3', 7, 2, 2, 'a2'),      # the second part, twice
            ('A', '2026-10-13 09:10:02', 7, 2, 1, 'd1'),                        # ten minutes and a second later
            ('A', t + '0', 7, 2, 1, 'e1'),                                      # part 1 again, another text
        ]), ['multi:A:7:2@' + t + '1', 'multi:B:7:2@' + t + '1', 'single:3', 'multi:A:7:3@' + t + '2',
             'multi:A:7:2@' + t + '1', 'multi:A:7:2@' + t + '1', 'multi:A:7:2@2026-10-13 09:10:02',
             'multi:A:7:2@' + t + '0'])
        # one second, one reference, two messages: two keys
        self.assertEqual(self.group([('A', t + '1', 0, 2, 1, 'x'), ('A', t + '1', 0, 2, 1, 'y')]),
                         ['multi:A:0:2@' + t + '1', 'multi:A:0:2@' + t + '1#2'])
        # a sender with every character a key is made of
        self.assertEqual(self.group([('a:b@c#2', t + '1', 1, 2, 1, 'x')]), ['multi:a:b@c#2:1:2@' + t + '1'])

    def test_a_known_key_is_kept(self):
        t = '2026-10-13 09:00:0'
        rows = [('A', t + '5', 7, 2, 2, 'a2'), ('A', t + '1', 7, 2, 1, 'a1')]
        self.assertEqual(self.group(rows), ['multi:A:7:2@' + t + '1'] * 2)
        self.assertEqual(self.group(rows, known=f'multi:A:7:2@{t}5\t1700000000\t1\n'), ['multi:A:7:2@' + t + '5'] * 2)
        # not the key of another time, sender or total; a key of the format before fits any time, once
        self.assertEqual(self.group(rows, known='multi:A:7:2@2026-10-12 09:00:05\t1\nmulti:B:7:2@' + t + '5\t1\n'
                                                'multi:A:7:3@' + t + '5\t1\n'), ['multi:A:7:2@' + t + '1'] * 2)
        two = rows + [('A', '2026-10-14 09:00:00', 7, 2, 1, 'later')]
        self.assertEqual(self.group(two, known='multi:A:7:2\t1\t1\n'),
                         ['multi:A:7:2', 'multi:A:7:2', 'multi:A:7:2@2026-10-14 09:00:00'])
        # a state file that is not there, or holds something else
        self.assertEqual(self.group(rows, known='garbage\n\n'), ['multi:A:7:2@' + t + '1'] * 2)
        r = self.awk('-v', 'mode=group', '-v', f'known={self.tmp}/none', stdin='0\tA\t' + t + '1\t7\t2\t1\tx\n')
        self.assertEqual((r.returncode, r.stdout), (0, '0\tA\t' + t + '1\t7\t2\t1\tx\tmulti:A:7:2@' + t + '1\n'))


class Listing(ShellTest):
    """`sms json` / `sms list` (every system): the SIM keeps messages until they are deleted, so two long messages of
    one sender under one reference number are both there; each is shown whole (FINDINGS 63)."""

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        (self.tmp / 'at' / 'CPMS').write_text('+CPMS: 4,50,4,50,4,50\nOK\n')

    def test_two_messages_under_one_reference_are_two(self):
        part = Pool.part
        pdus = (part(0x42, 2, 1, 'monday ', at='2026-10-12 08:00:00'), part(0x42, 2, 2, 'morning', at='2026-10-12 08:00:01'),
                part(0x42, 2, 2, 'evening', at='2026-10-13 20:00:01'), part(0x42, 2, 1, 'tuesday ', at='2026-10-13 20:00:00'))
        (self.tmp / 'at' / 'CMGL4').write_text(''.join(f'+CMGL: {i + 1},1,,30\n{p}\n' for i, p in enumerate(pdus)) + 'OK\n')
        for shell in self.each_shell():
            r = subprocess.run(shell + [str(TOP / 'rootfs/overlay/opt/mu300/bin/sms'), 'json'],
                               capture_output=True, text=True, timeout=30,
                               env=self.env(MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state'))
            self.assertEqual(r.returncode, 0, r.stderr)
            got = [(m['index'], m['time'], m['parts'], m['have'], m['text']) for m in json.loads(r.stdout)['messages']]
            self.assertEqual(got, [([1, 2], '2026-10-12 08:00:00', 2, 2, 'monday morning'),
                                   ([3, 4], '2026-10-13 20:00:01', 2, 2, 'tuesday evening')])


class Delete(ShellTest):
    """`sms delete` (every system): only a message index, read or all reach the modem."""

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        (self.tmp / 'at' / 'CPMS').write_text('+CPMS: 2,50,2,50,2,50\nOK\n')

    def test_anything_but_an_index_is_refused_before_the_modem(self):
        for shell in self.each_shell():
            for bad in ('1;AT+CFUN=0', '1,4', '-1', '1 2', 'x', '1\rAT'):
                (self.tmp / 'at.log').unlink(missing_ok=True)
                r = subprocess.run(shell + [str(TOP / 'rootfs/overlay/opt/mu300/bin/sms'), 'delete', bad],
                                   capture_output=True, text=True, timeout=30,
                                   env=self.env(MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state'))
                self.assertEqual(r.returncode, 2, (bad, r.stderr))
                log = (self.tmp / 'at.log').read_text() if (self.tmp / 'at.log').exists() else ''
                self.assertNotIn('CMGD', log, bad)
            r = subprocess.run(shell + [str(TOP / 'rootfs/overlay/opt/mu300/bin/sms'), 'delete', '7'],
                               capture_output=True, text=True, timeout=30,
                               env=self.env(MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state'))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('AT+CMGD=7', (self.tmp / 'at.log').read_text())


class Send(ShellTest):
    """`sms send` (every system, its own send path): one transaction under mu300-at's client lock, the modem's SMSC
    written back unchanged first, and the submission as the one request mu300-sms sends (FINDINGS 46)."""

    SMS_CMD = TOP / 'rootfs/overlay/opt/mu300/bin/sms'

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        self.atdir = self.tmp / 'run-at'
        self.atdir.mkdir()
        os.mkfifo(self.atdir / 'cmd')
        self.answer('CMGS', '+CMGS: 12\nOK\n')
        self.answer('CSCA', f'+CSCA: {Pool.SMSC_HEX}\nOK\n')

    def answer(self, name, text):
        (self.tmp / 'at' / name).write_text(text)

    def at_log(self):
        p = self.tmp / 'at.log'
        return p.read_text().splitlines() if p.exists() else []

    def held(self):
        p = self.tmp / 'held.log'
        return [line.split() for line in p.read_text().splitlines()] if p.exists() else []

    def send(self, shell, *args, **env):
        (self.tmp / 'at.log').unlink(missing_ok=True)
        (self.tmp / 'held.log').unlink(missing_ok=True)
        return self.script(shell, self.SMS_CMD, 'send', *args,
                           MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state', MU300_AT_DIR=self.atdir, **env)

    def test_a_send_is_one_transaction_under_the_at_lock(self):
        # the SMSC refresh, PDU mode and the submission (one request: AT+CMGS=N, CR, the PDU, Ctrl-Z), under one
        # acquisition of $ATDIR/lock that the mu300-at calls are told they hold; released afterwards, sent or refused
        for shell in self.each_shell():
            for cmgs, rc in (('+CMGS: 12\nOK\n', 0), ('+CMS ERROR: 500\n', 1), ('OK\n', 1)):
                with self.subTest(cmgs=cmgs.split('\n')[0]):
                    self.answer('CMGS', cmgs)
                    r = self.send(shell, '+905551112233', 'Merhaba', 'ğüş')
                    self.assertEqual(r.returncode, rc, r.stderr)
                    enc = subprocess.run(['awk', '-v', 'mode=encode', '-v', 'number=+905551112233', '-v',
                                          'text=Merhaba ğüş', '-f', str(AWK)], capture_output=True, text=True,
                                         stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL='C')).stdout.split()
                    self.assertEqual(self.at_log(), ['AT+CSCA?', f'AT+CSCA={Pool.SMSC_HEX}', 'AT+CMGF=0',
                                                     f'AT+CMGS={enc[0]}^{enc[1]}~'])
                    if rc == 0:
                        self.assertEqual(r.stdout, 'sent (reference 12)\n')
                    else:
                        self.assertTrue(r.stderr.startswith('sms: '), r.stderr)
                        self.assertNotIn('sent', r.stdout)
                    held = self.held()
                    self.assertEqual(len(held), 4, held)
                    pids = {p for pair in held for p in pair}
                    self.assertEqual(len(pids), 1, held)      # one holder, the same for all four, and none "none"
                    self.assertNotIn('none', pids)
                    self.assertFalse((self.atdir / 'lock').exists())
            self.answer('CMGS', '+CMGS: 12\nOK\n')

    def test_stdin_and_the_encoder_never_see_the_message_in_arguments(self):
        # send --stdin: the number on the first line, the text after it; the PDU encoder gets both in its environment
        # (an awk -v argument is visible to anyone who lists the processes), so a backslash stays a backslash
        self.stub('awk', 'printf "%s\\n" "$*" >> "$STUBLOG/awk-argv.log"; exec ' + shutil.which('awk') + ' "$@"')
        for shell in self.each_shell():
            (self.tmp / 'awk-argv.log').unlink(missing_ok=True)
            r = self.script(shell, self.SMS_CMD, 'send', '--stdin', stdin='+905551112233\nKod 4321 a\\nb\nsatır',
                            MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state', MU300_AT_DIR=self.atdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            argv = (self.tmp / 'awk-argv.log').read_text()
            self.assertNotIn('4321', argv)
            self.assertNotIn('905551112233', argv)
            enc = subprocess.run(['awk', '-v', 'mode=encode', '-f', str(AWK)], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL='C', MU300_PDU_NUMBER='+905551112233',
                                                                    MU300_PDU_TEXT='Kod 4321 a\\nb\nsatır')).stdout.split()
            self.assertEqual(self.at_log()[-1], f'AT+CMGS={enc[0]}^{enc[1]}~')
            self.assertEqual(self.send(shell, '--stdin', '123', stdin='x').returncode, 2)

    def test_a_smsc_the_modem_does_not_give_is_left_alone(self):
        # nothing is written when the SMSC cannot be read as "address",type; a write the modem refuses does not stop
        # the send; the character set is never touched
        for shell in self.each_shell():
            for label, csca, written in (('none', 'OK\n', 0), ('error', '+CME ERROR: 3\n', 0),
                                         ('no quotes', '+CSCA: 1555010099,145\nOK\n', 0),
                                         ('junk', '+CSCA: "+1555;AT+CFUN=0",145\nOK\n', 0),
                                         ('plain', '+CSCA: "+1555010099",145\nOK\n', 1),
                                         ('refused', '+CSCA: "+1555010099",145\nOK\n', 1)):
                with self.subTest(case=label):
                    self.answer('CSCA', csca)
                    (self.tmp / 'at' / 'CSCA1555').unlink(missing_ok=True)
                    if label == 'refused':
                        self.answer('CSCA1555', '+CMS ERROR: 302\n')
                    r = self.send(shell, '10099', 'hi')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    log = self.at_log()
                    self.assertEqual(log[0], 'AT+CSCA?')
                    self.assertEqual([c for c in log if c.startswith('AT+CSCA=')], ['AT+CSCA="+1555010099",145'] * written)
                    self.assertEqual(log[-2:-1], ['AT+CMGF=0'])
                    self.assertTrue(log[-1].startswith('AT+CMGS='), log)
                    self.assertNotIn('CSCS', ''.join(log))
                    self.assertNotIn('AT+CFUN', ''.join(log))
                    if label == 'refused':
                        self.assertIn('sms: the modem refused to refresh', r.stderr)
                    elif written:
                        self.assertEqual(r.stderr, '')
                    else:
                        self.assertIn('without the refresh', r.stderr)
            (self.tmp / 'at' / 'CSCA1555').unlink(missing_ok=True)
            self.answer('CSCA', f'+CSCA: {Pool.SMSC_HEX}\nOK\n')

    def test_a_held_at_lock_says_busy_before_anything_is_sent(self):
        # a live holder of mu300-at's lock: busy within the wait, nothing sent, the holder's lock left alone; a
        # holder that is gone is taken over
        lock = self.atdir / 'lock'
        for shell in self.each_shell():
            holder = subprocess.Popen(['sh', '-c', 'sleep 30; true', 'mu300-at'])
            try:
                lock.mkdir()
                (lock / 'pid').write_text(f'{holder.pid}\n')
                t0 = time.monotonic()
                r = self.send(shell, '10099', 'hi', MU300_AT_LOCK_WAIT='1')
                self.assertEqual((r.returncode, r.stderr.strip()), (1, 'mu300-at: busy'))
                self.assertLess(time.monotonic() - t0, 5)
                self.assertEqual(self.at_log(), [])
                self.assertEqual((lock / 'pid').read_text().strip(), str(holder.pid))
            finally:
                holder.kill()
                holder.wait()
            (lock / 'pid').write_text('999999\n')
            r = self.send(shell, '10099', 'hi', MU300_AT_LOCK_WAIT='1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(self.at_log()), 4)
            self.assertFalse(lock.exists())

    def test_no_daemon_no_lock(self):
        # without mu300-atd's fifo mu300-at opens the tty itself and takes no lock: none is taken here either, and the
        # send still goes the same way
        (self.atdir / 'cmd').unlink()
        for shell in self.each_shell():
            r = self.send(shell, '10099', 'hi')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(self.at_log()), 4)
            self.assertEqual(self.held(), [['none', 'none']] * 4)
            self.assertFalse((self.atdir / 'lock').exists())


class Watch(ShellTest):
    """`sms watch` (every system): a long message waits for its other parts, MU300_SMS_PARTIAL_WAIT at most."""

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        (self.tmp / 'at' / 'CPMS').write_text('+CPMS: 2,50,2,50,2,50\nOK\n')
        # one round of the loop: the second sleep ends the script
        self.stub('sleep', 'n=$(cat "$STUBLOG/rounds" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STUBLOG/rounds"; '
                           '[ "$n" -ge "${ROUNDS:-1}" ] && kill $PPID; exit 0')

    def watch(self, shell, cmgl, **env):
        (self.tmp / 'at' / 'CMGL4').write_text(cmgl)
        (self.tmp / 'rounds').unlink(missing_ok=True)
        hook = f'printf "%s|%s\\n" "$SMS_FROM" "$SMS_TEXT" >> "{self.tmp}/hook.out"'
        # A busybox that runs sleep as its own applet never calls the stub, so the loop sleeps for real: one round
        # takes well under the timeout, which then ends it.
        try:
            return subprocess.run(shell + [str(TOP / 'rootfs/overlay/opt/mu300/bin/sms'), 'watch', hook],
                                  capture_output=True, text=True, timeout=8,
                                  env=self.env(MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state', **env))
        except subprocess.TimeoutExpired as e:
            return e

    def test_an_incomplete_message_waits_then_is_reported(self):
        part = Pool.part
        p1 = part(0x45, 2, 1, 'hello')
        for shell in self.each_shell():
            for f in ('hook.out', 'state'):
                subprocess.run(['rm', '-rf', str(self.tmp / f)])
            self.watch(shell, f'+CMGL: 1,0,,30\n{p1}\nOK\n')
            time.sleep(0.3)
            self.assertFalse((self.tmp / 'hook.out').exists())
            key, first = (self.tmp / 'state' / 'partial').read_text().rstrip('\n').split('\t')
            (self.tmp / 'state' / 'partial').write_text(f'{key}\t{int(first) - 3600}\n')
            self.watch(shell, f'+CMGL: 1,0,,30\n{p1}\nOK\n')
            for _ in range(30):
                if (self.tmp / 'hook.out').exists():
                    break
                time.sleep(0.1)
            self.assertEqual((self.tmp / 'hook.out').read_text(), '+31641600986|hello[missing part 2]\n')
            # the whole message, once the rest comes: reported again, and the waiting state is gone
            p2 = part(0x45, 2, 2, ' world')
            self.watch(shell, f'+CMGL: 1,0,,30\n{p1}\n+CMGL: 2,0,,30\n{p2}\nOK\n')
            for _ in range(30):
                if len((self.tmp / 'hook.out').read_text().splitlines()) == 2:
                    break
                time.sleep(0.1)
            self.assertEqual((self.tmp / 'hook.out').read_text().splitlines()[1], '+31641600986|hello world')
            self.assertFalse((self.tmp / 'state' / 'partial').exists() and
                             (self.tmp / 'state' / 'partial').read_text())


@unittest.skipUnless(os.path.isdir('/proc/self'), 'mu300-smsd tells a live holder by /proc/PID/cmdline')
class Daemon(ShellTest):
    def setUp(self):
        super().setUp()
        self.atdir = self.tmp / 'run-at'
        (self.atdir / 'urc').mkdir(parents=True)
        os.mkfifo(self.atdir / 'cmd')
        # The stub modem: every command into at.log (and the stub mu300-sms's calls too, as "SMS ...", so the order
        # between the two is one list); OK to everything but the commands $STUBLOG/refuse names (ERROR), +CPIN: READY,
        # and +CNMI? answered from $STUBLOG/cnmi (what the daemon asked for, by default).
        self.stub('mu300-at', 'printf "%s\\n" "$*" >> "$STUBLOG/at.log"; cmd=$3\n'
                              'for r in $(cat "$STUBLOG/refuse" 2>/dev/null); do case $cmd in "$r"*) echo ERROR; exit 0 ;; esac; done\n'
                              'case $cmd in\n'
                              '    *CPIN*) printf "+CPIN: READY\\nOK\\n" ;;\n'
                              '    "AT+CNMI?") cat "$STUBLOG/cnmi" 2>/dev/null || echo "+CNMI: 3,2,2,0,1"; echo OK ;;\n'
                              '    *) echo OK ;;\n'
                              'esac')
        self.stub('fake-sms', 'echo "$*" >> "$STUBLOG/sms.log"; echo "SMS $*" >> "$STUBLOG/at.log"; '
                              'echo "${MU300_AT_LOCK_WAIT:-unset}" > "$STUBLOG/lockwait"; '
                              '[ -f "$STUBLOG/sms-fail" ] && [ "$1" = import-pdu ] && exit 1; exit 0')
        self.lock = self.tmp / 'smsd.lock'
        self.flag = self.tmp / 'direct'
        self.pool = self.tmp / 'pool'
        self.spool = self.pool / 'incoming'

    def start(self, shell, **env):
        e = dict(MU300_AT_DIR=self.atdir, MU300_SMS_BIN=self.stubs / 'fake-sms', MU300_SMS_LOCK=self.lock,
                 MU300_SMS_POLL=1, MU300_SMS_SYNC_INTERVAL=600, MU300_SMS_POOL=self.pool,
                 MU300_SMS_DIRECT_FLAG=self.flag)
        e.update(env)
        return subprocess.Popen(shell + [str(SMSD)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.env(**e))

    def syncs(self):
        p = self.tmp / 'sms.log'
        return p.read_text().splitlines() if p.exists() else []

    def at_log(self):
        p = self.tmp / 'at.log'
        return p.read_text().splitlines() if p.exists() else []

    def commands(self):
        """The AT commands asked so far (the stub mu300-sms's lines left out)."""
        return [l.split(' ', 2)[2] for l in self.at_log() if l.startswith('-t ')]

    def fresh(self):
        for f in ('sms.log', 'at.log', 'refuse', 'cnmi', 'sms-fail', 'lockwait'):
            (self.tmp / f).unlink(missing_ok=True)
        shutil.rmtree(self.pool, ignore_errors=True)
        self.flag.unlink(missing_ok=True)

    def spooled(self, name='cmt-1.abcdef.ready', pdu=PDU_GSM):
        self.spool.mkdir(parents=True, exist_ok=True)
        (self.spool / name).write_text(pdu + '\n')
        return self.spool / name

    def stop(self, d):
        d.send_signal(signal.SIGTERM)
        return d.communicate(timeout=10)

    def test_direct_delivery_is_asked_after_the_first_sync_and_a_message_is_acknowledged_after_its_import(self):
        for shell in self.each_shell():
            self.fresh()
            d = self.start(shell)
            try:
                self.wait_for(lambda: 'AT+CNMI=3,2,2,0,1' in self.commands(), 'direct delivery was not asked for')
                c = self.commands()
                # the SIM first, in the mode it is announced in, then PDU mode, the service that acknowledges, routing
                self.assertEqual(c[c.index('AT+CNMI=2,1,0,0,0'):], ['AT+CNMI=2,1,0,0,0', 'AT+CMGF=0', 'AT+CSMS=1', 'AT+CNMI=3,2,2,0,1'])
                log = self.at_log()
                self.assertLess(log.index('SMS sync'), log.index('-t 5 AT+CMGF=0'))
                self.wait_for(lambda: self.flag.exists(), 'no flag for direct delivery')
                self.assertEqual((self.tmp / 'lockwait').read_text().strip(), '8')   # never behind a stuck client for 90 s
                # a spooled message: pooled, and only then acknowledged; the file goes once the modem took the ack
                f = self.spooled()
                self.wait_for(lambda: 'AT+CNMA=1' in self.commands(), 'no acknowledgement', timeout=15)
                log = self.at_log()
                self.assertLess(log.index(f'SMS import-pdu {f}'), log.index('-t 5 AT+CNMA=1'))
                self.assertEqual(log.count('-t 5 AT+CNMA=1'), 1)
                self.wait_for(lambda: not f.exists(), 'the acknowledged file stayed')
                self.assertEqual(sorted(p.name for p in self.spool.iterdir()), [])
                # two at once: oldest first, one acknowledgement each
                self.spooled('cmt-200.b.ready'); self.spooled('cmt-100.a.ready', PDU_UCS2)
                self.wait_for(lambda: self.at_log().count('-t 5 AT+CNMA=1') == 3, 'two more acknowledgements', timeout=15)
                imports = [l for l in self.at_log() if l.startswith('SMS import-pdu')]
                self.assertEqual([i.rsplit('/', 1)[1] for i in imports[1:]], ['cmt-100.a.ready', 'cmt-200.b.ready'])
            finally:
                out, err = self.stop(d)
            self.assertIn('direct delivery is on', err)
            self.assertFalse(self.flag.exists(), 'the flag outlived its daemon')

    def test_a_refusal_leaves_the_sim_path_as_it_was(self):
        for shell in self.each_shell():
            for refuse, told in (('AT+CNMI=3', True), ('AT+CSMS=1', True), ('AT+CMGF=0', True), (None, False)):
                with self.subTest(refuse=refuse):
                    self.fresh()
                    if refuse:
                        (self.tmp / 'refuse').write_text(refuse + '\n')
                    d = self.start(shell, **({} if refuse else {'MU300_SMS_DIRECT': '0'}))
                    try:
                        self.wait_for(lambda: self.commands().count('AT+CNMI=2,1,0,0,0') == 2, 'storage indications not asked again')
                        c = self.commands()
                        second = c.index('AT+CNMI=2,1,0,0,0', c.index('AT+CNMI=2,1,0,0,0') + 1)
                        self.assertEqual(c[second:], ['AT+CNMI=2,1,0,0,0'])    # the last word
                        if not told:
                            self.assertNotIn('AT+CSMS=1', c)
                            self.assertNotIn('AT+CNMI=3,2,2,0,1', c)
                        time.sleep(1.2)
                        self.assertFalse(self.flag.exists())
                        # a +CMTI still starts a sync, as before
                        with open(self.atdir / 'urc' / 'stty_nr0.log', 'a') as f:
                            f.write('+CMTI: "SM",4\n')
                        self.wait_for(lambda: self.syncs() == ['sync', 'sync'], 'no sync after +CMTI', timeout=15)
                        # a frame that comes anyway is still pooled and acknowledged
                        self.spooled()
                        self.wait_for(lambda: 'AT+CNMA=1' in self.commands(), 'no acknowledgement', timeout=15)
                    finally:
                        out, err = self.stop(d)
                    self.assertIn('did not take direct delivery', err)
                    self.assertIn('announced (+CMTI)', err)

    def test_a_failed_import_is_never_acknowledged_and_three_rounds_give_direct_delivery_up(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'sms-fail').write_text('')
            d = self.start(shell)
            try:
                self.wait_for(lambda: self.flag.exists(), 'direct delivery was not asked for')
                f = self.spooled()
                self.wait_for(lambda: len([l for l in self.at_log() if l.startswith('SMS import-pdu')]) >= 3,
                              'three rounds of importing', timeout=15)
                self.assertNotIn('AT+CNMA=1', self.commands())
                self.wait_for(lambda: not self.flag.exists(), 'direct delivery was not given up')
                self.assertTrue((self.spool / 'cmt-1.abcdef.failed').exists())
                self.assertFalse(f.exists())
                c = self.commands()
                self.assertEqual(c[-1], 'AT+CNMI=2,1,0,0,0')
                self.assertNotIn('AT+CNMA=1', c)
            finally:
                out, err = self.stop(d)
            self.assertIn('could not pool', err)
            self.assertIn('three rounds of pooling or acknowledging failed', err)

    def test_a_link_in_the_spool_is_set_aside_and_never_imported(self):
        for shell in self.each_shell():
            self.fresh()
            d = self.start(shell)
            try:
                self.wait_for(lambda: self.flag.exists(), 'direct delivery was not asked for')
                self.spool.mkdir(parents=True, exist_ok=True)
                target = self.tmp / 'target.pdu'
                target.write_text(PDU_GSM + '\n')
                (self.spool / 'cmt-1.link.ready').symlink_to(target)
                self.wait_for(lambda: (self.spool / 'cmt-1.link.failed').is_symlink(), 'the link was not set aside')
                self.assertFalse([l for l in self.at_log() if l.startswith('SMS import-pdu')])
                self.assertNotIn('AT+CNMA=1', self.commands())
                self.assertTrue(self.flag.exists())              # one link is no failure of direct delivery
            finally:
                out, err = self.stop(d)
            self.assertIn('not a regular file; set aside', err)

    def test_an_acknowledgement_the_modem_refuses_keeps_the_file(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'refuse').write_text('AT+CNMA\n')
            d = self.start(shell)
            try:
                self.wait_for(lambda: self.flag.exists(), 'direct delivery was not asked for')
                f = self.spooled()
                self.wait_for(lambda: self.commands().count('AT+CNMA=1') == 2, 'a second try', timeout=15)
                self.assertTrue(f.exists())                       # imported (twice, idempotent), never removed
                self.assertEqual(len([l for l in self.at_log() if l.startswith('SMS import-pdu')]), 2)
            finally:
                out, err = self.stop(d)
            self.assertIn('did not take the acknowledgement', err)

    def test_routing_the_modem_dropped_is_put_back_at_the_periodic_sync(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'cnmi').write_text('+CNMI: 0,0,0,0,0\n')   # what a modem answers after an acknowledgement never came
            d = self.start(shell, MU300_SMS_SYNC_INTERVAL=2)
            try:
                self.wait_for(lambda: self.commands().count('AT+CNMI=3,2,2,0,1') >= 2, 'routing not put back', timeout=15)
                c = self.commands()
                self.assertLess(c.index('AT+CNMI?'), len(c) - c[::-1].index('AT+CNMI=3,2,2,0,1') - 1)
                self.assertTrue(self.flag.exists())
            finally:
                self.stop(d)

    def wait_for(self, cond, what, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.1)
        self.fail(what)

    def test_one_daemon_and_a_sync_on_each_new_message(self):
        for shell in self.each_shell():
            (self.tmp / 'sms.log').unlink(missing_ok=True)
            first = self.start(shell)
            try:
                self.wait_for(lambda: self.syncs() == ['sync'], 'no first sync')
                self.assertIn('AT+CNMI=2,1,0,0,0', (self.tmp / 'at.log').read_text())
                # a second one leaves the pool to the first
                second = self.start(shell)
                out, err = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 0)
                self.assertIn(f'pid {first.pid} is already pooling', err)
                # a +CMTI in the AT daemon's URC log: one more sync
                with open(self.atdir / 'urc' / 'stty_nr0.log', 'a') as f:
                    f.write('+CMTI: "SM",4\n')
                self.wait_for(lambda: self.syncs() == ['sync', 'sync'], 'no sync after +CMTI', timeout=15)
            finally:
                first.send_signal(signal.SIGTERM)
                first.communicate(timeout=10)
            self.assertFalse(self.lock.exists(), 'the lock outlived its daemon')

    def test_a_new_message_syncs_without_cksum(self):
        # OpenWrt's busybox has md5sum but no cksum: a +CMTI still starts a sync, and nothing says "not found"
        self.stub('cksum', 'echo "cksum: not found" >&2; exit 127')
        for shell in self.each_shell():
            (self.tmp / 'sms.log').unlink(missing_ok=True)
            (self.atdir / 'urc' / 'stty_nr0.log').unlink(missing_ok=True)
            d = self.start(shell)
            try:
                self.wait_for(lambda: self.syncs() == ['sync'], 'no first sync')
                time.sleep(1.5)   # a look or two at the URC logs before the message
                with open(self.atdir / 'urc' / 'stty_nr0.log', 'a') as f:
                    f.write('+CMTI: "SM",4\n')
                self.wait_for(lambda: self.syncs() == ['sync', 'sync'], 'no sync after +CMTI', timeout=15)
            finally:
                d.send_signal(signal.SIGTERM)
                out, err = d.communicate(timeout=10)
            self.assertNotIn('not found', err + out)

    def test_a_dead_holder_is_taken_over(self):
        for shell in self.each_shell():
            self.lock.mkdir()
            dead = subprocess.Popen(['true'])
            dead.wait()
            (self.lock / 'pid').write_text(f'{dead.pid}\n')
            d = self.start(shell)
            try:
                self.wait_for(lambda: (self.lock / 'pid').exists() and
                              (self.lock / 'pid').read_text().strip() == str(d.pid), 'lock not taken over')
            finally:
                d.send_signal(signal.SIGTERM)
                d.communicate(timeout=10)


class Wiring(unittest.TestCase):
    def test_the_spooler_is_on_every_system_and_the_at_daemon_knows_it(self):
        # mu300-atd runs on Ubuntu too, and a +CMT frame must never end in its log or be lost: the spooler is in the
        # shared tree, the pool and spool paths are the pool's everywhere
        spooler = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-cmt-spool'
        self.assertTrue(spooler.stat().st_mode & 0o111, 'not executable')
        self.assertIn('POOL=${MU300_SMS_POOL:-/etc/mu300/sms-pool}\n', spooler.read_text())
        self.assertIn('SPOOL=$POOL/incoming\n', spooler.read_text())
        atd = (TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-atd').read_text()
        self.assertIn('CMT_SPOOL=${MU300_CMT_SPOOL_BIN:-/opt/mu300/bin/mu300-cmt-spool}\n', atd)
        self.assertIn('SPOOL=$POOL/incoming\n', SMSD.read_text())
        self.assertIn('PARTS=$POOL/parts', SMS.read_text())
        # the message is never an argument: the daemon pipes the frame, the daemon of the pool reads a file
        self.assertNotRegex(atd, r'"\$CMT_SPOOL" +"\$')
        self.assertIn('"$SMS" import-pdu "$f"', SMSD.read_text())
        self.assertIn('export MU300_AT_LOCK_WAIT=${MU300_AT_LOCK_WAIT:-8}', SMSD.read_text())

    def test_init_script(self):
        src = INITD.read_text()
        self.assertTrue(src.startswith('#!/bin/sh /etc/rc.common\n'))
        self.assertIn('USE_PROCD=1', src)
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-smsd', src)
        self.assertIn('procd_set_param respawn', src)
        self.assertRegex(src, r'(?m)^START=\d+$')

    def test_build_enables_the_pool_on_openwrt_luci_only(self):
        src = BUILD.read_text()
        # inside the same luci-plugin branch that enables the panel's own service, never in the common lists
        block = src[src.index('    ln -sf ../init.d/unisoc-modem-ui'):]
        block = block[:block.index('\nfi\n')]
        self.assertIn('ln -sf ../init.d/mu300-smsd $R/etc/rc.d/S${n}mu300-smsd', block)
        self.assertIn('ln -sf /opt/mu300/bin/mu300-sms $R/usr/bin/mu300-sms', block)
        common = re.search(r'for s in (mu300-accounts[^;]*);', src).group(1)
        self.assertNotIn('smsd', common)
        # the commands every system links come from the shared list
        self.assertIn('for c in $(cat /in/opt-mu300/lib/path-commands)', src)
        common = (TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'path-commands').read_text().split()
        self.assertNotIn('mu300-sms', common)
        # the pool programs come from the luci overlay; the shared tree (every system) has none of them
        for name in ('mu300-sms', 'mu300-smsd'):
            self.assertFalse((TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / name).exists())
        self.assertFalse((TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-smsd').exists())


if __name__ == '__main__':
    unittest.main()
