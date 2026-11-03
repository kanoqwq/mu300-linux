"""Real ucode battery history against synthetic sysfs; no hardware access."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

TOP = next(p for p in Path(__file__).resolve().parents if (p/"tools/build-release.py").is_file())
PLUGIN = TOP/'openwrt'/('luci-app-t551m' if (TOP/'openwrt/luci-app-t551m').is_dir() else 'luci-app-mu300')
FAMILY = 'e5' if PLUGIN.name == 'luci-app-t551m' else 'mu300'
LIB = PLUGIN/'root/usr/share/ucode/mu300/battery-history.uc'
DAEMON = PLUGIN/'root/usr/libexec/unisoc-modem/battery-history'



@unittest.skipUnless(os.name == 'posix', 'target ucode runs in the existing build container')
class BatteryHistory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bat = self.root/'sys/class/power_supply/battery'
        self.chg = self.root/'sys/class/power_supply/usb'
        self.bat.mkdir(parents=True); self.chg.mkdir()
        self.persist = 'etc/e5/battery' if FAMILY == 'e5' else 'etc/mu300/battery'
        (self.root/self.persist).mkdir(parents=True)
        profile = self.root/'usr/share/unisoc-battery-history/profile.json'
        profile.parent.mkdir(parents=True); profile.write_bytes((PLUGIN/'root/usr/share/unisoc-battery-history/profile.json').read_bytes())
        (self.root/'run').mkdir()
        (self.root/'run/mu300').mkdir();(self.root/'run/mu300/device').write_text('u30air')
        (self.root/'mounted').write_text('1')
        self.write(self.bat, type='Battery', present=1, capacity=75, current_now=-500000,
                   voltage_now=4000000, temp=300)
        self.write(self.chg, type='USB', online=0, status='Discharging')
        self.minute = int(time.time())//60*60

    def write(self, directory, **values):
        for key, value in values.items(): (directory/key).write_text(str(value))

    def records(self, entries):
        (self.root/self.persist/'battery.csv').write_text(''.join(
            f'{t},{p},{ma},{ma*4},4000,{s},300\n' for t,p,ma,s in entries))

    def run_ucode(self, script):
        command = ['docker','run','--rm','--platform','linux/arm64',
            '-v',str(LIB)+':/usr/share/ucode/mu300/battery-history.uc:ro',
            '-v',str(self.root)+':/fixture','-e','UNISOC_BATTERY_ROOT=/fixture',
            '--entrypoint','/usr/bin/ucode','mu300-openwrt-base:25.12.5','-e',
            "import * as b from '/usr/share/ucode/mu300/battery-history.uc'; b.init(); "+script]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def history(self, key='24h'):
        return self.run_ucode(f'printf("%J", b.history("{key}", 120));')

    def test_live_state_and_units_do_not_wait_for_history(self):
        data = self.history()
        self.assertTrue(data['present']); self.assertIsNone(data['first'])
        self.assertEqual(data['current']['pct'],75)
        self.assertEqual(data['current']['ma'],-500)
        self.assertEqual(data['current']['mw'],-2000)
        self.assertEqual(data['current']['mv'],4000)
        self.assertIsNone(data['estimate_s'])

    def test_absent_battery_is_not_exposed(self):
        self.write(self.bat,present=0)
        self.assertFalse(self.history()['present'])
        (self.bat/'present').unlink(); (self.bat/'type').unlink()
        self.assertFalse(self.history()['present'])

    def test_continuous_discharge_has_an_estimate(self):
        self.records([(self.minute-1200+i*60,80-i//4,-500,'D') for i in range(21)])
        data=self.history('full')
        self.assertEqual(data['estimate_s'],18000)
        self.assertGreater(data['stats']['on_battery_s'],1000)
        self.assertFalse((self.root/'mnt/data').exists())

    def test_charging_flat_gauge_and_short_runs_have_no_estimate(self):
        self.records([(self.minute-1200+i*60,75,-500,'D') for i in range(21)])
        self.assertIsNone(self.history()['estimate_s'])
        self.records([(self.minute-300+i*60,80-i,-500,'D') for i in range(6)])
        self.assertIsNone(self.history()['estimate_s'])
        self.write(self.chg,online=1,status='Charging')
        self.write(self.bat,current_now=1000000)
        data=self.history()
        self.assertIsNone(data['estimate_s']); self.assertEqual(data['current']['st'],'C')
        self.assertEqual(data['current']['mw'],4000)

    def test_shutdown_gap_is_blank_and_not_used_for_estimate(self):
        self.records([(self.minute-1200+i*60,80-i//4,-500,'D') for i in range(11)]+
                     [(self.minute-60,76,-500,'D'),(self.minute,75,-500,'D')])
        data=self.history('full')
        self.assertIsNone(data['estimate_s'])
        self.assertIn(None,data['pct'])
        self.assertEqual(data['stats']['on_battery_s'],13*60)

    def test_retention_reload_and_invalid_range(self):
        self.records([(self.minute-11*86400,100,-500,'D'),(self.minute-60,75,-500,'D')])
        data=self.history()
        self.assertEqual(data['first'],self.minute-60)
        self.assertEqual(len((self.root/self.persist/'battery.csv').read_text().splitlines()),1)
        self.assertEqual(self.history()['first'],data['first'])
        self.assertIn('error',self.history('1y'))

    def test_luci_reads_the_menu_with_its_actual_stream_parser(self):
        menu=PLUGIN/'root/usr/share/luci/menu.d/luci-app-mu300.json'
        script='import { open } from "fs"; let m=json(open("/menu.json","r")); printf("%J",m["admin/modem/battery"]);'
        result=subprocess.run(['docker','run','--rm','--platform','linux/arm64','-v',str(menu)+':/menu.json:ro',
            '--entrypoint','/usr/bin/ucode','mu300-openwrt-base:25.12.5','-e',script],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        entry=json.loads(result.stdout)
        self.assertEqual(entry['action']['path'],'mu300/battery')
        self.assertEqual(entry['depends']['fs'],{'/run/unisoc-battery-history/present':'file'})



    def test_charging_status_uses_the_family_owner(self):
        self.write(self.bat,status='Not charging')
        self.write(self.chg,online=1,status='Charging')
        paired=self.chg.with_name('bq256xx-charger');paired.mkdir()
        self.write(paired,type='USB',online=1,status='Charging')
        self.assertEqual(self.history()['current']['st'],'C' if FAMILY=='mu300' else 'P')

    def test_real_daemon_publishes_the_web_rpc(self):
        script = """mkdir -p /var/run; ubusd >/tmp/ubusd.log 2>&1 & broker=$!;
