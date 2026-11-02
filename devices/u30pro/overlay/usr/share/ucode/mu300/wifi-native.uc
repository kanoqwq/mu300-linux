// U30 Pro STA integration. netifd/wpad own interfaces, DHCP, DNS and routing.
// No direct wpa_supplicant process, SC2355 workaround or second setting store.
import { error as bus_error } from 'ubus';
function interfaces(c) {
    let rows=[];
    c.foreach('wireless','wifi-iface',s=>{if(s.mode=='sta')push(rows,s);});
    return rows;
}
function selected(c) {
    let rows=interfaces(c), chosen=c.get('unisoc_modem','main','wifi_client_iface');
    if(chosen) {
        let found=filter(rows,s=>s['.name']==chosen);
        if(length(found)!=1)die('Selected client interface is unavailable / 所选客户端接口不存在');
        return found[0];
    }
    if(length(rows)>1)die('Select the client interface first / 存在多个客户端接口，请先选择');
    return rows[0];
}
export function status(c,bus) {
    let s=selected(c), configured=length(s?.ssid || '')>0;
    let result={configured,section:s?.['.name'],enabled:configured && s.disabled!='1',
        cfg:s?.ssid || '',ssid:'',state:0,device:s?.ifname || 'wlan1',address:null};
    if(!result.enabled)return result;
    result.state=1;
    let radio=bus.call('network.wireless','status',{})?.[s.device];
    for(let i in radio?.interfaces || [])if(i.section==s['.name'])result.device=i.ifname;
    let network=s.network;
    if(type(network)=='array')network=network[0];
    if(type(network)=='string' && match(network,/^[A-Za-z0-9_]+$/)) {
        let link=bus.call('network.interface.'+network,'status',{}) || {};
        if(link.up && length(link['ipv4-address'] || [])) {
            result.state=2;result.ssid=s.ssid;result.address=link['ipv4-address'][0].address;
        }
    }
    return result;
};
function check_pending(c) {
    if(length(c.changes('wireless') || {}))die('Resolve unsaved wireless changes first / 请先保存或撤销无线页面的更改');
}
function apply(c,bus) {
    if(!c.commit('wireless'))die('Cannot save wireless settings / 无法保存无线配置');
    bus.call('network','reload',{});
    // A successful reload is allowed to return no body; the ubus error is
    // authoritative, as in the shared APN reconnect implementation.
    if(bus_error(true))die('Settings saved; network reload failed / 配置已保存，但网络应用失败');
    return {ok:true,pending:true};
}
export function dispatch(c,bus,action,args) {
    let s=selected(c);
    if(action=='status')return status(c,bus);
    if(action=='scan') {
        let iface=c.get('unisoc_modem','main','wifi_device') || 'wlan0';
        let found=bus.call('iwinfo','scan',{device:iface});
        if(found==null)die('Wireless scan failed / 无线扫描失败');
        return {ok:true,results:found.results || []};
    }
    check_pending(c);
    if(action=='connect') {
        let ssid=args[0], password=args[1] || '';
        if(type(ssid)!='string' || length(ssid)<1 || length(ssid)>32 || index(ssid,'\x00')>=0 || match(ssid,/[\r\n]/))
            die('Invalid SSID / 热点名称无效');
        if(type(password)!='string' || (password!='' &&
            !((length(password)>=8 && length(password)<=63 && index(password,'\x00')<0 && !match(password,/[\r\n]/)) || match(password,/^[0-9A-Fa-f]{64}$/))))
            die('Invalid Wi-Fi key / Wi-Fi 密码无效');
        let radio=c.get('unisoc_modem','main','wifi_radio') || 'radio0';
        if(c.get('wireless',radio)!='wifi-device')die('Wireless radio unavailable / 无线电不存在');
        if(!s) {
            if(c.get('wireless','wwan')!=null)die('The wwan section is already used / wwan 配置节已被占用');
            if(c.get('network','wwan')!='interface')die('Client uplink is unavailable / 客户端上行接口不存在');
            c.set('wireless','wwan','wifi-iface');
            c.set('wireless','wwan','mode','sta');
            c.set('wireless','wwan','device',radio);
            c.set('wireless','wwan','network','wwan');
            c.set('wireless','wwan','ifname','wlan1');
            s=c.get_all('wireless','wwan');
        }
        c.set('wireless',s['.name'],'ssid',ssid);
        c.set('wireless',s['.name'],'encryption',password==''?'none':match(password,/^[0-9A-Fa-f]{64}$/)?'psk2':'sae-mixed');
        if(password=='')c.delete('wireless',s['.name'],'key');
        else c.set('wireless',s['.name'],'key',password);
        c.set('wireless',s['.name'],'disabled','0');
    } else if(action=='up') {
        if(!length(s?.ssid || ''))die('No saved Wi-Fi network / 没有已保存的 Wi-Fi 网络');
        c.set('wireless',s['.name'],'disabled','0');
    } else if(action=='disconnect') {
        if(!s)return {ok:true,unchanged:true};
        c.set('wireless',s['.name'],'disabled','1');
    } else if(action=='forget') {
        if(!s)return {ok:true,unchanged:true};
        c.delete('wireless',s['.name']);
    } else die('Unknown Wi-Fi action / 未知 Wi-Fi 操作');
    return apply(c,bus);
};
