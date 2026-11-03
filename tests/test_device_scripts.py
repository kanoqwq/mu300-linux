"""Small device scripts, against a fake / (MU300_SYSROOT) and stub commands: mu300-device, mu300-lan-ip, mu300-led,
mu300-ttl. They run on Ubuntu (dash, bash) and OpenWrt (busybox ash)."""
import os
import shutil
import time
import unittest

from helpers import BIN, TOP, ShellTest


class Device(ShellTest):
    def root(self, device=None, dt_u30=False):
        r = self.tmp / 'root'
        (r / 'run' / 'mu300').mkdir(parents=True, exist_ok=True)
        shutil.rmtree(r / 'proc/device-tree', ignore_errors=True)
        if device is not None:
            (r / 'run' / 'mu300' / 'device').write_text(device + '\n')
        if dt_u30:
            (r / 'proc' / 'device-tree' / 'charger_policy_service').mkdir(parents=True, exist_ok=True)
        return r

    def test_mu300_device(self):
        cases = [(None, False, 'f50'), (None, True, 'u30air'), ('u30air', False, 'u30air'),
                 ('f50', True, 'u30air')]  # hardware wins over a stale boot label
        for device, dt, want in cases:
            r = self.root(device, dt)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-device', MU300_SYSROOT=r).stdout.strip()
                self.assertEqual(out, want, (device, dt))
            (r / 'run' / 'mu300' / 'device').unlink(missing_ok=True)

    def test_lan_ip(self):
        for device, want in (('f50', '192.168.77.1'), ('v50', '192.168.77.1'), ('u30air', '192.168.77.1'), ('', '192.168.77.1')):
            r = self.root(device)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-lan-ip', MU300_SYSROOT=r, MU300_BIN=BIN).stdout.strip()
                self.assertEqual(out, want, device)

    def test_live_tree_overrides_labels_in_both_directions(self):
        for tree in ('soc/soc:ap-ahb/i2c@2270000/aw9523b_led@5b',
                     'soc/ap-ahb/i2c@2270000/aw9523b_led@5b', ''):
            r=self.root('v50' if not tree else 'f50')
            (r/'proc/device-tree'/tree).mkdir(parents=True)
            for shell in self.each_shell():
                self.assertEqual(self.script(shell,BIN/'mu300-device',MU300_SYSROOT=r).stdout.strip(),
                                 'v50' if tree else 'f50')

    def test_unknown_runtime_label_never_becomes_a_board(self):
        r=self.root('other-board')
        for shell in self.each_shell():
            self.assertEqual(self.script(shell,BIN/'mu300-device',MU300_SYSROOT=r).stdout.strip(),'f50')

    def test_initramfs_and_runtime_agree_despite_a_stale_label(self):
        source=(TOP/'boot/init').read_text()
        fragment=source[source.index('if [ -d /proc/device-tree/charger_policy_service ]; then'):
                        source.index('# its modules only')]
        for tree,want in [('charger_policy_service','u30air'),
                          ('soc/soc:ap-ahb/i2c@2270000/aw9523b_led@5b','v50'),
                          ('soc/ap-ahb/i2c@2270000/aw9523b_led@5b','v50'),('', 'f50')]:
            r=self.root('f50' if want!='f50' else 'v50')
            (r/'proc/device-tree'/tree).mkdir(parents=True)
            (r/'etc').mkdir(exist_ok=True)
            (r/'etc/mu300-device').write_text('f50\n' if want!='f50' else 'v50\n')
            code=fragment.replace('/proc/device-tree',str(r/'proc/device-tree')).replace('/etc/mu300-device',str(r/'etc/mu300-device'))
            for shell in self.each_shell():
                init=self.sh(shell,code+'\nprintf "%s\\n" "$DEVICE"')
                runtime=self.script(shell,BIN/'mu300-device',MU300_SYSROOT=r)
                self.assertEqual(init.returncode,0,init.stderr)
                self.assertEqual(init.stdout.strip(),want)
                self.assertEqual(runtime.stdout.strip(),want)


