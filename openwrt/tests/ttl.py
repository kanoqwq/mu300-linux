#!/usr/bin/env python3
"""Run both repositories' TTL engine against isolated firewall/config commands."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
APP = TOP / 'openwrt' / ('luci-app-t551m' if (TOP/'devices/e5').exists() else 'luci-app-mu300')
ENGINE = APP/'root/usr/libexec/unisoc-modem/ttl-engine'


class Ttl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root/'bin'; self.bin.mkdir()
        self.store = self.root/'firewall.json'
        self.store.write_text(json.dumps({'flow_offloading':'1','flow_offloading_hw':'1'}))
        self.conf = self.root/'etc/ttl.conf'
        self.net = self.root/'net'; self.net.mkdir()
        for name in ('sipa_eth0', 'sipa_eth15', 'wlan0', 'usb0', 'sipa_ethx', 'sipa_eth100'):
            (self.net/name).mkdir()
        self.env = dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'],
                        MU300_TTL_CONF=str(self.conf), MU300_TTL_LOCK=str(self.root/'lock'),
                        TEST_TTL_ROOT=str(self.root), MU300_TTL_SYS_NET=str(self.net))
        self.stub('uci', '''import sys,json,os
from pathlib import Path
r=Path(os.environ['TEST_TTL_ROOT']); p=r/'firewall.json'; d=json.loads(p.read_text())
a=sys.argv[1:]
if a[0]=='-q': a=a[1:]
if a[0]=='get': print(d.get(a[1].split('.')[-1],''))
elif a[0]=='commit':
 if (r/'commit.fail').exists(): sys.exit(1)
elif a[0]=='set':
 if (r/'uci.fail').exists(): sys.exit(1)
 k,v=a[1].split('=');d[k.split('.')[-1]]=v;p.write_text(json.dumps(d))
''')
        self.stub('fw4', '''import os,sys
from pathlib import Path
r=Path(os.environ['TEST_TTL_ROOT'])
with (r/'reloads').open('a') as f:f.write('reload\\n')
sys.exit(1 if (r/'reload.fail').exists() else 0)
''')
        self.stub('nft', """import os,sys,time
from pathlib import Path
r=Path(os.environ['TEST_TTL_ROOT']);a=sys.argv[1:];p=r/'rules';old=r/'legacy'
if a[0]=='list':
 p=old if a[2]=='inet' else p
 if not p.exists():sys.exit(1)
 print(p.read_text());sys.exit(0)
if a[0]=='-f':
 data=sys.stdin.read()
 if (r/'nft.fail').exists():sys.exit(1)
 if (r/'delay').exists():time.sleep(.35)
 if 'table netdev mu300_ttl {' in data:p.write_text(data)
 else:p.unlink(missing_ok=True)
 old.unlink(missing_ok=True)
