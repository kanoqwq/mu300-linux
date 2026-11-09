// Explicit software-flow policy. No modem commands, polling or new daemon.
import * as fs from 'fs';
import * as uci from 'uci';

const ROOT=getenv('UNISOC_NET_ROOT') || '';
const FW4=getenv('UNISOC_FLOW_FW4') || '/sbin/fw4';
const MARK=ROOT+'/etc/mu300/ttl.offload-off';
// Never commit unrelated LuCI/CLI deltas with this single-option operation.
const cfg=uci.cursor(ROOT+'/etc/config',ROOT+'/run/unisoc-flow-offload-deltas','');
function section() { return cfg?.get_first('firewall','defaults'); }
function status() {
    let id=section(), value=id ? cfg.get('firewall',id,'flow_offloading') : null;
    if(value!=null && value!='0' && value!='1')die('offload_read_failed');
    return {ok:true,supported:!!id && !!fs.stat(FW4),enabled:value=='1'};
}
function reload() {
    // User firewall hooks must not inherit either operation lock or RPC stdin.
    let command="'"+replace(FW4,/'/g,"'\\''")+"' -q reload </dev/null >/dev/null 2>&1 8>&- 9>&-";
    let pipe=fs.popen(command,'r');
    if(!pipe)return false;
    pipe.read('all');return pipe.close()==0;
}
function set(id,value) {
    return (value==null ? cfg.delete('firewall',id,'flow_offloading') : cfg.set('firewall',id,'flow_offloading',value)) && cfg.commit('firewall');
}
function marker(text) {
    if(text==null)return !fs.stat(MARK) || fs.unlink(MARK);
    let temp=MARK+'.new';
    if(fs.writefile(temp,text)==length(text) && fs.chmod(temp,384) && fs.rename(temp,MARK))return true;
    fs.unlink(temp);return false;
}
try {
    let action=ARGV[0] || 'get';
    if(action=='get') { print(sprintf('%J\n',status()));exit(0); }
    if(action!='apply')die('offload_invalid');
    let request=json(fs.readfile('/dev/stdin') || '{}');
    if(type(request)!='object')die('offload_invalid');
    delete request.ubus_rpc_session;
    if(length(keys(request))!=1 || type(request.enabled)!='bool')die('offload_invalid');
    let state=status();
    if(!state.supported)die('offload_unsupported');
    // A pending firewall edit must stay pending, rather than being silently
    // applied or left ready to overwrite this setting on the next LuCI save.
    for(let path in [ROOT+'/tmp/.uci/firewall',ROOT+'/var/run/uci/firewall'])
        if((fs.stat(path)?.size || 0)>0)die('offload_pending_changes');
    let id=section(), old=cfg.get('firewall',id,'flow_offloading');
    let saved=fs.readfile(MARK), updated=saved;
    if(saved!=null) {
        let entries=filter(split(trim(saved),'\n'),v=>length(v)>0);
        for(let entry in entries)
            if(index(['flow_offloading','flow_offloading_hw'],entry)<0)die('offload_read_failed');
        entries=filter(entries,v=>v!='flow_offloading');
        updated=length(entries) ? join('\n',entries)+'\n' : null;
    }
    // Retire only the legacy TTL software-restore ownership. Hardware restore
    // ownership remains intact, so disabling TTL cannot undo this user choice.
    if(updated!==saved && !marker(updated))die('offload_save_failed');
    let wanted=request.enabled?'1':'0';
    if(old==wanted) { print(sprintf('%J\n',status()));exit(0); }
    let error=null;
    if(!set(id,wanted))error='offload_save_failed';
    else if(!reload())error='offload_apply_failed';
    if(error) {
        let restored=set(id,old), markRestored=marker(saved);
        let activeRestored=restored && reload();
        print(sprintf('%J\n',{ok:false,error,rollback:restored && markRestored && activeRestored}));exit(1);
    }
    print(sprintf('%J\n',status()));
} catch(e) { print(sprintf('%J\n',{ok:false,error:replace(''+e,/\n.*/, '')}));exit(1); }
