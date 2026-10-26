"""The dashboard's battery tile: dashboard-info finds the battery and the charger in a fake /sys/class/power_supply
(MU300_POWER_SUPPLY_DIR) the way each device and kernel names them, and home.js turns the result into the tile.

- F50: no battery. Under mainline its PMIC's fuel gauge (sc27xx-fgu) still appears, with made-up values: no tile.
- U30 Air under 5.4: the vendor charger manager's "battery" (capacity, status, voltage_now, current_now) and the
  charger's "usb"/"ac" online flags (FINDINGS 33, mu300-toolkit). The vendor fuel gauge may be there as well.
- U30 Air under mainline: the UMP9620 fuel gauge "sc27xx-fgu" (FINDINGS 33d), positive current into the battery,
  no charger driver at all.
- Anything else of type Battery / USB / Mains is found by type, whatever its name."""
import json
import shutil
import subprocess
import unittest

from helpers import ShellTest, TOP

APP = TOP / 'openwrt' / 'luci-app-mu300'
INFO = APP / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'dashboard-info'
HOME = APP / 'htdocs' / 'luci-static' / 'resources' / 'view' / 'mu300' / 'home.js'

# FINDINGS 33d's node and the patch's sign: discharging at 470 mA from 3.85 V
FGU_MAINLINE = {'type': 'Battery', 'present': '1', 'capacity': '64', 'status': 'Discharging',
                'voltage_now': '3850000', 'current_now': '-470000', 'voltage_ocv': '3901000', 'temp': '281'}
BATTERY_54 = {'type': 'Battery', 'present': '1', 'capacity': '87', 'status': 'Charging',
              'voltage_now': '4123000', 'current_now': '780000'}


