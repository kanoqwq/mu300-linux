import json
import os
from helpers import ShellTest, TOP

SCRIPT=TOP/'rootfs/overlay/opt/mu300/bin/mu300-dual-radio'

class DualRadio(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('mu300-device', 'echo "${TEST_DEVICE:-f50}"')
        self.stub('uci', 'case "$3" in *.dual_standby) echo "${TEST_DSDS:-1}";; *.data_sim) echo "${TEST_DATA_SIM:-0}";; esac')
        self.run=self.tmp/'run'
        for directory,n in [('mu300-at',0),('mu300-at4',3)]:
            p=self.run/directory/'urc';p.mkdir(parents=True)
            (p/f'stty_nr{n}.ready').write_text(str(os.getpid()))
        client=self.tmp/'client'
        client.write_text('''#!/usr/bin/env python3
import json,os,sys,re
from pathlib import Path
p=Path(os.environ['STUBLOG']);f=p/'radio.json';s=json.loads(f.read_text());cmd=sys.argv[-1]
with (p/'commands').open('a') as log:log.write(cmd+'\\n')
slot=int(re.search(r'SPACTCARD=(\\d)',cmd)[1])
if cmd.endswith('+CFUN?'):print('+CFUN: '+str(s['cfun'][slot]))
elif cmd.endswith('+CPIN?'):
 if os.getenv('PIN_ERROR'):print('+CME ERROR: '+os.environ['PIN_ERROR'])
 elif s.get('sim_power',[1,1])[slot] and os.getenv('SIM_BUSY')=='1':print('+CME ERROR: 14')
 else:print('+CPIN: READY' if s.get('sim_power',[1,1])[slot] and slot in s.get('present',[0,1]) else '+CME ERROR: 10')
elif cmd.endswith('+SPTESTMODE?'):print('+SPTESTMODE: 134,128,1,20,3,1')
elif cmd.endswith('+SPSWDATA?'):print('+SPSWDATA: '+str(s['data']))
elif cmd.endswith('+SPSWDATA'):s['data']=slot
elif cmd.endswith('+SFUN=4'):
 if os.getenv('FAIL_RESUME')=='1':print('ERROR');sys.exit(1)
 s['cfun'][slot]=1
elif cmd.endswith('+SFUN=2'):
 if os.getenv('FAIL_SIM_POWER')=='1':print('ERROR');sys.exit(1)
 s['cfun'][slot]=0
 if 'sim_power' in s:s['sim_power'][slot]=1
elif cmd.endswith('+SFUN=5'):s['cfun'][slot]=int(os.getenv('OFF_STATE','2'))
f.write_text(json.dumps(s));print('OK')
''')
        client.chmod(0o755)
        self.extra=dict(MU300_DUAL_AT_CLIENT=client,MU300_DUAL_RUN=self.run,MU300_SIM_SLOT='1')
        (self.tmp/'sim-radio-policy').write_text('1 1\n')

    def test_u30pro_uses_inline_data_channel_urc_owners(self):
        for shell in self.each_shell():
            self.reset([0,0])
            for directory,n in [('mu300-at2',0),('mu300-at5',3)]:
                p=self.run/directory/'urc';p.mkdir(parents=True,exist_ok=True)
                (p/f'stty_nr{n}.ready').write_text(str(os.getpid()))
            # Legacy URC markers are deliberately gone; they are not U30 Pro owners.
            for directory,n in [('mu300-at',0),('mu300-at4',3)]:
                (self.run/directory/'urc'/f'stty_nr{n}.ready').unlink(missing_ok=True)
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30pro'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((self.run/'mu300-dual-radio-ready').exists())
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,1])
            # One missing native owner must reject before any radio command.
            self.reset([0,0])
            (self.run/'mu300-at5/urc/stty_nr3.ready').unlink()
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30pro'))
            self.assertNotEqual(result.returncode,0)
            self.assertIn('URC reader not ready',result.stderr)
            self.assertEqual((self.tmp/'commands').read_text(),'')

    def test_u30_single_standby_boot_closes_peer_and_keeps_both_off_policy(self):
        for shell in self.each_shell():
            for selected in ('0','1'):
                for state in ([0,0],[1,1]):
                    self.reset(state)
                    (self.tmp/'sim-radio-policy').write_text('1 1\n')
                    r=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air',TEST_DSDS='0',TEST_DATA_SIM=selected,MU300_SIM_SLOT=selected))
                    self.assertEqual(r.returncode,0,r.stderr)
                    current=json.loads((self.tmp/'radio.json').read_text())['cfun']
                    self.assertEqual(current[int(selected)],1)
                    self.assertIn(current[1-int(selected)],(0,2))
            self.reset([0,0])
            (self.tmp/'sim-radio-policy').write_text('0 0\n')
            r=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air',TEST_DSDS='0'))
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('SFUN=4',(self.tmp/'commands').read_text())

    def test_default_boot_keeps_sim2_rf_off(self):
        for shell in self.each_shell():
            (self.tmp/'sim-radio-policy').unlink(missing_ok=True)
            self.reset([0,0])
            result=self.script(shell,SCRIPT,**dict(self.extra,MU300_SIM_SLOT='0'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,2])
            self.assertNotIn('AT+SPACTCARD=1;+SFUN=4',(self.tmp/'commands').read_text())

    def test_u30_unpowered_inserted_sims_are_not_absent(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,sim_power=[0,0])))
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,1])
            commands=(self.tmp/'commands').read_text().splitlines()
            for slot in (0,1):
                power=f'AT+SPACTCARD={slot};+SFUN=2'
                self.assertEqual(commands.count(power),1)
                start=commands.index(power)
                self.assertEqual(commands[start+1],f'AT+SPACTCARD={slot};+CPIN?')
            self.assertTrue((self.run/'mu300-dual-radio-ready').exists())

    def test_u30_powered_probe_still_skips_a_real_empty_slot(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,sim_power=[0,0],present=[1])))
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[0,1])
            commands=(self.tmp/'commands').read_text()
            self.assertNotIn('AT+SPACTCARD=0;+SFUN=4',commands)
            self.assertNotIn('AT+SPACTCARD=0;+SPTESTMODEM=',commands)

    def test_u30_power_probe_does_not_override_explicit_rf_permissions(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'sim-radio-policy').write_text('1 0\n')
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,sim_power=[0,0])))
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air',MU300_SIM_SLOT='0'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,2])
            self.assertNotIn('AT+SPACTCARD=1;+SFUN=4',(self.tmp/'commands').read_text())

    def test_u30_failed_or_busy_sim_probe_never_commits_ready(self):
        for shell in self.each_shell():
            for flag in ('FAIL_SIM_POWER','SIM_BUSY'):
                self.reset([0,0])
                (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,sim_power=[0,0])))
                result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air',**{flag:'1'}))
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertFalse((self.run/'mu300-dual-radio-ready').exists())
                self.assertNotIn('SFUN=4',(self.tmp/'commands').read_text())

    def test_cme_100_is_not_cme_10_absence(self):
        for shell in self.each_shell():
            for model in ('u30air','f50','v50'):
                self.reset([0,0])
                result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE=model,PIN_ERROR='100'))
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertFalse((self.run/'mu300-dual-radio-ready').exists())
                self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_u30_presence_preflight_is_cold_only(self):
        for shell in self.each_shell():
            self.reset([1,0])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[1,0],data=0,sim_power=[1,0],present=[0])))
            (self.run/'mu300-dual-radio-ready').touch()
            result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE='u30air',MU300_SIM_SLOT='0'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_u30_presence_power_step_is_not_used_on_other_boards(self):
        for shell in self.each_shell():
            for model in ('f50','v50'):
                self.reset([0,0])
                (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,sim_power=[0,0])))
                result=self.script(shell,SCRIPT,**dict(self.extra,TEST_DEVICE=model))
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def reset(self,cfun):
        (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=cfun,data=0)))
        (self.tmp/'commands').write_text('')
        (self.run/'mu300-dual-radio-ready').unlink(missing_ok=True)

    def test_fresh_shared_initialization_preserves_modes_and_order(self):
        for shell in self.each_shell():
            self.reset([0,0])
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            commands=(self.tmp/'commands').read_text().splitlines()
            writes=[s for s in commands if not s.endswith('?')]
            self.assertEqual(writes,[
                'AT+SPACTCARD=0;+SFUN=2','AT+SPACTCARD=1;+SFUN=2',
                'AT+SPACTCARD=1;+SPTESTMODEM=134,128','AT+SPACTCARD=0;+SPTESTMODEM=134,128',
                'AT+SPACTCARD=1;+SPSWDATA','AT+SPACTCARD=0;+SFUN=4','AT+SPACTCARD=1;+SFUN=4'])

    def test_already_ready_is_read_only_and_mixed_state_is_refused(self):
        for shell in self.each_shell():
            for cfun,ok in [([1,1],True),([1,0],False),([0,1],False)]:
                self.reset(cfun)
                r=self.script(shell,SCRIPT,**self.extra)
                self.assertEqual(r.returncode==0,ok,r.stderr)
                if not ok: self.assertEqual(r.returncode,4)
                self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_initialized_mixed_state_resumes_only_the_requested_off_radio(self):
        for shell in self.each_shell():
            for cfun in ([1,0],[0,1],[1,2],[2,1],[1,4]):
                self.reset(cfun)
                (self.tmp/'sim-radio-policy').write_text('1 1\n')
                (self.run/'mu300-dual-radio-ready').touch()
                r=self.script(shell,SCRIPT,**self.extra)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,1])
                writes=[c for c in (self.tmp/'commands').read_text().splitlines() if not c.endswith('?')]
                off=0 if cfun[0]!=1 else 1
                self.assertEqual(writes,[f'AT+SPACTCARD={off};+SFUN=4'])

    def test_initialized_resume_keeps_disabled_or_absent_peer_off(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'sim-radio-policy').write_text('1 0\n')
            (self.run/'mu300-dual-radio-ready').touch()
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,0])
            self.assertNotIn('AT+SPACTCARD=1;+SFUN',(self.tmp/'commands').read_text())
            self.reset([0,1])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,1],data=1,present=[1])))
            (self.tmp/'sim-radio-policy').write_text('1 1\n')
            (self.run/'mu300-dual-radio-ready').touch()
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_initialized_resume_failure_does_not_reset_or_select_another_card(self):
        for shell in self.each_shell():
            self.reset([0,1])
            (self.tmp/'sim-radio-policy').write_text('1 1\n')
            (self.run/'mu300-dual-radio-ready').touch()
            r=self.script(shell,SCRIPT,**dict(self.extra,FAIL_RESUME='1'))
            self.assertNotEqual(r.returncode,0)
            self.assertIn('radio-on rejected',r.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[0,1])
            writes=[c for c in (self.tmp/'commands').read_text().splitlines() if not c.endswith('?')]
            self.assertEqual(writes,['AT+SPACTCARD=0;+SFUN=4'])

    def test_no_urc_reader_means_no_at_traffic(self):
        for shell in self.each_shell():
            self.reset([0,0])
            ready=self.run/'mu300-at4/urc/stty_nr3.ready'
            ready.write_text('99999999')
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual((self.tmp/'commands').read_text(),'')
            ready.write_text(str(os.getpid()))

    def test_saved_rf_combinations_survive_cold_and_warm_initialization(self):
        for shell in self.each_shell():
            for a in (0,1):
                for b in (0,1):
                    (self.tmp/'sim-radio-policy').write_text(f'{a} {b}\n')
                    self.reset([0,0])
                    result=self.script(shell,SCRIPT,**self.extra)
                    self.assertEqual(result.returncode,0,result.stderr)
                    expected=[1 if a else 2,1 if b else 2]
                    self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],expected)
                    commands=(self.tmp/'commands').read_text()
                    for card,enabled in enumerate((a,b)):
                        self.assertEqual(f'AT+SPACTCARD={card};+SFUN=4' in commands,bool(enabled))
                    (self.tmp/'commands').write_text('')
                    result=self.script(shell,SCRIPT,**self.extra)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_both_disabled_cold_boot_prepares_modes_without_starting_rf(self):
        for shell in self.each_shell():
            (self.tmp/'sim-radio-policy').write_text('0 0\n')
            self.reset([0,0])
            result=self.script(shell,SCRIPT,**dict(self.extra,OFF_STATE='0'))
            self.assertEqual(result.returncode,0,result.stderr)
            commands=(self.tmp/'commands').read_text()
            for slot in (0,1):
                self.assertIn(f'AT+SPACTCARD={slot};+SFUN=2',commands)
                self.assertIn(f'AT+SPACTCARD={slot};+SPTESTMODEM=',commands)
                self.assertIn(f'AT+SPACTCARD={slot};+SFUN=5',commands)
                self.assertNotIn(f'AT+SPACTCARD={slot};+SFUN=4',commands)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[0,0])
            (self.tmp/'commands').write_text('')
            result=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_warm_restart_enforces_only_the_disabled_slot(self):
        for shell in self.each_shell():
            for a,b in ((0,1),(1,0),(0,0)):
                (self.tmp/'sim-radio-policy').write_text(f'{a} {b}\n')
                self.reset([1,1])
                r=self.script(shell,SCRIPT,**self.extra)
                self.assertEqual(r.returncode,0,r.stderr)
                writes=[c for c in (self.tmp/'commands').read_text().splitlines() if not c.endswith('?')]
                self.assertEqual(writes,[f'AT+SPACTCARD={slot};+SFUN=5' for slot,on in enumerate((a,b)) if not on])

    def test_zero_off_readback_and_flight_mode_migration_do_not_reset_live_peer(self):
        for shell in self.each_shell():
            (self.tmp/'sim-radio-policy').write_text('1 0\n')
            for state in ([1,1],[1,4]):
                self.reset(state)
                result=self.script(shell,SCRIPT,**dict(self.extra,OFF_STATE='0'))
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,0])
                writes=[c for c in (self.tmp/'commands').read_text().splitlines() if not c.endswith('?')]
                self.assertEqual(writes,['AT+SPACTCARD=1;+SFUN=5'])
                (self.tmp/'commands').write_text('')
                result=self.script(shell,SCRIPT,**self.extra)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())

    def test_empty_slot_does_not_block_the_inserted_card(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,present=[1])))
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[0,1])
            writes=[s for s in (self.tmp/'commands').read_text().splitlines() if not s.endswith('?')]
            self.assertEqual(writes,['AT+SPACTCARD=1;+SFUN=2','AT+SPACTCARD=1;+SPTESTMODEM=134,128',
                                     'AT+SPACTCARD=1;+SPSWDATA','AT+SPACTCARD=1;+SFUN=4'])

    def test_only_sim0_and_already_active_sim1(self):
        for shell in self.each_shell():
            self.reset([0,0])
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,0],data=0,present=[0])))
            r=self.script(shell,SCRIPT,**dict(self.extra,MU300_SIM_SLOT='0'))
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads((self.tmp/'radio.json').read_text())['cfun'],[1,0])
            self.assertNotIn('AT+SPACTCARD=1;+SFUN',(self.tmp/'commands').read_text())
            (self.tmp/'radio.json').write_text(json.dumps(dict(cfun=[0,1],data=1,present=[1])))
            (self.tmp/'commands').write_text('')
            r=self.script(shell,SCRIPT,**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('SFUN=',(self.tmp/'commands').read_text())
