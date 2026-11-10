'use strict';
'require view';
'require rpc';
'require ui';
'require mu300.common as M';

var start = rpc.declare({object:'unisoc-euicc',method:'start',params:['job','slot','action','transport','eid','iccid','nickname','activation','confirmation','confirm','aid','scan_se'],nobatch:true,expect:{'':{}}});
var status = rpc.declare({object:'unisoc-euicc',method:'status',params:['job'],nobatch:true,expect:{'':{}}});
var answer = rpc.declare({object:'unisoc-euicc',method:'answer',params:['job','accept'],nobatch:true,expect:{'':{}}});
var cancel = rpc.declare({object:'unisoc-euicc',method:'cancel',params:['job'],nobatch:true,expect:{'':{}}});
var words = {
 'eSIM 管理':['eSIM management','eSIM yönetimi'],
 '管理插在设备卡槽中的实体 eUICC。普通 SIM 卡不能通过此页面变成 eSIM。':['Manage a removable eUICC in a device SIM slot. A regular SIM cannot be converted into an eSIM here.','Cihazın SIM yuvasındaki çıkarılabilir eUICC kartını yönetin. Normal bir SIM burada eSIM’e dönüştürülemez.'],
 '管理卡槽':['SIM to manage','Yönetilecek SIM'],
 '读取 eUICC':['Read eUICC','eUICC’yi oku'],
 '尚未读取':['Not read yet','Henüz okunmadı'],
 '已识别 eUICC':['eUICC detected','eUICC algılandı'],
 '正在准备…':['Preparing…','Hazırlanıyor…'],
 '正在读取卡片…':['Reading the card…','Kart okunuyor…'],
 '正在与 eUICC 交互…':['Communicating with eUICC…','eUICC ile iletişim kuruluyor…'],
 '正在连接运营商服务器…':['Connecting to the operator server…','Operatör sunucusuna bağlanılıyor…'],
 '正在安装配置…':['Installing the profile…','Profil yükleniyor…'],
 '正在读取配置…':['Reading profiles…','Profiller okunuyor…'],
 '操作完成':['Operation completed','İşlem tamamlandı'],
 '正在操作此卡，请勿拔卡、切换卡源或关闭设备。':['An operation is in progress. Do not remove the card, switch its source, or power off.','İşlem sürüyor. Kartı çıkarmayın, kart kaynağını değiştirmeyin veya cihazı kapatmayın.'],
 '卡片信息':['Card information','Kart bilgileri'],
 '卡片固件':['Card firmware','Kart yazılımı'],
 '可用空间':['Free space','Boş alan'],
 '配置列表':['Profiles','Profiller'],
 '此卡还没有配置':['This card has no profiles','Bu kartta henüz profil yok'],
 '已启用':['Enabled','Etkin'],
 '未启用':['Disabled','Devre dışı'],
 '启用':['Enable','Etkinleştir'],
 '停用':['Disable','Devre dışı bırak'],
 '删除':['Delete','Sil'],
 '重命名':['Rename','Yeniden adlandır'],
 '保存':['Save','Kaydet'],
 '取消':['Cancel','İptal'],
 '确认':['Confirm','Onayla'],
 '继续':['Continue','Devam et'],
 '输入小写 yes 后继续':['Type lowercase yes to continue','Devam etmek için küçük harfle yes yazın'],
 '此操作会改变卡内配置，可能中断该卡的蜂窝连接。请确认当前卡槽、EID 和 ICCID。':['This changes profiles on the card and may interrupt its cellular connection. Check the SIM slot, EID, and ICCID.','Bu işlem karttaki profilleri değiştirir ve hücresel bağlantısını kesebilir. SIM yuvasını, EID ve ICCID bilgilerini kontrol edin.'],
 '删除后不能从本机恢复；部分运营商的激活码仅能使用一次。':['Deletion cannot be undone locally; some activation codes can only be used once.','Silme işlemi cihazdan geri alınamaz; bazı etkinleştirme kodları yalnızca bir kez kullanılabilir.'],
 '配置昵称':['Profile nickname','Profil takma adı'],
 '下载配置':['Download profile','Profil indir'],
 '激活码':['Activation code','Etkinleştirme kodu'],
 '确认码（可选）':['Confirmation code (optional)','Onay kodu (isteğe bağlı)'],
 '粘贴以 LPA:1$ 开头的完整激活码，或读取二维码图片。下载需要可用的互联网连接，不会自动启用新配置。':['Paste the full activation code starting with LPA:1$, or read a QR image. Downloading requires Internet access; the new profile will not be enabled automatically.','LPA:1$ ile başlayan tam etkinleştirme kodunu yapıştırın veya QR görselini okuyun. İndirme için internet gerekir; yeni profil otomatik etkinleştirilmez.'],
 '读取二维码图片':['Read QR image','QR görselini oku'],
 '二维码中没有有效的 eSIM 激活码':['No valid eSIM activation code in this QR image','QR görselinde geçerli eSIM etkinleştirme kodu yok'],
 '正在识别二维码…':['Reading the QR image…','QR görseli okunuyor…'],
 '通知管理':['Notifications','Bildirimler'],
 '待发送通知':['Pending notifications','Bekleyen bildirimler'],
 '发送待处理通知':['Send pending notifications','Bekleyen bildirimleri gönder'],
 '配置变更会在卡中生成运营商通知。发送成功后会清除对应的待处理通知。':['Profile changes create operator notifications on the card. Successfully sent notifications are removed from the pending list.','Profil değişiklikleri kartta operatör bildirimleri oluşturur. Başarıyla gönderilen bildirimler bekleyen listeden kaldırılır.'],
 '接口设置（高级）':['Interface settings (advanced)','Arayüz ayarları (gelişmiş)'],
 'APDU 接口':['APDU interface','APDU arayüzü'],
 '仅在模组不支持默认接口时更改。更改后需重新读取卡片。':['Change only if the modem does not support the default interface. Read the card again after changing it.','Yalnızca modem varsayılan arayüzü desteklemiyorsa değiştirin. Değişiklikten sonra kartı yeniden okuyun.'],
 '无法与设备通信；任务可能仍在运行，可重新检查状态。':['Communication failed; the operation may still be running. You can check its status again.','İletişim başarısız; işlem hâlâ sürüyor olabilir. Durumunu yeniden kontrol edebilirsiniz.'],
 '重新检查状态':['Check status again','Durumu yeniden kontrol et'],
 '卡槽已改变，请重新读取':['SIM changed; read the card again','SIM değişti; kartı yeniden okuyun'],
 '当前卡不是 eUICC，或无法选择其管理应用':['The card is not an eUICC, or its management application cannot be selected','Kart eUICC değil veya yönetim uygulaması seçilemiyor'],
 '未插卡':['No SIM inserted','SIM takılı değil'],
 '模组暂不可用':['Modem unavailable','Modem kullanılamıyor'],
 '模组正忙，请稍后重试':['Modem is busy; try again shortly','Modem meşgul; kısa süre sonra tekrar deneyin'],
 '模组拒绝了 APDU 请求，请核对接口设置':['Modem rejected the APDU request; check the interface setting','Modem APDU isteğini reddetti; arayüz ayarını kontrol edin'],
 'AT 请求超时，请重新读取确认卡片状态':['AT request timed out; read the card again to check its state','AT isteği zaman aşımına uğradı; kart durumunu yeniden okuyun'],
 '已有 eUICC 操作正在执行':['An eUICC operation is already running','Bir eUICC işlemi zaten sürüyor'],
 '操作中断，请重新读取卡片确认结果，不要直接重复下载或删除':['Operation interrupted. Read the card to check the result before repeating a download or deletion','İşlem kesildi. İndirme veya silmeyi tekrarlamadan önce sonucu kontrol etmek için kartı okuyun'],
 '操作超时，请重新读取卡片确认结果':['Operation timed out; read the card to check the result','İşlem zaman aşımına uğradı; sonucu kontrol etmek için kartı okuyun'],
 '请先停用此配置再删除':['Disable this profile before deleting it','Silmeden önce bu profili devre dışı bırakın'],
 '配置已不存在，请重新读取':['Profile no longer exists; read the card again','Profil artık mevcut değil; kartı yeniden okuyun'],
 '系统未安装 lpac，请更新完整固件':['lpac is not installed; update the complete firmware','lpac kurulu değil; tam cihaz yazılımını güncelleyin'],
 '激活码格式无效':['Invalid activation code','Geçersiz etkinleştirme kodu'],
 '操作失败，请重新读取卡片确认状态':['Operation failed; read the card to check its state','İşlem başarısız; kart durumunu yeniden okuyun']
};
Object.assign(words,{
 '安全元件':['Secure element','Güvenli öğe'],
 '默认':['Default','Varsayılan'],
 '安装预览':['Installation preview','Yükleme önizlemesi'],
 '运营商':['Provider','Sağlayıcı'],
 '配置名称':['Profile name','Profil adı'],
 '确认安装此配置？':['Install this profile?','Bu profil yüklensin mi?'],
 '正在取消…':['Cancelling…','İptal ediliyor…'],
 '操作已取消，请重新读取卡片确认状态':['Operation cancelled; read the card to check its state','İşlem iptal edildi; durumunu kontrol etmek için kartı okuyun'],

 '服务器证书校验失败，请核对设备时间和 CA 证书':['Server certificate verification failed; check the device clock and CA certificates','Sunucu sertifikası doğrulanamadı; cihaz saatini ve CA sertifikalarını kontrol edin'],
 'eSIM 信任证书缺失或损坏，请更新完整固件':['eSIM trust certificate is missing or damaged; update the complete firmware','eSIM güven sertifikası eksik veya bozuk; tam aygıt yazılımını güncelleyin'],
 '运营商服务器连接超时':['Operator server connection timed out','Operatör sunucusu bağlantısı zaman aşımına uğradı'],
 '无法连接运营商服务器，请检查设备网络':['Cannot connect to the operator server; check the device network','Operatör sunucusuna bağlanılamıyor; cihaz ağını kontrol edin'],
 '任务参数已改变，请重新读取卡片':['Operation parameters changed; read the card again','İşlem parametreleri değişti; kartı yeniden okuyun'],
 '操作已提交，但回读未确认，请重新读取卡片，不要重复执行':['Operation was submitted, but readback was not confirmed. Read the card again; do not repeat the operation','İşlem gönderildi ancak geri okuma doğrulanamadı. Kartı yeniden okuyun; işlemi tekrarlamayın'],
 '此模组暂未提供可用的逻辑通道':['No usable logical channel is currently available on this modem','Modemde şu anda kullanılabilir mantıksal kanal yok'],
 'eUICC 返回的数据不完整，请重新读取':['Incomplete eUICC response; read the card again','eUICC yanıtı eksik; kartı yeniden okuyun']
});
Object.assign(words,{
 "管理": [
  "Manage",
  "Yönet"
 ],
 "名称": [
  "Name",
  "Ad"
 ],
 "状态": [
  "Status",
  "Durum"
 ],
 "操作": [
  "Actions",
  "İşlemler"
 ],
 "搜索配置": [
  "Search profiles",
  "Profil ara"
 ],
 "名称、运营商或 ICCID": [
  "Name, provider or ICCID",
  "Ad, sağlayıcı veya ICCID"
 ],
 "全部状态": [
  "All statuses",
  "Tüm durumlar"
 ],
 "没有匹配的配置": [
  "No matching profiles",
  "Eşleşen profil yok"
 ],
 "共 %s 个配置": [
  "%s profiles",
  "%s profil"
 ],
 "匹配 %s 个": [
  "%s matches",
  "%s eşleşme"
 ],
 "每页": [
  "Per page",
  "Sayfa başına"
 ],
 "上一页": [
  "Previous",
  "Önceki"
 ],
 "下一页": [
  "Next",
  "Sonraki"
 ],
 "第 %s 页，共 %s 页": [
  "Page %s of %s",
  "Sayfa %s / %s"
 ]
});
function t(s){var lang=M.uiLanguage();return lang==='zh'?s:(words[s]||[s,s])[lang==='tr'?1:0];}
function el(tag,text,cls){var n=document.createElement(tag);if(text!=null)n.textContent=t(text);if(cls)n.className=cls;return n;}
function button(text,click,cls){var b=el('button',text,'mud-btn '+(cls||''));b.type='button';b.onclick=click;return b;}
var errorText={no_sim:'未插卡',not_euicc:'当前卡不是 eUICC，或无法选择其管理应用',apdu_select:'当前卡不是 eUICC，或无法选择其管理应用',modem_unavailable:'模组暂不可用',modem_busy:'模组正忙，请稍后重试',at_rejected:'模组拒绝了 APDU 请求，请核对接口设置',at_timeout:'AT 请求超时，请重新读取确认卡片状态',card_changed:'卡槽已改变，请重新读取',operation_busy:'已有 eUICC 操作正在执行',operation_interrupted:'操作中断，请重新读取卡片确认结果，不要直接重复下载或删除',operation_timeout:'操作超时，请重新读取卡片确认结果',profile_enabled:'请先停用此配置再删除',profile_missing:'配置已不存在，请重新读取',lpac_missing:'系统未安装 lpac，请更新完整固件',activation_invalid:'激活码格式无效'};
Object.assign(errorText,{server_ca:'eSIM 信任证书缺失或损坏，请更新完整固件',server_tls:'服务器证书校验失败，请核对设备时间和 CA 证书',server_timeout:'运营商服务器连接超时',server_unavailable:'无法连接运营商服务器，请检查设备网络',request_changed:'任务参数已改变，请重新读取卡片',cancelled:'操作已取消，请重新读取卡片确认状态',readback_failed:'操作已提交，但回读未确认，请重新读取卡片，不要重复执行',apdu_channel:'此模组暂未提供可用的逻辑通道',apdu_reply:'eUICC 返回的数据不完整，请重新读取'});
var CSS=`
.mud.mud-euicc{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;max-width:1200px;margin:0 auto}.mud-euicc .eu-heading h2{margin:0;padding:0;border:0}.mud-euicc .eu-heading p{margin:8px 0 0;max-width:64ch;font-size:.78rem;color:var(--text-muted,var(--text-light,#888))}
.mud-euicc .eu-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.mud-euicc .eu-top{display:flex;align-items:flex-end;gap:16px}
.mud-euicc .eu-top.eu-panel{padding:16px}
.mud-euicc .eu-top-fields{display:flex;flex:1;gap:16px;min-width:0}
.mud-euicc .eu-top label{display:grid;gap:7px;flex:0 1 300px;min-width:0;margin:0}
.mud-euicc .eu-top label[hidden]{display:none}
.mud-euicc .eu-field-label{font-size:.72rem;font-weight:500;line-height:1.4;color:var(--text-muted,var(--text-light,#888))}
.mud-euicc .eu-top select{width:100%;max-width:none;min-width:0;min-height:40px;margin:0;font-size:.84rem}
.mud-euicc .eu-read{flex:none;min-height:40px;margin-left:auto;gap:8px;padding:8px 14px;font-weight:600;background:color-mix(in srgb,var(--brand,var(--primary,#168e86)) 14%,var(--surface,var(--background,#fff)));border-color:color-mix(in srgb,var(--brand,var(--primary,#168e86)) 45%,var(--hairline,var(--border,#ccc)))}
.mud-euicc .eu-read svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.mud-euicc .eu-summary{display:grid;grid-template-columns:2fr 1fr 1fr;gap:16px}.mud-euicc .eu-value{display:block;font-variant-numeric:tabular-nums;overflow-wrap:anywhere;margin-top:5px}.mud-euicc .eu-body{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:16px}.mud-euicc .eu-panel{padding:20px;min-width:0;margin:0}.mud-euicc h3{padding:0;border:0}.mud-euicc .eu-form{display:grid;gap:12px}.mud-euicc label.eu-field{display:grid;gap:6px}.mud-euicc input,.mud-euicc select,.eu-confirm input{box-sizing:border-box;min-width:0;width:100%;color:var(--text,#222);background:var(--surface,var(--background,#fff));border:1px solid var(--hairline,var(--border,#ccc));border-radius:8px;padding:9px 12px;font:inherit}.mud-euicc .eu-state{display:flex;gap:10px;align-items:center;min-height:20px;margin:0}.mud-euicc .eu-state[hidden]{display:none}.eu-confirm{display:grid;gap:12px;overflow-wrap:anywhere}.eu-confirm dl{margin:0}.eu-confirm dd{margin:0 0 8px;font-variant-numeric:tabular-nums}.eu-modal{position:fixed;inset:0;z-index:20000;background:color-mix(in srgb,var(--background,#000) 80%,transparent);display:flex;align-items:center;justify-content:center;padding:24px;backdrop-filter:blur(4px)}.eu-modal>div{max-width:440px;padding:24px;border:1px solid var(--hairline,var(--border,#bbb));border-radius:16px;background:var(--surface,var(--background,#fff));color:var(--text,#222);text-align:center}.eu-modal.mud{max-width:none;margin:0}.eu-modal .mud-spin{display:block;width:30px;height:30px;margin:0 auto 16px;border:3px solid var(--hairline,var(--border,#ccc));border-top-color:var(--brand,var(--primary,#168e86));border-radius:50%;animation:euiccspin .8s linear infinite}@keyframes euiccspin{to{transform:rotate(360deg)}}.mud-euicc .eu-empty{padding:24px;text-align:center}.mud-euicc .mud-note{line-height:1.6;overflow-wrap:anywhere}

.mud-euicc .eu-profile-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 12px}
.mud-euicc .eu-search{flex:1;min-width:140px;max-width:360px;font-size:.8rem;padding:8px 10px;margin:0}
.mud-euicc .eu-filter{width:auto;max-width:100%;font-size:.8rem;padding:8px 10px;margin:0}
.mud-euicc .eu-profile-table{width:100%;table-layout:fixed;border-collapse:collapse;margin:0;background:transparent}
.mud-euicc .eu-profile-table th,.mud-euicc .eu-profile-table td{padding:9px 10px;text-align:left;vertical-align:middle;border:0;border-bottom:1px solid var(--hairline,var(--border,#ddd));background:transparent}
.mud-euicc .eu-profile-table th{position:static;font-size:.7rem;font-weight:500;color:var(--text-muted,var(--text-light,#888));line-height:1.5}
.mud-euicc .eu-profile-table td{font-size:.78rem;line-height:1.5}
.mud-euicc .eu-profile-table th:nth-child(1){width:25%}.mud-euicc .eu-profile-table th:nth-child(2){width:18%}.mud-euicc .eu-profile-table th:nth-child(3){width:29%}.mud-euicc .eu-profile-table th:nth-child(4){width:14%}.mud-euicc .eu-profile-table th:nth-child(5){width:14%}
.mud-euicc .eu-profile-table .eu-profile-name{font-weight:600}.mud-euicc .eu-profile-table .eu-profile-name,.mud-euicc .eu-profile-table .eu-provider{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mud-euicc .eu-profile-table .eu-iccid{font-family:monospace;font-size:.75rem;overflow-wrap:anywhere;font-variant-numeric:tabular-nums;color:var(--text-muted,var(--text-light,#888))}
.mud-euicc .eu-profile-table .eu-status{white-space:nowrap;font-size:.7rem;color:var(--text-muted,var(--text-light,#888))}.mud-euicc .eu-profile-table .enabled .eu-status{color:var(--success,var(--brand,#168e86));font-weight:600}
.mud-euicc .eu-profile-table .eu-manage{padding:5px 10px;min-height:30px;font-size:.75rem;white-space:nowrap}
.mud-euicc .eu-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px;font-size:.72rem;color:var(--text-muted,var(--text-light,#888))}
.mud-euicc .eu-page-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.mud-euicc .eu-page-size{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;margin:0}
.mud-euicc .eu-page-size select{width:auto;padding:5px 8px;font-size:.75rem;margin:0}.mud-euicc .eu-pagination .mud-btn{padding:5px 10px;min-height:30px;font-size:.75rem}
@media(max-width:700px){.mud-euicc .eu-profile-table,.mud-euicc .eu-profile-table tbody{display:block}.mud-euicc .eu-profile-table thead{display:none}.mud-euicc .eu-profile-table tr{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:3px 8px;padding:10px 0;border-bottom:1px solid var(--hairline,var(--border,#ddd))}.mud-euicc .eu-profile-table td{display:block;min-width:0;padding:0;border:0}.mud-euicc .eu-profile-table .eu-profile-name{grid-column:1;grid-row:1}.mud-euicc .eu-profile-table .eu-provider{grid-column:1 / 3;grid-row:2;font-size:.7rem;color:var(--text-muted,var(--text-light,#888))}.mud-euicc .eu-profile-table .eu-iccid{grid-column:1 / 3;grid-row:3}.mud-euicc .eu-profile-table .eu-status{grid-column:2;grid-row:1;align-self:center}.mud-euicc .eu-profile-table .eu-row-actions{grid-column:3;grid-row:1 / 4;align-self:center}.mud-euicc .eu-search{max-width:none}.mud-euicc .eu-pagination{gap:10px}.mud-euicc .eu-page-controls{width:100%;justify-content:flex-end}}
@media(max-width:700px){.mud-euicc .eu-body,.mud-euicc .eu-summary{grid-template-columns:1fr}.mud-euicc .eu-panel{padding:16px}.mud-euicc .eu-top{flex-direction:column;align-items:stretch;gap:12px}.mud-euicc .eu-top-fields{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;width:100%}.mud-euicc .eu-top label{width:100%;flex:auto}.mud-euicc .eu-read{align-self:flex-end;min-width:140px}.mud-euicc .eu-actions .mud-btn{flex:1}.eu-modal{padding:16px}}
`;

