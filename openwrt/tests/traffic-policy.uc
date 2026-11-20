import * as p from 'traffic-policy';
let count=0;
function check(ok,label) { assert(ok,label); count++; }
function same(a,b,label) { check(sprintf('%J',a)==sprintf('%J',b),label); }
function fails(fn,label) { let caught=false; try { fn(); } catch(e) { caught=true; } check(caught,label); }
function clone(v) { return json(sprintf('%J',v)); }
function fixture() {
    let f={settings:p.config({}),state:{},network:{up:true,autostart:true,pending:false},events:[],
        slot:'0',boot:'one',now:1794960000,uci:{},snapshots:{'0':{clock_ok:true,cycle_used:2e9},'1':{clock_ok:true,cycle_used:0}},
        restarting:'done',spawned:[],switching:false,write_fail:false};
    f.io={settings:()=>clone(f.settings),state:()=>clone(f.state),save:(s)=>{if(f.write_fail)die('disk full');f.state=clone(s);},
        status:(s)=>{f.status=s;},runtime:()=>f.status || {},write_config:(s)=>{f.settings=clone(s);},
        configure:(fn)=>fn(),check_idle:()=>null,spawn:(force)=>push(f.spawned,force),snapshot:(s)=>f.snapshots[s],
        active_slot:()=>f.slot,boot:()=>f.boot,now:()=>f.now,switching:()=>f.switching,
        uci:(key,default_value)=>f.uci[key] ?? (key=='unisoc_modem.main.sim_slots' ? '2' : default_value),
        bus:(iface,method)=>{
            push(f.events,method);
            if (method=='down') { f.network.up=false; f.network.autostart=false; }
            if (method=='up') { f.network.up=true; f.network.autostart=true; }
            return clone(f.network);
        },restart:(slot)=>{f.restarted=slot;if(f.restarting=='error')die('no response');return f.restarting;}};
    f.c=p.controller(f.io);
    f.settings.slots['0']={enabled:true,limit_gb:2};
    return f;
}
let defaults=p.config({});
check(!p.needed(defaults,{}) && !p.needed(null,null),'default/absent config does not launch workers');
for (let slot in ['0','1']) { let c=p.config({});c.slots[slot].enabled=true;check(p.needed(c,{}),'each enabled slot runs'); }
let scheduled=p.config({});scheduled.schedule.enabled=true;
check(p.needed(scheduled,{}),'schedule runs');
check(p.needed(defaults,{stopped:{boot:'one',iface:'wan'}}),'disabled protection still restores owned WAN');
check(p.needed(defaults,{last:{result:'running'}}),'interrupted operation still reported');
check(!p.needed(defaults,{last:{result:'done'}}),'completed operation does not keep idle workers alive');
for (let bad in [null,[],{slots:[]},{slots:null},{slots:{'0':null}},{schedule:null},{slots:{'0':{enabled:1,limit_gb:1}}},
    {slots:{'0':{enabled:true,limit_gb:0}}},{slots:{'0':{enabled:false,limit_gb:-1}}},
    {slots:{'0':{enabled:false,limit_gb:'2'}}},{schedule:{enabled:true,time:'24:00'}},
    {schedule:{enabled:true,time:'7:00'}},{schedule:{enabled:'true',time:'07:00'}}])
    fails(()=>p.config(bad),'invalid configuration rejected');
{
    let f=fixture();f.snapshots['0'].cycle_used=1999999999;f.c.tick(false);
    check(!f.state.stopped && f.network.up,'below threshold');
    f.snapshots['0'].cycle_used=2e9;f.c.tick(false);
    check(!!f.state.stopped && !f.network.up,'exact threshold persists ownership and stops WAN');
    f.events=[];f.c.tick(false);same(f.events,['status'],'does not repeatedly stop an already down WAN');
    f.settings.slots['0'].enabled=false;f.c.tick(false);
    check(f.network.up && !f.state.stopped,'disable restores owned WAN');
}
for (let action in ['reset','raise','switch']) {
    let f=fixture();f.c.tick(false);
    if (action=='reset') f.snapshots['0'].cycle_used=0;
    if (action=='raise') f.settings.slots['0'].limit_gb=3;
    if (action=='switch') f.slot='1';
    f.c.tick(false);check(f.network.up && !f.state.stopped,action+' releases cutoff');
}
{
    let f=fixture();f.slot='1';f.c.tick(false);check(!f.state.stopped && f.network.up,'inactive SIM does not block');
    f=fixture();f.network={up:false,autostart:false};f.c.tick(false);
    f.settings.slots['0'].enabled=false;f.c.tick(false);
    same(f.events,['status','status'],'manual down never owned or resumed');
}
for (let key in ['disabled','auto']) {
    let f=fixture();f.c.tick(false);f.settings.slots['0'].enabled=false;
    f.uci['network.wan.'+key]=key=='disabled' ? '1' : '0';f.events=[];f.c.tick(false);
    same(f.events,['status'],'persistent manual '+key+' wins');
}
{
    let f=fixture();f.c.tick(false);f.snapshots['0']={clock_ok:false};f.c.tick(false);
    check(!f.network.up && !!f.state.stopped,'unknown accounting cannot release cutoff');
    f.boot='two';f.settings.slots['0'].enabled=false;f.events=[];f.c.tick(false);
    same(f.events,['status'],'reboot never restores stale ownership');
    f=fixture();f.write_fail=true;fails(()=>f.c.tick(false),'save failure surfaced');
    same(f.events,['status'],'no down before successful durable ownership');
}
{
    let f=fixture();f.c.api('set',{slot:'1',config:{quota:{enabled:true,limit_gb:8.5}}});
    f.c.api('set',{slot:'0',config:{schedule:{enabled:true,time:'16:45'}}});
    check(f.settings.slots['0'].limit_gb==2 && f.settings.slots['1'].limit_gb==8.5 &&
        f.settings.schedule.time=='16:45','partial saves keep other slot and schedule');
    check(f.c.api('get',{slot:'1',ubus_rpc_session:'fixture'}).slot==1,'RPC session metadata accepted');
    for (let request in [{slot:'all',config:{}},{slot:2,config:{}}])
        fails(()=>f.c.api('set',request),'invalid scope rejected');
    for (let confirm in [false,'true',null]) fails(()=>f.c.api('run',{confirm}),'explicit confirmation required');
    f.uci['unisoc_modem.main.sim_slots']='1';fails(()=>f.c.api('get',{slot:'1'}),'single SIM scope rejected');
}
{
    let f=fixture();f.c.tick(true);check(f.state.last.result=='quota_blocked' && !f.restarted,'manual restart respects quota');
    f.state={};f.settings.slots['0'].enabled=false;f.c.tick(true);
    check(f.state.last.result=='wan_disabled' && !f.restarted,'manual restart respects manual WAN off');
    f=fixture();f.settings.slots['0'].enabled=false;f.slot='1';f.c.tick(true);
    check(f.state.last.result=='done' && f.restarted=='1','restart uses actual slot');
    f.restarting='error';f.c.tick(true);check(f.state.last.detail=='no response','failure recorded');
    f.state.last={result:'running'};f.c.tick(false);check(f.state.last.detail=='interrupted','interrupted run reported');
    f.switching=true;f.events=[];f.c.tick(false);same(f.events,[],'no network access during card switch');
}
{
    let now=timelocal({year:2026,mon:11,mday:18,hour:6,min:59,sec:30,isdst:-1});
    let schedule={enabled:true,time:'07:00'}, state={};
    check(!p.due(state,schedule,now) && p.due(state,schedule,now+30),'runs when due');
    check(!p.due(state,schedule,now+40),'same day runs once');
    state.next_run=now+30;check(!p.due(state,schedule,now+31),'clock rollback cannot repeat');
    check(p.due(state,schedule,now+86430),'next day runs');
    state={};p.due(state,schedule,now);check(!p.due(state,schedule,now+3600) && state.last.result=='missed','missed job skipped');
    check(!p.due({},schedule,500) && !p.due({},{enabled:false,time:'07:00'},now+30),'unsynced and disabled ignored');
    check(localtime(p.next_run(now,'08:00')).hour==8,'local schedule');
    now=timelocal({year:2026,mon:12,mday:31,hour:23,min:59,sec:0,isdst:-1});
    check(localtime(p.next_run(now,'00:00')).year==2027,'calendar rollover');
    if (index(getenv('TZ') || '', 'EST5EDT')==0) {
        now=timelocal({year:2026,mon:10,mday:31,hour:8,min:0,sec:0,isdst:-1});
        check(p.next_run(now,'08:00')-now==25*3600,'DST preserves wall-clock schedule');
    }
}
print(sprintf('PASS %d policy assertions (TZ=%s)\n',count,getenv('TZ')));