""")

    def stub(self, name, code):
        p=self.bin/name;p.write_text('#!/usr/bin/env python3\n'+code);p.chmod(0o755)

    def call(self, *args, ok=True):
        r=subprocess.run(['sh',str(ENGINE),*args],env=self.env,text=True,capture_output=True,timeout=10)
        if ok:self.assertEqual(r.returncode,0,r.stderr)
        else:self.assertNotEqual(r.returncode,0)
        return r

    @property
    def mark(self): return self.conf.parent/'ttl.offload-off'

    def test_software_kept_hardware_paused_and_restored(self):
        self.call('set','65')
        rules=(self.root/'rules').read_text()
        self.assertIn('ip ttl set 65',rules);self.assertIn('ip6 hoplimit set 65',rules)
        self.assertEqual(rules.count('type filter hook egress device'),2)
        for name in ('sipa_eth0','sipa_eth15'): self.assertIn('device "'+name+'"',rules)
        for name in ('wlan0','usb0','sipa_ethx','sipa_eth100'): self.assertNotIn('device "'+name+'"',rules)
        self.assertNotIn('flush ruleset',rules)
        self.assertNotIn('hook postrouting',rules)
        self.assertIn('224.0.0.0/4',rules);self.assertIn('fe80::/10, ff00::/8',rules)
        self.assertIn('130, 131, 132, 133, 134, 135, 136, 137, 143',rules)
        self.assertEqual(json.loads(self.store.read_text()),{'flow_offloading':'1','flow_offloading_hw':'0'})
        self.assertEqual(self.mark.read_text(),'flow_offloading_hw\n')
        self.call('off')
        self.assertEqual(json.loads(self.store.read_text()),{'flow_offloading':'1','flow_offloading_hw':'1'})
        self.assertFalse(self.conf.exists());self.assertFalse((self.root/'rules').exists());self.assertFalse(self.mark.exists())

    def test_legacy_disabled_software_migrates_after_new_rules(self):
        self.conf.parent.mkdir();self.conf.write_text('TTL=64\n')
        self.mark.write_text('flow_offloading\nflow_offloading_hw\n')
        self.store.write_text(json.dumps({'flow_offloading':'0','flow_offloading_hw':'0'}))
        (self.root/'legacy').write_text('old postrouting rule')
        (self.root/'nft.fail').touch();self.call('apply',ok=False)
        self.assertEqual(json.loads(self.store.read_text())['flow_offloading'],'0')
        self.assertTrue((self.root/'legacy').exists())
        (self.root/'nft.fail').unlink();self.call('apply')
        self.assertFalse((self.root/'legacy').exists())
        self.assertEqual(json.loads(self.store.read_text()),{'flow_offloading':'1','flow_offloading_hw':'0'})
        self.assertEqual(self.mark.read_text(),'flow_offloading_hw\n')
        self.call('off')
        self.assertEqual(json.loads(self.store.read_text())['flow_offloading_hw'],'1')

    def test_legacy_software_only_and_disabled_hardware(self):
        self.conf.parent.mkdir();self.mark.write_text('flow_offloading\n')
        self.store.write_text(json.dumps({'flow_offloading':'0','flow_offloading_hw':'0'}))
        self.call('set','128')
        self.assertFalse(self.mark.exists())
        self.assertEqual(json.loads(self.store.read_text()),{'flow_offloading':'1','flow_offloading_hw':'0'})
        self.call('off')
        self.assertEqual(json.loads(self.store.read_text())['flow_offloading_hw'],'0')

    def test_no_cellular_then_add_remove_recreate(self):
        for p in self.net.glob('sipa_eth[0-9]*'): p.rmdir()
        self.call('set','64')
        self.assertNotIn('hook egress',(self.root/'rules').read_text())
        self.assertIn('IFACES=\n',self.call('state').stdout)
        (self.net/'sipa_eth1').mkdir();self.call('apply')
        self.assertIn('IFACES=sipa_eth1\n',self.call('state').stdout)
        (self.net/'sipa_eth1').rmdir();self.call('apply')
        self.assertNotIn('hook egress',(self.root/'rules').read_text())
        (self.net/'sipa_eth1').mkdir();self.call('apply')
        self.assertIn('device "sipa_eth1"',(self.root/'rules').read_text())
        self.assertEqual(self.conf.read_text(),'TTL=64\n')

    def test_state_describes_current_hooks_and_hardware_ownership(self):
        self.call('set','128');s=self.call('state').stdout
        for line in ('MODE=egress','OFFLOAD=1','OFFLOAD_HW=0','HW_PAUSED=1','IFACES=sipa_eth0 sipa_eth15'):
            self.assertIn(line+'\n',s)
        (self.root/'rules').unlink();(self.root/'legacy').touch()
        self.assertIn('MODE=legacy\n',self.call('state').stdout)

    def test_invalid_saved_ownership_never_writes_arbitrary_options(self):
        self.conf.parent.mkdir();self.mark.write_text('evil.option\n')
        original=self.store.read_text();self.call('set','64',ok=False);self.call('off',ok=False)
        self.assertEqual(self.store.read_text(),original)
        self.assertFalse((self.root/'rules').exists());self.assertFalse((self.root/'reloads').exists())

    def test_commit_failure_retains_retry_and_ownership(self):
        (self.root/'commit.fail').touch();self.call('set','64',ok=False)
        self.assertTrue((self.conf.parent/'ttl.offload-pending').exists())
        self.assertTrue(self.mark.exists());self.assertFalse(self.conf.exists())
        (self.root/'commit.fail').unlink();self.call('set','64')
        self.assertFalse((self.conf.parent/'ttl.offload-pending').exists())
        self.call('off');self.assertEqual(json.loads(self.store.read_text())['flow_offloading_hw'],'1')

    def test_failed_disable_keeps_saved_setting_for_retry(self):
        self.call('set','64');(self.root/'nft.fail').touch();self.call('off',ok=False)
        self.assertEqual(self.conf.read_text(),'TTL=64\n')
        self.assertTrue((self.root/'rules').exists());self.assertTrue(self.mark.exists())
        (self.root/'nft.fail').unlink();self.call('off');self.assertFalse(self.conf.exists())

    def test_invalid_values_do_not_change_settings(self):
        for v in ('','0','01','256','-1','1; reboot','1\n2','1.5','999999999999'):
            self.call('set',v,ok=False)
        self.assertFalse(self.conf.exists());self.assertFalse((self.root/'reloads').exists())

    def test_preserves_user_disabled_offload(self):
        self.store.write_text(json.dumps({'flow_offloading':'0','flow_offloading_hw':'0'}))
        self.call('set','64');self.call('apply');self.call('off')
        self.assertEqual(json.loads(self.store.read_text()),{'flow_offloading':'0','flow_offloading_hw':'0'})
        self.assertFalse((self.root/'reloads').exists())

    def test_failed_atomic_rule_keeps_previous_value_and_can_retry(self):
        self.call('set','64');before=(self.root/'rules').read_text()
        (self.root/'nft.fail').touch();self.call('set','128',ok=False)
        self.assertEqual(self.conf.read_text(),'TTL=64\n');self.assertEqual((self.root/'rules').read_text(),before)
        (self.root/'nft.fail').unlink();self.call('set','128')
        self.assertEqual(self.conf.read_text(),'TTL=128\n')

    def test_reload_failure_is_reported_and_retried_even_after_uci_changed(self):
        (self.root/'reload.fail').touch();self.call('set','64',ok=False)
        self.assertFalse(self.conf.exists());self.assertFalse((self.root/'rules').exists())
        (self.root/'reload.fail').unlink();self.call('set','64')
        self.assertEqual((self.root/'reloads').read_text().count('reload'),2)
        self.call('off')
        self.assertEqual(json.loads(self.store.read_text())['flow_offloading'],'1')

    def test_uci_failure_never_claims_success(self):
        (self.root/'uci.fail').touch();self.call('set','64',ok=False)
        self.assertFalse(self.conf.exists());self.assertFalse((self.root/'rules').exists())

    def test_process_restart_reapplies_saved_rule(self):
        self.call('set','128');(self.root/'rules').unlink()
        self.call('apply');self.assertIn('ttl set 128',(self.root/'rules').read_text())
        self.assertIn('TTL=128\nBACKEND=nft',self.call('state').stdout)

    def test_default_off_does_not_touch_firewall(self):
        self.call('apply');self.call('state')
        self.assertFalse((self.root/'rules').exists());self.assertFalse((self.root/'reloads').exists())

    def test_parallel_requests_are_serialized(self):
        (self.root/'delay').touch()
        procs=[subprocess.Popen(['sh',str(ENGINE),'set',v],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for v in ('64','128')]
        codes=[]
        for p in procs:
            out,err=p.communicate(timeout=10);codes.append(p.returncode)
        self.assertEqual(sorted(codes),[0,75])
        v=self.conf.read_text().strip().split('=')[1]
        self.assertIn('ttl set '+v,(self.root/'rules').read_text())
        self.call('off');self.assertEqual(json.loads(self.store.read_text())['flow_offloading'],'1')

    def test_json_adapter_exposes_hooks_software_and_hardware_separately(self):
        self.stub('ucode', 'import sys,json; print(json.dumps(sys.stdin.read()))')
        self.call('set','65')
        env=dict(self.env,MU300_TTL_CMD=str(ENGINE))
        r=subprocess.run(['sh',str(ENGINE.parent/'ttl'),'get'],env=env,text=True,capture_output=True,check=True)
        state=json.loads(r.stdout)
        self.assertEqual(state,{'ok':1,'enabled':1,'value':65,'backend':'nft','mode':'egress',
                               'offload':1,'offload_hw':0,'hw_paused':1,'iface':['sipa_eth0','sipa_eth15']})
        for bad in ('01','000','999999999999999999999999','256'):
            self.conf.write_text('TTL='+bad+'\n')
            r=subprocess.run(['sh',str(ENGINE.parent/'ttl'),'get'],env=env,text=True,capture_output=True,check=True)
            state=json.loads(r.stdout);self.assertEqual(state['enabled'],0);self.assertIsNone(state['value'])

    def test_hotplug_only_handles_cellular_devices(self):
        hook=APP/'root/etc/hotplug.d/net/92-unisoc-ttl'
        script=hook.read_text().replace('/usr/libexec/unisoc-modem/ttl-engine',str(ENGINE))
        self.call('set','65');(self.root/'rules').unlink()
        subprocess.run(['sh','-c',script],env=dict(self.env,ACTION='add',INTERFACE='wlan0'),check=True)
        self.assertFalse((self.root/'rules').exists())
        for action in ('add','remove','move'):
            subprocess.run(['sh','-c',script],env=dict(self.env,ACTION=action,INTERFACE='sipa_eth15'),check=True)
            self.assertTrue((self.root/'rules').exists())
            (self.root/'rules').unlink()

    def test_hotplug_apply_waits_for_ui_lock_then_reenumerates(self):
        (self.root/'delay').touch()
        p=subprocess.Popen(['sh',str(ENGINE),'set','65'],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        import time
        for _ in range(100):
            if self.conf.with_suffix('.conf.tmp').exists():break
            time.sleep(.01)
        (self.net/'sipa_eth2').mkdir()
        self.call('apply')
        _,err=p.communicate(timeout=10);self.assertEqual(p.returncode,0,err)
        self.assertIn('device "sipa_eth2"',(self.root/'rules').read_text())


if __name__=='__main__':unittest.main()