return view.extend({
 load:function(){return M.callSimGet().catch(function(){return {};});},
 render:function(sim){
  var self=this;M.injectCss();M.localizeMenu();self.sim=sim;self.data=null;self.busy=false;self.disposed=false;self.profileQuery="";self.profileFilter="";self.profilePage=0;self.profilePageSize=10;
  self.root=el('div',null,'mud mud-euicc');var heading=el('div',null,'eu-heading');heading.append(el('h2','eSIM 管理'),el('p','管理插在设备卡槽中的实体 eUICC。普通 SIM 卡不能通过此页面变成 eSIM。','mud-note'));self.root.append(el('style',CSS),heading);
  var top=el('section',null,'mud-card eu-panel eu-top'),fields=el('div',null,'eu-top-fields'),label=el('label');label.append(el('span','管理卡槽','eu-field-label'));self.slot=el('select',null,'eu-slot');
  ['0','1'].forEach(function(slot){if(sim.slots===1&&slot==='1')return;var o=el('option',(M.slotName?M.slotName(slot,sim):'SIM '+(Number(slot)+1))+' · IMEI'+(Number(slot)+1));o.value=slot;self.slot.append(o);});
  self.slot.value=(M.selectedSlot?M.selectedSlot():M.simSlot())||'0';if(self.slot.selectedIndex<0)self.slot.selectedIndex=0;label.append(self.slot);fields.append(label);
  var read=button('读取 eUICC',function(){self.run('scan');},'eu-read');
  var icon=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS('http://www.w3.org/2000/svg','path');
  icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('focusable','false');path.setAttribute('d','M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17');icon.append(path);read.prepend(icon);top.append(fields,read);
  self.aid='';self.seMask=0;self.se=el('select');self.seLabel=el('label');self.seLabel.append(el('span','安全元件','eu-field-label'),self.se);self.seLabel.hidden=true;fields.append(self.seLabel);self.se.onchange=function(){self.profilePage=0;self.profileQuery='';self.profileFilter='';self.aid=self.se.value;self.data=null;self.paint();self.run('scan');};
  self.state=el('div','尚未读取','eu-state mud-note');self.state.setAttribute('role','status');self.state.setAttribute('aria-live','polite');
  self.info=el('section',null,'mud-card eu-panel');self.profiles=el('section',null,'mud-card eu-panel');
  self.root.append(top,self.state,self.info,self.profiles);
  var body=el('div',null,'eu-body');self.download=el('section',null,'mud-card eu-panel eu-form');self.download.append(el('h3','下载配置'),el('p','粘贴以 LPA:1$ 开头的完整激活码，或读取二维码图片。下载需要可用的互联网连接，不会自动启用新配置。','mud-note'));
  self.activation=el('input');self.activation.type='password';self.activation.autocomplete='off';self.activation.maxLength=2048;self.activation.placeholder='LPA:1$…';
  self.confirmation=el('input');self.confirmation.type='password';self.confirmation.autocomplete='off';self.confirmation.maxLength=128;
  [['激活码',self.activation],['确认码（可选）',self.confirmation]].forEach(function(pair){var l=el('label',pair[0],'eu-field');l.append(pair[1]);self.download.append(l);});
  var actions=el('div',null,'eu-actions'),qr=el('input');qr.type='file';qr.accept='image/*';qr.hidden=true;qr.onchange=function(){self.readQR(qr.files[0]);qr.value='';};
  actions.append(button('读取二维码图片',function(){qr.click();}),button('下载配置',function(){self.ask('download',null,{activation:self.activation.value,confirmation:self.confirmation.value});},'primary'),qr);self.download.append(actions);
  self.notes=el('section',null,'mud-card eu-panel');body.append(self.download,self.notes);self.root.append(body);
  var advanced=el('details',null,'mud-card eu-panel');advanced.append(el('summary','接口设置（高级）'));var a=el('label','APDU 接口','eu-field');self.transport=el('select');[['csim','AT+CSIM'],['logical','AT+CCHO / CGLA']].forEach(function(v){var o=el('option',v[1]);o.value=v[0];self.transport.append(o);});a.append(self.transport);advanced.append(a,el('p','仅在模组不支持默认接口时更改。更改后需重新读取卡片。','mud-note'));self.root.append(advanced);
  self.slot.onchange=self.transport.onchange=function(){self.profilePage=0;self.profileQuery='';self.profileFilter='';self.data=null;self.aid='';self.seMask=0;self.paint();self.state.textContent=t('尚未读取');};
  self.paint();
  var saved;try{saved=JSON.parse(sessionStorage.getItem('unisoc-euicc-job')||'null');}catch(e){}
  // Resume observation only: never repeat a download/delete on reload.
  if(saved&&/^[0-9a-f]{32}$/.test(saved.job)&&['0','1'].includes(saved.slot)){self.slot.value=saved.slot;self.transport.value=saved.transport||'csim';self.job=saved.job;self.action=saved.action;self.timer=setTimeout(function(){if(self.root.isConnected)self.watch();},0);}
  return self.root;
 },
 paint:function(){
  var self=this,data=self.data;if(data){self.aid=data.aid||self.aid||'';self.seMask=data.ses||self.seMask||0;}self.se.replaceChildren();[['',t('默认')],['A06573746B6D65FFFF4953442D522030','SE 0'],['A06573746B6D65FFFF4953442D522031','SE 1']].forEach(function(v,i){if(i&&!(self.seMask&(1<<(i-1))))return;var o=el('option',v[1]);o.value=v[0];self.se.append(o);});self.se.value=self.aid;self.seLabel.hidden=!self.seMask;self.info.replaceChildren(el('h3','卡片信息'));self.profiles.replaceChildren(el('h3','配置列表'));self.notes.replaceChildren(el('h3','通知管理'));
  if(data){var summary=el('div',null,'eu-summary');[['EID',data.eid],['卡片固件',data.firmware||'—'],['可用空间',typeof data.free_bytes==='number'?data.free_bytes.toLocaleString()+' B':'—']].forEach(function(p){var c=el('div',p[0],'mud-note');c.append(el('strong',p[1],'eu-value'));summary.append(c);});self.info.append(summary);
   self.paintProfiles();
   var count=Array.isArray(data.notifications)?data.notifications.length:0;self.notes.append(el('p',t('待发送通知')+' · '+count,'eu-value'),el('p','配置变更会在卡中生成运营商通知。发送成功后会清除对应的待处理通知。','mud-note'));var n=button('发送待处理通知',function(){self.run('notifications');});n.disabled=!count;self.notes.append(n);
  }else{self.info.append(el('p','尚未读取','mud-note'));self.profiles.append(el('p','尚未读取','mud-note'));self.notes.append(el('p','尚未读取','mud-note'));}
  self.controls();
 },
 paintProfiles:function(){
  var self=this,tools=el('div',null,'eu-profile-tools'),search=el('input',null,'eu-search'),filter=el('select',null,'eu-filter');
  search.type='search';search.placeholder=t('名称、运营商或 ICCID');search.setAttribute('aria-label',t('搜索配置'));search.value=self.profileQuery;
  [['','全部状态'],['enabled','已启用'],['disabled','未启用']].forEach(function(v){var option=el('option',v[1]);option.value=v[0];filter.append(option);});
  filter.setAttribute('aria-label',t('状态'));filter.value=self.profileFilter;
  tools.append(search,filter);self.profileRows=el('tbody');var table=el('table',null,'eu-profile-table'),head=el('thead'),titles=el('tr');
  ['名称','运营商','ICCID','状态','操作'].forEach(function(name){var cell=el('th',name);cell.scope='col';titles.append(cell);});head.append(titles);table.append(head,self.profileRows);
  self.profileFooter=el('div',null,'eu-pagination');self.profiles.append(tools,table,self.profileFooter);
  search.oninput=function(){self.profileQuery=search.value;self.profilePage=0;self.paintProfileRows();};
  filter.onchange=function(){self.profileFilter=filter.value;self.profilePage=0;self.paintProfileRows();};
  self.paintProfileRows();
 },
 paintProfileRows:function(){
  var self=this,all=self.data.profiles||[],query=self.profileQuery.trim().toLocaleLowerCase(M.uiLanguage()==='tr'?'tr':undefined);
  var found=all.filter(function(p){return (!self.profileFilter||p.profileState.toLowerCase()===self.profileFilter)&&(!query||[p.profileNickname,p.profileName,p.serviceProviderName,p.iccid].join(' ').toLocaleLowerCase(M.uiLanguage()==='tr'?'tr':undefined).includes(query));});
  var pages=Math.max(1,Math.ceil(found.length/self.profilePageSize));self.profilePage=Math.max(0,Math.min(self.profilePage,pages-1));
  self.profileRows.replaceChildren();found.slice(self.profilePage*self.profilePageSize,(self.profilePage+1)*self.profilePageSize).forEach(function(p){
   var enabled=p.profileState.toLowerCase()==='enabled',row=el('tr',null,'eu-profile'+(enabled?' enabled':'')),name=p.profileNickname||p.profileName||p.serviceProviderName||p.iccid;
   var title=el('td',name,'eu-profile-name'),provider=el('td',p.serviceProviderName||'—','eu-provider');title.title=name;provider.title=p.serviceProviderName||'';
   var action=el('td',null,'eu-row-actions'),manage=button('管理',function(){self.manage(p);},'eu-manage');manage.setAttribute('aria-label',t('管理')+' · '+name);action.append(manage);
   row.append(title,provider,el('td',p.iccid,'eu-iccid'),el('td',enabled?'已启用':'未启用','eu-status'),action);self.profileRows.append(row);
  });
  if(!found.length){var empty=el('td',all.length?'没有匹配的配置':'此卡还没有配置','eu-empty mud-note');empty.colSpan=5;var row=el('tr');row.append(empty);self.profileRows.append(row);}
  var count=el('span',t('共 %s 个配置').replace('%s',all.length),'eu-profile-count');if(found.length!==all.length)count.append(document.createTextNode(' · '+t('匹配 %s 个').replace('%s',found.length)));
  var controls=el('div',null,'eu-page-controls'),label=el('label','每页','eu-page-size'),size=el('select');size.setAttribute('aria-label',t('每页'));
  [10,20,50].forEach(function(n){var option=el('option',String(n));option.value=String(n);size.append(option);});size.value=String(self.profilePageSize);label.append(size);
  size.onchange=function(){self.profilePageSize=Number(size.value);self.profilePage=0;self.paintProfileRows();};
  var previous=button('上一页',function(){if(!self.busy){self.profilePage--;self.paintProfileRows();}},'eu-previous'),next=button('下一页',function(){if(!self.busy){self.profilePage++;self.paintProfileRows();}},'eu-next');
  previous.disabled=self.profilePage===0;next.disabled=self.profilePage>=pages-1;
  controls.append(label,previous,el('span',t('第 %s 页，共 %s 页').replace('%s',self.profilePage+1).replace('%s',pages),'eu-page-label'),next);
  self.profileFooter.replaceChildren(count,controls);self.controls();
 },
 manage:async function(p){
  var self=this;if(self.busy||!self.data)return;var slot=self.slot.value,eid=self.data.eid,enabled=p.profileState.toLowerCase()==='enabled';
  var choices=[{label:t(enabled?'停用':'启用'),value:enabled?'disable':'enable'},{label:t('重命名'),value:'nickname'}];
  if(!enabled)choices.push({label:t('删除'),value:'delete',danger:true});choices.push({label:t('关闭'),value:null});
  var action=await M.choiceBox(p.profileNickname||p.profileName||p.iccid,(p.serviceProviderName||'—')+'\nICCID · '+p.iccid+'\n'+t(enabled?'已启用':'未启用'),choices);
  if(!action||self.disposed||!self.root.isConnected)return;
  if(self.slot.value!==slot||!self.data||self.data.eid!==eid||!self.data.profiles.some(function(current){return current.iccid===p.iccid;})){M.toast(t('卡槽已改变，请重新读取'),{type:'error'});return;}
  if(action==='nickname')self.rename(p);else self.ask(action,p);
 },
 controls:function(){
  var self=this,writable=typeof L.hasViewPermission!=='function'||L.hasViewPermission();
  self.root.querySelectorAll('button,input,select').forEach(function(n){if(n.dataset.euDisabled==null)n.dataset.euDisabled=n.disabled?'1':'0';n.disabled=self.busy||n.dataset.euDisabled==='1';});
  self.download.querySelectorAll('button,input').forEach(function(n){n.disabled=self.busy||!self.data||!writable;});
  self.profiles.querySelectorAll('button').forEach(function(n){n.disabled=n.disabled||!writable;});
  self.notes.querySelectorAll('button').forEach(function(n){n.disabled=n.disabled||!writable;});
  // Scanning opens a logical channel; read-only roles see recorded results only.
  if(!writable)self.root.querySelectorAll('button').forEach(function(n){n.disabled=true;});
 },
 ask:async function(action,p,extra){
  var self=this;if(self.busy||!self.data)return;var captured={slot:self.slot.value,eid:self.data.eid},box=el('div',null,'eu-confirm'),dl=el('dl');
  [[t('管理卡槽'),self.slot.selectedOptions[0].textContent],['EID',captured.eid],['ICCID',p?p.iccid:'—']].forEach(function(v){dl.append(el('dt',v[0]),el('dd',v[1]));});box.append(dl,el('p','此操作会改变卡内配置，可能中断该卡的蜂窝连接。请确认当前卡槽、EID 和 ICCID。'));if(action==='delete')box.append(el('p','删除后不能从本机恢复；部分运营商的激活码仅能使用一次。'));
  var input=el('input');input.autocomplete='off';input.placeholder=t('输入小写 yes 后继续');box.append(input);
  var title={enable:'启用',disable:'停用',delete:'删除',download:'下载配置'}[action];
  if(!await M.confirmBox(t(title),null,{content:box,okText:t('继续')})||!self.root.isConnected)return;
  if(input.value!=='yes'){M.toast(t('输入小写 yes 后继续'),{type:'error'});return;}
  if(self.slot.value!==captured.slot||!self.data||self.data.eid!==captured.eid){M.toast(t('卡槽已改变，请重新读取'),{type:'error'});return;}
  self.run(action,Object.assign({iccid:p?p.iccid:'',confirm:'yes'},extra||{}));
 },
 rename:async function(p){var self=this;if(self.busy)return;var slot=self.slot.value,eid=self.data.eid,input=el('input');input.maxLength=64;input.value=p.profileNickname;var field=el('label','配置昵称','eu-confirm');field.append(input);if(await M.confirmBox(t('重命名'),null,{content:field,okText:t('保存')})&&self.root.isConnected){if(self.slot.value!==slot||!self.data||self.data.eid!==eid){M.toast(t('卡槽已改变，请重新读取'),{type:'error'});return;}self.run('nickname',{iccid:p.iccid,nickname:input.value});}},
 overlay:function(on){var self=this;if(self.mask){self.mask.remove();self.mask=null;}if(!on)return;self.mask=el('div',null,'eu-modal mud');self.mask.setAttribute('role','status');self.mask.setAttribute('aria-live','polite');var card=el('div');self.progress=el('p','正在准备…');card.append(el('div',null,'mud-spin'),self.progress,el('p','正在操作此卡，请勿拔卡、切换卡源或关闭设备。','mud-note'));if(self.action==='download'){var stop=button('取消',async function(){stop.disabled=true;try{var reply=await cancel(self.job);if(!reply.ok)throw Error(reply.error);}catch(e){stop.disabled=false;M.toast(t(errorText[e.message]||'操作失败，请重新读取卡片确认状态'),{type:'error'});}});card.append(stop);}self.mask.append(card);document.body.append(self.mask);},
 stage:function(s){if(s==='reading_profiles')return '正在读取配置…';if(/es9p|es11/.test(s))return '正在连接运营商服务器…';if(/load_bound|store_data/.test(s))return '正在安装配置…';return '正在与 eUICC 交互…';},
 run:async function(action,extra){
  var self=this;if(self.busy)return;self.busy=true;self.action=action;self.controls();self.overlay(true);self.state.replaceChildren(el('i',null,'mud-spin'),el('span','正在准备…'));
  try{var v=Object.assign({slot:self.slot.value,action:action,transport:self.transport.value,eid:self.data?self.data.eid:'',aid:self.aid||'',scan_se:action==='scan'&&!self.seMask},extra||{});self.job=Array.from(crypto.getRandomValues(new Uint8Array(16)),function(b){return b.toString(16).padStart(2,'0');}).join('');sessionStorage.setItem('unisoc-euicc-job',JSON.stringify({job:self.job,slot:v.slot,transport:v.transport,action:action}));var r=await start(self.job,v.slot,v.action,v.transport,v.eid,v.iccid||'',v.nickname||'',v.activation||'',v.confirmation||'',v.confirm||'',v.aid||'',!!v.scan_se);if(!r.ok){self.job=null;sessionStorage.removeItem('unisoc-euicc-job');throw Error(r.error);}await self.watch();}
  catch(e){self.fail(e);}
  finally{self.activation.value='';self.confirmation.value='';}
 },
 watch:async function(){
  var self=this;if(self.disposed)return;if(!self.root.isConnected){self.unload();return;}self.busy=true;self.controls();if(!self.mask)self.overlay(true);
  try{var r=await status(self.job);if(self.disposed)return;if(!r.ok)throw Error(r.error);self.action=r.action;if(r.slot!==self.slot.value)throw Error('card_changed');
   if(r.state==='running'){
    if(r.preview){self.overlay(false);var box=el('div',null,'eu-confirm');[['运营商',(r.metadata||{}).serviceProviderName],['配置名称',(r.metadata||{}).profileName],['ICCID',(r.metadata||{}).iccid]].forEach(function(v){box.append(el('strong',v[0]),el('span',v[1]||'—'));});box.append(el('p','确认安装此配置？'));var accepted=await M.confirmBox(t('安装预览'),null,{content:box,okText:t('确认')});if(self.disposed||!self.root.isConnected)return;var reply=await answer(self.job,!!accepted);if(!reply.ok)throw Error(reply.error);self.overlay(true);}
    if(r.write&&r.write.total>0)self.progress.textContent=t('正在安装配置…')+' '+r.write.percent+'% · '+r.write.written+' / '+r.write.total+' B';else self.progress.textContent=t(self.stage(r.stage));self.timer=window.setTimeout(function(){self.watch();},1000);return;}
   sessionStorage.removeItem('unisoc-euicc-job');self.job=null;self.busy=false;self.overlay(false);if(r.state!=='done')throw Error(r.submitted?'readback_failed':r.error);self.data=r.result;self.paint();self.state.textContent=t('已识别 eUICC')+' · '+t('操作完成');
  }catch(e){self.fail(e);}
 },
 fail:function(e){var self=this;if(self.disposed)return;self.busy=false;self.overlay(false);self.data=null;self.paint();if(e.message==='invalid_job'){self.job=null;sessionStorage.removeItem('unisoc-euicc-job');e=Error('operation_interrupted');}self.state.textContent=t(errorText[e.message]||'操作失败，请重新读取卡片确认状态');if(self.job){self.state.textContent=t('无法与设备通信；任务可能仍在运行，可重新检查状态。');self.state.append(button('重新检查状态',function(){self.watch();}));self.root.querySelectorAll('button,input,select').forEach(function(n){if(!self.state.contains(n))n.disabled=true;});}M.toast(self.state.firstChild.textContent||self.state.textContent,{type:'error'});},
 readQR:async function(file){
  var self=this;if(!file||self.busy)return;if(file.size>16*1024*1024){M.toast(t('二维码中没有有效的 eSIM 激活码'),{type:'error'});return;}
  var image;
  try{self.state.textContent=t('正在识别二维码…');await new Promise(function(resolve,reject){if(window.jsQR){resolve();return;}var script=document.createElement('script');script.src=L.resource('mu300/vendor/jsQR.js');script.onload=resolve;script.onerror=reject;document.head.append(script);});image=await createImageBitmap(file);var scale=Math.min(1,2048/Math.max(image.width,image.height)),canvas=document.createElement('canvas');canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);var ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,canvas.width,canvas.height);var data=ctx.getImageData(0,0,canvas.width,canvas.height),code=window.jsQR(data.data,data.width,data.height);if(!code||!code.data.startsWith('LPA:1$'))throw Error();self.activation.value=code.data;self.state.textContent=t('激活码');}
  catch(e){M.toast(t('二维码中没有有效的 eSIM 激活码'),{type:'error'});}finally{if(image)image.close();}
 },
 unload:function(){this.disposed=true;if(this.timer)clearTimeout(this.timer);this.overlay(false);},
 handleSave:null,handleSaveApply:null,handleReset:null
});
