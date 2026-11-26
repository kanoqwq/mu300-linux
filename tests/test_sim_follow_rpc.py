"""Dispatch the shipped sim_get RPC without AT, RF or data-card mutations."""
import json
from helpers import ShellTest, TOP


class SimFollowRpc(ShellTest):
    def test_actual_slot_is_separate_from_saved_preference(self):
        source=TOP/'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash'
        actual=self.tmp/'actual'
        script=self.tmp/'rpc'
        script.write_text(source.read_text().replace('/run/mu300-data-sim-actual',str(actual))
                          .replace('/opt/mu300/bin/mu300-v50-sim',str(self.stubs/'physical')))
        library=self.tmp/'pool';library.write_text('mu300_sms_pool() { echo /unused; }\n')
        self.stub('uci','case "$3" in *.sim_slots) echo "${SLOTS:-2}";; *.data_sim) echo "${SAVED:-0}";; esac')
        self.stub('physical','printf \'{"model":"v50","supported":true,"logical_cards":[0,1]}\\n\'')
        self.stub('mu300-at','echo unexpected-at >&2; exit 99')
        for shell in self.each_shell():
            for value in ('0','1','invalid',None):
                if value is None:actual.unlink(missing_ok=True)
                else:actual.write_text(value+'\n')
                result=self.script(shell,script,'call','sim_get',MU300_SMS_POOL_LIB=library)
                self.assertEqual(result.returncode,0,result.stderr)
                reply=json.loads(result.stdout)
                self.assertEqual(reply['actual_sim'],int(value) if value in ('0','1') else None)
                self.assertEqual(reply['saved_sim'],0)
                self.assertEqual(reply['physical']['logical_cards'],[0,1])
                self.assertNotIn('unexpected-at',result.stderr)
