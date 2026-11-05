import * as policy from '../openwrt/overlay/opt/mu300/lib/network-recovery.uc';
let n=0;
function check(v,label){if(!v)die('FAIL '+label+'\n');n++;}
let s={proto:'mu300cell',autostart:true,up:false,pending:false,available:false,errors:[{code:'NO_DEVICE'}]};
let reg={slot:1,stat:1,rat:11},sim={slot:1,state:'ready'},calls=0;
function run(device,overrides){let now={...s,...overrides};return policy.recovery((m,p)=>{if(m=='status')return now;check(p.action==5 && p.available===true,'availability only');calls++;return {};},reg,1,1,false,{key:null,attempted:false},sim,device);}
check(!run(false,{}),'missing netdev stays blocked');
check(run(true,{}),'present netdev and registered ready SIM recovers NO_DEVICE');
check(!run(true,{autostart:false}),'manual off preserved');
check(!run(true,{errors:[{code:'NO_MODEM'}]}),'unrelated failure preserved');
check(!run(true,{pending:true}),'pending dial not duplicated');
check(calls==1,'only one justified restore');
printf('PASS %d live SIM and WAN checks\n',n);
