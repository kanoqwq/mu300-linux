"""Portable adapter boundary of luci-app-mu300 (K81).

Ported from kanoqwq/clean-tf-7.2 (tests/test_unisoc_plugin.py) onto our tree. Ours differs on purpose: the adapters
read their uci options through the shared allow-lists of lib.sh (S1), so a saved-lock directory must be under
/etc/unisoc-modem or /etc/mu300 and a custom AT program must sit in the platform directory. The tests that need a
state directory therefore run a copy of the plugin's libexec/share tree whose /etc/unisoc-modem is the scratch
directory; the allow-list itself is tested on the unmodified lib.sh."""
import json
import shutil
import threading

from helpers import ShellTest, TOP


APP = TOP / 'openwrt' / 'luci-app-mu300' / 'root'
AT = APP / 'usr' / 'libexec' / 'unisoc-modem' / 'at'
REPLAY = APP / 'usr' / 'libexec' / 'unisoc-modem' / 'boot-replay'
LOCK = APP / 'usr' / 'libexec' / 'unisoc-modem' / 'lock'
LIB = APP / 'usr' / 'share' / 'unisoc-modem' / 'lib.sh'


class Adapter(ShellTest):
    def tree(self):
        """A copy of the plugin's scripts with /etc/unisoc-modem and the runtime directory /var/run/unisoc-modem
        moved into the scratch directory; returns the scripts (at, boot-replay, lock) and the state directory the
        copy uses as its default."""
        root = self.tmp / 'tree'
        shutil.copytree(APP / 'usr' / 'libexec' / 'unisoc-modem', root / 'libexec' / 'unisoc-modem')
        shutil.copytree(APP / 'usr' / 'share' / 'unisoc-modem', root / 'share' / 'unisoc-modem')
        etc = self.tmp / 'etc' / 'unisoc-modem'
        for f in root.rglob('*'):
            if f.is_file():
                f.write_text(f.read_text().replace('/etc/unisoc-modem', str(etc))
                             .replace('/var/run/unisoc-modem', str(self.tmp / 'var-run' / 'unisoc-modem')))
        base = root / 'libexec' / 'unisoc-modem'
        return base / 'at', base / 'boot-replay', base / 'lock', etc / 'lock-state.d'

    def custom_program(self, name, body):
        """A program in the platform directory (the only place a custom AT command may live)."""
        plat = self.tmp / 'platform'
        plat.mkdir(exist_ok=True)
        p = plat / name
        p.write_text('#!/bin/sh\n' + body)
        p.chmod(0o755)
        return p

    def custom_uci(self, program, extra=''):
        self.stub('uci', f'''case "$3" in
unisoc_modem.main.at_backend) echo custom ;;
unisoc_modem.main.at_command) echo "{program}" ;;
{extra}esac''')


    def test_custom_at_contract(self):
        at, _, _, _ = self.tree()
        custom = self.custom_program('platform-at', 'printf "%s\\n" "$*" > "$STUBLOG/adapter.args"\nprintf "AT\\nOK\\n"\n')
        self.custom_uci(custom)
        for shell in self.each_shell():
            result = self.script(shell, at, '-t', '3', 'AT+CFUN?', MU300_PLATFORM_DIR=custom.parent)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('OK', result.stdout)
            self.assertEqual((self.tmp / 'adapter.args').read_text().strip(), '3 AT+CFUN?')

    def test_custom_at_outside_the_platform_directory_is_refused(self):
        # the fork let any executable be named; ours takes only a program directly in the platform directory (S1)
        elsewhere = self.tmp / 'elsewhere-at'
        elsewhere.write_text('#!/bin/sh\ntouch "$STUBLOG/was-called"\n')
        elsewhere.chmod(0o755)
        self.custom_uci(elsewhere)
        for shell in self.each_shell():
            result = self.script(shell, AT, 'AT', MU300_PLATFORM_DIR=self.tmp / 'platform')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.tmp / 'was-called').exists())

    def test_available_does_not_send_at(self):
        at, _, _, _ = self.tree()
        custom = self.custom_program('platform-at', 'touch "$STUBLOG/was-called"\n')
        self.custom_uci(custom)
        for shell in self.each_shell():
            result = self.script(shell, at, '--available', MU300_PLATFORM_DIR=custom.parent)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.tmp / 'was-called').exists())

    def test_state_dir_outside_etc_is_ignored(self):
        # unmodified lib.sh: a state directory the UI could set under /tmp falls back to /etc/unisoc-modem (S1)
        state = self.tmp / 'state'
        state.mkdir()
        (state / 'mode').write_text('4g')
        self.stub('uci', f'case "$3" in unisoc_modem.main.state_dir) echo "{state}" ;; esac')
        marker = self.tmp / 'replayed'
        for shell in self.each_shell():
            result = self.sh(shell, f'. {LIB}; safe_state_dir "$(uci_get state_dir)" /etc/unisoc-modem/lock-state.d')
            self.assertEqual(result.stdout.strip(), '/etc/unisoc-modem/lock-state.d', result.stderr)
            result = self.script(shell, REPLAY, UNISOC_REPLAY_MARKER=marker,
                                 UNISOC_AT_BIN=self.custom_program('at', 'touch "$STUBLOG/at-probed"\n'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.tmp / 'at-probed').exists())

    def test_disabled_replay_has_zero_at_traffic(self):
        _, replay, _, state = self.tree()
        state.mkdir(parents=True)
        (state / 'auto_apply').write_text('off')
        (state / 'mode').write_text('4g')
        self.stub('uci', 'exit 0')
        self.stub('grep', 'touch "$STUBLOG/at-was-probed"; exit 1')
        probe = self.custom_program('at', 'touch "$STUBLOG/at-probed"\n')
        for shell in self.each_shell():
            result = self.script(shell, replay, UNISOC_AT_BIN=probe)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.tmp / 'at-was-probed').exists())
            self.assertFalse((self.tmp / 'at-probed').exists())

    def test_ready_at_replays_saved_locks_immediately(self):
        _, replay, _, state = self.tree()
        state.mkdir(parents=True)
        (state / 'auto_apply').write_text('on')
        (state / 'mode').write_text('4g')
        at = self.custom_program('at', 'printf "AT\\nOK\\n"\n')
        lock = self.custom_program('lock', 'printf "%s\\n" "$*" > "$STUBLOG/replay.args"\n')
        self.stub('uci', '''case "$3" in
unisoc_modem.main.replay_phase) echo early ;; # obsolete values must not weaken the generic fallback
unisoc_modem.main.replay_timeout) echo 10 ;;
esac''')
        for shell in self.each_shell():
            result = self.script(shell, replay, UNISOC_AT_BIN=at, UNISOC_LOCK_BIN=lock)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.tmp / 'replay.args').read_text().strip(), 'replay late')

    def test_platform_early_window_suppresses_probe_and_late_replay(self):
        _, replay, _, state = self.tree()
        state.mkdir(parents=True)
        (state / 'mode').write_text('4g')
        marker = self.tmp / 'replayed'
        pending = self.tmp / 'pending'
        at = self.custom_program('at', 'touch "$STUBLOG/at-probed"\necho OK\n')
        lock = self.custom_program('lock', 'touch "$STUBLOG/late-called"\n')
        self.stub('uci', 'case "$3" in unisoc_modem.main.replay_timeout) echo 10 ;; esac')
        for shell in self.each_shell():
            marker.unlink(missing_ok=True)
            pending.touch()

            def finish_early():
                pending.unlink(missing_ok=True)
                marker.touch()
            timer = threading.Timer(0.1, finish_early)
            timer.start()
            try:
                result = self.script(shell, replay, UNISOC_AT_BIN=at, UNISOC_LOCK_BIN=lock,
                                     UNISOC_EARLY_PENDING=pending, UNISOC_REPLAY_MARKER=marker)
            finally:
                timer.join()
            self.assertEqual(result.returncode, 0,
                             f'{result.stderr}; pending={pending.exists()} marker={marker.exists()}')
            self.assertFalse((self.tmp / 'at-probed').exists())
            self.assertFalse((self.tmp / 'late-called').exists())

    def test_early_replay_restores_every_saved_lock_without_sfun(self):
        _, _, lock, state = self.tree()
        state.mkdir(parents=True)
        for k, v in (('auto_apply', 'on'), ('mode', 'nsa'), ('endc', 'on'), ('lte', '1,3,5'), ('nr', '41,78'),
                     ('cell', 'lte:1650,211\nnr:627264,393\n')):
            (state / k).write_text(v)
        at = self.tmp / 'at'
        at.write_text('''#!/bin/sh
printf "%s\\n" "$*" >> "$STUBLOG/at.commands"
case "$*" in
  *'AT+SPTESTMODE?'*) printf '+SPTESTMODE: 131,134,0\\nOK\\n' ;;
  *'AT+SP5GRAN?'*) printf '+SP5GRAN: 0\\nOK\\n' ;;
  *'AT+SPENDC?'*) printf '+ENDC: 1\\nOK\\n' ;;
  *'AT+SPLBAND=0'*) printf '+SPLBAND: 0,0,0,21,0\\nOK\\n' ;;
  *'AT+SPLBAND=3'*) printf '+SPLBAND: 0,0,272\\nOK\\n' ;;
  *'AT+SPFORCEFRQ=12,3'*) printf '+SPFORCEFRQ: 12,3,1650,211\\nOK\\n' ;;
  *'AT+SPFORCEFRQ=16,3'*) printf '+SPFORCEFRQ: 16,3,627264,393\\nOK\\n' ;;
  *) printf 'OK\\n' ;;
esac
''')
        at.chmod(0o755)
        self.stub('uci', 'case "$3" in unisoc_modem.main.data_interface) echo cellular ;; esac')
        # Band masks use POSIX awk arithmetic, so the host awk must work too.
        apply_dir = self.tmp / 'apply'
        marker = self.tmp / 'replayed'
        for shell in self.each_shell():
            (self.tmp / 'at.commands').unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            result = self.script(shell, lock, 'replay', 'early', MU300_AT=at,
                                 UNISOC_APPLY_DIR=apply_dir, UNISOC_REPLAY_MARKER=marker)
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = (self.tmp / 'at.commands').read_text()
            self.assertIn('AT+SP5GRAN=0', commands)
            self.assertIn('AT+SPTESTMODE=131,134,0', commands)
            self.assertIn('AT+SPENDC=1', commands)
            self.assertIn('AT+SPLBAND=1,0,0,0,21,0', commands, result.stderr)
            self.assertIn('AT+SPLBAND=2,0,0,272,0', commands)
            self.assertIn('AT+SPFORCEFRQ=12,6,1650,211', commands)
            self.assertIn('AT+SPFORCEFRQ=16,6,627264,393', commands)
            self.assertNotIn('AT+SFUN=', commands)
            self.assertTrue(marker.exists())

    def test_auto152_is_a_mode_lock_like_the_others(self):
        """FINDINGS 198 (kanoqwq's auto work mode): auto152 writes the automatic mode 152 (no 2G), keeps the slot1 and
        primary fields, and is replayed like every lock."""
        _, _, lock, state = self.tree()
        state.mkdir(parents=True)
        for k, v in (('auto_apply', 'on'), ('mode', 'auto152')):
            (state / k).write_text(v)
        at = self.tmp / 'at'
        at.write_text('''#!/bin/sh
printf "%s\\n" "$*" >> "$STUBLOG/at.commands"
case "$*" in
  *'AT+SPTESTMODE=152,'*) : > "$STUBLOG/mode152"; printf 'OK\\n' ;;
  *'AT+SPTESTMODE?'*) if [ -e "$STUBLOG/mode152" ]; then printf '+SPTESTMODE: 152,134,0\\nOK\\n'
                      else printf '+SPTESTMODE: 134,134,0\\nOK\\n'; fi ;;
  *'AT+SP5GRAN?'*) printf '+SP5GRAN: 1\\nOK\\n' ;;
  *) printf 'OK\\n' ;;
esac
''')
        at.chmod(0o755)
        self.stub('uci', 'case "$3" in unisoc_modem.main.data_interface) echo cellular ;; esac')
        for shell in self.each_shell():
            (self.tmp / 'at.commands').unlink(missing_ok=True)
            (self.tmp / 'mode152').unlink(missing_ok=True)
            (self.tmp / 'replayed').unlink(missing_ok=True)
            result = self.script(shell, lock, 'replay', 'early', MU300_AT=at, UNISOC_APPLY_DIR=self.tmp / 'apply',
                                 UNISOC_REPLAY_MARKER=self.tmp / 'replayed')
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = (self.tmp / 'at.commands').read_text()
            self.assertIn('AT+SP5GRAN=1', commands)
            self.assertIn('AT+SPTESTMODE=152,134,0', commands)
        text = lock.read_text()
        self.assertIn('152) echo auto152 ;;', text)
        self.assertIn('auto152) echo "152 1" ;;', text)

    def test_the_stack_restart_takes_the_radio_lock(self):
        """Final review minor 3: lock apply's and a late replay's SFUN restart run under mobile-data's radio lock
        (mobile-data radio-locked, waiting up to 120 s for the dial or the watchdog); when it stays busy, no SFUN is
        sent and the replay still finishes (the saved settings were sent; they apply at the radio's next restart)."""
        _, _, lock, state = self.tree()
        at = self.tmp / 'at'
        at.write_text('#!/bin/sh\necho "$* locked=${MU300_RADIO_LOCKED:-}" >> "$STUBLOG/at.commands"\n'
                      'case "$*" in *"AT+SPTESTMODE?"*) echo "+SPTESTMODE: 134,134,0" ;; *"AT+CFUN?"*) echo "+CFUN: 1" ;; esac\n'
                      'echo OK\n')
        at.chmod(0o755)
        md = self.tmp / 'mobile-data'
        md.write_text('#!/bin/sh\necho "$* wait=${MU300_RADIO_LOCK_WAIT:-}" >> "$STUBLOG/md"\n'
                      '[ "$1" = radio-locked ] || exit 0\n'
                      '[ -n "${MD_BUSY:-}" ] && exit 75\nshift; exec "$@"\n')
        md.chmod(0o755)
        self.stub('uci', 'exit 0')
        self.stub('ifup', 'exit 0')
        self.stub('sleep', 'exit 0')
        marker = self.tmp / 'replayed'
        for shell in self.each_shell():
            for busy in ('', '1'):
                with self.subTest(busy=busy):
                    state.mkdir(parents=True, exist_ok=True)
                    (state / 'mode').write_text('4g')
                    for f in ('at.commands', 'md'):
                        (self.tmp / f).unlink(missing_ok=True)
                    marker.unlink(missing_ok=True)
                    r = self.script(shell, lock, 'replay', MU300_AT=at, MU300_MOBILE_DATA=md, MD_BUSY=busy,
                                    UNISOC_APPLY_DIR=self.tmp / 'apply', UNISOC_REPLAY_MARKER=marker)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue(marker.exists())
                    self.assertEqual((self.tmp / 'md').read_text().splitlines(),
                                     [f'radio-locked {lock} sfun-restart late wait=120'])
                    sent = (self.tmp / 'at.commands').read_text().splitlines()
                    sfun = [l for l in sent if 'AT+SFUN=' in l]
                    if busy:
                        self.assertEqual(sfun, [])
                    else:
                        self.assertEqual(sfun[:2], ['-t 10 AT+SFUN=5 locked=1', '-t 30 AT+SFUN=4 locked=1'])

    # A fake modem for the reset: it starts with every lock set (mode 4G, EN-DC off, B3, n78, one cell per RAT) and
    # answers each read from what the writes so far have changed, the way the real one does. Knobs in $STUBLOG/modem
    # (the uci stub, the first command the tool runs, is where a test writes them):
    #   refuse              a command prefix the modem answers with ERROR
    #   silent-mode         AT+SPTESTMODE? answers nothing
    #   revert-after-sfun   once AT+SFUN=4 was sent, the LTE bands read back as locked again
    #   u30air              a cleared LTE mask reads back as the U30 Air's table (0,2018,2057,2271,2)
    #   lte-raw             the LTE masks the last non-zero AT+SPLBAND=1 wrote (a test may write it: a lock set
    #                       outside the panel); it is what AT+SPLBAND=0 answers until a zero mask clears it
    MODEM = r"""#!/bin/sh
M=$STUBLOG/modem; mkdir -p "$M"
printf "%s\n" "$*" >> "$STUBLOG/at.commands"
all=$*; cmd=AT+${all##*AT+}
if [ -f "$M/refuse" ] && [ "${cmd#"$(cat "$M/refuse")"}" != "$cmd" ]; then printf 'ERROR\n'; exit 0; fi
case "$cmd" in
  'AT+SPTESTMODE=134,'*) touch "$M/mode" ;;
  'AT+SP5GRAN=1') touch "$M/gran" ;;
  'AT+SPENDC=1') touch "$M/endc" ;;
  'AT+SPLBAND=1,0,0,0,0,0') rm -f "$M/lte-raw"; touch "$M/lte" ;;
  'AT+SPLBAND=1,'*) printf '%s\n' "${cmd#AT+SPLBAND=1,}" > "$M/lte-raw" ;;
  'AT+SPLBAND=2,0,0,0,0') touch "$M/nr" ;;
  'AT+SPFORCEFRQ=12,4') touch "$M/cell12" ;;
  'AT+SPFORCEFRQ=16,4') touch "$M/cell16" ;;
  'AT+SFUN=4') touch "$M/sfun" ;;
esac
case "$cmd" in
  'AT+SPTESTMODE?') [ -f "$M/silent-mode" ] && exit 0
      if [ -f "$M/mode" ]; then printf '+SPTESTMODE: 134,134,0,19,3,1\n'; else printf '+SPTESTMODE: 3,134,0,19,3,1\n'; fi ;;
  'AT+SP5GRAN?') if [ -f "$M/gran" ]; then printf '+SP5GRAN: 1\n'; else printf '+SP5GRAN: 0\n'; fi ;;
  'AT+SPENDC?') if [ -f "$M/endc" ]; then printf '+ENDC: 1\n'; else printf '+ENDC: 2\n'; fi ;;
  'AT+SPLBAND=0')
      if [ -f "$M/lte-raw" ]; then printf '+SPLBAND: %s\n' "$(cat "$M/lte-raw")"
      elif [ -f "$M/lte" ] && [ -f "$M/u30air" ]; then printf '+SPLBAND: 0,2018,2057,2271,2\n'
      elif [ -f "$M/lte" ] && ! { [ -f "$M/revert-after-sfun" ] && [ -f "$M/sfun" ]; }; then printf '+SPLBAND: 0,482,0,149,0\n'
      else printf '+SPLBAND: 0,0,0,4,0\n'; fi ;;
  'AT+SPLBAND=3') if [ -f "$M/nr" ]; then printf '+SPLBAND: 553,0,272\n'; else printf '+SPLBAND: 0,0,256\n'; fi ;;
  'AT+SPFORCEFRQ=12,3') if [ -f "$M/cell12" ]; then printf '+SPFORCEFRQ: 12,3\n'; else printf '+SPFORCEFRQ: 12,3,1650,211\n'; fi ;;
  'AT+SPFORCEFRQ=16,3') if [ -f "$M/cell16" ]; then printf '+SPFORCEFRQ: 16,3\n'; else printf '+SPFORCEFRQ: 16,3,627264,393\n'; fi ;;
  'AT+CFUN?') printf '+CFUN: 1\n' ;;
esac
printf 'OK\n'
"""
    SAVED = (('auto_apply', 'on'), ('mode', '4g'), ('endc', 'off'), ('lte', '3'), ('nr', '78'), ('cell', 'lte:1650,211\n'))
    RESET_WRITES = ['AT+SP5GRAN=1', 'AT+SPTESTMODE=134,134,0', 'AT+SPENDC=1', 'AT+SPLBAND=1,0,0,0,0,0',
                    'AT+SPLBAND=2,0,0,0,0', 'AT+SPFORCEFRQ=12,4', 'AT+SPFORCEFRQ=16,4']

    def reset_setup(self, knobs='', device='f50'):
        """The lock tool on the fake modem with every lock saved and set: (lock, state dir, run dir, at). KNOBS is
        shell code the uci stub runs (the first command the tool runs) to set the fake modem's knobs; DEVICE is what
        mu300-device answers (the board picks the LTE table)."""
        self.stub('mu300-device', f'echo {device}')
        _, _, lock, state = self.tree()
        at = self.tmp / 'at'
        at.write_text(self.MODEM)
        at.chmod(0o755)
        md = self.tmp / 'mobile-data'
        md.write_text('#!/bin/sh\n[ "$1" = radio-locked ] || exit 0\nshift; exec "$@"\n')
        md.chmod(0o755)
        self.stub('uci', f'mkdir -p "$STUBLOG/modem"\n{knobs}\nexit 0')
        self.stub('ifup', 'exit 0')
        self.stub('sleep', 'exit 0')
        # the background cache refreshes (sh "$0" get fresh after a reset, setsid "$0" get fresh after an EN-DC
        # apply) are not under test: keep them off the AT log - a real setsid (Linux) would append do_get's seven
        # queries a moment after the tool has returned, racing the next assertion
        self.stub('sh', 'exit 0')
        self.stub('setsid', 'exit 0')
        return lock, state, self.tmp / 'run', at

    def reset_run(self, shell, lock, state, run, at, *args):
        """One `lock reset` (or ARGS) from the saved-and-set starting point: (result, [AT commands sent])."""
        shutil.rmtree(state, ignore_errors=True)
        state.mkdir(parents=True)
        for k, v in self.SAVED:
            (state / k).write_text(v)
        shutil.rmtree(self.tmp / 'modem', ignore_errors=True)
        (self.tmp / 'at.commands').unlink(missing_ok=True)
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(mode=0o700)
        (run / 'lock.json').write_text('{"ts":1,"mode":{"label":"4g"},"cell":"lte:1650,211",'
                                       '"cells":["lte:1650,211"],"caps":{"nr":"1,5,6,8,28,41,78","lte":"1,3,5,8,34,38,39,40,41"}}\n')
        r = self.script(shell, lock, *(args or ('reset',)), MU300_AT=at, MU300_MOBILE_DATA=self.tmp / 'mobile-data',
                        MU300_DASH_DIR=run, UNISOC_APPLY_DIR=self.tmp / 'apply', MU300_SIM_RUN=self.tmp / 'sim-run')
        return r, self.sent()

    def sent(self):
        f = self.tmp / 'at.commands'
        if not f.exists():
            return []
        return [l.split(' ', 2)[2] for l in f.read_text().splitlines() if l.startswith('-t ')]

    def writes(self, sent):
        return [c for c in sent if '=' in c and not c.endswith('?') and not c.startswith('AT+SFUN')
                and c not in ('AT+SPLBAND=0', 'AT+SPLBAND=3') and not c.endswith(',3')]

    def reset_result(self, run):
        return json.loads((run / 'lock-reset.json').read_text())

    def test_a_refused_band_write_is_reported_not_saved(self):
        """#356: the band write's answer was thrown away, so a modem's ERROR was saved, replayed and reported as
        applied. Now: logged with the answer, an error on stdout, nothing saved, no stack restart."""
        lock, state, run, at = self.reset_setup('printf "AT+SPLBAND=2," > "$STUBLOG/modem/refuse"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'nr', '41')
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'The modem refused a write',
                                                    'detail': 'AT+SPLBAND=2,0,0,16,0'})
            self.assertIn('AT+SPLBAND=2,0,0,16,0', sent)
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN')], [])
            self.assertEqual((state / 'nr').read_text(), '78')
            self.assertIn('the modem refused AT+SPLBAND=2,0,0,16,0: ERROR', (run / 'lock.log').read_text())

    def test_bands_the_modem_does_not_support_are_refused_not_written_as_automatic(self):
        """None of the bands supported: the filter left nothing, which was written as automatic (AT+SPLBAND=2,0,0,0,0)
        and saved. Now refused with nothing sent; a partly supported list locks the supported ones and logs the rest."""
        lock, state, run, at = self.reset_setup()
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'nr', '999')
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'None of these bands is supported by this modem',
                                                    'detail': '999'})
            self.assertEqual(self.writes(sent), [])
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN')], [])
            self.assertEqual((state / 'nr').read_text(), '78')
            self.assertIn('apply nr refused: the modem supports none of the bands 999', (run / 'lock.log').read_text())
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'lte', '7')
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(self.writes(sent), [])
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'nr', '41,999')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('AT+SPLBAND=2,0,0,16,0', sent)
            self.assertEqual((state / 'nr').read_text(), '41')
            self.assertIn('left out of 41,999 (locking 41)', (run / 'lock.log').read_text())

    def test_an_accepted_band_write_is_saved_and_restarts_the_stack(self):
        lock, state, run, at = self.reset_setup()
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'nr', '41')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('AT+SPLBAND=2,0,0,16,0', sent)
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN')][:2], ['AT+SFUN=5', 'AT+SFUN=4'])
            self.assertEqual((state / 'nr').read_text(), '41')

    def test_sim2_and_the_dual_sim_manager_refuse_with_a_log_line(self):
        """#356: these refusals were only on the stderr of a detached process; the log says them now, and the boot
        replay says why it skipped."""
        lock, state, run, at = self.reset_setup()
        sim = self.tmp / 'sim-run'
        for marker, line in (('sim-managed', 'apply nr refused: the dual-SIM manager runs the radio'),
                             ('sim-slot', 'apply nr refused: SIM2 lock writes')):
            shutil.rmtree(sim, ignore_errors=True)
            sim.mkdir()
            (sim / marker).write_text('1\n')
            for shell in self.each_shell():
                with self.subTest(marker=marker):
                    r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'nr', '41')
                    self.assertEqual(r.returncode, 1)
                    self.assertEqual(sent, [])
                    self.assertEqual((state / 'nr').read_text(), '78')
                    self.assertIn(line, (run / 'lock.log').read_text())
            marker_file = self.tmp / 'replayed'
            marker_file.unlink(missing_ok=True)
            for shell in self.each_shell():
                r = self.script(shell, lock, 'replay', MU300_AT=at, MU300_DASH_DIR=run, MU300_SIM_RUN=sim,
                                UNISOC_APPLY_DIR=self.tmp / 'apply', UNISOC_REPLAY_MARKER=marker_file)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('replay skipped', (run / 'lock.log').read_text())

    def test_reset_sends_the_automatic_values_with_one_restart_and_clears_the_saved_state(self):
        """#96: mode and band locks live in the modem's NV and stay in force in Android. reset puts every lock back
        to automatic (the values measured on an F50), restarts the stack once, and drops the saved settings so the
        boot replay does not put them back; the replay switch itself is kept. Verified (kanoqwq's 23361ce, ported):
        every write's answer is checked, the five settings are read back before and after the restart, and the
        saved settings go only after both read-backs matched."""
        lock, state, run, at = self.reset_setup()
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout + (run / 'lock.log').read_text())
            self.assertEqual(self.writes(sent), self.RESET_WRITES)
            # one stack restart, after every write and after the first read-back
            sfun = [i for i, c in enumerate(sent) if c.startswith('AT+SFUN=')]
            self.assertEqual([sent[i] for i in sfun], ['AT+SFUN=5', 'AT+SFUN=4'])
            self.assertGreater(sfun[0], sent.index('AT+SPFORCEFRQ=16,4'))
            # read back: the mode, the NR mode, EN-DC, both band tables and both cell locks, before and after
            for q in ('AT+SPTESTMODE?', 'AT+SP5GRAN?', 'AT+SPENDC?', 'AT+SPLBAND=0', 'AT+SPLBAND=3',
                      'AT+SPFORCEFRQ=12,3', 'AT+SPFORCEFRQ=16,3'):
                before = [i for i, c in enumerate(sent) if c == q and sent.index('AT+SPFORCEFRQ=16,4') < i < sfun[0]]
                after = [i for i, c in enumerate(sent) if c == q and i > sfun[1]]
                self.assertTrue(before, (q, sent))
                self.assertTrue(after, (q, sent))
            self.assertEqual(sorted(p.name for p in state.iterdir()), ['auto_apply'])
            self.assertEqual((state / 'auto_apply').read_text(), 'on')
            self.assertFalse((self.tmp / 'apply').exists())
            # the next cell lock seeds its list from the cache: it must not offer the cells just removed
            cache = (run / 'lock.json').read_text()
            self.assertIn('"cells":[]', cache)
            self.assertIn('"cell":""', cache)
            # the result, for the page (lock get) and the caller
            res = self.reset_result(run)
            self.assertEqual(res['state'], 'ok')
            self.assertEqual(json.loads(r.stdout)['state'], 'ok')
            got = self.script(shell, lock, 'get', 'cached', MU300_DASH_DIR=run)
            self.assertEqual(json.loads(got.stdout)['reset']['state'], 'ok')

    def test_reset_on_the_u30_air_takes_its_modems_full_lte_table_as_automatic(self):
        """The U30 Air's modem reads a cleared LTE mask back as its own table (0,2018,2057,2271,2: bands 1,2,3,4,5,
        7,8,12,17,20,28,34,38-43,66), more than the F50's table here: an exact match failed every reset there
        ("lte read back as '0,2018,2057,2271,2'", seen on the U30 Air on 6.18.58)."""
        lock, state, run, at = self.reset_setup(': > "$STUBLOG/modem/u30air"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout + (run / 'lock.log').read_text())
            self.assertEqual(self.reset_result(run)['state'], 'ok')
            self.assertEqual(sorted(p.name for p in state.iterdir()), ['auto_apply'])

    U30_TABLE = '1,2,3,4,5,7,8,12,17,20,28,34,38,39,40,41,42,43,66'

    def lock_get(self, shell, lock, run, at):
        r = self.script(shell, lock, 'get', 'fresh', MU300_AT=at, MU300_DASH_DIR=run)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((run / 'lock.json').read_text())

    def fresh_modem(self, run, raw=None):
        """The fake modem with nothing written yet, the LTE mask RAW in it (None: cleared)."""
        m = self.tmp / 'modem'
        shutil.rmtree(m, ignore_errors=True)
        m.mkdir()
        (m / 'lte').touch()
        if raw is not None:
            (m / 'lte-raw').write_text(raw + '\n')
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(mode=0o700)

    def test_the_u30_air_offers_its_own_lte_table_and_automatic_reads_as_nothing_locked(self):
        """The LTE candidates were the F50's table on every board, so B2/B4/B7/B12/B17/B20/B28/B42/B43/B66 could not
        be locked on the U30 Air; and a cleared mask (the modem's whole table) was reported as all 19 bands locked.
        Now the board's table is the candidate list, and a read-back that holds all of it is nothing locked."""
        lock, state, run, at = self.reset_setup(device='u30air')
        for shell in self.each_shell():
            self.fresh_modem(run)
            (self.tmp / 'modem' / 'u30air').touch()
            data = self.lock_get(shell, lock, run, at)
            self.assertEqual(data['caps']['lte'], self.U30_TABLE)
            self.assertEqual(data['lte']['raw'], '0,2018,2057,2271,2')
            self.assertEqual(data['lte']['locked'], '')
            self.assertFalse(state.exists() and any(p.name.startswith('lte') for p in state.iterdir()),
                             'a read writes nothing to the state directory')

    def test_a_lock_the_panel_did_not_save_still_shows_as_that_lock(self):
        """#387's review: with no saved lock, the read-back 0,450,0,149,0 (a lock on the F50's list less B38, sent by
        hand) must show as that lock on the U30 Air, not as automatic, and must not become the table."""
        lock, state, run, at = self.reset_setup(device='u30air')
        for shell in self.each_shell():
            self.fresh_modem(run, '0,450,0,149,0')
            data = self.lock_get(shell, lock, run, at)
            self.assertEqual(sorted(map(int, data['lte']['locked'].split(','))), [1, 3, 5, 8, 34, 39, 40, 41])
            self.assertEqual(data['caps']['lte'], self.U30_TABLE)
            # a lock on every band of the F50's table is a lock on the U30 Air, not automatic
            self.fresh_modem(run, '0,482,0,149,0')
            data = self.lock_get(shell, lock, run, at)
            self.assertEqual(sorted(map(int, data['lte']['locked'].split(','))), [1, 3, 5, 8, 34, 38, 39, 40, 41])

    def test_the_f50_keeps_its_table_and_its_cleared_mask_reads_as_automatic(self):
        lock, state, run, at = self.reset_setup()
        for shell in self.each_shell():
            self.fresh_modem(run)
            data = self.lock_get(shell, lock, run, at)
            self.assertEqual(data['caps']['lte'], '1,3,5,8,34,38,39,40,41')
            self.assertEqual(data['lte']['raw'], '0,482,0,149,0')
            self.assertEqual(data['lte']['locked'], '')
            self.fresh_modem(run, '0,0,0,4,0')
            self.assertEqual(self.lock_get(shell, lock, run, at)['lte']['locked'], '3')

    def test_u30_air_bands_lock_and_replay_verify(self):
        """B20 and B7 lock on the U30 Air (masks 0,0,8,64,0), are saved, and the boot replay's read-back accepts
        them; the same bands stay refused on the F50."""
        lock, state, run, at = self.reset_setup(device='u30air')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'lte', '20,7')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout + (run / 'lock.log').read_text())
            self.assertIn('AT+SPLBAND=1,0,0,8,64,0', sent)
            self.assertEqual((state / 'lte').read_text(), '20,7')
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN')][:2], ['AT+SFUN=5', 'AT+SFUN=4'])
            data = self.lock_get(shell, lock, run, at)
            self.assertEqual(sorted(map(int, data['lte']['locked'].split(','))), [7, 20])
            # the replay's read-back: only the saved fields are read
            for k in ('mode', 'endc', 'nr', 'cell'):
                (state / k).unlink(missing_ok=True)
            r = self.script(shell, lock, 'verify', MU300_AT=at, MU300_DASH_DIR=run)
            self.assertEqual(r.returncode, 0, r.stderr)
            # a modem that lost the lock fails the check
            (self.tmp / 'modem' / 'lte-raw').write_text('0,0,0,4,0\n')
            r = self.script(shell, lock, 'verify', MU300_AT=at, MU300_DASH_DIR=run)
            self.assertEqual(r.returncode, 1)
        self.stub('mu300-device', 'echo f50')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at, 'apply', 'lte', '20,7')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.writes(sent), [])

    def test_replay_verify_of_automatic_needs_the_whole_table(self):
        """A saved automatic is verified: the read-back must hold every band of the board's table (or be empty), so
        a lock left in the modem fails the check on either board."""
        lock, state, run, at = self.reset_setup()
        for device, full in (('u30air', '0,2018,2057,2271,2'), ('f50', '0,482,0,149,0')):
            self.stub('mu300-device', f'echo {device}')
            for shell in self.each_shell():
                with self.subTest(device=device):
                    shutil.rmtree(state, ignore_errors=True)
                    state.mkdir(parents=True)
                    (state / 'lte').write_text('')
                    for raw, rc in ((full, 0), ('0,0,0,0,0', 0), ('0,450,0,149,0', 1), ('0,0,8,64,0', 1)):
                        self.fresh_modem(run, raw)
                        r = self.script(shell, lock, 'verify', MU300_AT=at, MU300_DASH_DIR=run)
                        self.assertEqual(r.returncode, rc, (device, raw, r.stderr))

    def test_a_refused_write_keeps_the_saved_locks_and_restarts_nothing(self):
        lock, state, run, at = self.reset_setup('printf "AT+SPLBAND=2," > "$STUBLOG/modem/refuse"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.writes(sent), self.RESET_WRITES[:5])   # stops at the refused one
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN=')], [])
            self.assertEqual(sorted(p.name for p in state.iterdir()), sorted(k for k, _ in self.SAVED))
            res = self.reset_result(run)
            self.assertEqual((res['state'], res['error'], res['cmd']),
                             ('failed', 'The modem refused a write', 'AT+SPLBAND=2,0,0,0,0'))
            self.assertEqual(json.loads(r.stdout)['state'], 'failed')

    def test_a_readback_that_differs_keeps_the_saved_locks(self):
        # the LTE table never clears (the write is swallowed): the read-back says so before any restart
        lock, state, run, at = self.reset_setup(': > "$STUBLOG/modem/revert-after-sfun"; : > "$STUBLOG/modem/sfun"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.writes(sent), self.RESET_WRITES)
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN=')], [])
            self.assertEqual(sorted(p.name for p in state.iterdir()), sorted(k for k, _ in self.SAVED))
            res = self.reset_result(run)
            self.assertEqual((res['state'], res['error'], res['step']),
                             ('failed', 'The read-back does not match the automatic values', 'lte'))

    def test_a_readback_that_differs_after_the_restart_keeps_the_saved_locks(self):
        lock, state, run, at = self.reset_setup(': > "$STUBLOG/modem/revert-after-sfun"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN=')], ['AT+SFUN=5', 'AT+SFUN=4'])
            self.assertEqual(sorted(p.name for p in state.iterdir()), sorted(k for k, _ in self.SAVED))
            res = self.reset_result(run)
            self.assertEqual((res['state'], res['error'], res['step']),
                             ('failed', 'The read-back after the stack restart does not match the automatic values', 'lte'))

    def test_a_modem_that_does_not_answer_the_mode_query_gets_nothing(self):
        lock, state, run, at = self.reset_setup(': > "$STUBLOG/modem/silent-mode"')
        for shell in self.each_shell():
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.writes(sent), [])
            self.assertEqual(sorted(p.name for p in state.iterdir()), sorted(k for k, _ in self.SAVED))
            self.assertEqual(self.reset_result(run)['error'], 'The modem did not answer the mode query; nothing was sent')

    def test_an_interrupted_reset_finishes_at_the_next_apply_or_replay(self):
        """A reset ended after it committed (the reset_pending tombstone written, the saved settings not yet
        deleted) is finished before the next apply saves anything and before the next replay reads anything."""
        lock, state, run, at = self.reset_setup()
        for shell in self.each_shell():
            # rm, the moment it is asked for the saved settings: the reset dies there (as a reboot or a kill would)
            self.stub('rm', 'case "$*" in *lock-state.d/mode*) kill -9 $PPID; exit 1 ;; esac\nexec /bin/rm "$@"')
            r, sent = self.reset_run(shell, lock, state, run, at)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual([c for c in sent if c.startswith('AT+SFUN=')], ['AT+SFUN=5', 'AT+SFUN=4'])
            self.assertTrue((state / 'reset_pending').exists())
            self.assertEqual(sorted(p.name for p in state.iterdir()),
                             sorted(['reset_pending'] + [k for k, _ in self.SAVED]))
            (self.stubs / 'rm').unlink()
            # the page sees an interrupted reset, not one that is still running
            got = json.loads(self.script(shell, lock, 'get', 'cached', MU300_DASH_DIR=run).stdout)
            self.assertEqual((got['reset']['state'], got['reset']['error']), ('interrupted', 'The reset was interrupted'))
            # the next apply finishes the cleanup first, then saves its own setting
            r = self.script(shell, lock, 'apply', 'endc', 'on', MU300_AT=at, MU300_MOBILE_DATA=self.tmp / 'mobile-data',
                            MU300_DASH_DIR=run, UNISOC_APPLY_DIR=self.tmp / 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(p.name for p in state.iterdir()), ['auto_apply', 'endc'])
            self.assertEqual((state / 'endc').read_text(), 'on')
            # and the early replay: the tombstone first, then nothing to replay, no AT write, the marker set
            for k, v in self.SAVED:
                (state / k).write_text(v)
            (state / 'reset_pending').write_text('1')
            (self.tmp / 'at.commands').unlink(missing_ok=True)
            marker = self.tmp / 'replayed'
            marker.unlink(missing_ok=True)
            r = self.script(shell, lock, 'replay', 'early', MU300_AT=at, MU300_DASH_DIR=run,
                            UNISOC_APPLY_DIR=self.tmp / 'apply', UNISOC_REPLAY_MARKER=marker)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(p.name for p in state.iterdir()), ['auto_apply'])
            self.assertEqual(self.sent(), [])
            self.assertTrue(marker.exists())

    def test_early_replay_readback_failure_keeps_late_fallback(self):
        _, _, lock, state = self.tree()
        state.mkdir(parents=True)
        (state / 'mode').write_text('4g')
        at = self.tmp / 'at'
        at.write_text('#!/bin/sh\ncase "$*" in *"AT+SPTESTMODE?"*) echo "+SPTESTMODE: 134,134,0" ;; esac\n')
        at.chmod(0o755)
        self.stub('uci', 'exit 0')
        marker = self.tmp / 'replayed'
        for shell in self.each_shell():
            result = self.script(shell, lock, 'replay', 'early', MU300_AT=at,
                                 UNISOC_APPLY_DIR=self.tmp / 'apply', UNISOC_REPLAY_MARKER=marker)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    import unittest
    unittest.main()


