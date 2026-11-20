"""Execute MU300's actual key dispatch block with deterministic command results."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
SOURCE=TOP/'rootfs/overlay/opt/mu300/bin/mu300-sysd'


@unittest.skipUnless(os.name=='posix' and os.environ.get('MU300_TEST_RUNTIME_IMAGE'),
                     'select a prepared ARM64 OpenWrt image with ucode/socket via MU300_TEST_RUNTIME_IMAGE')
class ButtonEvents(unittest.TestCase):
    def test_entire_service_compiles_with_target_ucode(self):
        result=subprocess.run(['docker','run','--rm','--pull','never','--platform','linux/arm64','--network','none',
            '-v',str(SOURCE)+':/service.uc:ro','--entrypoint','/usr/bin/ucode',
            os.environ['MU300_TEST_RUNTIME_IMAGE'],'-c','/service.uc','-o','/tmp/service.ucb'],
            capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_double_settings_use_real_uci_and_follow_saved_changes(self):
        source=SOURCE.read_text()
        block=source[source.index('let button_config = null;'):source.index('let button_clicks = {};')]
        prefix="""
import {writefile} from 'fs';
const R='',KEY_POWER=116;
let model='u30air';
function button_model(){return model;}
function check(ok,message){if(!ok)die(message);}
"""
        suffix="""
check(!button_double_enabled(138),'explicit none was active');
check(button_double_enabled(116),'configured power double missing');
model='v50';check(!button_double_enabled(464),'V50 default should be inactive');
model='f50pro';check(button_double_enabled(102),'F50 Pro action missing');
writefile('/etc/config/unisoc_modem',"config modem main\\n option u30air_button_double default\\n option f50pro_button_double none\\n");
model='u30air';check(button_double_enabled(138),'U30 Air default band action missing');
check(!button_double_enabled(116),'missing power double should be inactive');
model='f50pro';check(!button_double_enabled(102),'saved setting change was cached');
print('PASS real UCI: model defaults, configured actions and live settings changes');
"""
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);config=base/'config';config.mkdir()
            (config/'unisoc_modem').write_text("config modem main\n option u30air_button_double none\n option u30air_button_power_double custom\n option v50_button_double default\n option f50pro_button_double data-switch\n")
            test=base/'real.uc';test.write_text(prefix+block+suffix)
            result=subprocess.run(['docker','run','--rm','--pull','never','--platform','linux/arm64','--network','none',
                '-v',str(config)+':/etc/config','-v',str(test)+':/test.uc:ro','--entrypoint','/usr/bin/ucode',
                os.environ['MU300_TEST_RUNTIME_IMAGE'],'/test.uc'],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_default_custom_and_power_hold_use_the_original_owner(self):
        source=SOURCE.read_text()
        block=source[source.index('const KEY_POWER'):source.index('// The existing LED helper owns')]
        prefix="""
const R='', BIN='/opt/mu300/bin',parts={};
let calls=[],custom_result=0,timers=[],openwrt=true,settings={};
const uloop={timer:(ms,cb)=>{let t={ms:ms,run:cb,cancelled:false};t.cancel=()=>{t.cancelled=true;};push(timers,t);return t;}};
function flush(){let pending=timers;timers=[];for(let t in pending)if(!t.cancelled)t.run();}
function require(name){if(name!='uci')die('unexpected module');return {cursor:p=>({get:(config,section,key)=>settings[key],unload:()=>{}})};}
function num(k,v){return v;}
function readfile(p){return null;}
function exists(p){return openwrt&&p=='/etc/openwrt_release';}
function rd(p){return '10.00';}
function digits(s){return true;}
function deadline(s,p){return null;}
function idle_check(){}
function log(tag,text){}
function spawn(argv,cb){
 push(calls,argv);if(cb)cb(argv[0]=='/usr/libexec/unisoc-modem/buttons'?custom_result:0);
 return null;
}
"""
        suffix="""
function check(yes,msg){if(!yes)die(msg);}
key_model='u30air';press(KEY_WIFI,'short');
check(calls[0][0]=='mu300-led'&&calls[0][1]=='wake','single did not wake immediately');
check(length(filter(calls,x=>x[0]=='/usr/libexec/unisoc-modem/buttons'))==0,'single launched Python');
check(timers[length(timers)-1].ms==250,'wrong double window');
let before=length(calls);flush();check(length(calls)==before,'timer emitted a delayed single');
calls=[];press(KEY_WIFI,'short');press(KEY_WIFI,'short');
check(calls[0][0]=='mu300-led'&&calls[1][3]=='double','double lost immediate first single');
flush();calls=[];press(KEY_POWER,'short');
check(calls[0][0]=='mu300-led','power single delayed');
press(KEY_POWER,'long');
check(length(filter(calls,x=>x[0]=='poweroff'))==1,'power hold lost');
check(length(filter(calls,x=>x[0]=='mu300-led'&&x[1]=='shutdown'))==1,'shutdown indication lost');
check(length(filter(calls,x=>x[0]=='/usr/libexec/unisoc-modem/buttons'))==0,'power single or hold customized');
button_click_cancel();key_model='v50';calls=[];press(464,'short');
check(length(calls)==1&&calls[0][0]=='mu300-led','V50 short changed radio or launched Python');
press(464,'long');check(calls[1][3]=='long','V50 hold mapping lost');
button_click_cancel();key_model='u30air';calls=[];press(KEY_WIFI,'short');press(KEY_POWER,'short');
check(length(calls)==2&&calls[0][0]=='mu300-led'&&calls[1][0]=='mu300-led','key histories were mixed');
button_click_cancel();settings.u30air_button_double='none';settings.u30air_button_power_double='none';calls=[];
for(let i=0;i<8;i++)press(KEY_WIFI,'short');
check(length(calls)==8&&length(filter(calls,x=>x[0]=='mu300-led'))==8,'unmapped rapid singles swallowed');
check(button_clicks[KEY_WIFI]==null,'unmapped single armed a timer');
calls=[];press(KEY_POWER,'short');press(KEY_POWER,'short');
check(length(calls)==2&&calls[1][0]=='mu300-led','unmapped power repeats swallowed');
settings.u30air_button_double='data-switch';calls=[];press(KEY_WIFI,'short');
settings.u30air_button_double='none';press(KEY_WIFI,'short');
check(length(calls)==2&&calls[1][0]=='mu300-led','changing to none kept stale double history');
key_model='v50';button_click_cancel();calls=[];for(let i=0;i<8;i++)press(464,'short');
check(length(calls)==8,'V50 unmapped repeats swallowed');
button_click_cancel();openwrt=false;key_model='u30air';calls=[];press(KEY_WIFI,'short');press(KEY_WIFI,'short');
check(length(filter(calls,x=>x[0]=='/bin/sh'))==1,'non-OpenWrt double band action changed');
print('PASS immediate MU300 singles, independent keys, mapped doubles/holds and shutdown protection');
"""
        with tempfile.TemporaryDirectory() as folder:
            test=Path(folder)/'events.uc';test.write_text(prefix+block+suffix)
            result=subprocess.run(['docker','run','--rm','--pull','never','--platform','linux/arm64','--network','none',
                '-v',str(test)+':/test.uc:ro','--entrypoint','/usr/bin/ucode',
                os.environ['MU300_TEST_RUNTIME_IMAGE'],'/test.uc'],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
