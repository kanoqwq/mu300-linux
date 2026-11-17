'use strict';
'require baseclass';
'require ui';
'require unisoc-bluetooth.i18n as I';

var rows={
 title:['广播数据','Advertisement data','Yayın verileri'],
 hint:['显示最近收到的广播字段，未收到新数据时保留上次值。这里不是完整空中报文，字段可能未由设备或控制器提供。','Shows the latest received advertising fields; values remain until updated. This is not a complete air packet, and some fields may not be available.','Son alınan yayın alanlarını gösterir; yeni veri gelene kadar değerler korunur. Tam hava paketi değildir; bazı alanlar bulunmayabilir.'],
 name:['设备名称','Device name','Cihaz adı'],address:['设备地址','Device address','Cihaz adresi'],type:['地址类型','Address type','Adres türü'],
 public:['公开地址','Public address','Genel adres'],random:['随机地址','Random address','Rastgele adres'],
 rssi:['接收信号','RSSI','RSSI'],tx:['广播发射功率','Advertised TX power','Yayın TX gücü'],
 appearance:['外观标识','Appearance','Görünüm'],seen:['最近广播更新','Last advertisement update','Son yayın güncellemesi'],
 unknown_seen:['本次后台启动后尚未记录','Not observed since this backend started','Arka uç başladıktan sonra gözlemlenmedi'],
 manufacturer:['厂商数据','Manufacturer data','Üretici verileri'],service:['服务数据','Service data','Servis verileri'],
 uuids:['设备报告的服务 UUID','Reported service UUIDs','Bildirilen servis UUID değerleri'],
 flags:['广播标志','Advertising flags','Yayın bayrakları'],other:['其他 AD 字段','Other AD fields','Diğer AD alanları'],
 bytes:['字节','bytes','bayt'],empty:['未提供','Not provided','Sağlanmadı'],
 copy:['复制数据','Copy data','Veriyi kopyala'],copied:['已复制','Copied','Kopyalandı'],
 copy_manual:['请选择并复制下方内容','Select and copy the text below','Aşağıdaki metni seçip kopyalayın'],
 export:['导出广播 JSON','Export advertisement JSON','Yayın JSON dışa aktar'],
 copy_format:['复制格式可直接填入“BLE 广播实验”的对应数据框。','Copied entries can be pasted into the matching BLE advertising lab field.','Kopyalanan kayıtlar BLE yayın laboratuvarındaki ilgili alana yapıştırılabilir.']
};
function T(k){var r=rows[k],lang=I.locale();return r?r[lang.indexOf('zh')===0?0:lang.indexOf('tr')===0?2:1]:I.t(k);}
// Names from Bluetooth SIG Assigned Numbers, company identifiers. Unknown IDs
// remain numeric rather than being guessed from a name or MAC address.
var companies={6:'Microsoft',76:'Apple, Inc.',89:'Nordic Semiconductor ASA',117:'Samsung Electronics Co. Ltd.',224:'Google',911:'Xiaomi Inc.'};
function identifier(value,width){var n=Number(value);return Number.isInteger(n)&&n>=0?'0x'+n.toString(16).toUpperCase().padStart(width,'0'):String(value);}
function hex(value){return typeof value==='string'?value.replace(/\s/g,'').toUpperCase():Array.isArray(value)?value.map(function(n){return Number(n).toString(16).padStart(2,'0');}).join('').toUpperCase():'';}
function raw(value){var h=hex(value);return h?h.match(/.{1,2}/g).join(' '):'—';}
function button(label,fn){return E('button',{'class':'cbi-button',click:fn},label);}
async function copy(value,feedback){
 try{
  if(!navigator.clipboard || !window.isSecureContext)throw new Error('manual-copy');
  await navigator.clipboard.writeText(value);feedback.textContent=T('copied');
 }catch(error){
  var area=E('textarea',{readonly:true,rows:5},value);
  ui.showModal(T('copy_manual'),[E('div',{'class':'unisoc-bt bt-modal'},[
   E('div',{'class':'bt-field'},[area]),E('div',{'class':'bt-actions'},[button(T('close'),ui.hideModal)])
  ])]);area.focus();area.select();
 }
}
function block(title,label,value,copyValue){
 var feedback=E('span',{'class':'bt-muted','aria-live':'polite'});
 return E('section',{'class':'bt-ad-block'},[
  E('div',{'class':'bt-ad-heading'},[E('strong',{},title),E('span',{'class':'bt-muted'},(hex(value).length/2)+' '+T('bytes'))]),
  label?E('code',{'class':'bt-ad-label'},label):'',E('code',{'class':'bt-ad-hex'},raw(value)),
  E('div',{'class':'bt-ad-tools'},[button(T('copy'),function(){return copy(copyValue,feedback);}),feedback])
 ]);
}
function section(target,title,children){target.replaceChildren(E('h4',{},title),children.length?E('div',{'class':'bt-ad-blocks'},children):E('p',{'class':'bt-muted'},T('empty')));}
function metadata(d){
 var rows=[['name',d.Name || d.Alias || '—'],['address',d.Address],['type',d.AddressType?T(d.AddressType):'—']];
 if(d.RSSI!=null)rows.push(['rssi',d.RSSI+' dBm']);
 if(d.TxPower!=null)rows.push(['tx',d.TxPower+' dBm']);
 if(d.Appearance!=null)rows.push(['appearance',identifier(d.Appearance,4)]);
 rows.push(['seen',d.LastAdvertisement?new Date(d.LastAdvertisement*1000).toLocaleString():T('unknown_seen')]);
 return rows.map(function(pair){return E('div',{},[E('dt',{},T(pair[0])),E('dd',{},pair[1])]);});
}

