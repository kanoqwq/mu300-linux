// Existing ubus publishers only. Readiness follows actual subscription ACKs,
// so the initial call snapshot cannot race installation of the listeners.
import * as fs from 'fs';
import * as uloop from 'uloop';
import { connect } from 'ubus';
uloop.init();
const bus=connect();
if(!bus)die('call-notify: ubus unavailable\n');
function emit(value) {fs.stdout.write(sprintf('%J\n',value));fs.stdout.flush();}
const subscriptions=[];
for(let name in ['mu300.modem.urc','u30pro.panel.request','unisoc.forward.changed']) {
    let listener=bus.listener(name,(event,message)=>{let value={};value[event]=message;emit(value);});
    if(!listener)die('call-notify: subscription failed\n');
    push(subscriptions,listener);
}
const term=uloop.signal(15,()=>uloop.end());
emit({ready:true});
uloop.run();
