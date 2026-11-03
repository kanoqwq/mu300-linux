'use strict';
'require baseclass';
'require rpc';
'require mu300.common as M';

const get=rpc.declare({object:'unisoc-nfc',method:'get',nobatch:true,expect:{}});
const set=rpc.declare({object:'unisoc-nfc',method:'set',params:['enabled'],nobatch:true,expect:{}});
const words={
 '刷新':['Refresh','Yenile'],
 '正在读取…':['Loading…','Yükleniyor…'],
 '正在应用…':['Applying…','Uygulanıyor…'],
 '已开启':['Enabled','Açık'],
 '已关闭':['Disabled','Kapalı'],
 '已应用并保存':['Applied and saved','Uygulandı ve kaydedildi'],
 '靠近设备即可读取 NFC 内容，开关设置会自动保存。':['Tap a phone against the device to read its NFC content. The switch setting is saved automatically.','NFC içeriğini okumak için telefonu cihaza yaklaştırın. Anahtar ayarı otomatik kaydedilir.'],
 '无法读取 NFC 状态，请刷新重试':['Unable to read NFC status; refresh to retry','NFC durumu okunamadı; yenileyip tekrar deneyin'],
 'NFC 设置无效':['Invalid NFC setting','Geçersiz NFC ayarı'],
 '当前设备不支持 NFC':['This device does not support NFC','Bu cihaz NFC desteklemiyor'],
 'NFC 暂不可用，请刷新重试':['NFC is unavailable; refresh to retry','NFC kullanılamıyor; yenileyip tekrar deneyin'],
 'NFC 正忙，请稍后重试':['NFC is busy; retry shortly','NFC meşgul; kısa süre sonra tekrar deneyin'],
 'NFC 设置失败，请刷新后重试':['Unable to apply NFC settings; refresh and retry','NFC ayarı uygulanamadı; yenileyip tekrar deneyin']
};
function t(s) {const lang=M.uiLanguage();return lang==='zh'?s:(words[s] || [s,s])[lang==='tr'?1:0];}
function node(tag,text,cls) {const e=document.createElement(tag);if(text!=null)e.textContent=t(text);if(cls)e.className=cls;return e;}
function failure(s) {
 const keys={nfc_invalid:'NFC 设置无效',nfc_unsupported:'当前设备不支持 NFC',nfc_unavailable:'NFC 暂不可用，请刷新重试',
  nfc_busy:'NFC 正忙，请稍后重试',nfc_read_failed:'无法读取 NFC 状态，请刷新重试',nfc_apply_failed:'NFC 设置失败，请刷新后重试'};
 return t(keys[s && s.error] || '无法读取 NFC 状态，请刷新重试');
}
return baseclass.extend({mount:function(root) {
 const card=node('section',null,'mud-card mud-nfc');card.style.marginTop='14px';
 const head=node('div',null,'mud-device-head'),refresh=node('button','刷新','mud-btn');refresh.type='button';
 head.append(node('h3','NFC'),refresh);
 const row=node('div',null,'mud-r'),label=node('label','NFC','mud-k');label.htmlFor='mud-nfc-switch';
 const toggle=node('button',null,'mud-btn mud-voice-switch');toggle.id='mud-nfc-switch';toggle.type='button';
 toggle.setAttribute('role','switch');toggle.setAttribute('aria-label','NFC');toggle.setAttribute('aria-checked','false');
 const track=node('span',null,'mud-voice-track');track.setAttribute('aria-hidden','true');toggle.append(track);
 row.append(label,toggle);
 const current=node('span','--'),spinner=node('i',null,'mud-spin');spinner.hidden=true;
 const result=node('div',null,'mud-note');result.style.marginTop='10px';result.setAttribute('role','status');result.setAttribute('aria-live','polite');result.append(spinner,current);
 card.append(head,row,node('p','靠近设备即可读取 NFC 内容，开关设置会自动保存。','mud-note'),result);root.append(card);
 let state=null,busy=false,disposed=false;
 const writable=typeof L.hasViewPermission!=='function' || L.hasViewPermission();
 function disable() {toggle.disabled=busy || !writable || !state || !state.supported;refresh.disabled=busy;spinner.hidden=!busy;spinner.style.display=busy?'inline-block':'none';card.setAttribute('aria-busy',String(busy));}
 function paint(s) {
  if(!s || !s.ok || s.supported && s.enabled!==0 && s.enabled!==1 && typeof s.enabled!=='boolean')throw Error('invalid NFC reply');
  state=s;card.hidden=!s.supported;toggle.classList.toggle('on',!!s.enabled);toggle.setAttribute('aria-checked',String(!!s.enabled));
  current.textContent=t(s.enabled?'已开启':'已关闭');disable();
 }
 async function reload() {
  if(disposed || busy)return;busy=true;disable();current.textContent=t('正在读取…');
  try {const s=await get();if(disposed)return;if(!s || !s.ok)throw Object.assign(Error(failure(s)),{localized:true});paint(s);}
  catch(e){if(!disposed){state=null;current.textContent=e.localized?e.message:t('无法读取 NFC 状态，请刷新重试');}}
  finally{busy=false;if(!disposed)disable();}
 }
 toggle.onclick=async()=>{
  if(disposed || busy || !writable || !state || !state.supported)return;
  const desired=state.enabled?'0':'1';busy=true;disable();current.textContent=t('正在应用…');
  try {
   const s=await set(desired);if(disposed)return;
   if(!s || !s.ok)throw Object.assign(Error(failure(s)),{localized:true});
   paint(s);current.textContent=t('已应用并保存');M.toast(t('已应用并保存'),'success');
  } catch(e) {
   if(disposed)return;
   const message=e.localized?e.message:t('NFC 设置失败，请刷新后重试');
   try {const s=await get();if(!disposed && s && s.ok)paint(s);}catch(_){}
   if(!disposed){current.textContent=message;M.toast(message,'error');}
  } finally {busy=false;if(!disposed)disable();}
 };
 refresh.onclick=reload;reload();
 return {dispose:()=>{disposed=true;}};
}});