class Led(ShellTest):
    # the U30 Air's: power is the battery LED's white (the PMIC's green channel), the network LED blue on 4G
    # (net_blue), white on 5G (zte-ldo0), red without service (keyboard-backlight); the Wi-Fi LED's colours are LDOs too
    LEDS = ['sc27xx:blue', 'sc27xx:red', 'sc27xx:green', 'net_blue', 'keyboard-backlight', 'zte-ldo0', 'zte-ldo1', 'zte-ldo2']

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.setUp_leds()
        (self.root / 'run' / 'mu300').mkdir(parents=True)
        (self.root / 'proc').mkdir()
        self.conf = self.tmp / 'led.conf'
        self.uptime(100)
        self.stub('iw', 'cat "$STUBLOG/iw.out" 2>/dev/null')

    def setUp_leds(self):
        for n in self.LEDS:
            d = self.root / 'sys' / 'class' / 'leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n')
            (d / 'max_brightness').write_text('255\n')
            (d / 'trigger').write_text('[none] timer\n')

    def uptime(self, s):
        (self.root / 'proc' / 'uptime').write_text(f'{s}.42 1234.00\n')

    def reset(self):
        for n in self.LEDS:
            (self.root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
        for f in (self.root / 'run/mu300').glob('led/*'):
            f.unlink()
        for f in (self.root / 'run/mu300').glob('led/.awake-until'):
            f.unlink()
        self.conf.unlink(missing_ok=True)

    def state(self):
        return {n: (self.root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in self.LEDS}

    def led(self, shell, device, *args):
        (self.root / 'run/mu300/device').write_text(device + '\n')
        return self.script(shell, BIN / 'mu300-led', *args, MU300_SYSROOT=self.root, MU300_BIN=BIN,
                           MU300_LED_CONF=self.conf)

    def test_u30air(self):
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.led(shell, 'u30air', 'power', 'on').returncode, 0)
            self.assertEqual(self.led(shell, 'u30air', 'data', 'error').returncode, 0)
            s = self.state()
            self.assertEqual((s['sc27xx:green'], s['keyboard-backlight'], s['net_blue']), ('255', '255', '0'))
            self.led(shell, 'u30air', 'data', 'on')
            s = self.state()
            self.assertEqual((s['keyboard-backlight'], s['net_blue']), ('0', '255'))
            self.led(shell, 'u30air', 'data', '5g')
            s = self.state()
            self.assertEqual((s['keyboard-backlight'], s['net_blue'], s['zte-ldo0']), ('0', '0', '255'))
            self.led(shell, 'u30air', 'data', 'on')           # back to 4G
            s = self.state()
            self.assertEqual((s['net_blue'], s['zte-ldo0']), ('255', '0'))
            self.led(shell, 'u30air', 'data', '5g')
            self.led(shell, 'u30air', 'data', 'error')
            s = self.state()
            self.assertEqual((s['keyboard-backlight'], s['net_blue'], s['zte-ldo0']), ('255', '0', '0'))
            self.led(shell, 'u30air', 'data', 'off')
            self.assertEqual(self.state()['net_blue'], '0')
            self.assertEqual(self.state()['sc27xx:blue'], '0')  # the F50's LED is not touched

    def test_wifi_colour_follows_the_band(self):
        for shell in self.each_shell():
            self.reset()
            self.led(shell, 'u30air', 'power', 'on')
            for iw, lit, dark in (('channel 36 (5180 MHz), width: 80 MHz', 'zte-ldo1', 'zte-ldo2'),
                                  ('channel 6 (2437 MHz), width: 20 MHz', 'zte-ldo2', 'zte-ldo1')):
                (self.tmp / 'iw.out').write_text(f'Interface wlan0\n\ttype AP\n\t{iw}\n')
                self.led(shell, 'u30air', 'wifi', 'on')
                s = self.state()
                self.assertEqual((s[lit], s[dark]), ('255', '0'), iw)
            self.led(shell, 'u30air', 'wifi', 'off')
            self.assertEqual((self.state()['zte-ldo2'], self.state()['zte-ldo1']), ('0', '0'))
            # the hotspot just started and has no channel yet: its setting decides
            (self.tmp / 'iw.out').write_text('Interface wlan0\n\ttype AP\n')
            conf = self.tmp / 'hotspot.conf'
            for band, lit in (('2.4', 'zte-ldo2'), ('5', 'zte-ldo1')):
                conf.write_text(f'SSID=x\nPSK=12345678\nBAND={band}\n')
                (self.root / 'run/mu300/device').write_text('u30air\n')
                self.script(shell, BIN / 'mu300-led', 'wifi', 'on', MU300_SYSROOT=self.root, MU300_BIN=BIN,
                            MU300_LED_CONF=self.conf, MU300_HOTSPOT_CONF=conf)
                self.assertEqual(self.state()[lit], '255', band)

    def test_timeout(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.led(shell, 'u30air', 'power', 'on')          # boot: lit for 60 s
            self.led(shell, 'u30air', 'data', 'on')
            self.uptime(150)
            self.led(shell, 'u30air', 'sleep', '--if-due')    # not yet
            self.assertEqual(self.state()['net_blue'], '255')
            self.uptime(161)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual({v for v in self.state().values()}, {'0'})
            # a change while dark is kept, and shown on the next wake
            self.led(shell, 'u30air', 'data', 'error')
            self.assertEqual(self.state()['keyboard-backlight'], '0')
            self.led(shell, 'u30air', 'wake')
            s = self.state()
            self.assertEqual((s['sc27xx:green'], s['keyboard-backlight'], s['net_blue']), ('255', '255', '0'))
            # and the timeout counts from the wake
            self.uptime(161 + 59)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['sc27xx:green'], '255')
            self.uptime(161 + 61)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['sc27xx:green'], '0')

    def test_no_timeout(self):
        for shell in self.each_shell():
            for device, conf in (('u30air', 'LED_TIMEOUT=0\n'), ('f50', '')):
                self.reset()
                if conf:
                    self.conf.write_text(conf)
                self.uptime(100)
                self.led(shell, device, 'power', 'on')
                self.led(shell, device, 'data', 'on')
                self.uptime(100000)
                self.led(shell, device, 'sleep', '--if-due')
                lit = 'net_blue' if device == 'u30air' else 'sc27xx:blue'
                self.assertEqual(self.state()[lit], '255', device)

    def test_f50(self):
        for shell in self.each_shell():
            self.reset()
            for args in (('power', 'on'), ('wifi', 'on'), ('data', 'error'), ('wake',), ('sleep',)):
                # nothing to show on an F50: no error, and no LED of the U30 Air switched
                self.assertEqual(self.led(shell, 'f50', *args).returncode, 0, args)
            self.led(shell, 'f50', 'data', 'on')
            s = self.state()
            self.assertEqual(s['sc27xx:blue'], '255')
            self.assertEqual({v for k, v in s.items() if k != 'sc27xx:blue'}, {'0'})
            self.led(shell, 'f50', 'data', '5g')              # one LED: blue on 5G too
            self.assertEqual(self.state()['sc27xx:blue'], '255')

    def test_stale_siren_stops(self):
        # a siren the pid file does not name (left behind by a restart) stops by itself: two of them flashed
        # together, white lit throughout
        for shell in self.each_shell():
            self.reset()
            pid = self.root / 'run/mu300/led-siren.pid'
            self.led(shell, 'u30air', 'alarm', 'on')
            p = int(pid.read_text())
            pid.write_text('1\n')                                 # someone else's now
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    os.kill(p, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(p, 15)
                self.fail('the stale siren kept running')
            pid.unlink()

    def test_vendor_ldo_switches_on_5_4(self):
        # the 5.4 kernel has no zte-ldoN LEDs: ZTE's zte_ldo_leds switches the same LDOs through vddcamaN_status
        for shell in self.each_shell():
            self.reset()
            for n in ('zte-ldo0', 'zte-ldo1', 'zte-ldo2'):
                shutil.rmtree(self.root / 'sys/class/leds' / n)
            vendor = self.root / 'sys/devices/platform/zte_ldo_leds'
            vendor.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                (vendor / f'vddcama{i}_status').write_text('0\n')
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'data', '5g')
            (self.tmp / 'iw.out').write_text('Interface wlan0\n\ttype AP\n\tchannel 36 (5180 MHz)\n')
            self.led(shell, 'u30air', 'wifi', 'on')
            self.assertEqual([(vendor / f'vddcama{i}_status').read_text().strip() for i in range(3)], ['1', '1', '0'])
            self.led(shell, 'u30air', 'sleep')
            self.assertEqual([(vendor / f'vddcama{i}_status').read_text().strip() for i in range(3)], ['0', '0', '0'])
            shutil.rmtree(self.root / 'sys/devices')
            self.setUp_leds()

    def test_alarm_siren(self):
        # too hot: red and blue take turns on the PMIC's LED until the alarm is off, then the LEDs are as before
        for shell in self.each_shell():
            for device in ('u30air', 'f50'):
                self.reset()
                self.conf.write_text('LED_TIMEOUT=0\n')
                self.led(shell, device, 'power', 'on')
                self.led(shell, device, 'data', 'on')
                pid = self.root / 'run/mu300/led-siren.pid'
                try:
                    self.assertEqual(self.led(shell, device, 'alarm', 'on').returncode, 0)
                    self.assertTrue(pid.exists())
                    self.led(shell, device, 'alarm', 'on')        # a second one starts no second siren
                    # one colour at a time: white (the U30 Air's green channel) never mixed in
                    want = ({('255', '0', '0'), ('0', '255', '0'), ('0', '0', '255')} if device == 'u30air'
                            else {('255', '0', '0'), ('0', '0', '255')})
                    seen = set()
                    deadline = time.time() + 4
                    while time.time() < deadline and not want <= seen:
                        s = self.state()
                        seen.add((s['sc27xx:red'], s['sc27xx:green'], s['sc27xx:blue']))
                        time.sleep(0.03)
                    self.assertLessEqual(want, seen, device)
                    if device == 'f50':
                        self.assertEqual({g for _, g, _ in seen}, {'0'})    # the F50's green is green
                finally:
                    self.led(shell, device, 'alarm', 'off')
                self.assertFalse(pid.exists())
                time.sleep(0.4)                                   # a killed siren writes no more
                s = self.state()
                self.assertEqual(s['sc27xx:red'], '0')
                # the F50's blue is its data LED: back on; the U30 Air's white is its power LED
                self.assertEqual(s['sc27xx:blue'], '255' if device == 'f50' else '0', device)
                self.assertEqual(s['sc27xx:green'], '255' if device == 'u30air' else '0', device)
                self.assertEqual(self.led(shell, device, 'alarm', 'off').returncode, 0)   # off twice

    def test_missing_leds_and_bad_usage(self):
        for shell in self.each_shell():
            empty = self.tmp / 'empty'
            (empty / 'run/mu300').mkdir(parents=True, exist_ok=True)
            (empty / 'run/mu300/device').write_text('u30air\n')
            r = self.script(shell, BIN / 'mu300-led', 'data', 'on', MU300_SYSROOT=empty, MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0)  # an LED the device has not is skipped
            self.assertEqual(self.led(shell, 'u30air', 'blink').returncode, 2)


