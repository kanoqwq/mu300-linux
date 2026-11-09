"""Actual ucode backend against private proc/sys/config files; no device writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
APP=Path(os.environ.get('UNISOC_TEST_APP',TOP/'openwrt/luci-app-mu300'))
BACKEND=APP/'root/usr/libexec/unisoc-modem/network-performance.uc'

@unittest.skipUnless(shutil.which('ucode'),'real ucode/fs required')
class NetworkPerformance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name,value in {'proc/sys/kernel/random/boot_id':'boot-one',
            'proc/sys/net/ipv4/tcp_available_congestion_control':'reno cubic bbr',
            'proc/sys/net/ipv4/tcp_congestion_control':'cubic',
            'proc/sys/net/core/default_qdisc':'pfifo_fast','etc/config/sqm':'user QoS\n'}.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value+'\n')
        (self.root/'run').mkdir()
        self.config=self.root/'etc/unisoc-network-performance.json'
    def call(self,action='get',request=None,ok=True):
        p=subprocess.run(['ucode',str(BACKEND),action],input=json.dumps(request) if request is not None else '',
            text=True,capture_output=True,env=dict(os.environ,UNISOC_NET_ROOT=str(self.root)),timeout=5)
        value=json.loads(p.stdout)
        self.assertEqual(value['ok'],ok,(p.stdout,p.stderr))
        self.assertEqual(p.returncode,0 if ok else 1,(p.stdout,p.stderr))
        return value
    def test_get_is_read_only_and_lists_actual_algorithms(self):
        self.assertEqual(self.call()['available'],['reno','cubic','bbr'])
        self.assertEqual(list((self.root/'run').iterdir()),[])
        self.assertFalse(self.config.exists())
    def test_apply_bbr_cubic_and_default_preserves_original_queue(self):
        self.call('boot')
        bbr=self.call('apply',{'algorithm':'bbr','ubus_rpc_session':'a'*32})
        self.assertEqual((bbr['current'],bbr['qdisc'],bbr['selection']),('bbr','fq','bbr'))
        self.assertEqual(json.loads(self.config.read_text()),{'version':1,'algorithm':'bbr'})
        cubic=self.call('apply',{'algorithm':'cubic'})
        self.assertEqual((cubic['current'],cubic['qdisc']),('cubic','pfifo_fast'))
        self.call('apply',{'algorithm':'bbr'})
        reset=self.call('apply',{'algorithm':'default'})
        self.assertEqual((reset['current'],reset['qdisc']),('cubic','pfifo_fast'))
        self.assertFalse(self.config.exists())
        self.assertEqual((self.root/'etc/config/sqm').read_text(),'user QoS\n\n')
    def test_replay_uses_new_boot_defaults_and_keeps_selection(self):
        self.call('apply',{'algorithm':'bbr'})
        (self.root/'proc/sys/kernel/random/boot_id').write_text('boot-two\n')
        (self.root/'proc/sys/net/ipv4/tcp_congestion_control').write_text('cubic\n')
        (self.root/'proc/sys/net/core/default_qdisc').write_text('fq_codel\n')
        self.assertEqual(self.call('boot')['current'],'bbr')
        self.assertEqual(self.call('apply',{'algorithm':'default'})['qdisc'],'fq_codel')
    def test_unsupported_algorithm_leaves_current_and_saved_state(self):
        (self.root/'proc/sys/net/ipv4/tcp_available_congestion_control').write_text('reno cubic\n')
        self.assertEqual(self.call('apply',{'algorithm':'bbr'},ok=False)['error'],'network_unsupported')
        self.assertEqual(self.call()['current'],'cubic')
        self.assertFalse(self.config.exists())
    def test_invalid_requests_do_not_change_network(self):
        for value in ({'algorithm':'bbr; reboot'},{'algorithm':'reno'},{'algorithm':'bbr','command':'x'},[],{'algorithm':False}):
            self.assertEqual(self.call('apply',value,ok=False)['error'],'network_invalid')
        self.assertEqual(self.call()['current'],'cubic')
        self.assertFalse(self.config.exists())
    def test_save_failure_rolls_back_both_kernel_values(self):
        self.config.with_name(self.config.name+'.new').mkdir()
        failed=self.call('apply',{'algorithm':'bbr'},ok=False)
        self.assertEqual((failed['error'],failed['rollback']),('network_save_failed',True))
        self.assertEqual((self.call()['current'],self.call()['qdisc']),('cubic','pfifo_fast'))
    def test_default_does_not_force_cubic_over_an_existing_system_policy(self):
        (self.root/'proc/sys/net/ipv4/tcp_congestion_control').write_text('reno\n')
        self.call('apply',{'algorithm':'bbr'})
        self.assertEqual(self.call('apply',{'algorithm':'default'})['current'],'reno')
    def test_acl_separates_read_and_write(self):
        acl=json.loads((APP/'root/usr/share/rpcd/acl.d/unisoc-network-performance.json').read_text())['unisoc-network-performance']
        self.assertEqual(acl['read']['ubus']['unisoc-network-performance'],['get','get_offload'])
        self.assertEqual(acl['write']['ubus']['unisoc-network-performance'],['apply','apply_offload'])

if __name__=='__main__':unittest.main()
