"""U30 status events own three independent physical lamp groups."""
import os
import subprocess
import shutil
from pathlib import Path
from helpers import BIN,ShellTest

class U30Leds(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root';self.lamps=self.root/'sys/class/leds'
        for name in ['net_blue','zte-ldo0','zte-ldo1','zte-ldo2','keyboard-backlight','sc27xx:red','sc27xx:green','sc27xx:blue']:
            p=self.lamps/name;p.mkdir(parents=True)
            (p/'brightness').write_text('0\n');(p/'max_brightness').write_text('255\n' if name.startswith('sc27xx:') else '1\n');(p/'trigger').write_text('[none] timer\n')
        (self.root/'run/mu300').mkdir(parents=True);(self.root/'run/mu300/device').write_text('u30air\n')
        (self.root/'proc').mkdir();(self.root/'proc/uptime').write_text('100.00 999.00\n')
        self.battery=self.root/'sys/class/power_supply/sc27xx-fgu';self.battery.mkdir(parents=True)
        (self.battery/'capacity').write_text('65\n');(self.battery/'status').write_text('Charging\n')
        self.stub('uci',"""case "$3" in
 system.mu300_leds.timeout) echo "${TEST_TIMEOUT:-0}" ;;
 system.mu300_leds.signal) echo "${TEST_SIGNAL:-1}" ;;
 system.mu300_leds.wifi) echo "${TEST_WIFI:-1}" ;;
 system.mu300_leds.battery) echo "${TEST_BATTERY:-1}" ;;
 system.mu300_leds.battery_brightness) echo "${TEST_BRIGHTNESS:-255}" ;;
esac""")
        self.stub('iw','echo "channel 36 (5180 MHz)"')
        self.extra=dict(MU300_SYSROOT=self.root,MU300_BIN=BIN)

    def apply(self,shell,*args,**extra):
        r=self.script(shell,BIN/'u30air-led-status',*args,**dict(self.extra,**extra))
        self.assertEqual(r.returncode,0,r.stderr)
        return r.stdout.strip()

    def value(self,name):return (self.lamps/name/'brightness').read_text().strip()

    def led(self, shell, *args, **extra):
        return self.script(shell, BIN/'mu300-led', *args, **dict(self.extra, **extra))

    def test_charger_gate_revokes_boot_frames_before_waiting_and_restarts_on_power_key(self):
        # Exercise the actual gate and LED controller together. Only input,
        # module loading and logging are stubs; no real hardware or shutdown.
        controller=self.tmp/'charge-led'
        controller.write_text((BIN/'mu300-led').read_text().replace('#!/bin/sh\n',
            '#!/bin/sh\n[ "${1:-}" != boot-worker ] || exit 0\n',1))
        controller.chmod(0o755)
        self.stub('mu300-device','echo u30air')
        self.stub('mu300-led','exec "$TEST_LED_CONTROLLER" "$@"')
        self.stub('extra-modules','exit 0')
        self.stub('logger','exit 0')
        self.stub('mu300-keys', '''
            state="$MU300_SYSROOT/run/mu300/led"
            [ -f "$MU300_SYSROOT/run/mu300/charging-boot" ] || exit 90
            [ ! -f "$state/.boot-chase" ] || exit 91
            "$MU300_BIN/mu300-led" boot-frame charging-test sc27xx:red && exit 92
            cp "$state/.battery-applied" "$STUBLOG/charging-lamp"
            for name in red green blue; do
                cat "$MU300_SYSROOT/sys/class/leds/sc27xx:$name/brightness"
            done > "$STUBLOG/charging-channels"
            exit 0
        ''')
        (self.root/'tmp').mkdir(exist_ok=True)
        (self.root/'proc/cmdline').write_text('androidboot.mode=charger\n')
        chosen=self.root/'proc/device-tree/chosen';chosen.mkdir(parents=True)
        (chosen/'bootargs').write_bytes(b'androidboot.mode=charger\0')
        state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
        for shell in self.each_shell():
            for cap,status,marker,channels in (
                (5,'Charging','red:breathe:1','255\n0\n0\n'),
                (49,'Charging','blue:breathe:1','0\n0\n255\n'),
                (50,'Charging','white:breathe:1','0\n255\n0\n'),
                (100,'Full','white:steady:1','0\n255\n0\n')):
                (self.battery/'capacity').write_text(str(cap)+'\n')
                (self.battery/'status').write_text(status+'\n')
                (state/'.boot-chase').write_text('charging-test\n')
                result=self.script(shell,BIN/'mu300-charge-mode',MU300_SYSROOT=self.root,
                    MU300_BIN=self.stubs,TEST_LED_CONTROLLER=controller)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((self.tmp/'charging-lamp').read_text().strip(),marker)
                self.assertEqual((self.tmp/'charging-channels').read_text(),channels)
                self.assertFalse((self.root/'run/mu300/charging-boot').exists())
                # The power-key handoff may animate normal boot again, but
                # frames from the old preinit owner remain rejected.
                self.assertNotEqual((state/'.boot-chase').read_text().strip(),'charging-test')
                self.assertEqual(self.led(shell,'boot-frame','charging-test','sc27xx:blue').returncode,1)
                (state/'.boot-chase').unlink()

    def test_wifi_event_cannot_acknowledge_an_unapplied_battery_color(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led'
            (self.battery/'capacity').write_text('49\n')
            self.apply(shell,'battery')
            self.assertEqual(self.value('sc27xx:blue'),'255')
            (self.battery/'capacity').write_text('50\n')
            self.apply(shell,'wifi-on')
            self.assertEqual(self.value('sc27xx:blue'),'255')
            self.assertIn('battery blue:breathe:1\n',(state/'.tick-hint').read_text())
            self.assertEqual(self.led(shell,'tick').returncode,0)
            self.assertEqual(self.value('sc27xx:green'),'255')
            self.assertIn('battery white:breathe:1\n',(state/'.tick-hint').read_text())

    def test_sleep_clears_physical_wifi_without_a_desired_state_record(self):
        for shell in self.each_shell():
            (self.battery/'status').write_text('Discharging\n')
            self.assertEqual(self.led(shell,'wake',TEST_TIMEOUT='60').returncode,0)
            state=self.root/'run/mu300/led'
            for lamp in ('zte-ldo1','zte-ldo2'):
                (state/lamp).unlink(missing_ok=True)
                (self.lamps/lamp/'brightness').write_text('1\n')
            (self.root/'proc/uptime').write_text('161.00 0.00\n')
            self.assertEqual(self.led(shell,'tick',TEST_TIMEOUT='60').returncode,0)
            self.assertEqual(self.value('zte-ldo1'),'0')
            self.assertEqual(self.value('zte-ldo2'),'0')
            # A late hardware residue must also be cleared after the lease was consumed.
            (self.lamps/'zte-ldo1'/'brightness').write_text('1\n')
            self.assertEqual(self.led(shell,'tick',TEST_TIMEOUT='60').returncode,0)
            self.assertEqual(self.value('zte-ldo1'),'0')
            (self.root/'proc/uptime').write_text('100.00 0.00\n')

    def test_battery_event_after_deadline_preserves_pending_group_sleep(self):
        for shell in self.each_shell():
            (self.battery/'status').write_text('Discharging\n')
            (self.root/'proc/uptime').write_text('100.00 0.00\n')
            self.assertEqual(self.led(shell,'wake',TEST_TIMEOUT='60').returncode,0)
            self.apply(shell,'wifi-on',TEST_TIMEOUT='60')
            self.assertEqual(self.value('zte-ldo1'),'1')
            (self.root/'proc/uptime').write_text('161.00 0.00\n')
            self.apply(shell,'battery',TEST_TIMEOUT='60')
            # Battery-only reconciliation has not touched the Wi-Fi lamp.
            # Its hint must keep the expired lease due, not acknowledge it.
            self.assertEqual(self.value('zte-ldo1'),'1')
            self.assertIn('due 160\n',(self.root/'run/mu300/led/.tick-hint').read_text())
            self.assertEqual(self.led(shell,'tick',TEST_TIMEOUT='60').returncode,0)
            self.assertTrue(all(self.value(n)=='0' for n in ('zte-ldo1','zte-ldo2','sc27xx:green')))

    def test_shutdown_breathes_red_and_late_events_cannot_overwrite_it(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
            self.apply(shell,'wifi-on')
            (state/'.boot-chase').write_text('old-boot\n')
            lamp=self.lamps/'sc27xx:red'
            (lamp/'trigger').write_text('[none] timer pattern\n')
            (lamp/'hw_pattern').write_text('');(lamp/'repeat').write_text('0\n')
            result=self.led(shell,'shutdown',TEST_BRIGHTNESS='40')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((state/'.shutdown').exists())
            self.assertFalse((state/'.boot-chase').exists())
            self.assertEqual((lamp/'trigger').read_text().strip(),'pattern')
            self.assertEqual((lamp/'hw_pattern').read_text().strip(),'0 625 40 750 0 625 0 250')
            before=(lamp/'hw_pattern').stat().st_mtime_ns
            for event in (('tick',),('battery',),('wifi','on'),('wake',),('sleep',),('boot','on'),('alarm','on'),('shutdown',)):
                result=self.led(shell,*event)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((lamp/'hw_pattern').stat().st_mtime_ns,before,event)
                self.assertTrue(all(self.value(n)=='0' for n in ('zte-ldo0','zte-ldo1','zte-ldo2','sc27xx:green','sc27xx:blue')))
            self.assertFalse((self.root/'run/mu300/led-siren.pid').exists())
            self.assertEqual(self.led(shell,'boot-frame','old-boot','sc27xx:blue').returncode,1)
            self.assertEqual(self.led(shell,'shutdown','off').returncode,0)
            self.assertFalse((state/'.shutdown').exists())
            self.assertEqual(self.value('sc27xx:green'),'255')
            self.assertEqual(self.value('zte-ldo1'),'1')

    def test_tick_hint_tracks_timeout_charge_bucket_and_unplug(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led'
            for cap,color in ((9,'red'),(10,'blue'),(49,'blue'),(50,'white')):
                (self.battery/'capacity').write_text(str(cap)+'\n')
                result=self.led(shell,'tick',TEST_TIMEOUT='60')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((state/'.tick-hint').read_text(),
                                 f'due 700\nbattery {color}:breathe:1\n')
            charger=self.battery.parent/'bq256xx-charger';charger.mkdir(exist_ok=True)
            (charger/'online').write_text('0\n')  # FGU may still say Charging after unplug.
            (state/'.awake-until').write_text('160\n')
            result=self.led(shell,'tick',TEST_TIMEOUT='60')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((state/'.tick-hint').read_text(),'due 160\nbattery white:steady:0\n')
            (self.root/'proc/uptime').write_text('161.00 999.00\n')
            self.assertEqual(self.led(shell,'tick',TEST_TIMEOUT='60').returncode,0)
            self.assertEqual((state/'.tick-hint').read_text(),'due 761\nbattery white:steady:0\n')
            self.assertTrue(all(self.value('sc27xx:'+c)=='0' for c in ('red','green','blue')))
            charger.joinpath('online').unlink();charger.rmdir()
            (self.root/'proc/uptime').write_text('100.00 999.00\n')

    def test_boot_frames_survive_power_refresh_and_leave_other_lamps_independent(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
            token=state/'.boot-chase';token.write_text('test-owner\n')
            try:
                for channel in ('red','green','blue'):
                    r=self.led(shell,'boot-frame','test-owner','sc27xx:'+channel,TEST_BRIGHTNESS='40')
                    self.assertEqual(r.returncode,0,r.stderr)
                    self.apply(shell,'battery');self.apply(shell,'refresh');self.apply(shell,'wifi-on')
                    self.assertEqual([self.value('sc27xx:'+c) for c in ('red','green','blue')],
                                     ['40' if c==channel else '0' for c in ('red','green','blue')])
                    self.assertEqual(self.value('zte-ldo1'),'1')
                self.apply(shell,'online-5g')
                self.assertFalse(token.exists())
                self.assertEqual(self.value('sc27xx:green'),'255')
                self.assertEqual(self.value('sc27xx:blue'),'0')
                self.assertEqual(self.value('zte-ldo0'),'1')
                r=self.led(shell,'boot-frame','test-owner','sc27xx:red')
                self.assertEqual(r.returncode,1)
                self.assertEqual(self.value('sc27xx:red'),'0')
            finally:
                token.unlink(missing_ok=True)

    def test_boot_respects_disabled_battery_and_rejects_old_worker_token(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
            token=state/'.boot-chase';token.write_text('new-owner\n')
            try:
                r=self.led(shell,'boot-frame','old-owner','sc27xx:red')
                self.assertEqual(r.returncode,1)
                r=self.led(shell,'boot-frame','new-owner','sc27xx:red',TEST_BATTERY='0')
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertTrue(all(self.value('sc27xx:'+c)=='0' for c in ('red','green','blue')))
            finally:
                token.unlink(missing_ok=True)

    def test_battery_only_boot_lights_first_frame_before_worker_runs(self):
        # Stop the worker at its entry so only the boot command can draw.
        # This reproduces preinit before power-on has established a wake lease.
        controller=self.tmp/'boot-led'
        source=(BIN/'mu300-led').read_text()
        controller.write_text(source.replace('#!/bin/sh\n',
            '#!/bin/sh\n[ "${1:-}" != boot-worker ] || exit 0\n',1))
        controller.chmod(0o755)
        (self.battery/'status').write_text('Discharging\n')
        state=self.root/'run/mu300/led'
        for shell in self.each_shell():
            for enabled in ('1','0'):
                state.mkdir(exist_ok=True)
                for name in ('.boot-chase','.awake-until'):
                    (state/name).unlink(missing_ok=True)
                (self.root/'proc/uptime').write_text('100.00 999.00\n')
                extra=dict(self.extra,TEST_TIMEOUT='60',TEST_BATTERY=enabled,TEST_BRIGHTNESS='40')
                result=self.script(shell,controller,'boot','on',**extra)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual([self.value('sc27xx:'+c) for c in ('red','green','blue')],
                                 ['40' if enabled=='1' else '0','0','0'])
                self.assertEqual((state/'.awake-until').read_text().strip(),'160')
                # Repeated boot notifications must not reset a user's timeout.
                token=(state/'.boot-chase').read_text()
                (self.root/'proc/uptime').write_text('130.00 999.00\n')
                result=self.script(shell,controller,'boot','on',**extra)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((state/'.boot-chase').read_text(),token)
                self.assertEqual((state/'.awake-until').read_text().strip(),'160')
                (state/'.boot-chase').unlink()

    def test_boot_worker_exits_on_handoff_and_cannot_outlive_its_token(self):
        if not shutil.which('busybox'): self.skipTest('bundled BusyBox applets required')
        for shell in self.each_shell():
            state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
            token=state/'.boot-chase';token.write_text('worker-test\n')
            proc=subprocess.Popen(shell+[str(BIN/'mu300-led'),'boot-worker','worker-test'],
                                  env=self.env(**dict(self.extra,MU300_BUSYBOX=shutil.which('busybox'))),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                import time
                time.sleep(.4)
                self.assertIsNone(proc.poll())
                self.apply(shell,'offline')
                _,err=proc.communicate(timeout=5)
                self.assertEqual(proc.returncode,0,err)
                self.assertEqual(self.value('sc27xx:green'),'255')
                self.assertEqual(self.value('sc27xx:red'),'0')
            finally:
                token.unlink(missing_ok=True)
                if proc.poll() is None: proc.kill();proc.communicate()

    def test_thermal_owner_prevents_boot_start_and_frames(self):
        for shell in self.each_shell():
            state=self.root/'run/mu300/led';state.mkdir(exist_ok=True)
            siren=self.root/'run/mu300/led-siren.pid';siren.write_text(str(os.getpid())+'\n')
            token=state/'.boot-chase'
            try:
                r=self.led(shell,'boot','on');self.assertEqual(r.returncode,0,r.stderr)
                self.assertFalse(token.exists())
                token.write_text('thermal-test\n')
                r=self.led(shell,'boot-frame','thermal-test','sc27xx:red')
                self.assertEqual(r.returncode,1)
            finally:
                token.unlink(missing_ok=True);siren.unlink(missing_ok=True)

    def test_events_do_not_confuse_network_with_battery_and_wifi(self):
        for shell in self.each_shell():
            self.apply(shell,'online-5g');self.apply(shell,'wifi-on')
            self.assertEqual([self.value(n) for n in ['net_blue','zte-ldo0','zte-ldo1','zte-ldo2']],['0','1','1','0'])
            self.assertEqual(self.value('sc27xx:green'),'255');self.assertEqual(self.value('sc27xx:blue'),'0')
            self.apply(shell,'online-4g')
            self.assertEqual(self.value('net_blue'),'1');self.assertEqual(self.value('zte-ldo0'),'0')
            self.assertEqual(self.value('zte-ldo1'),'1');self.assertEqual(self.value('sc27xx:green'),'255')
            self.apply(shell,'offline');self.assertEqual(self.value('keyboard-backlight'),'1')
            self.apply(shell,'wifi-off');self.assertEqual(self.value('zte-ldo1'),'0')

    def test_controls_do_not_erase_desired_state(self):
        for shell in self.each_shell():
            self.apply(shell,'online-5g');self.apply(shell,'wifi-on')
            self.apply(shell,'refresh',TEST_SIGNAL='0',TEST_WIFI='0',TEST_BATTERY='0')
            self.assertTrue(all(self.value(n)=='0' for n in ['zte-ldo0','zte-ldo1','sc27xx:green']))
            self.apply(shell,'refresh',TEST_BRIGHTNESS='40')
            self.assertEqual(self.value('zte-ldo0'),'1');self.assertEqual(self.value('zte-ldo1'),'1');self.assertEqual(self.value('sc27xx:green'),'40')

    def test_battery_states_and_timeout(self):
        for shell in self.each_shell():
            (self.battery/'status').write_text('Discharging\n');(self.battery/'capacity').write_text('10\n')
            self.apply(shell,'battery');self.assertEqual(self.value('sc27xx:blue'),'255')
            self.assertEqual(self.value('sc27xx:red'),'0')
            (self.battery/'status').write_text('Full\n');self.apply(shell,'battery')
            self.assertEqual(self.value('sc27xx:green'),'255');self.assertEqual(self.value('sc27xx:red'),'0')
            (self.battery/'status').write_text('Discharging\n')
            self.apply(shell,'online-5g',TEST_TIMEOUT='60')
            self.assertEqual(self.value('zte-ldo0'),'0')
            r=self.script(shell,BIN/'mu300-led','wake',**dict(self.extra,TEST_TIMEOUT='60'));self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(self.value('zte-ldo0'),'1')
            self.apply(shell,'current',TEST_TIMEOUT='60');self.assertEqual(self.value('zte-ldo0'),'1')
            self.script(shell,BIN/'mu300-led','sleep',**dict(self.extra,TEST_TIMEOUT='60'));self.assertEqual(self.value('zte-ldo0'),'0')

    def test_power_holds_lamps_and_unplug_restarts_timeout(self):
        for shell in self.each_shell():
            online=self.root/'sys/class/power_supply/bq256xx-charger/online';online.parent.mkdir(exist_ok=True);online.write_text('1\n')
            (self.battery/'status').write_text('Charging\n')
            self.apply(shell,'online-5g',TEST_TIMEOUT='60');self.apply(shell,'wifi-on',TEST_TIMEOUT='60')
            (self.root/'proc/uptime').write_text('10000.00 0.00\n')
            self.script(shell,BIN/'mu300-led','sleep','--if-due',**dict(self.extra,TEST_TIMEOUT='60'))
            self.assertEqual(self.value('zte-ldo0'),'1');self.assertEqual(self.value('zte-ldo1'),'1')
            online.write_text('0\n');(self.battery/'status').write_text('Discharging\n')
            self.apply(shell,'battery',TEST_TIMEOUT='60')
            self.assertEqual((self.root/'run/mu300/led/.awake-until').read_text().strip(),'10060')
            (self.root/'proc/uptime').write_text('10061.00 0.00\n')
            self.script(shell,BIN/'mu300-led','sleep','--if-due',**dict(self.extra,TEST_TIMEOUT='60'))
            self.assertEqual(self.value('zte-ldo0'),'0');self.assertEqual(self.value('zte-ldo1'),'0')
            self.assertTrue(all(self.value(n)=='0' for n in ['sc27xx:red','sc27xx:green','sc27xx:blue']))
            online.write_text('1\n');(self.battery/'status').write_text('Charging\n')
            self.apply(shell,'battery',TEST_TIMEOUT='60')
            self.assertEqual(self.value('zte-ldo0'),'1');self.assertEqual(self.value('zte-ldo1'),'1')

    def test_charging_uses_hardware_rise_hold_fall_rest(self):
        for shell in self.each_shell():
            for channel in ('blue','green'):
                lamp=self.lamps/('sc27xx:'+channel)
                (lamp/'trigger').write_text('[none] timer pattern\n');(lamp/'hw_pattern').write_text('');(lamp/'repeat').write_text('0\n')
            for cap, channel, other in [(49,'blue','green'),(50,'green','blue'),(100,'green','blue')]:
                (self.battery/'capacity').write_text(str(cap)+'\n')
                # Real sysfs retains the list of supported triggers after a
                # write; ordinary fixture files must restore that list.
                for name in ('blue','green'):
                    (self.lamps/('sc27xx:'+name)/'trigger').write_text('[none] timer pattern\n')
                self.apply(shell,'battery',TEST_BRIGHTNESS='40')
                lamp=self.lamps/('sc27xx:'+channel)
                self.assertEqual((lamp/'trigger').read_text().strip(),'pattern')
                self.assertEqual((lamp/'hw_pattern').read_text().strip(),'0 625 40 750 0 625 0 250')
                self.assertEqual((lamp/'repeat').read_text().strip(),'-1')
                self.assertEqual(self.value('sc27xx:'+other),'0')
                self.assertFalse((lamp/'delay_on').exists())
            (self.battery/'status').write_text('Full\n');self.apply(shell,'battery')
            self.assertEqual((self.lamps/'sc27xx:green/trigger').read_text().strip(),'none')
            self.assertEqual(self.value('sc27xx:green'),'255');self.assertEqual(self.value('sc27xx:blue'),'0')
            (self.battery/'status').write_text('Charging\n')

    def test_low_charge_breathing_boundaries_and_unplug(self):
        for shell in self.each_shell():
            online=self.root/'sys/class/power_supply/bq256xx-charger/online'
            online.parent.mkdir(exist_ok=True);online.write_text('1\n')
            (self.battery/'status').write_text('Charging\n')
            for cap, channel in [(0,'red'),(9,'red'),(10,'blue'),(49,'blue'),(50,'green'),(9,'red')]:
                for name in ('red','green','blue'):
                    lamp=self.lamps/('sc27xx:'+name)
                    (lamp/'trigger').write_text('[none] timer pattern\n')
                    (lamp/'hw_pattern').write_text('');(lamp/'repeat').write_text('0\n')
                (self.battery/'capacity').write_text(str(cap)+'\n')
                self.apply(shell,'battery',TEST_BRIGHTNESS='40')
                state=self.root/'run/mu300/led'
                self.assertEqual((state/'.battery-breathe').read_text().strip(),'sc27xx:'+channel)
                lamp=self.lamps/('sc27xx:'+channel)
                self.assertEqual((lamp/'trigger').read_text().strip(),'pattern')
                self.assertEqual((lamp/'hw_pattern').read_text().strip(),'0 625 40 750 0 625 0 250')
                for other in {'red','green','blue'}-{channel}:
                    self.assertEqual(self.value('sc27xx:'+other),'0')
            self.apply(shell,'refresh',TEST_BATTERY='0')
            self.assertTrue(all(self.value('sc27xx:'+c)=='0' for c in ('red','green','blue')))
            # Unplug stops breathing, but low charge remains red.
            online.write_text('0\n')
            self.apply(shell,'battery')
            self.assertFalse((state/'.battery-breathe').exists())
            self.assertEqual(self.value('sc27xx:red'),'255')
            self.assertEqual(self.value('sc27xx:blue'),'0')

    def test_remaining_charge_color_boundary_never_changes_timeout(self):
        for shell in self.each_shell():
            (self.battery/'status').write_text('Discharging\n')
            self.script(shell,BIN/'mu300-led','wake',**dict(self.extra,TEST_TIMEOUT='60'))
            deadline=self.root/'run/mu300/led/.awake-until'
            before=deadline.read_text()
            for cap, channel in [(0,'red'),(9,'red'),(10,'blue'),(15,'blue'),(49,'blue'),(50,'green'),(51,'green'),(100,'green')]:
                (self.battery/'capacity').write_text(str(cap)+'\n')
                self.apply(shell,'battery',TEST_TIMEOUT='60')
                self.assertEqual(self.value('sc27xx:'+channel),'255')
                for other in {'red','green','blue'}-{channel}:
                    self.assertEqual(self.value('sc27xx:'+other),'0')
                self.assertFalse((self.root/'run/mu300/led/.battery-breathe').exists())
                self.assertEqual(deadline.read_text(),before)

    def test_unplug_ignores_stale_fgu_charging_and_stays_dark_after_events(self):
        for shell in self.each_shell():
            online=self.root/'sys/class/power_supply/bq256xx-charger/online'
            online.parent.mkdir(exist_ok=True);online.write_text('1\n')
            (self.root/'proc/uptime').write_text('100.00 0.00\n')
            self.apply(shell,'online-5g',TEST_TIMEOUT='60')
            self.apply(shell,'wifi-on',TEST_TIMEOUT='60')
            online.write_text('0\n') # FGU deliberately still reports Charging.
            self.apply(shell,'battery',TEST_TIMEOUT='60')
            self.assertEqual(self.value('sc27xx:blue'),'0')
            self.assertEqual(self.value('sc27xx:green'),'255')
            self.assertFalse((self.root/'run/mu300/led/.battery-breathe').exists())
            (self.root/'proc/uptime').write_text('161.00 0.00\n')
            self.script(shell,BIN/'mu300-led','sleep','--if-due',**dict(self.extra,TEST_TIMEOUT='60'))
            self.apply(shell,'refresh',TEST_TIMEOUT='60')
            self.assertTrue(all(self.value(n)=='0' for n in ['zte-ldo0','zte-ldo1','sc27xx:red','sc27xx:green','sc27xx:blue']))

    def test_queued_power_event_reads_online_after_timeout_lock(self):
        import fcntl,time
        for shell in self.each_shell():
            online=self.root/'sys/class/power_supply/bq256xx-charger/online'
            online.parent.mkdir(exist_ok=True);online.write_text('1\n')
            self.apply(shell,'online-5g',TEST_TIMEOUT='60')
            state=self.root/'run/mu300/led'
            with (state/'.lock').open('w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                proc=subprocess.Popen(shell+[str(BIN/'mu300-led'),'battery'],env=self.env(**dict(self.extra,TEST_TIMEOUT='60')),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    time.sleep(.1)
                    self.assertIsNone(proc.poll(),'battery event bypassed shared LED lock')
                    online.write_text('0\n')
                    (state/'.external-power').write_text('0\n')
                    (state/'.awake-until').unlink(missing_ok=True)
                    fcntl.flock(lock,fcntl.LOCK_UN)
                    out,err=proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode,0,err)
                finally:
                    if proc.poll() is None:proc.kill();proc.communicate()
            self.assertTrue(all(self.value(n)=='0' for n in ['sc27xx:red','sc27xx:green','sc27xx:blue']))

    def test_steady_to_breathing_stops_output_but_refresh_does_not_restart_curve(self):
        for shell in self.each_shell():
            lamp=self.lamps/'sc27xx:green'
            (lamp/'trigger').write_text('[none] timer pattern\n')
            (lamp/'brightness').write_text('255\n')
            (lamp/'hw_pattern').write_text('')
            (lamp/'repeat').write_text('0\n')
            self.apply(shell,'battery')
            # sysfs pattern_set supplies the new peak; the plain-file fixture
            # captures the required stop of the previous steady output.
            self.assertEqual((lamp/'brightness').read_text().strip(),'0')
            self.assertEqual((lamp/'hw_pattern').read_text().strip(),'0 625 255 750 0 625 0 250')
            # Reconcile a running hardware curve without restarting its phase.
            (lamp/'trigger').write_text('none timer [pattern]\n')
            (lamp/'brightness').write_text('255\n')
            before=(lamp/'hw_pattern').stat().st_mtime_ns
            self.apply(shell,'battery')
            self.assertEqual((lamp/'brightness').read_text().strip(),'255')
            self.assertEqual((lamp/'hw_pattern').stat().st_mtime_ns,before)