class WifiBand(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'hotspot.conf'

    def band(self, shell, *args):
        return self.script(shell, BIN / 'mu300-wifi-band', *args, MU300_HOTSPOT_CONF=self.conf)

    def test_toggle(self):
        for shell in self.each_shell():
            self.conf.write_text("SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=5\nCHANNEL=40\nCOUNTRY=TR\n")
            self.conf.chmod(0o600)
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            r = self.band(shell, 'toggle')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'hotspot: 2.4 GHz'), r.stderr)
            self.assertEqual(self.conf.read_text(),
                             "SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=2.4\nCHANNEL=auto\nCOUNTRY=TR\n")
            self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
            self.band(shell, 'toggle')
            self.assertIn('BAND=5\n', self.conf.read_text())
            self.band(shell, '2.4')
            self.band(shell, '2.4')
            self.assertEqual(self.conf.read_text().count('BAND='), 1)

    def test_missing_keys_and_file(self):
        for shell in self.each_shell():
            self.conf.write_text('SSID=x\nPSK=12345678\n')   # an old file without BAND/CHANNEL: 5 GHz
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            self.band(shell, 'toggle')
            self.assertEqual(self.conf.read_text(), 'SSID=x\nPSK=12345678\nBAND=2.4\nCHANNEL=auto\n')
            self.conf.unlink()
            self.assertEqual(self.band(shell, '5').returncode, 1)
            self.assertEqual(self.band(shell, '6').returncode, 2)


