'use strict';
'require baseclass';
'require ui';
'require unisoc-bluetooth.i18n as I';
'require unisoc-bluetooth.feedback as F';

var rows={
 smart:['智能家居设备','Smart home devices','Akıllı ev cihazları'],
 hint:['自动识别 BTHome 及 Theengs 支持的广播设备；加密 BTHome 需填写密钥。启用网关后自动采集，按钮事件不会被普通数值上报节流。','Decode BTHome and Theengs-supported advertisements. Encrypted BTHome requires a key. Enable the gateway to collect data. Button events bypass measurement throttling.','BTHome ve Theengs destekli yayınları çözer. Şifreli BTHome anahtar gerektirir. Veri toplamak için ağ geçidini açın. Düğme olayları ölçüm sınırlamasından etkilenmez.'],
 manage:['管理设备','Manage device','Cihazı yönet'],room:['房间／分组','Room / group','Oda / grup'],
 manufacturer:['厂商','Manufacturer','Üretici'],model:['型号','Model','Model'],
 bindkey:['BTHome 密钥（留空保留）','BTHome key (blank keeps current)','BTHome anahtarı (boşsa mevcut korunur)'],
 clear_bindkey:['清除已保存的 BTHome 密钥','Clear saved BTHome key','Kayıtlı BTHome anahtarını sil'],
 auto_connect:['断开后自动重连','Reconnect after disconnection','Kesilince yeniden bağlan'],
 offline_after:['多久未收到数据视为离线（秒，0 为不超时）','Offline after silence (seconds; 0 disables expiry)','Veri gelmezse çevrimdışı süresi (saniye; 0 devre dışı)'],
 presence_hint:['在线表示最近收到广播，与“已连接”不同。按键类设备不按广播间隔判离线；手机随机地址不保证稳定。','Online means recently seen, separate from connected. Event-based devices do not expire between broadcasts. Phone random addresses may change.','Çevrimiçi, bağlı değil yakın zamanda görüldü demektir. Olay cihazları yayın aralarında zaman aşımına uğramaz. Telefonun rastgele adresi değişebilir.'],
 online:['在线','Online','Çevrimiçi'],offline:['未见／离线','Unseen / offline','Görülmedi / çevrimdışı'],
 seen:['最近收到','Last seen','Son görülme'],never:['尚未收到数据','No data yet','Henüz veri yok'],
 measured:['最近测量','Last measurement','Son ölçüm'],
 values:['传感器数据','Sensor values','Sensör değerleri'],empty:['尚未识别出传感器数据','No sensor values decoded yet','Henüz sensör verisi çözülemedi'],
 edit:['编辑','Edit','Düzenle'],keep:['暂存，保存后应用','Keep; apply when saved','Tut; kaydedilince uygula'],
 unmanage:['取消管理','Stop managing','Yönetimden çıkar'],
 active_state:['开启／触发','On / active','Açık / etkin'],inactive_state:['关闭／未触发','Off / inactive','Kapalı / etkin değil'],
 press:['单击','Press','Basış'],double_press:['双击','Double press','Çift basış'],triple_press:['三击','Triple press','Üçlü basış'],
 long_press:['长按','Long press','Uzun basış'],long_double_press:['长按两次','Long double press','Uzun çift basış'],long_triple_press:['长按三次','Long triple press','Uzun üçlü basış'],hold_press:['持续按下','Held down','Basılı'],hold_release:['松开','Released','Bırakıldı'],
 pending:['更改已暂存，请点击“保存并应用”。','Changes are pending. Select Save & apply.','Değişiklikler bekliyor. Kaydet ve uygula seçin.'],
 policy:['扫描与上报','Scanning & reporting','Tarama ve raporlama'],
 policy_hint:['暂停为 0 时持续扫描；非零时按扫描／暂停窗口运行。上报只控制网关发送频率，不重置蓝牙或 Wi-Fi。','Pause 0 scans continuously; otherwise scanning alternates with pauses. Reporting limits outbound events without resetting Bluetooth or Wi-Fi.','Duraklama 0 ise sürekli, aksi halde aralıklı taranır. Raporlama sınırı Bluetooth veya Wi-Fi sıfırlamaz.'],
 scan_seconds:['每次扫描（秒）','Scan window (seconds)','Tarama süresi (saniye)'],
 pause_seconds:['扫描后暂停（秒）','Pause after scanning (seconds)','Tarama sonrası duraklama (saniye)'],
 min_interval:['最短上报间隔（秒）','Minimum report interval (seconds)','En kısa rapor aralığı (saniye)'],
 max_interval:['最长静默间隔（秒）','Maximum report silence (seconds)','En uzun rapor sessizliği (saniye)'],
 change_only:['数据变化才上报，达到最长间隔时允许刷新','Report changes, allowing a refresh after the maximum interval','Değişince raporla; en uzun aralıkta yenilenebilir'],
 rssi_delta:['信号变化阈值（dBm）','Signal change threshold (dBm)','Sinyal değişimi eşiği (dBm)'],
 controls:['智能家居控制','Smart home controls','Akıllı ev kontrolleri'],
 control_hint:['只执行这里定义的动作。MQTT 主题为 <前缀>/commands/<标识>/set，结果在 /result；保留消息不会触发动作。开关状态仅在回读确认后更新。','Only configured actions can run. MQTT uses <prefix>/commands/<id>/set, with results on /result. Retained commands are ignored. Switch state updates require readback.','Yalnız tanımlı işlemler çalışır. MQTT: <önek>/commands/<kimlik>/set, sonuç /result. Kalıcı komutlar yok sayılır. Anahtar durumu geri okuma gerektirir.'],
 id:['标识（字母／数字／下划线）','ID (letters / digits / underscore)','Kimlik (harf / rakam / alt çizgi)'],
 kind:['执行动作','Action','İşlem'],domain:['HA 控件类型','HA entity type','HA varlık türü'],
 button:['按钮','Button','Düğme'],switch:['开关','Switch','Anahtar'],number:['数值','Number','Sayı'],
 command_write:['写入 BLE 特征','Write BLE characteristic','BLE özelliği yaz'],
 command_read:['读取 BLE 特征','Read BLE characteristic','BLE özelliği oku'],
 command_serial_write:['发送串口数据','Send serial data','Seri veri gönder'],
 on:['开启数据（十六进制）','ON data (hex)','AÇIK verisi (hex)'],off:['关闭数据（十六进制）','OFF data (hex)','KAPALI verisi (hex)'],
 min:['最小值','Minimum','En az'],max:['最大值','Maximum','En çok'],step:['步长','Step','Adım'],
 templates:['设备模板','Device templates','Cihaz şablonları'],
 import_templates:['导入模板 JSON','Import template JSON','Şablon JSON içe aktar'],
 export_templates:['导出自定义模板','Export custom templates','Özel şablonları dışa aktar'],
 template_hint:['模板只包含协议、特征和解码配置，不执行脚本。厂商私有协议需要匹配的型号模板。','Templates contain protocol, characteristic and decoding settings, never scripts. Proprietary protocols need model-specific support.','Şablonlar yalnız protokol, özellik ve çözümleme ayarları içerir, betik çalıştırmaz. Özel protokoller model desteği gerektirir.'],
 apply:['应用模板','Apply template','Şablon uygula'],
 template_bthome:['BTHome v2 传感器／按钮','BTHome v2 sensors / buttons','BTHome v2 sensörler / düğmeler'],
 template_battery:['标准蓝牙电量服务','Standard Bluetooth battery service','Standart Bluetooth pil servisi'],
 template_raw:['原始广播转发','Raw advertisement bridge','Ham yayın köprüsü'],
 proxy:['Home Assistant 蓝牙代理','Home Assistant Bluetooth proxy','Home Assistant Bluetooth vekili'],
 proxy_hint:['在 Home Assistant 添加 ESPHome，填写本机监听地址、端口和加密密钥。主动连接默认关闭，开启后只允许下列设备；它与网页共用一个蓝牙后台。','Add ESPHome in Home Assistant using this bind address, port and encryption key. Active connections default off and require the allowlist. The proxy shares the web Bluetooth backend.','Home Assistant içinde ESPHome ekleyin; adresi, portu ve şifreleme anahtarını girin. Aktif bağlantılar varsayılan kapalıdır ve izin listesi gerektirir. Web ile aynı Bluetooth arka ucunu kullanır.'],
 proxy_active:['允许 HA 主动连接与 GATT 操作','Allow HA active connections and GATT operations','HA aktif bağlantılarına ve GATT işlemlerine izin ver'],
 proxy_key:['Noise 加密密钥（留空保留）','Noise encryption key (blank keeps current)','Noise anahtarı (boşsa mevcut korunur)'],
 clear_proxy_key:['清除已保存的代理密钥（须先关闭代理）','Clear saved proxy key (disable proxy first)','Kayıtlı vekil anahtarını sil (önce vekili kapatın)'],
 generate:['生成新密钥','Generate new key','Yeni anahtar üret'],
 key_hint:['请先复制新密钥到 Home Assistant，再保存设置。更换密钥后旧客户端需要重新配置。','Copy the new key to Home Assistant before saving. Existing clients must be reconfigured after a key change.','Kaydetmeden önce yeni anahtarı Home Assistant içine kopyalayın. Eski istemciler yeniden ayarlanmalıdır.'],
 slots:['最大主动连接数','Maximum active connections','En çok aktif bağlantı'],
 clients:['代理客户端','Proxy clients','Vekil istemcileri'],
 proxy_devices:['允许 HA 主动连接的设备地址','Devices HA may actively connect to','HA aktif bağlantısına izin verilen cihazlar'],
 backup:['备份与恢复','Backup & restore','Yedekleme ve geri yükleme'],
 export:['导出已保存配置（含密钥）','Export saved settings (including secrets)','Kayıtlı ayarları dışa aktar (anahtarlar dahil)'],
 import:['导入配置备份','Import configuration backup','Ayar yedeğini içe aktar'],
 backup_hint:['备份包含 MQTT 密码和设备密钥，请妥善保存。导入会替换网关配置，并按导入设置启动；不导入控制器地址或 BlueZ 配对资料。','Backups contain MQTT passwords and device keys. Import replaces gateway settings and starts services according to them. Controller addresses and BlueZ pairing records are not imported.','Yedek MQTT parolaları ve cihaz anahtarları içerir. İçe aktarma ayarları değiştirir ve bu ayarlara göre başlatır. Denetleyici adresi ve BlueZ eşleştirmeleri aktarılmaz.'],
 confirm_restore:['确认替换蓝牙网关配置？','Replace Bluetooth gateway settings?','Bluetooth ağ geçidi ayarları değiştirilsin mi?'],
 decoder:['广播解析','Advertisement decoder','Yayın çözücü'],
 temperature:['温度','Temperature','Sıcaklık'],humidity:['湿度','Humidity','Nem'],battery:['电量','Battery','Pil'],
 pressure:['气压','Pressure','Basınç'],co2:['二氧化碳','Carbon dioxide','Karbondioksit'],moisture:['湿润度','Moisture','Nemlilik'],voltage:['电压','Voltage','Gerilim'],current:['电流','Current','Akım'],power:['功率／电源','Power','Güç'],energy:['电量累计','Energy','Enerji'],
 presence:['存在状态','Presence','Varlık'],opening:['开合状态','Opening','Açıklık'],occupancy:['占用状态','Occupancy','Doluluk'],window:['窗磁','Window','Pencere'],lock:['锁状态','Lock','Kilit'],smoke:['烟雾','Smoke','Duman'],
 door:['门磁','Door','Kapı'],motion:['移动','Motion','Hareket'],illuminance:['照度','Illuminance','Aydınlık'],
 bind:['本机监听 IP','Local bind IP','Yerel dinleme IP'],
 imported:['已导入，正在重新加载配置…','Imported. Reloading settings…','İçe aktarıldı. Ayarlar yükleniyor…']
};
function T(k){var r=rows[k],lang=I.locale();return r ? r[lang.indexOf('zh')===0?0:lang.indexOf('tr')===0?2:1] : I.t(k);}
function input(value,type){return E('input',{type:type||'text',value:value==null?'':value});}
function field(k,node){return E('label',{'class':'bt-field'},[E('span',{},T(k)),node]);}
function select(values,current){return E('select',{},values.map(function(x){return E('option',{value:x[0],selected:x[0]===current||null},x[1]);}));}
function checkbox(value,label){return E('label',{'class':'bt-check'},[E('input',{type:'checkbox',checked:!!value||null}),E('span',{},T(label))]);}
function checked(node){return node.querySelector('input').checked;}
// LuCI attaches modals outside the page root; carry the Bluetooth style scope.
function modal(title,body,actions){return ui.showModal(title,[E('div',{'class':'unisoc-bt bt-modal'},[
 E('div',{'class':'bt-modal-body'},body),E('div',{'class':'bt-actions'},actions)
])]);}
function clean(record){var out={};Object.keys(record).forEach(function(k){if(!/_set$/.test(k))out[k]=record[k];});return out;}
function download(name,value){var a=E('a',{href:URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),download:name});a.click();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);}

