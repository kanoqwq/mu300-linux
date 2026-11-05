"""Execute the actual fw4 transform and topology resolver under target ucode."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

TOP = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('flow_stage', TOP/'tools/stage-flow-offload.py')
STAGE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(STAGE)
IMAGE = os.environ.get('FLOW_TEST_IMAGE', 'mu300-openwrt-base:25.12.5')


@unittest.skipUnless(os.name == 'posix' and shutil.which('docker'), 'Linux target ucode container')
class FlowOffload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='flow-offload-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.container = 'flow-offload-'+uuid.uuid4().hex[:8]
        subprocess.run(['docker', 'run', '-d', '--network', 'none', '--name', cls.container,
                        '--platform', 'linux/arm64', '-v', str(cls.root)+':/fixture',
                        IMAGE, '/bin/sleep', '3600'], check=True, stdout=subprocess.DEVNULL)
        cls.addClassCleanup(lambda: subprocess.run(['docker', 'rm', '-f', cls.container],
                                                   check=True, stdout=subprocess.DEVNULL))
        cls.stock = (Path(os.environ['FLOW_FW4_SOURCE']).read_text() if os.environ.get('FLOW_FW4_SOURCE')
                     else subprocess.check_output(['docker', 'exec', cls.container, 'cat',
                                                   '/usr/share/ucode/fw4.uc'], text=True))
        cls.fixed = STAGE.transform(cls.stock)
        (cls.root/'fw4.uc').write_text(cls.fixed)
        subprocess.run(['docker', 'exec', cls.container, '/usr/bin/ucode', '-c',
                        '-o', '/dev/null', '/fixture/fw4.uc'], check=True)
        start = cls.fixed.index('function resolve_lower_devices(')
        end = cls.fixed.index('\nfunction ', start+1)
        cls.helper = cls.fixed[start:end]
        program = r'''import { readfile } from 'fs';
let input=json(readfile('/fixture/case.json')),opened=0,closed=0,queries=[];
let fs={opendir:function(path){
 let name=substr(path,length('/sys/class/net/'));
 if(!(name in input.present))return null;
 let entries=map(input.lower[name] || [],v=>'lower_'+v),i=0;
 return {read:()=>i<length(entries)?entries[i++]:null,close:()=>{}};
}};
let ubus={connect:function(){
 opened++;
 if(input.no_bus)return null;
 return {call:function(object,method){
   push(queries,[object,method]);
   if(input.throw_call)die('fixture topology unavailable');
   return input.status;
 },disconnect:()=>{closed++;}};
}};
HELPER
let owner={
 defaults:{flow_offloading:input.enabled},
 state:{networks:input.networks},
 default_option:function(key){return this.defaults[key];},
 resolve_hw_offload_devices:()=>input.hardware,
 zones:()=>input.zones,
 METHOD
};
let before=sprintf('%J',[owner.defaults,owner.state,input]);
let devices=owner.resolve_offload_devices();
if(before!==sprintf('%J',[owner.defaults,owner.state,input]))die('Configuration changed');
printf('%J',{devices,opened,closed,queries});
'''.replace('HELPER', cls.helper).replace('METHOD', STAGE.FIXED)
        (cls.root/'test.uc').write_text(program)

    def fixture(self, **changes):
        value = dict(enabled=True, hardware=None,
                     present=['br-lan', 'usb0', 'wlan0', 'sipa_eth0', 'sipa_eth8', 'docker0'],
                     lower={'br-lan': ['usb0', 'wlan0']},
                     status={'br-lan': {'devtype': 'bridge'}},
                     zones=[dict(related_physdevs=['br-lan', 'docker0'],
                                 network=[dict(device='wan', invert=False)])],
                     networks={'wan': dict(up=True, device='sipa_eth0')})
        value.update(changes)
        return value

    def run_ucode(self, case, ok=True):
        (self.root/'case.json').write_text(json.dumps(case))
        result = subprocess.run(['docker', 'exec', self.container, '/usr/bin/ucode',
                                 '/fixture/test.uc'], capture_output=True, text=True, timeout=10)
        if not ok:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual(reply['queries'], [['network.device', 'status']] if reply['opened'] else [])
        self.assertEqual(reply['closed'], reply['opened'])
        return reply['devices']

    def test_stock_legacy_current_migration_preserves_unrelated_code(self):
        self.assertIn(STAGE.STOCK, self.stock)
        for source in (self.stock, self.stock.replace(STAGE.STOCK, STAGE.LEGACY), self.fixed):
            with self.subTest(legacy=STAGE.LEGACY in source):
                result = STAGE.transform(source)
                self.assertEqual(result, self.fixed)
                self.assertEqual(STAGE.transform(result), result)
                self.assertEqual(result.replace(STAGE.FIXED, STAGE.STOCK), self.stock)

    def test_atomic_staging_preserves_mode_and_rejects_unknown_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            module = base/'usr/share/ucode/fw4.uc'
            module.parent.mkdir(parents=True)
            module.write_text(self.stock)
            module.chmod(0o640)
            self.assertTrue(STAGE.stage(base))
            self.assertFalse(STAGE.stage(base))
            self.assertEqual(module.read_text(), self.fixed)
            self.assertEqual(module.stat().st_mode & 0o777, 0o640)
            self.assertEqual([p.name for p in module.parent.iterdir()], ['fw4.uc'])
            module.write_text(self.stock)
            pending = module.with_name('fw4.uc.unisoc-new')
            pending.write_text('another owner')
            with self.assertRaises(ValueError):
                STAGE.stage(base)
            self.assertEqual(pending.read_text(), 'another owner')
            self.assertEqual(module.read_text(), self.stock)
            pending.unlink()
            pending.symlink_to('missing-unowned-target')
            with self.assertRaises(ValueError):
                STAGE.stage(base)
            self.assertTrue(pending.is_symlink())
            self.assertEqual(module.read_text(), self.stock)
            pending.unlink()
            module.write_text(self.stock.replace('let devices = this.resolve_hw_offload_devices();',
                                                 'let devices = changed_upstream();'))
            before = module.read_bytes()
            with self.assertRaises(ValueError):
                STAGE.stage(base)
            self.assertEqual(module.read_bytes(), before)

    def test_bridge_members_replace_bridge_master(self):
        self.assertEqual(self.run_ucode(self.fixture()), ['docker0', 'sipa_eth0', 'usb0', 'wlan0'])

    def test_dynamic_sim_interfaces_and_modemmanager_device_names(self):
        for device in ('sipa_eth8', 'wwan0', 'wwan1', 'rmnet_data0'):
            case = self.fixture(present=['br-lan', 'usb0', 'wlan0', device],
                                zones=[dict(related_physdevs=['br-lan'], network=[dict(device='wan')])],
                                networks={'wan': dict(up=True, device=device)})
            self.assertEqual(self.run_ucode(case), sorted([device, 'usb0', 'wlan0']))

    def test_vlan_bridge_and_duplicate_ports(self):
        case = self.fixture(present=['br-lan', 'br-lan.10', 'usb0', 'wlan0', 'sipa_eth0'],
                            lower={'br-lan.10': ['br-lan'], 'br-lan': ['usb0', 'wlan0']},
                            status={'br-lan.10': {'devtype': 'vlan'}, 'br-lan': {'devtype': 'bridge'}},
                            zones=[dict(related_physdevs=['br-lan.10', 'wlan0', 'missing'],
                                        network=[dict(device='wan'), dict(device='wan')])])
        self.assertEqual(self.run_ucode(case), ['sipa_eth0', 'usb0', 'wlan0'])

    def test_unrelated_down_absent_and_inverted_networks_excluded(self):
        case = self.fixture(zones=[dict(related_physdevs=['missing'],
                                        network=[dict(device='wan', invert=True),
                                                 dict(device='down'), dict(device='unknown')])],
                            networks={'wan': dict(up=True, device='sipa_eth0'),
                                      'down': dict(up=False, device='sipa_eth8'),
                                      'unrelated': dict(up=True, device='docker0')})
        self.assertEqual(self.run_ucode(case), [])

    def test_disabled_offload_and_hardware_choice_preserved(self):
        self.assertEqual(self.run_ucode(self.fixture(enabled=False, no_bus=True)), [])
        self.assertEqual(self.run_ucode(self.fixture(hardware=['eth0', 'eth1'], no_bus=True)),
                         ['eth0', 'eth1'])

    def test_dual_stack_networks_share_ports_without_duplicates(self):
        case = self.fixture(zones=[dict(related_physdevs=['br-lan'], network=[dict(device='wan'), dict(device='wan6')])],
                            networks={'wan': dict(up=True, device='sipa_eth0'),
                                      'wan6': dict(up=True, device='sipa_eth0')})
        self.assertEqual(self.run_ucode(case), ['sipa_eth0', 'usb0', 'wlan0'])

    def test_members_and_sim_changes_read_new_topology(self):
        self.assertEqual(self.run_ucode(self.fixture(lower={'br-lan': []})), ['docker0', 'sipa_eth0'])
        self.assertEqual(self.run_ucode(self.fixture()), ['docker0', 'sipa_eth0', 'usb0', 'wlan0'])
        self.assertEqual(self.run_ucode(self.fixture(networks={'wan': dict(up=True, device='sipa_eth8')})),
                         ['docker0', 'sipa_eth8', 'usb0', 'wlan0'])

    def test_topology_failure_reports_error_without_old_fallback(self):
        for change, message in ((dict(no_bus=True), 'Unable to read flowtable'),
                                (dict(status=None), 'Invalid flowtable'),
                                (dict(throw_call=True), 'Unable to read flowtable')):
            self.assertIn(message, self.run_ucode(self.fixture(**change), ok=False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
