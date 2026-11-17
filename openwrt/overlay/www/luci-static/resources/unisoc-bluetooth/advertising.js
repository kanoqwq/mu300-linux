'use strict';
'require baseclass';
'require ui';
'require unisoc-bluetooth.i18n as I';
'require unisoc-bluetooth.feedback as F';

var rows={
 title:['BLE 广播实验','BLE advertising lab','BLE yayın laboratuvarı'],
 hint:['创建仅广播的实验设备：可被扫描，但不提供 GATT 服务或实际设备功能。保存不启动；重启后不会自动续播。','Create scan-visible test advertisements without GATT services or device functions. Saving does not start broadcasting, and sessions never resume after a restart.','GATT servisi veya cihaz işlevi olmayan, taramada görünen test yayınları oluşturun. Kaydetmek yayını başlatmaz; yeniden başlatma sonrası devam etmez.'],
 address_hint:['广播实例由本机控制器发出，地址由 BlueZ 管理；多条定义不代表多个独立 MAC。','Advertisements use this controller; BlueZ manages addresses. Multiple definitions do not imply separate MAC addresses.','Yayınlar bu denetleyiciyi kullanır; adresleri BlueZ yönetir. Birden çok tanım, ayrı MAC adresleri anlamına gelmez.'],
 add:['新建实验广播','New test advertisement','Yeni test yayını'],
 edit:['编辑广播','Edit advertisement','Yayını düzenle'],
 save:['保存定义','Save definition','Tanımı kaydet'],
 label:['实验名称（仅网页显示）','Experiment label (web only)','Deney etiketi (yalnız web)'],
 name:['广播名称（可留空）','Advertised name (optional)','Yayın adı (isteğe bağlı)'],
 uuids:['服务 UUID（逗号或空格分隔）','Service UUIDs (comma or space separated)','Servis UUID değerleri (virgül veya boşluk)'],
 interval:['广播间隔（毫秒）','Advertising interval (ms)','Yayın aralığı (ms)'],
 timeout:['运行时长（秒，0 为持续）','Lifetime (seconds; 0 continues)','Süre (saniye; 0 sürekli)'],
 manufacturer:['厂商数据（每行 公司ID=十六进制数据）','Manufacturer data (company ID=hex per line)','Üretici verisi (satır başına şirket kimliği=hex)'],
 service:['服务数据（每行 UUID=十六进制数据）','Service data (UUID=hex per line)','Servis verisi (satır başına UUID=hex)'],
 payload_hint:['使用传统 BLE 数据长度：广播内容最多 31 字节，名称最多 29 个 UTF-8 字节。公司 ID 支持十进制或 0x 前缀；修改运行中的广播请先停止。','Legacy BLE limits: 31 advertising bytes and 29 UTF-8 bytes for the name. Company IDs accept decimal or 0x notation. Stop an advertisement before editing it.','Klasik BLE sınırları: 31 yayın baytı ve ad için 29 UTF-8 baytı. Şirket kimliği ondalık veya 0x olabilir. Düzenlemeden önce yayını durdurun.'],
 start:['开始广播','Start advertising','Yayını başlat'],stop:['停止','Stop','Durdur'],stop_all:['停止所有实验广播','Stop all lab advertisements','Tüm deney yayınlarını durdur'],
 active:['广播中','Advertising','Yayın yapılıyor'],saved:['已保存／未启动','Saved / stopped','Kayıtlı / durduruldu'],
 uncertain:['注册结果待确认，请先停止后重试','Registration uncertain; stop before retrying','Kayıt sonucu belirsiz; yeniden denemeden önce durdurun'],
 empty:['尚未创建实验广播。','No test advertisements yet.','Henüz test yayını yok.'],
 unavailable:['请先开启蓝牙，以检测控制器的广播能力。','Turn on Bluetooth to check advertising support.','Yayın desteğini görmek için Bluetooth açın.'],
 unsupported:['当前控制器没有提供 BLE 广播能力。','This controller does not expose BLE advertising.','Bu denetleyicide BLE yayın desteği yok.'],
 instances:['控制器正在使用／支持的广播实例','Controller advertising instances: active / supported','Denetleyici yayın sayısı: etkin / desteklenen'],
 duplicate:['复制','Duplicate','Kopyala'],
 delete_title:['删除这条广播定义？','Delete this advertisement definition?','Bu yayın tanımı silinsin mi?'],
 delete_hint:['只删除所选实验定义，不影响真实设备、配对或网关配置。','Only this test definition is removed. Devices, pairings and gateway settings are retained.','Yalnız seçilen deney tanımı silinir. Cihazlar, eşleştirmeler ve ağ geçidi ayarları korunur.'],
 continuous:['持续运行','Continuous','Sürekli'],
 data_error:['广播数据格式不正确，请按每行“标识=十六进制数据”填写。','Invalid data format. Use identifier=hex on each line.','Geçersiz veri biçimi. Her satırda kimlik=hex kullanın.']
};
function T(k){var r=rows[k],lang=I.locale();return r?r[lang.indexOf('zh')===0?0:lang.indexOf('tr')===0?2:1]:I.t(k);}
function input(value,type){return E('input',{type:type || 'text',value:value==null?'':value});}
function field(key,node,wide){return E('label',{'class':'bt-field'+(wide?' bt-lab-wide':'')},[E('span',{},T(key)),node]);}
function lines(value,kind){
 return value.split(/\r?\n/).map(function(line){return line.trim();}).filter(Boolean).map(function(line){
  var match=/^([^=]+)=(.*)$/.exec(line);if(!match)throw new Error('invalid_advertising_data');
  return kind==='company'?{company:Number(match[1].trim()),hex:match[2].trim()}:{uuid:match[1].trim(),hex:match[2].trim()};
 });
}
function modal(title,body,buttons){ui.showModal(title,[E('div',{'class':'unisoc-bt bt-modal'},[E('div',{'class':'bt-modal-body'},body),E('div',{'class':'bt-actions'},buttons)])]);}

