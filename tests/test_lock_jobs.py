"""Verified MU300 writes and asynchronous results without touching a modem."""
import json
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from helpers import ShellTest, TOP

LIB = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem'


class LockWrites(ShellTest):
    def setUp(self):
        super().setUp()
        self.state = self.tmp / 'saved'
        self.state.mkdir()
        self.run = self.tmp / 'run'
        self.run.mkdir()
        self.lock = self.tmp / 'lib/lock'
        self.lock.parent.mkdir()
        for name in ('lock', 'lock-apply.sh', 'lock-jobs.sh'):
            shutil.copyfile(LIB / name, self.lock.parent / name)
            (self.lock.parent / name).chmod(0o755)
        self.modem = self.tmp / 'modem.json'
        self.stub('uci', f'case "$3" in unisoc_modem.main.state_dir) echo "{self.state}";; *.sim_slots) echo "${{TEST_SIM_SLOTS:-1}}";; *.auto_work_mode) echo "${{TEST_AUTO_MODE:-default}}";; *.auto_work_mode_default) echo "${{TEST_DEFAULT_MODE:-134}}";; esac')
        self.stub('logger', ':')
        self.stub('ifup', 'echo ifup >> "$STUBLOG/commands"')
        self.stub('sleep', ':')
        self.stub('jsonfilter', 'exit 1')
        at = self.tmp / 'fake-at'
        at.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['STUBLOG']); f=p/'modem.json'; s=json.loads(f.read_text())
cmd=sys.argv[-1]
with (p/'commands').open('a') as log: log.write(cmd+'\\n')
reads={'AT+SPTESTMODE?':('SPTESTMODE','tm'), 'AT+SP5GRAN?':('SP5GRAN','gran'),
 'AT+CAVIMS?':('CAVIMS','volte'),
 'AT+SPENDC?':('ENDC','endc'), 'AT+SPLBAND=0':('SPLBAND','lte'),
 'AT+SPLBAND=3':('SPLBAND','nr'), 'AT+CFUN?':('CFUN','cfun')}
if cmd in reads:
 if cmd=='AT+CAVIMS?' and s.get('no_voice_query'): print('ERROR'); sys.exit(1)
 if s.get('silent'): sys.exit(1)
 label,key=reads[cmd]; value=s.get('read_override',{}).get(key,s[key])
 if key=='endc' and value=='2': value=s.get('endc_off_reply','0')
 print('+'+label+': '+value); print('OK'); sys.exit(0)
if cmd=='AT+SP5GCMDS="get nr support_band"':
 print('+SP5GCMDS: get nr support_band,6,41,78,1,8,28,5'); print('OK'); sys.exit(0)
if cmd=='AT+SP5GCMDS="get nr synch_param",42':
 print('+SP5GCMDS: "get nr synch_param",42,'+s['vonr']); print('OK'); sys.exit(0)
if cmd in ('AT+SPFORCEFRQ=12,3','AT+SPFORCEFRQ=16,3'):
 tag=cmd.split('=')[1].split(',')[0]; cells=s.get('cell_read_override',{}).get(tag,s['cells'+tag])
 print('+SPFORCEFRQ: '+tag+',3'+(''.join(','+x for x in cells))); print('OK'); sys.exit(0)
if s.get('reject'): print('+CME ERROR: 3'); sys.exit(0)
if s.get('reject_restart') and cmd.startswith('AT+SFUN='): print('ERROR'); sys.exit(0)
for prefix,key in [('AT+SPTESTMODE=','tm'),('AT+SP5GRAN=','gran'),('AT+SPENDC=','endc'),
 ('AT+CAVIMS=','volte'),('AT+SP5GCMDS="set nr param",45,','vonr'),
 ('AT+SPLBAND=1,','lte'),('AT+SPLBAND=2,','nr')]:
 if cmd.startswith(prefix) and not s.get('ignore'): s[key]=cmd[len(prefix):]
if cmd.startswith('AT+SPTESTMODE=') and s.get('corrupt_mode_fields'): s['tm']=s['tm'].split(',')[0]+',134,0'
if cmd.startswith('AT+SPFORCEFRQ='):
 args=cmd.split('=')[1].split(','); key='cells'+args[0]
 if args[1]=='4': s[key]=[]
 elif args[1]=='6': s[key]=sorted(set(s[key]+[','.join(args[2:])]))