class Power(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('busybox', 'shift 4; exec "$@"')   # busybox timeout -s KILL N CMD...
        for name in ('ubus', 'ip', 'iw', 'uci'):
            self.stub(name, 'exit 1')
        self.psy = self.tmp / 'power_supply'
        self.psy.mkdir()

    def supply(self, name, **attrs):
        d = self.psy / name
        d.mkdir()
        for k, v in attrs.items():
            (d / k).write_text(v + '\n')

    def device(self, name):
        if name is None:
            return
        self.stub('mu300-device', f'echo {name}')

    def power(self):
        out = []
        for shell in self.each_shell():
            r = self.script(shell, INFO, MU300_POWER_SUPPLY_DIR=self.psy)
            out.append(json.loads(r.stdout)['power'])
        self.assertTrue(out)
        for p in out[1:]:
            self.assertEqual(p, out[0])
        return out[0]

    def test_f50_under_mainline_has_no_battery(self):
        self.device('f50')
        self.supply('sc27xx-fgu', **FGU_MAINLINE)
        p = self.power()
        self.assertEqual(p['present'], 0)
        self.assertIsNone(p['capacity'])
        self.assertIsNone(p['w'])

    def test_v50_has_no_battery(self):
        # the V50 (FINDINGS 95) has none either, and the same PMIC fuel gauge under mainline
        self.device('v50')
        self.supply('sc27xx-fgu', **FGU_MAINLINE)
        p = self.power()
        self.assertEqual(p['present'], 0)
        self.assertIsNone(p['capacity'])

    def test_f50_under_54_has_no_battery(self):
        self.device('f50')
        self.supply('usb', type='USB', online='1')
        p = self.power()
        self.assertEqual(p['present'], 0)
        self.assertIsNone(p['usb'])

    def test_u30air_under_54(self):
        self.device('u30air')
        self.supply('sc27xx-fgu', type='Unknown', capacity='12')   # the vendor gauge behind the charger manager
        self.supply('battery', **BATTERY_54)
        self.supply('ac', type='Mains', online='0')
        self.supply('usb', type='USB', online='1')
        p = self.power()
        self.assertEqual((p['present'], p['capacity'], p['status']), (1, 87, 'Charging'))
        self.assertEqual(p['volt'], 4.12)
        self.assertEqual(p['ua'], 780000)
        self.assertEqual(p['w'], 3.22)                    # 4.123 V x 0.78 A
        self.assertEqual(p['usb'], 1)
        self.assertIsNone(p['in_w'])                      # the charger manager's usb says online only

    def test_u30air_under_mainline(self):
        self.device('u30air')
        self.supply('sc27xx-fgu', **FGU_MAINLINE)
        p = self.power()
        self.assertEqual((p['present'], p['capacity'], p['status']), (1, 64, 'Discharging'))
        self.assertEqual(p['volt'], 3.85)
        self.assertEqual(p['ua'], -470000)
        self.assertEqual(p['w'], 1.81)                    # |3.85 V x -0.47 A|
        self.assertIsNone(p['usb'])                       # no charger driver under mainline

    def test_found_by_type_with_charger_input(self):
        self.device(None)                                 # no mu300-device: any board, found by type
        self.supply('bq2560x-charger', type='USB', online='1', voltage_now='5012000', current_now='900000')
        self.supply('gauge', type='Battery', status='Charging', voltage_now='3990000', current_now='660000')
        self.supply('wireless', type='Wireless', online='1')
        p = self.power()
        self.assertEqual(p['present'], 1)
        self.assertIsNone(p['capacity'])                  # no capacity file: the tile shows the voltage
        self.assertEqual(p['volt'], 3.99)
        self.assertEqual(p['w'], 2.63)
        self.assertEqual((p['usb'], p['in_volt'], p['in_ua'], p['in_w']), (1, 5.01, 900000, 4.51))

    def test_the_charger_online_after_offline_ones_with_input_current(self):
        self.device('u30air')
        self.supply('battery', **BATTERY_54)
        self.supply('ac', type='Mains', online='0')
        self.supply('bq2560x', type='USB', online='0', voltage_now='1000000', current_now='1')
        self.supply('usb', type='USB', online='1', voltage_now='5000000', input_current_now='1000000')
        p = self.power()
        self.assertEqual((p['usb'], p['in_volt'], p['in_ua'], p['in_w']), (1, 5.0, 1000000, 5.0))

    def test_an_absent_battery_or_odd_values(self):
        self.device('u30air')
        self.supply('battery', type='Battery', present='0', capacity='50')
        self.supply('sc27xx-fgu', type='Battery', capacity='x', status='Full', voltage_now='', current_now='a"b')
        p = self.power()
        self.assertEqual(p['present'], 1)
        self.assertEqual(p['status'], 'Full')
        self.assertIsNone(p['capacity'])
        self.assertIsNone(p['volt'])
        self.assertIsNone(p['ua'])
        self.assertIsNone(p['w'])

    def test_no_supplies_at_all(self):
        self.device('u30air')
        p = self.power()
        self.assertEqual(p['present'], 0)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the LuCI JS tests')
class Tile(unittest.TestCase):
    """home.js's power block alone, with M.set/M.v stubbed and _() returning the English text."""

    def render(self, power):
        src = HOME.read_text(encoding='utf-8')
        start = src.index('var p = i.power || {}')
        end = src.index("M.set('model'", start)
        js = r'''
String.prototype.format = function() { const a = arguments; let n = 0; return this.replace(/%s/g, () => String(a[n++])); };
const _ = (s) => s;
const out = {}, kpi = { style: { display: 'none' } };
const M = { set: (id, t) => { out[id] = t; }, v: (id) => id === 'batt-kpi' ? kpi : null };
const i = { power: ''' + json.dumps(power) + ''' };
''' + src[start:end] + '''
process.stdout.write(JSON.stringify({ shown: kpi.style.display !== 'none', value: out.batt, label: out['batt-l'] }));
'''
        r = subprocess.run(['node', '-'], input=js, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_no_battery_no_tile(self):
        t = self.render({'present': 0, 'capacity': None, 'usb': 1})
        self.assertFalse(t['shown'])
        self.assertNotIn('value', t)

    def test_charging_on_usb(self):
        t = self.render({'present': 1, 'capacity': 87, 'status': 'Charging', 'volt': 4.12, 'ua': 780000,
                         'w': 3.22, 'usb': 1, 'in_volt': 5.01, 'in_ua': 900000, 'in_w': 4.51})
        self.assertTrue(t['shown'])
        self.assertEqual(t['value'], '87%')
        self.assertEqual(t['label'], 'Battery · charging 3.2 W · 4.12 V · USB in 4.5 W (5.01 V)')

    def test_discharging_under_mainline(self):
        t = self.render({'present': 1, 'capacity': 64, 'status': 'Discharging', 'volt': 3.85, 'ua': -470000,
                         'w': 1.81, 'usb': None})
        self.assertEqual(t['value'], '64%')
        self.assertEqual(t['label'], 'Battery · drawing 1.8 W · 3.85 V')

    def test_full_and_usb_without_numbers(self):
        t = self.render({'present': 1, 'capacity': 100, 'status': 'Full', 'volt': 4.35, 'ua': 0, 'w': 0,
                         'usb': 1})
        self.assertEqual(t['label'], 'Battery · full · 4.35 V · USB')

    def test_unknown_status_goes_by_the_sign(self):
        t = self.render({'present': 1, 'capacity': None, 'status': 'Unknown', 'volt': 3.99, 'ua': 650000,
                         'w': 2.59})
        self.assertEqual(t['value'], '3.99 V')
        self.assertEqual(t['label'], 'Battery · charging 2.6 W')     # the voltage is the value already

    def test_unknown_when_full_on_usb(self):
        # the 5.4 SQC charger says Unknown once full (mu300-toolkit); a few mA either way is noise
        t = self.render({'present': 1, 'capacity': 100, 'status': 'Unknown', 'volt': 4.34, 'ua': 3000,
                         'w': 0.01, 'usb': 1})
        self.assertEqual(t['label'], 'Battery · full · 4.34 V · USB')

    def test_not_charging_is_not_charging(self):
        t = self.render({'present': 1, 'capacity': 80, 'status': 'Not charging', 'volt': 4.0, 'ua': 5000,
                         'w': 0.02, 'usb': 1})
        self.assertEqual(t['label'], 'Battery · not charging · 4 V · USB')

    def test_discharging_on_usb_without_a_current(self):
        t = self.render({'present': 1, 'capacity': 80, 'status': 'Discharging', 'volt': 4.0, 'usb': 1})
        self.assertEqual(t['label'], 'Battery · not charging · 4 V · USB')


if __name__ == '__main__':
    unittest.main()
