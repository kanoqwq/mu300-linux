"""Physical F50 lamps keep independent, localized brightness settings."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP


LED = TOP / 'rootfs/overlay/opt/mu300/bin/led-status'
UI = TOP / 'openwrt/overlay/www/luci-static/resources/view/system/leds.js'
DEFAULTS = TOP / 'openwrt/overlay/etc/uci-defaults/90-mu300'


class LedSettings(unittest.TestCase):
    def test_v50_without_physical_groups_never_drives_unrelated_lamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);leds=base/'leds';run=base/'run';run.mkdir()
            bindir=base/'bin';bindir.mkdir()
            for name, maximum in [('aw_led',120),('red',120),('green',80),('blue',120),
                                  ('sc27xx:red',255),('sc27xx:green',255),('sc27xx:blue',255)]:
                node=leds/name;node.mkdir(parents=True)
                (node/'brightness').write_text('7\n' if name.startswith('sc27xx:') else '0\n')
                (node/'max_brightness').write_text(str(maximum)+'\n')
                (node/'trigger').write_text('none\n')
            for name,body in [('mu300-device','echo v50'),('uci','exit 0')]:
                p=bindir/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
            env=dict(os.environ,PATH=f'{bindir}:{os.environ["PATH"]}',
                     MU300_LED_SYSFS_ROOT=str(leds),MU300_LED_RUN_DIR=str(run))
            def apply(state,expected):
                p=subprocess.run(['sh',str(LED),state],env=env,capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                actual=tuple((leds/n/'brightness').read_text().strip() for n in ('red','green','blue'))
                self.assertEqual(actual,expected)
                for n in ('red','green','blue'):
                    self.assertEqual((leds/('sc27xx:'+n)/'brightness').read_text(),'7\n')
                self.assertFalse((leds/'white').exists())
            apply('online-4g',('0','0','0'))
            apply('online-5g',('0','0','0'))
            apply('offline',('0','0','0'))
            apply('wifi-on',('0','0','0'))

    def test_signal_and_wifi_brightness_are_independent_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            leds = base / 'leds'
            for name in ('sc27xx:red', 'sc27xx:green', 'sc27xx:blue', 'keyboard-backlight'):
                led = leds / name
                led.mkdir(parents=True)
                (led / 'brightness').write_text('0\n')
                (led / 'trigger').write_text('none\n')
                (led / 'max_brightness').write_text('255\n')
            bindir = base / 'bin'
            bindir.mkdir()
            uci = bindir / 'uci'
            uci.write_text('''#!/bin/sh
case "$3" in
  system.mu300_leds.signal) echo "${TEST_SIGNAL_ENABLED:-1}" ;;
  system.mu300_leds.wifi) echo "${TEST_WIFI_ENABLED:-1}" ;;
  system.mu300_leds.signal_brightness) echo "${TEST_SIGNAL_BRIGHTNESS:-255}" ;;
  system.mu300_leds.wifi_brightness) echo "${TEST_WIFI_BRIGHTNESS-}" ;;
esac
''')
            uci.chmod(0o755)
            device = bindir / 'mu300-device'
            device.write_text('#!/bin/sh\necho f50\n')
            device.chmod(0o755)
            run_dir = base / 'run'
            run_dir.mkdir()
            env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}',
                       MU300_LED_SYSFS_ROOT=str(leds), MU300_LED_RUN_DIR=str(run_dir),
                       TEST_SIGNAL_BRIGHTNESS='64', TEST_WIFI_BRIGHTNESS='32')

            def apply(state):
                result = subprocess.run([str(LED), state], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

            def level(name):
                return (leds / name / 'brightness').read_text().strip()

            apply('online-4g')
            self.assertEqual(tuple(level('sc27xx:' + c) for c in ('red', 'green', 'blue')),
                             ('0', '0', '64'))
            apply('wifi-on')
            self.assertEqual(level('keyboard-backlight'), '32')
            apply('online-5g')
            self.assertEqual(tuple(level('sc27xx:' + c) for c in ('red', 'green', 'blue')),
                             ('0', '64', '0'))
            self.assertEqual(level('keyboard-backlight'), '32')
            env['TEST_SIGNAL_BRIGHTNESS'] = '96'
            env['TEST_WIFI_BRIGHTNESS'] = '48'
            apply('refresh')
            self.assertEqual(level('sc27xx:green'), '96')
            self.assertEqual(level('keyboard-backlight'), '48')
            env['TEST_WIFI_BRIGHTNESS'] = ''
            apply('refresh')
            self.assertEqual(level('keyboard-backlight'), '55')
            env['TEST_SIGNAL_ENABLED'] = '0'
            env['TEST_WIFI_ENABLED'] = '0'
            apply('refresh')
            self.assertEqual(tuple(level('sc27xx:' + c) for c in ('red', 'green', 'blue')),
                             ('0', '0', '0'))
            self.assertEqual(level('keyboard-backlight'), '0')
            env['TEST_SIGNAL_ENABLED'] = '1'
            env['TEST_WIFI_ENABLED'] = '1'
            env['TEST_SIGNAL_BRIGHTNESS'] = '0'
            env['TEST_WIFI_BRIGHTNESS'] = '255'
            (leds / 'keyboard-backlight/max_brightness').write_text('100\n')
            apply('refresh')
            self.assertEqual(level('sc27xx:green'), '0')
            self.assertEqual(level('keyboard-backlight'), '100')
            env['TEST_SIGNAL_BRIGHTNESS'] = 'bad'
            env['TEST_WIFI_BRIGHTNESS'] = '999'
            (leds / 'keyboard-backlight/max_brightness').write_text('255\n')
            apply('refresh')
            self.assertEqual(level('sc27xx:green'), '255')
            self.assertEqual(level('keyboard-backlight'), '55')

    def test_defaults_and_animations_use_brightness(self):
        defaults = DEFAULTS.read_text(encoding='utf-8')
        script = LED.read_text(encoding='utf-8')
        self.assertIn("set system.mu300_leds.signal_brightness='255'", defaults)
        self.assertIn('wifi_led_brightness=127', defaults)
        self.assertIn('!= f50 ] || wifi_led_brightness=55', defaults)
        self.assertIn("set system.mu300_leds.wifi_brightness='$wifi_led_brightness'", defaults)
        self.assertIn('set_rgb "$_r" "$_w" "$_b"', script)
        self.assertIn('set_rgb 255 0 0; pause_us 120000', script)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for LuCI JS smoke tests')
    def test_page_has_chinese_english_turkish_and_validated_controls(self):
        harness = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
let lang = 'en';
const L = { env: { get lang() { return lang; } } };
const form = { NamedSection: {}, GridSection: {}, Flag: {}, Value: {}, ListValue: {} };
form.Map = function() {
    const map = { sections: [], section(_type, name, _id, title) {
        const section = { name, title, options: [], option(_type, key, label, description) {
            const option = { key, label, description, value() {} };
            this.options.push(option); return option;
        } };
        this.sections.push(section); return section;
    }, render() { return this; } };
    return map;
};
const view = { extend: obj => obj };
const rpc = { declare: () => () => ({}) };
const page = new Function('view', 'uci', 'rpc', 'form', 'fs', 'L', 'document', 'navigator', '_', src)(
    view, {}, rpc, form, {}, L, { documentElement: { lang: 'en' } }, { language: 'en' }, s => s);
for (const [locale, title, signal, wifi] of [
    ['en', 'LED Configuration', 'Signal lamp brightness', 'Wi-Fi lamp brightness'],
    ['zh_cn', 'LED 配置', '信号灯亮度', 'Wi-Fi 灯亮度'],
    ['tr_TR', 'LED yapılandırması', 'Sinyal ışığı parlaklığı', 'Wi-Fi ışığı parlaklığı']
]) {
    lang = locale;
    const map = page.render([{}, []]);
    if (map.sections[0].title !== (locale === 'en' ? 'Physical status lamps' :
        locale.startsWith('zh') ? '物理状态灯' : 'Fiziksel durum ışıkları')) throw Error(locale + ': section');
    const options = map.sections[0].options;
    const sig = options.find(o => o.key === 'signal_brightness');
    const wf = options.find(o => o.key === 'wifi_brightness');
    if (sig.label !== signal || wf.label !== wifi) throw Error(locale + ': brightness labels');
    if (sig.datatype !== 'range(0,255)' || wf.datatype !== 'range(0,255)') throw Error('fallback range');
    if (sig.default !== '255' || wf.default !== '127') throw Error('defaults');
    const actual = page.render([{ 'keyboard-backlight': { max_brightness: 127, triggers: [] }, 'sc27xx:blue': { max_brightness: 255, triggers: [] } }, []]);
    const realWifi = actual.sections[0].options.find(o => o.key === 'wifi_brightness');
    if (realWifi.datatype !== 'range(0,127)' || realWifi.default !== '55') throw Error('F50 hardware maximum and default');
    const v50 = page.render([{ aw_led: {triggers: []}, red: {triggers: []}, green: {triggers: []}, blue: {triggers: []} }, []]);
    if (v50.sections[0].options.some(o => o.key === 'wifi' || o.key === 'wifi_brightness')) throw Error('V50 has no verified independent Wi-Fi lamp');
    const adapted = page.render([{ 'v50:net1:white': { max_brightness: 80, triggers: [] } }, []]);
    if (!adapted.sections[0].options.some(o => o.key === 'signal2') ||
        !adapted.sections[0].options.some(o => o.key === 'wifi')) throw Error('V50 physical lamps missing');
    const u30 = page.render([{ 'zte-ldo0': {max_brightness:1,triggers:[]}, 'zte-ldo1': {max_brightness:1,triggers:[]}, 'zte-ldo2': {max_brightness:1,triggers:[]}, net_blue: {max_brightness:1,triggers:[]} }, []]);
    const opts = u30.sections[0].options;
    if (opts.some(o => ['signal_brightness','wifi_brightness','signal2'].includes(o.key))) throw Error('U30 binary lamps must not offer fake brightness or a second SIM lamp');
    const battery = opts.find(o => o.key === 'battery_brightness');
    if (!battery || battery.datatype !== 'range(0,255)' || !opts.some(o => o.key === 'battery')) throw Error('U30 battery controls');
    if (!opts.some(o => o.key === 'timeout' && o.default === '60')) throw Error('U30 idle timeout');

}
'''
        result = subprocess.run(['node', '-', str(UI)], input=harness, capture_output=True,
                                text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
