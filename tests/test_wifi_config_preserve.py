"""Run hotspot initialization on restored UCI without an initialization stamp."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
SCRIPT = TOP/'rootfs/overlay/opt/mu300/bin/openwrt-wifi-config'
MOCK = '''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['WIFI_STATE']);s=json.loads(p.read_text());a=sys.argv[1:]
if a and a[0]=='-q':a=a[1:]
op=a[0];v=s['values']
if op=='show':
 if s.get('unreadable'):sys.exit(1)
 for key,value in v.items():
  if key.count('.')==1:print(key+'='+value)
elif op=='get':
 if a[1] not in v:sys.exit(1)
 print(v[a[1]])
elif op=='changes':
 if s.get('pending'):print('wireless.ap0.key=unsaved')
else:
 s['writes'].append(a);p.write_text(json.dumps(s));sys.exit(79)
'''


@unittest.skipUnless(os.name == 'posix', 'POSIX shell fixture')
class PreserveWireless(unittest.TestCase):
    def run_config(self, mode='ap', encryption='psk2', key='saved-password', pending=False,
                   extra=False, disabled='0', marker=False, unreadable=False):
        with tempfile.TemporaryDirectory(prefix='wifi-restore-') as folder:
            root=Path(folder)
            for name in ('etc/mu300','etc/config','bin'):(root/name).mkdir(parents=True,exist_ok=True)
            (root/'etc/openwrt_release').write_text('OpenWrt\n')
            (root/'etc/mu300/hotspot.conf').write_text('SSID=Old Android hotspot\nPSK=old-android-password\n')
            values={'wireless.radio0':'wifi-device','wireless.radio0.band':'5g',
                    'wireless.radio0.channel':'36','wireless.radio0.disabled':disabled,
                    'wireless.ap0':'wifi-iface','wireless.ap0.device':'radio0',
                    'wireless.ap0.mode':mode,'wireless.ap0.ssid':'User WiFi',
                    'wireless.ap0.encryption':encryption,'wireless.ap0.key':key,
                    'wireless.ap0.disabled':disabled}
            if extra:
                values.update({'wireless.default_radio0':'wifi-iface','wireless.default_radio0.mode':'ap',
                    'wireless.default_radio0.device':'radio0','wireless.default_radio0.ssid':'OpenWrt',
                    'wireless.default_radio0.encryption':'none','wireless.default_radio0.disabled':'1'})
            state=root/'state.json';state.write_text(json.dumps({'values':values,'writes':[],'pending':pending,'unreadable':unreadable}))
            # The real wireless file must remain byte-for-byte intact as well.
            raw=('config wifi-iface ap0\n option key '+repr(key)+'\n').encode()
            (root/'etc/config/wireless').write_bytes(raw)
            if marker:(root/'etc/mu300/wifi-configured').touch()
            for name in ('uci','wifi'):
                p=root/'bin'/name;p.write_text(MOCK);p.chmod(0o755)
            p=subprocess.run(['sh',str(SCRIPT)],env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH'],
                MU300_SYSROOT=str(root),WIFI_STATE=str(state),
                MU300_WIFI_CHANNELS=str(TOP/'rootfs/overlay/opt/mu300/lib/wifi-channels.sh')),
                capture_output=True,text=True,timeout=5)
            after=json.loads(state.read_text())
            self.assertEqual(after['values'],values)
            self.assertEqual(after['writes'],[])
            self.assertEqual((root/'etc/config/wireless').read_bytes(),raw)
            self.assertNotIn(key,p.stdout+p.stderr) if key else None
            self.assertEqual(p.returncode,1 if (pending or unreadable) and not marker else 0,p.stderr)
            self.assertEqual((root/'etc/mu300/wifi-configured').exists(),marker or not (pending or unreadable))

    def test_restored_passwords_with_and_without_stamp(self):
        for key in ('new-password',"quote'and\\slash$;123",'f'*64):
            for marker in (False,True):
                with self.subTest(key_length=len(key),stamp=marker):self.run_config(key=key,marker=marker)

    def test_all_saved_roles_and_auth_modes_are_preserved(self):
        for mode in ('ap','sta','mesh'):
            for enc in ('psk2','sae','sae-mixed','none','wpa2'):
                with self.subTest(mode=mode,enc=enc):self.run_config(mode,enc,'' if enc=='none' else 'user-password')

    def test_disabled_or_extra_templates_do_not_erase_user_ap(self):
        self.run_config(disabled='1')
        self.run_config(extra=True)

    def test_pending_changes_are_neither_committed_nor_stamped(self):
        self.run_config(pending=True)

    def test_unreadable_existing_config_is_not_reinitialized(self):
        self.run_config(unreadable=True)


if __name__=='__main__':unittest.main()
