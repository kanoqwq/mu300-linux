import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from helpers import TOP, ucode
from native_dashboard_fixture import Snapshot

class NativeSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.snap=Snapshot(self.root)

    def test_active_data_slot_is_not_the_view_or_saved_preference(self):
        self.snap.write('run/mu300-data-sim-actual','1')
        self.snap.responses['network.interface.wan.status']['up']=False
        self.assertEqual(self.snap.run(slot='0')['modem']['data_sim'],1)
        self.snap.responses['network.interface.wan.status'].update(up=True,l3_device='sipa_eth0')
        self.assertEqual(self.snap.run(slot='1')['modem']['data_sim'],0)
        self.snap.write('run/mu300/sim-active-unknown','1')
        self.assertIsNone(self.snap.run()['modem']['data_sim'])

    def test_equipment_queries_are_background_and_failed_reads_are_throttled(self):
        self.snap.write('proc/sys/kernel/random/boot_id','fixture-boot')
        self.snap.write('run/mu300-at/owner/pid','100')
        self.snap.write('proc/100/cmdline','mu300-modemd')
        fifo=self.root/'run/mu300-at/cmd'; os.mkfifo(fifo)
        self.snap.run(slot='1')
        job=json.loads((self.root/'spawn.json').read_text())
        self.assertEqual(job['exe'],'/bin/sh')
        self.assertEqual(job['args'],['/usr/libexec/unisoc-modem/dashboard-equipment'])
        self.assertEqual(job['env']['MU300_EQUIPMENT_SLOTS'],'2')
        (self.root/'spawn.json').unlink()
        self.snap.write('run/unisoc-modem-equipment/boot_id','fixture-boot')
        attempt=self.snap.write('run/unisoc-modem-equipment/attempt','')
        self.snap.run(slot='0')
        self.assertFalse((self.root/'spawn.json').exists(),'failed IMEI reads must not query on every page poll')
        os.utime(attempt,(1,1))
        self.snap.run(slot='0')
        self.assertTrue((self.root/'spawn.json').exists(),'failed reads should eventually retry')

    def test_dual_equipment_cache_and_boot_isolation(self):
        self.snap.write('proc/sys/kernel/random/boot_id','fixture-boot')
        self.snap.write('run/unisoc-modem-equipment/boot_id','fixture-boot')
        self.snap.write('run/unisoc-modem-equipment/imei0','866100000000408')
        self.snap.write('run/unisoc-modem-equipment/imei1','866100000000409')
        result=self.snap.run(slot='1')
        self.assertEqual(result['modem']['imeis'],['866100000000408','866100000000409'])
        self.snap.write('run/unisoc-modem-equipment/imei1','ERROR')
        self.assertEqual(self.snap.run(slot='1')['modem']['imeis'],['866100000000408',None])
        self.snap.write('proc/sys/kernel/random/boot_id','other-boot')
        self.assertEqual(self.snap.run()['modem']['imeis'],[None,None])

    def test_full_snapshot_and_public_apn(self):
        s=self.snap
        s.write('sys/class/thermal/thermal_zone0/type','soc-thmzone\n');s.write('sys/class/thermal/thermal_zone0/temp','42000\n')
        s.write('run/unisoc-modem-traffic/view.json','{"fixture":123}\n')
        s.write('proc/sys/net/netfilter/nf_conntrack_count','7\n')
        good=s.run()
        self.assertEqual(good['cpu'],dict(n=2,total=100,idle=85,freqs=[]))
        self.assertEqual(good['mem'],dict(total_kb=1024,avail_kb=512))
        self.assertEqual(good['temps']['soc'],42);self.assertEqual(good['conns'],7)
        self.assertEqual(good['wan']['apn'],'CMIOT5G');self.assertEqual(good['wan']['device'],'sipa_eth3')
        self.assertEqual(good['wan']['ip6'],'2001:db8::2')
        self.assertEqual(good['wan']['dns'],'1.1.1.1, 2001:4860:4860::8888')
        self.assertEqual(good['wifi']['ssid'],'FixtureWiFi');self.assertEqual(good['wifi']['width'],'80 MHz')
        self.assertEqual(good['lan']['ip'],'192.168.1.1');self.assertEqual(good['traffic'],{'fixture':123})
        (s.config/'network').write_text("config interface 'wan'\n option proto 'unsupported'\n option password 'DO-NOT-EXPOSE'\n")
        bad=s.run();self.assertIsNone(bad['wan']['apn']);self.assertNotIn('DO-NOT-EXPOSE',json.dumps(bad))
        for key in ['cpu','mem','power','wifi','lan','temps','boot','traffic','conns','net']:
            self.assertEqual(good[key],bad[key],key)

    def test_lease_countdown_uses_the_snapshot_timestamp(self):
        expiry=int(time.time())+3600
        self.snap.write('tmp/dhcp.leases',f'{expiry} aa:bb:cc:dd:ee:ff 192.168.1.2 client *\n')
        result=self.snap.run()
        self.assertEqual(result['lan']['list'][0]['left'],max(0,expiry-result['ts']))

    def test_status_recognizes_active_owner_arguments_after_nul(self):
        s=self.snap;s.write('tmp/unisoc-modem/collect.lck/pid','123\n')
        path=s.write('proc/123/cmdline','')
        path.write_bytes(b'ucode\0/opt/mu300/bin/mu300-modemd\0')
        result=s.run('status','0',True)
        self.assertEqual(result['refreshing'],1)
        self.assertFalse((self.root/'last-call.json').exists(),'a live collector must not be kicked again')

    def test_live_device_counters_and_invalid_path(self):
        s=self.snap
        before=s.run();s.write('sys/class/net/sipa_eth3/statistics/rx_bytes','29214\n')
        after=s.run();self.assertEqual(after['net']['mobile']['rx']-before['net']['mobile']['rx'],29113)
        self.assertEqual(after['net']['mobile'],after['net']['sipa_eth0'])
        s.responses['network.interface.wan.status']['l3_device']='../../etc/passwd'
        invalid=s.run();self.assertIsNone(invalid['net']['mobile']);self.assertIsNone(invalid['wan']['device'])

    def test_selected_sim_does_not_show_other_sim_apn(self):
        self.assertEqual(self.snap.run(slot='0')['wan']['apn'],'CMIOT5G')
        self.assertIsNone(self.snap.run(slot='1')['wan']['apn'])

    def test_status_refresh_and_explicit_failure(self):
        s=self.snap
        state=s.run('status','1',True);self.assertEqual(state['refreshing'],1)
        self.assertEqual(json.loads((self.root/'last-call.json').read_text())['a'],{'slot':'1','mode':'full'})
        s.responses['mu300.modem.collect']={'started':0,'error':'unavailable'}
        state=s.run('status','0',True);self.assertIn('error',state['cell']);self.assertIn('error',state['sig'])
        self.assertEqual(state['refreshing'],0)
        self.assertIn('error',s.run('status','../../bad',True))

    def test_sms_signature_is_scoped_without_reading_message_content(self):
        s=self.snap;s.write('etc/mu300/sms/next_id','2\n');s.write('etc/mu300/sms/msg/2','SECRET MESSAGE')
        s.write('etc/mu300/sms/sim1/next_id','1\n');s.write('etc/mu300/sms/sim1/msg/1','OTHER SECRET')
        for slot,count in [('0',2),('1',1)]:
            r=s.run('sms_head',slot);self.assertEqual(r['next_id'],count);self.assertEqual(r['count'],1)
            self.assertNotIn('SECRET',json.dumps(r))

    def test_custom_pool_and_explicit_external_adapter(self):
        s=self.snap;s.setup_config(" option sms_pool '/mnt/custom pool'\n")
        s.write('mnt/custom pool/next_id','5');s.write('mnt/custom pool/msg/5','PRIVATE')
        self.assertEqual(s.run('sms_head','0')['next_id'],5)
        s.setup_config(" option sms_command '/usr/libexec/my-platform/sms'\n")
        self.assertEqual(s.run('sms_head','0'),{'adapter':True})

    def test_live_device_tree_outweighs_stale_battery_board_label(self):
        s=self.snap;s.write('sys/class/power_supply/sc27xx-fgu/capacity','63')
        self.assertEqual(s.run('power')['present'],1)
        (self.root/'proc/device-tree').mkdir()
        self.assertEqual(s.run('power')['present'],0,'F50 DT must exclude a synthetic FGU despite a stale U30 label')
        (self.root/'proc/device-tree/charger_policy_service').mkdir()
        s.write('run/mu300/device','f50')
        self.assertEqual(s.run('power')['present'],1,'actual U30 DT must retain its real battery')

    def test_dynamic_boot_slot_and_readback_artifacts(self):
        s=self.snap;s.write('etc/mu300/default-boot','linux\n');s.write('run/mu300/linux-slot','a\n')
        helper=s.write('opt/mu300/bin/mu300-next-boot','#!/bin/sh\n');helper.chmod(0o755)
        self.assertFalse(s.run('boot')['can_set_default'])
        for name in ['linux-trial','android']:s.write('run/mu300/misc-bc-'+name+'.bin','record')
        state=s.run('boot');self.assertTrue(state['can_set_default']);self.assertFalse(state['temporary'])

    def test_no_shell_processes_or_optional_backend_switches(self):
        source=(TOP/'openwrt/luci-app-mu300/root/usr/share/ucode/mu300/dash.uc').read_text()
        for forbidden in ['popen(', 'system(', 'require(', 'AT+', 'c.set(', 'c.commit(']:self.assertNotIn(forbidden,source)
        acl=json.loads((TOP/'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertEqual(acl['read']['ubus']['mu300.modem'],['signal'])
        self.assertNotIn('mu300.modem',acl['write']['ubus'])

class ServiceCpuPlacement(unittest.TestCase):
    def test_cli_applies_saves_and_replays_explicit_choice(self):
        cmd=ucode()
        if not cmd:self.skipTest('ucode runtime required')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s=Snapshot(root)
            for path,value in {
                'sys/devices/system/cpu/cpu0/cpu_capacity':'512',
                'sys/devices/system/cpu/cpu1/cpu_capacity':'1024',
                'sys/fs/cgroup/cgroup.controllers':'cpuset cpu',
                'sys/fs/cgroup/cgroup.subtree_control':'cpuset',
                'sys/fs/cgroup/services/cpuset.cpus':''}.items():s.write(path,value+'\n')
            module=TOP/'rootfs/overlay/usr/share/ucode/mu300/service-cpus.uc'
            source=(TOP/'rootfs/overlay/opt/mu300/bin/mu300-service-cpus').read_text()
            source=source.replace("from 'mu300.service-cpus'",'from '+json.dumps(str(module)))
            source=source.replace('cursor()','cursor(getenv("MU300_DASH_CONFIG"))')
            source=source.replace("apply('',mode)",'apply('+json.dumps(td)+',mode)')
            script=root/'placement';script.write_text(source)
            def run(action):
                return subprocess.run(cmd+[str(script),action],env=dict(os.environ,MU300_DASH_CONFIG=str(s.config)),capture_output=True,text=True,timeout=5)
            self.assertEqual(run('get').stdout.strip(),'little')
            self.assertTrue(json.loads(run('all').stdout)['ok'])
            self.assertEqual(run('get').stdout.strip(),'all')
            s.write('sys/fs/cgroup/services/cpuset.cpus','1\n')
            self.assertTrue(json.loads(run('apply').stdout)['ok'])
            self.assertEqual((root/'sys/fs/cgroup/services/cpuset.cpus').read_text().strip(),'')
            self.assertTrue(json.loads(run('little').stdout)['ok'])
            self.assertEqual(run('get').stdout.strip(),'little')
            s.write('sys/fs/cgroup/cgroup.controllers','cpu\n')
            failed=run('all');self.assertNotEqual(failed.returncode,0)
            self.assertFalse(json.loads(failed.stdout)['ok'])
            self.assertEqual(run('get').stdout.strip(),'little','a failed operation must not save another mode')

    def test_little_all_offline_and_dependency_errors(self):
        cmd=ucode()
        if not cmd:self.skipTest('ucode runtime required')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def write(p,s):
                p=root/p;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
            for n,cap in [(0,512),(1,512),(2,1024)]:write(f'sys/devices/system/cpu/cpu{n}/cpu_capacity',str(cap))
            write('sys/devices/system/cpu/cpu1/online','0\n')
            write('sys/fs/cgroup/cgroup.controllers','cpuset cpu\n');write('sys/fs/cgroup/cgroup.subtree_control','cpuset\n')
            write('sys/fs/cgroup/services/cpuset.cpus','\n')
            module=TOP/'rootfs/overlay/usr/share/ucode/mu300/service-cpus.uc'
            script=root/'test.uc';script.write_text(f"import {{apply}} from {json.dumps(str(module))}; print(sprintf('%J',apply(ARGV[0],ARGV[1])));")
            def run(mode):
                p=subprocess.run(cmd+[str(script),td,mode],capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
            self.assertEqual(run('little'),dict(ok=True,mode='little',cpus='0'))
            self.assertEqual(run('all'),dict(ok=True,mode='all',cpus=''))
            write('sys/devices/system/cpu/cpu0/online','0\n');self.assertFalse(run('little')['ok'])
            write('sys/fs/cgroup/cgroup.controllers','cpu\n');self.assertFalse(run('all')['ok'])

class SmsWatcher(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_change_signature_retry_and_sim_race(self):
        p=subprocess.run(['node',str(TOP/'tests/dashboard_sms_watch.js')],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
