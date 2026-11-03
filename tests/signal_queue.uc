import {queue} from '../rootfs/overlay/usr/share/ucode/mu300/signal-queue.uc';
let timers=[],starts=[],callbacks={},occupied={},replies=[],count=0;
function check(value,label){if(!value)die('FAIL: '+label+'\n');count++;}
let q=queue((slot,reply)=>{push(starts,slot);callbacks[slot]=reply;return null;},slot=>!!occupied[slot],
 (ms,cb)=>{let t={ms,cb,cancelled:false};t.cancel=()=>{t.cancelled=true;};push(timers,t);return t;});
for(let i in [0,1,2])q.request('0',result=>push(replies,result));
check(length(starts)==1,'same SIM concurrent reads share one round');
callbacks['0']({ts:1,sig:{rsrp:-90}});
check(length(replies)==3 && !q.has('0'),'all waiters complete, no queued garbage');
q.request('0',result=>push(replies,result));
check(length(starts)==2,'a later request requires fresh data, no stale cache');
callbacks['0']({error:'modem unavailable'});
check(replies[-1].error=='modem unavailable','real failure propagates');
occupied['0']=true;
q.request('0',result=>push(replies,result));q.request('0',result=>push(replies,result));
check(length(starts)==2 && q.has('0'),'background owner keeps AT ordering');
q.request('1',result=>push(replies,result));
check(length(starts)==3 && starts[-1]=='1','other SIM independent');
callbacks['1']({ts:2});occupied['0']=false;q.resume();
check(length(starts)==4 && starts[-1]=='0','event completion wakes queued read');
callbacks['0']({ts:3});check(length(replies)==7,'queued waiters all complete');
occupied['0']=true;q.request('0',result=>push(replies,result));
timers[-1].cb();check(!q.has('0') && replies[-1].busy==1,'bounded queue wait is busy, not dead modem');
occupied['0']=false;q.resume();check(length(starts)==4,'expired work cannot start later');
q.request('0',()=>{die('closed caller\n');});q.request('0',result=>push(replies,result));
callbacks['0']({ts:4});check(replies[-1].ts==4,'one closed caller cannot block others');
printf('PASS: %d in-flight signal scheduling checks\n',count);
