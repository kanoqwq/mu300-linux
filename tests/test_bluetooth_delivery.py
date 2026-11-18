"""Actual OTA introduction and next update of the native Bluetooth event bridge."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOP/'installer/tests'))
from test_ota import analyze, Patch, Engine
release=runpy.run_path(str(TOP/'tests/traffic_policy_delivery.py'))['release']
FILES=('etc/init.d/unisoc-bluetooth', 'usr/lib/unisoc-bluetooth/events.uc',
       'usr/share/rpcd/acl.d/unisoc-bluetooth.json',
       'www/cgi-bin/unisoc-bluetooth-events',
       'www/luci-static/resources/view/unisoc-bluetooth/settings.js')

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'actual POSIX OTA ownership')
class Delivery(unittest.TestCase):
    def test_bridge_introduction_and_next_update_preserve_pairing_and_config(self):
        current={n:(TOP/'openwrt/overlay'/n).read_bytes() for n in FILES}
        previous={}
        for n in FILES:
            result=subprocess.run(['git','-c','safe.directory='+str(TOP),'-C',str(TOP),
                                   'show','HEAD:openwrt/overlay/'+n],capture_output=True)
            if result.returncode==0:previous[n]=result.stdout
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);live=base/'live'
            saved={'etc/unisoc-bluetooth/config.json':b'{"autostart":true,"alias":"User gateway"}',
                   'etc/bluetooth/keys/device/info':b'pairing secret',
                   'etc/config/wireless':b'user AP password'}
            for name,data in dict(previous,**saved).items():
                p=live/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                if name.startswith(('etc/init.d/','www/cgi-bin/')):p.chmod(0o755)
            meta=live/'etc/mu300/release.json';meta.parent.mkdir(parents=True)
            meta.write_text(json.dumps(dict(profile='mu300',image_version='mu300-r1',kernel_release='7.2.8')))
            following=dict(current,**{'usr/lib/unisoc-bluetooth/events.uc':current['usr/lib/unisoc-bluetooth/events.uc']+b'\n// next release\n'})
            for number,files in ((1,previous),(2,current),(3,following)):
                release(base/str(number),'mu300-r'+str(number),files)
            for number in (1,2):
                output=base/(str(number)+'.ota');analyze(base/str(number),base/str(number+1),output)
                with Patch(output) as patch:Engine(live).apply(patch)
                self.assertEqual((live/'usr/lib/unisoc-bluetooth/events.uc').read_bytes(),
                                 current['usr/lib/unisoc-bluetooth/events.uc']+(b'\n// next release\n' if number==2 else b''))
                self.assertIn(b'events.uc',(live/'etc/init.d/unisoc-bluetooth').read_bytes())
                self.assertIn(b'410 Gone',(live/'www/cgi-bin/unisoc-bluetooth-events').read_bytes())
                for name,data in saved.items():self.assertEqual((live/name).read_bytes(),data)

if __name__=='__main__':unittest.main(verbosity=2)
