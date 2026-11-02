import * as fs from 'fs';
import { cursor } from 'uci';
import * as native from 'mu300.wifi-native';
const directory='/tmp/u30pro-wifi-native-fixture';
let passed=0,calls=[],online=false;
function check(ok,message) {if(!ok)die(message);passed++;}
function fails(fn) {try{fn();}catch(e){passed++;return;}die('Expected rejection');}
fs.mkdir(directory,448);
fs.writefile(directory+'/wireless',"config wifi-device 'radio0'\noption type 'mac80211'\nconfig wifi-iface 'ap0'\noption mode 'ap'\noption device 'radio0'\noption ssid 'Preserve AP'\noption key 'unchanged-key'\n");
fs.writefile(directory+'/network',"config interface 'wwan'\noption proto 'dhcp'\n");
fs.writefile(directory+'/unisoc_modem',"config modem 'main'\n");
let bus={call:function(object,method,p){
    push(calls,[object,method]);
    if(object=='network.wireless')return {radio0:{interfaces:[{section:'wwan',ifname:'wlan1'}]}};
    if(object=='network.interface.wwan')return {up:online,'ipv4-address':online?[{address:'192.0.2.3'}]:[]};
    if(object=='iwinfo')return {results:[{ssid:'Visible AP',signal:-42}]};
    if(object=='network' && method=='reload')return null;
    return {};
}};
function run(action,args) {return native.dispatch(cursor(directory),bus,action,args || []);}
check(!run('status').configured,'empty STA state');
check(length(run('scan').results)==1,'scan through iwinfo');
check(run('connect',["Quoted ' AP",'saved-key']).pending,'accept valid native STA');
let c=cursor(directory);
check(c.get('wireless','wwan','mode')=='sta','STA mode saved');
check(c.get('wireless','wwan','encryption')=='sae-mixed','SAE/WPA2 native capability');
check(c.get('wireless','ap0','ssid')=='Preserve AP' && c.get('wireless','ap0','key')=='unchanged-key','AP credentials preserved');
check(run('status').state==1,'unassociated STA is pending');
online=true;check(run('status').state==2,'netifd address confirms link');
run('disconnect');check(!run('status').enabled,'disconnect disables only STA');
run('up');check(run('status').enabled,'restore saved STA');
fails(()=>run('connect',['Bad\nSSID','saved-key']));
fails(()=>run('connect',['Bad\x00SSID','saved-key']));
fails(()=>run('connect',['Good','short']));
c=cursor(directory);c.set('wireless','ap0','ssid','unsaved');
fails(()=>native.dispatch(c,bus,'disconnect',[]));c.revert('wireless');
run('forget');check(!run('status').configured,'forget removes only STA');
c=cursor(directory);check(c.get('wireless','ap0','ssid')=='Preserve AP','AP kept after forget');
run('connect',['Open AP']);c=cursor(directory);
check(c.get('wireless','wwan','encryption')=='none' && c.get('wireless','wwan','key')==null,'open network does not retain a key');
run('connect',['Raw PSK',sprintf('%064d',1)]);c=cursor(directory);
check(c.get('wireless','wwan','encryption')=='psk2','64 hex PSK is not an SAE passphrase');
c.set('wireless','second','wifi-iface');c.set('wireless','second','mode','sta');c.commit('wireless');
fails(()=>run('status'));
for(let name in ['wireless','network','unisoc_modem'])fs.unlink(directory+'/'+name);
fs.rmdir(directory);
printf('Native netifd Wi-Fi: %d assertions passed; mock ubus, no hardware or AT access\n',passed);
