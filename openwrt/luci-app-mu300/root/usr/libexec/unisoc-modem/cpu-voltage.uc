// Boot-only voltage profiles. No arbitrary shell arguments, live voltage
// writes, AT access, polling worker, or firmware/boot partition modifications.
import * as fs from 'fs';
const DIR=getenv('MU300_CPU_VOLTAGE_DIR') || '/etc/unisoc-modem';
const RUN=getenv('MU300_CPU_VOLTAGE_RUN') || '/run';
const ROOT=getenv('MU300_CPU_SYS') || '/sys/devices/system/cpu/cpufreq';
const CONFIG=DIR+'/cpu-voltage.json', ARMED=DIR+'/cpu-voltage-armed.json';
const RECOVERY=RUN+'/unisoc-cpu-voltage-recovery.json';
const BOOT=trim(fs.readfile(getenv('MU300_CPU_BOOT_ID') || '/proc/sys/kernel/random/boot_id') || '');
const PLATFORM=getenv('MU300_CPU_PLATFORM') || '/opt/mu300/bin/cpu-voltage-platform';
function readjson(path) { try { return json(fs.readfile(path) || 'null'); } catch(e) { return null; } }
function valid(v) {
    if(type(v)!='array' || length(v)!=3) return false;
    for(let n in v) if(type(n)!='int' || n < -50000 || n > 25000 || n%3125) return false;
    return true;
}
function same(a,b) { return valid(a) && valid(b) && join(',',a)==join(',',b); }
function store(path,value) {
    let s=sprintf('%J\n',value), tmp=path+'.new';
    fs.mkdir(DIR,448);
    if(fs.writefile(tmp,s)!=length(s) || !fs.chmod(tmp,384) || !fs.rename(tmp,path)) {
        fs.unlink(tmp); return false;
    }
    return true;
}
function supported(force) {
    let cache=RUN+'/unisoc-cpu-capability.json', c=readjson(cache);
    if(!force && BOOT && c?.boot==BOOT) return c.supported===true;
    // The installed adapter checks the matching native boot-offset driver ABI.
    // Environment overrides are test-only, never RPC/user configuration.
    if(!fs.access(PLATFORM,'x') || !match(PLATFORM,/^[\/a-zA-Z0-9_.-]+$/)) return false;
    let p=fs.popen(PLATFORM+' check >/dev/null 2>&1','r');
    let ok=p && p.close()==0;
    // Do not pin a transient early-boot discovery failure for the whole boot.
    if(BOOT && ok) store(cache,{boot:BOOT,supported:true});
    return !!ok;
}
function current() {
    let domains=[], seen={};
    for(let id in sort(fs.lsdir(ROOT)||[])) {
        if(!match(id,/^policy[0-9]+$/)) continue;
        let path=ROOT+'/'+id+'/', d=trim(fs.readfile(path+'scaling_voltage_domain')||''),
            offset=trim(fs.readfile(path+'scaling_voltage_offset')||'');
        if(!match(d,/^[012]$/) || !match(offset,/^-?[0-9]+$/) || seen[d]) continue;
        seen[d]=true;
        let table=[];
        for(let line in split(trim(fs.readfile(path+'scaling_voltage_table')||''),'\n')) {
            let pair=match(line,/^([0-9]+) ([0-9]+)$/);
            if(pair) push(table,{khz:int(pair[1]),uv:int(pair[2])});
        }
        push(domains,{id:int(d),policy:id,cpus:trim(fs.readfile(path+'related_cpus')||fs.readfile(path+'affected_cpus')||''),offset:int(offset),table});
    }
    return domains;
}
function status() {
    let domains=current(), config=readjson(CONFIG), firmware=supported(false);
    let next=config?.version==1 && valid(config.offsets)?config.offsets:[0,0,0];
    return {ok:1,supported:firmware && length(domains)==3,firmware,domains,
        offsets:next,min:-50000,max:25000,step:3125,
        pending:length(filter(domains,d=>d.offset!=next[d.id]))>0,
        recovery:readjson(RUN+'/unisoc-cpu-voltage-recovery.json')?.reason || null};
}
function save(input) {
    if(!valid(input?.offsets)) return {ok:0,error:'CPU 电压配置无效'};
    let nonzero=length(filter(input.offsets,n=>n!=0))>0;
    if(nonzero && (!supported(true) || length(current())!=3)) return {ok:0,error:'当前内核或固件不支持电压调整'};
    if(!store(CONFIG,{version:1,offsets:input.offsets})) return {ok:0,error:'无法保存 CPU 设置'};
    // Explicit saving retries a paused trial only from observed factory voltage.
    // Saving while an offset is active must not erase its boot evidence.
    let armed=readjson(ARMED), domains=current();
    if(armed?.phase=='paused' && length(domains)==3 && !length(filter(domains,d=>d.offset!=0))) {
        if(!fs.unlink(ARMED)) return {ok:0,error:'无法保存 CPU 设置'};
        fs.unlink(RECOVERY);
    }
    return status();
}
function bootargs() {
    let config=readjson(CONFIG), armed=readjson(ARMED);
    if(fs.stat(ARMED)) {
        if(BOOT && armed?.boot==BOOT) return '0,0,0'; // never apply twice
    }
    if(config?.version!=1 || !valid(config.offsets)) return '0,0,0';
    if(!length(filter(config.offsets,n=>n!=0))) {
        if(BOOT) { fs.unlink(ARMED); fs.unlink(RECOVERY); }
        return '0,0,0';
    }
    if(!BOOT || !supported(true)) return '0,0,0';
    // Missing shutdown is normal on externally powered F50/V50. Confirmed
    // settings survive any power cut. Only an unfinished NEW trial is paused;
    // it is not evidence of a crash and never rejects or deletes the profile.
    let reason=null;
    if(fs.stat(ARMED) && (!valid(armed?.offsets) ||
        (armed.version!=null && armed.version!=2) ||
        (armed.version==2 && index(['pending','confirmed','paused'],armed.phase)<0))) reason='invalid_boot_record';
    else if(armed?.version==2 && same(armed.offsets,config.offsets) &&
            (armed.phase=='pending' || armed.phase=='paused')) reason='boot_unconfirmed';
    if(reason) {
        store(ARMED,{version:2,boot:BOOT,offsets:config.offsets,phase:'paused'});
        store(RECOVERY,{reason});
        return '0,0,0';
    }
    let confirmed=armed?.version==2 && armed.phase=='confirmed' && same(armed.offsets,config.offsets);
    if(!store(ARMED,{version:2,boot:BOOT,offsets:config.offsets,phase:confirmed?'confirmed':'pending'})) return '0,0,0';
    fs.unlink(RECOVERY);
    return join(',',config.offsets);
}
function confirm() {
    let armed=readjson(ARMED);
    if(!fs.stat(ARMED) || armed?.phase=='paused') return {ok:1};
    if(!BOOT || armed?.boot!=BOOT || !valid(armed.offsets)) return {ok:0,error:'无法确认 CPU 电压启动状态'};
    let domains=current();
    if(length(domains)!=3 || length(filter(domains,d=>d.offset!=armed.offsets[d.id])))
        return {ok:0,error:'CPU 电压读回与启动设置不符'};
    if(armed.version==2 && armed.phase=='confirmed') return {ok:1};
    if(!store(ARMED,{version:2,boot:BOOT,offsets:armed.offsets,phase:'confirmed'}))
        return {ok:0,error:'无法确认 CPU 电压启动状态'};
    return {ok:1};
}
let action=ARGV[0]||'get', result;
try {
    if(action=='boot-args') { print(bootargs()+'\n'); exit(0); }
    if(action=='get') result=status();
    else if(action=='save') {
        let text=fs.stdin.read(4097);
        result=length(text)>4096?{ok:0,error:'CPU 电压配置无效'}:save(json(text));
    } else if(action=='confirm' || action=='shutdown') result=confirm();
    else result={ok:0,error:'CPU 电压配置无效'};
} catch(e) {
    if(action=='boot-args') { print('0,0,0\n'); exit(0); }
    result={ok:0,error:'CPU 电压配置无效'};
}
print(sprintf('%J\n',result));
exit(result.ok?0:1);
