"""Product form selection using real mapping libraries and an isolated UCI store."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOP = Path(__file__).resolve().parents[1]
SOURCE = TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/product-form'


class ProductForm(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='product-form-', dir='/dev/shm')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root/'run'; self.run.mkdir()
        self.bin = self.root/'opt/mu300/bin'; self.bin.mkdir(parents=True)
        (self.root/'etc/mu300').mkdir(parents=True)
        (self.root/'proc').mkdir()
        self.state = self.root/'uci.json'; self.state.write_text('{}')
        self.commands = self.root/'commands'
        executable = self.root/'uci'
        executable.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
store=Path(os.environ['PRODUCT_FORM_TEST_STORE'])
state=json.loads(store.read_text())
args=sys.argv[1:]
if args and args[0]=='-q':args=args[1:]
if args[0]=='get':
 value=state.get(args[1].split('.')[-1])
 if value is None:sys.exit(1)
 print(value)
elif args[0]=='changes':
 if os.environ.get('PRODUCT_FORM_TEST_DIRTY'):print('unisoc_modem.main.other=1')
elif args[0]=='batch':
 if os.environ.get('PRODUCT_FORM_TEST_FAIL_COMMIT'):sys.exit(1)
 text=sys.stdin.read()
 with open(os.environ['PRODUCT_FORM_TEST_COMMANDS'],'a') as out:out.write(text)
 for line in text.splitlines():
  if line.startswith('set '):
   key,value=line[4:].split('=',1);state[key.split('.')[-1]]=value
  elif line.startswith('delete '):state.pop(line.split('.')[-1],None)
 store.write_text(json.dumps(state))
''')
        executable.chmod(0o755)
        pidof = self.root/'pidof'
        pidof.write_text('#!/bin/sh\n[ "${PRODUCT_FORM_TEST_LIVE:-0}" = 1 ]\n')
        pidof.chmod(0o755)
        libs = {
            'MU300_U30PRO_CARD_LIB': TOP/'devices/u30pro/overlay/opt/mu300/lib/u30pro-card.sh',
            'MU300_F50PRO_CARD_LIB': TOP/'devices/f50pro/overlay/opt/mu300/lib/f50pro-card.sh',
            'MU300_V50_CARD_LIB': TOP/'rootfs/overlay/opt/mu300/lib/v50-card.sh'}
        env = dict(PATH=str(self.root)+':'+os.environ['PATH'], MU300_SYSROOT=str(self.root),
                   MU300_U30PRO_RUN=str(self.run), MU300_V50_RUN=str(self.run),
                   PRODUCT_FORM_TEST_STORE=str(self.state), PRODUCT_FORM_TEST_COMMANDS=str(self.commands))
        env.update({k: str(v) for k,v in libs.items() if v.exists()})
        self.environment = patch.dict(os.environ, env)
        self.environment.start(); self.addCleanup(self.environment.stop)
        loader = importlib.machinery.SourceFileLoader('product_form', str(SOURCE))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec); loader.exec_module(self.mod)
        self.product('v50', 1)

    def product(self, name, board):
        device='u30pro' if name in ('u30pro','f50pro') else name
        tool=self.bin/'mu300-device';tool.write_text('#!/bin/sh\necho '+device+'\n');tool.chmod(0o755)
        (self.root/'etc/mu300/model').write_text(name+'\n')
        (self.root/'proc/cmdline').write_text('zteboot.zte_boardid='+str(board)+'\n' if name!='v50' else 'androidboot.zte_boardid='+str(board)+'\n')

    def config(self, **items):
        self.state.write_text(json.dumps(items))

    def values(self):
        return json.loads(self.state.read_text())

    def get(self):
        return self.mod.execute('get', {})

    def test_v50_catalog_has_its_own_numbering(self):
        r=self.get()
        self.assertEqual(r['model'],'v50')
        self.assertEqual([o['cards'] for o in r['options']],[[0,1,2],[0,1,2,3],[0,1,3],[0,1]])
        self.assertEqual(r['effective_board_id'],1)
        self.assertFalse(self.commands.exists())

    @unittest.skipUnless((TOP/'devices/f50pro').is_dir(), 'Pro model libraries are in the Pro repository')
    def test_pro_catalogs_do_not_share_board_number_meanings(self):
        self.product('u30pro',4);u=self.get()
        self.product('f50pro',4);f=self.get()
        self.assertEqual(u['options'][4]['cards'],[0,1,2,3])
        self.assertEqual(f['options'][4]['cards'],[0,1,3,4])
        self.assertNotIn('1000',[o['value'] for o in f['options']])

    def test_save_freezes_old_effective_form_until_boot(self):
        self.config(v50_card='2',data_sim='1',keep_sms='yes',dual_standby='0')
        result=self.mod.execute('set',{'model':'v50','mode':'3'})
        self.assertEqual((result['mode'],result['active_mode'],result['effective_board_id']),('3','auto',1))
        self.assertTrue(result['pending_reboot'])
        self.assertEqual(self.values()['v50_card'],'2')
        self.mod.execute('boot',{})
        self.assertEqual(self.get()['effective_board_id'],3)
        self.assertNotIn('v50_card',self.values())
        self.assertEqual(self.values()['keep_sms'],'yes')
        self.assertEqual(self.values()['dual_standby'],'0')

    def test_restore_auto_waits_for_restart_and_removes_override(self):
        self.config(v50_board_override='3')
        self.mod.execute('boot',{})
        result=self.mod.execute('set',{'model':'v50','mode':'auto'})
        self.assertEqual((result['mode'],result['active_mode']),('auto','3'))
        self.assertNotIn('v50_board_override',self.values())
        self.mod.execute('boot',{})
        self.assertEqual(self.get()['effective_board_id'],1)

    def test_pending_form_does_not_change_on_service_restart(self):
        self.mod.execute('set',{'model':'v50','mode':'3'})
        with patch.dict(os.environ,PRODUCT_FORM_TEST_LIVE='1'):
            self.mod.execute('boot',{})
        self.assertEqual(self.get()['effective_board_id'],1)
        self.assertTrue(self.get()['pending_reboot'])

    def test_invalid_and_cross_product_choices_are_not_saved(self):
        for choice in ('4','1000','-1','3; reboot','',None):
            with self.subTest(choice=choice):
                with self.assertRaises(self.mod.Error):
                    self.mod.execute('set',{'model':'v50','mode':choice})
        with self.assertRaisesRegex(self.mod.Error,'model_changed'):
            self.mod.execute('set',{'model':'u30pro','mode':'1'})
        self.assertEqual(self.values(),{})

    def test_unsupported_products_have_no_override(self):
        for name in ('f50','u30air','e5'):
            self.product(name,0)
            with self.assertRaisesRegex(self.mod.Error,'unsupported'):self.get()

    def test_unknown_detected_board_can_use_explicit_known_form(self):
        self.product('v50',99)
        self.assertIsNone(self.get()['effective_board_id'])
        result=self.mod.execute('set',{'model':'v50','mode':'2'})
        self.assertEqual(result['detected_board_id'],99)
        self.mod.execute('boot',{})
        self.assertEqual(self.get()['effective_board_id'],2)

    def test_shell_mapping_uses_frozen_marker_and_rejects_invalid_override(self):
        self.mod.execute('set',{'model':'v50','mode':'3'})
        lib=self.mod.library('v50')
        def board():
            return subprocess.run(['/bin/sh','-c','. "$1"; v50_board_id','sh',lib],capture_output=True,text=True)
        self.assertEqual(board().stdout.strip(),'1')
        self.mod.execute('boot',{})
        self.assertEqual(board().stdout.strip(),'3')
        self.mod.marker('v50').write_text('1000\n')
        self.assertNotEqual(board().returncode,0)

    def test_other_unsaved_uci_edits_are_not_committed(self):
        with patch.dict(os.environ,PRODUCT_FORM_TEST_DIRTY='1'):
            with self.assertRaisesRegex(self.mod.Error,'unsaved'):
                self.mod.execute('set',{'model':'v50','mode':'3'})
        self.assertEqual(self.values(),{})
        self.assertFalse(self.commands.exists())

    def test_v50_incompatible_or_partial_pair_cannot_block_preinit(self):
        for routes in ({'v50_route0':'3','v50_route1':'2'}, {'v50_route0':'3'}):
            self.config(v50_card='3',data_sim='1',**routes)
            self.mod.execute('set',{'model':'v50','mode':'3'})
            self.mod.execute('boot',{})
            values=self.values()
            for key in ('v50_card','v50_route0','v50_route1'):self.assertNotIn(key,values)
            self.assertEqual(values['data_sim'],'1')

    def test_v50_valid_pair_survives_manual_form_change(self):
        self.config(v50_card='3',v50_route0='3',v50_route1='1',dual_standby='1')
        self.mod.execute('set',{'model':'v50','mode':'2'})
        self.mod.execute('boot',{})
        for key,value in {'v50_card':'3','v50_route0':'3','v50_route1':'1','dual_standby':'1'}.items():
            self.assertEqual(self.values()[key],value)

    def test_commit_failure_keeps_the_original_active_topology(self):
        with patch.dict(os.environ,PRODUCT_FORM_TEST_FAIL_COMMIT='1'):
            with self.assertRaisesRegex(self.mod.Error,'storage'):
                self.mod.execute('set',{'model':'v50','mode':'3'})
        self.assertEqual(self.get()['active_mode'],'auto')
        self.assertEqual(self.values(),{})

    @unittest.skipUnless((TOP/'devices/f50pro').is_dir(), 'Pro-specific boot migrations')
    def test_f50_incompatible_pair_is_removed_together_before_boot(self):
        self.product('f50pro',4)
        self.config(f50pro_card='4',f50pro_route0='3',f50pro_route1='4',data_sim='1',sms_pool='/etc/sms')
        self.mod.execute('set',{'model':'f50pro','mode':'1'})
        self.mod.execute('boot',{})
        state=self.values()
        for key in ('f50pro_card','f50pro_route0','f50pro_route1'):self.assertNotIn(key,state)
        self.assertEqual(state['data_sim'],'1');self.assertEqual(state['sms_pool'],'/etc/sms')

    @unittest.skipUnless((TOP/'devices/f50pro').is_dir(), 'Pro-specific boot migrations')
    def test_f50_valid_pair_and_u30_single_slot_data_migration(self):
        self.product('f50pro',4)
        self.config(f50pro_card='4',f50pro_route0='3',f50pro_route1='4')
        self.mod.execute('set',{'model':'f50pro','mode':'5'})
        self.mod.execute('boot',{})
        self.assertEqual(self.values()['f50pro_route0'],'3')
        self.assertEqual(self.values()['f50pro_route1'],'4')
        self.product('u30pro',0)
        self.config(u30pro_card='2',data_sim='1')
        self.mod.execute('set',{'model':'u30pro','mode':'5'})
        self.mod.execute('boot',{})
        self.assertEqual(self.values()['data_sim'],'0')
        self.assertNotIn('u30pro_card',self.values())

    def test_boot_precedes_preinit_and_cp_in_the_pro_service(self):
        service=TOP/'devices/u30pro/overlay/etc/init.d/mu300-vendor'
        if not service.exists():self.skipTest('Pro service is in the Pro repository')
        text=service.read_text()
        self.assertLess(text.index('product-form boot'),text.index('/usr/sbin/u30pro-sim-preinit'))
        self.assertLess(text.index('/usr/sbin/u30pro-sim-preinit'),text.index('procd_open_instance cp\n'))


if __name__=='__main__':
    unittest.main(verbosity=2)
