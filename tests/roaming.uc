import * as roam from '../rootfs/overlay/opt/mu300/lib/roaming.uc';
import * as recovery from '../openwrt/overlay/opt/mu300/lib/network-recovery.uc';
let count=0;
function check(value,label){if(!value)die('FAIL: '+label+'\n');count++;}
for(let slot in [0,1]) {
 for(let stat in [0,1,2,3,4,5,6,7,8,9,10]) {
  let selected=roam.registration({slot,stat,rat:7},null,slot);
  check(!!selected==(index([1,5,9,10],stat)>=0),'valid registered domain');
  check(roam.blocked(selected,false)==(stat==5||stat==10),'default off');
  check(!roam.blocked(selected,true),'explicit allow');
 }
 check(roam.registration({slot:1-slot,stat:5},null,slot)==null,'other SIM cannot block');
 check(roam.registration({slot,stat:1},{slot,stat:5},slot).stat==5,'NR roaming overrides EPS home');
 check(roam.registration({slot,stat:5},{slot,stat:0},slot).stat==5,'NR deregistered cannot hide EPS roaming');
}
let status={proto:'mu300cell',up:true,pending:false,available:true,autostart:true},events=[];
function call(method,data){if(method=='status')return status;push(events,data);return {};}
let state={slot:0,stat:5,rat:11};
check(roam.suspend(call,state,false,false),'roaming transition stops data');
check(length(events)==2 && events[0].error[0]=='ROAMING_DISABLED' && events[1].available===false,'error plus availability only, no RF command');
events=[];status.available=false;
check(!roam.suspend(call,state,false,false)&&!length(events),'no event loop');
status.available=true;status.autostart=false;
check(!roam.suspend(call,state,false,false),'manual ifdown respected');
status.autostart=true;
check(!roam.suspend(call,state,false,true),'radio operation or manual down respected');
check(!roam.suspend(call,state,true,false),'allow policy never drops data');
status={proto:'mu300cell',up:false,pending:false,available:false,autostart:true,errors:[{code:'ROAMING_DISABLED'}]};
let previous={key:null,attempted:false};events=[];
check(recovery.recovery(call,{slot:0,stat:1,rat:7},0,0,false,previous),'return home resumes');
check(length(events)==1&&events[0].available===true,'availability only recovery');
check(!recovery.recovery(call,{slot:0,stat:1,rat:7},0,0,false,previous),'resume deduplicated');
status.autostart=false;previous={key:null,attempted:false};
check(!recovery.recovery(call,state,0,0,false,previous),'allow cannot override manual disconnect');
printf('PASS: %d roaming and event recovery checks\n',count);
