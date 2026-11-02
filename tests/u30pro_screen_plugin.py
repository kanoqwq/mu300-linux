"""Exercise published screen-data add-on against the production state adapter.

Uses the existing prepared target root in an isolated container, fake ubus
inputs and a harmless command logger. Never executes mobile-data or an AT.
"""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
ROOT = Path('/home/kano/openwrt-build/package-work/u30pro-release/root')
PLUGIN = TOP/'work/u30pro-runtime/plugin-compat/data'


@unittest.skipUnless(os.name == 'posix' and ROOT.exists() and PLUGIN.exists(), 'prepared target root and reviewed upstream fixture')
class ScreenData(unittest.TestCase):
    def test_explicit_user_choice_survives_transient_disconnect_and_native_toggle(self):
        source = (TOP/'devices/u30pro/overlay/usr/sbin/u30pro-ui-feed').read_text()
        # Execute the actual production state projection, not a second implementation.
        body = source[source.index("    let data_iface="):source.index("    let data=")]
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='u30pro-plugin-') as folder:
            base = Path(folder)
            guest = '/tmp/' + base.name
            plugin = PLUGIN.read_text().replace('/run/u30pro-cellular/status.json', guest+'/status.json')
            (base/'data').write_text(plugin)
            (base/'u30pro-celld').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "'+guest+'/commands"\n')
            (base/'u30pro-celld').chmod(0o755)
            program = '''import * as real from 'fs';
const cases=[
 {manual:false,disabled:'0',up:true,autostart:true,want:true},
 {manual:false,disabled:'0',up:false,autostart:true,want:true},
 {manual:false,disabled:'0',up:false,autostart:false,want:false},
 {manual:true,disabled:'0',up:true,autostart:true,want:false},
 {manual:false,disabled:'1',up:false,autostart:true,want:false}
];
for(let c in cases){
 const fs={stat:p=>c.manual?{}:null,
   writefile:(p,s)=>real.writefile('GUEST/status.json',s),rename:()=>true};
 const config={get:(a,b,k)=>a=='network'?c.disabled:'customwan'};
 const view={wan:{up:c.up,autostart:c.autostart,device:'sipa_eth0',ip4:'10.0.0.2'}};
 BODY
 const state=json(real.readfile('GUEST/status.json'));
 if(state.desired_up!=c.want || state.connected!=c.up || state.interface!='sipa_eth0' || state.address!='10.0.0.2')die('state projection mismatch');
 if(system('PATH=GUEST:/usr/bin:/bin /bin/sh GUEST/data state >GUEST/result'))die('state command failed');
 if(trim(real.readfile('GUEST/result'))!=(c.want?'1':'0'))die('native plugin reads incorrect state');
 if(system('PATH=GUEST:/usr/bin:/bin /bin/sh GUEST/data toggle'))die('toggle failed');
 const commands=trim(real.readfile('GUEST/commands'));
 if(!match(commands,c.want?/--ctl disconnect$/:/--ctl reconnect$/))die('native plugin chose wrong action');
}
print('PASS native screen-data status/toggle: connected, temporary disconnect, manual disconnect, disabled WAN; no AT/hardware access\\n');
'''.replace('BODY', body).replace('GUEST', guest)
            (base/'test.uc').write_text(program)
            reply=subprocess.check_output(['docker','run','--rm','--platform','linux/arm64',
                '-v',str(ROOT)+':/target','--entrypoint','/bin/sh','mu300-openwrt-base:25.12.5',
                '-ec','chroot /target /usr/bin/ucode '+guest+'/test.uc'],text=True)
            self.assertIn('PASS native screen-data',reply)
            print(reply,end='')


if __name__=='__main__':unittest.main()