class Buttons(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('mu300-device', 'echo "${TEST_BUTTON_DEVICE:-f50}"')

    def test_actions(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'systemctl', 'logger', 'poweroff'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; [ "{name} $*" != "systemctl is-active --quiet mu300-hotspot" ]')
        cases = [('116 short', ['mu300-led wake']),
                 ('138 short', ['mu300-led wake', 'mu300-wifi-band toggle']),
                 ('138 long', ['mu300-led wake', 'systemctl is-active --quiet mu300-hotspot', 'systemctl start mu300-hotspot']),
                 ('0 tick', [getattr(self,'LED_TICK','mu300-led sleep --if-due')]),
                 ('116 long', ['mu300-led wake', 'systemctl poweroff']),
                 ('115 short', [])]
        for shell in self.each_shell():
            for event, want in cases:
                (self.tmp / 'keys.in').write_text(event + '\n')
                (self.tmp / 'calls').unlink(missing_ok=True)
                r = self.script(shell, BIN / 'mu300-buttons')
                self.assertEqual(r.returncode, 0, r.stderr)
                calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
                self.assertEqual([c for c in calls if not c.startswith('logger')], want, event)

    def test_u30air_band_requires_two_short_presses(self):
        for name in ('mu300-led', 'mu300-wifi-band', 'systemctl', 'logger', 'poweroff'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; [ "{name} $*" != "systemctl is-active --quiet mu300-hotspot" ]')
        self.stub('date', 'echo "unexpected wall-clock read" >&2; exit 99')
        cases = [
            ('echo "138 short"', 0, 0),
            ('echo "138 short"; sleep 0.08; echo "138 short"', 1, 0),
            ('echo "138 short"; sleep 0.65; echo "138 short"', 0, 0),
            ('echo "138 short"; sleep 0.08; echo "138 short"; sleep 0.08; echo "138 short"', 1, 0),
            ('echo "138 short"; echo "138 long"; echo "138 short"', 0, 1),
            ('echo "138 short"; echo "116 short"; echo "138 short"', 0, 0),
        ]
        for shell in self.each_shell():
            for events, toggles, hotspot in cases:
                with self.subTest(shell=shell, events=events):
                    self.stub('mu300-keys', events)
                    (self.tmp/'calls').write_text('')
                    result = self.script(shell, BIN/'mu300-buttons', TEST_BUTTON_DEVICE='u30air')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = (self.tmp/'calls').read_text().splitlines()
                    self.assertEqual(calls.count('mu300-wifi-band toggle'), toggles, calls)
                    self.assertEqual(calls.count('systemctl start mu300-hotspot'), hotspot, calls)


class ThermalGuard(ShellTest):
    """thermal-guard's LED alarm (one round against a fake /: MU300_SYSROOT)."""
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        led = self.bin / 'mu300-led'
        led.write_text('#!/bin/sh\necho "mu300-led $*" >> "$STUBLOG/calls"\n')
        led.chmod(0o755)
        shutil.copy(BIN / 'mu300-device', self.bin / 'mu300-device')
        self.device('u30air')
        self.stub('logger', ':')
        self.stub('poweroff', 'echo poweroff >> "$STUBLOG/calls"')
        # BusyBox standalone ash can bypass PATH stubs for its built-in applets.
        # Never invoke the host's poweroff/logger while testing a fake sysroot.
        code = (BIN / 'thermal-guard').read_text()
        self.assertIn('\n        poweroff\n', code)
        self.assertIn('logger -t ', code)
        code = code.replace('logger -t ', str(self.stubs / 'logger') + ' -t ')
        code = code.replace('\n        poweroff\n', '\n        ' + str(self.stubs / 'poweroff') + '\n')
        self.guard = self.tmp / 'thermal-guard'
        self.guard.write_text(code)

    def device(self, name):
        (self.root / 'run/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'run/mu300/device').write_text(name + '\n')

    def round(self, shell, soc, battery=None, trips=False):
        z = self.root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'temp').write_text(f'{soc}\n')
        if trips:
            (z / 'trip_point_0_temp').write_text('85000\n')
        if battery is not None:
            b = self.root / 'sys/class/power_supply/battery'
            b.mkdir(parents=True, exist_ok=True)
            (b / 'type').write_text('Battery\n')
            (b / 'temp').write_text(f'{battery}\n')
        (self.tmp / 'calls').unlink(missing_ok=True)
        r = self.script(shell, self.guard, MU300_SYSROOT=self.root, MU300_BIN=self.bin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []

    def test_alarm_on_and_off(self):
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            self.assertEqual(self.round(shell, 60000, 300), [])
            self.assertEqual(self.round(shell, 86000, 300), ['mu300-led alarm on'])
            self.assertEqual(self.round(shell, 90000, 300), [])                # on already
            self.assertEqual(self.round(shell, 80000, 300), [])                # not cool enough yet
            self.assertEqual(self.round(shell, 74000, 300), ['mu300-led alarm off'])
            self.assertEqual(self.round(shell, 60000, 510), ['mu300-led alarm on'])   # the battery alone
            self.assertEqual(self.round(shell, 60000, 460), [])
            self.assertEqual(self.round(shell, 60000, 440), ['mu300-led alarm off'])

    def test_f50_battery_is_ignored(self):
        # the F50 has no battery, but mainline's fuel gauge reports one at 75.0 C from an open NTC input: the
        # alarm went on at every boot and never off (v2026.10.06)
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('f50')
            self.assertEqual(self.round(shell, 46000, 750), [])
            self.assertEqual(self.round(shell, 86000, 750), ['mu300-led alarm on'])    # the SoC still counts
            self.assertEqual(self.round(shell, 74000, 750), ['mu300-led alarm off'])

    def test_vendor_kernel_only_watches(self):
        # 5.4 has its own trip points: no throttling or power-off here, but the alarm still works
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            self.assertEqual(self.round(shell, 110000, trips=True), ['mu300-led alarm on'])
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            self.assertEqual(self.round(shell, 110000), ['poweroff', 'mu300-led alarm on'])   # mainline: critical

    def test_guard_recovery_keeps_user_ceiling_and_external_changes(self):
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('f50')
            p=self.root/'sys/devices/system/cpu/cpufreq/policy0'
            p.mkdir(parents=True)
            (p/'scaling_available_frequencies').write_text('400000 800000 1200000\n')
            (p/'scaling_max_freq').write_text('800000\n')
            (p/'cpuinfo_max_freq').write_text('1200000\n')
            self.round(shell,86000)
            self.assertEqual((p/'scaling_max_freq').read_text().strip(),'400000')
            self.round(shell,40000)
            self.assertEqual((p/'scaling_max_freq').read_text().strip(),'800000')
            self.round(shell,86000)
            (p/'scaling_max_freq').write_text('1200000\n')
            self.round(shell,40000)
            self.assertEqual((p/'scaling_max_freq').read_text().strip(),'1200000')

    def test_mainline_native_cooling_keeps_emergency_and_user_caps(self):
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('f50')
            (self.root / 'sys/module/mu300_thermal').mkdir(parents=True)
            z = self.root / 'sys/class/thermal/thermal_zone0'
            z.mkdir(parents=True)
            (z / 'type').write_text('soc-thmzone\n')
            (z / 'mode').write_text('enabled\n')
            (z / 'trip_point_0_type').write_text('passive\n')
            c = z.parent / 'cooling_device0'
            c.mkdir()
            (c / 'type').write_text('cpufreq-cpu0\n')
            (z / 'cdev0').symlink_to(c)
            p = self.root / 'sys/devices/system/cpu/cpufreq/policy0'
            p.mkdir(parents=True)
            (p / 'scaling_available_frequencies').write_text('400000 800000 1200000\n')
            (p / 'scaling_max_freq').write_text('800000\n')
            (p / 'cpuinfo_max_freq').write_text('1200000\n')
            self.round(shell, 40000, trips=True)
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '800000')
            self.round(shell, 86000, trips=True)
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '800000')
            self.assertIn('poweroff', self.round(shell, 106000, trips=True))
            # A disabled zone or missing CPU binding must restore fallback.
            (z / 'cdev0').unlink()
            self.round(shell, 86000, trips=True)
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '400000')


class Nfc(ShellTest):
    """mu300-nfc against a fake FM11NT08: 1 KiB behind a stub i2ctransfer, which answers only while a stub gpioset
    holds line 190 low, as the tag does."""
    EEPROM = r'''
import os, sys
mem = os.path.join(os.environ['STUBLOG'], 'tag.bin')
data = bytearray(open(mem, 'rb').read())
args = [a for a in sys.argv[1:] if a != '-y']
bus, op = args[0], args[1]
if not os.path.exists(os.path.join(os.environ['STUBLOG'], 'held')) or bus != '2' or not op.endswith('@0x57'):
    sys.exit('Error: Sending messages failed: Remote I/O error')
n = int(op[1:op.index('@')])
addr = int(args[2], 16) << 8 | int(args[3], 16)
if op.startswith('w') and len(args) > 4 and args[4].startswith('r'):
    print(' '.join('0x%02x' % b for b in data[addr:addr + int(args[4][1:])]))
elif op.startswith('w'):
    body = [int(x, 16) for x in args[4:]]
    # NDEF pages of 16 bytes; the configuration byte on its own
    assert len(body) == n - 2 and (len(body) <= 16 and addr % 16 == 0 and addr >= 16 or (addr, len(body)) == (0x3bf, 1)), args
    data[addr:addr + len(body)] = bytes(body)
    open(mem, 'wb').write(bytes(data))
    open(os.path.join(os.environ['STUBLOG'], 'writes'), 'a').write('%x\\n' % addr)
'''
    # what ZTE's firmware leaves there: UID, capability container (NDEF, 872 bytes), a Wi-Fi record
    ZTE = bytes.fromhex('1df4bcdd99030012 88b20000e1106d00'.replace(' ', ''))

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        chip = self.tmp / 'devices/platform/soc/64170000.gpio/gpiochip4'
        chip.mkdir(parents=True)
        (self.root / 'sys/bus/gpio/devices').mkdir(parents=True)
        (self.root / 'sys/bus/gpio/devices/gpiochip4').symlink_to(chip)
        (self.tmp / 'eeprom.py').write_text(self.EEPROM)
        self.stub('i2ctransfer', 'exec python3 "$STUBLOG/eeprom.py" "$@"')
        # libgpiod 2: holds the line until killed
        self.stub('gpioset', '[ "$1" = --help ] && { echo "  -c, --chip"; exit 0; }; '
                             '[ "$*" = "-c gpiochip4 ${TEST_NFC_PIN:-190}=0" ] || exit 1; touch "$STUBLOG/held"; '
                             'trap \'rm -f "$STUBLOG/held"; exit 0\' TERM; while :; do sleep 0.05; done')
        self.conf = self.tmp / 'hotspot.conf'
        self.conf.write_text('SSID=U30 AIR\nPSK=secret pass!\nBAND=5\n')

    def blank(self, ndef=b'\x03\x00\xfe'):
        (self.tmp / 'tag.bin').write_bytes(self.ZTE + ndef + bytes(1024 - 16 - len(ndef)))
        (self.tmp / 'writes').unlink(missing_ok=True)

    def nfc(self, shell, *args, device='u30air'):
        (self.root / 'run/mu300/device').write_text(device + '\n')
        return self.script(shell, BIN / 'mu300-nfc', *args, MU300_SYSROOT=self.root, MU300_BIN=BIN,
                           MU300_HOTSPOT_CONF=self.conf, TEST_NFC_PIN='189' if device=='v50' else '190')

    def writes(self):
        w = self.tmp / 'writes'
        return w.read_text().split() if w.exists() else []

    def test_wifi_record_is_zte_s(self):
        # byte for byte what the stock firmware writes (WSC credential, WPA2-PSK, AES)
        for shell in self.each_shell():
            self.blank()
            r = self.nfc(shell, 'wifi')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'wifi: U30 AIR (with its password)'), r.stderr)
            self.assertNotIn('secret', r.stdout + r.stderr)
            ssid, key = b'U30 AIR', b'secret pass!'
            cred = (b'\x10\x45\x00' + bytes([len(ssid)]) + ssid + b'\x10\x03\x00\x02\x00\x20\x10\x0f\x00\x02\x00\x08'
                    + b'\x10\x27\x00' + bytes([len(key)]) + key + b'\x10\x20\x00\x06' + bytes(6)
                    + b'\x10\x49\x00\x06\x00\x37\x2a\x00\x01\x20')
            payload = b'\x10\x0e\x00' + bytes([len(cred)]) + cred
            rec = b'\xd2\x17' + bytes([len(payload)]) + b'application/vnd.wfa.wsc' + payload
            want = b'\x03' + bytes([len(rec)]) + rec + b'\xfe'
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(data[16:16 + len(want)], want)
            self.assertEqual(data[:16], self.ZTE)                     # UID and capability container untouched
            self.assertFalse((self.tmp / 'held').exists())            # the line is let go
            # again: nothing changed, nothing written
            (self.tmp / 'writes').unlink()
            self.assertEqual(self.nfc(shell, 'wifi').returncode, 0)
            self.assertEqual(self.writes(), [])

    def test_url_text_clear(self):
        for shell in self.each_shell():
            self.blank()
            for args, shown in ((('url', 'https://github.com/dikeckaan/mu300-linux'),
                                 'url: https://github.com/dikeckaan/mu300-linux'),
                                (('url', 'tel:+905551112233'), 'url: tel:+905551112233'),
                                (('url', 'geo:41.0,29.0'), 'url: geo:41.0,29.0'),
                                (('text', 'Merhaba, şifre kutunun altında'), 'text: Merhaba, şifre kutunun altında'),
                                (('clear',), 'empty')):
                r = self.nfc(shell, *args)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, shown), r.stderr)
                self.assertEqual(self.nfc(shell).stdout.strip(), shown)
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(data[16:19], b'\x03\x00\xfe')

    def test_long_url(self):
        # over 255 bytes: a long record and a three-byte TLV length
        for shell in self.each_shell():
            self.blank()
            url = 'https://example.com/' + 'x' * 400
            r = self.nfc(shell, 'url', url)
            self.assertEqual(r.stdout.strip(), 'url: ' + url, r.stderr)
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual((data[16], data[17]), (0x03, 0xff))
            r = self.nfc(shell, 'url', 'https://example.com/' + 'x' * 900)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('too long', r.stderr)

    def test_sync_keeps_what_the_user_wrote(self):
        for shell in self.each_shell():
            self.blank()
            self.conf.write_text('SSID=U30 AIR\nPSK=secret pass!\nBAND=5\n')
            self.assertEqual(self.nfc(shell, 'sync').stdout.strip(), 'wifi: U30 AIR (with its password)')
            self.conf.write_text('SSID=Ev\nPSK=another one\n')
            self.assertEqual(self.nfc(shell, 'sync').stdout.strip(), 'wifi: Ev (with its password)')
            self.nfc(shell, 'url', 'https://example.com/')
            (self.tmp / 'writes').unlink()
            r = self.nfc(shell, 'sync')
            self.assertIn('left as it is', r.stdout)
            self.assertEqual(self.writes(), [])
            self.assertEqual(self.nfc(shell).stdout.strip(), 'url: https://example.com/')

    def test_on_off(self):
        # ZTE's NFC switch: bit 5 of the configuration byte 0x3bf (set: phones get nothing); nothing else changes
        for shell in self.each_shell():
            self.blank()
            data = bytearray((self.tmp / 'tag.bin').read_bytes())
            data[0x3b0:0x3c0] = bytes.fromhex('0578f057806002 1e0000009e44000420'.replace(' ', ''))
            (self.tmp / 'tag.bin').write_bytes(bytes(data))
            r = self.nfc(shell)
            self.assertIn('NFC is off', r.stdout)
            r = self.nfc(shell, 'on')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'NFC on'), r.stderr)
            after = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(after[0x3b0:0x3c0], bytes.fromhex('0578f0578060021e0000009e44000400'))
            self.assertEqual(after[:0x3bf], bytes(data[:0x3bf]))
            self.assertNotIn('NFC is off', self.nfc(shell).stdout)
            self.assertEqual(self.nfc(shell, 'off').stdout.strip(), 'NFC off')
            self.assertEqual((self.tmp / 'tag.bin').read_bytes()[0x3bf], 0x20)

    def test_state_is_read_only_and_sync_preserves_disabled_rf(self):
        for shell in self.each_shell():
            self.blank()
            self.assertEqual(self.nfc(shell, 'off').returncode, 0)
            before=(self.tmp/'tag.bin').read_bytes(); writes=self.writes()
            r=self.nfc(shell, 'state')
            self.assertEqual((r.returncode,r.stdout.strip()), (0,'0'),r.stderr)
            self.assertEqual((self.tmp/'tag.bin').read_bytes(),before)
            self.assertEqual(self.writes(),writes)
            self.assertEqual(self.nfc(shell,'sync').returncode,0)
            self.assertEqual(self.nfc(shell,'state').stdout.strip(),'0')
            self.assertEqual(self.nfc(shell,'on').returncode,0)
            self.assertEqual(self.nfc(shell,'state').stdout.strip(),'1')

    def test_failed_state_read_does_not_become_disabled_success(self):
        self.blank()
        self.stub('i2ctransfer','exit 1')
        for shell in self.each_shell():
            r=self.nfc(shell,'state')
            self.assertNotEqual(r.returncode,0,r.stderr)
            self.assertNotEqual(r.stdout.strip(),'0')
            self.assertFalse(self.writes())

    def test_refusals(self):
        for shell in self.each_shell():
            self.blank()
            r = self.nfc(shell, 'wifi', device='f50')
            self.assertEqual(r.returncode, 1)
            self.assertIn('no supported NFC tag', r.stderr)
            self.assertEqual(self.nfc(shell, 'blink').returncode, 2)
            self.assertEqual(self.nfc(shell, 'url').returncode, 1)
            # no capability container: not written
            (self.tmp / 'tag.bin').write_bytes(bytes(1024))
            r = self.nfc(shell, 'url', 'https://example.com/')
            self.assertEqual(r.returncode, 1)
            self.assertIn('capability container', r.stderr)
            self.assertEqual(self.writes(), [])

    def test_v50_uses_its_stock_reset_pin_and_read_only_discovery(self):
        (self.root/'proc/device-tree/soc/ap-ahb/i2c@2270000/aw9523b_led@5b').mkdir(parents=True)
        node=self.root/'proc/device-tree/ntag_fm11_gpio';node.mkdir(parents=True)
        (node/'ntag-reset-gpio').write_bytes(bytes.fromhex('00000018000000bd00000000'))
        for shell in self.each_shell():
            self.blank()
            r=self.nfc(shell,'supported',device='v50')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse((self.tmp/'held').exists());self.assertEqual(self.writes(),[])
            r=self.nfc(shell,'wifi',device='v50')
            self.assertEqual((r.returncode,r.stdout.strip()),(0,'wifi: U30 AIR (with its password)'),r.stderr)
            self.assertFalse((self.tmp/'held').exists())

    def test_v50_unknown_reset_binding_is_rejected_without_touching_the_tag(self):
        node=self.root/'proc/device-tree/ntag_fm11_gpio';node.mkdir(parents=True)
        prop=node/'ntag-reset-gpio'
        for value in (None,bytes(8),bytes.fromhex('00000018000000be00000000')):
            prop.unlink(missing_ok=True)
            if value is not None:prop.write_bytes(value)
            for shell in self.each_shell():
                self.blank()
                r=self.nfc(shell,'wifi',device='v50')
                self.assertEqual(r.returncode,1,r.stderr)
                self.assertFalse((self.tmp/'held').exists());self.assertEqual(self.writes(),[])


