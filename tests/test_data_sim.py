"""Isolated netifd/data-card transactions: no radio, network, or real UCI."""
import json
from helpers import ShellTest, TOP

SCRIPT=TOP/'rootfs/overlay/opt/mu300/bin/mu300-data-sim'

class DataSim(ShellTest):
    def setUp(self):
        ShellTest.setUp(self)
        self.db=self.tmp/'state.json'
        self.envlib=self.tmp/'sim-env'
        self.envlib.write_text('''mu300_sim_env() {
MU300_SIM_DEVICE=card$1
MU300_SIM_SHARED=$STUBLOG/at
}
''')
        (self.tmp/'at').mkdir()
        import os
        os.mkfifo(self.tmp/'at/cmd')
        driver=self.tmp/'driver'
        driver.write_text('''#!/usr/bin/env python3
import json,sys,os
from pathlib import Path
p=Path(os.environ['STUBLOG']); f=p/'state.json'; s=json.loads(f.read_text())
name=sys.argv[1]; a=sys.argv[2:]
with (p/'commands').open('a') as log: log.write(name+' '+repr(a)+'\\n')
def save(): f.write_text(json.dumps(s))
if name=='uci':
 if a[:2]==['-q','get']:
  k=a[2].split('.')[-1]
  print({'data_sim':s['saved'],'sim_slots':s.get('slots',2),'data_interface':'wan','dual_standby':os.getenv('TEST_DSDS','')}.get(k,''))
 elif a[:2]==['-q','set']:
  s['saved']=int(a[2].split('=')[1]); save()
 elif a[:2]==['-q','commit'] and s.get('fail_commit'):
  s['fail_commit']=False; save(); sys.exit(1)
elif name=='ubus':
 if 'notify_proto' in a:
  update=json.loads(a[-1]); assert update=={'action':5,'available':True}
  s['available']=True
  if s.get('autostart',True): s['up']=True
  save(); print('{}')
 else:
  print(json.dumps({'up':s['up'],'pending':s.get('pending',False),'available':s.get('available',True),'autostart':s.get('autostart',True),'l3_device':'card'+str(s['actual'])}))
elif name=='ifdown':
 if not s.get('stuck'): s['up']=False; s['autostart']=False; save()
elif name=='ifup':
 s['autostart']=True; save()
 if s.get('available',True) and (s['saved']!=1 or not s.get('fail_target')):
  s['actual']=s['saved']; s['up']=True; save()
elif name=='mu300-at':
 cmd=a[-1]
 if cmd.endswith('+SPSWDATA?'): print('+SPSWDATA: '+str(s['actual'])+'\\nOK')
 elif cmd.endswith('+CPIN?'):
  if s.get('absent_target') and 'SPACTCARD=1;' in cmd: print('+CME ERROR: 10')
  else: print('+CPIN: '+('SIM PIN' if s.get('pin') else 'READY')+'\\nOK')
 elif cmd.endswith('+SPSWDATA'):
  target=int(cmd.split('SPACTCARD=')[1][0])
  if not (target==1 and s.get('fail_switch')): s['actual']=target; save()
  print('OK')
elif name=='jsonfilter':
 try: print(str(json.load(sys.stdin)[a[-1][2:]]).lower())
 except: sys.exit(1)
''')
        driver.chmod(0o755)
        for name in ('uci','ubus','ifdown','ifup','mu300-at','jsonfilter'):
            self.stub(name,f'exec "{driver}" {name} "$@"')
        self.stub('sleep', ':')
        (self.tmp/'ready').touch()
        # BusyBox ash can run ifdown/ifup as applets before consulting PATH.
        # Keep all test network operations inside these explicit fixtures.
        self.controller=self.tmp/'data-sim'
        source=SCRIPT.read_text()
        shims='\n'.join(name+'() { "$STUBLOG/stubs/'+name+'" "$@"; }' for name in ('ifdown','ifup','sleep'))
        self.controller.write_text(source.replace('#!/bin/sh\n','#!/bin/sh\n'+shims+'\n',1))
        self.extra=dict(MU300_SIM_ENV_LIB=self.envlib,MU300_SIM_MUTEX=self.tmp/'mutex',MU300_DUAL_READY=self.tmp/'ready',MU300_DATA_SIM_ACTUAL=self.tmp/'actual',TEST_DSDS='1')
        self.stub('radio-control','[ "$*" = "--switch-enable 1 1" ] || exit 2; [ "${FAIL_RADIO_ENABLE:-0}" != 1 ] || exit 1; printf "1 1\\n" > "$UNISOC_RADIO_POLICY"; echo enable-sim2 >> "$STUBLOG/commands"')
        self.extra['MU300_RADIO_CONTROL']=self.stubs/'radio-control'

    def u30_radio_fixture(self):
        self.extra['TEST_DSDS']='0'
        self.stub('mu300-device','echo "${TEST_SINGLE_MODEL:-'+getattr(self,'single_model','u30air')+'}"')
        self.stub('radio-control','''
case "$1:$2:$3" in --switch-policy:[01]:[01]) ;; *) exit 2;; esac
echo "radio $2 $3" >> "$STUBLOG/commands"
[ "${FAIL_RADIO_ENABLE:-0}:$3" != 1:1 ] || exit 1
flags=$(cat "$UNISOC_RADIO_POLICY")
if [ "$3" = 1 ]; then
    if [ "$2" = 0 ]; then flags='1 0'; else flags='0 1'; fi
elif [ "$2" = 0 ]; then flags="0 ${flags#* }"; else flags="${flags% *} 0"; fi
printf '%s\\n' "$flags" > "$UNISOC_RADIO_POLICY"
''')

    def test_u30_single_standby_switch_and_reverse_are_explicit_rf_opt_ins(self):
        self.u30_radio_fixture()
        for shell in self.each_shell():
            self.reset()
            (self.tmp/'sim-radio-policy').write_text('1 0\n')
            for target in ('1','0'):
                r=self.run_switch(shell,target)
                self.assertEqual(r.returncode,0,r.stderr)
                state=json.loads(self.db.read_text())
                self.assertEqual((state['saved'],state['actual']),(int(target),int(target)))
                self.assertEqual((self.tmp/'sim-radio-policy').read_text().strip(),'0 1' if target=='1' else '1 0')
            commands=(self.tmp/'commands').read_text()
            self.assertLess(commands.index('ifdown'),commands.index('radio 1 1'))

    def test_u30_failed_switch_restores_original_or_both_off_permissions(self):
        self.u30_radio_fixture()
        for shell in self.each_shell():
            for flags in ('1 0','0 0'):
                self.reset(fail_switch=True)
                (self.tmp/'sim-radio-policy').write_text(flags+'\n')
                r=self.run_switch(shell,'1')
                self.assertNotEqual(r.returncode,0)
                self.assertIn('sim_switch_failed_restored',r.stderr)
                self.assertEqual((self.tmp/'sim-radio-policy').read_text().strip(),flags)
                self.assertEqual(json.loads(self.db.read_text())['saved'],0)

    def test_u30_manual_data_off_stays_off_and_missing_sim_does_not_change_rf(self):
        self.u30_radio_fixture()
        for shell in self.each_shell():
            self.reset(autostart=False,up=False)
            (self.tmp/'sim-radio-policy').write_text('0 0\n')
            r=self.run_switch(shell,'1')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('ifup',(self.tmp/'commands').read_text())
            self.reset(absent_target=True)
            (self.tmp/'sim-radio-policy').write_text('1 0\n')
            self.assertNotEqual(self.run_switch(shell,'1').returncode,0)
            self.assertNotIn('radio ',(self.tmp/'commands').read_text())

    def test_f50_single_standby_switch_failure_and_data_off_follow_same_policy(self):
        self.single_model='f50'
        self.test_u30_single_standby_switch_and_reverse_are_explicit_rf_opt_ins()
        self.test_u30_failed_switch_restores_original_or_both_off_permissions()
        self.test_u30_manual_data_off_stays_off_and_missing_sim_does_not_change_rf()

    def test_first_switch_to_sim2_remembers_rf_opt_in(self):
        for shell in self.each_shell():
            (self.tmp/'sim-radio-policy').unlink(missing_ok=True)
            self.reset()
            result=self.run_switch(shell,'1')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((self.tmp/'sim-radio-policy').read_text(),'1 1\n')
            self.assertLess((self.tmp/'commands').read_text().index('enable-sim2'),(self.tmp/'commands').read_text().index('ifdown'))
            result=self.run_switch(shell,'0')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((self.tmp/'sim-radio-policy').read_text(),'1 1\n')

    def test_failed_enable_or_absent_sim2_leaves_current_data_untouched(self):
        for shell in self.each_shell():
            for absent in (False,True):
                (self.tmp/'sim-radio-policy').unlink(missing_ok=True)
                self.reset(absent_target=absent)
                result=self.script(shell,self.controller,'apply','1',**dict(self.extra,FAIL_RADIO_ENABLE='1'))
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('ifdown',(self.tmp/'commands').read_text())
                self.assertEqual(json.loads(self.db.read_text())['actual'],0)

    def test_legacy_sim2_opt_in_is_preserved_when_returning_to_sim1(self):
        for shell in self.each_shell():
            (self.tmp/'sim-radio-policy').unlink(missing_ok=True)
            self.reset(saved=1,actual=1)
            result=self.run_switch(shell,'0')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((self.tmp/'sim-radio-policy').read_text(),'1 1\n')

    def reset(self, **values):
        state=dict(saved=0,actual=0,up=True)
        state.update(values)
        self.db.write_text(json.dumps(state))
        (self.tmp/'commands').write_text('')

    def run_switch(self,shell,target):
        return self.script(shell,self.controller,'apply',target,**self.extra)

    def test_same_card_never_disconnects(self):
        for shell in self.each_shell():
            self.reset()
            r=self.run_switch(shell,'0')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertNotIn('ifdown',(self.tmp/'commands').read_text())
            self.assertNotIn('ifup',(self.tmp/'commands').read_text())

    def test_select_actual_card_resumes_wan_waiting_on_old_saved_card(self):
        for shell in self.each_shell():
            for target in (0,1):
                for available in (False,True):
                    self.reset(saved=1-target,actual=target,up=False,available=available)
                    r=self.run_switch(shell,str(target))
                    self.assertEqual(r.returncode,0,r.stderr)
                    state=json.loads(self.db.read_text())
                    self.assertEqual((state['saved'],state['actual'],state['up']),(target,target,True))
                    commands=(self.tmp/'commands').read_text()
                    self.assertNotIn('ifdown',commands)
                    self.assertEqual(commands.count('ifup '),1)
                    self.assertEqual(commands.count("'notify_proto'"),0 if available else 1)

    def test_select_actual_card_preserves_manual_off_and_running_wan(self):
        for shell in self.each_shell():
            for status in ({'up':False,'autostart':False},{'up':True},{'up':False,'pending':True}):
                self.reset(saved=0,actual=1,available=False,**status)
                r=self.run_switch(shell,'1')
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(json.loads(self.db.read_text())['saved'],1)
                commands=(self.tmp/'commands').read_text()
                self.assertNotIn('ifup',commands)
                self.assertNotIn('ifdown',commands)
                self.assertNotIn("'notify_proto'",commands)

    def test_switch_from_unavailable_old_card_reenables_protocol(self):
        for shell in self.each_shell():
            self.reset(saved=0,actual=0,up=False,available=False)
            r=self.run_switch(shell,'1')
            self.assertEqual(r.returncode,0,r.stderr)
            state=json.loads(self.db.read_text())
            self.assertEqual((state['saved'],state['actual'],state['up']),(1,1,True))
            self.assertIn("'notify_proto'",(self.tmp/'commands').read_text())

    def test_validated_switch_and_reverse(self):
        for shell in self.each_shell():
            self.reset()
            for target in ('1','0'):
                r=self.run_switch(shell,target)
                self.assertEqual(r.returncode,0,r.stderr)
                s=json.loads(self.db.read_text())
                self.assertEqual((s['saved'],s['actual'],s['up']),(int(target),int(target),True))
            self.assertFalse((self.tmp/'mutex').exists())

    def test_manually_disabled_data_stays_off_on_other_card(self):
        for shell in self.each_shell():
            self.reset(autostart=False)
            state=json.loads(self.db.read_text());state['up']=False
            self.db.write_text(json.dumps(state))
            r=self.run_switch(shell,'1')
            self.assertEqual(r.returncode,0,r.stderr)
            state=json.loads(self.db.read_text())
            self.assertEqual((state['saved'],state['actual'],state['up']),(1,1,False))
            self.assertNotIn('ifup',(self.tmp/'commands').read_text())

    def test_failed_selection_restores_original(self):
        for shell in self.each_shell():
            self.reset(fail_switch=True)
            r=self.run_switch(shell,'1')
            self.assertNotEqual(r.returncode,0)
            self.assertIn('sim_switch_failed_restored',r.stderr)
            s=json.loads(self.db.read_text())
            self.assertEqual((s['saved'],s['actual'],s['up']),(0,0,True))

    def test_offline_card_can_be_selected_and_stays_selected(self):
        for shell in self.each_shell():
            self.reset(fail_target=True)
            r=self.run_switch(shell,'1')
            self.assertEqual(r.returncode,0,r.stderr)
            s=json.loads(self.db.read_text())
            self.assertEqual((s['saved'],s['actual'],s['up']),(1,1,False))
            r=self.script(shell,SCRIPT,'get',**self.extra)
            view=json.loads(r.stdout)
            self.assertEqual((view['saved_sim'],view['actual_sim'],view['data_connected']),(1,1,False))
            self.assertNotIn("mu300-at ['-t', '3', 'AT+SPACTCARD=0;+SPSWDATA?']",(self.tmp/'commands').read_text().split('ifup')[-1])

    def test_unready_and_invalid_inputs_do_not_disconnect(self):
        for shell in self.each_shell():
            for target in ('2','../1','1'):
                self.reset(pin=True)
                r=self.run_switch(shell,target)
                self.assertNotEqual(r.returncode,0)
                self.assertNotIn('ifdown',(self.tmp/'commands').read_text())

    def test_mutex_refuses_concurrent_modem_changes(self):
        for shell in self.each_shell():
            self.reset()
            (self.tmp/'mutex').mkdir()
            r=self.run_switch(shell,'1')
            self.assertIn('modem_busy',r.stderr)
            self.assertNotIn('mu300-at',(self.tmp/'commands').read_text())
            (self.tmp/'mutex').rmdir()

    def test_missing_internal_card_is_reported_without_disconnecting_the_external_card(self):
        for shell in self.each_shell():
            self.reset(absent_target=True)
            r=self.run_switch(shell,'1')
            self.assertNotEqual(r.returncode,0)
            self.assertIn('sim_not_ready',r.stderr)
            self.assertNotIn('ifdown',(self.tmp/'commands').read_text())
            self.assertEqual(json.loads(self.db.read_text()),
                             dict(saved=0,actual=0,up=True,absent_target=True))
