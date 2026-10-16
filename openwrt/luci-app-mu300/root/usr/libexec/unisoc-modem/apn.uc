// Profiles contain no modem commands. netifd remains the owner of the bearer.
const cfg = 'unisoc_apn';
export function reconnect(bus,last_error,iface) {
    if(!bus)return false;
    // Successful netifd methods may send no response body. Check the ubus
    // status rather than treating a null response as a failed operation.
    for(let request in [['network','reload'],['network.interface.'+iface,'down'],['network.interface.'+iface,'up']]) {
        bus.call(request[0],request[1],{});
        if(last_error(true))return false;
    }
    return true;
};
function fail(error) { return {ok:0,error}; }
function valid_id(id) { return type(id)=='string' && match(id,/^[A-Za-z0-9_]{1,64}$/); }
function auth(p) { return p.auth || 'none'; }
function credentials(p) {
    let user=p.username || '', password=p.password || '';
    return type(user)=='string' && type(password)=='string' && length(user)<=64 && length(password)<=64 &&
        !match(user+password,/["\\[:cntrl:]]/) && !match(user+password,/[^ -~]/);
}
// Preserve the modem's identifier, including vendor/card letters and padding.
export function valid_identity(p) { return type(p)=='object' && type(p.iccid)=='string' && match(p.iccid,/^[A-Za-z0-9]{18,22}$/); };
function options(p) { return {apn:p.apn || '',pdptype:p.pdptype || 'IP',auth:auth(p),username:p.username || '',password:p.password || '',roaming:p.roaming || p.pdptype || 'IP',attach:p.attach=='1'?'1':'0'}; }
function same(a,b) { return sprintf('%J',options(a))==sprintf('%J',options(b)); }
function binding(c,identity) {
    let found=null;
    if(valid_identity(identity))c.foreach(cfg,'selection',s=>{if(s.iccid==identity.iccid)found=s;});
    return found;
}
// The small rule list is independently maintained. It deliberately excludes
// IMS/MMS, IoT/private/test APNs and ambiguous MVNO guesses.
export function automatic(imsi) {
    if(type(imsi)!='string' || !match(imsi,/^[0-9]{14,16}$/))return null;
    let numeric=substr(imsi,0,5), apn=null;
    if(numeric in ['46000','46002','46004','46007','46008'])apn='cmnet';
    if(numeric in ['46001','46006','46009','46030'])apn='3gnet';
    if(numeric in ['46003','46005','46011','46012'])apn='ctnet';
    if(numeric=='46015')apn='cbnet';
    return apn?{apn,pdptype:'IPV4V6',roaming:'IPV4V6',auth:'none',username:'',password:'',attach:'0'}:null;
};
export function validate(p) {
    if(type(p)!='object' || type(p.name)!='string' || !length(trim(p.name)) ||
       length(p.name)>64 || match(p.name,/[[:cntrl:]]/)) return false;
    let value=p.apn ?? '';
    if(type(value)!='string' || length(value)>100 ||
       (value!='' && !match(value,/^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$/))) return false;
    for(let label in split(value,'.'))if(length(label)>63)return false;
    // mu300cell requires an IPv4 bearer address; IPv6-only is not supported.
    return p.pdptype in ['IP','IPV4V6'] && (p.roaming || p.pdptype) in ['IP','IPV4V6'] &&
        auth(p) in ['none','pap','chap','papchap'] && credentials(p) &&
        (p.attach==null || p.attach in ['0','1']) &&
        length(sprintf('AT+SPCGAPNCFG=1,"%s","%s","%s",0,"%s","%s",3',value,p.pdptype,p.roaming || p.pdptype,p.username || '',p.password || ''))<=240;
};
export function resolve(c,identity,base) {
    let b=binding(c,identity);
    if(!b)return {ok:1,source:'network',settings:options(base)};
    if(b.mode=='auto') {
        let settings=automatic(identity.imsi);
        // Unknown operators retain network-selected defaults, never a guessed APN.
        return {ok:1,source:settings?'auto':'default',settings:settings || options({pdptype:'IPV4V6'})};
    }
    let p=c.get_all(cfg,b.profile);
    if(!p || p['.type']!='profile' || !validate(p))return fail('apn_missing');
    return {ok:1,source:'sim',profile:b.profile,settings:options(p)};
};
export function get(c,identity) {
    let iface=c.get('unisoc_modem','main','data_interface') || 'wan';
    if(!match(iface,/^[A-Za-z0-9_]{1,32}$/) || c.get('network',iface)!='interface' ||
       c.get('network',iface,'proto')!='mu300cell') return fail('apn_unsupported');
    let current=options(c.get_all('network',iface));
    let selected=binding(c,identity), resolved=resolve(c,identity,current);
    if(resolved.ok)current=resolved.settings;
    // Credentials are only returned by the private dialer resolve path.
    let publicCurrent={...current,has_password:!!length(current.password)};delete publicCurrent.password;
    let profiles=[];
    c.foreach(cfg,'profile',s=>{
        let publicOptions=options(s);delete publicOptions.password;
        push(profiles,{...publicOptions,id:s['.name'],name:s.name || s['.name'],has_password:!!length(s.password || ''),
            current:selected?selected.mode=='manual' && selected.profile==s['.name']:same(s,current)});
    });
    return {ok:1,iface,current:publicCurrent,profiles,data_sim:c.get('unisoc_modem','main','data_sim')=='1'?1:0,
        remembered:!!selected,mode:selected?.mode || 'global',identity_ready:!!valid_identity(identity),source:resolved.source || 'error'};
};
export function change(c,action,p,reload,identity) {
    let state=get(c,identity);
    if(!state.ok)return state;
    if(!(action in ['save','delete','apply','auto']))return fail('apn_invalid');
    if(type(p)!='object')return fail('apn_invalid');
    if(length(c.changes(cfg) ?? {}) || length(c.changes('network') ?? {}) ||
       length(c.changes('unisoc_modem') ?? {})) return fail('apn_unsaved');
    let id=p.id || '', profile=null;
    if(id) {
        if(!valid_id(id))return fail('apn_invalid');
        for(let item in state.profiles)if(item.id==id)profile=item;
        if(!profile)return fail('apn_missing');
    }
    let remember=p.remember===true || action=='auto';
    if((action=='apply' || action=='auto') && remember && !valid_identity(identity))return fail('apn_identity');
    let target=action=='apply' && !remember?'network':cfg;
    function failed() { c.revert(target);return fail('apn_storage'); }
    if(action=='save') {
        if(profile && p.password==null)p.password=c.get(cfg,id,'password') || '';
        if(!validate(p))return fail('apn_invalid');
        if(!id && length(state.profiles)>=32)return fail('apn_limit');
        for(let item in state.profiles)
            if(item.id!=id && same(c.get_all(cfg,item.id),p))return fail('apn_duplicate');
        if(!id)id=c.add(cfg,'profile');
        if(!id || !c.set(cfg,id,'name',trim(p.name)) || !c.set(cfg,id,'apn',p.apn) ||
           !c.set(cfg,id,'pdptype',p.pdptype))return failed();
        for(let key,value in options(p))if(!c.set(cfg,id,key,value))return failed();
    } else if(action=='auto' || (action=='apply' && remember)) {
        if(action=='apply' && (!profile || !validate(c.get_all(cfg,id))))return fail('apn_invalid');
        let b=binding(c,identity), sid=b?.['.name'] || c.add(cfg,'selection');
        if(!sid || !c.set(cfg,sid,'iccid',identity.iccid) || !c.set(cfg,sid,'mode',action=='auto'?'auto':'manual') ||
            !c.set(cfg,sid,'profile',action=='auto'?'':id))return failed();
    } else {
        if(!profile)return fail('apn_missing');
        if(action=='delete') {
            if(profile.current)return fail('apn_current');
            let used=false;c.foreach(cfg,'selection',s=>{if(s.mode=='manual' && s.profile==id)used=true;});
            if(used)return fail('apn_bound');
            if(!c.delete(cfg,id))return failed();
        } else {
            let settings=c.get_all(cfg,id);
            if(!validate(settings))return fail('apn_invalid');
            if(binding(c,identity))return fail('apn_scope');
            if(profile.current)return reload(state.iface)?{ok:1,unchanged:1}:{ok:0,saved:1,error:'apn_reload'};
            if(!c.set('network',state.iface,'apn',profile.apn) ||
               !c.set('network',state.iface,'pdptype',profile.pdptype))return failed();
            for(let key,value in options(settings))if(!c.set('network',state.iface,key,value))return failed();
        }
    }
    if(!c.commit(target))return failed();
    if((action=='apply' || action=='auto') && !reload(state.iface))return {ok:0,saved:1,error:'apn_reload'};
    return {ok:1,id,pending:action=='apply' || action=='auto'};
};