return baseclass.extend({
 t:T,
 create:function(view,config){
  var self=Object.create(this);self.view=view;var g=config.gateway;
  self.known=(g.known_devices||[]).map(clean);self.commands=(g.commands||[]).map(clean);self.catalog=[];self.savedCommands=(g.commands||[]).map(function(c){return c.id;});
  var policy=g.policy||{scan_seconds:30,pause_seconds:0,min_interval:2,max_interval:60,change_only:true,rssi_delta:5};
  self.policy={};Object.keys(policy).forEach(function(k){self.policy[k]=k==='change_only'?checkbox(policy[k],k):input(policy[k],'number');});
  var p=g.proxy||{enabled:false,bind:'127.0.0.1',port:6053,name:'unisoc-bluetooth',active:false,slots:3,devices:[]};
  self.proxy={};['enabled','active'].forEach(function(k){self.proxy[k]=checkbox(p[k],k==='active'?'proxy_active':'enable');});
  ['bind','port','name','slots','psk'].forEach(function(k){self.proxy[k]=input(p[k],k==='psk'?'password':k==='port'||k==='slots'?'number':'text');});
  self.proxy.devices=input(p.devices.join(', '));self.proxy.clear=checkbox(false,'clear_proxy_key');self.proxyInfo=E('p',{'class':'bt-muted'});self.knownList=E('div');self.commandList=E('div');
  var picker=E('input',{type:'file',accept:'.json,application/json',hidden:true,change:function(){self.importBackup(picker.files[0]);picker.value='';}});
  var templateFile=E('input',{type:'file',accept:'.json,application/json',hidden:true,change:function(){
   var file=templateFile.files[0];templateFile.value='';if(!file)return;
   if(file.size>196608){view.showError(new Error('invalid_bluetooth_template'));return;}
   file.text().then(function(text){return view.call('import_templates',{templates:JSON.parse(text)});}).then(function(r){setTemplates(r.templates);}).catch(function(e){view.showError(e);});
  }});
  var templateMac=input(''),templateChoice=select([],''),templateButton=self.button(T('apply'),function(){
   var template=self.templates.find(function(t){return t.id===templateChoice.value;});
   if(!template)return;
   var mac=templateMac.value.trim().toUpperCase();
   if(!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(mac)){view.showError(new Error('invalid_bluetooth_address'));return;}
   var d=self.known.find(function(d){return d.device===mac;});if(!d){d={device:mac};self.known.push(d);}
   d.template=template.id;d.decoder=template.decoder;
   (template.subscriptions||[]).forEach(function(sub){
    if(!view.subRows.some(function(row){var f=row.fields;return f.address.value===mac&&f.service.value===sub.service&&f.characteristic.value===sub.characteristic;}))view.addSub(Object.assign({},sub,{device:mac}));
   });self.changed();self.updateKnown();
   (template.commands||[]).forEach(function(c){var id=c.id.slice(0,30)+'_'+mac.replace(/:/g,'').toLowerCase();if(!self.commands.some(function(x){return x.id===id;}))self.commands.push(Object.assign({},c,{device:mac,id:id}));});self.updateCommands();
  });
  function setTemplates(templates){self.templates=templates;templateChoice.replaceChildren();templates.forEach(function(t){templateChoice.appendChild(E('option',{value:t.id},T({'bthome-v2':'template_bthome','gatt-battery':'template_battery','raw-bridge':'template_raw'}[t.id]||t.name)));});}
  view.call('templates').then(function(r){setTemplates(r.templates);}).catch(function(e){view.showError(e);});
  self.templates=[];
  self.node=E('div',{},[
   self.card('smart',[E('p',{'class':'bt-muted'},T('hint')),self.knownList,self.button(T('add'),function(){self.edit('');}),E('p',{'class':'bt-muted'},T('presence_hint'))]),
   self.card('templates',[E('p',{'class':'bt-muted'},T('template_hint')),E('div',{'class':'bt-grid'},[field('address',templateMac),field('templates',templateChoice)]),E('div',{'class':'bt-actions'},[
    templateButton,self.button(T('import_templates'),function(){templateFile.click();}),self.button(T('export_templates'),function(){download('bluetooth-templates.json',{format:1,kind:'unisoc-bluetooth-templates',templates:self.templates.filter(function(t){return ['bthome-v2','gatt-battery','raw-bridge'].indexOf(t.id)<0;})});}),templateFile])]),
   self.card('controls',[E('p',{'class':'bt-muted'},T('control_hint')),self.commandList,self.button(T('add'),function(){self.editCommand(null);})]),
   self.card('policy',[E('p',{'class':'bt-muted'},T('policy_hint')),E('div',{'class':'bt-grid'},Object.keys(self.policy).filter(function(k){return k!=='change_only';}).map(function(k){return field(k,self.policy[k]);})),self.policy.change_only]),
   self.card('proxy',[E('p',{'class':'bt-muted'},T('proxy_hint')),self.proxy.enabled,self.proxy.active,
    E('div',{'class':'bt-grid'},['bind','port','name','slots'].map(function(k){return field(k,self.proxy[k]);})),
    field('proxy_devices',self.proxy.devices),field('proxy_key',self.proxy.psk),
    E('div',{'class':'bt-actions'},[self.button(T('generate'),function(){return view.call('proxy_key').then(function(r){self.proxy.psk.value=r.key;self.proxy.psk.type='text';self.proxy.clear.querySelector('input').checked=false;self.changed();}).catch(function(e){view.showError(e);});})]),self.proxy.clear,
    E('p',{'class':'bt-muted'},T('key_hint')),self.proxyInfo]),
   self.card('backup',[E('p',{'class':'bt-muted'},T('backup_hint')),E('div',{'class':'bt-actions'},[
    self.button(T('export'),function(){return view.call('backup').then(function(r){download('bluetooth-settings.json',r.backup);}).catch(function(e){view.showError(e);});}),
    self.button(T('import'),function(){picker.click();}),picker])])
  ]);
  self.updateKnown();self.updateCommands();return self;
 },
 button:function(label,fn){return this.view.button(label,fn);},
 card:function(title,children){return E('section',{'class':'bt-card'},[E('h3',{},T(title))].concat(children));},
 changed:function(){this.view.feedback.replaceChildren(E('p',{'class':'bt-message',role:'status'},T('pending')));},
 update:function(status){this.catalog=status.catalog||[];this.updateKnown();var p=status.proxy||{};this.proxyInfo.textContent=T('clients')+': '+(p.clients||0)+' · '+T('slots')+': '+(p.active_connections||0)+' / '+(p.slots||0);},
 updateKnown:function(){
  var self=this,devices={};this.catalog.forEach(function(d){if((d.measurements||[]).length||d.error||self.known.some(function(k){return k.device===d.address;}))devices[d.address]=d;});this.known.forEach(function(d){devices[d.device]=Object.assign({},devices[d.device]||{address:d.device,measurements:[]},d);});
  this.knownList.replaceChildren();
  Object.keys(devices).forEach(function(mac){var d=devices[mac],values=(d.measurements||[]).filter(function(m){return m.name!=='packet_id';}).map(function(m){var label=T(m.name),value=m.domain==='event'?T(m.value):m.domain==='binary_sensor'?T(m.value?'active_state':'inactive_state'):m.value;return label+': '+value+(m.unit?' '+m.unit:'');}).join(' · ');
   self.knownList.appendChild(E('article',{'class':'bt-smart-device'},[
    E('div',{},[E('strong',{},d.name||mac),E('span',{'class':'bt-badge'},T(d.online?'online':'offline')),E('div',{'class':'bt-muted'},mac+(d.room?' · '+d.room:''))]),
    E('div',{'class':'bt-reading'},[E('span',{},values||T('empty')),d.error?F.message(F.error(d.error),d.error,'error'):'']),
    E('small',{'class':'bt-muted'},T('seen')+': '+(d.last_seen?new Date(d.last_seen*1000).toLocaleString():T('never'))+(d.measurement_time?' · '+T('measured')+': '+new Date(d.measurement_time*1000).toLocaleString():'')),
    E('div',{'class':'bt-actions'},[self.button(T('edit'),function(){self.edit(mac);}),self.known.some(function(k){return k.device===mac;})?self.button(T('unmanage'),function(){self.known=self.known.filter(function(x){return x.device!==mac;});self.changed();self.updateKnown();}):''])
   ]));
  });
 },
 edit:function(mac){
  var self=this,d=this.known.find(function(d){return d.device===mac;})||{device:mac};
  var f={device:input(mac),name:input(d.name),room:input(d.room),manufacturer:input(d.manufacturer),model:input(d.model),bindkey:input('','password'),offline_after:input(d.offline_after==null?300:d.offline_after,'number'),auto_connect:checkbox(d.auto_connect,'auto_connect'),clear_bindkey:checkbox(false,'clear_bindkey'),decoder:select([['auto',T('automatic')],['bthome','BTHome v2'],['theengs','Theengs Decoder'],['raw',T('template_raw')]],d.decoder||'auto')};
  if(mac)f.device.disabled=true;
  modal(T('manage'),[E('div',{'class':'bt-grid'},Object.keys(f).filter(function(k){return k!=='auto_connect'&&k!=='clear_bindkey';}).map(function(k){return field(k==='device'?'address':k,f[k]);})),f.auto_connect,f.clear_bindkey],
   [self.button(T('close'),ui.hideModal),self.button(T('keep'),function(){
    var n=Object.assign({},d);Object.keys(f).forEach(function(k){n[k]=k==='auto_connect'||k==='clear_bindkey'?checked(f[k]):k==='offline_after'?Number(f[k].value):f[k].value;});n.device=n.device.trim().toUpperCase();
    if(!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(n.device)){self.view.showError(new Error('invalid_bluetooth_address'));return;}
    self.known=self.known.filter(function(x){return x.device!==n.device;});self.known.push(n);self.changed();self.updateKnown();ui.hideModal();
   })]);
 },
 updateCommands:function(){var self=this;this.commandList.replaceChildren();this.commands.forEach(function(c){
  var actions=[];function run(label,value){var button=self.button(label,function(){return self.view.action('command',{id:c.id,value:value instanceof Function?value():value});});button.disabled=self.savedCommands.indexOf(c.id)<0;return button;}
  if(c.domain==='switch'){actions.push(run('ON','ON'));actions.push(run('OFF','OFF'));}
  else if(c.domain==='number'){var number=input(c.min,'number');number.min=c.min;number.max=c.max;number.step=c.step;actions.push(number);actions.push(run(T('send'),function(){return number.value;}));}
  else actions.push(run(T('send'),'PRESS'));
  actions.push(self.button(T('edit'),function(){self.editCommand(c);}));actions.push(self.button(T('remove'),function(){self.commands=self.commands.filter(function(x){return x!==c;});self.changed();self.updateCommands();}));
  self.commandList.appendChild(E('div',{'class':'bt-device'},[E('div',{},[E('strong',{},c.name),E('div',{'class':'bt-muted'},c.id+' · '+c.device+' · '+T(c.domain))]),E('div',{'class':'bt-actions'},actions)]));
 });},
 editCommand:function(previous){
  var self=this,c=previous||{id:'',name:'',device:'',kind:'write',domain:'button',codec:'u8',min:0,max:100,step:1};var f={};
  ['id','name','device','service','characteristic','hex','on','off','min','max','step'].forEach(function(k){f[k]=input(c[k],['min','max','step'].indexOf(k)>=0?'number':'text');});
  f.kind=select(['write','read','serial_write','connect','disconnect'].map(function(k){return[k,T('command_'+k)==='command_'+k?T(k):T('command_'+k)];}),c.kind);
  f.domain=select(['button','switch','number'].map(function(k){return[k,T(k)];}),c.domain);f.codec=select(['u8','u16le','i16le','u32le'].map(function(k){return[k,k];}),c.codec);
  var grid=E('div',{'class':'bt-grid bt-command-form'},Object.keys(f).map(function(k){return E('div',{'data-field':k},field(k==='device'?'address':k,f[k]));}));
  function visibility(){Object.keys(f).forEach(function(k){var show=true;if(['service','characteristic'].indexOf(k)>=0)show=['read','write'].indexOf(f.kind.value)>=0;if(['on','off'].indexOf(k)>=0)show=f.domain.value==='switch';if(['min','max','step','codec'].indexOf(k)>=0)show=f.domain.value==='number';if(k==='hex')show=f.domain.value==='button'&&['write','serial_write'].indexOf(f.kind.value)>=0;grid.querySelector('[data-field="'+k+'"]').hidden=!show;});}
  f.kind.addEventListener('change',visibility);f.domain.addEventListener('change',visibility);visibility();
  modal(T('controls'),[grid],[self.button(T('close'),ui.hideModal),self.button(T('keep'),function(){var d={};Object.keys(f).forEach(function(k){d[k]=['min','max','step'].indexOf(k)>=0?Number(f[k].value):f[k].value;});if(previous)self.commands=self.commands.filter(function(x){return x!==previous;});self.commands.push(d);self.savedCommands=self.savedCommands.filter(function(id){return id!==d.id;});self.changed();self.updateCommands();ui.hideModal();})]);
 },
 configuration:function(){
  var p={};Object.keys(this.policy).forEach(function(k){p[k]=k==='change_only'?checked(this.policy[k]):Number(this.policy[k].value);},this);
  var proxy={enabled:checked(this.proxy.enabled),active:checked(this.proxy.active)};
  ['bind','name','psk'].forEach(function(k){proxy[k]=this.proxy[k].value;},this);['port','slots'].forEach(function(k){proxy[k]=Number(this.proxy[k].value);},this);
  proxy.devices=this.proxy.devices.value.split(/[,\s]+/).filter(Boolean);
  proxy.clear_secret=checked(this.proxy.clear);
  return {known_devices:this.known.map(clean),commands:this.commands,policy:p,proxy:proxy};
 },
 saved:function(){this.proxy.psk.value='';this.proxy.psk.type='password';this.proxy.clear.querySelector('input').checked=false;this.known.forEach(function(d){d.bindkey='';delete d.clear_bindkey;});this.savedCommands=this.commands.map(function(c){return c.id;});this.updateCommands();},
 importBackup:function(file){
  var self=this;if(!file)return;if(file.size>196608){this.view.showError(new Error('invalid_bluetooth_backup'));return;}
  file.text().then(function(text){var backup=JSON.parse(text);if(backup.kind!=='unisoc-bluetooth-settings'||backup.format!==1)throw new Error('invalid_bluetooth_backup');
   modal(T('confirm_restore'),[E('p',{},T('backup_hint'))],[self.button(T('close'),ui.hideModal),self.button(T('accept'),function(){return self.view.call('restore',{backup:backup}).then(function(){ui.hideModal();window.location.reload();}).catch(function(e){self.view.showError(e);});})]);
  }).catch(function(e){self.view.showError(e);});
 }
});
