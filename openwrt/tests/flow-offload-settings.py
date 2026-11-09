"""Real target ucode/UCI with private firewall files and a harmless fw4 stub."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

TOP = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get('UNISOC_TEST_APP', TOP/'openwrt'/(
    'luci-app-t551m' if (TOP/'devices/e5').exists() else 'luci-app-mu300')))


@unittest.skipUnless(os.name == 'posix' and shutil.which('docker'), 'Linux target ucode container required')
class FlowSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='offload-settings-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.container = 'offload-settings-'+uuid.uuid4().hex[:8]
        cls.app = '/src/'+APP.relative_to(TOP).as_posix()
        subprocess.run(['docker','run','-d','--network','none','--platform','linux/arm64',
                        '--user',str(os.getuid())+':'+str(os.getgid()),
                        '--name',cls.container,'-v',str(TOP)+':/src:ro','-v',str(cls.root)+':/fixture',
                        os.environ.get('FLOW_TEST_IMAGE','mu300-openwrt-base:25.12.5'),
                        '/bin/sleep','1800'],check=True,stdout=subprocess.DEVNULL)
        cls.addClassCleanup(lambda: subprocess.run(['docker','rm','-f',cls.container],
                                                   check=True,stdout=subprocess.DEVNULL))

    def setUp(self):
        self.fixture = self.root/uuid.uuid4().hex
        self.fixture.mkdir()
        self.remote = '/fixture/'+self.fixture.name
        for folder in ['etc/config','etc/mu300','run','tmp/.uci']:
            (self.fixture/folder).mkdir(parents=True,exist_ok=True)
        self.config = self.fixture/'etc/config/firewall'
        self.original = "config defaults 'main'\n option flow_offloading '1'\n option flow_offloading_hw '0'\n option input 'ACCEPT'\n\nconfig zone 'lan'\n option name 'lan'\n"
        self.config.write_text(self.original)
        self.mark = self.fixture/'etc/mu300/ttl.offload-off'
        fw4 = self.fixture/'fw4'
        fw4.write_text('''#!/bin/sh
base=${0%/*}
echo reload >> "$base/reloads"
if [ -f "$base/reload.fail" ]; then rm "$base/reload.fail"; exit 1; fi
[ ! -f "$base/reload.always-fail" ]
''')
        fw4.chmod(0o755)

    def command(self, *args, payload=None, wrapper=False):
        prefix = ['docker','exec','-i','-e','UNISOC_NET_ROOT='+self.remote,
                  '-e','UNISOC_FLOW_FW4='+self.remote+'/fw4']
        if wrapper:
            prefix += ['-e','UNISOC_NET_LOCK='+self.remote+'/network.lock',
                       '-e','MU300_TTL_LOCK='+self.remote+'/ttl.lock',
                       '-e','UNISOC_FLOW_LIB='+self.app+'/root/usr/libexec/unisoc-modem/flow-offload.uc']
            command = ['/bin/sh',self.app+'/root/usr/libexec/unisoc-modem/network-performance']
        else:
            command = ['/usr/bin/ucode',self.app+'/root/usr/libexec/unisoc-modem/flow-offload.uc']
        return subprocess.run(prefix+[self.container]+command+list(args),
                              input=json.dumps(payload) if payload is not None else '',
                              text=True,capture_output=True,timeout=15)

    def call(self, payload=None, ok=True):
        result = self.command('get' if payload is None else 'apply',payload=payload)
        self.assertEqual(result.returncode,0 if ok else 1,(result.stdout,result.stderr))
        value = json.loads(result.stdout)
        self.assertEqual(value['ok'],ok,value)
        return value

    def test_get_is_read_only(self):
        self.assertEqual(self.call(),{'ok':True,'supported':True,'enabled':True})
        self.assertEqual(self.config.read_text(),self.original)
        self.assertFalse((self.fixture/'reloads').exists())

    def test_toggle_persists_only_software_option(self):
        self.call({'enabled':False,'ubus_rpc_session':'a'*32})
        self.assertFalse(self.call()['enabled'])
        self.assertIn("option flow_offloading_hw '0'",self.config.read_text())
        self.assertIn("option input 'ACCEPT'",self.config.read_text())
        self.assertIn("config zone 'lan'",self.config.read_text())
        self.call({'enabled':True})
        self.assertTrue(self.call()['enabled'])
        self.assertEqual((self.fixture/'reloads').read_text().splitlines(),['reload','reload'])

    def test_invalid_values_never_reload_or_save(self):
        for value in [{'enabled':'0'},{'enabled':0},{'enabled':None},{'enabled':True,'command':'reboot'},[],{},'1']:
            self.assertEqual(self.call(value,ok=False)['error'],'offload_invalid')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertFalse((self.fixture/'reloads').exists())

    def test_ttl_legacy_restore_does_not_override_user_choice(self):
        self.mark.write_text('flow_offloading\nflow_offloading_hw\n')
        self.call({'enabled':False})
        self.assertEqual(self.mark.read_text(),'flow_offloading_hw\n')
        self.assertFalse(self.call()['enabled'])

    def test_ttl_legacy_marker_is_retired_even_if_option_already_matches(self):
        self.mark.write_text('flow_offloading\n')
        self.call({'enabled':True})
        self.assertFalse(self.mark.exists())
        self.assertFalse((self.fixture/'reloads').exists())

    def test_reload_failure_restores_settings_and_ttl_ownership(self):
        self.mark.write_text('flow_offloading\nflow_offloading_hw\n')
        (self.fixture/'reload.fail').touch()
        result = self.call({'enabled':False},ok=False)
        self.assertEqual((result['error'],result['rollback']),('offload_apply_failed',True))
        self.assertTrue(self.call()['enabled'])
        self.assertEqual(self.mark.read_text(),'flow_offloading\nflow_offloading_hw\n')
        self.assertEqual(len((self.fixture/'reloads').read_text().splitlines()),2)

    def test_failed_rollback_is_reported(self):
        (self.fixture/'reload.always-fail').touch()
        result = self.call({'enabled':False},ok=False)
        self.assertFalse(result['rollback'])

    def test_missing_option_is_off_and_rollback_restores_absence(self):
        self.config.write_text(self.original.replace(" option flow_offloading '1'\n",''))
        self.assertFalse(self.call()['enabled'])
        (self.fixture/'reload.fail').touch()
        self.assertTrue(self.call({'enabled':True},ok=False)['rollback'])
        self.assertNotIn('option flow_offloading ',self.config.read_text())

    def test_pending_firewall_changes_are_not_committed(self):
        delta = self.fixture/'tmp/.uci/firewall'
        delta.write_text('firewall.main.input=DROP\n')
        self.assertEqual(self.call({'enabled':False},ok=False)['error'],'offload_pending_changes')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual(delta.read_text(),'firewall.main.input=DROP\n')
        self.assertFalse((self.fixture/'reloads').exists())

    def test_missing_firewall_or_fw4_is_unavailable(self):
        self.config.unlink()
        self.assertFalse(self.call()['supported'])
        self.assertEqual(self.call({'enabled':True},ok=False)['error'],'offload_unsupported')

    def test_wrapper_serializes_with_ttl_and_get_remains_read_only(self):
        holder = subprocess.Popen(['docker','exec',self.container,'/bin/sh','-c',
            'exec 8>"$1/ttl.lock"; flock -x 8; echo ready; sleep 2','fixture',self.remote],
            stdout=subprocess.PIPE,text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.stdout.close)
        self.assertEqual(holder.stdout.readline().strip(),'ready')
        result = self.command('offload_apply',payload={'enabled':False},wrapper=True)
        self.assertEqual(json.loads(result.stdout)['error'],'offload_busy')
        result = self.command('offload_get',wrapper=True)
        self.assertEqual(json.loads(result.stdout)['enabled'],True)
        holder.wait(timeout=5)
        result = self.command('offload_apply',payload={'enabled':False},wrapper=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(json.loads(result.stdout)['enabled'])

    def test_rpc_and_acl_separate_reads_from_writes(self):
        result = subprocess.run(['docker','exec',self.container,'/bin/sh',
                                 self.app+'/root/usr/libexec/rpcd/unisoc-network-performance','list'],
                                capture_output=True,text=True,check=True)
        methods = json.loads(result.stdout)
        self.assertEqual(methods['apply_offload'],{'enabled':'Boolean'})
        acl = json.loads((APP/'root/usr/share/rpcd/acl.d/unisoc-network-performance.json').read_text())['unisoc-network-performance']
        self.assertEqual(acl['read']['ubus']['unisoc-network-performance'],['get','get_offload'])
        self.assertEqual(acl['write']['ubus']['unisoc-network-performance'],['apply','apply_offload'])


if __name__=='__main__':
    unittest.main()