class NrBand(ShellTest):
    """The collector names an NR cell's band from its ARFCN. n78 (620000-653333) lies inside n77 (620000-680000):
    the narrower band must be looked up first, or every n78 cell - the only one of the two this modem has - shows
    as n77 (reported by a user in China)."""

    def nrband(self, shell, fr):
        text = (APP / 'usr' / 'libexec' / 'unisoc-modem' / 'cell').read_text()
        funcs = set()
        start = 0
        while True:
            i = text.find('function nrband(', start)
            if i < 0:
                break
            funcs.add(text[i:text.index('\n        }\n', i) + 10])
            start = i + 1
        self.assertTrue(funcs)
        out = []
        for f in funcs:
            r = self.sh(shell, f"awk '{f}\nBEGIN {{ print nrband({fr}) }}'")
            self.assertEqual(r.returncode, 0, r.stderr)
            out.append(r.stdout.strip())
        return out

    def test_n78_is_not_named_n77(self):
        for shell in self.each_shell():
            for fr, band in ((627264, '78'), (620000, '78'), (653333, '78'), (653334, '77'), (680000, '77'),
                             (504990, '41'), (428000, '1'), (155000, '28'), (700000, '79'), (1, '0')):
                for got in self.nrband(shell, fr):
                    self.assertEqual(got, band, (shell, fr))