class Ttl(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')
        # nft records its arguments, and the ruleset it is given on stdin
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = -f ] && cat >> "$STUBLOG/nft.in"; '
                         '[ "$1" = list ] && exit 1; exit 0')
        self.entry = self.tmp / 'mu300-ttl'
        code = (BIN / 'mu300-ttl').read_text()
        self.assertIn('$(id -u)', code)
        code = code.replace('$(id -u)', '$(' + str(self.stubs / 'id') + ' -u)')
        self.entry.write_text(code)

    def ttl(self, shell, *args):
        return self.script(shell, self.entry, *args, MU300_TTL_CONF=self.conf)

    def reset(self):
        for f in ('nft.args', 'nft.in'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)

    def test_set(self):
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'TTL=64\n')
            rules = (self.tmp / 'nft.in').read_text()
            self.assertIn('table inet mu300_ttl', rules)
            self.assertIn('oifname "sipa_eth*" ip ttl set 64', rules)
            self.assertIn('oifname "sipa_eth*" ip6 hoplimit set 64', rules)
            self.assertIn('fixed at 64', r.stdout)

    def test_invalid_values_change_nothing(self):
        for shell in self.each_shell():
            for v in ('0', '256', 'abc', '-1', '6 4', ''):
                self.reset()
                r = self.ttl(shell, 'set', v)
                self.assertEqual(r.returncode, 2, v)
                self.assertFalse(self.conf.exists(), v)
                self.assertFalse((self.tmp / 'nft.in').exists(), v)

    def test_off_and_apply(self):
        for shell in self.each_shell():
            self.reset()
            self.ttl(shell, 'set', '128')
            (self.tmp / 'nft.in').unlink()
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertFalse(self.conf.exists())
            self.assertIn('delete table inet mu300_ttl', (self.tmp / 'nft.args').read_text())
            self.assertFalse((self.tmp / 'nft.in').exists())  # no rule without a setting
            self.assertIn('not changed', r.stdout)
            # a hand-edited file with junk sets nothing
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=999\n')
            self.ttl(shell, 'apply')
            self.assertFalse((self.tmp / 'nft.in').exists())

    def test_not_root(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.ttl(shell).returncode, 0)  # status works without root



