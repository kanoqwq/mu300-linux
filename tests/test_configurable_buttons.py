"""Button persistence and physical-event dispatch, without real device access."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TOP = Path(__file__).resolve().parents[1]
BACKEND = TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/buttons.py'


@unittest.skipUnless(os.name == 'posix', 'uses the production POSIX shell')
class Buttons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'etc/mu300').mkdir(parents=True)
        (self.root/'bin').mkdir()
        self.db = self.root/'uci.json'
        self.db.write_text('{}')
        script = """#!/usr/bin/python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['BUTTON_TEST_DB']);data=json.loads(p.read_text());args=sys.argv[1:]
if args[0]=='-q':args=args[1:]
op=args[0]
if op=='get':
 if args[1] not in data:sys.exit(1)
 print(data[args[1]])
elif op=='set':
 k,v=args[1].split('=',1);data[k]=v;p.write_text(json.dumps(data))
elif op=='commit':
 if os.environ.get('BUTTON_TEST_COMMIT_FAIL'):sys.exit(1)
elif op=='revert':
 data.pop(args[1],None);p.write_text(json.dumps(data))
else:sys.exit(2)
"""
        for name, text in (('uci', script), ('ubus', '#!/bin/sh\nexit 0\n'),
                           ('logger', '#!/bin/sh\nexit 0\n')):
            p=self.root/'bin'/name
            p.write_text(text);p.chmod(0o755)
        self.env=dict(os.environ, MU300_SYSROOT=str(self.root), BUTTON_TEST_DB=str(self.db),
                      PATH=str(self.root/'bin')+':'+os.environ['PATH'])
        self.set_model('u30air')

    def set_model(self, name):
        (self.root/'etc/mu300/model').write_text(name)
        p=self.root/'bin/mu300-device'
        p.write_text('#!/bin/sh\necho '+name+'\n');p.chmod(0o755)
        self.env['MU300_BIN']=str(self.root/'bin')

    def invoke(self, *args, incoming=None):
        return subprocess.run([sys.executable, str(BACKEND), *args],
                              input=json.dumps(incoming) if incoming is not None else None,
                              env=self.env, text=True, capture_output=True, timeout=5)

    def get(self):
        return json.loads(self.invoke('get').stdout)

    def save(self, **values):
        return json.loads(self.invoke('set', incoming=values).stdout)

    def test_models_defaults_and_power_capabilities(self):
        for product in ('u30air','u30pro','f50pro','v50'):
            self.set_model(product);r=self.get()
            self.assertTrue(r['supported'])
            self.assertEqual(r['power'], product in ('u30air','u30pro'))
            self.assertEqual(r['short'], {'u30pro':'page-next','f50pro':'none','v50':'none'}.get(product,'default'))
        self.set_model('f50')
        self.assertFalse(self.get()['supported'])

    def test_shell_text_roundtrip_save_does_not_execute_then_key_executes(self):
        marker=self.root/'ran'
        command="printf '%s' \"quoted $ ; & text\" > "+shlex.quote(str(marker))
        self.assertTrue(self.save(short='custom',short_command=command)['ok'])
        self.assertEqual(self.get()['short_command'],command)
        self.assertFalse(marker.exists())
        self.assertEqual(self.invoke('run','function','short').returncode,0)
        end=time.monotonic()+3
        while not marker.exists() and time.monotonic()<end:time.sleep(.02)
        self.assertEqual(marker.read_text(),'quoted $ ; & text')

    def test_power_long_rejected_even_with_forged_config(self):
        marker=self.root/'forbidden'
        self.assertFalse(self.save(power_long='custom')['ok'])
        self.save(power_short='custom',power_command='touch '+shlex.quote(str(marker)))
        self.assertEqual(self.invoke('run','power','long').returncode,2)
        self.assertFalse(marker.exists())
        self.assertEqual(self.invoke('worker','power','short').returncode,0)

    def test_double_actions_and_u30air_original_double_band(self):
        self.assertEqual(self.get()['double'],'wifi-band')
        marker=self.root/'double-ran'
        command='touch '+shlex.quote(str(marker))
        self.assertTrue(self.save(double='custom',double_command=command)['ok'])
        self.assertEqual(self.invoke('worker','function','double').returncode,0)
        self.assertTrue(marker.exists())
        self.assertTrue(self.save(power_double='custom',power_double_command='true')['ok'])
        self.assertEqual(self.invoke('worker','power','double').returncode,0)
        self.set_model('v50')
        self.assertEqual(self.get()['double'],'none')
        self.assertFalse(self.save(power_double='custom',power_double_command='true')['ok'])

    def test_single_line_validation_no_shell_blacklist(self):
        for text in ('', ' ', 'echo ok\necho bad', 'echo ok\r', 'echo\0bad', 'a'*4097):
            self.assertFalse(self.save(short='custom',short_command=text)['ok'],repr(text))
        self.assertTrue(self.save(short='custom',short_command="printf '%s' '\\n'; true")['ok'])

    def test_only_function_keys_on_v50_and_f50pro(self):
        for product in ('v50','f50pro'):
            self.set_model(product)
            self.assertFalse(self.save(power_short='custom',power_command='true')['ok'])
            self.assertEqual(self.invoke('run','power','short').returncode,2)

    def test_original_behavior_and_none_are_distinct(self):
        self.assertEqual(self.invoke('run','function','short').returncode,3)
        self.save(short='none')
        self.assertEqual(self.invoke('run','function','short').returncode,0)

    def test_old_f50pro_fields_and_unrelated_settings_survive(self):
        self.set_model('f50pro')
        self.db.write_text(json.dumps({'unisoc_modem.main.f50pro_button_short':'wifi-band',
                                      'unisoc_modem.main.keep':'yes'}))
        self.assertEqual(self.get()['short'],'wifi-band')
        self.save(short='custom',short_command='true')
        self.assertEqual(json.loads(self.db.read_text())['unisoc_modem.main.keep'],'yes')
        self.save(long='none')
        self.assertEqual(self.get()['short_command'],'true')

    def test_rpc_schema_only_exposes_settings_and_keeps_write_permission(self):
        rpc=TOP/'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash'
        result=subprocess.run(['/bin/sh',str(rpc),'list'],input='',env=dict(self.env,MU300_SMS_POOL_LIB=str(TOP/'rootfs/overlay/opt/mu300/lib/sms-pool.sh'),MU300_SMS_POOL=str(self.root/'sms')),
                              text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        schema=json.loads(result.stdout)
        self.assertIn('double',schema['button_set'])
        self.assertIn('power_double_command',schema['button_set'])
        self.assertNotIn('power_long',schema['button_set'])
        self.assertNotIn('button_run',schema)
        acl=json.loads((TOP/'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        rules=next(iter(acl.values()))
        self.assertIn('button_get',rules['read']['ubus']['mu300dash'])
        self.assertNotIn('button_set',rules['read']['ubus']['mu300dash'])
        self.assertIn('*',rules['write']['ubus']['mu300dash'])

    def test_builtin_actions_use_existing_transaction_owners(self):
        spec=importlib.util.spec_from_file_location('buttons',BACKEND)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.RUN=self.root
        (self.root/'mu300-data-sim-actual').write_text('0')
        with patch.object(module,'call') as call:
            call.return_value='{"logical_cards":[0,3]}'
            module.execute('u30pro','data-switch','')
            self.assertEqual(call.call_args.args[0][-3:],['apply','data','u30pro-card-3'])
            call.reset_mock();module.execute('v50','data-switch','')
            self.assertEqual(call.call_args.args[0][-3:],['apply','data','1'])
            call.reset_mock();module.execute('u30air','wifi-band','')
            self.assertEqual(call.call_args.args[0][-1],'toggle')


if __name__=='__main__':
    unittest.main()
