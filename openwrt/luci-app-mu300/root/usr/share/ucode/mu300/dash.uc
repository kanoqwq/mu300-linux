// Native dashboard snapshot. Shared by rpcd and the compatibility CLI.
// No AT access, shell commands, configuration writes or private credentials.
import * as fs from 'fs';
import * as nl from 'nl80211';
import * as rt from 'rtnl';
import { statvfs } from 'luci.core';
import { cursor } from 'uci';
import { connect } from 'ubus';
import * as uloop from 'uloop';
import * as hotspot from '/usr/libexec/unisoc-modem/hotspot.uc';
import * as apn from '/usr/libexec/unisoc-modem/apn.uc';

const ROOT = getenv('MU300_DASH_SYSROOT') || '';
const CONFIG = getenv('MU300_DASH_CONFIG') || '/etc/config';
function rd(path) { return trim(fs.readfile(ROOT + path) || ''); }
function obj(path) { try { return json(rd(path)); } catch(e) { return null; } }
function num(v) { return v != null && match(''+v, /^-?[0-9]+(\.[0-9]+)?$/) ? +v : null; }
function text(v) { return v == null || v === '' ? null : replace(''+v,/[\r\n]/g,''); }
function device(v) { return type(v)=='string' && v!='.' && v!='..' && match(v,/^[A-Za-z0-9_.:-]{1,15}$/) ? v : null; }
function exists(p) { return fs.stat(ROOT+p)!=null; }
function executable(p) { return fs.access(ROOT+p,'x')==true; }
export function sim_state(slot) {
    if(!(slot in ['0','1']))return 'unknown';
    let row=obj('/run/mu300-registration/sim'+slot+'.json');
    // The AT owner records absence only after the SIM was known to be powered.
    // An empty ICCID or CFUN=0 alone is never proof of removal.
    if(type(row)!='object' || row.slot!==int(slot) || !exists('/run/mu300-registration/sim-power'+slot) ||
       !(row.state in ['ready','locked','absent']))return 'unknown';
    return row.state;
};
// Equipment identity is independent of SIM presence and the selected view.
// HTTP reads use RAM files; only this bounded background worker uses the AT owner.
let equipment_job=null;
function equipment(atd,slots) {
    let dir='/run/unisoc-modem-equipment', boot_id=rd('/proc/sys/kernel/random/boot_id');
    let valid=boot_id!='' && rd(dir+'/boot_id')==boot_id;
    let values=map(['0','1'],n=>valid && match(rd(dir+'/imei'+n),/^[0-9]{15}$/)?rd(dir+'/imei'+n):null);
    let stamp=fs.stat(ROOT+dir+'/attempt')?.mtime, age=stamp==null?null:time()-stamp;
    if(atd && boot_id!='' && !equipment_job && (values[0]==null || (slots=='2' && values[1]==null)) &&
       (!valid || age==null || age<0 || age>=60)) {
        equipment_job=uloop.process('/bin/sh',['/usr/libexec/unisoc-modem/dashboard-equipment'],{
            PATH:'/usr/sbin:/usr/bin:/sbin:/bin',MU300_EQUIPMENT_SLOTS:slots
        },()=>{equipment_job=null;});
    }
    return values;
};
function call(bus,o,m) { return bus?.call(o,m,{}) || {}; }
function net(dev) {
    dev=device(dev);
    if(!dev || !exists('/sys/class/net/'+dev))return null;
    let p='/sys/class/net/'+dev+'/';
    return {rx:num(rd(p+'statistics/rx_bytes')),tx:num(rd(p+'statistics/tx_bytes')),up:rd(p+'operstate')=='up'?1:0};
};
function address(rows,dev,global6) {
    for(let a in rows || []) if(a.dev==dev && a.address) {
        let s=replace(a.address,/\/.*/,'');
        if(!global6 || match(s,/^[23]/))return s;
    }
    return null;
};
function board() {
    // Match mu300-device: live DT outranks a restored rootfs/runtime label.
    if(exists('/proc/device-tree/charger_policy_service'))return 'u30air';
    if(exists('/proc/device-tree/soc/soc:ap-ahb/i2c@2270000/aw9523b_led@5b') ||
       exists('/proc/device-tree/soc/ap-ahb/i2c@2270000/aw9523b_led@5b'))return 'v50';
    if(exists('/proc/device-tree'))return 'f50';
    return rd('/run/mu300/device');
};
export function power() {
    const base='/sys/class/power_supply';
    let model=board(), battery=null, usb=null;
    let names=['battery','sc27xx-fgu',...(fs.lsdir(ROOT+base) || [])], seen={};
    for(let name in names) {
        if(seen[name])continue; seen[name]=true;
        let p=base+'/'+name, kind=rd(p+'/type');
        if(model!='f50' && model!='v50' && !battery &&
           (name=='battery' || name=='sc27xx-fgu' || kind=='Battery') &&
           rd(p+'/present')!='0') {
            let raw=rd(p+'/capacity'), cap=match(raw,/^[0-9]+$/)?+raw:null;
            if(cap!=null && cap>=0 && cap<=100)battery=p;
        }
        if(name in ['usb','ac','bq256xx-charger'] || match(kind,/^(USB|Mains)/)) {
            let on=num(rd(p+'/online'));
            if(on==1)usb=1; else if(on==0 && usb==null)usb=0;
        }
    }
    let volts=battery?num(rd(battery+'/voltage_now')):null;
    return {present:battery?1:0,capacity:battery?num(rd(battery+'/capacity')):null,
        status:battery?text(rd(battery+'/status')):null,
        volt:volts!=null && volts>0?+sprintf('%.2f',volts/1000000.0):null,
        ua:battery?num(rd(battery+'/current_now')):null,usb};
};
export function boot() {
    let state=rd('/etc/mu300/default-boot') || 'android';
    if(!(state in ['android','linux']))state='unknown';
    let slot=rd('/run/mu300/linux-slot') || 'b', writable=false;
    if(executable('/opt/mu300/bin/mu300-next-boot') && slot in ['a','b']) {
        writable=fs.stat(ROOT+'/run/mu300/misc-bc-linux-trial.bin')?.size>0 &&
            fs.stat(ROOT+'/run/mu300/misc-bc-android.bin')?.size>0;
        if(slot=='b')writable = writable || fs.stat(ROOT+'/run/mu300/misc-bc-slot-b-trial.bin')?.size>0 &&
            fs.stat(ROOT+'/run/mu300/misc-bc-slot-a.bin')?.size>0;
    }
    return {default:state,temporary:state=='unknown'?null:state!='linux',can_set_default:!!writable};
};
function clients(dev,bus,leases) {
    if(!device(dev) || !exists('/sys/class/net/'+dev))return {rows:[],count:0};
    let dump=nl.request(nl.const.NL80211_CMD_GET_STATION,nl.const.NLM_F_DUMP,{dev}), rows=[], seen={};
    // Drivers without station metrics still expose association through hostapd.
    // Both are sources in this single collector, never an alternate backend.
    for(let s in dump || []) {
        let mac=lc(s.mac || '');
        if(!match(mac,/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/) || seen[mac])continue;
        seen[mac]=true;
        let i=s.sta_info || {}, lease=leases[mac] || {};
        let rate=(r)=>(r?.bitrate32 ?? r?.bitrate)!=null?sprintf('%g Mbit/s',(r.bitrate32 ?? r.bitrate)/10.0):null;
        push(rows,{mac,host:lease.host,ip:lease.ip,signal:i.signal ?? null,
            tx:rate(i.tx_bitrate),rx:rate(i.rx_bitrate),
            conn:i.connected_time!=null?sprintf('%d min',int(i.connected_time/60)):null});
    }
    if(!length(rows)) {
        let associated=call(bus,'hostapd.'+dev,'get_clients').clients;
        if(associated!=null && dump==null)dump=[];
        for(let mac in associated || {}) {
            mac=lc(mac);
            if(!match(mac,/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/))continue;
            let lease=leases[mac] || {};
            push(rows,{mac,host:lease.host,ip:lease.ip,signal:null,tx:null,rx:null,conn:null});
        }
    }
    return {rows,count:dump==null && !length(rows)?null:length(rows)};
};
export function conntrack() {
    let raw=rd('/proc/sys/net/netfilter/nf_conntrack_count');
    let scalar=match(raw,/^[0-9]+$/)?+raw:null;
    if(scalar!=null)return scalar;
    // Older kernels may expose only the table. Count it in bounded chunks,
    // as wc -l did, rather than allocating a potentially huge connection table.
    fs.error();
    let table=fs.open(ROOT+'/proc/net/nf_conntrack','r');
    if(!table)return null;
    let count=0;
    while(true) {
        let chunk=table.read(4096);
        if(chunk==null || chunk=='')break;
        count+=length(split(chunk,'\n'))-1;
    }
    let error=fs.error(); table.close();
    return error?null:count;
};

