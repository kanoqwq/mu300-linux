'use strict';
'require baseclass';
'require rpc';
'require ui';
'require mu300.common as M';

var get=rpc.declare({object:'unisoc-product-form',method:'get',nobatch:true,expect:{'':{}}});
var set=rpc.declare({object:'unisoc-product-form',method:'set',params:['model','mode'],nobatch:true,expect:{'':{}}});
var words={
 '产品形态':['Product form','Ürün yapısı'],
 '自动识别（跟随原机 board_id）':['Automatic (stock board_id)','Otomatik (cihazın board_id değeri)'],
 '原机 board_id':['Stock board_id','Cihazın board_id değeri'],
 '当前生效':['Active now','Şu anda etkin'],
 '待重启生效':['Restart required','Yeniden başlatma gerekli'],
 '保存产品形态':['Save product form','Ürün yapısını kaydet'],
 '正在保存…':['Saving…','Kaydediliyor…'],
 '恢复自动识别':['Restore automatic detection','Otomatik algılamayı geri yükle'],
 '未知':['Unknown','Bilinmiyor'],
 '内置':['Internal','Dahili'],
 '插拔':['Removable','Çıkarılabilir'],
 '原厂无屏形态':['Stock screenless variant','Ekransız fabrika çeşidi'],
 '按当前型号选择 SIM 卡源组合。':['Select the SIM source layout for this model.','Bu modelin SIM kaynak yapısını seçin.'],
 '保存后重启生效，不会立即切卡。下次启动会清理新形态中不存在的旧卡源选择，保留短信、流量、APN 和射频设置。':['Takes effect after restart without switching cards now. Next startup clears saved source choices absent from the new form, preserving SMS, traffic, APN, and radio settings.','Şimdi kart değiştirilmez; yeniden başlatınca uygulanır. Sonraki açılışta yeni yapıda bulunmayan eski kart seçimleri temizlenir; SMS, trafik, APN ve radyo ayarları korunur.'],
 '确认产品形态？':['Confirm product form?','Ürün yapısını onaylıyor musunuz?'],
 '选错形态可能导致卡片不可用或卡槽与 IMEI 归属变化。请按实际硬件选择；保存后不会自动重启。':['An incorrect form may make SIMs unavailable or change their slot-to-IMEI mapping. Choose according to the hardware; saving will not restart the device.','Yanlış yapı SIM’leri kullanılamaz yapabilir veya yuva ile IMEI eşlemesini değiştirebilir. Donanıma göre seçin; kaydetmek cihazı yeniden başlatmaz.'],
 '设置已保存，请重启设备生效':['Saved; restart the device to apply','Kaydedildi; uygulamak için cihazı yeniden başlatın'],
 '设置已保存':['Settings saved','Ayarlar kaydedildi'],
 '无法读取产品形态，请重试':['Cannot read the product form; retry','Ürün yapısı okunamadı; tekrar deneyin'],
 '保存失败，请重试':['Could not save; retry','Kaydedilemedi; tekrar deneyin'],
 '请先保存或撤销其他待提交的适配设置':['Save or discard other pending adaptation changes first','Önce bekleyen diğer uyarlama değişikliklerini kaydedin veya iptal edin'],
 '设备型号已改变，请刷新页面':['Device model changed; refresh the page','Cihaz modeli değişti; sayfayı yenileyin'],
 '此形态不属于当前型号':['This form does not belong to this model','Bu yapı mevcut modele ait değil'],
 '另一项产品形态设置正在保存':['Another product form change is being saved','Başka bir ürün yapısı değişikliği kaydediliyor'],
 '刷新':['Refresh','Yenile']
};
function t(s){var lang=M.uiLanguage();return lang==='zh'?s:(words[s]||[s,s])[lang==='tr'?1:0];}
function node(tag,text,cls){var n=document.createElement(tag);if(text!=null)n.textContent=t(text);if(cls)n.className=cls;return n;}
function shape(option){
 var built=option.cards.filter(function(c){return c<3;}),ext=option.cards.filter(function(c){return c>=3;});
 var lang=M.uiLanguage(),name=lang==='zh'?built.length+'贴'+ext.length+'插':built.length+' '+t('内置')+' / '+ext.length+' '+t('插拔');
 if(built.length)name+=' · '+t('内置')+' '+built.map(function(c){return c+1;}).join('+');
 if(option.value==='4'&&option.model==='u30pro')name+=' · '+t('原厂无屏形态');
 return name;
}
var CSS='.mud-product-form{padding:20px;margin-bottom:20px}.mud-product-form h3{margin:0 0 12px;padding:0;border:0;font-size:1rem}.mud-product-form .pf-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}.mud-product-form .pf-picker{min-width:0;max-width:100%}.mud-product-form .pf-picker>.cbi-dropdown{max-width:100%;box-sizing:border-box}.mud-product-form .pf-meta{display:flex;gap:4px 20px;flex-wrap:wrap}.mud-product-form .mud-note{line-height:1.5;margin:4px 0;overflow-wrap:anywhere}.mud-product-form .pf-status{display:flex;align-items:center;gap:8px;margin-top:8px}.mud-product-form .pf-status:empty{display:none}.mud-product-form .pf-status .mud-spin{flex:none}@media(max-width:600px){.mud-product-form{padding:16px}.mud-product-form .pf-picker{flex-basis:100%}.mud-product-form .pf-row .mud-btn{flex:1;white-space:normal}}';

