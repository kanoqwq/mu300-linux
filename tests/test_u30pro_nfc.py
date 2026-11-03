"""NFC tests use a file-backed I2C device and isolated configuration, never hardware."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from helpers import ShellTest, TOP


def attr(tag, value):
    return struct.pack('>HH', tag, len(value)) + value


def credential(ssid, key=b''):
    """Independent WSC encoder; no production encoder or fixtures as oracle."""
    cred = (attr(0x1026, b'\1') + attr(0x1045, ssid) +
            attr(0x1003, b'\0\x20' if key else b'\0\1') +
            attr(0x100f, b'\0\10' if key else b'\0\1') +
            attr(0x1027, key) + attr(0x1020, b'\xff' * 6))
    body = attr(0x104a, b'\x10') + attr(0x100e, cred) + attr(0x1049, b'\0\x37\x2a\0\1\x20')
    kind = b'application/vnd.wfa.wsc'
    msg = bytes([0xd2, len(kind), len(body)]) + kind + body
    return bytes([3, len(msg)]) + msg + b'\xfe'


class NFCDevice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='u30pro-nfc-build-')
        cls.cli = Path(cls.build.name) / 'nfc'
        subprocess.run(['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined',
                        str(TOP/'tests/fixtures/u30pro_nfc_io.c'), '-o', str(cls.cli)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='u30pro-nfc-io-')
        self.image = Path(self.tmp.name)/'tag'
        self.log = Path(self.tmp.name)/'writes'
        image = bytearray(65536)
        image[12:16] = bytes.fromhex('e1106d00')
        self.image.write_bytes(image)
        self.env = dict(os.environ, NFC_TEST_IMAGE=str(self.image), NFC_TEST_WRITES=str(self.log))

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, key=b'', **env):
        return subprocess.run([str(self.cli), *args], input=key, capture_output=True,
                              env=dict(self.env, **env), timeout=10)

    def writes(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def patch(self, offset, data):
        image = bytearray(self.image.read_bytes())
        image[offset:offset+len(data)] = data
        self.image.write_bytes(image)

    def test_wsc_encoding_and_boundaries(self):
        for ssid, key in [('Open', ''), ('测试 AP', 'test key 123'), ('a'*32, 'AB'*32)]:
            with self.subTest(ssid=ssid):
                r = self.call('encode', ssid, 'wpa2' if key else 'open', *([key] if key else []))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(bytes.fromhex(r.stdout.decode()), credential(ssid.encode(), key.encode()))
        for ssid, key in [('', ''), ('a'*33, ''), ('ok', 'short'), ('ok', 'z'*64)]:
            r = self.call('encode', ssid, 'wpa2' if key else 'open', *([key] if key else []))
            self.assertNotEqual(r.returncode, 0)

    def test_write_readback_and_no_repeated_eeprom_writes(self):
        r = self.call('wifi', 'My AP', 'wpa2', key=b'test key 123')
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = credential(b'My AP', b'test key 123')
        self.assertEqual(self.image.read_bytes()[16:16+len(expected)], expected)
        writes = self.writes()
        self.assertEqual(writes[0], '16 4')
        self.assertEqual(writes[-1], '16 4')
        r = self.call('wifi', 'My AP', 'wpa2', key=b'test key 123')
        self.assertEqual(r.stdout, b'unchanged\n')
        self.assertEqual(self.writes(), writes)
        r = self.call('show')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b'ssid My AP', r.stdout)
        self.assertNotIn(b'test key 123', r.stdout + r.stderr)

    def test_off_clears_ndef_and_on_restores_without_touching_identity(self):
        identity = self.image.read_bytes()[:16]
        self.assertEqual(self.call('wifi', 'AP', 'open').returncode, 0)
        self.assertEqual(self.call('clear').returncode, 0)
        self.assertEqual(self.image.read_bytes()[16:19], b'\3\0\xfe')
        self.assertIn(b'empty NDEF', self.call('show').stdout)
        count = len(self.writes())
        self.assertEqual(self.call('clear').stdout, b'unchanged\n')
        self.assertEqual(len(self.writes()), count)
        self.assertEqual(self.call('wifi', 'AP', 'open').returncode, 0)
        self.assertEqual(self.image.read_bytes()[:16], identity)

    def test_interrupted_body_write_keeps_ndef_unpublished(self):
        self.assertEqual(self.call('wifi', 'Old AP', 'open').returncode, 0)
        r = self.call('wifi', 'New AP', 'wpa2', key=b'new key 123', NFC_TEST_FAIL_OFFSET='0x14')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.image.read_bytes()[17], 0)
        self.assertEqual(self.call('wifi', 'New AP', 'wpa2', key=b'new key 123').returncode, 0)

    def test_read_only_cc_and_invalid_input_never_write(self):
        self.patch(15, b'\x0f')
        self.assertEqual(self.call('show').returncode, 0)
        self.assertNotEqual(self.call('clear').returncode, 0)
        self.assertFalse(self.writes())
        for key in (b'short', b'validpass\nextra', b'test\0password', b'a'*80):
            self.assertNotEqual(self.call('wifi', 'AP', 'wpa2', key=key).returncode, 0)
            self.assertFalse(self.writes())

    def test_malformed_wsc_is_bounded(self):
        for data in (b'\3\xff', b'\3\1', b'\3\x50\xd2\xff\xff',
                     b'\3\x30\xd2\x16\x10application/vnd.wfa.wsc\x10\x0e\xff\xff'):
            self.patch(16, data + b'\0'*200)
            r = self.call('show')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(b'Sanitizer', r.stderr)
            self.assertFalse(self.writes())


class NFCControl(ShellTest):
    def setUp(self):
        super().setUp()
        self.control = self.tmp/'control'
        self.control.write_bytes((TOP/'devices/u30pro/overlay/usr/sbin/u30pro-nfc-control').read_bytes().replace(b'\r\n', b'\n'))
        self.control.chmod(0o755)
        self.platform = self.tmp/'platform'
        self.platform.write_text('u30pro\n')
        self.config = self.tmp/'config'
        self.config.write_text(json.dumps({'wireless.selected.key': 'private dummy password',
                                          'wireless.selected.ssid': 'Selected AP'}))
        self.fields = self.tmp/'fields'
        self.fields.write_text('available\n1\nsection\nselected\nssid\nSelected AP\nenc\npsk2\nenabled\n1\n')
        self.stub('uci', '''exec python3 - "$@" <<'PY'
import json, os, sys
from pathlib import Path
p=Path(os.environ['STUBLOG'])/'config'; c=json.loads(p.read_text()); a=sys.argv[1:]
if a[0]=='-q': a=a[1:]
if a[0]=='get':
    if a[1] not in c: sys.exit(1)
    print(c[a[1]])
elif a[0]=='set':
    k,v=a[1].split('=',1); c[k]=v; p.write_text(json.dumps(c))
elif a[0]!='commit': sys.exit(2)
PY''')
        self.stub('hotspot', 'cat "$STUBLOG/fields"; exit "${HOTSPOT_FAIL:-0}"')
        # stdin belongs to the Wi-Fi key, so use a script file (not a heredoc).
        helper = self.stubs/'nfc-helper.py'
        helper.write_text('import json,os,sys\nfrom pathlib import Path\np=Path(os.environ["STUBLOG"])/"calls"\nwith p.open("a") as f: f.write(json.dumps({"args":sys.argv[1:],"stdin":sys.stdin.read()})+"\\n")\nsys.exit(int(os.environ.get("NFC_FAIL","0")))\n')
        self.stub('nfc', 'exec python3 "$STUBLOG/stubs/nfc-helper.py" "$@"')
        for cmd in ('logger', 'ubus'):
            self.stub(cmd, 'printf "%s\\n" "$*" >> "$STUBLOG/messages"')

    def call(self, *args, **env):
        return self.script(['sh'], self.control, *args,
                           U30PRO_NFC_TOOL=self.stubs/'nfc', U30PRO_NFC_HOTSPOT=self.stubs/'hotspot',
                           U30PRO_NFC_PLATFORM=self.platform, U30PRO_NFC_CONTROL=self.control,
                           U30PRO_NFC_RUN=self.tmp/'run', **env)

    def calls(self):
        p=self.tmp/'calls'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []

    def test_switch_persists_and_config_events_do_not_reenable(self):
        self.assertEqual(self.call('off').returncode, 0)
        self.assertEqual(self.call('state').stdout.strip(), '0')
        self.assertEqual(self.call('sync').returncode, 0)
        self.assertEqual([c['args'] for c in self.calls()], [['clear'], ['clear']])
        self.assertEqual(self.call('toggle').returncode, 0)
        self.assertEqual(self.call('state').stdout.strip(), '1')
        self.assertEqual(self.calls()[-1], {'args':['wifi','Selected AP','wpa2'], 'stdin':'private dummy password'})
        self.assertNotIn('private dummy password', (self.tmp/'messages').read_text())

    def test_selected_ap_and_authentication_modes(self):
        for mode, expected in [('none','open'), ('sae-mixed','wpa2'), ('psk2+ccmp','wpa2'),
                               ('sae', None), ('owe', None), ('wpa2', None)]:
            self.fields.write_text(f'available\n1\nsection\nselected\nssid\nSelected AP\nenc\n{mode}\nenabled\n1\n')
            r=self.call('sync'); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.calls()[-1]['args'], ['wifi','Selected AP',expected] if expected else ['clear'])

    def test_unavailable_rpc_does_not_erase_token(self):
        self.assertNotEqual(self.call('sync', HOTSPOT_FAIL='1').returncode, 0)
        self.assertFalse(self.calls())

    def test_field_value_is_not_parsed_as_another_field_name(self):
        self.fields.write_text('available\n1\nsection\nselected\nssid\nenabled\nenc\npsk2\nenabled\n1\n')
        self.assertEqual(self.call('sync').returncode, 0)
        self.assertEqual(self.calls()[-1]['args'], ['wifi','Selected AP','wpa2'])

    def test_failed_write_is_not_reported_as_success(self):
        r=self.call('on', NFC_FAIL='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.tmp/'run/result').read_text().strip(), 'error')
        self.assertIn('失败', (self.tmp/'messages').read_text())
        self.assertFalse(list((self.tmp/'run').glob('error.*')))

    def test_disabled_ap_and_other_platform(self):
        self.fields.write_text('available\n1\nsection\nselected\nenabled\n0\n')
        self.assertEqual(self.call('sync').returncode, 0)
        self.assertEqual(self.calls()[-1]['args'], ['clear'])
        self.platform.write_text('u30air\n')
        self.assertEqual(self.call('toggle').returncode, 2)
        self.assertEqual(len(self.calls()), 1)

    def test_existing_function_menu_plugin_contract(self):
        plugin=TOP/'devices/u30pro/overlay/usr/share/u30pro/ui-plugins/nfc.plugin'
        values=dict(s.split('=',1) for s in plugin.read_text().splitlines() if '=' in s)
        self.assertEqual(values['type'], 'toggle')
        self.assertEqual(values['title'], 'NFC')
        self.assertEqual(values['cmd'], '/usr/sbin/u30pro-nfc-control toggle')
        self.assertEqual(values['state_cmd'], '/usr/sbin/u30pro-nfc-control state')


class NFCScreen(unittest.TestCase):
    def test_real_screen_loads_and_renders_native_toggle(self):
        from u30pro_power_menu import PowerMenu
        directory=(TOP/'devices/u30pro/overlay/usr/share/u30pro/ui-plugins').as_posix()
        source=r'''#define UI_HOST
#define UI_RUN "TEST_RUN"
#define main simulator_main
#include "u30pro_ui.c"
#undef main
#include <assert.h>
int main(void) {
 ui_init_canvases();g_ms=vclock=10000;
 assert(plug_load_file("PLUGIN_DIRECTORY","nfc.plugin",&plugs[0]));
 nplug=1;assert(plugs[0].type==PT_TOGGLE);
 assert(!strcmp(plugs[0].title,"NFC"));
 assert(gx_has_all(FN16,plugs[0].title));
 fn_build();int index=-1;
 for(int i=0;i<nfn_entries;i++)if(fn_entries[i].plugin==0)index=i;
 assert(index>=0);
 // Existing function rows and switch geometry, including scrolled entries.
 scroll_pos[0]=(float)(index*54);g_reg=1;page_func(&scr);g_reg=0;
 int found=0;for(int i=0;i<nhits;i++)if(hits[i].id==H_FN+index)found=1;
 assert(found);shot_write("TEST_RUN","nfc-function");
 // Run no device command: substitute the command only inside the host model.
 snprintf(plugs[0].cmd,sizeof plugs[0].cmd,"printf toggled");
 fn_sub=-1;fn_open(index);assert(plugs[0].state==1 && fn_sub==-1);
 fn_open(index);assert(plugs[0].state==0 && fn_sub==-1);
 return 0;
}'''.replace('PLUGIN_DIRECTORY',directory)
        PowerMenu.compile_run(self,source)


if __name__ == '__main__':
    unittest.main()