export function info(slot) {
    const c=cursor(CONFIG), bus=connect(null,2), now=time();
    function cfg(k,d) { return c.get('unisoc_modem','main',k) || d; }
    let iface=cfg('data_interface','wan'), iface6=cfg('data_interface_v6','wan6');
    let wan=match(iface,/^[A-Za-z0-9_]{1,32}$/)?call(bus,'network.interface.'+iface,'status'):{};
    let wan6=match(iface6,/^[A-Za-z0-9_]{1,32}$/)?call(bus,'network.interface.'+iface6,'status'):{};
    let mobile=device(wan.l3_device || wan.device || cfg('data_device','sipa_eth0'));
    let lan=device(cfg('lan_device','br-lan')), usb=device(cfg('usb_device','usb0'));
    let wifi=hotspot.status(c,call(bus,'network.wireless','status'),cfg('wifi_iface',null),cfg('wifi_device','wlan0'));
    let wd=device(wifi.device || cfg('wifi_device','wlan0'));
    let rows4=rt.request(rt.const.RTM_GETADDR,rt.const.NLM_F_DUMP,{family:rt.const.AF_INET});
    let rows6=rt.request(rt.const.RTM_GETADDR,rt.const.NLM_F_DUMP,{family:rt.const.AF_INET6});
    let leases=[], bymac={};
    for(let line in split(rd('/tmp/dhcp.leases'),'\n')) {
        let f=split(trim(line),/[ \t]+/);
        if(length(f)<4)continue;
        let row={host:f[3]=='*'?null:text(f[3]),ip:text(f[2]),mac:text(f[1]),left:max(0,(num(f[0]) || 0)-now)};
        push(leases,row); bymac[lc(f[1])]=row;
    }
    let stations=clients(wd,bus,bymac), hapd=wd?call(bus,'hostapd.'+wd,'get_status'):{};
    let cpu=split(split(rd('/proc/stat'),'\n')[0],/[ \t]+/), total=0;
    for(let i=1;i<=8;i++)total+=num(cpu[i]) || 0;
    let freqs=[];
    for(let p in fs.glob(ROOT+'/sys/devices/system/cpu/cpufreq/policy*') || []) {
        if(fs.readfile(p+'/scaling_cur_freq')==null)continue;
        push(freqs,{cur:num(trim(fs.readfile(p+'/scaling_cur_freq') || '')),max:num(trim(fs.readfile(p+'/cpuinfo_max_freq') || ''))});
    }
    let mem={};
    for(let line in split(rd('/proc/meminfo'),'\n')) {
        let m=match(line,/^(MemTotal|MemAvailable):[ \t]+([0-9]+)/);
        if(m)mem[m[1]]=+m[2];
    }
    let temps={soc:null,cpu:null,modem:null,board:null,hottest:null};
    let labels={'soc-thmzone':'soc','apcpu0-thmzone':'cpu','nr0-thmzone':'modem','board-thmzone':'board'};
    for(let z in fs.glob(ROOT+'/sys/class/thermal/thermal_zone*') || []) {
        let t=num(trim(fs.readfile(z+'/temp') || '')), kind=trim(fs.readfile(z+'/type') || '');
        if(t==null)continue;
        t=+sprintf('%.1f',t/1000.0);
        if(temps.hottest==null || t>temps.hottest)temps.hottest=t;
        if(labels[kind] && temps[labels[kind]]==null)temps[labels[kind]]=t;
    }
    let vfs=statvfs(ROOT || '/'), storage={total_kb:null,used_kb:null,avail_kb:null};
    if(vfs?.frsize)storage={total_kb:vfs.blocks*vfs.frsize/1024,used_kb:(vfs.blocks-vfs.bfree)*vfs.frsize/1024,avail_kb:vfs.bavail*vfs.frsize/1024};
    let release=match(rd('/etc/openwrt_release'),/DISTRIB_DESCRIPTION='([^']*)'/);
    let kernel=rd('/proc/sys/kernel/osrelease'), fw=release?release[1]:'Linux '+kernel;
    let private_fw=obj('/etc/unisoc-ota/current.json')?.version || rd('/etc/mu300/image-version');
    if(private_fw)fw=private_fw+' ('+fw+')';
    let data_slot=cfg('data_sim','0')=='1'?'1':'0';
    let active_slot=rd('/run/mu300-data-sim-actual');
    if(wan.up) {
        if(mobile==cfg('sim0_device','sipa_eth0'))active_slot='0';
        else if(mobile==cfg('sim1_device','sipa_eth8'))active_slot='1';
    }
    if(exists('/run/mu300/sim-active-unknown'))active_slot='';
    let selection=apn.status(c,obj('/run/unisoc-apn-sim'+data_slot+'.json') || {}), selected_apn=null;
    if(selection.ok && (slot==null || ''+selection.data_sim==slot) && selection.source in ['sim','auto','network','default'] &&
       type(selection.apn)=='string' && length(selection.apn)<=100 && !match(selection.apn,/[^A-Za-z0-9.-]/))
        selected_apn=selection.apn || '(SIM default)';
    let dns=[];
    for(let w in [wan,wan6])for(let ip in w['dns-server'] || [])if(index(dns,ip)<0)push(dns,ip);
    let atd=false;
    for(let p in ['/run/mu300-at','/run/mu300-at4']) {
        let pid=rd(p+'/owner/pid');
        if(match(pid,/^[1-9][0-9]*$/) && exists('/proc/'+pid) && fs.stat(ROOT+p+'/cmd')?.type=='fifo')atd=true;
    }
    let urc=fs.stat(ROOT+'/run/mu300-at/urc/stty_nr0.log');
    let ht=replace(wifi.htmode || '',/[^0-9]/g,'');
    return {ts:now,uptime:int(rd('/proc/uptime')),host:text(c.get('system','@system[0]','hostname') || rd('/proc/sys/kernel/hostname')),
        model:text(rd('/tmp/sysinfo/model') || replace(rd('/proc/device-tree/model'),/\x00/g,'')),fw,kernel,
        cpu:{n:length(filter(split(rd('/proc/cpuinfo'),'\n'),line=>!!match(line,/^processor[ \t]*:/))),total,idle:(num(cpu[4]) || 0)+(num(cpu[5]) || 0),freqs},
        mem:{total_kb:mem.MemTotal ?? null,avail_kb:mem.MemAvailable ?? null},storage,temps,power:power(),
        net:{mobile:net(mobile),sipa_eth0:net(mobile),brlan:net(lan),wlan0:net(wd),usb0:net(usb)},
        wan:{up:wan.up?1:0,uptime:wan.uptime ?? null,proto:text(wan.proto),device:mobile,apn:selected_apn,
            ip4:wan['ipv4-address']?.[0]?.address ?? null,ip6:address(rows6,mobile,true),dns:text(join(', ',dns))},
        wifi:{available:wifi.available?1:0,enabled:wifi.enabled?1:0,pending:wifi.pending?1:0,section:text(wifi.section),radio:text(wifi.radio),
            error:text(wifi.error),up:wifi.up && hapd.status=='ENABLED' && exists('/sys/class/net/'+wd)?1:0,
            ssid:text(wifi.ssid),channel:text(wifi.channel),band:text(wifi.band),width:ht?substr(ht,0,2)+' MHz':null,
            country:text(wifi.country),enc:wifi.enc || 'none',hidden:wifi.hidden ?? null,hostapd:hapd.status=='ENABLED'?1:0,
            clients:stations.rows,clients_n:stations.count},
        lan:{leases:length(leases),ip:address(rows4,lan,false),list:leases},
        modem:{data_sim:active_slot in ['0','1']?int(active_slot):null,alive:atd || (urc && now-urc.mtime<240)?1:0,atd:atd?1:0,data_paused:exists('/run/mu300-mobile-data-down')?1:0,
            imeis:equipment(atd,cfg('sim_slots','1')),sims:map(['0','1'],n=>({slot:int(n),state:atd?sim_state(n):'unknown'}))},
        boot:boot(),capabilities:{dualboot:executable('/opt/mu300/bin/mu300-next-boot')?1:0},
        conns:conntrack(),traffic:obj('/run/unisoc-modem-traffic/view.json')};
};

