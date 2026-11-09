'use strict';
'require baseclass';
'require rpc';
'require mu300.common as M';

const get=rpc.declare({object:'unisoc-network-performance',method:'get_offload',nobatch:true,expect:{}});
const apply=rpc.declare({object:'unisoc-network-performance',method:'apply_offload',params:['enabled'],nobatch:true,expect:{}});
const words={
 '软件流量卸载':['Software flow offloading','Yazılımsal akış hızlandırma'],
 '刷新':['Refresh','Yenile'],
 '正在读取…':['Loading…','Yükleniyor…'],
 '正在应用…':['Applying…','Uygulanıyor…'],
 '已开启':['Enabled','Açık'],
 '已关闭':['Disabled','Kapalı'],
 '已应用并保存':['Applied and saved','Uygulandı ve kaydedildi'],
 '加速热点与 USB 的转发流量，可与固定 TTL 同时使用。更改后会重载防火墙，设置重启后保留。':['Accelerates forwarded hotspot and USB traffic and works with fixed TTL. Changes reload the firewall and persist across reboots.','Hotspot ve USB üzerinden yönlendirilen trafiği hızlandırır; sabit TTL ile birlikte çalışır. Değişiklikler güvenlik duvarını yeniden yükler ve yeniden başlatmalarda korunur.'],
 '当前系统不提供流量卸载设置':['Flow offloading settings are unavailable on this system','Bu sistem akış hızlandırma ayarlarını sağlamıyor'],
 '无法读取流量卸载设置，请刷新重试':['Unable to read flow offloading settings; refresh to retry','Akış hızlandırma ayarları okunamadı; yenileyip tekrar deneyin'],
 '流量卸载设置无效':['Invalid flow offloading setting','Geçersiz akış hızlandırma ayarı'],
 '网络设置正在应用，请稍后重试':['Network settings are being applied; retry shortly','Ağ ayarları uygulanıyor; kısa süre sonra tekrar deneyin'],
 '防火墙有待应用的更改，请先应用或放弃':['Apply or discard pending firewall changes first','Önce bekleyen güvenlik duvarı değişikliklerini uygulayın veya iptal edin'],
 '无法保存流量卸载设置':['Unable to save flow offloading settings','Akış hızlandırma ayarları kaydedilemedi'],
 '防火墙重载失败，流量卸载未应用':['Firewall reload failed; flow offloading was not applied','Güvenlik duvarı yeniden yüklenemedi; akış hızlandırma uygulanmadı'],
 '恢复原设置未完成，请刷新核对实际状态':['Restoring the original settings was incomplete; refresh to check the actual state','Önceki ayarlara dönüş tamamlanamadı; gerçek durumu denetlemek için yenileyin']
};
function t(text) {const lang=M.uiLanguage();return lang==='zh'?text:(words[text] || [text,text])[lang==='tr'?1:0];}
function node(tag,text,cls) {const e=document.createElement(tag);if(text!=null)e.textContent=t(text);if(cls)e.className=cls;return e;}
function failure(state) {
 const keys={offload_invalid:'流量卸载设置无效',offload_unsupported:'当前系统不提供流量卸载设置',
  offload_busy:'网络设置正在应用，请稍后重试',network_busy:'网络设置正在应用，请稍后重试',
  offload_pending_changes:'防火墙有待应用的更改，请先应用或放弃',offload_read_failed:'无法读取流量卸载设置，请刷新重试',
  offload_save_failed:'无法保存流量卸载设置',offload_apply_failed:'防火墙重载失败，流量卸载未应用'};
 return t(keys[state && state.error] || '无法读取流量卸载设置，请刷新重试')+
  (state && state.rollback===false?' · '+t('恢复原设置未完成，请刷新核对实际状态'):'');
}
return baseclass.extend({mount:function(root) {
 const section=node('div',null,'mud-flow-offload');
 section.style.cssText='margin-top:18px;padding-top:16px;border-top:1px solid var(--hairline,var(--border,#ddd))';
 const head=node('div',null,'mud-device-head'), refresh=node('button','刷新','mud-btn');refresh.type='button';
 head.append(node('h3','软件流量卸载'),refresh);
 const row=node('div',null,'mud-r'), label=node('span','软件流量卸载','mud-k');label.id='mud-flow-offload-label';
 const toggle=node('button',null,'mud-btn mud-voice-switch');toggle.type='button';toggle.id='mud-flow-offload-switch';
 toggle.setAttribute('role','switch');toggle.setAttribute('aria-labelledby',label.id);toggle.setAttribute('aria-checked','false');
 const track=node('span',null,'mud-voice-track');track.setAttribute('aria-hidden','true');toggle.append(track);row.append(label,toggle);
 const result=node('div',null,'mud-note');result.style.marginTop='10px';result.setAttribute('role','status');result.setAttribute('aria-live','polite');
 const spinner=node('i',null,'mud-spin'), current=node('span','正在读取…');result.append(spinner,current);
 section.append(head,row,node('p','加速热点与 USB 的转发流量，可与固定 TTL 同时使用。更改后会重载防火墙，设置重启后保留。','mud-note'),result);root.append(section);
 let state=null,busy=false,disposed=false;
 const writable=typeof L.hasViewPermission!=='function' || L.hasViewPermission();
 function disable() {toggle.disabled=busy || !writable || !state || !state.supported;refresh.disabled=busy;spinner.hidden=!busy;spinner.style.display=busy?'inline-block':'none';section.setAttribute('aria-busy',String(busy));}
 function paint(value) {
  if(!value || !value.ok || typeof value.supported!=='boolean' || typeof value.enabled!=='boolean')throw Error('invalid offload reply');
  state=value;toggle.classList.toggle('on',value.enabled);toggle.setAttribute('aria-checked',String(value.enabled));
  current.textContent=t(!value.supported?'当前系统不提供流量卸载设置':value.enabled?'已开启':'已关闭');disable();
 }
 async function reload() {
  if(disposed || busy)return;busy=true;disable();current.textContent=t('正在读取…');
  try {const value=await get();if(disposed)return;if(!value || !value.ok)throw Object.assign(Error(failure(value)),{localized:true});paint(value);}
  catch(e){if(!disposed){state=null;current.textContent=e.localized?e.message:t('无法读取流量卸载设置，请刷新重试');}}
  finally{busy=false;if(!disposed)disable();}
 }
 toggle.onclick=async()=>{
  if(disposed || busy || !writable || !state || !state.supported)return;
  const wanted=!state.enabled;busy=true;disable();current.textContent=t('正在应用…');
  try {
   const value=await apply(wanted);if(disposed)return;
   if(!value || !value.ok)throw Object.assign(Error(failure(value)),{localized:true});
   paint(value);current.textContent=t('已应用并保存');
  } catch(e) {
   if(disposed)return;
   const text=e.localized?e.message:t('无法读取流量卸载设置，请刷新重试');
   state=null;
   try {const value=await get();if(!disposed && value && value.ok)paint(value);}catch(_){}
   if(!disposed)current.textContent=text;
  } finally{busy=false;if(!disposed)disable();}
 };
 refresh.onclick=reload;reload();
 return {dispose:()=>{disposed=true;}};
}});
