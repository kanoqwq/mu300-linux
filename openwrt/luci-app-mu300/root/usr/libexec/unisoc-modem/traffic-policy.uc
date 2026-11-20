// Shared policy state machine: no daemon, AT reader or independent timer.
export function config(value) {
    if (type(value) != 'object' || 'slots' in value && type(value.slots) != 'object') die('invalid_config');
    let result={slots:{},schedule:'schedule' in value ? value.schedule : {enabled:false,time:'07:00'}};
    for (let slot in ['0','1']) {
        let c=value.slots && slot in value.slots ? value.slots[slot] : {enabled:false,limit_gb:0}, n=c?.limit_gb;
        if (type(c) != 'object' || type(c.enabled) != 'bool' || !(type(n) in ['int','double']) ||
            !(n>=0 && n<=100000) || c.enabled && n<=0) die('invalid_config');
        result.slots[slot]={enabled:c.enabled,limit_gb:n};
    }
    let s=result.schedule;
    if (type(s) != 'object' || type(s.enabled) != 'bool' || type(s.time) != 'string' ||
        !match(s.time,/^([01][0-9]|2[0-3]):[0-5][0-9]$/)) die('invalid_config');
    result.schedule={enabled:s.enabled,time:s.time};
    return result;
};

// A disabled policy must finish restoring its own cutoff and reporting an
// interrupted restart before the accounting process stops launching workers.
export function needed(settings,state) {
    return !!(settings?.schedule?.enabled || settings?.slots?.['0']?.enabled ||
        settings?.slots?.['1']?.enabled || state?.stopped || state?.last?.result=='running');
};

export function next_run(now,clock) {
    let day=localtime(now), parts=split(clock,':');
    day.hour=+parts[0]; day.min=+parts[1]; day.sec=0; day.isdst=-1;
    let at=timelocal(day);
    if (at>now) return at;
    day.mday++; day.isdst=-1;
    return timelocal(day);
};

function local(stamp,date_only) {
    if (!stamp) return null;
    let t=localtime(stamp), day=sprintf('%04d-%02d-%02d',t.year,t.mon,t.mday);
    return date_only ? day : day+sprintf(' %02d:%02d:%02d',t.hour,t.min,t.sec);
}

export function due(state,schedule,now) {
    if (!schedule.enabled || now<1704067200) return false;
    let at=state.next_run;
    if (!at || state.schedule_time!=schedule.time) {
        state.next_run=next_run(now,schedule.time); state.schedule_time=schedule.time;
        return false;
    }
    if (now<at) return false;
    state.next_run=next_run(now,schedule.time);
    let day=local(now,true);
    if ((state.last_day || '')>=day) return false;
    state.last_day=day;
    if (now-at>120) { state.last={at:now,result:'missed'}; return false; }
    return true;
};

export function exceeded(settings,snapshot) {
    if (!settings.enabled) return false;
    if (snapshot.clock_ok!==true || !(type(snapshot.cycle_used) in ['int','double'])) return null;
    return snapshot.cycle_used>=settings.limit_gb*1000000000;
};

// Explicit I/O keeps offline tests on the real state machine without touching
// a WAN, RF permissions or the modem's command owner.
export function controller(io) {
    function reconcile(state,settings,slot,iface,status,boot) {
        let stop=state.stopped;
        if (stop && (stop.boot!=boot || stop.iface!=iface)) { delete state.stopped; stop=null; }
        let blocked=exceeded(settings.slots[slot],io.snapshot(slot));
        if (blocked==null) blocked=!!stop;
        if (blocked) {
            if (!stop && status.autostart===true) {
                state.stopped={boot,iface}; io.save(state); stop=state.stopped;
            }
            if (stop && (status.autostart===true || status.up || status.pending)) io.bus(iface,'down');
        }
        else if (stop) {
            if (status.autostart!==true && io.uci('network.'+iface+'.disabled','0')!='1' &&
                io.uci('network.'+iface+'.auto','1')!='0') io.bus(iface,'up');
            delete state.stopped;
        }
        return blocked;
    }
    function tick(force) {
        let now=io.now(), settings=config(io.settings()), state=io.state(), before=sprintf('%J',state);
        if (state.last?.result=='running') { state.last.result='failed'; state.last.detail='interrupted'; }
        let slot=io.active_slot(), iface=io.uci('unisoc_modem.main.data_interface','wan');
        if (!match(iface,/^[a-zA-Z0-9_]{1,32}$/)) die('invalid_interface');
        if (io.switching()) return;
        let status=io.bus(iface,'status');
        if (io.active_slot()!=slot) return;
        let blocked=reconcile(state,settings,slot,iface,status,io.boot());
        let scheduled=due(state,settings.schedule,now);
        if (force || scheduled) {
            state.last={at:now,result:'running',slot:+slot}; io.save(state);
            try {
                state.last.result=blocked ? 'quota_blocked' : status.autostart!==true ||
                    io.uci('network.'+iface+'.disabled','0')=='1' ? 'wan_disabled' : io.restart(slot);
            } catch(e) { state.last.result='failed'; state.last.detail=e.message; }
        }
        if (before!=sprintf('%J',state)) io.save(state);
        io.status({active_slot:+slot,blocked,last_check:now});
    }
    function api(op,request) {
        if (type(request)!='object') die('invalid_config');
        if (op=='run') {
            if (request.confirm!==true) die('confirmation_required');
            io.check_idle(); io.spawn(true); return {ok:1};
        }
        let slot=''+request.slot;
        if (!(slot in ['0','1']) || slot=='1' && io.uci('unisoc_modem.main.sim_slots','1')!='2') die('invalid_slot');
        if (op=='set') {
            if (type(request.config)!='object') die('invalid_config');
            io.configure(function() {
                let settings=config(io.settings());
                if ('quota' in request.config) settings.slots[slot]=request.config.quota;
                if ('schedule' in request.config) settings.schedule=request.config.schedule;
                io.write_config(config(settings));
            });
            io.spawn(false); return {ok:1};
        }
        if (op!='get') die('invalid_operation');
        let settings=config(io.settings()), state=io.state(), now=io.now();
        let planned=settings.schedule.enabled && now>=1704067200 ? next_run(now,settings.schedule.time) : null;
        return {ok:1,slot:+slot,quota:settings.slots[slot],schedule:settings.schedule,
            local_time:local(now),next_time:local(planned),last_time:local(state.last?.at),now,
            next_run:planned,last:state.last ?? null,runtime:io.runtime()};
    }
    return {reconcile,tick,api};
};