function age(path) { let s=fs.stat(ROOT+path); return s?.size>0?time()-s.mtime:-1; }
function owner(dir) {
    let pid=rd(dir+'/pid');
    return match(pid,/^[1-9][0-9]*$/) && exists('/proc/'+pid) &&
        !!match(join(' ',split(rd('/proc/'+pid+'/cmdline'),'\x00')),/unisoc-modem\/cell|mu300-modemd/);
};
export function status(slot,cells) {
    let c=cursor(CONFIG);
    if(!(slot in ['0','1']) || (slot=='1' && c.get('unisoc_modem','main','sim_slots')!='2'))return {error:'SIM 卡槽不可用'};
    let dir='/tmp/unisoc-modem'+(slot=='1'?'/sim1':''), cage=age(dir+'/cell.json'), sage=age(dir+'/sig.json');
    let busy=exists('/run/unisoc-modem-lock-apply') || exists('/run/unisoc-data-sim-switch') || owner(dir+'/collect.lck');
    let fast=busy || owner(dir+'/fast.lck'), failed=false;
    let mode=cage<0 || cage>=5?'full':sage<0 || sage>=1?'fast':null;
    if(mode && !(mode=='full'?busy:fast)) {
        let r=connect(null,2)?.call('mu300.modem','collect',{slot,mode});
        failed=!r?.started && !r?.busy;
        if(mode=='full' && (r?.started || r?.busy))busy=true;
    }
    return {info:cells?null:info(slot),cell:failed?{error:'Unable to read cellular status'}:obj(dir+'/cell.json'),
        sig:failed?{error:'Unable to read cellular status'}:obj(dir+'/sig.json'),cell_age:cage,sig_age:sage,refreshing:busy?1:0};
};

