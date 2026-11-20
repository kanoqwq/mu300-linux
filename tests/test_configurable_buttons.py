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

    def backend(self):
        spec=importlib.util.spec_from_file_location('buttons',BACKEND)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.RUN=self.root
        return module

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
        self.assertTrue(self.save(long='custom',long_command=command)['ok'])
        self.assertEqual(self.get()['long_command'],command)
        self.assertFalse(marker.exists())
        self.assertEqual(self.invoke('run','function','long').returncode,0)
        end=time.monotonic()+3
        while not marker.exists() and time.monotonic()<end:time.sleep(.02)
        self.assertEqual(marker.read_text(),'quoted $ ; & text')

    def test_rpc_session_metadata_is_not_a_button_setting(self):
        for product in ('u30pro','f50pro','u30air','v50'):
            self.set_model(product)
            self.assertTrue(self.save(long='wifi-band',ubus_rpc_session='a'*32)['ok'])
            self.assertEqual(self.get()['long'],'wifi-band')
            self.assertFalse(any('ubus_rpc_session' in key for key in json.loads(self.db.read_text())))
            self.assertFalse(self.save(ubus_rpc_session='a'*32,power_long='custom')['ok'])
            self.assertFalse(self.save(ubus_rpc_session='a'*32,unknown='none')['ok'])

    def test_power_long_rejected_even_with_forged_config(self):
        marker=self.root/'forbidden'
        self.assertFalse(self.save(power_long='custom')['ok'])
        self.save(power_short='custom',power_command='touch '+shlex.quote(str(marker)))
        self.assertEqual(self.invoke('run','power','long').returncode,2)
        self.assertFalse(marker.exists())
        self.assertEqual(self.invoke('worker','power','short').returncode,3)

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
            self.assertFalse(self.save(long='custom',long_command=text)['ok'],repr(text))
        self.assertTrue(self.save(long='custom',long_command="printf '%s' '\\n'; true")['ok'])

    def test_only_function_keys_on_v50_and_f50pro(self):
        for product in ('v50','f50pro'):
            self.set_model(product)
            self.assertFalse(self.save(power_short='custom',power_command='true')['ok'])
            self.assertEqual(self.invoke('run','power','short').returncode,2)

    def test_original_behavior_and_none_are_distinct(self):
        self.assertEqual(self.invoke('run','function','short').returncode,3)
        self.assertFalse(self.save(short='none')['ok'])
        self.assertEqual(self.invoke('run','function','short').returncode,3)

    def test_old_f50pro_fields_and_unrelated_settings_survive(self):
        self.set_model('f50pro')
        self.db.write_text(json.dumps({'unisoc_modem.main.f50pro_button_short':'wifi-band',
                                      'unisoc_modem.main.keep':'yes'}))
        self.assertEqual(self.get()['short'],'none')
        self.save(long='custom',long_command='true')
        self.assertEqual(json.loads(self.db.read_text())['unisoc_modem.main.keep'],'yes')
        self.save(double='none')
        self.assertEqual(self.get()['long_command'],'true')

    def test_rpc_schema_only_exposes_settings_and_keeps_write_permission(self):
        rpc=TOP/'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash'
        result=subprocess.run(['/bin/sh',str(rpc),'list'],input='',env=dict(self.env,MU300_SMS_POOL_LIB=str(TOP/'rootfs/overlay/opt/mu300/lib/sms-pool.sh'),MU300_SMS_POOL=str(self.root/'sms')),
                              text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        schema=json.loads(result.stdout)
        self.assertIn('double',schema['button_set'])
        self.assertIn('power_double_command',schema['button_set'])
        self.assertNotIn('power_long',schema['button_set'])
        for field in ('short','short_command','power_short','power_command'):
            self.assertNotIn(field,schema['button_set'])
        self.assertNotIn('button_run',schema)
        acl=json.loads((TOP/'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        rules=next(iter(acl.values()))
        self.assertIn('button_get',rules['read']['ubus']['mu300dash'])
        self.assertNotIn('button_set',rules['read']['ubus']['mu300dash'])
        self.assertIn('*',rules['write']['ubus']['mu300dash'])

    def test_old_single_commands_are_ignored_and_cannot_be_saved(self):
        for product in ('u30air','u30pro','v50','f50pro'):
            self.set_model(product)
            module=self.backend(); mapping=module.keys(product)
            marker=self.root/'forbidden'
            self.db.write_text(json.dumps({mapping['short']:'custom', mapping['short_command']:'touch '+str(marker),
                                         mapping['power_short']:'custom',mapping['power_command']:'touch '+str(marker)}))
            self.assertEqual(self.invoke('worker','function','short').returncode,3)
            self.assertEqual(self.invoke('worker','power','short').returncode,3 if product in ('u30air','u30pro') else 2)
            self.assertFalse(marker.exists())
            self.assertFalse(self.save(short='custom',short_command='true')['ok'])
            self.assertTrue(self.save(long='none')['ok'])
            self.assertEqual(json.loads(self.db.read_text())[mapping['short']],'custom')

    def test_builtin_actions_use_existing_transaction_owners(self):
        module=self.backend()
        (self.root/'mu300-data-sim-actual').write_text('0')
        with patch.object(module,'call') as call:
            call.return_value=json.dumps(dict(ok=1,supported=True,ready=True,model='u30pro',
                                             cards=[0,1,3],active_card=0,logical_cards=[0,3]))
            module.execute('u30pro','data-switch','')
            self.assertEqual(call.call_args.args[0][-3:],['apply','data','u30pro-card-1'])
            call.reset_mock()
            call.return_value=json.dumps(dict(ok=1,supported=True,ready=True,model='v50',
                                             cards=[0,1,2],active_card=1,logical_cards=[0,1]))
            module.execute('v50','data-switch','')
            self.assertEqual(call.call_args.args[0][-3:],['apply','data','v50-card-2'])
            call.reset_mock();module.execute('u30air','wifi-band','')
            self.assertEqual(call.call_args.args[0][-1],'toggle')

    def test_physical_button_cycles_every_form_option_through_existing_apply(self):
        # Real board/form option sets. Expected order is the SIM page's order,
        # independent of which two sources happen to be routed right now.
        layouts={
            'u30pro':[[0,1,2,3],[0,1,2],[0,1],[0,1,3],[3],[0,2]],
            'f50pro':[[0,1,2],[0,1],[0,2],[0,2,4,3],[0,1,4,3],[4,3],[0,4,3]],
            'v50':[[0,1,2],[0,1,2,3],[0,1,3],[0,1]]}
        module=self.backend()
        for product,forms in layouts.items():
            for order in forms:
                with self.subTest(product=product,cards=order):
                    state=dict(ok=1,supported=True,ready=True,model=product,
                               cards=sorted(order),active_card=order[0],
                               saved_card=order[-1],logical_cards=[0,1])
                    applied=[]
                    def dispatch(argv, **kwargs):
                        if argv[-1]=='status':
                            owner='mu300-v50-sim' if product=='v50' else 'mu300-physical-sim'
                            self.assertEqual(Path(argv[0]).name,owner)
                            return json.dumps(state)
                        target=argv[-1]
                        self.assertEqual(argv[1:3],['apply','data'])
                        self.assertEqual(Path(argv[0]).name,'data-sim')
                        self.assertTrue(target.startswith(product+'-card-'))
                        state['active_card']=int(target.rsplit('-',1)[1])
                        applied.append(state['active_card'])
                        return ''
                    with patch.object(module,'call',side_effect=dispatch):
                        for _ in range(len(order)*2):module.execute(product,'data-switch','')
                    expected=(order[1:]+order[:1])*2 if len(order)>1 else []
                    self.assertEqual(applied,expected)

    def test_physical_switch_never_guesses_from_saved_or_unavailable_sources(self):
        module=self.backend()
        valid=dict(ok=1,supported=True,ready=True,model='v50',cards=[0,1,2,3],
                   active_card=3,saved_card=0,logical_cards=[3,2])
        with patch.object(module,'call',return_value=json.dumps(valid)) as call:
            module.execute('v50','data-switch','')
            self.assertEqual(call.call_args.args[0][-1],'v50-card-0')
        for change in ({'ready':False},{'ok':0},{'supported':False},{'model':'u30pro'},
                       {'active_card':None},{'active_card':False},{'active_card':4},
                       {'cards':[]},{'cards':[0,0]},{'cards':[False,1]},
                       {'cards':[0,4]},{'cards':'0,1'}):
            with self.subTest(change=change),patch.object(module,'call',return_value=json.dumps(dict(valid,**change))) as call:
                with self.assertRaises(ValueError):module.execute('v50','data-switch','')
                self.assertEqual(call.call_count,1)  # status only; no write

    def test_u30air_keeps_logical_dual_slot_switching(self):
        module=self.backend()
        for slot,target in [('0','1'),('1','0')]:
            (self.root/'mu300-data-sim-actual').write_text(slot)
            with patch.object(module,'call',return_value='') as call:
                module.execute('u30air','data-switch','')
                self.assertEqual(call.call_args.args[0][-3:],['apply','data',target])
                self.assertEqual(call.call_count,1)

    def test_wifi_toggle_is_available_for_every_supported_key(self):
        for product in ('u30air','v50','u30pro','f50pro'):
            self.set_model(product)
            values=dict(long='toggle-wifi',double='toggle-wifi')
            if product in ('u30air','u30pro'):
                values.update(power_double='toggle-wifi')
            self.assertTrue(self.save(**values)['ok'],product)
            for key,value in values.items():self.assertEqual(self.get()[key],value)

    def test_wifi_toggle_uses_selected_hotspot_owner_and_saved_enabled_state(self):
        module=self.backend()
        for product in ('u30air','v50','u30pro','f50pro'):
            for enabled,target in ((True,'off'),(False,'on')):
                replies=[json.dumps(dict(available=True,enabled=enabled,up=not enabled)),
                         json.dumps(dict(ok=1,pending=1))]
                with patch.object(module,'call',side_effect=replies) as call:
                    module.execute(product,'toggle-wifi','')
                    self.assertEqual([list(c.args[0]) for c in call.call_args_list],
                                     [[module.LIB/'hotspot','status'],[module.LIB/'hotspot',target]])

    def test_wifi_toggle_rejects_unknown_ap_and_propagates_apply_failure(self):
        module=self.backend()
        for state in ({'available':False},{'available':True},
                      {'available':True,'enabled':'false'}):
            with patch.object(module,'call',return_value=json.dumps(state)) as call:
                with self.assertRaises(ValueError):module.execute('u30air','toggle-wifi','')
                self.assertEqual(call.call_count,1)
        with patch.object(module,'call',side_effect=[json.dumps(dict(available=True,enabled=True)),
                        json.dumps(dict(ok=0,error='hotspot_busy'))]):
            with self.assertRaisesRegex(ValueError,'hotspot_busy'):
                module.execute('u30air','toggle-wifi','')

    def test_existing_pro_legacy_settings_survive_other_changes(self):
        self.set_model('u30pro')
        self.db.write_text(json.dumps({'u30pro_native_control.settings.home_long_action':'reconnect-cellular'}))
        self.assertTrue(self.save(double='toggle-wifi')['ok'])
        self.assertEqual(self.get()['long'],'reconnect-cellular')


if __name__=='__main__':
    unittest.main()
