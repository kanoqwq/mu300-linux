"""Production sysd with private sensors/helpers; no real device access."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import unittest

TOP=Path(__file__).resolve().parents[1]
SYSD=TOP/'rootfs/overlay/opt/mu300/bin/mu300-sysd'


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.root=self.base/'root';self.bin=self.base/'bin';self.bin.mkdir()
        (self.root/'proc').mkdir(parents=True);(self.root/'proc/uptime').symlink_to('/proc/uptime')
        (self.root/'run/mu300').mkdir(parents=True)
        (self.root/'run/mu300/device').write_text('f50')
        self.stub('mu300-device','''#!/bin/sh
echo f50
''')
        self.stub('mu300-led','''#!/bin/sh
exit 0
''')
        self.env=dict(os.environ,MU300_SYSROOT=str(self.root),MU300_RUN=str(self.root/'run'),MU300_BIN=str(self.bin),PATH=str(self.bin)+':'+os.environ['PATH'])

    def tearDown(self):self.tmp.cleanup()

    def stub(self,name,source):
        p=self.bin/name;p.write_text(source);p.chmod(0o755);return p

    def zone(self,temp):
        p=self.root/'sys/class/thermal/thermal_zone0';p.mkdir(parents=True,exist_ok=True)
        (p/'temp').write_text(str(temp));(p/'trip_point_0_temp').write_text('85000')

    def round(self,temp,expected):
        self.zone(temp)
        p=subprocess.run(['ucode',str(SYSD),'--round'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn(f'next look in {expected} s',p.stderr)

    def test_adaptive_thermal_limits_alarm_and_read_error(self):
        self.round(45000,25);self.round(10000,30);self.round(84000,5)
        self.round(86000,5)
        (self.root/'run/mu300/thermal-alarm').unlink()
        (self.root/'sys/module/mu300_thermal').mkdir(parents=True)
        self.round(45000,10)  # No native binding: guard remains the policy owner.
        p=self.root/'sys/class/thermal/thermal_zone0/temp';p.write_text('unavailable')
        bad=subprocess.run(['ucode',str(SYSD),'--round'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(bad.returncode,0)

    def test_led_deadline_does_not_fork_every_five_seconds(self):
        helper=self.stub('keys','''#!/usr/bin/python3
import ctypes,time
ctypes.CDLL(None).prctl(1,15)  # Match the real helper's parent-death signal.
time.sleep(60)
''')
        self.stub('mu300-led','''#!/usr/bin/python3
import os,time
from pathlib import Path
root=Path(os.environ['MU300_SYSROOT'])
p=root/'run/mu300/led';p.mkdir(exist_ok=True)
with (root/'calls').open('a') as f:f.write('tick\\n')
now=int(float(Path('/proc/uptime').read_text().split()[0]))
(p/'.tick-hint').write_text('due '+str(now+600)+'\\nbattery -\\n')
''')
        env=dict(self.env,MU300_KEYS_BIN=str(helper),MU300_SYSD_PARTS='led')
        daemon=subprocess.Popen(['ucode',str(SYSD)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+9
            while not (self.root/'calls').exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue((self.root/'calls').exists())
            time.sleep(6)
            self.assertEqual((self.root/'calls').read_text().splitlines(),['tick'])
            self.assertIsNone(daemon.poll())
        finally:
            daemon.send_signal(signal.SIGTERM);daemon.communicate(timeout=5)

    def test_child_actions_do_not_inherit_relaxed_timer_slack(self):
        helper=self.stub('keys','''#!/usr/bin/python3
import time
print("116 short",flush=True)
time.sleep(.2)
''')
        self.stub('mu300-led','''#!/usr/bin/python3
import os
from pathlib import Path
Path(os.environ['SLACK_RESULT']).write_text(Path('/proc/self/timerslack_ns').read_text())
''')
        env=dict(self.env,MU300_SYSROOT='',MU300_KEYS_BIN=str(helper),MU300_SYSD_PARTS='keys',MU300_SYSD_ONCE='1',SLACK_RESULT=str(self.base/'slack'))
        p=subprocess.run(['ucode',str(SYSD)],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertNotEqual(int((self.base/'slack').read_text()),500000000)

    def test_late_battery_event_cannot_hide_expired_wifi_sleep_from_sysd(self):
        led=TOP/'rootfs/overlay/opt/mu300/bin/mu300-led'
        realbin=led.parent
        (self.root/'proc/uptime').unlink()
        (self.root/'proc/uptime').write_text('100.00 0.00\n')
        (self.root/'run/mu300/device').write_text('u30air\n')
        for name in ('net_blue','keyboard-backlight','zte-ldo0','zte-ldo1','zte-ldo2','sc27xx:red','sc27xx:green','sc27xx:blue'):
            lamp=self.root/'sys/class/leds'/name;lamp.mkdir(parents=True)
            (lamp/'brightness').write_text('0\n');(lamp/'max_brightness').write_text('255\n')
            (lamp/'trigger').write_text('[none] timer pattern\n')
        battery=self.root/'sys/class/power_supply/sc27xx-fgu';battery.mkdir(parents=True)
        (battery/'capacity').write_text('65\n');(battery/'status').write_text('Discharging\n')
        charger=battery.parent/'bq256xx-charger';charger.mkdir();(charger/'online').write_text('0\n')
        self.stub('uci', '#!/bin/sh\n[ "$3" != system.mu300_leds.timeout ] || echo 60\n')
        self.stub('iw', '#!/bin/sh\necho "channel 36 (5180 MHz)"\n')
        # Execute the real detector from a private executable fixture. A
        # read-only Windows Docker bind need not preserve source execute bits.
        self.stub('mu300-device',(realbin/'mu300-device').read_text())
        env=dict(self.env,REAL_LED=str(led))
        probe=subprocess.run(['sh',str(self.bin/'mu300-device')],env=env,capture_output=True,text=True)
        self.assertEqual(probe.stdout.strip(),'u30air',probe.stderr)
        for args in (('wake',),('wifi','on')):
            initial=subprocess.run(['sh','-x',str(led),*args],env=env,check=True,capture_output=True,text=True)
        self.assertNotEqual((self.root/'sys/class/leds/zte-ldo1/brightness').read_text().strip(),'0',initial.stderr)
        self.stub('mu300-led', '''#!/bin/sh
printf '%s\\n' "$1" >> "$MU300_SYSROOT/calls"
sh "$REAL_LED" "$@"
result=$?
[ "$1" != tick ] || touch "$MU300_SYSROOT/tick-done"
exit "$result"
''')
        helper=self.stub('keys', '''#!/usr/bin/python3
import ctypes,os,subprocess,time
from pathlib import Path
ctypes.CDLL(None).prctl(1,15)
root=Path(os.environ['MU300_SYSROOT'])
while not (root/'tick-done').exists():time.sleep(.02)
time.sleep(.3)
root.joinpath('proc/uptime').write_text('161.00 0.00\\n')
subprocess.run(['sh',os.environ['REAL_LED'],'battery'],check=True)
print('uevent power_supply',flush=True)
time.sleep(60)
''')
        env.update(MU300_KEYS_BIN=str(helper),MU300_SYSD_PARTS='led')
        daemon=subprocess.Popen(['ucode',str(SYSD)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        wifi=self.root/'sys/class/leds/zte-ldo1/brightness'
        try:
            until=time.monotonic()+10
            while time.monotonic()<until and wifi.read_text().strip()!='0':time.sleep(.05)
            self.assertEqual(wifi.read_text().strip(),'0','late battery event postponed whole-board sleep')
            self.assertEqual((self.root/'calls').read_text().splitlines(),['tick','tick'])
            time.sleep(.3)
            self.assertEqual((self.root/'calls').read_text().splitlines(),['tick','tick'])
        finally:
            daemon.send_signal(signal.SIGTERM);daemon.communicate(timeout=5)

    def test_color_change_during_tick_is_not_acknowledged_by_completion(self):
        psy=self.root/'sys/class/power_supply'
        (psy/'sc27xx-fgu').mkdir(parents=True)
        (psy/'bq256xx-charger').mkdir()
        (psy/'sc27xx-fgu/capacity').write_text('49\n')
        (psy/'sc27xx-fgu/status').write_text('Discharging\n')
        (psy/'bq256xx-charger/online').write_text('0\n')
        helper=self.stub('keys','''#!/usr/bin/python3
import ctypes,os,time
from pathlib import Path
ctypes.CDLL(None).prctl(1,15)
p=Path(os.environ['MU300_SYSROOT'])/'calls'
while not p.exists():time.sleep(.02)
time.sleep(.1)
print('uevent power_supply',flush=True)
time.sleep(60)
''')
        self.stub('mu300-led','''#!/usr/bin/python3
import os,time
from pathlib import Path
root=Path(os.environ['MU300_SYSROOT']);p=root/'run/mu300/led';p.mkdir(exist_ok=True)
cap=root/'sys/class/power_supply/sc27xx-fgu/capacity'
color='white' if int(cap.read_text())>=50 else 'blue'
now=int(float(Path('/proc/uptime').read_text().split()[0]))
(p/'.tick-hint').write_text('due '+str(now+600)+'\\nbattery '+color+':steady:0\\n')
calls=root/'calls'
first=not calls.exists()
with calls.open('a') as f:f.write(color+'\\n')
if first:cap.write_text('50\\n')
''')
        env=dict(self.env,MU300_KEYS_BIN=str(helper),MU300_SYSD_PARTS='led',MU300_SYSD_LOG=str(self.base/'sysd.log'))
        daemon=subprocess.Popen(['ucode',str(SYSD)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10  # Production's first LED check is at five seconds.
            while time.monotonic()<deadline:
                if (self.root/'calls').exists() and len((self.root/'calls').read_text().splitlines())>=2:break
                time.sleep(.05)
            self.assertEqual((self.root/'calls').read_text().splitlines(),['blue','white'])
            time.sleep(.7)
            self.assertEqual((self.root/'calls').read_text().splitlines(),['blue','white'])
        finally:
            daemon.send_signal(signal.SIGTERM);_,err=daemon.communicate(timeout=5)
            if (self.base/'sysd.log').exists():print((self.base/'sysd.log').read_text())
            if err:print(err.decode())


if __name__=='__main__':unittest.main()