export function sms_head(slot) {
    let c=cursor(CONFIG);
    if(!(slot in ['0','1']) || (slot=='1' && c.get('unisoc_modem','main','sim_slots')!='2'))return {error:'SIM 卡槽不可用'};
    let cmd=c.get('unisoc_modem','main','sms_command') || '';
    // Adapter selection is explicit configuration. An external adapter owns
    // its format; never assume its records use the native pool layout.
    if(cmd && !match(cmd,/(^|\/)mu300-sms$/))return {adapter:true};
    let pool=rtrim(c.get('unisoc_modem','main','sms_pool') || '/etc/mu300/sms','/');
    if(pool=='/etc/unisoc-modem/sms')pool='/etc/mu300/sms';
    if(substr(pool,0,1)!='/' || match(pool,/[[:cntrl:]]/) || index(pool,'//')>=0 ||
       length(filter(split(pool,'/'),part=>part=='.' || part=='..')))
        return {error:'Invalid SMS pool'};
    if(slot=='1')pool+='/sim1';
    let s=fs.stat(ROOT+pool+'/msg');
    return {source:pool,next_id:num(rd(pool+'/next_id')),count:length(filter(fs.lsdir(ROOT+pool+'/msg') || [],f=>!!match(f,/^[0-9]+$/))),mtime:s?.mtime ?? null};
};
