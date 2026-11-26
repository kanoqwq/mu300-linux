'use strict';
'require baseclass';
'require rpc';
'require mu300.common as M';

const get=rpc.declare({object:'unisoc-roaming',method:'get',params:['slot'],nobatch:true,expect:{}});
const set=rpc.declare({object:'unisoc-roaming',method:'set',params:['slot','enabled'],nobatch:true,expect:{}});
const words={
 '数据漫游':['Data roaming','Veri dolaşımı'],
 '允许数据漫游':['Allow data roaming','Veri dolaşımına izin ver'],
 '刷新':['Refresh','Yenile'],
 '正在读取…':['Loading…','Yükleniyor…'],
 '正在保存…':['Saving…','Kaydediliyor…'],
 '已允许':['Allowed','İzin verildi'],
 '已禁止':['Blocked','Engellendi'],
 '设置已保存':['Settings saved','Ayarlar kaydedildi'],
 '默认关闭，按当前查看的 SIM 保存。关闭后仍可驻网、通话和收短信，只禁止漫游网络的数据连接；开启可能产生漫游费用。':['Off by default and saved for the SIM being viewed. Turning it off only blocks roaming data; registration, calls and SMS remain available. Enabling it may incur roaming charges.','Varsayılan olarak kapalıdır ve görüntülenen SIM için kaydedilir. Kapalıyken yalnızca dolaşım verisi engellenir; şebeke kaydı, aramalar ve SMS kullanılabilir. Açmak dolaşım ücreti doğurabilir.'],
 '当前处于漫游网络，数据连接已禁止':['Currently roaming; data is blocked','Şu anda dolaşımda; veri engellendi'],
 '无法读取数据漫游设置，请刷新重试':['Unable to read data roaming settings; refresh to retry','Veri dolaşımı ayarı okunamadı; yenileyip tekrar deneyin'],
 '保存数据漫游设置失败，请刷新后重试':['Unable to save data roaming settings; refresh and retry','Veri dolaşımı ayarı kaydedilemedi; yenileyip tekrar deneyin'],
 '设置已保存，但未能通知网络服务，请刷新后重试':['Settings saved, but the network service was not notified; refresh and retry','Ayar kaydedildi ancak ağ hizmetine bildirim gönderilemedi; yenileyip tekrar deneyin'],
 '查看卡槽已改变，请重新操作':['The viewed SIM changed; try again','Görüntülenen SIM değişti; tekrar deneyin']
};
function t(s){const l=M.uiLanguage();return l==='zh'?s:(words[s]||[s,s])[l==='tr'?1:0];}
function node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=t(text);if(cls)e.className=cls;return e;}
return baseclass.extend({mount:function(root){
 let slot=null;
 const card=node('section',null,'mud-sec');
 const head=node('h3','数据漫游'),refresh=node('button','刷新','mud-btn');refresh.type='button';
 const row=node('div',null,'mud-r mud-voice-row'),label=node('label','允许数据漫游','mud-k');
 const toggle=node('button',null,'mud-btn mud-voice-switch');toggle.type='button';toggle.id='mud-roaming-switch';label.htmlFor=toggle.id;
 toggle.setAttribute('role','switch');toggle.setAttribute('aria-label',t('允许数据漫游'));toggle.setAttribute('aria-checked','false');
 const track=node('span',null,'mud-voice-track');track.setAttribute('aria-hidden','true');toggle.append(track);
 label.style.flex='1';row.append(label,refresh,toggle);
 const status=node('div',null,'mud-note');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const spinner=node('i',null,'mud-spin'),text=node('span','正在读取…');status.append(spinner,text);
 card.append(head,row,node('p','默认关闭，按当前查看的 SIM 保存。关闭后仍可驻网、通话和收短信，只禁止漫游网络的数据连接；开启可能产生漫游费用。','mud-note'),status);root.append(card);
 let state=null,busy=false,disposed=false;
 const writable=typeof L.hasViewPermission!=='function'||L.hasViewPermission();
 function controls(){toggle.disabled=busy||!writable||!state;refresh.disabled=busy;spinner.style.display=busy?'inline-block':'none';card.setAttribute('aria-busy',String(busy));}
 function valid(s){return s&&s.ok===1&&s.slot===Number(slot)&&(s.enabled===0||s.enabled===1);}
 function paint(s){state=s;toggle.classList.toggle('on',s.enabled===1);toggle.setAttribute('aria-checked',String(s.enabled===1));text.textContent=t(s.blocked?'当前处于漫游网络，数据连接已禁止':s.enabled?'已允许':'已禁止');}
 async function request(save){
  if(disposed||busy||save&&(!writable||!state))return;
  busy=true;controls();text.textContent=t(save?'正在保存…':'正在读取…');
  try{
   // Follow-data mode is asynchronous at page startup. Bind the state to the
   // resolved SIM, never the temporary SIM1 display fallback. A refresh may
   // rebind, while a save must still match the card whose state was displayed.
   const selected=await M.resolveSelectedSlot();
   if(disposed)return;
   if(save&&selected!==slot){state=null;text.textContent=t('查看卡槽已改变，请重新操作');return;}
   if(!save){slot=selected;state=null;}
   const target=slot;
   const s=save?await set(target,state.enabled?'0':'1'):await get(target);
   if(disposed)return;
   if(M.selectedSlot()!==target){state=null;text.textContent=t('查看卡槽已改变，请重新操作');return;}
   if(!valid(s))throw Error(s&&s.error||'failed');
   paint(s);if(save)M.toast(t('设置已保存'),{type:'success'});
  }catch(e){
   if(disposed)return;
   state=null;
   text.textContent=t(e.message==='roaming_notify_failed'?'设置已保存，但未能通知网络服务，请刷新后重试':save?'保存数据漫游设置失败，请刷新后重试':'无法读取数据漫游设置，请刷新重试');
   if(save)M.toast(text.textContent,{type:'error'});
  }finally{busy=false;if(!disposed)controls();}
 }
 toggle.onclick=()=>request(true);refresh.onclick=()=>request(false);request(false);
 return {dispose:()=>{disposed=true;}};
}});
