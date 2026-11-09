'use strict';
'require baseclass';
'require rpc';
'require mu300.common as M';
'require mu300.flow-offload as FlowOffload';
const get=rpc.declare({object:'unisoc-network-performance',method:'get',nobatch:true,expect:{}});
const apply=rpc.declare({object:'unisoc-network-performance',method:'apply',params:['algorithm'],nobatch:true,expect:{}});
const words={
 '网络性能':['Network performance','Ağ performansı'],
 '当前拥塞控制':['Active congestion control','Etkin tıkanıklık denetimi'],
 '默认队列':['Default queue','Varsayılan kuyruk'],
 'TCP 拥塞控制':['TCP congestion control','TCP tıkanıklık denetimi'],
 '系统默认':['System default','Sistem varsayılanı'],
 '刷新':['Refresh','Yenile'],
 '保存并应用':['Save and apply','Kaydet ve uygula'],
 '正在读取…':['Loading…','Yükleniyor…'],
 '正在应用…':['Applying…','Uygulanıyor…'],
 '已应用并保存，开机后继续生效。':['Applied and saved for future boots.','Uygulandı ve sonraki açılışlar için kaydedildi.'],
 '主要影响设备自身新建的 TCP 连接；不会将热点或 USB 转发的客户端连接统一改为 BBR。':['Mainly affects new TCP connections made by this device. Forwarded hotspot and USB client connections do not automatically use BBR.','Temel olarak bu cihazın yeni TCP bağlantılarını etkiler. Hotspot ve USB üzerinden yönlendirilen istemci bağlantıları otomatik olarak BBR kullanmaz.'],
 'BBR 配套使用 FQ 作为新建接口的默认队列；现有连接、接口和 QoS 不重置。设置重启后保留。':['BBR selects FQ as the default queue for newly created interfaces. Existing connections, interfaces and QoS are retained. Settings persist across reboots.','BBR, yeni arayüzler için varsayılan kuyruk olarak FQ kullanır. Mevcut bağlantılar, arayüzler ve QoS korunur. Ayarlar yeniden başlatmada saklanır.'],
 '当前内核不提供此设置':['This kernel does not provide this setting','Bu çekirdek bu ayarı sağlamıyor'],
 '无法读取网络性能设置，请刷新重试':['Unable to read network performance settings; refresh to retry','Ağ performansı ayarları okunamadı; yenileyip tekrar deneyin'],
 '网络性能设置无效':['Invalid network performance settings','Geçersiz ağ performansı ayarları'],
 '网络性能设置正在应用，请稍后重试':['Network settings are being applied; retry shortly','Ağ ayarları uygulanıyor; kısa süre sonra tekrar deneyin'],
 '当前内核不支持所选算法':['The kernel does not support this algorithm','Çekirdek bu algoritmayı desteklemiyor'],
 '内核未接受网络性能设置':['The kernel did not accept the network settings','Çekirdek ağ ayarlarını kabul etmedi'],
 '无法保存网络性能设置':['Unable to save network performance settings','Ağ performansı ayarları kaydedilemedi'],
 '恢复原设置未完成，请刷新核对实际状态':['Restoring the original settings was incomplete; refresh to check the actual state','Önceki ayarlara dönüş tamamlanamadı; gerçek durumu denetlemek için yenileyin']
};
function t(s) {const lang=M.uiLanguage();return lang==='zh'?s:(words[s] || [s,s])[lang==='tr'?1:0];}
function message(state) {
 const key={network_invalid:'网络性能设置无效',network_busy:'网络性能设置正在应用，请稍后重试',
  network_unsupported:'当前内核不支持所选算法',network_apply_failed:'内核未接受网络性能设置',
  network_save_failed:'无法保存网络性能设置',network_read_failed:'无法读取网络性能设置，请刷新重试'};
 return t(key[state && state.error] || '无法读取网络性能设置，请刷新重试')+
  (state && state.rollback===false?' · '+t('恢复原设置未完成，请刷新核对实际状态'):'');
}
function node(tag,text,cls) {const e=document.createElement(tag);if(text!=null)e.textContent=t(text);if(cls)e.className=cls;return e;}
return baseclass.extend({mount:function(root) {
 const card=node('section',null,'mud-card mud-network-performance');card.style.marginTop='14px';
 const head=node('div',null,'mud-device-head'), refresh=node('button','刷新','mud-btn');refresh.type='button';
 head.append(node('h3','网络性能'),refresh);
 const current=node('span','--','mud-v'), qdisc=node('span','--','mud-v');
 function row(label,value) {const r=node('div',null,'mud-r');r.append(node('span',label,'mud-k'),value);return r;}
 const select=node('select');select.id='mud-network-algorithm';select.disabled=true;
 const field=node('div',null,'mud-device-field'), label=node('label','TCP 拥塞控制');label.htmlFor=select.id;field.append(label,select);
 const save=node('button','保存并应用','mud-btn');save.type='button';save.disabled=true;
 const actions=node('div',null,'mud-device-actions');actions.append(save);
 const result=node('p','正在读取…','mud-note');result.style.marginTop='12px';result.setAttribute('role','status');result.setAttribute('aria-live','polite');
 card.append(head,row('当前拥塞控制',current),row('默认队列',qdisc),field,actions,
  node('p','主要影响设备自身新建的 TCP 连接；不会将热点或 USB 转发的客户端连接统一改为 BBR。','mud-note'),
  node('p','BBR 配套使用 FQ 作为新建接口的默认队列；现有连接、接口和 QoS 不重置。设置重启后保留。','mud-note'),result);
 root.append(card);
 const offload=FlowOffload.mount(card);
 let state=null,busy=false,disposed=false;
 const writable=typeof L.hasViewPermission!=='function' || L.hasViewPermission();
 function disable() {select.disabled=save.disabled=busy || !writable || !state || !state.supported;refresh.disabled=busy;card.setAttribute('aria-busy',String(busy));}
 function paint(s) {
  state=s;current.textContent=s.current || '--';qdisc.textContent=s.qdisc || '--';select.replaceChildren();
  const def=node('option');def.value='default';def.textContent=t('系统默认')+' ('+((s.defaults || {}).algorithm || '--').toUpperCase()+')';select.append(def);
  ['cubic','bbr'].forEach(v=>{if((s.available || []).includes(v)){const o=node('option');o.value=v;o.textContent=v.toUpperCase();select.append(o);}});
  select.value=Array.from(select.options).some(o=>o.value===s.selection)?s.selection:'default';disable();
 }
 async function reload() {
  if(disposed || busy)return;busy=true;disable();result.textContent=t('正在读取…');
  try {const s=await get();if(disposed)return;if(!s.ok)throw Object.assign(Error(message(s)),{localized:true});paint(s);result.textContent=s.supported?'':t('当前内核不提供此设置');}
  catch(e){if(!disposed){state=null;result.textContent=e.localized?e.message:t('无法读取网络性能设置，请刷新重试');result.title=e.message || '';}}
  finally{busy=false;if(!disposed)disable();}
 }
 save.onclick=async()=>{
  if(disposed || busy || !writable || !state || !state.supported)return;
  busy=true;disable();result.textContent=t('正在应用…');
  try {const s=await apply(select.value);if(disposed)return;if(!s.ok)throw Object.assign(Error(message(s)),{localized:true});paint(s);result.textContent=t('已应用并保存，开机后继续生效。');}
  catch(e){if(!disposed){result.textContent=e.localized?e.message:t('内核未接受网络性能设置');result.title=e.message || '';try{const s=await get();if(!disposed && s.ok)paint(s);}catch(_){} }}
  finally{busy=false;if(!disposed)disable();}
 };
 refresh.onclick=reload;reload();
 return {dispose:()=>{disposed=true;offload.dispose();}};
}});