if cmd=='AT+SFUN=4' and s.get('drift'): s['lte']='0,0,0,0,0'
if cmd=='AT+SFUN=4' and s.get('reset_drift'): s['lte']='0,0,0,5,0'
f.write_text(json.dumps(s))
if s.get('omit_ok'): sys.exit(1)
print('OK')
''')
        at.chmod(0o755)
        self.extra = dict(MU300_AT=at, MU300_DASH_DIR=self.run,
                          UNISOC_APPLY_DIR=self.tmp / 'mutex', UNISOC_REPLAY_MARKER=self.tmp / 'marker')
        self.reset()

    def reset(self, **extra):
        state = dict(tm='134,128,1,0,0,0', gran='1', endc='1',
            lte='0,0,0,0,0', nr='0,0,0,0', cfun='1', cells12=['1650,10'], cells16=[],
            volte='1',vonr='0')
        state.update(extra)
        self.modem.write_text(json.dumps(state))
        (self.tmp / 'commands').write_text('')

    def apply(self, shell, kind, val):
        return self.script(shell, self.lock, 'apply', kind, val, **self.extra)

    def test_automatic_mode_override_and_reset_share_codec(self):
        for shell in self.each_shell():
            for default,choice,expected in [('134','default','134'),('134','152','152'),('152','default','152'),('152','134','134')]:
                for kind in ('mode','reset'):
                    self.reset(tm='131,128,1', gran='0')
                    result=self.script(shell,self.lock,'apply',kind,'auto',
                        **dict(self.extra,TEST_AUTO_MODE=choice,TEST_DEFAULT_MODE=default))
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(json.loads(self.modem.read_text())['tm'],expected+',128,1')

    def test_auto_mode_boot_replay_uses_selected_code_before_dial(self):
        for shell in self.each_shell():
            (self.tmp/'marker').unlink(missing_ok=True)
            self.reset(tm='134,128,1', gran='1')
            (self.state/'mode').write_text('auto')
            (self.state/'auto_apply').write_text('on')
            result=self.script(shell,self.lock,'replay','early',**dict(self.extra,TEST_AUTO_MODE='152'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(self.modem.read_text())['tm'],'152,128,1')
            self.assertNotIn('AT+SFUN=',(self.tmp/'commands').read_text())

    def test_second_slot_override_preserves_first_slot(self):
        for shell in self.each_shell():
            for kind in ('mode','reset'):
                self.reset(tm='128,131,0',gran='0')
                scoped=self.state/'sim1';scoped.mkdir(exist_ok=True)
                env=dict(self.extra,TEST_AUTO_MODE='152',MU300_SIM_SLOT='1',
                         MU300_STATE_DIR=scoped,MU300_DASH_DIR=self.run/'sim1')
                result=self.script(shell,self.lock,'apply',kind,'auto',**env)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(self.modem.read_text())['tm'],'128,152,0')

    def test_disabled_sim2_accepts_settings_without_enabling_rf(self):
        for shell in self.each_shell():
            for kind in ('mode','reset'):
                self.reset(tm='128,131,0',gran='0',cfun='4')
                (self.tmp/'sim-radio-policy').write_text('1 0\n')
                scoped=self.state/'sim1';scoped.mkdir(exist_ok=True)
                env=dict(self.extra,TEST_AUTO_MODE='152',MU300_SIM_SLOT='1',
                         MU300_STATE_DIR=scoped,MU300_DASH_DIR=self.run/'sim1')
                result=self.script(shell,self.lock,'apply',kind,'auto',**env)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(self.modem.read_text())['tm'],'128,152,0')
                self.assertEqual(json.loads(self.modem.read_text())['cfun'],'4')
                self.assertNotIn('AT+SFUN=',(self.tmp/'commands').read_text())

    def seed_locks(self, directory=None):
        directory = directory or self.state
        directory.mkdir(parents=True, exist_ok=True)
        saved = dict(mode='nsa', endc='off', lte='1,3', nr='41,78',
                     cell='lte:1650,10\nnr:627264,393', auto_apply='off')
        for key, value in saved.items():
            (directory / key).write_text(value)
        return saved

    def test_voice_switches_save_verified_state_and_do_not_cycle_radio(self):
        for shell in self.each_shell():
            self.reset()
            for kind, value in [('volte','off'), ('vonr','on')]:
                result=self.apply(shell,kind,value)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((self.state/kind).read_text(),value)
            current=json.loads((self.run/'lock.json').read_text())
            self.assertNotIn('ims',current)
            self.assertIs(current['volte']['enabled'],False)
            self.assertEqual(current['volte']['source'],'modem')
            self.assertIs(current['vonr']['enabled'],True)
            trace=(self.tmp/'commands').read_text()
            self.assertNotIn('SFUN',trace)
            self.assertNotIn('ifup',trace)
            self.assertNotIn('AT+IMSEN',trace)
            self.assertNotIn('AT+CASIMS=',trace)

    def test_non_voice_apply_keeps_both_live_voice_states(self):
        for shell in self.each_shell():
            self.reset()
            result = self.apply(shell, 'endc', 'off')
            self.assertEqual(result.returncode, 0, result.stderr)
            current = json.loads((self.run / 'lock.json').read_text())
            self.assertIs(current['volte']['enabled'], True)
            self.assertIs(current['vonr']['enabled'], False)
            self.assertEqual(current['volte']['source'], 'modem')
            self.assertFalse((self.run / 'voice-volte').exists())
            self.assertFalse((self.run / 'voice-vonr').exists())
            trace = (self.tmp / 'commands').read_text()
            self.assertNotIn('AT+CAVIMS=', trace)
            self.assertNotIn('AT+SFUN=', trace)

    def test_single_voice_apply_keeps_other_unmodified_voice_state(self):
        for shell in self.each_shell():
            self.reset()
            result = self.apply(shell, 'volte', 'off')
            self.assertEqual(result.returncode, 0, result.stderr)
            current = json.loads((self.run / 'lock.json').read_text())
            self.assertIs(current['volte']['enabled'], False)
            self.assertIs(current['vonr']['enabled'], False)
            self.assertFalse((self.run / 'voice-vonr').exists())

    def test_unrelated_reader_does_not_query_voice_during_apply(self):
        for shell in self.each_shell():
            self.reset()
            mutex = self.tmp / 'mutex'
            mutex.mkdir(exist_ok=True)
            # A live sibling worker owns the mutex, not the getter's parent.
            owner = subprocess.Popen(['/bin/sleep', '60'])
            (mutex / 'pid').write_text(str(owner.pid))
            try:
                result = self.script(shell, self.lock, 'get', 'fresh', **self.extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                trace = (self.tmp / 'commands').read_text()
                self.assertNotIn('AT+CAVIMS?', trace)
                self.assertNotIn('get nr synch_param', trace)
            finally:
                owner.terminate()
                owner.wait()
                (mutex / 'pid').unlink()
                mutex.rmdir()

    def test_voice_rejection_or_readback_mismatch_never_save(self):
        for shell in self.each_shell():
            for kind, value, fault in [('volte','off','reject'),
                                       ('volte','off','ignore'),('vonr','on','ignore')]:
                self.reset(**{fault:True})
                (self.state/kind).unlink(missing_ok=True)
                result=self.apply(shell,kind,value)
                self.assertNotEqual(result.returncode,0,(kind,fault))
                self.assertFalse((self.state/kind).exists())
                self.assertNotIn('SFUN',(self.tmp/'commands').read_text())

    def test_voice_only_replay_is_early_once_and_never_restarts(self):
        for shell in self.each_shell():
            self.reset()
            for kind,value in [('ims','off'),('volte','off'),('vonr','on')]:
                (self.state/kind).write_text(value)
            result=self.replay(shell,'early')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((self.tmp/'marker').exists())
            trace=(self.tmp/'commands').read_text()
            self.assertEqual(trace.count('AT+CAVIMS=0'),1)
            self.assertNotIn('SFUN',trace)
            self.assertNotIn('AT+IMSEN',trace)
            self.assertEqual(self.script(shell,self.lock,'replay','late',**self.extra).returncode,0)
            self.assertEqual((self.tmp/'commands').read_text(),trace)

    def test_failed_fresh_voice_query_never_falls_back_to_write_receipt(self):
        for shell in self.each_shell():
            self.reset()
            result=self.apply(shell,'volte','off')
            self.assertEqual(result.returncode,0,result.stderr)
            self.reset(no_voice_query=True)
            result=self.script(shell,self.lock,'get','fresh',**self.extra)
            current=json.loads(result.stdout)
            self.assertIn('mode',current)
            self.assertNotIn('ims',current)
            self.assertIsNone(current['volte']['enabled'])
            self.assertEqual(current['volte']['source'],'unknown')
            self.assertIs(current['vonr']['enabled'],False)

    def test_removed_ims_is_rejected_and_old_preference_is_ignored(self):
        for shell in self.each_shell():
            self.reset()
            (self.state/'ims').write_text('off')
            result=self.apply(shell,'ims','off')
            self.assertNotEqual(result.returncode,0)
            result=self.replay(shell,'early')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((self.tmp/'commands').read_text(),'')

    def test_native_cavims_read_does_not_assume_saved_preference_is_live(self):
        for shell in self.each_shell():
            self.reset()
            (self.state/'volte').write_text('off')
            result=self.script(shell,self.lock,'get','fresh',**self.extra)
            current=json.loads(result.stdout)
            self.assertIs(current['volte']['enabled'],True)
            self.assertEqual(current['volte']['saved'],'off')
            self.assertEqual((self.tmp/'commands').read_text().count('AT+CAVIMS?'),1)

    def test_secondary_voice_settings_do_not_write_primary_preferences(self):
        for shell in self.each_shell():
            self.reset()
            second=self.state/'sim1';second.mkdir(exist_ok=True)
            scoped=self.run/'sim1';scoped.mkdir(exist_ok=True)
            (self.state/'volte').write_text('on')
            result=self.script(shell,self.lock,'apply','volte','off',**dict(self.extra,
                MU300_SIM_SLOT='1',MU300_STATE_DIR=second,MU300_DASH_DIR=scoped))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((second/'volte').read_text(),'off')
            self.assertEqual((self.state/'volte').read_text(),'on')
            self.assertTrue((scoped/'voice-volte').exists())
            self.assertFalse((self.run/'voice-volte').exists())

    def test_reset_keeps_voice_preferences(self):
        for shell in self.each_shell():
            self.reset(ims='0',volte='0',vonr='1')
            for kind,val in [('ims','off'),('volte','off'),('vonr','on')]:
                (self.state/kind).write_text(val)
            result=self.apply(shell,'reset','auto')
            self.assertEqual(result.returncode,0,result.stderr)
            for kind,val in [('ims','off'),('volte','off'),('vonr','on')]:
                self.assertEqual((self.state/kind).read_text(),val)
            trace=(self.tmp/'commands').read_text()
            self.assertNotIn('AT+IMSEN=',trace)
            self.assertNotIn('AT+CAVIMS=',trace)

    def test_voice_only_boot_worker_detects_retained_settings(self):
        for shell in self.each_shell():
            for kind in ('volte','vonr'):
                with self.subTest(kind=kind):
                    self.reset()
                    path=self.state/kind;path.write_text('off')
                    (self.tmp/'marker').unlink(missing_ok=True)
                    env=dict(self.extra,UNISOC_AT_BIN=self.tmp/'fake-at',
                        UNISOC_LOCK_BIN=self.lock,UNISOC_EARLY_PENDING=self.tmp/'pending')
                    result=self.script(shell,LIB/'boot-replay',**env)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertTrue((self.tmp/'marker').exists())
                    self.assertNotIn('SFUN',(self.tmp/'commands').read_text())
                    path.unlink()

    def test_reset_clears_all_types_once_preserving_other_sim_and_identity(self):
        for shell in self.each_shell():
            for omit_ok in (False, True):
                self.reset(tm='131,128,1', gran='0', endc='0', lte='0,0,0,5,0',
                           nr='0,0,272,0', cells16=['627264,393'], omit_ok=omit_ok)
                self.seed_locks()
                other = self.seed_locks(self.state / 'sim1')
                (self.state / 'identity').mkdir(exist_ok=True)
                (self.state / 'identity/iccid').write_text('keep')
                r = self.apply(shell, 'reset', 'auto')
                if omit_ok:
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn('频段设置未确认', r.stderr)
                    self.assertTrue((self.state / 'lte').exists())
                    self.assertTrue((self.state / 'nr').exists())
                    self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())
                    continue
                self.assertEqual(r.returncode, 0, r.stderr)
                modem = json.loads(self.modem.read_text())
                for key, value in dict(tm='134,128,1', gran='1', endc='1',
                                       lte='0,0,0,0,0', nr='0,0,0,0', cells12=[], cells16=[]).items():
                    self.assertEqual(modem[key], value, key)
                for key in ('mode', 'endc', 'lte', 'nr', 'cell', 'reset_pending'):
                    self.assertFalse((self.state / key).exists(), key)
                self.assertEqual((self.state / 'auto_apply').read_text(), 'off')
                self.assertEqual((self.state / 'identity/iccid').read_text(), 'keep')
                for key, value in other.items():
                    self.assertEqual((self.state / 'sim1' / key).read_text(), value)
                commands = (self.tmp / 'commands').read_text()
                self.assertEqual(commands.count('AT+SFUN=5\n'), 1)
                self.assertEqual(commands.count('AT+SFUN=4\n'), 1)
                self.assertEqual(json.loads((self.run / 'lock.json').read_text())['cells'], [])

    def test_reset_slot1_keeps_slot0_state_and_mode_and_does_not_redial_it(self):
        for shell in self.each_shell():
            self.reset(tm='128,131,0', gran='0')
            original = self.seed_locks()
            self.seed_locks(self.state / 'sim1')
            env = dict(self.extra, MU300_SIM_SLOT='1', MU300_STATE_DIR=self.state / 'sim1',
                       MU300_DASH_DIR=self.run / 'sim1')
            r = self.script(shell, self.lock, 'apply', 'reset', 'auto', **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(self.modem.read_text())['tm'], '128,134,0')
            for key, value in original.items():
                self.assertEqual((self.state / key).read_text(), value)
            self.assertEqual(list(p.name for p in (self.state / 'sim1').iterdir()), ['auto_apply'])
            self.assertNotIn('ifup\n', (self.tmp / 'commands').read_text())

    def test_reset_invalidates_other_sim_cache_but_not_its_saved_settings(self):
        for shell in self.each_shell():
            for slot in ('0', '1'):
                self.reset()
                self.seed_locks()
                self.seed_locks(self.state / 'sim1')
                (self.run / 'sim1').mkdir(exist_ok=True)
                (self.run / 'lock.json').write_text('{}')
                (self.run / 'sim1/lock.json').write_text('{}')
                state = self.state if slot == '0' else self.state / 'sim1'
                cache = self.run if slot == '0' else self.run / 'sim1'
                other_state = self.state / 'sim1' if slot == '0' else self.state
                other_cache = self.run / 'sim1' if slot == '0' else self.run
                env = dict(self.extra, TEST_SIM_SLOTS='2', MU300_SIM_SLOT=slot,
                           MU300_STATE_DIR=state, MU300_DASH_DIR=cache)
                r = self.script(shell, self.lock, 'apply', 'reset', 'auto', **env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse((other_cache / 'lock.json').exists())
                self.assertTrue((cache / 'lock.json').exists())
                self.assertEqual((other_state / 'mode').read_text(), 'nsa')
                self.assertEqual((other_state / 'nr').read_text(), '41,78')

    def test_reset_failure_never_discards_saved_intent(self):
        for shell in self.each_shell():
            for failure in ('reject', 'ignore', 'silent', 'reject_restart', 'corrupt_mode_fields'):
                self.reset(tm='131,128,1', **{failure: True})
                original = self.seed_locks()
                r = self.apply(shell, 'reset', 'auto')
                self.assertNotEqual(r.returncode, 0, failure)
                for key, value in original.items():
                    self.assertEqual((self.state / key).read_text(), value, (failure, key))
                self.assertFalse((self.state / 'reset_pending').exists())
                if failure in ('reject', 'ignore', 'silent', 'corrupt_mode_fields'):
                    self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_reset_checks_each_readback_not_just_mode(self):
        for shell in self.each_shell():
            for key, value in [('endc', '0'), ('gran', '0')]:
                self.reset(read_override={key: value})
                original = self.seed_locks()
                r = self.apply(shell, 'reset', 'auto')
                self.assertNotEqual(r.returncode, 0, (key, value))
                self.assertEqual((self.state / 'mode').read_text(), original['mode'])
                self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_interrupted_reset_cleanup_prevents_replay_even_with_marker_and_auto_off(self):
        for shell in self.each_shell():
            for phase in ('early', 'late'):
                self.reset()
                self.seed_locks()
                other = self.seed_locks(self.state / 'sim1')
                (self.state / 'reset_pending').write_text('1')
                (self.tmp / 'marker').write_text('')
                r = self.script(shell, self.lock, 'replay', phase, **self.extra)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual((self.tmp / 'commands').read_text(), '')
                self.assertFalse((self.state / 'mode').exists())
                self.assertFalse((self.state / 'reset_pending').exists())
                self.assertEqual((self.state / 'auto_apply').read_text(), 'off')
                for key, value in other.items():
                    self.assertEqual((self.state / 'sim1' / key).read_text(), value)

    def test_reset_rejects_remaining_cells_on_either_rat(self):
        for shell in self.each_shell():
            for rat in ('12', '16'):
                self.reset(cell_read_override={rat: ['1650,10']})
                self.seed_locks()
                r = self.apply(shell, 'reset', 'auto')
                self.assertNotEqual(r.returncode, 0, rat)
                self.assertIn('清除回读不一致', r.stderr)
                self.assertTrue((self.state / 'cell').exists())
                self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_reset_barrier_publication_failure_keeps_all_old_files(self):
        # BusyBox ash may use its built-in mv instead of a PATH stub.
        # A shell function injects the same publication failure in every shell.
        self.lock.write_text('mv() { case "$2" in */reset_pending) return 1;; esac; command mv "$@"; }\n' + self.lock.read_text())
        for shell in self.each_shell():
            self.reset()
            original = self.seed_locks()
            r = self.apply(shell, 'reset', 'auto')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('旧锁定仍可能在开机回放', r.stderr)
            self.assertFalse((self.state / 'reset_pending').exists())
            for key, value in original.items():
                self.assertEqual((self.state / key).read_text(), value)

    def test_reset_persistence_failures_are_explicit_and_block_old_replay(self):
        for shell in self.each_shell():
            self.reset()
            self.seed_locks()
            (self.state / 'mode').unlink()
            (self.state / 'mode').mkdir()
            r = self.apply(shell, 'reset', 'auto')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('旧锁定回放已阻止', r.stderr)
            self.assertTrue((self.state / 'reset_pending').exists())
            self.reset()
            for phase in ('early', 'late'):
                self.assertNotEqual(self.replay(shell, phase).returncode, 0)
            self.assertNotEqual(self.apply(shell, 'endc', 'on').returncode, 0)
            self.assertEqual((self.tmp / 'commands').read_text(), '')
            (self.state / 'mode').rmdir()
            # Once storage is repaired, replay finishes cleanup without AT.
            self.assertEqual(self.replay(shell, 'early').returncode, 0)
            self.assertFalse((self.state / 'reset_pending').exists())
            self.assertEqual((self.tmp / 'commands').read_text(), '')

    def test_reset_job_done_only_after_verified_cleanup(self):
        for shell in self.each_shell():
            self.reset()
            self.seed_locks()
            job = json.loads(self.script(shell, self.lock, 'start', 'reset', 'auto', **self.extra).stdout)
            self.assertEqual(job['ok'], 1, job)
            self.assertEqual(job['kind'], 'reset')
            for _ in range(150):
                result = json.loads(self.script(shell, self.lock, 'status', job['id'], **self.extra).stdout)
                if result.get('state') in ('done', 'error'):
                    break
                time.sleep(.02)
            self.assertEqual(result['state'], 'done', result)
            self.assertEqual(result['ok'], 1)
            self.assertFalse((self.state / 'mode').exists())
            commands = (self.tmp / 'commands').read_text()
            self.script(shell, self.lock, 'status', job['id'], **self.extra)
            self.assertEqual((self.tmp / 'commands').read_text(), commands)

    def test_verified_mode_preserves_slot1_and_primary(self):
        for shell in self.each_shell():
            self.reset()
            r = self.apply(shell, 'mode', 'nsa')
            self.assertEqual(r.returncode, 0, r.stderr)
            commands = (self.tmp / 'commands').read_text()
            self.assertIn('AT+SPTESTMODE=131,128,1\n', commands)
            self.assertEqual((self.state / 'mode').read_text(), 'nsa')
            self.assertEqual(commands.count('AT+SFUN=5'), 1)
            cache = json.loads((self.run / 'lock.json').read_text())
            self.assertNotIn('6', cache['write_caps']['nr'].split(','))
            self.assertIn('6', cache['caps']['nr'].split(','))

    def test_missing_ok_can_only_succeed_with_real_readback(self):
        for shell in self.each_shell():
            self.reset(omit_ok=True)
            r = self.apply(shell, 'endc', 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.state / 'endc').read_text(), 'off')
            self.assertEqual(json.loads((self.run / 'lock.json').read_text())['endc'], '0')
            self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_slot1_mode_preserves_slot0_and_does_not_redial_slot0(self):
        for shell in self.each_shell():
            self.reset()
            r = self.script(shell, self.lock, 'apply', 'mode', 'nsa',
                            MU300_SIM_SLOT='1', **self.extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            commands = (self.tmp / 'commands').read_text()
            self.assertIn('AT+SPTESTMODE=134,131,1\n', commands)
            self.assertNotIn('ifup\n', commands)
            cache = json.loads((self.run / 'lock.json').read_text())
            self.assertEqual(cache['mode']['work'], '131')

    def test_endc_write_two_accepts_only_off_query_values(self):
        for shell in self.each_shell():
            for reply in ('0', '2'):
                self.reset(endc_off_reply=reply)
                r = self.apply(shell, 'endc', 'off')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('AT+SPENDC=2\n', (self.tmp / 'commands').read_text())
                self.assertEqual((self.state / 'endc').read_text(), 'off')
                self.assertEqual(json.loads((self.run / 'lock.json').read_text())['endc'], reply)
            for reply in ('', '1', '3', '-1', 'ERROR'):
                self.reset(endc_off_reply=reply)
                (self.state / 'endc').write_text('on')
                r = self.apply(shell, 'endc', 'off')
                self.assertNotEqual(r.returncode, 0, repr(reply))
                self.assertEqual((self.state / 'endc').read_text(), 'on')

    def test_endc_enable_cannot_be_confirmed_by_off_reply(self):
        for shell in self.each_shell():
            self.reset(ignore=True)
            state = json.loads(self.modem.read_text())
            state['endc'] = '0'
            self.modem.write_text(json.dumps(state))
            r = self.apply(shell, 'endc', 'on')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((self.state / 'endc').exists())

    def test_early_endc_off_replay_accepts_zero_without_sfun(self):
        for shell in self.each_shell():
            self.reset()
            (self.state / 'endc').write_text('off')
            (self.tmp / 'marker').unlink(missing_ok=True)
            r = self.script(shell, self.lock, 'replay', 'early', **self.extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((self.tmp / 'marker').exists())
            commands = (self.tmp / 'commands').read_text()
            self.assertIn('AT+SPENDC=2\n', commands)
            self.assertIn('AT+SPENDC?\n', commands)
            self.assertNotIn('SFUN', commands)

    def replay(self, shell, phase):
        (self.tmp / 'marker').unlink(missing_ok=True)
        return self.script(shell, self.lock, 'replay', phase, **self.extra)

    def test_late_replay_requires_readback_and_successful_activation(self):
        (self.state / 'lte').write_text('1')
        for shell in self.each_shell():
            for failure in ('reject', 'omit_ok', 'reject_restart', 'silent'):
                self.reset(**{failure: True})
                r = self.replay(shell, 'late')
                self.assertNotEqual(r.returncode, 0, failure)
                self.assertFalse((self.tmp / 'marker').exists(), failure)
                if failure in ('reject', 'omit_ok'):
                    self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_replay_preserves_mode_fields_and_refuses_missing_source(self):
        (self.state / 'mode').write_text('nsa')
        for shell in self.each_shell():
            self.reset()
            r = self.replay(shell, 'early')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('AT+SPTESTMODE=131,128,1\n', (self.tmp / 'commands').read_text())
            self.reset(silent=True)
            r = self.replay(shell, 'early')
            self.assertNotEqual(r.returncode, 0)
            commands = (self.tmp / 'commands').read_text()
            self.assertNotIn('AT+SPTESTMODE=', commands)
            self.assertNotIn('AT+SP5GRAN=', commands)

    def test_early_success_suppresses_all_late_replay_traffic(self):
        (self.state / 'lte').write_text('1')
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.replay(shell, 'early').returncode, 0)
            before = (self.tmp / 'commands').read_text()
            r = self.script(shell, self.lock, 'replay', 'late', **self.extra)
            self.assertEqual(r.returncode, 0)
            self.assertEqual((self.tmp / 'commands').read_text(), before)
            self.assertNotIn('SFUN', before)

    def test_replay_empty_unlock_and_final_cell_without_newline(self):
        for shell in self.each_shell():
            for value in ('', 'nr:627264,393'):
                self.reset()
                (self.state / 'cell').write_text(value)
                r = self.replay(shell, 'early')
                self.assertEqual(r.returncode, 0, r.stderr)
                modem = json.loads(self.modem.read_text())
                self.assertEqual(modem['cells12'], [])
                self.assertEqual(modem['cells16'], ['627264,393'] if value else [])

    def test_invalid_saved_replay_never_sends_partial_settings(self):
        (self.state / 'mode').write_text('4g')
        for shell in self.each_shell():
            for kind, value in [('endc', 'bad'), ('nr', '6'), ('lte', '79'), ('cell', 'bad:1,2')]:
                self.reset()
                (self.state / kind).write_text(value)
                r = self.replay(shell, 'early')
                self.assertNotEqual(r.returncode, 0, (kind, value))
                self.assertEqual((self.tmp / 'commands').read_text(), '')
                (self.state / kind).unlink()

    def test_replay_duplicate_bands_are_canonicalized(self):
        (self.state / 'lte').write_text('3,1,3')
        (self.state / 'nr').write_text('78,41,78')
        for shell in self.each_shell():
            self.reset()
            r = self.replay(shell, 'early')
            self.assertEqual(r.returncode, 0, r.stderr)
            modem = json.loads(self.modem.read_text())
            self.assertEqual(modem['lte'], '0,0,0,5,0')
            self.assertEqual(modem['nr'], '0,0,272,0')

    def test_replay_rejects_corrupt_mode_but_does_not_compare_band_readback(self):
        for shell in self.each_shell():
            (self.state / 'mode').write_text('nsa')
            self.reset(corrupt_mode_fields=True)
            self.assertNotEqual(self.replay(shell, 'early').returncode, 0)
            self.assertFalse((self.tmp / 'marker').exists())
            (self.state / 'mode').unlink()
            for kind, raw in [('lte','ERROR'), ('lte','0,0'), ('nr','0,0'), ('nr','bad')]:
                (self.state / kind).write_text('')
                self.reset(read_override={kind:raw})
                self.assertEqual(self.replay(shell, 'early').returncode, 0, (kind,raw))
                self.assertTrue((self.tmp / 'marker').exists())
                (self.state / kind).unlink()

    def test_replay_silent_setter_requires_fresh_proof_but_error_always_fails(self):
        (self.state / 'endc').write_text('on')
        for shell in self.each_shell():
            self.reset(omit_ok=True)
            self.assertEqual(self.replay(shell, 'early').returncode, 0)
            self.reset(reject=True) # readback was already on, but ERROR is not success
            self.assertNotEqual(self.replay(shell, 'early').returncode, 0)
            self.assertFalse((self.tmp / 'marker').exists())

    def test_rejected_and_unacknowledged_band_writes_do_not_save_or_restart(self):
        for shell in self.each_shell():
            for failure in ('reject', 'omit_ok'):
                self.reset(**{failure: True})
                (self.state / 'lte').write_text('3')
                r = self.apply(shell, 'lte', '1')
                self.assertNotEqual(r.returncode, 0, failure)
                self.assertEqual((self.state / 'lte').read_text(), '3')
                self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_band_mask_change_after_restart_is_still_saved(self):
        for shell in self.each_shell():
            self.reset(drift=True)
            (self.state / 'lte').write_text('3')
            r = self.apply(shell, 'lte', '1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.state / 'lte').read_text(), '1')
            self.assertEqual(json.loads(self.modem.read_text())['lte'], '0,0,0,0,0')

    def test_acknowledged_band_subset_is_saved_and_cache_shows_actual(self):
        for shell in self.each_shell():
            for kind, val, actual in [('lte', '1,3', '0,0,0,1,0'), ('nr', '1,41,78', '0,0,16,0')]:
                self.reset(read_override={kind: actual})
                r = self.apply(shell, kind, val)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual((self.state / kind).read_text(), val)
                commands = (self.tmp / 'commands').read_text()
                self.assertEqual(commands.count('AT+SFUN=5'), 1)
                cache = json.loads((self.run / 'lock.json').read_text())
                self.assertEqual(cache[kind]['locked'], '1' if kind == 'lte' else '41')

    def test_acknowledged_band_drift_or_unchanged_mask_does_not_fail_replay(self):
        for shell in self.each_shell():
            for phase in ('early', 'late'):
                for failure in ('ignore', 'drift'):
                    self.reset(**{failure: True})
                    (self.state / 'lte').write_text('1')
                    r = self.replay(shell, phase)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue((self.tmp / 'marker').exists())
                    self.assertEqual((self.state / 'lte').read_text(), '1')
                    if phase == 'early': self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_reset_does_not_require_band_mask_equality(self):
        for shell in self.each_shell():
            for key, value in [('lte', '0,0,0,1,0'), ('nr', '0,0,256,0'), ('lte', 'ERROR'), ('nr', '0,0')]:
                self.reset(read_override={key: value})
                self.seed_locks()
                r = self.apply(shell, 'reset', 'auto')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse((self.state / 'lte').exists())
                self.assertFalse((self.state / 'nr').exists())

    def test_band_missing_ok_is_not_proven_by_matching_old_mask(self):
        for shell in self.each_shell():
            self.reset(omit_ok=True, lte='0,0,0,1,0')
            r = self.apply(shell, 'lte', '1')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('频段设置未确认', r.stderr)
            self.assertFalse((self.state / 'lte').exists())
            self.assertNotIn('SFUN', (self.tmp / 'commands').read_text())

    def test_restart_error_restores_radio_attempt_but_does_not_save(self):
        for shell in self.each_shell():
            self.reset(reject_restart=True)
            r = self.apply(shell, 'lte', '1')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('协议栈重启被拒绝', r.stderr)
            self.assertIn('AT+SFUN=4', (self.tmp / 'commands').read_text())
            self.assertFalse((self.state / 'lte').exists())

    def test_persistence_error_is_not_reported_as_success(self):
        (self.state / 'endc').mkdir()
        for shell in self.each_shell():
            self.reset()
            r = self.apply(shell, 'endc', 'off')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('设置保存失败', r.stderr)

    def test_invalid_inputs_never_send_at(self):
        for shell in self.each_shell():
            for kind, val in [('lte', '1,79'), ('nr', '6'), ('nr','78;reboot'), ('nr','1,,2'),
                              ('mode','bogus'), ('endc','2'), ('cell','nr:123,1008'),
                              ('cell','lte:123,504'), ('cell','other:1,2'), ('auto_apply','yes'),
                              ('reset',''), ('reset','all'), ('reset','auto;reboot')]:
                self.reset()
                r = self.apply(shell, kind, val)
                self.assertNotEqual(r.returncode, 0, (kind,val))
                self.assertEqual((self.tmp / 'commands').read_text(), '')

    def test_manual_cell_channel_boundaries(self):
        for shell in self.each_shell():
            for value in ('lte:0,0', 'lte:262143,503', 'nr:1,0', 'nr:3279165,1007'):
                self.reset()
                result = self.apply(shell, 'cell', value)
                self.assertEqual(result.returncode, 0, (value, result.stderr))
                self.assertIn(value, (self.state / 'cell').read_text().splitlines())
            for value in ('nr:0,0', 'lte:262144,0', 'nr:3279166,0', 'lte:1,-1', 'nr:1,1008'):
                self.reset()
                result = self.apply(shell, 'cell', value)
                self.assertNotEqual(result.returncode, 0, value)
                self.assertEqual((self.tmp / 'commands').read_text(), '')

    def test_duplicate_bands_and_multi_rat_cells(self):
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.apply(shell, 'nr', '78,41,78').returncode, 0)
            self.assertEqual((self.state / 'nr').read_text(), '41,78')
            r = self.apply(shell, 'cell', 'nr:627264,393')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.state / 'cell').read_text(), 'lte:1650,10\nnr:627264,393\n')
            self.assertEqual(self.apply(shell, 'cell', 'off-nr').returncode, 0)
            self.assertEqual((self.state / 'cell').read_text(), 'lte:1650,10\n')
            self.assertEqual(self.apply(shell, 'cell', 'off').returncode, 0)
            self.assertEqual((self.state / 'cell').read_text(), '')

    def test_job_completion_and_status_have_no_at_for_auto_apply(self):
        for shell in self.each_shell():
            self.reset()
            start = self.script(shell, self.lock, 'start', 'auto_apply', 'off', **self.extra)
            job = json.loads(start.stdout)
            self.assertEqual(job['ok'], 1, start.stderr)
            self.assertRegex(job['id'], r'^[A-Za-z0-9]{10}$')
            result = {}
            for _ in range(50):
                result = json.loads(self.script(shell, self.lock, 'status', job['id'], **self.extra).stdout)
                if result.get('state') in ('done','error'): break
                time.sleep(.02)
            self.assertEqual(result['state'], 'done', result)
            self.assertEqual(result['ok'], 1)
            self.assertEqual((self.state / 'auto_apply').read_text(), 'off')
            self.assertEqual((self.tmp / 'commands').read_text(), '')

    def test_expired_missing_and_dead_job_results_are_explicit(self):
        for shell in self.each_shell():
            for id, age, pid in [('a'*10, 300, '1'), ('b'*10, 5, '99999999')]:
                path = self.run / 'lock-jobs' / id
                path.mkdir(parents=True, exist_ok=True)
                path.joinpath('result').write_text(json.dumps(dict(id=id, state='running')))
                path.joinpath('started').write_text(str(int(float(Path('/proc/uptime').read_text().split()[0]))-age))
                path.joinpath('pid').write_text(pid)
                r = self.script(shell, self.lock, 'status', id, **self.extra)
                self.assertEqual(json.loads(r.stdout)['state'], 'error', r)
            for id in ('../lock.json', 'missing000'):
                r = self.script(shell, self.lock, 'status', id, **self.extra)
                self.assertEqual(json.loads(r.stdout)['state'], 'error')

    def test_job_reports_real_modem_failure(self):
        for shell in self.each_shell():
            self.reset(reject=True)
            job = json.loads(self.script(shell, self.lock, 'start', 'endc', 'off', **self.extra).stdout)
            for _ in range(50):
                result = json.loads(self.script(shell, self.lock, 'status', job['id'], **self.extra).stdout)
                if result['state'] in ('done','error'): break
                time.sleep(.02)
            self.assertEqual(result['state'], 'error')
            self.assertEqual(result['ok'], 0)
            self.assertIn('模组拒绝', result['error'])
            self.assertFalse((self.state / 'endc').exists())

    def test_dead_mutex_reclaimed_but_live_owner_not_removed(self):
        for shell in self.each_shell():
            mutex = self.tmp / 'mutex'
            mutex.mkdir(exist_ok=True)
            (mutex / 'pid').write_text('99999999')
            self.assertEqual(self.apply(shell, 'endc', 'on').returncode, 0)
            mutex.mkdir(exist_ok=True)
            (mutex / 'pid').write_text('1')
            self.reset()
            r = self.apply(shell, 'endc', 'off')
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((mutex / 'pid').read_text(), '1')
            self.assertEqual((self.tmp / 'commands').read_text(), '')
            shutil.rmtree(mutex)


class LockFrontend(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_result_waiter(self):
        r = subprocess.run(['node', str(TOP / 'tests/dashboard_lock_jobs.js')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_packaging_and_no_timestamp_success(self):
        for name in ('home', 'locks'):
            src = (LIB.parents[3] / f'htdocs/luci-static/resources/view/mu300/{name}.js').read_text()
            self.assertNotIn('readback(Date.now()', src)
            self.assertNotIn('35000', src)
        for path in ('tools/build-openwrt-tf-magisk.sh','tools/make-release.sh'):
            for name in ('lock-apply.sh','lock-jobs.sh'):
                self.assertIn('/unisoc-modem/'+name, (TOP / path).read_text())