ucode /usr/libexec/unisoc-modem/battery-history >/tmp/history.log 2>&1 & worker=$!;
trap 'kill $worker $broker 2>/dev/null || true' EXIT;
sleep 2;
ubus call unisoc-battery-history history '{"range":"24h","points":120,"ubus_rpc_session":"fixture"}' || { cat /tmp/history.log >&2; exit 1; };
kill -TERM $worker; wait $worker
"""
        result=subprocess.run(['docker','run','--rm','--platform','linux/arm64',
            '-v',str(LIB)+':/usr/share/ucode/mu300/battery-history.uc:ro',
            '-v',str(DAEMON)+':/usr/libexec/unisoc-modem/battery-history:ro',
            '-v',str(self.root)+':/fixture','-e','UNISOC_BATTERY_ROOT=/fixture',
            '--entrypoint','/bin/sh','mu300-openwrt-base:25.12.5','-c',script],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        value=json.loads(result.stdout)
        self.assertTrue(value['present']);self.assertEqual(value['current']['pct'],75)
        self.assertTrue((self.root/'run/unisoc-battery-history/present').exists())

    def test_other_gauge_names_and_all_e5_profiles(self):
        original=self.bat
        for name in ('sc27xx-fgu','ump962x-fgu'):
            renamed=original.with_name(name);original.rename(renamed)
            self.assertTrue(self.history()['present'])
            self.assertEqual(self.history()['current']['ma'],-500)
            renamed.rename(original)
        if FAMILY == 'e5':
            p=self.root/'etc/e5/device-profile';p.parent.mkdir(parents=True,exist_ok=True)
            for profile in ('e5','t551m','m8','t512n','k1','yuanhang41s','lym10k','changxing60','y60'):
                p.write_text(profile);self.assertTrue(self.history()['present'],profile)

    def test_f50_v50_synthetic_gauges_are_not_batteries(self):
        if FAMILY != 'mu300':return
        for board in ('f50','v50'):
            (self.root/'run/mu300/device').write_text(board)
            self.assertFalse(self.history()['present'])

    def test_no_battery_startup_creates_no_instance_or_history(self):
        self.write(self.bat,present=0)
        import shutil
        shutil.rmtree(self.root/self.persist)
        service=PLUGIN/'root/etc/init.d/unisoc-battery-history'
        script='. /service; procd_open_instance() { echo SPAWNED; }; start_service'
        result=subprocess.run(['docker','run','--rm','--platform','linux/arm64',
            '-v',str(LIB)+':/usr/share/ucode/mu300/battery-history.uc:ro',
            '-v',str(DAEMON)+':/usr/libexec/unisoc-modem/battery-history:ro',
            '-v',str(service)+':/service:ro','-v',str(self.root)+':/fixture',
            '-e','UNISOC_BATTERY_ROOT=/fixture','--entrypoint','/bin/sh',
            'mu300-openwrt-base:25.12.5','-c',script],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('SPAWNED',result.stdout)
        self.assertFalse((self.root/self.persist).exists())
        self.assertFalse((self.root/'run/unisoc-battery-history/present').exists())
        if FAMILY == 'mu300':
            self.write(self.bat,present=1)
            for board in ('f50','v50'):
                (self.root/'run/mu300/device').write_text(board)
                again=subprocess.run(result.args,capture_output=True,text=True,timeout=30)
                self.assertEqual(again.returncode,0,again.stderr)
                self.assertNotIn('SPAWNED',again.stdout)
                self.assertFalse((self.root/self.persist).exists())

if __name__ == '__main__': unittest.main()