class Usb(ShellTest):
    """mu300-usb against a fake charger (busybox i2cget/i2cset on files) and a fake role switch."""

    def setUp(self):
        super().setUp()
        r = self.root = self.tmp / 'root'
        (r / 'run/mu300').mkdir(parents=True)
        (r / 'run/mu300/device').write_text('u30air\n')
        node = r / 'sys/firmware/devicetree/base/soc/i2c@22a0000'
        node.mkdir(parents=True)
        i2c = r / 'sys/bus/i2c/devices/i2c-6'
        i2c.mkdir(parents=True)
        (i2c / 'of_node').symlink_to(node)
        role = r / 'sys/class/usb_role/25100000.dwc3-role-switch'
        role.mkdir(parents=True)
        (role / 'role').write_text('device\n')
        self.stub('id', 'echo 0')
        self.stub('sleep', ':')
        # i2c-tools (which mu300-usb prefers to busybox's): i2cget -y BUS ADDR REG / i2cset -y BUS ADDR REG VALUE,
        # one file per register. (Not a stub called busybox: that would also replace the "busybox sh" under test.)
        self.stub('i2cget', '[ "$1" = -y ] && shift; cat "$STUBLOG/reg-$3" 2>/dev/null || echo 0x00')
        self.stub('i2cset', '[ "$1" = -y ] && shift; printf "0x%02x\\n" $(( $4 )) > "$STUBLOG/reg-$3"; '
                            'echo "$3=$(( $4 ))" >> "$STUBLOG/writes"')
        self.entry = self.tmp / 'mu300-usb'
        code = (BIN / 'mu300-usb').read_text()
        self.assertIn('$(id -u)', code)
        self.assertIn('"i2c$op"', code)
        code = code.replace('$(id -u)', '$(' + str(self.stubs / 'id') + ' -u)')
        code = code.replace('"i2c$op"', '"' + str(self.stubs) + '/i2c$op"')
        self.entry.write_text(code)

    def regs(self, **values):
        for k, v in values.items():
            (self.tmp / f'reg-0x{k[1:]}').write_text(v + '\n')

    def reg(self, r):
        return int((self.tmp / f'reg-{r}').read_text(), 16)

    def usb(self, shell, *args):
        return self.script(shell, self.entry, *args, MU300_SYSROOT=self.root,
                           PATH=f'{self.stubs}:{BIN}:/usr/bin:/bin')

    def role(self):
        return (self.root / 'sys/class/usb_role/25100000.dwc3-role-switch/role').read_text().strip()

    def test_host_and_back(self):
        for shell in self.each_shell():
            self.regs(r01='0x1a', r05='0x9f', r08='0x00')   # no outside supply
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.role(), 'host')
            self.assertEqual(self.reg('0x01'), 0x1a | 0x20)    # OTG_CONFIG on, the rest untouched
            self.assertEqual(self.reg('0x05'), 0x9f & ~0x30)   # watchdog off
            r = self.usb(shell, 'device')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.role(), 'device')
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertEqual(self.reg('0x05'), (0x9f & ~0x30) | 0x10)

    def test_powered_hub_uses_external_supply_without_boost(self):
        for shell in self.each_shell():
            self.regs(r01='0x1a', r05='0x9f', r08='0x24')   # power good, a USB host drives VBUS
            (self.tmp / 'writes').unlink(missing_ok=True)
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.reg('0x01') & 0x20, 0)
            self.assertEqual(self.role(), 'host')

    def test_boot_switches_a_leftover_boost_off(self):
        for shell in self.each_shell():
            self.regs(r01='0x3a', r05='0x8f')
            r = self.usb(shell, 'boot')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertIn('switched off', r.stdout)
            # nothing to do, nothing written
            (self.tmp / 'writes').unlink(missing_ok=True)
            self.assertEqual(self.usb(shell, 'boot').stdout, '')
            self.assertFalse((self.tmp / 'writes').exists())

    def test_f50(self):
        for shell in self.each_shell():
            (self.root / 'run/mu300/device').write_text('f50\n')
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 1)
            self.assertIn('only the U30 Air', r.stderr)
            self.assertEqual(self.usb(shell, 'boot').returncode, 0)   # boot is quiet on every device

if __name__ == '__main__':
    unittest.main()
