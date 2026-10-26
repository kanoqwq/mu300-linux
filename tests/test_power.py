"""mu300-power: profiles, idle radios and the charging boot, against a fake / (MU300_SYSROOT), a fake /run and
stub commands. The spec is docs/superpowers/specs/2026-10-06-power-profiles-design.md."""
import os
import unittest

from helpers import ShellTest, BIN, TOP

POWER = BIN / 'mu300-power'


class PowerTest(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.run_dir = self.tmp / 'run'
        (self.run_dir / 'mu300').mkdir(parents=True)
        self.conf = self.tmp / 'power.conf'
        for name in ('iw', 'wifi', 'systemctl', 'mobile-data', 'mu300-led', 'logger', 'poweroff'):
            self.stub(name, 'echo "$(basename "$0") $*" >> "$STUBLOG/calls"')
        self.stub('iw', 'echo "iw $*" >> "$STUBLOG/calls"; cat "$STUBLOG/stations" 2>/dev/null')
        self.stub('mu300-device', 'echo u30air')
        # The stubs keep the state a wake looks at (mu300-power restores by inspection): the AP ($STUBLOG/ap-up: wifi
        # and the hotspot unit, read by ubus and systemctl is-active), the VPN unit ($STUBLOG/vpn-up), the modem's
        # mode file (mobile-data suspend/resume) and mu300-led's idle flag.
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"\n'
                          'case $1 in down) rm -f "$STUBLOG/ap-up" ;; up) : > "$STUBLOG/ap-up" ;; esac')
        self.stub('ubus', '[ -e "$STUBLOG/ap-up" ]')
        self.stub('systemctl', self.SYSTEMCTL)
        self.stub('mobile-data', 'echo "mobile-data $*" >> "$STUBLOG/calls"\n'
                                 'case $1 in suspend) echo "$2" > "$MU300_RUN/mu300-mobile-data.suspend" ;;\n'
                                 '    resume) rm -f "$MU300_RUN/mu300-mobile-data.suspend" ;; esac')
        self.stub('mu300-led', 'echo "mu300-led $*" >> "$STUBLOG/calls"\n'
                               'case "$1 ${2:-}" in "idle on") mkdir -p "$MU300_SYSROOT/run/mu300/led"; : > "$MU300_SYSROOT/run/mu300/led/.idle" ;;\n'
                               '    "idle off") rm -f "$MU300_SYSROOT/run/mu300/led/.idle" ;; esac')
        (self.tmp / 'ap-up').touch()

    SYSTEMCTL = ('echo "systemctl $*" >> "$STUBLOG/calls"\n'
                 'case "$*" in\n'
                 '    "stop mu300-hotspot") rm -f "$STUBLOG/ap-up" ;;\n'
                 '    "start mu300-hotspot") : > "$STUBLOG/ap-up" ;;\n'
                 '    "is-active --quiet mu300-hotspot") [ -e "$STUBLOG/ap-up" ]; exit ;;\n'
                 '    "is-active --quiet mu300-vpn") [ -e "$STUBLOG/vpn-up" ]; exit ;;\n'
                 '    "stop mu300-vpn") rm -f "$STUBLOG/vpn-up" ;;\n'
                 '    "start mu300-vpn") : > "$STUBLOG/vpn-up" ;;\n'
                 'esac')

    # --- the fake device
    def psy(self, name, **files):
        d = self.root / 'sys/class/power_supply' / name
        d.mkdir(parents=True, exist_ok=True)
        for k, v in files.items():
            (d / k).write_text(f'{v}\n')
        return d

    def battery(self, capacity=64, temp=281, status='Discharging', current=-470000):
        return self.psy('sc27xx-fgu', type='Battery', present=1, capacity=capacity, temp=temp, status=status,
                        voltage_now=3850000, current_now=current)

    def charger(self, online=1, usb_type='SDP', charge_type='Fast'):
        return self.psy('bq256xx-charger', type='USB', online=online, usb_type=usb_type, charge_type=charge_type)

    def write_conf(self, text):
        self.conf.write_text(text)

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def power(self, shell, args, **env):
        return self.sh(shell, f'"{POWER}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_POWER_CONF=self.conf, MU300_POWER_INTERVAL=0, **env)


class Config(PowerTest):
    def test_defaults_without_a_file(self):
        self.battery()
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('profile: battery (auto)', r.stdout)
            self.assertIn('battery: WIFI_IDLE=10 RADIO_IDLE=off LEDS_IDLE=off CPU=full', r.stdout)
            self.assertIn('plugged: WIFI_IDLE=0 RADIO_IDLE=keep LEDS_IDLE=on CPU=full', r.stdout)
            self.assertIn('saver: WIFI_IDLE=5 RADIO_IDLE=off LEDS_IDLE=off CPU=eco', r.stdout)

    def test_set_rewrites_one_key_and_validates(self):
        for shell in self.each_shell():
            self.write_conf('PROFILE=auto\nbattery_WIFI_IDLE=10\n')
            r = self.power(shell, 'set battery.WIFI_IDLE 15')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\n')
            r = self.power(shell, 'set saver.CPU eco')
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\nsaver_CPU=eco\n')
            r = self.power(shell, 'set battery.RADIO_IDLE sometimes')
            self.assertEqual(r.returncode, 2)
            self.assertIn('RADIO_IDLE', r.stderr)
            self.assertNotIn('sometimes', self.conf.read_text())
            r = self.power(shell, 'set battery.COLOUR red')
            self.assertEqual(r.returncode, 2)
            r = self.power(shell, 'set CHARGE_TO 80')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('CHARGE_TO=80\n', self.conf.read_text())
            r = self.power(shell, 'set CHARGE_TO 90')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('CHARGE_TO=90\n', self.conf.read_text())
            for bad in ('101', '080', '-1', '8a', '1e2', ' 5', '$(id)', ''):
                r = self.power(shell, f"set CHARGE_TO '{bad}'")
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn('CHARGE_TO=90\n', self.conf.read_text())

    def test_bad_value_is_default_and_reported(self):
        self.battery()
        for shell in self.each_shell():
            self.write_conf('battery_WIFI_IDLE=ten\nSAVER_BELOW=abc\n')
            r = self.power(shell, 'status')
            self.assertIn('battery: WIFI_IDLE=10', r.stdout)
            self.assertIn('ignored in power.conf: SAVER_BELOW battery_WIFI_IDLE', r.stdout)

    def test_numbers_with_a_leading_zero_are_ignored(self):
        """dash reads 08 as an illegal (octal) number; 00 would idle at once. Both count as the default."""
        self.battery()
        for shell in self.each_shell():
            for bad in ('08', '00', '010', '12345'):
                self.write_conf(f'battery_WIFI_IDLE={bad}\nSAVER_BELOW=05\n')
                r = self.power(shell, 'status')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('battery: WIFI_IDLE=10', r.stdout, bad)
                self.assertIn('SAVER_BELOW=20', r.stdout)
                self.assertIn('ignored in power.conf: SAVER_BELOW battery_WIFI_IDLE', r.stdout)
                self.assertEqual(self.power(shell, f'set battery.WIFI_IDLE {bad}').returncode, 2)
            for good in ('0', '9', '10', '9999'):
                self.assertEqual(self.power(shell, f'set battery.WIFI_IDLE {good}').returncode, 0, good)
            self.assertEqual(self.power(shell, 'set SAVER_BELOW 007').returncode, 2)

    def test_profile_selection(self):
        for shell in self.each_shell():
            self.battery(capacity=50)
            self.write_conf('')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            self.charger(online=1)
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            self.charger(online=0)
            self.battery(capacity=15)
            self.assertIn('profile: saver (auto, battery under 20 %)', self.power(shell, 'status').stdout)
            self.write_conf('SAVER_BELOW=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            r = self.power(shell, 'profile plugged')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('PROFILE=plugged\n', self.conf.read_text())
            self.assertIn('profile: plugged (forced)', self.power(shell, 'status').stdout)
            self.power(shell, 'profile auto')
            self.assertIn('(auto', self.power(shell, 'status').stdout)
            self.assertEqual(self.power(shell, 'profile loud').returncode, 2)

    def test_plugged_falls_back_to_extcon_without_a_charger_node(self):
        for shell in self.each_shell():
            self.battery()
            e = self.root / 'sys/class/extcon/extcon0'
            e.mkdir(parents=True, exist_ok=True)
            (e / 'state').write_text('USB=1\nUSB-HOST=0\n')
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            (e / 'state').write_text('USB=0\nUSB-HOST=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)

    def test_f50_ignores_its_fuel_gauge(self):
        # the F50's PMIC fuel gauge reports a battery that is not there (seen: 19 %, 75 C on v2026.10.12)
        self.stub('mu300-device', 'echo f50')
        self.battery(capacity=19, temp=750)
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: none', r.stdout)
            self.assertIn('profile: plugged (auto)', r.stdout)

    def test_v50_ignores_its_fuel_gauge(self):
        # the V50 (FINDINGS 95): no battery either, its PMIC's fuel gauge as the F50's
        self.stub('mu300-device', 'echo v50')
        self.battery(capacity=19, temp=750)
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: none', r.stdout)

    def test_no_battery_is_plugged(self):
        # the F50: no battery node; the daemon runs with battery knobs inert and the device counts as plugged
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: none', r.stdout)
            self.assertIn('profile: plugged (auto)', r.stdout)


class Robustness(PowerTest):
    def test_empty_sysfs_values_do_not_abort_status(self):
        for shell in self.each_shell():
            d = self.battery()
            for k in ('voltage_now', 'current_now', 'temp'):
                (d / k).write_text('')
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: 64 %', r.stdout)

    def test_any_charger_online_counts(self):
        for shell in self.each_shell():
            self.battery()
            self.psy('a-usb', type='USB', online=0)
            self.psy('b-mains', type='Mains', online=1)
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)

    def test_second_battery_node_is_used_and_ignored_not_repeated(self):
        for shell in self.each_shell():
            self.psy('a-gone', type='Battery', present=0, capacity=1)
            self.battery(capacity=64)
            self.write_conf('SAVER_BELOW=abc')
            self.power(shell, 'status')
            r = self.power(shell, 'status')
            self.assertIn('battery: 64 %', r.stdout)
            self.assertEqual((self.run_dir / 'mu300/power/ignored').read_text().count('SAVER_BELOW'), 1)
            self.assertEqual(self.power(shell, 'status').returncode, 0)

    def test_set_appends_after_a_file_without_a_newline(self):
        for shell in self.each_shell():
            self.write_conf('PROFILE=auto')
            self.power(shell, 'set CHARGE_TO 80')
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nCHARGE_TO=80\n')


class DaemonTest(PowerTest):
    def setUp(self):
        super().setUp()
        self.battery()
        (self.root / 'proc').mkdir(parents=True, exist_ok=True)
        self.uptime(0)
        udc = self.root / 'sys/class/udc/25100000.dwc3'
        udc.mkdir(parents=True)
        (udc / 'state').write_text('not attached\n')
        cpu = self.root / 'sys/devices/system/cpu'
        (cpu / 'cpu7').mkdir(parents=True)
        (cpu / 'cpu7/online').write_text('1\n')
        (cpu / 'cpufreq/policy4').mkdir(parents=True)
        (cpu / 'cpufreq/policy4/scaling_max_freq').write_text('2301000\n')
        (cpu / 'cpufreq/policy4/cpuinfo_max_freq').write_text('2301000\n')
        (self.root / 'etc').mkdir(exist_ok=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        (self.root / 'sys/class/net/wlan0').mkdir(parents=True)

    def uptime(self, seconds):
        (self.root / 'proc/uptime').write_text(f'{seconds}.00 0.00\n')

    def stations(self, n):
        (self.tmp / 'stations').write_text(''.join(f'Station 02:00:00:00:00:0{i} (on wlan0)\n' for i in range(n)))

    def usb_host(self, attached):
        (self.root / 'sys/class/udc/25100000.dwc3/state').write_text('configured\n' if attached else 'not attached\n')

    def loops(self, shell, n=1, **env):
        r = self.power(shell, 'daemon', MU300_POWER_LOOPS=n, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def state(self):
        return (self.run_dir / 'mu300/power/state').read_text().strip()

    def idle_after_a_minute(self, shell, conf):
        self.write_conf(conf)
        self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
        self.assertEqual(self.state(), 'idle')


class Daemon(DaemonTest):
    def test_idle_after_wifi_idle_minutes_without_clients(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\nbattery_RADIO_IDLE=off\nbattery_LEDS_IDLE=off\n')
            self.stations(0)
            self.uptime(100); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 9 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 10 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            c = self.calls()
            # the order: LEDs, hotspot, modem, CPU
            self.assertLess(c.index('mu300-led idle on'), c.index('wifi down'))
            self.assertLess(c.index('wifi down'), c.index('mobile-data suspend off'))
            self.assertIn('idle', (self.run_dir / 'mu300/power/reason').read_text())

    def test_a_station_or_a_usb_host_restarts_the_timer(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\n')
            self.uptime(0); self.stations(1); self.loops(shell)
            self.uptime(9 * 60); self.stations(1); self.loops(shell)
            self.uptime(18 * 60); self.stations(0); self.loops(shell)   # 9 min since the last station
            self.assertEqual(self.state(), 'active')
            self.uptime(19 * 60 + 1); self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60); self.usb_host(False); self.loops(shell)   # still 10 min since the USB host
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60 + 2); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_wifi_idle_zero_never_idles(self):
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1)   # plugged: WIFI_IDLE=0
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(24 * 3600); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertNotIn('wifi down', self.calls())

    def test_wake_brings_the_hotspot_up_before_the_modem(self):
        """R13: hotspot, LEDs, CPU, then the modem resume (its dial can take minutes)."""
        for shell in self.each_shell():
            self.setUp()
            # the modem stub notes whether the CPU was already back when it ran
            self.stub('mobile-data', 'echo "mobile-data $* cpu7=$(cat "$MU300_SYSROOT/sys/devices/system/cpu/cpu7/online")" >> "$STUBLOG/calls"\n'
                                     'case $1 in suspend) echo "$2" > "$MU300_RUN/mu300-mobile-data.suspend" ;;\n'
                                     '    resume) rm -f "$MU300_RUN/mu300-mobile-data.suspend" ;; esac')
            self.write_conf('battery_WIFI_IDLE=1\nbattery_CPU=eco\n')
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '0')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '1500000')
            (self.tmp / 'calls').unlink()
            r = self.power(shell, 'wake')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            c = self.calls()
            self.assertLess(c.index('wifi up'), c.index('mu300-led idle off'))
            self.assertLess(c.index('mu300-led idle off'), c.index('mobile-data resume'))
            self.assertIn('mobile-data resume cpu7=1', c)
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '1')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')

    def test_idle_command_enters_idle_now_and_ubuntu_uses_systemctl(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stations(0)
            self.power(shell, 'idle')
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertIn('systemctl stop mu300-hotspot', self.calls())

    def test_radio_idle_lte_and_keep(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=lte\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertIn('mobile-data suspend lte', self.calls())
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=keep\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertNotIn('mobile-data suspend', self.calls())

    def test_plugging_in_while_idle_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.charger(online=1)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('plugged', (self.run_dir / 'mu300/power/reason').read_text())

    def test_a_plug_wakes_even_a_forced_battery_profile(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('PROFILE=battery\nbattery_WIFI_IDLE=1\n')
            self.charger(online=0)
            self.idle_after_a_minute(shell, 'PROFILE=battery\nbattery_WIFI_IDLE=1\n')
            self.charger(online=1)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('plugged', (self.run_dir / 'mu300/power/reason').read_text())

    def test_idle_on_the_charger_stays_idle(self):
        """plugged_WIFI_IDLE=1 with the charger online throughout: idle, and no wake from being plugged."""
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1)
            self.idle_after_a_minute(shell, 'plugged_WIFI_IDLE=1\n')
            self.loops(shell); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertNotIn('wifi up', self.calls())
            # a forced plugged profile on battery is the same: no edge, no wake
            self.setUp()
            self.idle_after_a_minute(shell, 'PROFILE=plugged\nplugged_WIFI_IDLE=1\n')
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_f50_sleep_now_stays_idle(self):
        """The F50 has no battery (always plugged): "Sleep now" must not be undone by the next loop."""
        for shell in self.each_shell():
            self.setUp()
            import shutil
            shutil.rmtree(self.root / 'sys/class/power_supply')
            self.stations(0); self.loops(shell)
            self.power(shell, 'idle'); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.loops(shell); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_station_dump_failure_is_zero_stations(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'sys/class/net/wlan0').rmdir()   # no wlan0: the hotspot is down
            self.stub('iw', 'echo "command failed: No such device (-19)" >&2; exit 237')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_station_dump_failure_with_wlan0_is_activity(self):
        """With wlan0 there, a failed dump is not proof of nobody: the timer restarts (the safe direction)."""
        for shell in self.each_shell():
            self.setUp()
            self.stub('iw', 'echo "command failed: Operation not supported (-95)" >&2; exit 161')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell); self.uptime(200); self.loops(shell)
            self.assertEqual(self.state(), 'active')

    def test_a_failed_dump_does_not_wake_idle_but_a_station_does(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.stub('iw', 'exit 161')   # an AP that is down may refuse the dump
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.stub('iw', 'cat "$STUBLOG/stations"')
            self.stations(1)   # the hotspot was brought up by hand and a client came
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('station', (self.run_dir / 'mu300/power/reason').read_text())

    def test_exit_trap_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            (self.tmp / 'calls').unlink()
            # the daemon's loop gets TERM: the trap must leave the radios up
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('rc=0', r.stdout)
            self.assertIn('wifi up', self.calls())
            # no dial on the way out: the radio comes back and the watcher redials
            self.assertIn('mobile-data resume nodial', self.calls())
            self.assertEqual(self.state(), 'active')

    def term_while_idle(self, shell):
        self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
        (self.tmp / 'calls').unlink()
        r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                    MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                    MU300_POWER_INTERVAL=1)
        self.assertIn('rc=0', r.stdout)

    def test_exit_trap_does_not_wake_during_a_shutdown(self):
        for shell in self.each_shell():
            self.setUp()
            (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
            (self.run_dir / 'mu300/power/shutdown').touch()   # mu300-buttons' held power key, the low battery
            self.term_while_idle(shell)
            self.assertNotIn('wifi up', self.calls()); self.assertNotIn('mobile-data resume', self.calls())
            self.assertEqual(self.state(), 'idle')
            # Ubuntu: systemd says it is stopping
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; [ "$1" != is-system-running ] || echo stopping')
            self.term_while_idle(shell)
            self.assertNotIn('systemctl start', self.calls()); self.assertNotIn('mobile-data resume', self.calls())
            self.assertEqual(self.state(), 'idle')

    def test_profile_change_while_idle_applies_eco(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nsaver_WIFI_IDLE=1\n')
            cpu = self.root / 'sys/devices/system/cpu'
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '1')
            self.battery(capacity=10)   # saver: eco
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '0')
            self.assertEqual((cpu / 'cpufreq/policy4/scaling_max_freq').read_text().strip(), '1500000')

    def test_usb_host_wakes_from_idle(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.usb_host(True)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('USB', (self.run_dir / 'mu300/power/reason').read_text())

    def test_leds_idle_on_leaves_the_leds(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_LEDS_IDLE=on\n')
            self.assertNotIn('mu300-led idle', self.calls())

    def test_state_is_idle_before_the_first_action(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $* state=$(cat "$MU300_RUN/mu300/power/state")" >> "$STUBLOG/calls"')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.assertIn('wifi down state=idle', self.calls())

    def test_a_failed_action_is_retried(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"; if [ ! -e "$STUBLOG/once" ]; then touch "$STUBLOG/once"; exit 1; fi')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.assertEqual(self.calls().count('wifi down'), 1)
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 2)
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 2)

    def test_a_retry_the_state_no_longer_wants_is_dropped(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"; [ "$1" = up ] || exit 1')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.power(shell, 'wake')
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 1)

    def test_an_unknown_state_is_treated_as_active(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell)
            (self.run_dir / 'mu300/power/state').write_text('bogus\n')
            self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_wake_does_not_touch_cpu_limits_it_did_not_set(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')   # CPU=full
            pol = self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq'
            pol.write_text('1800000\n')
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(pol.read_text().strip(), '1800000')


class Guards(DaemonTest):
    def charging_boot(self, shell, capacity=3, online=1):
        (self.run_dir / 'mu300/boot-mode').write_text('charger\n')
        self.charger(online=online); self.battery(capacity=capacity)
        return self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)

    def test_charging_boot_exits_only_on_key(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.charging_boot(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.state(), 'charging-boot')
            self.assertTrue((self.run_dir / 'mu300/charging-boot').exists())
            c = self.calls()
            self.assertIn('wifi down', c); self.assertIn('mobile-data suspend off', c); self.assertIn('mu300-led charge on', c)
            self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # a computer does not end a charging boot
            self.battery(capacity=50); self.loops(shell); self.loops(shell); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # on the charger: nothing happens
            self.assertNotIn('poweroff', self.calls())
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('mu300-led charge off', self.calls())
            self.assertIn('mobile-data resume', self.calls())
            self.assertFalse((self.run_dir / 'mu300/charging-boot').exists())

    def test_the_power_key_does_not_end_a_charging_boot(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'wake power'); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')
            self.assertFalse((self.run_dir / 'mu300/power/wake').exists())
            self.power(shell, 'wake wifi'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertEqual(self.power(shell, 'wake volume').returncode, 2)

    def test_a_restart_after_the_key_stays_active(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)   # boot-mode still says charger
            self.assertEqual(self.state(), 'active')

    def test_a_restart_inside_the_charging_boot_stays_in_it(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)
            self.assertEqual(self.state(), 'charging-boot')

    def test_ubuntu_wake_starts_the_modem_unit(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.charging_boot(shell)
            self.assertIn('systemctl stop mu300-hotspot', self.calls())
            self.power(shell, 'wake'); self.loops(shell)
            self.assertIn('systemctl start --no-block mu300-mobile-data', self.calls())
            self.assertIn('systemctl start mu300-hotspot', self.calls())

    def test_term_in_the_charging_boot_leaves_the_radios_down(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            (self.tmp / 'calls').unlink()
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('rc=0', r.stdout)
            self.assertNotIn('wifi up', self.calls())
            self.assertEqual(self.state(), 'charging-boot')

    def test_unplugged_in_the_charging_boot_powers_off(self):
        # the stock charger mode: the charger goes, the device goes off (kanoqwq 85d72fa9, FINDINGS 196); two loops
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell, capacity=60, online=1)
            self.charger(online=0); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.charger(online=1); self.loops(shell)          # back in between: the count starts again
            self.charger(online=0); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            r = self.loops(shell)
            self.assertIn('poweroff', self.calls())
            self.assertIn('charger removed in the charging boot: powering off', r.stderr)
            self.assertTrue((self.run_dir / 'mu300/power/shutdown').exists())

    def test_unplugged_but_usb_or_charging_is_no_unplug(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell, capacity=60, online=0)
            e = self.root / 'sys/class/extcon/extcon0'
            e.mkdir(parents=True, exist_ok=True)
            (e / 'state').write_text('USB=1\nUSB-HOST=0\n')   # the charger node says offline, extcon a cable
            for _ in range(3): self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            (e / 'state').write_text('USB=0\nUSB-HOST=0\n')
            self.battery(capacity=60, status='Charging')
            for _ in range(3): self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.assertEqual(self.state(), 'charging-boot')

    def test_power_on_charger_is_a_normal_boot(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.write_conf('POWER_ON_CHARGER=1\n')
            (self.run_dir / 'mu300/charging-boot').touch()       # an older init made it
            self.charging_boot(shell, capacity=60, online=1)
            self.assertEqual(self.state(), 'active')
            self.assertFalse((self.run_dir / 'mu300/charging-boot').exists())
            c = self.calls()
            self.assertIn('systemctl start --no-block mu300-mobile-data mu300-hotspot', c)
            self.assertNotIn('mobile-data suspend', c); self.assertNotIn('mu300-led charge on', c)
            self.assertIn('charger boot started as a normal boot', (self.run_dir / 'mu300/power/reason').read_text())
            self.charger(online=0); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())            # an awake device on battery is no charging boot

    def test_power_on_charger_setting(self):
        for shell in self.each_shell():
            self.setUp()
            self.assertIn('POWER_ON_CHARGER=0', self.power(shell, 'status').stdout)
            self.assertEqual(self.power(shell, 'set POWER_ON_CHARGER 1').returncode, 0)
            self.assertIn('POWER_ON_CHARGER=1', self.power(shell, 'status').stdout)
            self.assertEqual(self.power(shell, 'set POWER_ON_CHARGER 2').returncode, 2)

    def low_device(self, capacity, mv, online=0, level=None, device='u30air'):
        self.stub('mu300-device', f'echo {device}')
        self.charger(online=online)
        d = self.battery(capacity=capacity)
        (d / 'voltage_now').write_text(f'{mv * 1000}\n')
        if level is not None: (d / 'capacity_level').write_text(f'{level}\n')
        return d

    def test_low_battery_needs_three_unplugged_loops(self):
        # Android's rule (FINDINGS 196): 0 % (or capacity_level Critical), unplugged, three loops in a row; every state
        for shell in self.each_shell():
            self.setUp()
            self.low_device(0, 3500)
            self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.charger(online=1); self.loops(shell)          # a plug in between resets the count
            self.charger(online=0); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            r = self.loops(shell)
            self.assertIn('poweroff', self.calls())
            self.assertIn('battery at 0 % and unplugged for three loops: powering off', r.stderr)

    def test_critical_level_counts_as_empty(self):
        for shell in self.each_shell():
            self.setUp()
            self.low_device(4, 3500, level='Critical')
            for _ in range(3): self.loops(shell)
            self.assertIn('poweroff', self.calls())
            self.setUp()
            self.low_device(4, 3500, level='Low')
            for _ in range(4): self.loops(shell)
            self.assertNotIn('poweroff', self.calls())

    def test_low_battery_no_poweroff_while_usb_or_charging(self):
        for shell in self.each_shell():
            self.setUp()
            self.low_device(0, 3500)
            e = self.root / 'sys/class/extcon/extcon0'
            e.mkdir(parents=True, exist_ok=True)
            (e / 'state').write_text('USB=1\nUSB-HOST=0\n')
            for _ in range(4): self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            (e / 'state').write_text('USB=0\nUSB-HOST=0\n')
            d = self.battery(capacity=0, status='Charging'); (d / 'voltage_now').write_text('3500000\n')
            for _ in range(4): self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            d = self.battery(capacity=0, status='Discharging'); (d / 'voltage_now').write_text('3500000\n')
            for _ in range(3): self.loops(shell)
            self.assertIn('poweroff', self.calls())
            self.assertTrue((self.run_dir / 'mu300/power/shutdown').exists())

    def test_one_low_sample_does_not_power_off(self):
        for shell in self.each_shell():
            self.setUp()
            d = self.low_device(50, 3700)
            self.battery(capacity=0); self.loops(shell)
            self.battery(capacity=50); self.loops(shell)
            self.battery(capacity=0); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())

    def test_stock_undervoltage_rule(self):
        # cm_uvlo_check_work: five samples at or under 3.10 + 0.12 V, then off at <= 1 % or <= 3.10 V
        for shell in self.each_shell():
            for cap, mv, online, off in ((1, 3200, 0, True), (30, 3200, 0, False), (30, 3090, 0, True),
                                         (1, 3250, 0, False), (1, 3200, 1, False), (0, 0, 0, False)):
                self.setUp()
                self.low_device(cap, mv, online=online)
                r = self.loops(shell, MU300_POWER_UVLO_GAP=0)
                self.assertEqual('poweroff' in self.calls(), off, (cap, mv, online, r.stderr))
                if off: self.assertIn(f'battery at {mv} mV ({cap} %) for five samples, unplugged: powering off', r.stderr)

    def test_undervoltage_threshold_from_the_device_tree(self):
        for shell in self.each_shell():
            self.setUp()
            self.low_device(30, 3390)
            cm = self.root / 'proc/device-tree/charger-manager'
            cm.mkdir(parents=True)
            (cm / 'cm-shutdown-voltage').write_bytes((3400000).to_bytes(4, 'big'))
            self.loops(shell, MU300_POWER_UVLO_GAP=0)
            self.assertIn('poweroff', self.calls())

    def test_no_battery_no_low_battery_poweroff(self):
        for shell in self.each_shell():
            for device in ('f50', 'v50'):
                self.setUp()
                self.low_device(0, 3000, device=device)
                for _ in range(4): self.loops(shell, MU300_POWER_UVLO_GAP=0)
                self.assertNotIn('poweroff', self.calls())

    def test_temperature_guard_with_hysteresis(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); self.battery(temp=460)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'temp')
            self.battery(temp=420); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet under 40.0
            self.battery(temp=390); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.battery(temp=-5); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(temp=20); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet over 3.0
            self.battery(temp=40); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_charge_limit_80(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=80\n')
            ch = self.charger(online=1); self.battery(capacity=81)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'limit')
            self.battery(capacity=77); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(capacity=74); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_charge_limit_any_value(self):
        """1-99: off at the limit, on under limit-5; a changed limit is judged afresh; 100 lets go at once."""
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=60\n')
            ch = self.charger(online=1); self.battery(capacity=60)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-limit').read_text().strip(), '60')
            self.battery(capacity=55); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet under 55
            self.battery(capacity=54); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.assertFalse((self.run_dir / 'mu300/power/charge-limit').exists())
            # held at 58 by a limit of 60, then the limit goes up to 70: charging again at once
            self.battery(capacity=60); self.loops(shell)
            self.battery(capacity=58); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.write_conf('CHARGE_TO=70\n'); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            # held again, then 100: on, also without a capacity reading
            self.battery(capacity=70); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            (self.root / 'sys/class/power_supply/sc27xx-fgu/capacity').unlink()
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # no reading: kept off
            self.write_conf('CHARGE_TO=100\n'); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.assertFalse((self.run_dir / 'mu300/power/charge-off').exists())

    def test_charge_limit_0_and_the_5_percent_floor(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=0\n')
            ch = self.charger(online=1); self.battery(capacity=50)
            r = self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertIn('charging off: limit', r.stderr)
            self.battery(capacity=6); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(capacity=4); r = self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')   # never held under 5 %
            self.assertIn('charging on: battery under 5 %', r.stderr)
            self.battery(capacity=5); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            # the temperature guard still has the last word: too hot under 5 % stays off
            self.battery(capacity=3, temp=470); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'temp')

    def test_charge_limit_command(self):
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1); self.battery(capacity=50)
            r = self.power(shell, 'charge-limit')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('charge limit: none (100 %)', r.stdout)
            r = self.power(shell, 'charge-limit 80')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('charge limit: 80 % (charging stops at 80 %, starts again under 75 %)', r.stdout)
            self.assertIn('CHARGE_TO=80\n', self.conf.read_text())
            r = self.power(shell, 'charge-limit 0')
            self.assertIn('charge limit: 0 %', r.stdout)
            r = self.power(shell, 'charge-limit 3')
            self.assertIn('stops at 5 %, starts again under 5 %', r.stdout)
            r = self.power(shell, 'charge-limit 101')
            self.assertEqual(r.returncode, 2)
            r = self.power(shell, 'charge-limit 1 2')
            self.assertEqual(r.returncode, 2)
            self.assertIn('CHARGE_TO=3\n', self.conf.read_text())

    def test_charge_limit_on_the_f50_does_nothing(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('mu300-device', 'echo f50')
            self.write_conf('CHARGE_TO=0\n')
            ch = self.charger(online=1); self.battery(capacity=50)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            r = self.power(shell, 'charge-limit')
            self.assertIn('no battery here', r.stdout)

    def test_guard_without_a_reading_or_a_switch_changes_nothing(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1)
            (ch / 'charge_type').unlink()
            self.battery(temp=460); self.loops(shell)
            self.assertFalse((ch / 'charge_type').exists())
            self.setUp()
            ch = self.charger(online=1); b = self.battery(); (b / 'temp').unlink()
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_log_line(self):
        for shell in self.each_shell():
            self.setUp()
            self.battery(capacity=64, temp=281, current=-470000)
            out = self.tmp / 'power.csv'
            r = self.power(shell, f'log 0 {out}', MU300_POWER_LOOPS=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = out.read_text().splitlines()
            self.assertEqual(lines[0], 'epoch,mV,mA,mW,capacity,temp,state,profile')
            self.assertEqual(len(lines), 3)
            f = lines[1].split(',')
            self.assertEqual(f[1:6], ['3850', '-470', '-1809', '64', '281'])   # 3.85 V * -0.47 A = -1.81 W
            self.assertEqual(f[6:], ['active', 'battery'])

    def test_log_into_a_missing_directory_fails(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.power(shell, f'log 0 {self.tmp}/nodir/power.csv', MU300_POWER_LOOPS=2)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nodir', r.stderr)

    def test_cpu_full_restores_when_eco_could_not_read_the_limit(self):
        for shell in self.each_shell():
            self.setUp()
            cpu = self.root / 'sys/devices/system/cpu'
            (cpu / 'cpufreq/policy4/scaling_max_freq').unlink()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_CPU=eco\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '0')
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '1')
            self.assertEqual((cpu / 'cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')

    def test_retried_actions_do_not_eat_the_retry_list(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; cat >/dev/null; exit 1')
            self.stub('mobile-data', 'echo "mobile-data $*" >> "$STUBLOG/calls"; exit 1')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.calls().count('mobile-data suspend off'), 1)
            self.loops(shell)
            self.assertEqual(self.calls().count('mobile-data suspend off'), 2)

    def test_no_reading_keeps_charging_off(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); b = self.battery(temp=460)
            self.loops(shell)
            (b / 'temp').unlink(); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.setUp()
            self.write_conf('CHARGE_TO=80\n')
            ch = self.charger(online=1); b = self.battery(capacity=85)
            self.loops(shell)
            (b / 'capacity').unlink(); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')

    def test_n_a_without_our_marker_is_not_written(self):
        """bq256xx reads N/A whenever it is not charging: without $ST/charge-off it is not ours to switch on, and
        nothing is logged loop after loop."""
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=0, charge_type='N/A'); self.battery(temp=250)
            r1 = self.loops(shell); r2 = self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertNotIn('charging', r1.stderr + r2.stderr)

    def test_guard_logs_only_transitions(self):
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1); self.battery(temp=460)
            r1 = self.loops(shell); r2 = self.loops(shell)
            self.assertIn('charging off: temp', r1.stderr); self.assertNotIn('charging', r2.stderr)
            self.battery(temp=390)
            r3 = self.loops(shell); r4 = self.loops(shell)
            self.assertIn('charging on', r3.stderr); self.assertNotIn('charging', r4.stderr)

    def test_a_failed_write_turns_the_guard_off_once(self):
        if os.geteuid() == 0:
            self.skipTest('root writes a read-only file')
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); self.battery(temp=460)
            (ch / 'charge_type').chmod(0o444)
            r1 = self.loops(shell); r2 = self.loops(shell)
            self.assertIn('failed', r1.stderr)
            self.assertNotIn('failed', r2.stderr); self.assertNotIn('charging', r2.stderr)
            self.assertTrue((self.run_dir / 'mu300/power/switch-failed').exists())
            (ch / 'charge_type').chmod(0o644)

    def test_the_bq256xx_node_is_the_switch(self):
        for shell in self.each_shell():
            self.setUp()
            other = self.psy('a-mains', type='Mains', online=1, charge_type='Fast')
            ch = self.charger(online=1); self.battery(temp=460)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((other / 'charge_type').read_text().strip(), 'Fast')
            # by the driver named in uevent, when the name says nothing
            self.setUp()
            other = self.psy('a-mains', type='Mains', online=1, charge_type='Fast')
            ch = self.psy('charger', type='USB', online=1, charge_type='Fast', uevent='DRIVER=bq256xx')
            self.battery(temp=460); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((other / 'charge_type').read_text().strip(), 'Fast')

    def test_node_reset_while_our_marker_says_off(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1, charge_type='Fast'); self.battery(temp=460)
            (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
            (self.run_dir / 'mu300/power/charge-off').write_text('temp\n')   # marker says off, node was reset to on
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')

    def test_no_switch_is_logged_once(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); (ch / 'charge_type').unlink()
            r1 = self.loops(shell); r2 = self.loops(shell)
            self.assertIn('no charge switch', r1.stderr)
            self.assertNotIn('no charge switch', r2.stderr)

    def test_idle_request_in_the_charging_boot_is_dropped(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'idle'); self.loops(shell)
            self.power(shell, 'wake'); self.loops(shell)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')

    def test_log_validates_seconds(self):
        for shell in self.each_shell():
            self.setUp()
            out = self.tmp / 'p.csv'
            self.assertEqual(self.power(shell, f'log abc {out}').returncode, 2)
            self.assertEqual(self.power(shell, f'log 0 {out}').returncode, 2)   # 0 only with MU300_POWER_LOOPS

class Incident(DaemonTest):
    """2026-10-07, the U30 Air: the raw idle actions run by hand (mu300-led idle on, wifi down, mobile-data suspend
    lte/off) while the state file said "active". Every wake trusted the file and did nothing, the keys' LED wake was a
    no-op under the idle flag, and the device sat dark and deaf for hours. A wake now restores by looking, with no
    daemon, and every external step has a deadline."""

    def taken_down_by_hand(self, ubuntu=False):
        """What the measurement script left: AP down, modem suspended off, LEDs held dark, eco CPU without its marker
        - and the state file still "active"."""
        if ubuntu:
            (self.root / 'etc/openwrt_release').unlink()
        (self.tmp / 'ap-up').unlink()
        (self.run_dir / 'mu300-mobile-data.suspend').write_text('off\n')
        (self.root / 'run/mu300/led').mkdir(parents=True, exist_ok=True)
        (self.root / 'run/mu300/led/.idle').touch()
        cpu = self.root / 'sys/devices/system/cpu'
        (cpu / 'cpu7/online').write_text('0\n')
        (cpu / 'cpufreq/policy4/scaling_max_freq').write_text('1500000\n')
        st = self.run_dir / 'mu300/power'
        st.mkdir(parents=True, exist_ok=True)
        (st / 'state').write_text('active\n')

    def assert_restored(self):
        c = self.calls()
        self.assertTrue((self.tmp / 'ap-up').exists(), c)
        self.assertFalse((self.run_dir / 'mu300-mobile-data.suspend').exists(), c)
        self.assertFalse((self.root / 'run/mu300/led/.idle').exists(), c)
        cpu = self.root / 'sys/devices/system/cpu'
        self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '1')
        self.assertEqual((cpu / 'cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')
        self.assertEqual(self.state(), 'active')
        # the order: hotspot, LEDs, then the modem (whose dial can take minutes)
        self.assertLess(c.index('mu300-led idle off'), c.index('mobile-data resume'))

    def test_a_key_restores_by_inspection_without_the_daemon(self):
        for shell in self.each_shell():
            for ubuntu in (False, True):
                self.setUp()
                self.taken_down_by_hand(ubuntu)
                r = self.power(shell, 'wake power')   # no daemon anywhere: the wake does the work itself
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assert_restored()
                c = self.calls()
                self.assertIn('systemctl start mu300-hotspot' if ubuntu else 'wifi up', c)
                self.assertLess(c.index('systemctl start mu300-hotspot' if ubuntu else 'wifi up'), c.index('mu300-led idle off'))
                self.assertIn('woken by the power key', (self.run_dir / 'mu300/power/reason').read_text())

    def test_a_wake_with_everything_up_changes_nothing(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.power(shell, 'wake')
            self.assertEqual(r.returncode, 0, r.stderr)
            c = self.calls()
            for not_called in ('wifi', 'mu300-led idle', 'mobile-data', 'vpn'):
                self.assertNotIn(not_called, c)
            self.assertEqual(self.state(), 'active')

    def test_a_hotspot_turned_off_on_purpose_stays_off(self):
        for shell in self.each_shell():
            # the Wi-Fi key held (mu300-buttons' marker), and OpenWrt's UCI (the panel, wifi-client)
            for how in ('marker', 'iface', 'radio'):
                self.setUp()
                self.taken_down_by_hand()
                if how == 'marker':
                    (self.run_dir / 'mu300/power/hotspot-off').touch()
                else:
                    key = 'wireless.@wifi-iface[0].disabled' if how == 'iface' else 'wireless.@wifi-device[0].disabled'
                    self.stub('uci', f'[ "$*" = "-q get {key}" ] && echo 1')
                self.power(shell, 'wake')
                self.assertNotIn('wifi up', self.calls(), how)
                self.assertFalse((self.run_dir / 'mu300-mobile-data.suspend').exists(), how)   # the rest comes back
            # Ubuntu: the unit disabled, or the radio a Wi-Fi client
            for how in ('disabled', 'client'):
                self.setUp()
                self.taken_down_by_hand(ubuntu=True)
                if how == 'disabled':
                    self.stub('systemctl', self.SYSTEMCTL.replace('case "$*" in', 'case "$*" in\n    "is-enabled mu300-hotspot") echo disabled; exit 1 ;;'))
                else:
                    (self.run_dir / 'mu300-wifi-client.active').touch()
                self.power(shell, 'wake')
                self.assertNotIn('systemctl start mu300-hotspot', self.calls(), how)

    def test_a_key_while_idle_wakes_without_the_daemon(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.power(shell, 'wake wifi')   # the daemon is not running (the loops above have ended)
            self.assertEqual(self.state(), 'active')
            self.assertTrue((self.tmp / 'ap-up').exists())
            self.assertFalse((self.run_dir / 'mu300-mobile-data.suspend').exists())
            self.assertFalse((self.root / 'run/mu300/led/.idle').exists())

    def test_cpu_eco_cap_without_the_marker_is_lifted_but_not_under_a_thermal_alarm(self):
        for shell in self.each_shell():
            self.setUp()
            pol = self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq'
            pol.write_text('1500000\n')
            self.power(shell, 'wake')
            self.assertEqual(pol.read_text().strip(), '2301000')
            self.setUp()
            pol = self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq'
            pol.write_text('1500000\n')
            (self.run_dir / 'mu300/thermal-alarm').touch()
            self.power(shell, 'wake')
            self.assertEqual(pol.read_text().strip(), '1500000')

    # --- deadlines
    def hung(self, name):
        """NAME never returns (it notes its pid, to check that it was killed)."""
        self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; echo $$ >> "$STUBLOG/hung.pids"; exec sleep 1000')

    def assert_killed(self):
        import time
        pids = [int(p) for p in (self.tmp / 'hung.pids').read_text().split()]
        self.assertTrue(pids)
        for pid in pids:
            for _ in range(50):   # (the LED sync runs in the background with its own deadline)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail(f'pid {pid} still runs')

    def test_a_hung_step_cannot_hold_the_wake(self):
        import time
        for shell in self.each_shell():
            for name in ('wifi', 'mobile-data', 'mu300-led', 'ubus'):
                self.setUp()
                self.taken_down_by_hand()
                self.hung(name)
                t0 = time.time()
                r = self.power(shell, 'wake', MU300_POWER_STEP_DEADLINE=1, MU300_POWER_MODEM_DEADLINE=1,
                               MU300_POWER_LED_DEADLINE=1, MU300_POWER_PROBE_DEADLINE=1, MU300_POWER_SYNC_DEADLINE=1)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertLess(time.time() - t0, 15, name)
                self.assertIn('did not finish in 1 s', self.calls(), name)   # (logger)
                self.assertEqual(self.state(), 'active', name)
                self.assert_killed()
                # the next steps ran all the same
                self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '1', name)

    def test_a_hung_station_dump_cannot_hold_the_daemon(self):
        import time
        for shell in self.each_shell():
            self.setUp()
            self.hung('iw')
            t0 = time.time()
            self.loops(shell, MU300_POWER_PROBE_DEADLINE=1)
            self.assertLess(time.time() - t0, 15)
            self.assert_killed()

    # --- the actions lock
    def test_a_dead_holders_lock_is_taken_over_and_a_live_restore_is_not_doubled(self):
        for shell in self.each_shell():
            self.setUp()
            self.taken_down_by_hand()
            st = self.run_dir / 'mu300/power'
            os.symlink('999999:0:idle', st / 'busy')   # a holder long gone
            r = self.power(shell, 'wake')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assert_restored()
            self.assertFalse(os.path.lexists(st / 'busy'))
            # a restore in progress (this test process is alive) does the work: the second wake returns at once
            self.setUp()
            self.taken_down_by_hand()
            st = self.run_dir / 'mu300/power'
            import subprocess
            holder = subprocess.Popen(['sh', '-c', 'sleep 30; : mu300-power'])   # a live mu300-power, by its cmdline
            try:
                os.symlink(f'{holder.pid}:0:restore', st / 'busy')
                r = self.power(shell, 'wake')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn('wifi up', self.calls())
            finally:
                holder.kill(); holder.wait()
            os.unlink(st / 'busy')

    # --- review round 1
    def test_a_stale_hold_marker_does_not_keep_an_idled_hotspot_down(self):
        for shell in self.each_shell():
            # the marker is there but the hotspot is up (it came back some other way): idle takes it down, the wake
            # brings it back and the marker goes
            self.setUp()
            (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
            (self.run_dir / 'mu300/power/hotspot-off').write_text('0\n')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.assertFalse((self.tmp / 'ap-up').exists())
            self.power(shell, 'wake')
            self.assertTrue((self.tmp / 'ap-up').exists())
            self.assertFalse((self.run_dir / 'mu300/power/hotspot-off').exists())
            # a hotspot the user really had off (marker, hotspot down) stays off through idle and the wake
            self.setUp()
            (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
            (self.run_dir / 'mu300/power/hotspot-off').write_text('0\n')
            (self.tmp / 'ap-up').unlink()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.power(shell, 'wake')
            self.assertFalse((self.tmp / 'ap-up').exists())
            self.assertNotIn('wifi up', self.calls())
            # Ubuntu: a disabled unit that was running comes back after idle
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stub('systemctl', self.SYSTEMCTL.replace('case "$*" in', 'case "$*" in\n    "is-enabled mu300-hotspot") echo disabled; exit 1 ;;'))
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.power(shell, 'wake')
            self.assertIn('systemctl start mu300-hotspot', self.calls())

    def test_an_old_marker_goes_once_the_hotspot_is_seen_up(self):
        for shell in self.each_shell():
            self.setUp()
            st = self.run_dir / 'mu300/power'
            st.mkdir(parents=True, exist_ok=True)
            (st / 'hotspot-off').write_text('100\n')
            self.uptime(270); self.power(shell, 'wake')
            self.assertTrue((st / 'hotspot-off').exists())    # 170 s old: a hold whose toggle may still run (150 s)
            self.uptime(290); self.power(shell, 'wake')
            self.assertFalse((st / 'hotspot-off').exists())

    def test_endc_still_off_is_resumed_by_the_wake_and_the_self_check(self):
        for shell in self.each_shell():
            for how in ('wake', 'loop'):
                self.setUp()
                (self.run_dir / 'mu300-mobile-data.endc-restore').touch()
                self.stub('mobile-data', 'echo "mobile-data $*" >> "$STUBLOG/calls"; [ "$1" != resume ] || rm -f "$MU300_RUN/mu300-mobile-data.endc-restore"')
                if how == 'wake':
                    self.power(shell, 'wake')
                else:
                    self.uptime(10); self.loops(shell)
                self.assertIn('mobile-data resume', self.calls(), how)
                self.assertFalse((self.run_dir / 'mu300-mobile-data.endc-restore').exists(), how)

    def test_a_vpn_probe_that_fails_or_times_out_counts_as_wanted(self):
        for shell in self.each_shell():
            for enabled in ('exit 2', 'sleep 30'):   # a failure that is not "disabled" (1), and a timeout
                self.setUp()
                self.vpn_openwrt()
                self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
                init = self.root / 'etc/init.d/mu300-vpn'
                init.write_text(init.read_text().replace('    enabled) [ ! -e "$STUBLOG/vpn-disabled" ] ;;', f'    enabled) {enabled} ;;'))
                self.power(shell, 'wake', MU300_POWER_PROBE_DEADLINE=1)
                self.assertIn('mu300-vpn start', self.calls(), enabled)
                self.assertTrue((self.tmp / 'vpn-up').exists(), enabled)

    def test_the_vpn_is_not_started_again_from_vpn_status(self):
        """vpn_wanted asks `mu300-vpn enabled`, never `status` (its exit-IP lookup goes on the network)."""
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            self.stub('mu300-vpn', 'echo "mu300-vpn $*" >> "$STUBLOG/calls"; [ "$1" != enabled ] || echo 1')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
            self.power(shell, 'wake')
            self.assertIn('mu300-vpn enabled', self.calls())
            self.assertNotIn('mu300-vpn status', self.calls())
            self.assertTrue((self.tmp / 'vpn-up').exists())

    def test_a_failed_vpn_start_is_retried_by_the_self_check(self):
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
            init = self.root / 'etc/init.d/mu300-vpn'
            good = init.read_text()
            init.write_text(good.replace('    start) : > "$STUBLOG/vpn-up" ;;', '    start) exit 1 ;;'))
            self.uptime(100); self.power(shell, 'wake')
            self.assertFalse((self.tmp / 'vpn-up').exists())
            self.assertTrue((self.run_dir / 'mu300/power/vpn-stopped').exists())
            init.write_text(good)
            self.uptime(110); r = self.loops(shell)    # throttled: within the grace period of the failed try
            self.assertFalse((self.tmp / 'vpn-up').exists(), r.stderr)
            self.uptime(230); r = self.loops(shell)
            self.assertIn('self-check', r.stderr)
            self.assertTrue((self.tmp / 'vpn-up').exists())
            self.assertFalse((self.run_dir / 'mu300/power/vpn-stopped').exists())

    def test_an_idled_hotspot_stays_down_for_a_wifi_client(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            (self.run_dir / 'mu300-wifi-client.active').touch()   # the radio became a client meanwhile
            (self.tmp / 'calls').unlink()
            self.power(shell, 'wake')
            self.assertNotIn('wifi up', self.calls())

    def test_term_during_a_bounded_step_leaves_no_watchdog_behind(self):
        import time
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61)
            self.stub('mobile-data', 'echo "mobile-data $* start" >> "$STUBLOG/calls"; sleep 2\n'
                                     'echo "mobile-data $* done" >> "$STUBLOG/calls"\n'
                                     'case $1 in suspend) echo "$2" > "$MU300_RUN/mu300-mobile-data.suspend" ;;\n'
                                     '    resume) rm -f "$MU300_RUN/mu300-mobile-data.suspend" ;; esac')
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1, MU300_POWER_MODEM_DEADLINE=4)
            self.assertIn('rc=0', r.stdout, r.stderr)
            c = self.calls()
            # the suspend in flight finished, then the trap woke
            self.assertLess(c.index('mobile-data suspend off done'), c.index('mobile-data resume nodial start'), c)
            time.sleep(5)   # past the suspend's deadline: its watchdog must be gone, not faking a timeout
            self.assertNotIn('did not finish', self.calls())
            self.assertEqual(list((self.run_dir / 'mu300/power').glob('.deadline.*')), [])
            self.assertEqual(self.state(), 'active')

    # --- the VPN while the modem is off
    def vpn_openwrt(self, running=True):
        d = self.root / 'etc/init.d'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'mu300-vpn').write_text('#!/bin/sh\necho "init.d/mu300-vpn $*" >> "$STUBLOG/calls"\n'
                                     'case $1 in running) [ -e "$STUBLOG/vpn-up" ] ;; stop) rm -f "$STUBLOG/vpn-up" ;;\n'
                                     '    enabled) [ ! -e "$STUBLOG/vpn-disabled" ] ;;\n'
                                     '    start) : > "$STUBLOG/vpn-up" ;; esac\n')
        (d / 'mu300-vpn').chmod(0o755)
        if running:
            (self.tmp / 'vpn-up').touch()

    def test_vpn_stops_with_the_modem_off_and_starts_on_the_wake(self):
        for shell in self.each_shell():
            for ubuntu in (False, True):
                self.setUp()
                if ubuntu:
                    (self.root / 'etc/openwrt_release').unlink()
                    (self.tmp / 'vpn-up').touch()
                    stop, start = 'systemctl stop mu300-vpn', 'systemctl start mu300-vpn'
                else:
                    self.vpn_openwrt()
                    stop, start = 'init.d/mu300-vpn stop', 'init.d/mu300-vpn start'
                self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
                c = self.calls()
                self.assertLess(c.index('mobile-data suspend off'), c.index(stop))   # once the modem is off
                self.assertFalse((self.tmp / 'vpn-up').exists())
                self.assertTrue((self.run_dir / 'mu300/power/vpn-stopped').exists())
                self.power(shell, 'wake')
                c = self.calls()
                # the VPN before the modem: no window in clear with KILL_SWITCH=0
                self.assertLess(c.index(start), c.index('mobile-data resume'))
                self.assertTrue((self.tmp / 'vpn-up').exists())
                self.assertFalse((self.run_dir / 'mu300/power/vpn-stopped').exists())

    def test_vpn_is_left_alone_on_lte_and_when_it_was_not_running(self):
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=lte\n')
            self.power(shell, 'wake')
            self.assertNotIn('mu300-vpn stop', self.calls()); self.assertNotIn('mu300-vpn start', self.calls())
            self.setUp()
            self.vpn_openwrt(running=False)
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
            self.power(shell, 'wake')
            self.assertNotIn('mu300-vpn stop', self.calls()); self.assertNotIn('mu300-vpn start', self.calls())

    def test_without_the_vpn_module_nothing_is_asked_of_it(self):
        # the VPN is a module of its own (dikeckaan/mu300-linux-vpn): without it there is no init script (OpenWrt)
        # and no unit (Ubuntu: systemctl says inactive), and the modem goes off and comes back without a VPN step
        for shell in self.each_shell():
            for ubuntu in (False, True):
                self.setUp()
                if ubuntu:
                    (self.root / 'etc/openwrt_release').unlink()
                self.assertFalse((self.root / 'etc/init.d/mu300-vpn').exists())
                self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
                r = self.power(shell, 'wake')
                c = self.calls()
                self.assertIn('mobile-data suspend off', c)
                self.assertNotIn('mu300-vpn stop', c)
                self.assertNotIn('mu300-vpn start', c)
                self.assertFalse((self.run_dir / 'mu300/power/vpn-stopped').exists())

    def test_no_vpn_flap_while_the_suspend_keeps_failing(self):
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            self.stub('mobile-data', 'echo "mobile-data $*" >> "$STUBLOG/calls"; exit 1')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
            self.loops(shell); self.loops(shell)   # the retries
            self.assertGreaterEqual(self.calls().count('mobile-data suspend off'), 2)
            self.assertNotIn('mu300-vpn stop', self.calls()); self.assertNotIn('mu300-vpn start', self.calls())
            self.assertTrue((self.tmp / 'vpn-up').exists())

    def test_no_vpn_stop_while_the_uplink_is_the_wifi_client(self):
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            (self.run_dir / 'mu300-wifi-client.active').touch()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
            self.assertNotIn('mu300-vpn stop', self.calls())
            self.assertTrue((self.tmp / 'vpn-up').exists())

    def test_a_vpn_turned_off_meanwhile_is_not_started_again(self):
        for shell in self.each_shell():
            for how in ('disabled', 'conf'):
                self.setUp()
                self.vpn_openwrt()
                self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=off\n')
                if how == 'disabled':
                    (self.tmp / 'vpn-disabled').touch()
                else:   # ENABLE=0 in vpn.conf, as `mu300-vpn enabled` prints it
                    self.stub('mu300-vpn', '[ "$1" = enabled ] && echo 0')
                self.power(shell, 'wake')
                self.assertNotIn('mu300-vpn start', self.calls(), how)
                self.assertFalse((self.run_dir / 'mu300/power/vpn-stopped').exists(), how)

    def test_the_exit_trap_starts_the_vpn_again(self):
        for shell in self.each_shell():
            self.setUp()
            self.vpn_openwrt()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('rc=0', r.stdout)
            self.assertIn('mobile-data resume nodial', self.calls())
            self.assertTrue((self.tmp / 'vpn-up').exists())

    # --- the daemon's self-check
    def test_self_check_restores_an_active_device_with_its_radios_down(self):
        for shell in self.each_shell():
            # plugged (WIFI_IDLE=0): the hotspot down is restored; past the boot's grace only
            self.setUp()
            self.charger(online=1)
            (self.tmp / 'ap-up').unlink()
            self.uptime(60); self.loops(shell)
            self.assertNotIn('wifi up', self.calls())
            self.uptime(600); r = self.loops(shell)
            self.assertIn('wifi up', self.calls())
            self.assertIn('self-check', r.stderr)
            self.assertEqual(self.state(), 'active')
            # and not again at once, should the hotspot not start
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"')
            (self.tmp / 'ap-up').unlink()
            self.uptime(610); self.loops(shell)
            self.assertEqual(self.calls().count('wifi up'), 1)
            self.uptime(800); self.loops(shell)
            self.assertEqual(self.calls().count('wifi up'), 2)
            # a modem held down while active is resumed whatever the profile
            self.setUp()
            (self.run_dir / 'mu300-mobile-data.suspend').write_text('lte\n')
            self.uptime(10); r = self.loops(shell)
            self.assertIn('mobile-data resume', self.calls())
            self.assertIn('self-check', r.stderr)
            self.assertFalse((self.run_dir / 'mu300-mobile-data.suspend').exists())

    def test_self_check_respects_a_hotspot_turned_off_on_purpose(self):
        for shell in self.each_shell():
            for how in ('marker', 'uci'):
                self.setUp()
                self.charger(online=1)
                (self.tmp / 'ap-up').unlink()
                if how == 'marker':
                    (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
                    (self.run_dir / 'mu300/power/hotspot-off').touch()
                else:
                    self.stub('uci', '[ "$*" = "-q get wireless.@wifi-iface[0].disabled" ] && echo 1')
                self.uptime(600); self.loops(shell)
                self.assertNotIn('wifi up', self.calls(), how)
            # a battery profile (WIFI_IDLE > 0) leaves a downed hotspot to the idle timer
            self.setUp()
            (self.tmp / 'ap-up').unlink()
            self.uptime(600); self.loops(shell)
            self.assertNotIn('wifi up', self.calls())


class Units(unittest.TestCase):
    def test_openwrt_instance_runs_mobile_data_through_netifd(self):
        f = (TOP / 'openwrt/overlay/etc/init.d/mu300-power').read_text()
        self.assertIn('procd_set_param env MU300_NETIFD=1', f)


ADAPTER = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power'


class Adapter(DaemonTest):
    def adapter(self, shell, args):
        return self.sh(shell, f'"{ADAPTER}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_POWER_CONF=self.conf, MU300_POWER_BIN=POWER)

    def test_get_is_json_with_the_state_and_the_knobs(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=15\nbattery_CPU=turbo\n')
            self.charger(online=0, usb_type='Unknown [SDP] CDP DCP')
            r = self.adapter(shell, 'get')
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(d['profile'], 'battery'); self.assertEqual(d['why'], 'auto')
            self.assertEqual(d['conf']['battery']['WIFI_IDLE'], 15)
            self.assertEqual(d['conf']['plugged']['RADIO_IDLE'], 'keep')
            self.assertEqual(d['conf']['PROFILE'], 'auto'); self.assertEqual(d['conf']['SAVER_BELOW'], 20)
            self.assertEqual(d['battery']['capacity'], 64); self.assertEqual(d['battery']['ma'], -470)
            self.assertEqual(d['charger']['usb_type'], 'SDP')
            self.assertEqual(d['state'], 'active')
            self.assertEqual(d['supply'], 'battery')
            self.assertEqual(d['ignored'], ['battery_CPU'])
            self.assertIsNone(d['idle_since_s']); self.assertEqual(d['charge_off'], '')

    def test_get_has_the_input_current_and_any_charge_limit(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=37\n')
            ch = self.charger(online=1, usb_type='Unknown [SDP] CDP DCP')
            (ch / 'input_current_limit').write_text('1500000\n')
            d = json.loads(self.adapter(shell, 'get').stdout)
            self.assertEqual(d['charger']['input_ma'], 1500)
            self.assertEqual(d['conf']['CHARGE_TO'], 37)
            self.assertEqual(d['conf']['POWER_ON_CHARGER'], 0)
            self.assertEqual(json.loads(self.adapter(shell, 'set POWER_ON_CHARGER 1').stdout), {'ok': 1})
            self.assertEqual(json.loads(self.adapter(shell, 'get').stdout)['conf']['POWER_ON_CHARGER'], 1)
            r = self.adapter(shell, 'set CHARGE_TO 0')
            self.assertEqual(json.loads(r.stdout), {'ok': 1})
            r = self.adapter(shell, 'set CHARGE_TO 101')
            self.assertEqual(json.loads(r.stdout)['ok'], 0)

    def test_get_on_the_f50_has_no_battery(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            self.stub('mu300-device', 'echo f50')
            d = json.loads(self.adapter(shell, 'get').stdout)
            self.assertIsNone(d['battery'])

    def test_get_without_battery_or_charger_is_null(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            import shutil
            shutil.rmtree(self.root / 'sys/class/power_supply')
            d = json.loads(self.adapter(shell, 'get').stdout)
            self.assertIsNone(d['battery']); self.assertIsNone(d['charger']); self.assertEqual(d['ignored'], [])

    def test_leading_zeros_are_not_json_numbers(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=08\n')
            b = self.battery()
            (b / 'capacity').write_text('064\n'); (b / 'temp').write_text('0281\n')
            r = self.adapter(shell, 'get')
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(d['conf']['battery']['WIFI_IDLE'], 10)
            self.assertIsNone(d['battery']['capacity']); self.assertIsNone(d['battery']['temp'])

    def test_a_reason_with_quotes_is_still_json(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            st = self.run_dir / 'mu300/power'
            st.mkdir(parents=True, exist_ok=True)
            (st / 'reason').write_text('said "hi" \\ back\\slash\ttab\n')
            (st / 'charge-off').write_text('te"mp\n')
            d = json.loads(self.adapter(shell, 'get').stdout)
            self.assertEqual(d['reason'], 'said "hi" \\ back\\slash\ttab')
            self.assertEqual(d['charge_off'], 'te"mp')

    def test_set_wake_idle(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            r = self.adapter(shell, 'set battery.WIFI_IDLE 20')
            self.assertEqual(json.loads(r.stdout), {'ok': 1})
            self.assertIn('battery_WIFI_IDLE=20', self.conf.read_text())
            r = self.adapter(shell, 'set battery.WIFI_IDLE never')
            d = json.loads(r.stdout); self.assertEqual(d['ok'], 0); self.assertIn('WIFI_IDLE', d['error'])
            self.assertEqual(json.loads(self.adapter(shell, 'idle').stdout), {'ok': 1})
            self.assertTrue((self.run_dir / 'mu300/power/idle-now').exists())
            self.assertEqual(json.loads(self.adapter(shell, 'wake').stdout), {'ok': 1})
            # the wake restores without the daemon, in the background (rpcd ends a call at 30 s)
            import time
            reason = self.run_dir / 'mu300/power/reason'
            for _ in range(100):
                if reason.exists() and 'woken' in reason.read_text():
                    break
                time.sleep(0.1)
            self.assertIn('woken', reason.read_text())
