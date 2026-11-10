"""Stock board variants, physical-vs-logical slots and broker-only mux changes."""
import json
import os
from helpers import BIN, TOP, ShellTest

LIB = TOP/'rootfs/overlay/opt/mu300/lib/v50-card.sh'
CONTROL = BIN/'mu300-v50-sim'


class V50Cards(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root'; (self.root/'proc/device-tree/chosen').mkdir(parents=True)
        self.args=self.root/'proc/device-tree/chosen/bootargs'
        self.args.write_bytes(b'console=ttyS1 androidboot.zte_boardid=1\0')
        self.run=self.tmp/'run'; (self.run/'mu300').mkdir(parents=True)
        (self.run/'mu300-dual-radio-ready').touch(); (self.run/'mu300-ril-handshake').touch()
        (self.run/'mu300-data-sim-actual').write_text('0\n')
        for n in ('mu300-at','mu300-at4'):
            (self.run/n).mkdir();os.mkfifo(self.run/n/'cmd')
        self.hw=self.tmp/'mux';self.hw.mkdir()
        for n,v in [('chip_select','0'),('psim_select','0')]: (self.hw/n).write_text(v+'\n')
        self.state=self.tmp/'state.json'
        self.initial=dict(data_sim=0,v50_card=0,up=True,autostart=True,radio=[1,1],cfun=[1,1])
        self.state.write_text(json.dumps(self.initial))
        driver=self.tmp/'driver.py'
        driver.write_text(r'''
import json,sys,os,subprocess,re
from pathlib import Path
p=Path(os.environ['STUBLOG']);s=json.loads((p/'state.json').read_text());name=sys.argv[1];a=sys.argv[2:]
def save(): (p/'state.json').write_text(json.dumps(s))
def log(t):
 with (p/'commands').open('a') as f:f.write(t+'\n')
if name=='uci':
 if a[:2]==['-q','get']:
  k=a[2].split('.')[-1]
  if k=='state_dir':print(p/'identity/lock-state.d')
  else:print(s.get(k,''))
 elif a[:2]==['-q','set']:
  k,v=a[2].split('=',1);key=k.split('.')[-1]
  if key==os.environ.get('FAIL_SAVE_KEY') and not (p/'save-failed-once').exists():
   (p/'save-failed-once').touch();sys.exit(1)
  s[key]=int(v);save()
 elif a[:2]==['-q','delete']:s.pop(a[2].split('.')[-1],None);save()
elif name=='ubus':print(json.dumps(dict(up=s['up'],pending=False,autostart=s['autostart'])))
elif name=='jsonfilter':
 try:print(str(json.load(sys.stdin)[a[-1][2:]]).lower())
 except Exception:sys.exit(1)
elif name in ('ifdown','ifup'):
 log(name);s['up']=name=='ifup';save()
elif name=='mu300-device':print(os.environ.get('TEST_MODEL','v50'))
elif name=='mu300-radio-allowed':sys.exit(0 if s['radio'][int(a[0])] else 1)
elif name=='sleep':log('sleep '+a[0])
elif name=='mu300-at':
 cmd=a[-1];slot=int(re.search(r'SPACTCARD=([01]);',cmd)[1]);log(cmd)
 if 'CFUN?' in cmd:print('+CFUN: '+str(s['cfun'][slot]))
 elif 'SPSWDATA?' in cmd:print('+SPSWDATA: '+str(s.get('actual',s['data_sim'])))
 elif 'CPIN?' in cmd:
  count=s.get('pin_queries',0);s['pin_queries']=count+1;save()
  if count<int(os.environ.get('PIN_DELAY','0')):print('+CME ERROR: 10');sys.exit(0)
  print('+CPIN: READY')
 else:
  f=int(cmd.rsplit('=',1)[-1]);s['cfun'][slot]=1 if f==4 else 0;save()
  if f==2 and os.environ.get('FAIL_POWER_ON')=='1' and str(slot)==os.environ.get('FAIL_POWER_SLOT',str(slot)) and not (p/'failed-once').exists():
   (p/'failed-once').touch();print('ERROR');sys.exit(1)
 print('OK')
elif name=='mobile-data':
 assert a[0]=='radio-locked',a
 lock=p/'run/mu300-radio-on.owner';lock.symlink_to(str(os.getpid()))
 try:sys.exit(subprocess.run(a[1:]).returncode)
 finally:lock.unlink(missing_ok=True)
elif name=='mu300-data-sim':
 log('logical '+a[-1]);assert os.environ.get('MU300_DATA_SIM_GATE_HELD')=='1'
 assert (p/'run/unisoc-data-sim-switch/pid').read_text().strip()==str(os.getppid())
 if os.environ.get('FAIL_LOGICAL')=='1' and not (p/'failed-logical-once').exists():
  (p/'failed-logical-once').touch();sys.exit(1)
 s['data_sim']=int(a[-1]);(p/'run/mu300-data-sim-actual').write_text(a[-1]+'\n');save()
else:sys.exit(2)
''')
        for n in ['uci','ubus','jsonfilter','ifup','ifdown','mu300-device','mu300-radio-allowed','sleep','mu300-at','mobile-data','mu300-data-sim']:
            self.stub(n,f'exec python3 "{driver}" {n} "$@"')
        self.stub('mu300-v50-sim',f'exec /bin/sh "{CONTROL}" "$@"')
        self.stub('logger', ':')
        self.dash=self.tmp/'dash';self.dash.mkdir()
        (self.dash/'sim-scope.sh').write_text('sim_scope() { export MU300_SIM_SLOT=$1; }\n')
        replay=self.dash/'lock';replay.write_text('#!/bin/sh\necho "replay $MU300_SIM_SLOT $*" >> "$STUBLOG/commands"\n');replay.chmod(0o755)
        # BusyBox may resolve ifdown/ifup/sleep as applets before PATH.
        # Keep every test call, including the nested RF worker, in fixtures.
        self.controller=self.tmp/'v50-sim'
        shims='\n'.join(n+'() { "$STUBLOG/stubs/'+n+'" "$@"; }' for n in ['ifdown','ifup','sleep'])
        self.controller.write_text(CONTROL.read_text().replace('#!/bin/sh\n','#!/bin/sh\n'+shims+'\n',1))
        self.stub('mu300-v50-sim',f'exec /bin/sh "{self.controller}" "$@"')
        self.stub('euicc-guard', '[ "$1" = --guard ] || exit 1; shift; export UNISOC_EUICC_GUARDED=1; exec "$@"')
        self.extra=dict(MU300_EUICC_TOOL=self.stubs/'euicc-guard',MU300_SYSROOT=self.root,MU300_BIN=self.stubs,MU300_V50_CARD_LIB=LIB,
                        MU300_V50_RUN=self.run,MU300_V50_MUX=self.hw,MU300_DASH_CACHE_ROOT=self.tmp/'cache',MU300_DASH_BIN=self.dash)
        self.pair=self.tmp/'v50-pair'
        self.pair.write_text((BIN/'mu300-v50-card-pair').read_text().replace('#!/bin/sh\n','#!/bin/sh\n'+shims+'\n',1))
        self.extra['MU300_V50_CONTROL']=self.stubs/'mu300-v50-sim'

    def run_pair(self,shell,*args,**env):
        return self.script(shell,self.pair,*args,**dict(self.extra,**env))

    def test_preinit_restores_pair_before_cp_without_at_or_config_changes(self):
        for shell in self.each_shell():
            state=dict(self.initial,v50_route0=3,v50_route1=2)
            self.state.write_text(json.dumps(state));before=self.state.read_bytes()
            for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
            result=self.call(shell,'preinit');self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([int((self.hw/n).read_text()) for n in ('chip_select','psim_select')],[1,1])
            self.assertEqual(self.state.read_bytes(),before)
            self.assertFalse((self.tmp/'commands').exists())
            (self.run/'mu300-v50-cp-started').touch()
            self.assertEqual(self.call(shell,'preinit').returncode,0)
            (self.run/'mu300-v50-cp-started').unlink()

    def test_preinit_preserves_unsaved_and_legacy_peer_selector(self):
        for shell in self.each_shell():
            for state,want in [({},[1,0]),(dict(self.initial,v50_card=3),[1,1])]:
                self.state.write_text(json.dumps(state))
                (self.hw/'chip_select').write_text('1\n');(self.hw/'psim_select').write_text('0\n')
                result=self.call(shell,'preinit');self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual([int((self.hw/n).read_text()) for n in ('chip_select','psim_select')],want)
            self.assertFalse((self.tmp/'commands').exists())

    def test_preinit_rejects_invalid_pair_and_live_changes_before_gpio_write(self):
        for shell in self.each_shell():
            before={p.name:p.read_bytes() for p in self.hw.iterdir()}
            for state in (dict(self.initial,v50_route0=3),dict(self.initial,v50_route0=3,v50_route1=3)):
                self.state.write_text(json.dumps(state))
                self.assertNotEqual(self.call(shell,'preinit').returncode,0)
                self.assertEqual(before,{p.name:p.read_bytes() for p in self.hw.iterdir()})
            self.state.write_text(json.dumps(dict(self.initial,v50_route0=3,v50_route1=2)))
            (self.run/'mu300-v50-cp-started').touch()
            result=self.call(shell,'preinit');self.assertIn('modem_busy',result.stderr)
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.hw.iterdir()})
            (self.run/'mu300-v50-cp-started').unlink()

    def test_preinit_unknown_board_keeps_stock_and_non_v50_cannot_write(self):
        for shell in self.each_shell():
            before={p.name:p.read_bytes() for p in self.hw.iterdir()}
            self.args.write_bytes(b'androidboot.zte_boardid=99\0')
            self.assertEqual(self.call(shell,'preinit').returncode,0)
            self.args.write_bytes(b'androidboot.zte_boardid=1\0')
            for model in ('f50','u30air'):
                self.assertNotEqual(self.call(shell,'preinit',TEST_MODEL=model).returncode,0)
                self.assertNotEqual(self.run_pair(shell,'apply','3','2',TEST_MODEL=model).returncode,0)
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.hw.iterdir()})
            self.assertFalse((self.tmp/'commands').exists())

    def test_preinit_is_in_the_common_loader_before_modem_control_exec(self):
        loader=(BIN/'android-vendor-start').read_text()
        self.assertLess(loader.index('mu300-v50-sim preinit'),loader.index('exec chroot $A "$modem_exe"'))
        self.assertLess(loader.index('mu300-v50-cp-started'),loader.index('exec chroot $A "$modem_exe"'))
        self.assertIn('CONFIG_ZTE_V50_SIM=y',(TOP/'upstream/mu300-mainline.config').read_text())
        assembly=(TOP/'openwrt/build-rootfs.sh').read_text()
        self.assertIn('chmod 0755 $R/opt/mu300/bin/mu300-v50-card-pair',assembly)

    def test_production_shell_syntax(self):
        import subprocess
        for shell in self.each_shell():
            for path in (BIN/'mu300-v50-sim',BIN/'mu300-v50-card-pair',BIN/'android-vendor-start',BIN/'mobile-data',
                         TOP/'openwrt/build-rootfs.sh',TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/data-sim'):
                if path.read_text().startswith('#!/bin/bash') and os.path.basename(shell[0])!='bash':
                    continue
                result=subprocess.run(shell+['-n',str(path)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,(str(path),result.stderr))

    def test_saved_source_status_rejects_wrong_bank_and_multiline_values(self):
        for shell in self.each_shell():
            for a,b,want in [(3,2,[3,2]),(1,3,[None,None]),('0\n3','1\n2',[None,None])]:
                self.state.write_text(json.dumps(dict(self.initial,v50_route0=a,v50_route1=b)))
                result=self.call(shell,'status');self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['saved_routes'],want)

    def test_pair_validates_every_stock_board_without_any_at(self):
        allowed={0:([0],[1,2]),1:([0,3],[1,2]),2:([0,3],[1]),3:([0],[1])}
        for shell in self.each_shell():
            for board,(first,second) in allowed.items():
                self.args.write_bytes(f'androidboot.zte_boardid={board}\0'.encode())
                for a,b in [(0,1),(0,2),(3,1),(3,2),(1,0),(3,3),(99,1)]:
                    result=self.run_pair(shell,'validate',str(a),str(b))
                    self.assertEqual(result.returncode==0,a in first and b in second,(board,a,b,result.stderr))
        self.assertFalse((self.tmp/'commands').exists())

    def test_pair_changes_only_peer_and_keeps_data_and_rf_intent(self):
        for shell in self.each_shell():
            state=dict(self.initial,radio=[1,0]);self.state.write_text(json.dumps(state))
            (self.hw/'chip_select').write_text('0\n');(self.hw/'psim_select').write_text('0\n')
            (self.tmp/'commands').unlink(missing_ok=True)
            result=self.run_pair(shell,'apply','0','2');self.assertEqual(result.returncode,0,result.stderr)
            after=json.loads(self.state.read_text())
            self.assertEqual([after['v50_route0'],after['v50_route1']],[0,2])
            self.assertEqual((after['data_sim'],after['radio'],after['up']),(0,[1,0],True))
            calls=(self.tmp/'commands').read_text()
            self.assertNotIn('ifdown',calls);self.assertNotIn('ifup',calls)
            self.assertNotIn('logical',calls);self.assertNotIn('SFUN=4',calls)

    def test_pair_routes_both_stacks_restores_before_cp_and_runtime_boot_is_noop(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial))
            (self.hw/'chip_select').write_text('0\n');(self.hw/'psim_select').write_text('0\n')
            (self.tmp/'commands').unlink(missing_ok=True)
            result=self.run_pair(shell,'apply','3','2');self.assertEqual(result.returncode,0,result.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertEqual((calls.count('ifdown'),calls.count('ifup')),(1,1))
            self.assertNotIn('logical',calls)
            self.assertEqual(json.loads(self.state.read_text())['data_sim'],0)
            for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
            (self.tmp/'commands').unlink()
            self.assertEqual(self.call(shell,'preinit').returncode,0)
            result=self.sh(shell,'"$MU300_BIN/mobile-data" radio-locked "$MU300_BIN/mu300-v50-sim" boot',**self.extra)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((self.tmp/'commands').exists())

    def test_pair_failed_second_route_restores_both_gpio_settings_and_wan(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial))
            for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
            # Fail the second bank specifically, after the first one changed.
            (self.tmp/'failed-once').unlink(missing_ok=True)
            result=self.run_pair(shell,'apply','3','2',FAIL_POWER_ON='1',FAIL_POWER_SLOT='1')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('sim_switch_failed_restored',result.stderr)
            self.assertEqual([int((self.hw/n).read_text()) for n in ('chip_select','psim_select')],[0,0])
            state=json.loads(self.state.read_text());self.assertTrue(state['up'])
            self.assertNotIn('v50_route0',state);self.assertNotIn('v50_route1',state)
            self.assertFalse((self.run/'mu300/v50-switch-incomplete').exists())

    def test_single_card_switch_updates_the_saved_pair_instead_of_reverting_next_boot(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(dict(self.initial,v50_route0=0,v50_route1=1)))
            (self.hw/'chip_select').write_text('0\n');(self.hw/'psim_select').write_text('0\n')
            result=self.call(shell,'apply','2');self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads(self.state.read_text())
            self.assertEqual([state['v50_route0'],state['v50_route1'],state['v50_card'],state['data_sim']],[0,2,2,1])

    def test_ui_job_adapter_dispatches_valid_pair_and_rejects_invalid_board_pair(self):
        (self.dash/'lock-jobs.sh').write_text((TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/lock-jobs.sh').read_text())
        adapter=self.tmp/'job-adapter'
        adapter.write_text((TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/data-sim').read_text().replace('/opt/mu300/bin/',str(self.stubs)+'/'))
        self.stub('mu300-v50-card-pair',f'exec /bin/sh "{self.pair}" "$@"')
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial))
            for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
            result=self.script(shell,adapter,'apply','data','v50-pair-3-2',**self.extra)
            self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads(self.state.read_text())
            self.assertEqual([state['v50_route0'],state['v50_route1']],[3,2])
            (self.tmp/'commands').unlink(missing_ok=True)
            for bad in ('v50-pair-3-3','v50-pair-9-1','v50-pair-3-2;reboot'):
                result=self.script(shell,adapter,'apply','data',bad,**self.extra)
                self.assertNotEqual(result.returncode,0,bad)
            self.assertFalse((self.tmp/'commands').exists())

    def test_noop_pair_keeps_manual_data_off_and_runtime_boot_restores_both_routes(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(dict(self.initial,autostart=False,up=False)))
            for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
            (self.tmp/'commands').unlink(missing_ok=True)
            result=self.run_pair(shell,'apply','0','1');self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((self.tmp/'commands').exists())
            self.state.write_text(json.dumps(dict(self.initial,v50_route0=3,v50_route1=2)))
            result=self.sh(shell,'"$MU300_BIN/mobile-data" radio-locked "$MU300_BIN/mu300-v50-sim" boot',**self.extra)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([int((self.hw/n).read_text()) for n in ('chip_select','psim_select')],[1,1])
            calls=(self.tmp/'commands').read_text()
            self.assertIn('AT+SPACTCARD=0;+SFUN=2',calls)
            self.assertIn('AT+SPACTCARD=1;+SFUN=2',calls)
            self.assertNotIn('SFUN=4',calls);self.assertNotIn('ifdown',calls)

    def test_save_failure_restores_pair_and_same_source_data_selection(self):
        for shell in self.each_shell():
            for same_source in (False,True):
                self.state.write_text(json.dumps(dict(self.initial,v50_route0=0,v50_route1=1)))
                for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
                (self.tmp/'save-failed-once').unlink(missing_ok=True)
                if same_source:result=self.call(shell,'apply','1',FAIL_SAVE_KEY='v50_route1')
                else:result=self.run_pair(shell,'apply','3','2',FAIL_SAVE_KEY='v50_route1')
                self.assertNotEqual(result.returncode,0)
                state=json.loads(self.state.read_text())
                self.assertEqual([state['v50_route0'],state['v50_route1'],state['v50_card'],state['data_sim']],[0,1,0,0])
                self.assertEqual([int((self.hw/n).read_text()) for n in ('chip_select','psim_select')],[0,0])

    def test_pair_busy_and_not_ready_never_touch_gpio_at_or_existing_owner(self):
        for shell in self.each_shell():
            for reason in ('data','lock','not-ready'):
                gate=self.run/('unisoc-data-sim-switch' if reason=='data' else 'unisoc-modem-lock-apply')
                if reason=='not-ready':(self.run/'mu300-dual-radio-ready').unlink()
                else:gate.mkdir();(gate/'pid').write_text('existing-owner')
                before=self.state.read_bytes()
                gpio={p.name:p.read_bytes() for p in self.hw.iterdir()}
                try:
                    result=self.run_pair(shell,'apply','3','2')
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual(self.state.read_bytes(),before)
                    self.assertEqual(gpio,{p.name:p.read_bytes() for p in self.hw.iterdir()})
                    self.assertFalse((self.tmp/'commands').exists())
                    if reason!='not-ready':self.assertEqual((gate/'pid').read_text(),'existing-owner')
                finally:
                    if reason=='not-ready':(self.run/'mu300-dual-radio-ready').touch()
                    else:(gate/'pid').unlink();gate.rmdir()

    def call(self,shell,*args,**env):
        return self.script(shell,self.controller,*args,**dict(self.extra,**env))

    def test_stock_board_map_and_reject_unfitted_cards(self):
        for shell in self.each_shell():
            for board,ids in [(0,[0,1,2]),(1,[0,1,2,3]),(2,[0,1,3]),(3,[0,1])]:
                self.args.write_bytes(f'androidboot.zte_boardid={board}\0'.encode())
                r=self.call(shell,'status');self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(json.loads(r.stdout)['cards'],ids)
                for n in range(4):
                    r=self.call(shell,'validate',str(n))
                    self.assertEqual(r.returncode==0,n in ids,(board,n,r.stderr))
        self.assertFalse((self.tmp/'commands').exists(),'status/validation sent a modem command')

    def test_identity_slot_names_follow_live_mux_and_only_fitted_positions(self):
        for shell in self.each_shell():
            for board,cards in [(0,[0,1,2]),(1,[0,1,2,3]),(2,[0,1,3]),(3,[0,1])]:
                self.args.write_bytes(f'androidboot.zte_boardid={board}\0'.encode())
                for chip in (0,1):
                    for psim in (0,1):
                        (self.hw/'chip_select').write_text(str(chip))
                        (self.hw/'psim_select').write_text(str(psim))
                        status=json.loads(self.call(shell,'status').stdout)
                        routes=[3 if psim else 0,2 if chip else 1]
                        self.assertEqual(status['logical_cards'],[r if r in cards else None for r in routes])
                        self.assertEqual(status['model'],'v50')
        self.assertFalse((self.tmp/'commands').exists())

    def test_board_source_precedence_and_unknown_hardware_do_not_write(self):
        for shell in self.each_shell():
            (self.root/'proc/driver').mkdir(exist_ok=True)
            board=self.root/'proc/driver/board_id';board.write_text('3\n')
            self.assertEqual(json.loads(self.call(shell,'status').stdout)['board_id'],3)
            r=self.call(shell,'apply','3');self.assertNotEqual(r.returncode,0)
            board.write_text('9\n')
            self.assertFalse(json.loads(self.call(shell,'status').stdout)['supported'])
            r=self.call(shell,'apply','0');self.assertNotEqual(r.returncode,0)
            board.unlink()
            r=self.call(shell,'status',TEST_MODEL='f50');self.assertFalse(json.loads(r.stdout)['supported'])
            r=self.call(shell,'apply','0',TEST_MODEL='u30air');self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.tmp/'commands').exists())

    def test_each_mux_bank_is_powered_down_before_change_and_other_bank_is_untouched(self):
        for shell in self.each_shell():
            for target,attribute,stack in [(2,'chip_select',1),(3,'psim_select',0)]:
                self.state.write_text(json.dumps(self.initial));(self.tmp/'commands').unlink(missing_ok=True)
                (self.hw/'chip_select').write_text('0\n');(self.hw/'psim_select').write_text('0\n')
                r=self.call(shell,'apply',str(target));self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual((self.hw/attribute).read_text().strip(),'1')
                other='psim_select' if stack else 'chip_select'
                self.assertEqual((self.hw/other).read_text().strip(),'0')
                calls=(self.tmp/'commands').read_text()
                off=f'AT+SPACTCARD={stack};+SFUN=5';power=f'AT+SPACTCARD={stack};+SFUN=3'
                on=f'AT+SPACTCARD={stack};+SFUN=2';rf=f'AT+SPACTCARD={stack};+SFUN=4'
                self.assertLess(calls.index(off),calls.index(power));self.assertLess(calls.index(power),calls.index('sleep 5'))
                self.assertLess(calls.index('sleep 5'),calls.index(on));self.assertLess(calls.index(on),calls.index(rf))
                self.assertLess(calls.index(on),calls.index(f'replay {stack} replay early'))
                self.assertLess(calls.index(f'replay {stack} replay early'),calls.index(rf))
                self.assertNotIn(f'AT+SPACTCARD={1-stack};+SFUN',calls)
                self.assertEqual(json.loads(self.state.read_text())['v50_card'],target)
                self.assertFalse((self.run/'mu300/sim-sequence').exists())
                self.assertFalse((self.run/'mu300/v50-switch-incomplete').exists())

    def test_unchanged_route_has_no_power_cycle_and_manual_data_off_stays_off(self):
        for shell in self.each_shell():
            s=dict(self.initial,up=False,autostart=False);self.state.write_text(json.dumps(s))
            (self.tmp/'commands').unlink(missing_ok=True)
            r=self.call(shell,'apply','0');self.assertEqual(r.returncode,0,r.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertNotIn('SFUN',calls);self.assertNotIn('ifup',calls)
            self.assertFalse(json.loads(self.state.read_text())['up'])

    def test_same_online_card_never_interrupts_wan(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial));(self.tmp/'commands').unlink(missing_ok=True)
            r=self.call(shell,'apply','0');self.assertEqual(r.returncode,0,r.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertNotIn('ifdown',calls);self.assertNotIn('ifup',calls);self.assertNotIn('SFUN',calls)

    def test_power_or_logical_failure_restores_old_mux_and_preference(self):
        for shell in self.each_shell():
            for failure in ('FAIL_POWER_ON','FAIL_LOGICAL'):
                self.state.write_text(json.dumps(self.initial));(self.hw/'chip_select').write_text('0\n')
                for name in ('failed-once','failed-logical-once'): (self.tmp/name).unlink(missing_ok=True)
                r=self.call(shell,'apply','2',**{failure:'1'})
                self.assertNotEqual(r.returncode,0)
                self.assertIn('sim_switch_failed_restored',r.stderr)
                self.assertEqual((self.hw/'chip_select').read_text().strip(),'0')
                state=json.loads(self.state.read_text())
                self.assertEqual(state['v50_card'],0);self.assertEqual(state['data_sim'],0)
                self.assertTrue(state['up']);self.assertFalse((self.run/'mu300/v50-switch-incomplete').exists())

    def test_internal_sequence_cannot_run_without_existing_rf_owner(self):
        for shell in self.each_shell():
            r=self.call(shell,'--route','2');self.assertNotEqual(r.returncode,0)
            self.assertIn('modem_busy',r.stderr)
        self.assertFalse((self.tmp/'commands').exists())

    def test_sim_ready_can_arrive_after_power_command_without_early_rollback(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial));(self.hw/'chip_select').write_text('0\n')
            r=self.call(shell,'apply','2',PIN_DELAY='2');self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(self.state.read_text())['v50_card'],2)

    def test_changed_card_invalidates_identity_but_never_sms_traffic_or_lock_policy(self):
        identity=self.tmp/'identity/lock-state.d/sim1/identity/sim.json'
        identity.parent.mkdir(parents=True)
        for shell in self.each_shell():
            identity.write_text('{"iccid":"old"}')
            sentinel=self.tmp/'identity/lock-state.d/sim1/nr';sentinel.write_text('78')
            self.state.write_text(json.dumps(self.initial));(self.hw/'chip_select').write_text('0\n')
            r=self.call(shell,'apply','2');self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse(identity.exists());self.assertEqual(sentinel.read_text(),'78')

    def test_inflight_collector_cannot_publish_after_first_switch_or_rollback(self):
        source=(TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/cell').read_text()
        start=source.index('CARD_EPOCH_PATH=')
        end=source.index('\nmkdir -p "$DIR"',start)
        guard=source[start:end]
        epoch=self.tmp/'epoch'
        for shell in self.each_shell():
            epoch.unlink(missing_ok=True)
            for initial in ('','old'):
                if initial:epoch.write_text(initial)
                r=self.sh(shell,guard+'\nprintf new > "$MU300_CARD_EPOCH_PATH"\ncard_current\necho stale-published\n',MU300_CARD_EPOCH_PATH=epoch)
                self.assertEqual(r.returncode,0,r.stderr);self.assertNotIn('stale-published',r.stdout)
            r=self.sh(shell,guard+'\ncard_current\necho current-published\n',MU300_CARD_EPOCH_PATH=epoch)
            self.assertIn('current-published',r.stdout)

    def test_boot_restore_stays_rf_off_for_original_mode_and_replay_sequence(self):
        for shell in self.each_shell():
            state=dict(self.initial,v50_card=2);self.state.write_text(json.dumps(state))
            (self.hw/'chip_select').write_text('0\n');(self.tmp/'commands').unlink(missing_ok=True)
            command='"$MU300_BIN/mobile-data" radio-locked "$MU300_BIN/mu300-v50-sim" boot'
            r=self.sh(shell,command,**self.extra);self.assertEqual(r.returncode,0,r.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertIn('AT+SPACTCARD=1;+SFUN=2',calls)
            self.assertNotIn('SFUN=4',calls,'RF started before the original boot replay')
            self.assertNotIn('ifdown',calls)
            (self.tmp/'commands').unlink()
            r=self.sh(shell,command,**self.extra);self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse((self.tmp/'commands').exists(),'unchanged route added boot AT/delay')

    def test_boot_retry_finishes_pending_power_on_without_repeating_mux_delay(self):
        for shell in self.each_shell():
            state=dict(self.initial,v50_card=2);self.state.write_text(json.dumps(state))
            (self.hw/'chip_select').write_text('0\n');(self.tmp/'failed-once').unlink(missing_ok=True)
            command='"$MU300_BIN/mobile-data" radio-locked "$MU300_BIN/mu300-v50-sim" boot'
            r=self.sh(shell,command,**dict(self.extra,FAIL_POWER_ON='1'));self.assertNotEqual(r.returncode,0)
            self.assertTrue((self.run/'mu300/v50-route-pending-1').exists())
            (self.tmp/'commands').unlink(missing_ok=True)
            r=self.sh(shell,command,**self.extra);self.assertEqual(r.returncode,0,r.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertIn('AT+SPACTCARD=1;+SFUN=2',calls)
            self.assertNotIn('SFUN=3',calls);self.assertNotIn('sleep 5',calls)
            self.assertFalse((self.run/'mu300/v50-route-pending-1').exists())

    def test_first_sim2_enable_replays_before_delegating_to_existing_rf_controller(self):
        for shell in self.each_shell():
            state=dict(self.initial,radio=[1,0]);self.state.write_text(json.dumps(state))
            (self.hw/'chip_select').write_text('0\n');(self.tmp/'commands').unlink(missing_ok=True)
            r=self.call(shell,'apply','2');self.assertEqual(r.returncode,0,r.stderr)
            calls=(self.tmp/'commands').read_text()
            self.assertIn('replay 1 replay early',calls)
            self.assertNotIn('AT+SPACTCARD=1;+SFUN=4',calls)
            self.assertLess(calls.index('replay 1 replay early'),calls.index('logical 1'))
