'use strict';
'require baseclass';
'require rpc';
'require mu300.common as M';
var params=['short','long','short_command','long_command','power_short','power_command','double','double_command','power_double','power_double_command'];
var get=rpc.declare({object:'mu300dash',method:'button_get',nobatch:true,expect:{'':{}}});
var set=rpc.declare({object:'mu300dash',method:'button_set',params:params,nobatch:true,expect:{'':{}}});
var words={
 '实体按键':['Physical buttons','Fiziksel tuşlar'],
 '功能键 · 短按':['Function · short press','İşlev · kısa basma'],
 '功能键 · 双击':['Function · double press','İşlev · çift basma'],
 '电源键 · 双击':['Power · double press','Güç · çift basma'],
 '双击间隔为 500 毫秒。短按会等待此窗口结束，双击不会触发短按动作。':['Double-press window: 500 ms. A single press waits for this window; double presses do not run the single-press action.','Çift basma aralığı 500 ms. Tek basma bu sürenin bitmesini bekler; çift basma tek basma işlemini çalıştırmaz.'],
 '功能键 · 长按':['Function · long press','İşlev · uzun basma'],
 '电源键 · 短按':['Power · short press','Güç · kısa basma'],
 '电源键长按固定用于关机，不能自定义。':['Holding the power button always keeps its shutdown function.','Güç tuşuna uzun basma kapatma işlevini korur; değiştirilemez.'],
 '保留原有按键行为；设置后重启仍保留。':['Original actions remain until changed. Settings survive reboot.','Değiştirilene kadar mevcut işlevler korunur. Ayarlar yeniden başlatmada korunur.'],
 '默认行为':['Default','Varsayılan'],
 '无动作':['No action','İşlem yok'],
 '唤醒指示灯':['Wake indicator lights','Gösterge ışıklarını uyandır'],
 '唤醒／熄灭屏幕':['Wake / turn off screen','Ekranı uyandır / kapat'],
 '设置参数无效，请刷新页面后重试':['Invalid settings. Refresh the page and retry.','Geçersiz ayarlar. Sayfayı yenileyip tekrar deneyin.'],
 '设置无法写入，请检查可用空间和文件系统写入权限':['Cannot write settings. Check free space and filesystem write permissions.','Ayarlar yazılamıyor. Boş alanı ve dosya sistemi yazma izinlerini kontrol edin.'],
 '这台设备没有可自定义的电源键':['This device has no configurable power button.','Bu cihazda özelleştirilebilir güç tuşu yok.'],
 '切换上网卡':['Switch data SIM','Veri SIM’ini değiştir'],
 '切换 Wi-Fi 频段':['Switch Wi-Fi band','Wi-Fi bandını değiştir'],
 '切换 NFC 开关':['Toggle NFC','NFC’yi aç/kapat'],
 '自定义指令':['Custom command','Özel komut'],
 '一行 shell 命令':['One-line shell command','Tek satırlık shell komutu'],
 '以 root 身份执行，仅在按键触发时运行。保存不会执行命令。':['Runs as root when the button is pressed. Saving does not execute the command.','Tuşa basıldığında root olarak çalışır. Kaydetmek komutu çalıştırmaz.'],
 '下一页':['Next page','Sonraki sayfa'],
 '切换 Wi-Fi 开关':['Toggle Wi-Fi','Wi-Fi aç/kapat'],
 '重新拨号':['Reconnect cellular','Hücresel bağlantıyı yenile'],
 '刷新状态':['Refresh status','Durumu yenile'],
 '保存按键设置':['Save button settings','Tuş ayarlarını kaydet'],
 '正在保存…':['Saving…','Kaydediliyor…'],'已保存':['Saved','Kaydedildi'],
 '按键设置读取失败':['Unable to read button settings','Tuş ayarları okunamadı'],
 '按键设置保存失败':['Unable to save button settings','Tuş ayarları kaydedilemedi'],
 '请输入一行命令，不能包含换行。':['Enter a non-empty single-line command.','Boş olmayan tek satırlık bir komut girin.']
};
function t(s){var lang=M.uiLanguage();return lang==='zh'?s:(words[s]||[s,s])[lang==='tr'?1:0];}
// Describe the original input owners' actions; selecting 'default' retains
// those paths rather than saving a different action on the user's behalf.
function defaultAction(model,key){
 if(model==='u30pro')return key==='short'?'下一页':key==='power_short'?'唤醒／熄灭屏幕':'无动作';
 if(model==='u30air')return key==='short'||key==='power_short'?'唤醒指示灯':key==='long'?'切换 Wi-Fi 开关':key==='double'?'切换 Wi-Fi 频段':'无动作';
 return '无动作';
}
function replyError(reply){
 var names={invalid_button_settings:'设置参数无效，请刷新页面后重试',invalid_button_action:'设置参数无效，请刷新页面后重试',invalid_button_command:'请输入一行命令，不能包含换行。',empty_button_command:'请输入一行命令，不能包含换行。',storage_failed:'设置无法写入，请检查可用空间和文件系统写入权限',power_button_unavailable:'这台设备没有可自定义的电源键'};
 return Error(reply&&reply.error?(names[reply.error]?t(names[reply.error]):String(reply.error)):t('设置参数无效，请刷新页面后重试'));
}
function node(tag,text,cls){var n=document.createElement(tag);if(text)n.textContent=t(text);if(cls)n.className=cls;return n;}
return baseclass.extend({mount:function(root){
 var card=node('section',null,'mud-card mud-buttons');card.hidden=true;card.style.marginTop='14px';
 card.append(node('style','.mud-buttons[hidden],.mud-buttons [hidden]{display:none!important}.mud-buttons .mud-device-field{min-width:0}.mud-buttons .mud-note{line-height:1.6}'));
 card.append(node('h3','实体按键'),node('p','保留原有按键行为；设置后重启仍保留。','mud-note'));
 var fields=node('div',null,'mud-device-grid'),powerFields=node('div',null,'mud-device-grid'),rows=[],disposed=false,busy=true,loaded=false;
 var writable=typeof L.hasViewPermission!=='function'||L.hasViewPermission();
 fields.style.gridTemplateColumns=powerFields.style.gridTemplateColumns='repeat(auto-fit,minmax(min(100%,220px),1fr))';
 powerFields.style.cssText+=';margin-top:14px;padding-top:10px;border-top:1px solid var(--hairline,var(--border,#ccc))';
 card.append(fields,powerFields,node('p','双击间隔为 500 毫秒。短按会等待此窗口结束，双击不会触发短按动作。','mud-note'));
 [['short','short_command','功能键 · 短按',0],['double','double_command','功能键 · 双击',3],['long','long_command','功能键 · 长按',1],['power_short','power_command','电源键 · 短按',2],['power_double','power_double_command','电源键 · 双击',4]].forEach(function(item){
  var index=item[3];
  var row=node('div',null,'mud-device-field'),label=node('label',item[2]),select=node('select');
  select.id='mud-button-'+index;label.htmlFor=select.id;
  var options=[['default','默认行为'],['none','无动作'],['data-switch','切换上网卡'],['wifi-band','切换 Wi-Fi 频段'],['nfc-toggle','切换 NFC 开关'],['custom','自定义指令']];
  options.forEach(function(pair){var o=node('option',pair[1]);o.value=pair[0];select.append(o);});
  var commandBox=node('div'),commandLabel=node('label','一行 shell 命令'),command=node('input'),hint=node('p','以 root 身份执行，仅在按键触发时运行。保存不会执行命令。','mud-note');
  command.type='text';command.id=select.id+'-command';commandLabel.htmlFor=command.id;command.maxLength=4096;command.autocomplete='off';command.spellcheck=false;
  command.style.cssText='display:block;width:100%;min-width:0;box-sizing:border-box;margin-top:7px';
  commandBox.append(commandLabel,command,hint);row.append(label,select,commandBox);(item[0].indexOf('power_')===0?powerFields:fields).append(row);
  var field={row:row,select:select,command:command,box:commandBox,key:item[0],commandKey:item[1]};
  select.onchange=function(){commandBox.hidden=select.value!=='custom';};
  rows.push(field);
 });
 var powerNote=node('p','电源键长按固定用于关机，不能自定义。','mud-note'),save=node('button','保存按键设置','mud-btn'),result=node('p',null,'mud-note');
 save.type='button';result.setAttribute('role','status');card.append(powerNote,save,result);root.append(card);
 function disable(){save.disabled=busy||!loaded||!writable;rows.forEach(function(r){r.select.disabled=r.command.disabled=busy||!loaded||!writable;});card.setAttribute('aria-busy',String(busy));}
 function paint(reply){
  if(!reply||!reply.ok)throw replyError(reply);
  card.hidden=!reply.supported;if(!reply.supported)return;
  rows.forEach(function(r){r.row.hidden=r.key.indexOf('power_')===0&&!reply.power;});powerNote.hidden=!reply.power;powerFields.hidden=!reply.power;
  rows.forEach(function(r,index){
   var zh=M.uiLanguage()==='zh';
   r.select.querySelector('[value="default"]').textContent=t('默认行为')+(zh?'（':' (')+t(defaultAction(reply.model,r.key))+(zh?'）':')');
   if(reply.model==='u30pro'&&(r.key==='short'||r.key==='long')&&!r.select.querySelector('[value="page-next"]')){
    [['page-next','下一页'],['toggle-wifi','切换 Wi-Fi 开关'],['reconnect-cellular','重新拨号'],['refresh-status','刷新状态']].forEach(function(pair){var o=node('option',pair[1]);o.value=pair[0];r.select.append(o);});
   }
   r.select.value=reply[r.key]||(r.key.indexOf('double')>=0?'none':'default');
   if(!r.select.value)throw Error('invalid action');
   r.command.value=reply[r.commandKey]||'';r.box.hidden=r.select.value!=='custom';
  });loaded=true;
 }
 disable();get().then(function(r){if(!disposed)paint(r);}).catch(function(){if(!disposed){card.hidden=false;result.textContent=t('按键设置读取失败');}}).finally(function(){busy=false;if(!disposed)disable();});
 save.onclick=function(){
  if(disposed||busy||!loaded||!writable)return;
  var values={};
  for(var r of rows){
   if(!r.row.hidden&&r.select.value==='custom'&&(!r.command.value.trim()||/[\r\n\x00]/.test(r.command.value))){result.textContent=t('请输入一行命令，不能包含换行。');r.command.focus();return;}
   values[r.key]=r.select.value;values[r.commandKey]=r.command.value;
  }
  busy=true;disable();result.textContent=t('正在保存…');
  return set.apply(null,params.map(function(k){return values[k];})).then(function(r){if(disposed)return;paint(r);result.textContent=t('已保存');}).catch(function(e){if(!disposed)result.textContent=t('按键设置保存失败')+(e&&e.message?'：'+e.message:'');}).finally(function(){busy=false;if(!disposed)disable();});
 };
 return {dispose:function(){disposed=true;}};
}});