return baseclass.extend({
 t:T,hex:hex,identifier:identifier,
 create:function(){var self=Object.create(this);self.entries=new Map();return self;},
 render:function(d,adapter){
  var key=(adapter || '')+'|'+d.Address,entry=this.entries.get(key);
  if(!entry){
   entry={meta:E('dl',{'class':'bt-ad-meta'}),manufacturer:E('div'),service:E('div'),uuids:E('div'),extra:E('div'),prints:{}};
   entry.node=E('details',{'class':'bt-broadcast'},[E('summary',{},T('title')),E('div',{'class':'bt-ad-body'},[
    E('p',{'class':'bt-muted'},T('hint')),entry.meta,entry.manufacturer,entry.service,entry.uuids,entry.extra,
    E('p',{'class':'bt-muted'},T('copy_format')),button(T('export'),function(){
     var url=URL.createObjectURL(new Blob([JSON.stringify(entry.data,null,2)],{type:'application/json'}));
     var a=E('a',{href:url,download:'ble-'+d.Address.replace(/:/g,'-')+'.json'});a.click();setTimeout(function(){URL.revokeObjectURL(url);},1000);
    })
   ])]);this.entries.set(key,entry);
  }
  entry.data=d;
  // Retain the same <details> node and unchanged payload nodes during SSE
  // updates, preserving expansion, scroll position and selected hex text.
  function changed(part,value,paint){var print=JSON.stringify(value);if(entry.prints[part]!==print){entry.prints[part]=print;paint();}}
  changed('meta',[d.Name,d.Alias,d.Address,d.AddressType,d.RSSI,d.TxPower,d.Appearance,d.LastAdvertisement],function(){entry.meta.replaceChildren.apply(entry.meta,metadata(d));});
  changed('manufacturer',d.ManufacturerData || {},function(){
   section(entry.manufacturer,T('manufacturer'),Object.keys(d.ManufacturerData || {}).sort(function(a,b){return Number(a)-Number(b);}).map(function(k){
    var id=identifier(k,4),label=id+' ('+Number(k)+')'+(companies[Number(k)]?' · '+companies[Number(k)]:'');
    return block(label,'',d.ManufacturerData[k],id+'='+hex(d.ManufacturerData[k]));
   }));
  });
  changed('service',d.ServiceData || {},function(){
   section(entry.service,T('service'),Object.keys(d.ServiceData || {}).sort().map(function(k){return block(k,'',d.ServiceData[k],k+'='+hex(d.ServiceData[k]));}));
  });
  changed('uuids',d.UUIDs || [],function(){section(entry.uuids,T('uuids'),(d.UUIDs || []).map(function(k){return E('code',{'class':'bt-ad-label'},k);}));});
  changed('extra',[d.AdvertisingFlags,d.AdvertisingData],function(){
   var fields=[];
   if(d.AdvertisingFlags!=null)fields.push(block(T('flags'),'AD 0x01',d.AdvertisingFlags,hex(d.AdvertisingFlags)));
   Object.keys(d.AdvertisingData || {}).sort(function(a,b){return Number(a)-Number(b);}).forEach(function(k){fields.push(block('AD '+identifier(k,2),'',d.AdvertisingData[k],hex(d.AdvertisingData[k])));});
   section(entry.extra,T('other'),fields);
  });
  return entry.node;
 },
 prune:function(devices,adapter){var keep=new Set(devices.map(function(d){return (adapter || '')+'|'+d.Address;}));this.entries.forEach(function(entry,key){if(!keep.has(key))this.entries.delete(key);},this);}
});