return baseclass.extend({
 title:function(){return t('产品形态');},
 load:function(){return get().catch(function(){return {ok:0};});},
 render:function(initial,options){
  if(initial&&initial.supported===false)return null;
  var embedded=options&&options.embedded;
  M.injectCss();var root=node(embedded?'div':'section',null,embedded?'mud mud-product-form mud-product-form-embedded':'mud mud-card mud-product-form');root.append(node('style',CSS+'.mud-product-form-embedded{padding:0;margin:0}'));
  if(!embedded)root.append(node('h3','产品形态'));
  root.append(node('p','按当前型号选择 SIM 卡源组合。','mud-note'));
  var meta=node('div',null,'pf-meta mud-note'),row=node('div',null,'pf-row'),select=node('div',null,'pf-picker'),save=node('button','保存产品形态','mud-btn primary'),automatic=node('button','恢复自动识别','mud-btn'),refresh=node('button','刷新','mud-btn'),state=node('div',null,'pf-status mud-note');
  select.setAttribute('aria-label',t('产品形态'));save.type=automatic.type=refresh.type='button';state.setAttribute('role','status');state.setAttribute('aria-live','polite');row.append(select,save,automatic,refresh);root.append(meta,node('p','保存后重启生效，不会立即切卡。下次启动会清理新形态中不存在的旧卡源选择，保留短信、流量、APN 和射频设置。','mud-note'),row,state);
  var dropdown=null,catalog='',data=null,busy=false,writable=typeof L.hasViewPermission!=='function'||L.hasViewPermission();
  function controls(){var disabled=busy||!data||!writable;if(dropdown){dropdown.node.toggleAttribute('disabled',disabled);dropdown.node.setAttribute('aria-disabled',String(disabled));dropdown.node.setAttribute('tabindex',disabled?'-1':'0');}save.disabled=disabled||dropdown.getValue()===data.mode;automatic.disabled=disabled||data.mode==='auto';refresh.disabled=busy;root.setAttribute('aria-busy',String(busy));}
  function paint(value){
   data=value&&value.ok&&Array.isArray(value.options)?value:null;
   meta.replaceChildren();
   if(data){
    var choices={auto:t('自动识别（跟随原机 board_id）')},order=['auto'];
    data.options.forEach(function(o){choices[o.value]=shape(Object.assign({model:data.model},o));order.push(o.value);});
    var signature=JSON.stringify(choices);
    if(!dropdown||catalog!==signature){dropdown=new ui.Dropdown(data.mode,choices,{sort:order,multiple:false,optional:false});select.replaceChildren(dropdown.render());catalog=signature;dropdown.node.setAttribute('aria-label',t('产品形态'));}
    else dropdown.setValue(data.mode);
    var current=data.effective_board_id==null?t('未知'):shape(Object.assign({model:data.model},data.options.find(function(o){return o.value===String(data.effective_board_id);})||{cards:[],value:''}));
    meta.append(node('span',({f50pro:'F50 Pro · MU3356',u30pro:'U30 Pro · MU5358',v50:'V50'})[data.model]||data.model),node('span',t('原机 board_id')+'：'+(data.detected_board_id==null?t('未知'):data.detected_board_id)),node('span',t('当前生效')+'：'+current));
    state.textContent=data.pending_reboot?t('待重启生效'):'';
   }else state.textContent=t('无法读取产品形态，请重试');
   controls();
  }
  async function reload(){if(busy)return;busy=true;controls();try{paint(await get());}catch(e){paint(null);}finally{busy=false;controls();}}
  async function apply(choice){
   if(busy||!data||!writable||choice===data.mode)return;
   var product=data.model;busy=true;controls();
   try{
    if(!await M.confirmBox(t('确认产品形态？'),t('选错形态可能导致卡片不可用或卡槽与 IMEI 归属变化。请按实际硬件选择；保存后不会自动重启。')))return;
    state.replaceChildren(node('i',null,'mud-spin'),node('span','正在保存…'));
    var result=await set(product,choice);
    if(!result||!result.ok){var errors={product_form_unsaved:'请先保存或撤销其他待提交的适配设置',product_form_model_changed:'设备型号已改变，请刷新页面',product_form_invalid:'此形态不属于当前型号',product_form_busy:'另一项产品形态设置正在保存'};throw Error(t(errors[result&&result.error]||'保存失败，请重试'));}
    paint(result);M.toast(t(result.pending_reboot?'设置已保存，请重启设备生效':'设置已保存'),{type:'success'});
   }catch(e){state.textContent=e.message||t('保存失败，请重试');M.toast(state.textContent,{type:'error'});}
   finally{busy=false;controls();}
  }
  function blockDisabled(ev){if((busy||!data||!writable)&&ev.key!=='Tab'){ev.preventDefault();ev.stopImmediatePropagation();}}
  select.addEventListener('click',blockDisabled,true);select.addEventListener('keydown',blockDisabled,true);select.addEventListener('widget-change',controls);save.onclick=function(){if(dropdown)apply(dropdown.getValue());};automatic.onclick=function(){apply('auto');};refresh.onclick=reload;paint(initial);return root;
 }
});
