import * as fs from 'fs';
import * as p from 'traffic-policy';
function clone(v) { return json(sprintf('%J',v)); }
let cfg=p.config({}), state={}, network={up:true,autostart:true,pending:false};
let slot='0', snapshots={'0':{clock_ok:true,cycle_used:0},'1':{clock_ok:true,cycle_used:0}};
let now=1794960000, down=0, up=0, restarted=0, writes=0, switching=false, calls=0;
const io={
    settings:()=>clone(cfg),state:()=>clone(state),save:(s)=>{state=clone(s);writes++;},
    status:()=>null,active_slot:()=>slot,snapshot:(s)=>snapshots[s],uci:(key,value)=>value,
    now:()=>now,boot:()=>'same-boot',switching:()=>switching,
    bus:(iface,method)=>{
        calls++; assert(iface=='wan');
        if(method=='down') {down++;network.up=false;network.autostart=false;}
        if(method=='up') {up++;network.up=true;network.autostart=true;}
        return clone(network);
    },restart:(s)=>{assert(s==slot);restarted++;return 'done';}
};
let c=p.controller(io);
for(let i=0;i<10000;i++) {
    slot=''+(i%2);now+=10;
    cfg=p.config({});cfg.slots[slot]={enabled:true,limit_gb:1};
    snapshots[slot]={clock_ok:true,cycle_used:0};
    c.tick(false);assert(network.up && !state.stopped,'below threshold');
    snapshots[slot].cycle_used=1e9;
    c.tick(false);assert(!network.up && state.stopped,'exact threshold');
    let before=down;c.tick(false);assert(down==before,'duplicate ifdown');
    cfg.slots[slot].enabled=false;
    assert(p.needed(cfg,state),'owned cutoff must still recover while disabled');
    c.tick(false);assert(network.up && !state.stopped,'release owned WAN');
    assert(!p.needed(cfg,state),'disabled policy must go idle');
    network={up:false,autostart:false,pending:false};
    c.tick(true);assert(state.last.result=='wan_disabled','manual WAN off must win');
    network={up:true,autostart:true,pending:false};
    c.tick(true);assert(state.last.result=='done' && !p.needed(cfg,state),'manual completion must go idle');
    switching=true;before=calls;c.tick(false);assert(calls==before,'no WAN access during switch');switching=false;
}
assert(down==10000 && up==10000 && restarted==10000,'lost/duplicate actions');
gc();
print(sprintf('PASS 10000 dual-SIM cycles, 70000 ticks, down=%d up=%d restart=%d writes=%d\n',down,up,restarted,writes));
print(fs.readfile('/proc/self/status'));
