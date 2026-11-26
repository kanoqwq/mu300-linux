"""Actual V50 physical/data/RF controllers; only AT, GPIO and netifd are fixtures."""
import json
import unittest
from helpers import TOP, BIN
from test_v50_sim import V50Cards


class V50SingleRf(unittest.TestCase):
    def setUp(self):
        self.f=V50Cards();self.f.setUp();f=self.f
        f.args.write_bytes(b'androidboot.zte_boardid=3\0')
        env=f.tmp/'real-sim-env'
        env.write_text('''mu300_sim_env() {
    MU300_SIM_SLOT=${1:-$(uci -q get unisoc_modem.main.data_sim)}
    MU300_SIM_DEVICE=card$MU300_SIM_SLOT
    MU300_SIM_SHARED=$MU300_V50_RUN/mu300-at
    [ "$MU300_SIM_SLOT" != 1 ] || MU300_SIM_SHARED=$MU300_V50_RUN/mu300-at4
    export MU300_SIM_SLOT MU300_SIM_DEVICE MU300_SIM_SHARED
}
''')
        radio=f.tmp/'real-radio'
        radio.write_text((BIN/'mu300-sim-radio').read_text().replace('/opt/mu300/bin/',str(f.stubs)+'/'))
        f.stub('mu300-sim-env',env.read_text())
        f.stub('mu300-radio-allowed',f'exec /bin/sh "{BIN / "mu300-radio-allowed"}" "$@"')
        f.stub('mu300-sim-radio',f'exec /bin/sh "{radio}" "$@"')
        data=f.tmp/'real-data-sim'
        shims='\n'.join(n+'() { "$STUBLOG/stubs/'+n+'" "$@"; }' for n in ('ifup','ifdown','sleep'))
        data.write_text((BIN/'mu300-data-sim').read_text().replace('#!/bin/sh\n','#!/bin/sh\n'+shims+'\n',1))
        f.stub('mu300-data-sim',f'exec /bin/sh "{data}" "$@"')
        f.stub('mobile-data','''case "$1" in
 radio-locked)
  shift; ln -s $$ "$MU300_V50_RUN/mu300-radio-on.owner" || exit
  "$@"; rc=$?; rm -f "$MU300_V50_RUN/mu300-radio-on.owner"; exit "$rc";;
 radio-policy) shift; exec /bin/sh "$REAL_RADIO" --locked "$@";;
 *) echo unexpected-radio-initializer >&2; exit 1;;
esac''')
        f.extra.update(MU300_SIM_ENV_LIB=env,MU300_DUAL_READY=f.run/'mu300-dual-radio-ready',
                       MU300_SIM_MUTEX=f.run/'unisoc-data-sim-switch',
                       MU300_DATA_SIM_ACTUAL=f.run/'mu300-data-sim-actual',REAL_RADIO=radio,
                       MU300_RADIO_CONTROL=f.stubs/'mu300-sim-radio',
                       MU300_RIL_HANDSHAKE=f.run/'mu300-ril-handshake',PIN_REQUIRES_POWER='1',REAL_NETIFD='1')
        self.policy=f.tmp/'sim-radio-policy'

    def tearDown(self):self.f.tearDown()

    def reset(self,flags='1 0',chip=0,up=True):
        f=self.f
        for name in ('v50-switch-incomplete','v50-route-pending-0','v50-route-pending-1'):
            (f.run/'mu300'/name).unlink(missing_ok=True)
        state=dict(f.initial,radio=list(map(int,flags.split())),cfun=list(map(int,flags.split())),
                   sim_power=list(map(int,flags.split())),actual=0,data_sim=0,sim_slots=2,
                   data_interface='wan',up=up,autostart=up)
        f.state.write_text(json.dumps(state));self.policy.write_text(flags+'\n')
        (f.run/'mu300-data-sim-actual').write_text('0\n')
        (f.hw/'chip_select').write_text(str(chip)+'\n');(f.hw/'psim_select').write_text('0\n')
        (f.tmp/'commands').unlink(missing_ok=True)

    def test_single_rf_roundtrip_and_no_physical_controls_stay_grey(self):
        f=self.f
        for shell in f.each_shell():
            self.reset()
            for target,flags in ((1,'0 1'),(0,'1 0')):
                result=f.call(shell,'apply',str(target))
                self.assertEqual(result.returncode,0,result.stderr+'\n'+(f.tmp/'commands').read_text()+'\n'+f.state.read_text())
                state=json.loads(f.state.read_text())
                self.assertEqual(self.policy.read_text().strip(),flags)
                self.assertEqual(state['cfun'],list(map(int,flags.split())))
                self.assertEqual(state['data_sim'],target);self.assertEqual(state['actual'],target)
                self.assertTrue(state['up']);self.assertTrue(state.get('available',True))
                self.assertTrue(json.loads(f.call(shell,'status').stdout)['ready'])
            calls=(f.tmp/'commands').read_text()
            self.assertLess(calls.index('AT+SPACTCARD=0;+SFUN=5'),calls.index('AT+SPACTCARD=1;+SFUN=4'))

    def test_board_with_two_cards_does_not_restore_an_unfitted_third_card(self):
        f=self.f
        for shell in f.each_shell():
            self.reset(chip=1)
            before=json.loads(f.call(shell,'status').stdout)
            self.assertEqual(before['logical_cards'],[0,1])
            result=f.call(shell,'apply','1');self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((f.hw/'chip_select').read_text().strip(),'0')
            self.assertEqual(json.loads(f.state.read_text())['data_sim'],1)
            self.assertEqual(self.policy.read_text(),'0 1\n')
            self.assertFalse((f.run/'mu300/v50-switch-incomplete').exists())

    def test_rf_enable_failure_restores_old_data_and_single_rf(self):
        f=self.f
        for shell in f.each_shell():
            self.reset(chip=1)
            result=f.call(shell,'apply','1',FAIL_RF_SLOT='1')
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('sim_rollback_failed',result.stderr)
            state=json.loads(f.state.read_text())
            self.assertEqual(state['data_sim'],0);self.assertEqual(state['actual'],0)
            self.assertEqual(self.policy.read_text(),'1 0\n');self.assertEqual(state['cfun'],[1,0])
            self.assertTrue(state['up'])
            self.assertTrue(json.loads(f.call(shell,'status').stdout)['ready'])

    def test_missing_target_and_manual_wan_off_preserve_user_choices(self):
        f=self.f
        for shell in f.each_shell():
            self.reset()
            result=f.call(shell,'apply','1',ABSENT_SLOT='1')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(self.policy.read_text(),'1 0\n')
            self.assertTrue(json.loads(f.state.read_text())['up'])
            self.assertTrue(json.loads(f.call(shell,'status').stdout)['ready'])
            self.reset(up=False)
            result=f.call(shell,'apply','1');self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(json.loads(f.state.read_text())['up'])
            self.assertEqual(self.policy.read_text(),'0 1\n')

    def test_existing_two_rf_mode_is_not_converted_to_single_rf(self):
        f=self.f
        for shell in f.each_shell():
            self.reset(flags='1 1')
            result=f.call(shell,'apply','1');self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.policy.read_text(),'1 1\n')
            self.assertEqual(json.loads(f.state.read_text())['cfun'],[1,1])

    def test_competing_switch_is_rejected_without_touching_owner_or_rf(self):
        f=self.f
        for shell in f.each_shell():
            self.reset();gate=f.run/'unisoc-data-sim-switch';gate.mkdir();(gate/'pid').write_text('existing')
            before=f.state.read_bytes();policy=self.policy.read_bytes()
            try:
                result=f.call(shell,'apply','1')
                self.assertNotEqual(result.returncode,0);self.assertIn('modem_busy',result.stderr)
                self.assertEqual((gate/'pid').read_text(),'existing')
                self.assertEqual(f.state.read_bytes(),before);self.assertEqual(self.policy.read_bytes(),policy)
                self.assertFalse((f.tmp/'commands').exists())
            finally:(gate/'pid').unlink();gate.rmdir()


def load_tests(loader,tests,pattern):return loader.loadTestsFromTestCase(V50SingleRf)

if __name__=='__main__':unittest.main(verbosity=2)