return baseclass.extend({
 t:T,
 create:function(view){
  var self=Object.create(this);self.view=view;self.profiles=[];
  self.capability=E('p',{'class':'bt-muted'});self.list=E('div');
  self.stopAll=self.button(T('stop_all'),function(){return self.request('advertising_stop',{});});
  self.node=E('section',{'class':'bt-card bt-lab'},[E('h3',{},T('title')),E('p',{'class':'bt-muted'},T('hint')),self.capability,
   E('div',{'class':'bt-actions'},[self.button(T('add'),function(){self.edit();}),self.stopAll]),self.list,E('p',{'class':'bt-muted'},T('address_hint'))]);
  return self;
 },
 button:function(label,fn){return this.view.button(label,fn);},
 request:function(method,args){
  var self=this;
  return self.view.call(method,args).then(function(result){self.update(result.advertising);return self.view.update();}).catch(function(error){self.view.showError(error);});
 },
 update:function(state){
  var self=this;state=state || {};self.profiles=state.profiles || [];
  self.capability.textContent=!state.powered?T('unavailable'):!state.supported?T('unsupported'):T('instances')+': '+(state.active_instances || 0)+' / '+(state.supported_instances || 0);
  self.stopAll.disabled=!self.profiles.some(function(p){return p.active;});
  self.list.replaceChildren();
  if(!self.profiles.length)self.list.appendChild(E('p',{'class':'bt-muted'},T('empty')));
  self.profiles.forEach(function(p){
   var start=self.button(T(p.active?'stop':'start'),function(){return self.request(p.active?'advertising_stop':'advertising_start',{id:p.id});});
   start.disabled=!p.active && (!state.powered || !state.supported);
   var edit=self.button(T('edit'),function(){self.edit(p);}),remove=self.button(T('remove'),function(){self.remove(p);});
   edit.disabled=remove.disabled=p.active;
   self.list.appendChild(E('article',{'class':'bt-device'},[
    E('div',{},[E('strong',{},p.label),E('div',{'class':'bt-device-state'},[E('span',{'class':'bt-badge'+(p.active?' bt-active':'')},T(p.state==='uncertain'?'uncertain':p.active?'active':'saved'))]),
     E('div',{'class':'bt-muted'},(p.name?p.name+' · ':'')+p.interval_ms+' ms · '+(p.timeout?p.timeout+' s':T('continuous')))]),
    E('div',{'class':'bt-actions'},[start,edit,self.button(T('duplicate'),function(){self.edit(Object.assign({},p,{id:''}));}),remove])
   ]));
  });
 },
 edit:function(profile){
  var self=this,p=profile || {id:'',label:'BLE Lab',name:'BLE Lab',uuids:[],manufacturer_data:[],service_data:[],interval_ms:1000,timeout:60};
  var f={label:input(p.label),name:input(p.name),uuids:input(p.uuids.join(', ')),interval:input(p.interval_ms,'number'),timeout:input(p.timeout,'number'),
   manufacturer:E('textarea',{rows:3,placeholder:'0xFFFF=010203'},p.manufacturer_data.map(function(v){return '0x'+v.company.toString(16).padStart(4,'0')+'='+v.hex;}).join('\n')),
   service:E('textarea',{rows:3,placeholder:'180F=64'},p.service_data.map(function(v){return v.uuid+'='+v.hex;}).join('\n'))};
  f.interval.min=20;f.interval.max=10240;f.timeout.min=0;f.timeout.max=65535;
  var message=E('div',{'aria-live':'polite'}),save=self.button(T('save'),function(){
   var record;
   try{record={id:p.id,label:f.label.value,name:f.name.value,uuids:f.uuids.value.split(/[,\s]+/).filter(Boolean),interval_ms:Number(f.interval.value),timeout:Number(f.timeout.value),manufacturer_data:lines(f.manufacturer.value,'company'),service_data:lines(f.service.value,'uuid')};}
   catch(error){message.replaceChildren(F.message(T('data_error'),error.message,'error'));return;}
   save.disabled=true;
   return self.view.call('advertising_save',{profile:record}).then(function(result){self.update(result.advertising);ui.hideModal();return self.view.update();}).catch(function(error){message.replaceChildren(F.message(F.error(error.message),error.message,'error'));}).finally(function(){save.disabled=false;});
  });
  modal(T(profile?'edit':'add'),[E('p',{'class':'bt-muted'},T('payload_hint')),message,E('div',{'class':'bt-grid'},Object.keys(f).map(function(k){return field(k,f[k],['uuids','manufacturer','service'].indexOf(k)>=0);}))],[self.button(T('close'),ui.hideModal),save]);
 },
 remove:function(p){
  var self=this,message=E('div');
  modal(T('delete_title'),[E('p',{},p.label),E('p',{'class':'bt-muted'},T('delete_hint')),message],[self.button(T('close'),ui.hideModal),self.button(T('remove'),function(){
   return self.view.call('advertising_delete',{id:p.id}).then(function(result){self.update(result.advertising);ui.hideModal();return self.view.update();}).catch(function(error){message.replaceChildren(F.message(F.error(error.message),error.message,'error'));});
  })]);
 }
});
