'use strict';
'require rpc';
'require baseclass';
/* mu300 面板共享模块：rpc 声明、格式化/信号质量助手、主题感知的样式表。
 * 页面通过 'require mu300.common' 引用（LuCI 的 dotted require 映射到
 * /luci-static/resources/mu300/common.js）。
 *
 * 样式只引用主题令牌：本机装的是 luci-theme-aurora（--surface/--hairline/
 * --brand/--text-muted/--success 等，html[data-darkmode] 一键翻转深浅色），
 * 其它主题时逐级回退到 LuCI 标准变量（--background-alt/--border/--primary...）。
 * 质量色不用写死的色值，走 --success/--warning/--danger 与 color-mix，
 * 深浅两套模式都跟随主题。 */

function selectedSlot() {
    try { return sessionStorage.getItem('mu300-view-slot') === '1' ? '1' : '0'; } catch(e) { return '0'; }
}
function selectViewedSlot(slot) {
    if (String(slot) !== '0' && String(slot) !== '1') return false;
    try { sessionStorage.setItem('mu300-view-slot', String(slot)); return true; } catch(e) { return false; }
}
function scopedRpc(options) {
    var count = (options.params || []).length;
    options.params = (options.params || []).concat(['slot']);
    var call = rpc.declare(options);
    return function() {
        var args = Array.prototype.slice.call(arguments, 0, count);
        while (args.length < count) args.push('');
        args.push(selectedSlot());
        return call.apply(null, args);
    };
}
var callSimGet = rpc.declare({ object: 'mu300dash', method: 'sim_get', expect: { '': {} } });
function simSelector(root) {
    callSimGet().then(function(info) {
        if (!root.isConnected) return;
        if (info.slots !== 2) {
            if (selectedSlot() !== '0') { sessionStorage.removeItem('mu300-view-slot'); location.reload(); }
            return;
        }
        var panel = document.createElement('details'); panel.className = 'mud-card mud-sim-selector';
        panel.style.marginBottom = '14px';
        var summary = document.createElement('summary');
        summary.className = 'mud-sim-toggle';
        summary.textContent = translate('正在查看') + ' SIM ' + (Number(selectedSlot()) + 1);
        var label = document.createElement('label'), select = document.createElement('select');
        label.textContent = translate('查看卡槽') + ' ';
        ['0','1'].forEach(function(v) { var o=document.createElement('option'); o.value=v; o.textContent='SIM '+(Number(v)+1); select.appendChild(o); });
        select.value = selectedSlot(); label.appendChild(select);
        var body=document.createElement('div'); body.style.cssText='padding-top:12px;animation:mudfade-in .2s ease';
        var note=document.createElement('p'); note.className='mud-note';
        note.textContent=translate('仅切换查看与控制对象，不切换上网卡。');
        body.append(label,note); panel.append(summary,body); root.prepend(panel);
        select.onchange=function() { selectViewedSlot(select.value); location.reload(); };
    }).catch(function() {});
}
var callRates = rpc.declare({ object: 'mu300rates', method: 'rates', nobatch: true, expect: { '': {} } });
var callCells = scopedRpc({ object: 'mu300status', method: 'cells', nobatch: true, expect: { '': {} } });
var callStatus = scopedRpc({ object: 'mu300status', method: 'status', nobatch: true, expect: { '': {} } });
var callSignal = scopedRpc({ object: 'mu300.modem', method: 'signal', nobatch: true, expect: { '': {} } });
var callSysinfo = rpc.declare({ object: 'mu300status', method: 'sysinfo', nobatch: true, expect: { '': {} } });
var callAct    = scopedRpc({ object: 'mu300dash', method: 'act', params: [ 'op', 'arg' ], expect: { '': {} } });
var callAt     = scopedRpc({ object: 'mu300dash', method: 'at', nobatch: true, params: [ 'cmd' ], expect: { '': {} } });
var callAtHist = scopedRpc({ object: 'mu300dash', method: 'at_history', expect: { '': {} } });
var callLockGet = scopedRpc({ object: 'mu300dash', method: 'lock_get', nobatch: true, expect: { '': {} } });
var callLockFresh = scopedRpc({ object: 'mu300dash', method: 'lock_get', nobatch: true, params: [ 'fresh' ], expect: { '': {} } });
var callLockSet = scopedRpc({ object: 'mu300dash', method: 'lock_set', nobatch: true, params: [ 'kind', 'val' ], expect: { '': {} } });
var callLockStatus = scopedRpc({ object: 'mu300dash', method: 'lock_status', nobatch: true, params: [ 'id' ], expect: { '': {} } });
function waitLockJob(started, active) {
	return new Promise(function(resolve, reject) {
		if (!started || !started.ok || !started.id) { reject(new Error(started && started.error || '网络设置提交失败')); return; }
		var done = false, next, limit;
		var finish = function(error, result) {
			if (done) return;
			done = true; clearTimeout(limit); clearTimeout(next);
			if (error) reject(error); else resolve(result);
		};
		limit = setTimeout(function() { finish(new Error('应用超时，请刷新确认模组状态')); }, 240000);
		var check = function() {
			if (active && !active()) { finish(new Error('页面已关闭')); return; }
			callLockStatus(started.id).then(function(r) {
				if (done) return;
				if (active && !active()) { finish(new Error('页面已关闭')); return; }
				if (r && r.id === started.id && r.state === 'done' && r.ok) finish(null, r);
				else if (!r || r.state === 'error') finish(new Error(r && r.error || '网络设置执行失败'));
				else if (r.id === started.id && (r.state === 'queued' || r.state === 'running')) next = setTimeout(check, 1000);
				else finish(new Error('无效的操作状态'));
			}, function() { finish(new Error('无法查询操作结果，请刷新确认模组状态')); });
		};
		check();
	});
}
var callSmsHead = scopedRpc({ object: 'mu300status', method: 'sms_head', nobatch: true, expect: { '': {} } });
var callSmsList = scopedRpc({ object: 'mu300dash', method: 'sms_list', params: [ 'page' ], expect: { '': {} } });
var callSmsShow = scopedRpc({ object: 'mu300dash', method: 'sms_show', params: [ 'id' ], expect: { '': {} } });
var callSmsSend = scopedRpc({ object: 'mu300dash', method: 'sms_send', params: [ 'num', 'text' ], expect: { '': {} } });
var smsJobRpc = rpc.declare({ object: 'mu300dash', method: 'sms_job', params: [ 'slot' ], nobatch: true, expect: { '': {} } });
function callSmsJob(slot) { return smsJobRpc(slot == null ? selectedSlot() : String(slot)); }
var callSmsDel  = scopedRpc({ object: 'mu300dash', method: 'sms_delete', params: [ 'id', 'sim' ], expect: { '': {} } });
var callSmsSync = scopedRpc({ object: 'mu300dash', method: 'sms_sync', expect: { '': {} } });
var callForwardGet = rpc.declare({ object: 'mu300dash', method: 'forward_get', params: [ 'profile' ], expect: { '': {} } });
var callForwardStatus = rpc.declare({ object: 'mu300dash', method: 'forward_status', params: [ 'profile' ], expect: { '': {} } });
var callForwardSet = rpc.declare({ object: 'mu300dash', method: 'forward_set', params: [ 'payload' ], expect: { '': {} } });
var callForwardClear = rpc.declare({ object: 'mu300dash', method: 'forward_log_clear', expect: { '': {} } });
var callForwardTest = rpc.declare({ object: 'mu300dash', method: 'forward_test', params: [ 'profile' ], expect: { '': {} } });
var callTrafficGet = scopedRpc({ object: 'mu300dash', method: 'traffic_get', expect: { '': {} } });
var callTrafficSet = scopedRpc({ object: 'mu300dash', method: 'traffic_set', params: [ 'payload' ], expect: { '': {} } });
var callTrafficClear = scopedRpc({ object: 'mu300dash', method: 'traffic_clear', nobatch: true, params: [ 'confirm' ], expect: { '': {} } });
var callTtlGet = rpc.declare({ object: 'mu300dash', method: 'ttl_get', expect: { '': {} } });
var callTtlSet = rpc.declare({ object: 'mu300dash', method: 'ttl_set', params: [ 'value' ], expect: { '': {} }, nobatch: true });
var callUsbGet = rpc.declare({ object: 'mu300dash', method: 'usb_get', expect: { '': {} } });
var callUsbSet = rpc.declare({ object: 'mu300dash', method: 'usb_set', params: [ 'kind', 'value', 'scope', 'auto' ], expect: { '': {} } });
var callUsbNetList = rpc.declare({ object: 'mu300dash', method: 'usb_net_list', expect: { '': {} } });
var callUsbNetAdd = rpc.declare({ object: 'mu300dash', method: 'usb_net_add', params: [ 'iface' ], expect: { '': {} } });

/* Dashboard translations deliberately ship as a tiny runtime catalog: a
 * standalone package works in any OpenWrt buildroot without po2lmo or extra
 * language packages. Chinese remains the source/fallback language. */
var DASH_I18N = {
	"AT 通道正忙，正在等待更新…": ["AT channel busy; waiting for an update…", "AT kanalı meşgul; güncelleme bekleniyor…"],
	"模组尚未返回有效的实时信号": ["The modem has not returned valid live signal data", "Modem henüz geçerli canlı sinyal verisi döndürmedi"],
	"另一项 TTL 操作正在执行，请稍后重试": ["Another TTL operation is running; please retry shortly", "Başka bir TTL işlemi çalışıyor; biraz sonra yeniden deneyin"],
	"当前设置": ["Current setting", "Geçerli ayar"],
	"固定为 %d": ["Fixed at %d", "%d olarak sabit"],
	"保持不变（默认）": ["Not changed (default)", "Değiştirilmedi (varsayılan)"],
	"生效规则": ["Rule in force", "Etkin kural"],
	"流量卸载": ["Flow offloading", "Akış hızlandırma"],
	"已启用": ["Enabled", "Etkin"],
	"已禁用": ["Disabled", "Devre dışı"],
	"蜂窝网络 TTL": ["Cellular TTL", "Mobil veri TTL"],
	"设为 %d": ["Set %d", "%d ayarla"],
	"设置": ["Set", "Ayarla"],
	"关闭 TTL": ["Turn off TTL", "TTL kapat"],
	"请输入 1 至 255 的整数": ["Enter an integer from 1 to 255", "1 ile 255 arasında tam sayı girin"],
	"设置会保留到重启后；默认关闭。": ["Settings persist after reboot; disabled by default.", "Ayarlar yeniden başlatmada korunur; varsayılan kapalıdır."],
	"操作失败：%s": ["Failed: %s", "Başarısız: %s"],
	"管理连接已断开": ["Management connection lost", "Yönetim bağlantısı kesildi"],
	"无法读取 TTL 设置": ["Cannot read TTL settings", "TTL ayarı okunamadı"],
	"无法应用 TTL 设置": ["Cannot apply TTL settings", "TTL ayarı uygulanamadı"],
	"TTL 无效": ["Invalid TTL", "Geçersiz TTL"],
	"设置蜂窝出口数据包的 TTL 和 IPv6 跳数限制；仅影响蜂窝网络，不改变 Wi-Fi 或 USB 局域网。": ["Set the TTL and IPv6 hop limit of cellular outbound packets. Local Wi-Fi and USB traffic are unchanged.", "Hücresel çıkışın TTL ve IPv6 atlama sınırını ayarlar. Yerel Wi-Fi ve USB trafiğini değiştirmez."],
	"启用后会关闭防火墙流量卸载，可能降低转发性能；关闭 TTL 后恢复由本功能修改的卸载设置。": ["Enabling TTL disables firewall flow offloading and may reduce forwarding performance. Turning TTL off restores offloading settings changed by this feature.", "TTL etkinleştirilince akış hızlandırması kapatılır; performans düşebilir. Kapatıldığında bu özelliğin değiştirdiği ayarlar geri yüklenir."],

    "自动模式兼容性": ["Automatic mode compatibility", "Otomatik mod uyumluluğu"],
    "机型默认": ["Model default", "Model varsayılanı"],
    "全制式自动（134，含 2G）": ["All-RAT auto (134, includes 2G)", "Tüm teknolojiler otomatik (134, 2G dahil)"],
    "自动选网（152，不含 2G）": ["Auto selection (152, excludes 2G)", "Otomatik seçim (152, 2G hariç)"],
    "134：5G／4G／3G／2G 自动，可回落 2G；152：5G／4G／3G 自动，不使用 2G。编号表示制式组合，不代表固定的频段优先级。": ["134: automatic 5G/4G/3G/2G, with 2G fallback. 152: automatic 5G/4G/3G, without 2G. The numbers identify radio technology combinations, not fixed frequency-band priorities.", "134: 2G'ye geri dönüş dahil otomatik 5G/4G/3G/2G. 152: 2G olmadan otomatik 5G/4G/3G. Numaralar sabit frekans bandı önceliklerini değil, radyo teknolojisi birleşimlerini belirtir."],
    "保存并应用": ["Save and apply", "Kaydet ve uygula"],
    "此项可能影响双卡延迟和自动选网偏好，例如联通 n78／n1 的驻留选择；实际效果取决于基带和网络，不保证驻留指定频段。延迟高、网络卡顿或驻留不理想时可尝试调整，应用后会重新连接网络。": ["This option may affect dual-SIM latency and automatic network selection preferences, such as camping on China Unicom n78 or n1. Results depend on modem firmware and the network; a specific band is not guaranteed. Try adjusting it for high latency, stalls or an undesired serving band. Applying it reconnects the cellular network.", "Bu seçenek çift SIM gecikmesini ve otomatik şebeke seçimi tercihlerini, örneğin China Unicom n78 veya n1 bandına bağlanmayı etkileyebilir. Sonuç modem yazılımına ve şebekeye bağlıdır; belirli bir bant garanti edilmez. Yüksek gecikme, takılma veya istenmeyen bant seçimi durumunda değiştirmeyi deneyin. Uygulama hücresel ağı yeniden bağlar."],
    "兼容性选项由两张 SIM 共用，本次应用到当前查看的 SIM；显式 4G／SA／NSA 模式不受影响。": ["Both SIMs share this compatibility option; this operation applies to the SIM being viewed. Explicit 4G/SA/NSA modes are unaffected.", "Bu uyumluluk seçeneği iki SIM için ortaktır; işlem görüntülenen SIM için uygulanır. Açıkça seçilen 4G/SA/NSA modları etkilenmez."],
    "正在读取兼容性设置…": ["Reading compatibility settings…", "Uyumluluk ayarları okunuyor…"],
    "无法读取兼容性设置，请刷新页面后重试": ["Unable to read compatibility settings; reload the page and try again", "Uyumluluk ayarları okunamadı; sayfayı yenileyip tekrar deneyin"],
    "保存兼容性设置失败，未执行网络模式应用": ["Could not save compatibility settings; the network mode was not applied", "Uyumluluk ayarları kaydedilemedi; şebeke modu uygulanmadı"],
    '恢复核心初始状态': ['Restore initial core states', 'İlk çekirdek durumlarını geri yükle'],
    'CPU0 保持在线': ['CPU0 stays online', 'CPU0 açık kalır'],
    "CPU0 深度休眠": ["CPU0 deep idle", "CPU0 derin boşta bekleme"],
    "允许 CPU0 深度休眠": ["Allow CPU0 deep idle", "CPU0 derin boşta beklemesine izin ver"],
    "开启有利于空闲省电；关闭优先低延迟。仅改变 CPU0 深层空闲状态，WFI 和其他核心保持原设置。": ["Enable to favor idle power savings; disable to favor low latency. Only CPU0 deep states change; WFI and other cores keep their settings.", "Boştayken enerji tasarrufu için açın; düşük gecikme için kapatın. Yalnızca CPU0 derin durumları değişir; WFI ve diğer çekirdek ayarları korunur."],
    "设置即时生效并在开机后保留。": ["Changes apply immediately and persist across reboots.", "Ayarlar hemen uygulanır ve yeniden başlatmalarda korunur."],
    "设置已应用并保存，重启后继续生效。": ["Settings applied and saved for future boots.", "Ayarlar uygulandı ve sonraki açılışlar için kaydedildi."],
    "无法读取 CPU0 深度休眠状态": ["Cannot read CPU0 deep idle state", "CPU0 derin boşta bekleme durumu okunamıyor"],
    "CPU0 深度休眠设置失败": ["CPU0 deep idle change failed", "CPU0 derin boşta bekleme ayarı başarısız"],
    "CPU 核心开关": ["CPU core controls", "CPU çekirdek denetimleri"],
    "按内核能力逐核开启或关闭。CPU0 保持在线；关闭核心会降低并行处理能力，是否省电取决于实际负载。": ["Enable or disable individual cores supported by the kernel. CPU0 stays online. Disabling cores reduces parallel capacity; power savings depend on the workload.", "Çekirdek desteğine göre CPU çekirdeklerini açın veya kapatın. CPU0 açık kalır. Çekirdek kapatmak paralel kapasiteyi azaltır; enerji tasarrufu yüke bağlıdır."],
    "仅保存核心开关；不改变调频、调压和温控配置。": ["Save core states only; frequency, voltage and thermal settings are unchanged.", "Yalnızca çekirdek durumlarını kaydeder; frekans, voltaj ve sıcaklık ayarları değişmez."],
    "应用核心设置": ["Apply core settings", "Çekirdek ayarlarını uygula"],
    "正在读取核心状态…": ["Reading core states…", "Çekirdek durumları okunuyor…"],
    "核心设置已应用；调频与温控页面将在下次打开时刷新。": ["Core settings applied. Reopen CPU settings to refresh frequency and thermal controls.", "Çekirdek ayarları uygulandı. Frekans ve sıcaklık kontrollerini yenilemek için CPU ayarlarını yeniden açın."],
    "此核心由内核管理，不能关闭": ["This core is managed by the kernel and cannot be disabled", "Bu çekirdek sistem tarafından yönetiliyor ve kapatılamaz"],
    "CPU 核心配置无效": ["Invalid CPU core settings", "Geçersiz CPU çekirdek ayarları"],
    "CPU 核心列表已变化，请重新载入": ["The CPU core list changed; reload the page", "CPU çekirdek listesi değişti; sayfayı yenileyin"],
    "无法保存核心设置": ["Unable to save core settings", "Çekirdek ayarları kaydedilemedi"],
    "内核未接受核心开关设置": ["The kernel rejected the core state change", "Sistem çekirdek durumu değişikliğini reddetti"],
    "核心设置正忙，请稍后重试": ["Core settings are busy; try again shortly", "Çekirdek ayarları meşgul; biraz sonra yeniden deneyin"],
    "无法读取 CPU 核心状态": ["Unable to read CPU core states", "CPU çekirdek durumları okunamadı"],
    "回滚未完成，请检查实际核心状态": ["Rollback was incomplete; check the actual core states", "Geri alma tamamlanamadı; gerçek çekirdek durumlarını kontrol edin"],
    "离线": ["Offline", "Çevrimdışı"],
    'V50 物理卡选择': ['V50 physical SIM selection', 'V50 fiziksel SIM seçimi'],
    '2内置卡': ['2 built-in SIMs', '2 dahili SIM'],
    '2内置卡 + 1插拔卡': ['2 built-in SIMs + 1 removable SIM', '2 dahili SIM + 1 çıkarılabilir SIM'],
    '3内置卡': ['3 built-in SIMs', '3 dahili SIM'],
    '3内置卡 + 1插拔卡': ['3 built-in SIMs + 1 removable SIM', '3 dahili SIM + 1 çıkarılabilir SIM'],
    '内置 SIM': ['Built-in SIM', 'Dahili SIM'],
    '外置 SIM': ['External SIM', 'Harici SIM'],
    '内置 SIM 3 与内置 SIM 2 共用逻辑卡槽 SIM2；外置 SIM 与内置 SIM 1 共用 SIM1。射频、锁定及 AT 查看仍按两个逻辑卡槽控制。': ['Built-in SIM 3 shares logical SIM2 with built-in SIM 2; the external SIM shares SIM1 with built-in SIM 1. Radio, locks and AT views still use the two logical slots.', 'Dahili SIM 3 ve dahili SIM 2 mantıksal SIM2 yuvasını; harici SIM ve dahili SIM 1 ise SIM1 yuvasını paylaşır. Radyo, kilitler ve AT görünümü iki mantıksal yuvayı kullanır.'],
    '无法识别 V50 硬件版本，未更改选卡': ['Unknown V50 board version; SIM selection unchanged', 'V50 kart sürümü bilinmiyor; SIM seçimi değiştirilmedi'],
    '此功能仅适用于 V50': ['This feature is only available on V50', 'Bu özellik yalnızca V50 içindir'],
    '此硬件版本没有所选 SIM': ['The selected SIM is not fitted on this board', 'Seçilen SIM bu kartta bulunmuyor'],
    'SIM 选择驱动尚未就绪，请更新内核并重启': ['SIM selector is not ready; update the kernel and reboot', 'SIM seçici hazır değil; çekirdeği güncelleyip yeniden başlatın'],
    'SIM 电源切换未完成': ['SIM power transition failed', 'SIM güç geçişi başarısız'],
    'SIM 硬件选择失败': ['Hardware SIM selection failed', 'Donanım SIM seçimi başarısız'],
	"等待切回受电后恢复充电": ["Charging will resume when the port returns to power-sink mode", "Bağlantı noktası güç alıcı moduna dönünce şarj devam edecek"],
	"保存并应用": ["Save & apply","Kaydet ve uygula"],
	"充电管理": ["Charging controls","Şarj denetimi"],
	"当前电量": ["Battery level","Pil seviyesi"],
	"电池电流": ["Battery current", "Pil akımı"],
	"正数充电，负数放电；显示电池净电流，不是 USB 输入电流。": ["Positive: charging; negative: discharging. Net battery current, not USB input current.", "Pozitif: şarj; negatif: deşarj. USB giriş akımı değil, net pil akımıdır."],
	"充电": ["Charging", "Şarj"],
	"放电": ["Discharging", "Deşarj"],
	"USB供电": ["USB power", "USB gücü"],
	"电池供电": ["Battery power", "Pil gücü"],
	"供电来源未知": ["Unknown power source", "Bilinmeyen güç kaynağı"],
	"功率和电流均为电池净值，不是 USB 输入值；充放电方向按电池电流判断。": ["Power and current are net battery values, not USB input. Direction follows battery current.", "Güç ve akım USB girişini değil, pilin net değerlerini gösterir. Yön pil akımına göre belirlenir."],
	"充电状态": ["Charging status","Şarj durumu"],
	"正在读取…": ["Reading…","Okunuyor…"],
	"启用充电上限": ["Enable charge limit","Şarj sınırını etkinleştir"],
	"停止充电于（50–100%）": ["Stop charging at (50–100%)","Şarjı durdur (%50–100)"],
	"恢复充电于（20–95%）": ["Resume charging at (20–95%)","Şarjı sürdür (%20–95)"],
	"临时充满一次": ["Charge to full once","Bir kez tam şarj et"],
	"正在临时充满…": ["Charging to full once…","Bir kez tam şarj ediliyor…"],
	"临时充满后，自动恢复原来的充电上限。": ["The original charge limit resumes automatically after reaching full charge.","Tam şarjdan sonra önceki şarj sınırı otomatik geri yüklenir."],
	"充电阈值必须是范围内的整数": ["Charge thresholds must be whole numbers within range","Şarj eşikleri belirtilen aralıkta tam sayı olmalıdır"],
	"恢复充电阈值必须低于停止阈值": ["The resume threshold must be lower than the stop threshold","Devam eşiği durdurma eşiğinden düşük olmalıdır"],
	"当前设备的充电控制暂不可用": ["Charging controls are currently unavailable on this device","Bu cihazda şarj denetimi şu anda kullanılamıyor"],
	"充电设置保存失败": ["Failed to save charging settings","Şarj ayarları kaydedilemedi"],
	"充电设置已保存，但服务应用失败，请重试": ["Charging settings were saved, but the service could not apply them. Please retry.","Şarj ayarları kaydedildi ancak hizmet uygulayamadı. Yeniden deneyin."],
	"充电设置正在应用，请稍后重试": ["Charging settings are being applied. Please try again shortly.","Şarj ayarları uygulanıyor. Biraz sonra yeniden deneyin."],
	"充电控制读取失败，请刷新重试": ["Failed to read charging controls. Refresh to retry.","Şarj denetimi okunamadı. Yenileyerek tekrar deneyin."],
	"已按上限暂停充电": ["Paused at charge limit","Şarj sınırında duraklatıldı"],
	"已充满": ["Fully charged","Tam şarj oldu"],
	"正在充电": ["Charging","Şarj ediliyor"],
	"使用电池": ["On battery","Pille çalışıyor"],
	"未充电": ["Not charging","Şarj olmuyor"],
	"正在应用充电设置…": ["Applying charging settings…","Şarj ayarları uygulanıyor…"],
	"到达上限后暂停充电，降至下限时恢复。关闭上限会恢复正常充电。设置在重启后保留。": ["Charging pauses at the upper limit and resumes at the lower limit. Disabling the limit restores normal charging. Settings survive reboot.","Üst sınırda şarj durur, alt sınırda devam eder. Sınır kapatılınca normal şarja dönülür. Ayarlar yeniden başlatmada korunur."],
	"暂停电池充电时保留 USB 供电，不会关闭 USB 数据或 OTG。": ["Pausing battery charging keeps USB power, data and OTG available.","Pil şarjını duraklatmak USB gücünü, veriyi veya OTG işlevini kapatmaz."],
	"暂停电池充电": ["Pause battery charging","Pil şarjını duraklat"],
	"已暂停电池充电": ["Battery charging paused","Pil şarjı duraklatıldı"],
	"恢复自动充电": ["Resume automatic charging","Otomatik şarja dön"],
	"充电设置已应用，重启后保留": ["Charging settings applied and saved across reboots","Şarj ayarları uygulandı ve yeniden başlatma için kaydedildi"],
	"上电自启动": ["Start automatically when power is connected","Güç bağlandığında otomatik başlat"],
	"开启后，插入电源直接启动 OpenWrt；关闭后仅充电，长按电源键启动。设置在下次充电启动时生效。": ["When enabled, connecting power starts OpenWrt. When disabled, charge only; hold the power button to start. Applies on the next charger boot.","Etkinse güç bağlandığında OpenWrt başlar. Kapalıysa yalnızca şarj olur; başlatmak için güç düğmesini basılı tutun. Bir sonraki şarj açılışında uygulanır."],
	"充电设置已保存，但驱动应用失败，请重试": ["Settings saved, but the charger rejected the change; retry","Ayarlar kaydedildi ancak şarj sürücüsü değişikliği uygulayamadı; yeniden deneyin"],
	"充电状态保存失败，请重试": ["Unable to save charging state; retry","Şarj durumu kaydedilemedi; yeniden deneyin"],
    '切换': ['Switch', 'Geçiş yap'],
    '编辑': ['Edit', 'Düzenle'],
    '接入点 APN': ['Access points (APN)', 'Erişim noktaları (APN)'],
    '无法读取当前 SIM 身份，请稍后重试': ['Cannot read the current SIM identity; try again later', 'Geçerli SIM kimliği okunamadı; daha sonra tekrar deneyin'],
    '此接入点仍被 SIM 使用，不能删除': ['This profile is still assigned to a SIM and cannot be deleted', 'Bu profil hâlâ bir SIM’e atanmış; silinemez'],
    '当前 SIM 已记住接入点，请使用按卡应用': ['This SIM has a saved profile; apply it per SIM', 'Bu SIM’in kayıtlı profili var; SIM’e özel uygulayın'],
    '无鉴权': ['No authentication', 'Kimlik doğrulama yok'],
    '用户名': ['Username', 'Kullanıcı adı'],
    '密码': ['Password', 'Parola'],
    '正在连接': ['Connecting', 'Bağlanıyor'],
    '连接失败': ['Connection failed', 'Bağlantı başarısız'],
    '鉴权方式': ['Authentication', 'Kimlik doğrulama'],
    '留空保留原密码': ['Leave empty to keep the saved password', 'Kayıtlı parolayı korumak için boş bırakın'],
    '未设置密码': ['No password set', 'Parola ayarlanmadı'],
    '清除已保存密码': ['Clear saved password', 'Kayıtlı parolayı sil'],
    '漫游 IP 协议': ['Roaming IP protocol', 'Dolaşım IP protokolü'],
    '配置初始附着 APN（高级）': ['Configure initial attach APN (advanced)', 'İlk bağlantı APN’sini yapılandır (gelişmiş)'],
    '初始附着默认关闭；仅在运营商需要时启用。不修改 IMS 配置。': ['Initial attach configuration is off by default; enable only if required by your carrier. IMS settings are not changed.', 'İlk bağlantı yapılandırması varsayılan olarak kapalıdır; yalnızca operatör gerektiriyorsa açın. IMS ayarları değiştirilmez.'],
    '按当前 SIM 记住选择': ['Remember selection for this SIM', 'Seçimi bu SIM için hatırla'],
    '使用全局接口配置': ['Using global interface settings', 'Genel arayüz ayarları kullanılıyor'],
    '自动匹配当前 SIM': ['Auto-match current SIM', 'Geçerli SIM’i otomatik eşleştir'],
    '按运营商匹配公网 APN；未知运营商使用 SIM 默认。专网或物联卡请手动设置。': ['Match a public APN by carrier; unknown carriers use the SIM default. Configure private networks and IoT SIMs manually.', 'Operatöre göre genel APN eşleştirilir; bilinmeyen operatörlerde SIM varsayılanı kullanılır. Özel ağları ve IoT SIM’leri elle yapılandırın.'],
    '当前蜂窝接口不支持 APN 管理': ['The current cellular interface does not support APN management', 'Geçerli hücresel arayüz APN yönetimini desteklemiyor'],
    '接入点参数无效': ['Invalid access point settings', 'Geçersiz erişim noktası ayarları'],
    '接入点已不存在，请刷新': ['Access point no longer exists; refresh the list', 'Erişim noktası artık mevcut değil; listeyi yenileyin'],
    '网络或 APN 配置有未应用更改，请先保存或撤销': ['Network or APN settings have pending changes; save or discard them first', 'Ağ veya APN ayarlarında bekleyen değişiklikler var; önce kaydedin veya geri alın'],
    '接入点保存失败': ['Failed to save the access point', 'Erişim noktası kaydedilemedi'],
    '最多保存 32 个接入点': ['You can save up to 32 access points', 'En fazla 32 erişim noktası kaydedebilirsiniz'],
    '相同 APN 和协议的接入点已存在': ['An access point with this APN and protocol already exists', 'Bu APN ve protokole sahip bir erişim noktası zaten var'],
    '不能删除当前接入点，请先切换': ['Switch to another access point before deleting the current one', 'Geçerli erişim noktasını silmeden önce başka birine geçin'],
    '配置已保存，但应用失败，请刷新后重试': ['Settings saved, but could not be applied; refresh and try again', 'Ayarlar kaydedildi ancak uygulanamadı; yenileyip tekrar deneyin'],
    '正在应用…': ['Applying…', 'Uygulanıyor…'],
    '接入点已应用，正在连接；能否上网取决于 SIM 和运营商': ['APN applied; connecting. Internet access depends on your SIM and carrier', 'APN uygulandı; bağlanılıyor. İnternet erişimi SIM ve operatörünüze bağlıdır'],
    '接入点已保存': ['Access point saved', 'Erişim noktası kaydedildi'],
    '留空使用 SIM 默认接入点': ['Leave empty to use the SIM default APN', 'SIM varsayılan APN değerini kullanmak için boş bırakın'],
    '接入点名称': ['Profile name', 'Profil adı'],
    'IP 协议': ['IP protocol', 'IP protokolü'],
    '编辑接入点': ['Edit access point', 'Erişim noktasını düzenle'],
    '添加接入点': ['Add access point', 'Erişim noktası ekle'],
    '保存仅修改列表，点击切换后才会应用。': ['Saving only updates the list. Switch to a profile to apply it.', 'Kaydetmek yalnızca listeyi günceller. Uygulamak için profile geçin.'],
    '当前配置': ['Current configuration', 'Geçerli yapılandırma'],
    'SIM 默认': ['SIM default', 'SIM varsayılanı'],
    '接入点应用于当前上网接口，不切换 SIM。切换会短暂中断蜂窝网络，重启后仍保留。': ['Profiles apply to the current data interface without switching SIMs. Switching briefly interrupts cellular data and persists across reboots.', 'Profiller SIM değiştirmeden geçerli veri arayüzüne uygulanır. Geçiş hücresel veriyi kısa süreli keser ve yeniden başlatmalarda korunur.'],
    '暂无接入点，请添加': ['No access points yet. Add one to get started.', 'Henüz erişim noktası yok. Başlamak için bir tane ekleyin.'],
    '再次应用': ['Apply again', 'Yeniden uygula'],
    '切换接入点？': ['Switch access point?', 'Erişim noktası değiştirilsin mi?'],
    '将重新建立蜂窝连接，USB 和 Wi-Fi 不受影响。': ['The cellular connection will be re-established. USB and Wi-Fi are unaffected.', 'Hücresel bağlantı yeniden kurulacak. USB ve Wi-Fi etkilenmez.'],
    '删除接入点？': ['Delete access point?', 'Erişim noktası silinsin mi?'],
	'清除全部蜂窝锁定': ['Clear all cellular locks', 'Tüm hücresel kilitleri temizle'],
	'清除当前 SIM 全部锁定': ['Clear all locks for the selected SIM', 'Seçili SIM için tüm kilitleri temizle'],
	'清除 SIM %s 的全部锁定？': ['Clear all locks for SIM %s?', 'SIM %s için tüm kilitler temizlensin mi?'],
	'确认清除': ['Confirm clearing', 'Temizlemeyi onayla'],
	'按当前查看 SIM 恢复自动模式和频段、解除小区锁定并开启 EN-DC；只删除当前卡的已保存锁定，保留两张卡的开机自动应用开关。': ['Restore automatic mode and bands, remove cell locks and enable EN-DC through the viewed SIM. Only its saved locks are deleted; both SIMs retain their apply-at-startup preferences.', 'Görüntülenen SIM üzerinden otomatik mod ve bantları geri yükle, hücre kilitlerini kaldır ve EN-DC’yi aç. Yalnızca bu SIM’in kayıtlı kilitleri silinir; her iki SIM’in açılışta uygulama tercihleri korunur.'],
	'注意：部分模组的频段或小区锁定由双卡共享（F50 的 NR 频段已确认共享），清除也会影响另一张卡。另一张卡的保存配置不会删除，其开机回放可能再次改变共享设置。清除会短暂中断蜂窝连接；频段以指令确认成功为准，其他设置回读通过后才删除当前卡的保存项。': ['Note: some modems share band or cell locks between SIMs (NR bands are confirmed shared on F50), so clearing also affects the other SIM. Its saved settings remain and may change shared settings again at boot. Cellular service briefly disconnects; band commands require acknowledgement; the selected SIM’s saved locks are deleted after the other settings pass readback.', 'Not: bazı modemlerde bant veya hücre kilitleri SIM’ler arasında ortaktır (F50’de NR bantlarının ortak olduğu doğrulandı); temizleme diğer SIM’i de etkiler. Diğer SIM’in kayıtlı ayarları korunur ve açılışta ortak ayarları yeniden değiştirebilir. Hücresel bağlantı kısa süre kesilir; bant komutlarının onayı gerekir; yalnızca seçili SIM’in kayıtlı kilitleri diğer ayarların geri okumaları doğrulanınca silinir.'],
	'恢复自动模式和频段、解除 LTE/NR 小区锁定并开启 EN-DC。双卡共享的模组设置也会影响另一张卡（F50 的 NR 频段共享）。只清除当前卡的保存项，保留另一张卡配置和开机自动应用开关；另一张卡的回放可能重新改变共享设置。蜂窝会短暂断开；频段不要求回读一致，其他设置仍需回读确认。': ['Restore automatic mode and bands, remove LTE/NR cell locks and enable EN-DC. Shared modem settings also affect the other SIM (F50 shares NR bands). Delete only the selected SIM’s saved locks; keep the other SIM’s settings and startup preferences. Its replay may change shared settings again. Cellular service briefly disconnects; band masks need not match readback; other settings still require confirmation.', 'Otomatik mod ve bantları geri yükle, LTE/NR hücre kilitlerini kaldır ve EN-DC’yi aç. Ortak modem ayarları diğer SIM’i de etkiler (F50’de NR bantları ortaktır). Yalnızca seçili SIM’in kayıtlı kilitlerini sil; diğer SIM’in ayarlarını ve açılış tercihlerini koru. Diğer SIM’in ayarları yeniden uygulanınca ortak ayarlar değişebilir. Hücresel bağlantı kısa süre kesilir; bant maskelerinin geri okumayla eşleşmesi gerekmez; diğer ayarlar yine doğrulanır.'],
	'查看卡槽已改变，请重新确认操作': ['The viewed SIM slot changed; confirm the operation again', 'Görüntülenen SIM yuvası değişti; işlemi yeniden onaylayın'],
	'当前 SIM 的全部锁定已清除，已核对并清理保存配置': ['All locks for the selected SIM were cleared, verified, and removed from saved settings', 'Seçili SIM’in tüm kilitleri temizlendi, doğrulandı ve kayıtlı ayarlardan kaldırıldı'],
	'清除未完成，部分设置可能已生效；已保存锁定未删除，请刷新后重试': ['Clearing was incomplete; some settings may have changed. Saved locks were not deleted. Refresh and retry.', 'Temizleme tamamlanmadı; bazı ayarlar değişmiş olabilir. Kayıtlı kilitler silinmedi. Yenileyip tekrar deneyin.'],
	'清除回读不一致，部分设置可能已生效；已保存锁定未删除，请刷新后重试': ['Reset readback did not match; some settings may have changed. Saved locks were not deleted. Refresh and retry.', 'Sıfırlama geri okuması eşleşmedi; bazı ayarlar değişmiş olabilir. Kayıtlı kilitler silinmedi. Yenileyip tekrar deneyin.'],
	'模组已恢复自动，但清除结果保存失败；旧锁定仍可能在开机回放，请重试': ['The modem is automatic, but saving the reset failed. Old locks may still be reapplied at boot; retry.', 'Modem otomatik moda döndü ancak sıfırlama kaydedilemedi. Eski kilitler açılışta yeniden uygulanabilir; tekrar deneyin.'],
	'模组已恢复自动，旧锁定回放已阻止，但保存文件清理失败，请重试': ['The modem is automatic and replay of old locks is blocked, but saved-file cleanup failed. Retry.', 'Modem otomatik moda döndü ve eski kilitlerin uygulanması engellendi ancak kayıtlı dosyalar temizlenemedi. Tekrar deneyin.'],
	'上次清除的保存文件尚未清理完成，请检查存储后重试': ['The previous reset still needs saved-file cleanup. Check storage and retry.', 'Önceki sıfırlamanın kayıtlı dosyaları henüz temizlenmedi. Depolamayı kontrol edip tekrar deneyin.'],
	'开机角色策略': ['Boot role policy', 'Açılış rolü ilkesi'],
	'当前共享协议': ['Current tethering protocol', 'Geçerli paylaşım protokolü'],
	'下次启动协议': ['Protocol on next boot', 'Sonraki açılış protokolü'],
	'主机模式不使用共享协议': ['No tethering protocol in host mode', 'Ana makine modunda paylaşım protokolü kullanılmaz'],
	'多个共享协议': ['Multiple tethering protocols', 'Birden çok paylaşım protokolü'],
	'未能确认': ['Not confirmed', 'Doğrulanamadı'],
	'设备模式（默认）': ['Device mode (default)', 'Cihaz modu (varsayılan)'],
	'USB 状态读取失败，请刷新重试': ['Could not read USB status; refresh to retry', 'USB durumu okunamadı; yenileyip tekrar deneyin'],
	'USB 角色后台切换失败，请重试': ['The background USB role switch failed; please retry', 'Arka planda USB rolü değiştirilemedi; tekrar deneyin'],
	'启动协议文件与保存设置不一致，请重新保存': ['Boot protocol file differs from saved settings; save again', 'Açılış protokolü dosyası kayıtlı ayarlardan farklı; tekrar kaydedin'],
	'设置已保存，当前协议不变，重启后生效': ['Settings saved; the current protocol is unchanged until reboot', 'Ayarlar kaydedildi; geçerli protokol yeniden başlatılana kadar değişmez'],
	'仅保存选择，未启用开机应用': ['Selection saved only; boot application is not enabled', 'Yalnızca seçim kaydedildi; açılışta uygulama etkin değil'],
	'请求结果未能确认，请刷新后检查，勿重复提交': ['The result could not be confirmed; refresh to check before resubmitting', 'Sonuç doğrulanamadı; tekrar göndermeden önce yenileyip kontrol edin'],
	'读取 USB 网卡失败，请刷新重试': ['Could not read USB adapters; refresh to retry', 'USB ağ bağdaştırıcıları okunamadı; yenileyip tekrar deneyin'],
	'无法记录 USB 角色切换结果': ['Could not record the USB role switch result', 'USB rolü değiştirme sonucu kaydedilemedi'],
	'热点连接可能已中断，请重新连接后确认状态': ['The hotspot connection may have closed; reconnect to check its state', 'Erişim noktası bağlantısı kesilmiş olabilir; durumu kontrol etmek için yeniden bağlanın'],
	'热点 AP 配置节': ['Hotspot AP configuration section', 'Erişim noktası yapılandırma bölümü'],
	'留空时匹配 Wi-Fi 网卡；只有一个 AP 时自动选择。多个 AP 无法唯一匹配时必须指定无线配置节名称。': ['Leave empty to match the Wi-Fi interface, or select the only AP. If multiple APs cannot be matched uniquely, specify the wireless section name.', 'Wi-Fi arayüzünü eşleştirmek veya tek AP’yi seçmek için boş bırakın. Birden çok AP benzersiz eşleştirilemiyorsa kablosuz bölüm adını belirtin.'],
	'热点配置节不存在或不是 AP': ['The hotspot section is missing or is not an AP', 'Erişim noktası bölümü yok veya AP değil'],
	'存在多个热点，请在适配设置选择目标 AP': ['Multiple hotspots found; select the target AP in adapter settings', 'Birden çok erişim noktası bulundu; uyarlama ayarlarında hedef AP’yi seçin'],
	'没有配置热点': ['No hotspot is configured', 'Yapılandırılmış erişim noktası yok'],
	'热点所属无线电无效': ['The hotspot radio is invalid', 'Erişim noktasının radyosu geçersiz'],
	'热点开关参数无效': ['Invalid hotspot state', 'Geçersiz erişim noktası durumu'],
	'热点正在应用配置，请稍后重试': ['Hotspot changes are being applied; try again shortly', 'Erişim noktası ayarları uygulanıyor; biraz sonra tekrar deneyin'],
	'无线配置有未应用更改，请先保存或撤销': ['Wireless settings have pending edits; save or discard them first', 'Kablosuz ayarlarda bekleyen değişiklikler var; önce kaydedin veya iptal edin'],
	'启用此无线电会影响其他接口，请在无线页面处理': ['Enabling this radio affects other interfaces; use the Wireless page', 'Bu radyoyu açmak diğer arayüzleri etkiler; Kablosuz sayfasını kullanın'],
	'热点配置写入失败': ['Could not write hotspot settings', 'Erişim noktası ayarları yazılamadı'],
	'热点配置保存失败': ['Could not save hotspot settings', 'Erişim noktası ayarları kaydedilemedi'],
	'热点配置已保存，但应用失败，请重试': ['Hotspot settings saved, but applying failed; please retry', 'Erişim noktası ayarları kaydedildi ancak uygulanamadı; tekrar deneyin'],
	'热点配置应用失败': ['Could not apply hotspot settings', 'Erişim noktası ayarları uygulanamadı'],
	'热点配置已保存，正在应用': ['Hotspot settings saved; applying changes', 'Erişim noktası ayarları kaydedildi; uygulanıyor'],
	'清空流量记录': ['Clear traffic records', 'Trafik kayıtlarını temizle'],
	'清空所有流量记录？': ['Clear all traffic records?', 'Tüm trafik kayıtları temizlensin mi?'],
	'将清空今日、每月历史和套餐周期校准，并从当前网卡计数重新开始。已保存的套餐额度、结算日、计量方式和统计网卡保持不变。此操作不可撤销。': ['This clears today’s usage, monthly history and cycle calibration, then starts again from the current interface counters. Saved quotas, billing day, counting direction and interface stay unchanged. This cannot be undone.', 'Bugünkü kullanım, aylık geçmiş ve dönem kalibrasyonu silinir; mevcut arayüz sayaçlarından yeniden başlanır. Kayıtlı kotalar, fatura günü, sayım yönü ve arayüz değişmez. Bu işlem geri alınamaz.'],
	'正在清空流量记录…': ['Clearing traffic records…', 'Trafik kayıtları temizleniyor…'],
	'流量记录已清空': ['Traffic records cleared', 'Trafik kayıtları temizlendi'],
	'清空失败': ['Could not clear records', 'Kayıtlar temizlenemedi'],
	'请先确认清空操作': ['Please confirm before clearing records', 'Kayıtları temizlemeden önce onaylayın'],
	'仅清除本地统计，不会重置运营商账单，也不会断开网络。': ['Only local statistics are cleared. This does not reset carrier billing or disconnect the network.', 'Yalnızca yerel istatistikler silinir. Operatör faturası sıfırlanmaz ve ağ bağlantısı kesilmez.'],
	'已应用并核对模组状态': ['Applied and verified against the modem', 'Uygulandı ve modemden doğrulandı'],
	'开机自动应用设置已保存': ['Boot auto-apply preference saved', 'Açılışta otomatik uygulama tercihi kaydedildi'],
	'网络设置提交失败': ['Could not submit network settings', 'Ağ ayarları gönderilemedi'],
	'网络设置执行失败': ['Network settings failed', 'Ağ ayarları uygulanamadı'],
	'应用超时，请刷新确认模组状态': ['Operation timed out; refresh to check the actual modem state', 'İşlem zaman aşımına uğradı; modem durumunu denetlemek için yenileyin'],
	'无法查询操作结果，请刷新确认模组状态': ['Cannot query the result; refresh to check the actual modem state', 'Sonuç sorgulanamıyor; modem durumunu denetlemek için yenileyin'],
	'无效的操作状态': ['Invalid operation status', 'Geçersiz işlem durumu'],
	'页面已关闭': ['Page closed', 'Sayfa kapatıldı'],
	'操作结果已过期或不存在': ['Operation result expired or unavailable', 'İşlem sonucu süresi dolmuş veya mevcut değil'],
	'设置进程已中断，请刷新确认模组状态': ['Settings worker stopped; refresh to check the modem state', 'Ayar işlemi durdu; modem durumunu denetlemek için yenileyin'],
	'另一项网络设置仍在执行，请稍后重试': ['Another network setting is in progress; try again shortly', 'Başka bir ağ ayarı uygulanıyor; biraz sonra tekrar deneyin'],
	'无效的频段列表': ['Invalid band list', 'Geçersiz bant listesi'],
	'所选频段不受此模组支持': ['Selected bands are not supported by this modem', 'Seçilen bantlar bu modem tarafından desteklenmiyor'],
	'所选频段无法用当前模组指令编码': ['Selected bands cannot be encoded by this modem command', 'Seçilen bantlar bu modem komutuyla kodlanamıyor'],
	'无效的小区参数': ['Invalid cell parameters', 'Geçersiz hücre parametreleri'],
	'无效的网络设置': ['Invalid network setting', 'Geçersiz ağ ayarı'],
	'频段设置未确认，请重试': ['Band setting was not acknowledged; retry', 'Bant ayarı onaylanmadı; tekrar deneyin'],
	'模组拒绝了设置': ['The modem rejected the setting', 'Modem ayarı reddetti'],
	'设置保存失败': ['Could not save settings', 'Ayarlar kaydedilemedi'],
	'无法读取当前 SIM 模式，未发送设置': ['Cannot read the current SIM mode; no settings sent', 'Mevcut SIM modu okunamıyor; ayar gönderilmedi'],
	'无法读取当前小区锁定，未发送设置': ['Cannot read current cell locks; no settings sent', 'Mevcut hücre kilitleri okunamıyor; ayar gönderilmedi'],
	'模组回读与请求设置不一致，未保存': ['Modem readback does not match; setting not saved', 'Modem geri okuması eşleşmiyor; ayar kaydedilmedi'],
	'设置已写入，但射频未恢复，请检查模组状态': ['Setting written, but radio did not recover; check the modem', 'Ayar yazıldı ancak radyo geri gelmedi; modemi denetleyin'],
	'协议栈重启后设置不一致，未保存': ['Setting changed after the protocol restart; not saved', 'Protokol yeniden başlatılınca ayar değişti; kaydedilmedi'],
	'协议栈重启被拒绝，请检查模组状态': ['Protocol restart rejected; check the modem state', 'Protokolün yeniden başlatılması reddedildi; modem durumunu denetleyin'],
	'正在确认设置结果…': ['Verifying the setting…', 'Ayar doğrulanıyor…'],
	'等待模组能力数据': ['Waiting for modem capabilities', 'Modem yetenekleri bekleniyor'],
	'本机号码': ['Phone number', 'Telefon numarası'],
	'流量池': ['Data plan', 'Veri paketi'],
	'今日流量': ['Today’s data', 'Bugünkü veri'],
	'当月流量': ['This month’s data', 'Bu ayki veri'],
	'自然月统计': ['Calendar-month usage', 'Takvim ayı kullanımı'],
	'套餐周期已用': ['Used this billing cycle', 'Bu dönemde kullanılan'],
	'套餐剩余': ['Plan remaining', 'Pakette kalan'],
	'套餐与流量池': ['Plan and data allowance', 'Paket ve veri kotası'],
	'套餐名称': ['Plan name', 'Paket adı'],
	'每周期额度（GB）': ['Allowance per cycle (GB)', 'Dönem kotası (GB)'],
	'每日参考额度（GB）': ['Daily reference allowance (GB)', 'Günlük referans kotası (GB)'],
	'每月重置日（1–31）': ['Monthly reset day (1–31)', 'Aylık sıfırlama günü (1–31)'],
	'套餐计量方式': ['Plan accounting', 'Paket hesaplama yöntemi'],
	'上下行合计': ['Download + upload', 'İndirme + yükleme'],
	'仅下行': ['Download only', 'Yalnızca indirme'],
	'仅上行': ['Upload only', 'Yalnızca yükleme'],
	'额度为 0 表示不限量；1 GB = 1000 MB。重置日在设备当地时间零点生效，短月份使用当月最后一天。': ['Set 0 for unlimited; 1 GB = 1000 MB. Reset takes place at midnight in the device timezone; shorter months use their last day.', 'Sınırsız için 0 girin; 1 GB = 1000 MB. Sıfırlama cihazın yerel saatine göre gece yarısı yapılır; kısa aylarda ayın son günü kullanılır.'],
	'统计网卡': ['Accounting interface', 'İstatistik arayüzü'],
	'自动使用蜂窝 WAN 网卡': ['Use the cellular WAN interface automatically', 'Hücresel WAN arayüzünü otomatik kullan'],
	'校准本周期已用（GB，可选）': ['Adjust used data this cycle (GB, optional)', 'Bu dönem kullanılan veriyi düzelt (GB, isteğe bağlı)'],
	'留空保持现有统计': ['Leave blank to keep existing usage', 'Mevcut kullanımı korumak için boş bırakın'],
	'校准只调整当前套餐周期，不修改每日和自然月历史；下个周期自动恢复实际统计。': ['Adjustments affect only the current billing cycle, not daily or calendar-month history. The next cycle uses measured data again.', 'Düzeltme yalnızca mevcut paket dönemini etkiler; günlük ve takvim ayı geçmişi değişmez. Sonraki dönemde ölçülen veri kullanılır.'],
	'最近每日用量': ['Recent daily usage', 'Son günlük kullanımlar'],
	'每月用量历史': ['Monthly usage history', 'Aylık kullanım geçmişi'],
	'日期': ['Date', 'Tarih'],
	'月份': ['Month', 'Ay'],
	'下行': ['Download', 'İndirme'],
	'上行': ['Upload', 'Yükleme'],
	'合计': ['Total', 'Toplam'],
	'从启用统计时开始记录；统计约每 10 秒更新、每 60 秒保存。突然断电可能损失最近未保存的记录。': ['Recording starts when accounting is enabled. Usage updates about every 10 seconds and is saved every 60 seconds. Sudden power loss may lose recent unsaved usage.', 'Kayıt, istatistik hizmeti açıldığında başlar. Kullanım yaklaşık 10 saniyede güncellenir, 60 saniyede kaydedilir. Ani güç kesintisinde son kaydedilmemiş veriler kaybolabilir.'],
	'本地网卡统计供参考，计费以运营商为准。额度用于显示和提醒，不会自动断网。': ['Local interface statistics are an estimate; carrier billing takes precedence. Allowances display warnings and never disconnect the network.', 'Yerel arayüz istatistikleri tahminidir; operatörün hesabı esas alınır. Kotalar uyarı gösterir, ağı otomatik kesmez.'],
	'不限量': ['Unlimited', 'Sınırsız'],
	'未设置套餐名称': ['No plan name set', 'Paket adı ayarlanmadı'],
	'每周期额度：': ['Cycle allowance: ', 'Dönem kotası: '],
	'下次重置：': ['Next reset: ', 'Sonraki sıfırlama: '],
	'每日参考额度：': ['Daily reference allowance: ', 'Günlük referans kotası: '],
	'更新于：': ['Updated: ', 'Güncelleme: '],
	'流量统计服务暂不可用': ['Data accounting service is unavailable', 'Veri istatistik hizmeti kullanılamıyor'],
	'统计网卡暂不可用': ['Accounting interface is unavailable', 'İstatistik arayüzü kullanılamıyor'],
	'系统时间尚未同步': ['System clock is not synchronized yet', 'Sistem saati henüz eşitlenmedi'],
	'统计数据保存失败': ['Failed to save usage data', 'Kullanım verileri kaydedilemedi'],
	'已达到套餐额度': ['Plan allowance reached', 'Paket kotasına ulaşıldı'],
	'已达到每日参考额度': ['Daily reference allowance reached', 'Günlük referans kotasına ulaşıldı'],
	'暂无统计记录': ['No usage records yet', 'Henüz kullanım kaydı yok'],
	'校准本周期已用流量？': ['Adjust this cycle’s used data?', 'Bu dönemin kullanılan verisi düzeltilecek mi?'],
	'这会将当前套餐周期已用值调整为输入值，每日和自然月记录不变。': ['This sets current cycle usage to the entered value. Daily and calendar-month records stay unchanged.', 'Mevcut dönem kullanımı girilen değere ayarlanır. Günlük ve takvim ayı kayıtları değişmez.'],
	'正在保存流量池设置…': ['Saving data plan settings…', 'Veri paketi ayarları kaydediliyor…'],
	'短信转发': ['SMS forwarding', 'SMS yönlendirme'],
	'开启短信转发': ['Enable SMS forwarding', 'SMS yönlendirmeyi aç'],
	'转发已开启': ['Forwarding enabled', 'Yönlendirme açık'],
	'转发配置': ['Forwarding profiles', 'Yönlendirme profilleri'],
	'启用当前配置': ['Enable this profile', 'Bu profili etkinleştir'],
	'电源通知使用共用配置；请同时开启该配置并设置渠道。': ['Power notifications use the shared profile; enable that profile and configure its channel.', 'Güç bildirimleri ortak profili kullanır; bu profili etkinleştirip kanalını yapılandırın.'],
	'转发正在执行，请稍后再试': ['A delivery is running; please try again shortly', 'Bir gönderim sürüyor; lütfen biraz sonra tekrar deneyin'],
	'配置模式': ['Configuration mode', 'Yapılandırma modu'],
	'所有 SIM 共用': ['Shared by all SIMs', 'Tüm SIM kartları için ortak'],
	'按 SIM 独立配置': ['Separate profile per SIM', 'SIM başına ayrı profil'],
	'正在编辑': ['Editing profile', 'Düzenlenen profil'],
	'共用配置 / 电源通知': ['Shared profile / power notifications', 'Ortak profil / güç bildirimleri'],
	'独立模式按收到短信的卡选择渠道和黑名单，不受上网卡切换影响。电源通知始终使用共用配置。': ['Separate mode selects the channel and blacklist by the receiving SIM, not the data SIM. Power notifications always use the shared profile.', 'Ayrı modda kanal ve kara liste veri SIM kartına değil, SMS alan karta göre seçilir. Güç bildirimleri her zaman ortak profili kullanır.'],
	'当前配置用于新短信转发。': ['This profile handles new incoming SMS.', 'Bu profil yeni gelen SMS mesajlarını yönlendirir.'],
	'当前配置不用于短信；保存后仍保留，可单独测试。': ['This profile is inactive for SMS; its settings are retained and can be tested separately.', 'Bu profil SMS için etkin değil; ayarları korunur ve ayrı olarak test edilebilir.'],
	'电源通知请在共用配置中设置，独立模式下仍生效。': ['Configure power notifications in the shared profile; they remain active in separate mode.', 'Güç bildirimlerini ortak profilde ayarlayın; ayrı modda da etkin kalır.'],
	'填入推送预设': ['Fill from a push preset', 'Bildirim ön ayarını doldur'],
	'自定义 / 兼容旧版': ['Custom / legacy compatible', 'Özel / eski sürümle uyumlu'],
	'企业微信': ['WeCom', 'WeCom'],
	'钉钉 Webhook': ['DingTalk Webhook', 'DingTalk Webhook'],
	'预设只填入表单，请替换密钥后保存；需要钉钉加签时请选择“钉钉机器人”。': ['Presets only fill the form. Replace the keys and save; use DingTalk bot for signed requests.', 'Ön ayarlar yalnızca formu doldurur. Anahtarları değiştirip kaydedin; imzalı istekler için DingTalk botunu seçin.'],
	'请求方法': ['Request method', 'İstek yöntemi'],
	'请求超时（秒，1–120）': ['Request timeout (seconds, 1–120)', 'İstek zaman aşımı (saniye, 1–120)'],
	'正文格式': ['Body format', 'Gövde biçimi'],
	'正文模板': ['Body template', 'Gövde şablonu'],
	'纯文本': ['Plain text', 'Düz metin'],
	'占位符：{from}、{text}、{time}、{sim}、{device}、{kind}。JSON 占位符放在字符串内；按格式自动转义，绝不执行命令。': ['Placeholders: {from}, {text}, {time}, {sim}, {device}, {kind}. Put JSON placeholders inside strings; values are escaped for the format, never executed.', 'Yer tutucular: {from}, {text}, {time}, {sim}, {device}, {kind}. JSON yer tutucularını dizgelerin içine koyun; değerler biçime göre kaçışlanır, komut olarak çalıştırılmaz.'],
	'GET 只发送 URL 参数；JSON 模板留空时沿用旧版 from/text/date 格式。仅支持公网 HTTPS，验证证书且不跟随重定向。': ['GET sends URL parameters only. An empty JSON template preserves the legacy from/text/date format. Public HTTPS only, with certificate verification and no redirects.', 'GET yalnızca URL parametrelerini gönderir. Boş JSON şablonu eski from/text/date biçimini korur. Yalnızca genel HTTPS; sertifika doğrulanır, yönlendirmeler izlenmez.'],
	'附加请求头（每行 Name: value）': ['Extra headers (Name: value, one per line)', 'Ek başlıklar (satır başına Name: value)'],
	'请求超时必须为 1–120 秒': ['Request timeout must be 1–120 seconds', 'İstek zaman aşımı 1–120 saniye olmalıdır'],
	'正文模板必须是有效 JSON': ['Body template must be valid JSON', 'Gövde şablonu geçerli JSON olmalıdır'],
	'填入推送预设？': ['Fill from this push preset?', 'Bu bildirim ön ayarı doldurulsun mu?'],
	'将替换当前 Webhook 地址、正文及请求头，保存后才会生效。': ['This replaces the Webhook URL, body and headers in the form. Changes take effect only after saving.', 'Formdaki Webhook adresi, gövde ve başlıklar değiştirilir. Değişiklikler yalnızca kaydettikten sonra geçerli olur.'],
	'切换编辑配置？': ['Switch the profile being edited?', 'Düzenlenen profil değiştirilsin mi?'],
	'未保存的修改将被丢弃。': ['Unsaved changes will be discarded.', 'Kaydedilmemiş değişiklikler silinecek.'],
	'转发未开启': ['Forwarding disabled', 'Yönlendirme kapalı'],
	'转发方式': ['Forwarding method', 'Yönlendirme yöntemi'],
	'模板语言': ['Template language', 'Şablon dili'],
	'简体中文': ['Simplified Chinese', 'Basitleştirilmiş Çince'],
	'保存后用于转发标题、提示文字和电源通知；短信原文及设备备注保持不变。': ['After saving, this language is used for forwarding titles, labels, and power notifications. Original SMS text and device notes stay unchanged.', 'Kaydettikten sonra yönlendirme başlıkları, etiketler ve güç bildirimleri bu dilde gönderilir. SMS metni ve cihaz notları değiştirilmez.'],
	'钉钉机器人': ['DingTalk bot', 'DingTalk botu'],
	'本机短信': ['Device SMS', 'Cihaz SMS’i'],
	'邮件 SMTP': ['Email SMTP', 'E-posta SMTP'],
	'HTTPS 地址': ['HTTPS address', 'HTTPS adresi'],
	'以固定 JSON 格式发送，不执行自定义命令。': ['Sends a fixed JSON payload; no custom commands are run.', 'Sabit bir JSON gönderir; özel komut çalıştırılmaz.'],
	'机器人 Webhook 地址': ['Bot webhook URL', 'Bot webhook adresi'],
	'加签密钥 · 留空保留': ['Signing secret · leave blank to keep', 'İmza anahtarı · korumak için boş bırakın'],
	'清除加签密钥': ['Clear signing secret', 'İmza anahtarını sil'],
	'附带设备信息': ['Include device information', 'Cihaz bilgisini ekle'],
	'SMTP 服务器域名': ['SMTP server domain', 'SMTP sunucu alan adı'],
	'端口 · 465 / 587': ['Port · 465 / 587', 'Bağlantı noktası · 465 / 587'],
	'发件邮箱': ['Sender email', 'Gönderen e-posta'],
	'收件邮箱': ['Recipient email', 'Alıcı e-posta'],
	'授权码 / 密码 · 留空保留': ['App password / password · leave blank to keep', 'Uygulama şifresi / şifre · korumak için boş bırakın'],
	'已配置，留空保留': ['Configured; leave blank to keep', 'Yapılandırıldı; korumak için boş bırakın'],
	'请输入授权码': ['Enter an app password', 'Uygulama şifresini girin'],
	'可选': ['Optional', 'İsteğe bağlı'],
	'清除邮件配置': ['Clear email configuration', 'E-posta yapılandırmasını sil'],
	'使用邮箱提供的 SMTP 授权码；始终验证 TLS 证书。': ['Use your email provider’s SMTP app password; the TLS certificate is always verified.', 'E-posta sağlayıcınızın SMTP uygulama şifresini kullanın; TLS sertifikası her zaman doğrulanır.'],
	'短信接收号码 · 每行一个，最多 3 个': ['SMS recipients · one per line, up to 3', 'SMS alıcıları · satır başına bir, en fazla 3'],
	'经本机 SIM 转发，可能产生短信费用；每条最多 70 个 UCS-2 单元，不限制每日转发条数。': ['Forwarding uses the device SIM and may incur SMS charges; up to 70 UCS-2 units per message, with no daily forwarding limit.', 'Yönlendirme cihazın SIM kartını kullanır ve ücret doğurabilir; mesaj başına en fazla 70 UCS-2 birimi, günlük yönlendirme sınırı yoktur.'],
	'电源通知': ['Power notifications', 'Güç bildirimleri'],
	'电源状态通知': ['Power status notifications', 'Güç durumu bildirimleri'],
	'充电状态变化，或电量跨过 5%、20%、40%、60%、80%、100% 时通知。': ['Notify when charging status changes or battery level crosses 5%, 20%, 40%, 60%, 80%, or 100%.', 'Şarj durumu değiştiğinde veya pil seviyesi %5, %20, %40, %60, %80 ya da %100 eşiğini geçtiğinde bildir.'],
	'设备暂未提供有效电池状态': ['No valid battery status is available on this device', 'Bu cihazda geçerli pil durumu yok'],
	'设备备注': ['Device note', 'Cihaz notu'],
	'黑名单': ['Blocklists', 'Engelleme listeleri'],
	'号码黑名单 · 每行一个，最多 64 个': ['Number blocklist · one per line, up to 64', 'Numara engelleme listesi · satır başına bir, en fazla 64'],
	'关键词黑名单 · 每行一个，最多 32 个': ['Keyword blocklist · one per line, up to 32', 'Anahtar kelime engelleme listesi · satır başına bir, en fazla 32'],
	'命中的新短信只记为已处理，不会转发；清空规则后不会补发。规则不影响电源通知。': ['Matching new SMS are marked processed and not forwarded; clearing rules will not resend them. Power notifications are unaffected.', 'Eşleşen yeni SMS’ler işlenmiş sayılır ve yönlendirilmez; kurallar silinince tekrar gönderilmez. Güç bildirimleri etkilenmez.'],
	'测试与状态': ['Test and status', 'Test ve durum'],
	'设备独立执行，关闭页面仍生效；仅转发新短信，失败不自动重发。': ['Runs on the device even with this page closed; only new SMS are forwarded, and failed deliveries are not retried.', 'Sayfa kapalıyken de cihazda çalışır; yalnızca yeni SMS’ler yönlendirilir ve başarısız teslimatlar yeniden denenmez.'],
	'保存设置': ['Save settings', 'Ayarları kaydet'],
	'发送测试消息': ['Send test message', 'Test mesajı gönder'],
	'刷新状态': ['Refresh status', 'Durumu yenile'],
	'最近转发：': ['Last forwarding: ', 'Son yönlendirme: '],
	'最近投递记录': ['Recent deliveries', 'Son teslimatlar'],
	'CPU 设置': ['CPU settings', 'CPU ayarları'],
	"频率、电压、温控分别保存。频率和温控即时生效，电压重启后生效。": ["Frequency, voltage and thermal settings are saved separately. Frequency and thermal settings apply immediately; voltage applies after reboot.", "Frekans, voltaj ve sıcaklık ayarları ayrı kaydedilir. Frekans ve sıcaklık hemen, voltaj yeniden başlatmada uygulanır."],
	"仅控制本区域；未勾选时只应用本次，并取消本区域的开机设置。": ["Controls this section only. When unchecked, apply for this session and remove only this section’s startup settings.", "Yalnızca bu bölümü yönetir. İşaretli değilse bu oturum için uygular ve yalnızca bu bölümün açılış ayarlarını kaldırır."],
	"应用温控设置": ["Apply thermal settings", "Sıcaklık ayarlarını uygula"],
	"应用频率设置": ["Apply frequency settings", "Frekans ayarlarını uygula"],
	"应用温控设置？": ["Apply thermal settings?", "Sıcaklık ayarları uygulansın mı?"],
	"应用频率设置？": ["Apply frequency settings?", "Frekans ayarları uygulansın mı?"],
	"恢复温控启动基线？": ["Restore thermal boot baseline?", "Başlangıç sıcaklık ayarları geri yüklensin mi?"],
	"恢复频率启动基线？": ["Restore frequency boot baseline?", "Başlangıç frekans ayarları geri yüklensin mi?"],
	"仅恢复本次启动记录的温控阈值并取消温控开机应用；不修改频率或电压设置。": ["Restore only thermal thresholds recorded for this boot and disable thermal startup settings. Frequency and voltage settings are unchanged.", "Yalnızca bu açılışın sıcaklık eşiklerini geri yükler ve sıcaklık açılış ayarlarını kapatır. Frekans ve voltaj ayarları değişmez."],
	"仅恢复本次启动记录的频率并取消频率开机应用；不修改温控或电压设置。": ["Restore only frequencies recorded for this boot and disable frequency startup settings. Thermal and voltage settings are unchanged.", "Yalnızca bu açılışın frekanslarını geri yükler ve frekans açılış ayarlarını kapatır. Sıcaklık ve voltaj ayarları değişmez."],
	"温控阈值将立即应用；提高阈值可能增加温度。不修改频率或电压设置。": ["Thermal thresholds apply immediately; raising them may increase temperature. Frequency and voltage settings are unchanged.", "Sıcaklık eşikleri hemen uygulanır; yükseltmek sıcaklığı artırabilir. Frekans ve voltaj ayarları değişmez."],
	"降低频率可能降低吞吐量；提高最低频率可能增加功耗和温度。不修改温控或电压设置。": ["Lower frequencies may reduce throughput; higher minimum frequencies may increase power use and temperature. Thermal and voltage settings are unchanged.", "Düşük frekans verimi azaltabilir; yüksek alt frekans güç tüketimini ve sıcaklığı artırabilir. Sıcaklık ve voltaj ayarları değişmez."],
	"正在应用温控设置…": ["Applying thermal settings…", "Sıcaklık ayarları uygulanıyor…"],
	"正在应用频率设置…": ["Applying frequency settings…", "Frekans ayarları uygulanıyor…"],
	"温控设置已应用": ["Thermal settings applied", "Sıcaklık ayarları uygulandı"],
	"频率设置已应用": ["Frequency settings applied", "Frekans ayarları uygulandı"],
	"请刷新页面后分别应用频率或温控设置": ["Reload the page and apply frequency or thermal settings separately", "Sayfayı yenileyip frekans veya sıcaklık ayarlarını ayrı ayrı uygulayın"],
	"频率与温控即时应用；电压偏移独立保存，重启后生效。": ["Frequency and thermal settings apply immediately; voltage offsets are saved separately and apply after reboot.", "Frekans ve sıcaklık ayarları hemen uygulanır; voltaj ofsetleri ayrı kaydedilir ve yeniden başlatmada uygulanır."],
	"温控管理": ["Thermal management", "Sıcaklık yönetimi"],
	"按实际温区调整被动阈值；临界保护与回差只读，保留内核降频绑定。": ["Adjust passive thresholds for actual thermal zones. Critical protection and hysteresis are read-only; kernel cooling bindings are preserved.", "Gerçek sıcaklık bölgelerinin pasif eşiklerini ayarlayın. Kritik koruma ve histerezis salt okunurdur; çekirdek soğutma bağlantıları korunur."],
	"温区已禁用": ["Thermal zone disabled", "Sıcaklık bölgesi devre dışı"],
	"内核温控": ["Kernel thermal control", "Çekirdek sıcaklık denetimi"],
	"降频触发温度": ["Throttling threshold", "Frekans düşürme eşiği"],
	"被动温控起点": ["Passive monitoring threshold", "Pasif izleme eşiği"],
	"临界保护温度": ["Critical protection threshold", "Kritik koruma eşiği"],
	"高温保护温度": ["Hot protection threshold", "Yüksek sıcaklık koruma eşiği"],
	"只读阈值": ["Read-only threshold", "Salt okunur eşik"],
	"回差": ["Hysteresis", "Histerezis"],
	"只读": ["Read-only", "Salt okunur"],
	"降温等级（当前 / 最大）": ["Cooling state (current / maximum)", "Soğutma seviyesi (mevcut / en yüksek)"],
	"当前内核没有可写的 CPU 被动温控阈值，仅显示实际传感器。": ["This kernel exposes no writable passive CPU thermal thresholds; actual sensors are shown read-only.", "Bu çekirdekte yazılabilir pasif CPU sıcaklık eşiği yok; gerçek sensörler salt okunur gösterilir."],
	"温度传感器": ["Temperature sensors", "Sıcaklık sensörleri"],
	"提高阈值可能增加温度；此处不会关闭内核临界保护或设备紧急保护。": ["Raising thresholds may increase temperature. Kernel critical protection and device emergency protection are not disabled here.", "Eşikleri yükseltmek sıcaklığı artırabilir. Çekirdek kritik koruması ve cihaz acil koruması burada kapatılmaz."],
	"应用频率与温控": ["Apply frequency & thermal settings", "Frekans ve sıcaklık ayarlarını uygula"],
	"恢复启动基线": ["Restore boot baseline", "Başlangıç ayarlarını geri yükle"],
	"恢复启动基线？": ["Restore boot baseline?", "Başlangıç ayarları geri yüklensin mi?"],
	"恢复本次启动记录的频率与温控阈值，并取消开机自动应用；不修改电压。": ["Restore frequency and thermal thresholds recorded for this boot and disable automatic application at startup. Voltage is unchanged.", "Bu açılışta kaydedilen frekans ve sıcaklık eşiklerini geri yükle ve açılışta otomatik uygulamayı kapat. Voltaj değişmez."],
	"频率与温控阈值将立即应用。降低频率可能降低吞吐量；提高最低频率或温控阈值可能增加功耗和温度。": ["Frequency and thermal thresholds apply immediately. Lower frequencies may reduce throughput; higher minimum frequencies or thermal thresholds may increase power use and temperature.", "Frekans ve sıcaklık eşikleri hemen uygulanır. Düşük frekans verimi azaltabilir; yüksek alt frekans veya sıcaklık eşikleri güç tüketimini ve sıcaklığı artırabilir."],
	"温控配置无效": ["Invalid thermal settings", "Geçersiz sıcaklık ayarları"],
	"温区不存在或名称不唯一": ["Thermal zone is missing or its name is ambiguous", "Sıcaklık bölgesi yok veya adı benzersiz değil"],
	"只能调整可写的 CPU 被动温控阈值": ["Only writable passive CPU thermal thresholds can be adjusted", "Yalnızca yazılabilir pasif CPU sıcaklık eşikleri ayarlanabilir"],
	"温控阈值超出保护范围": ["Thermal threshold is outside the protection limits", "Sıcaklık eşiği koruma sınırlarının dışında"],
	"被动温控阈值必须至少相隔 1°C 并递增": ["Passive thresholds must increase with at least 1°C separation", "Pasif eşikler en az 1°C aralıkla artmalıdır"],
	"温控配置回读不一致": ["Thermal settings did not match readback", "Sıcaklık ayarları geri okumayla eşleşmedi"],
	"无法读取 CPU 启动基线": ["Could not read CPU boot baseline", "CPU başlangıç ayarları okunamadı"],
	"无法保存 CPU 启动基线": ["Could not save CPU boot baseline", "CPU başlangıç ayarları kaydedilemedi"],
	'SIM 卡管理': ['SIM management', 'SIM yönetimi'],
	'切换上网卡后，看板、锁定和 AT 的查看卡槽会同步；仍可手动查看另一张卡。': ['Switching the data SIM also updates the SIM viewed by the dashboard, network locks and AT terminal. You can still view the other SIM manually.', 'Veri SIM kartı değiştirildiğinde panel, ağ kilitleri ve AT terminalinde görüntülenen SIM de güncellenir. Diğer SIM kartını yine elle görüntüleyebilirsiniz.'],
	'切换上网卡': ['Switch data SIM', 'Veri SIM kartını değiştir'],
	"双卡双待": ["Dual SIM dual standby", "Çift SIM çift bekleme"],
	"此功能仅适用于 MU300/F50 和 U30 Air": ["This feature is only available on MU300/F50 and U30 Air", "Bu özellik yalnızca MU300/F50 ve U30 Air için kullanılabilir"],
	"默认关闭。关闭时最多开启一张卡的射频，也可全部关闭；开启另一张卡会先关闭原卡。开启双待后可分别启用两卡射频，不会自动打开另一张卡。": ["Off by default. With dual standby off, only one SIM radio can be on, or both can be off. Enabling the other radio first disables the current one. Dual standby allows both radios but does not turn either on automatically.", "Varsayılan olarak kapalıdır. Kapalıyken yalnızca bir SIM radyosu açık olabilir veya ikisi de kapalı olabilir. Diğerini açmak önce mevcut radyoyu kapatır. Çift bekleme iki radyoya izin verir, otomatik olarak açmaz."],
	"开启双卡双待？": ["Enable dual SIM dual standby?", "Çift SIM çift bekleme açılsın mı?"],
	"关闭双卡双待？": ["Disable dual SIM dual standby?", "Çift SIM çift bekleme kapatılsın mı?"],
	"关闭双待时会保留当前单卡射频；若两张均开启，则保留默认上网卡。设置会在重启后保留。": ["Disabling dual standby keeps the single enabled radio. If both are on, it keeps the default data SIM. The setting persists after reboot.", "Çift beklemeyi kapatmak açık olan tek radyoyu korur. İkisi de açıksa varsayılan veri SIM kartını korur. Ayar yeniden başlatmadan sonra korunur."],
	"正在应用双卡双待设置…": ["Applying dual standby settings…", "Çift bekleme ayarları uygulanıyor…"],
	"U30 Air 原生双卡": ["U30 Air native dual SIM", "U30 Air yerel çift SIM"],
	"启用双卡热切换（首次启用后需重启）": ["Enable live SIM switching (reboot after first enabling)", "Canlı SIM geçişini etkinleştir (ilk etkinleştirmede yeniden başlatın)"],
	"仅保存为下次开机默认卡": ["Save as next-boot default only", "Yalnızca sonraki açılış varsayılanı olarak kaydet"],
	"关闭热切换时仍可选择下次开机使用的 SIM。此设置不会立即切卡或重启。": ["With live switching off, you can still select the SIM for the next boot. Saving does not switch or reboot now.", "Canlı geçiş kapalıyken sonraki açılış SIM kartını seçebilirsiniz. Kaydetmek hemen geçiş veya yeniden başlatma yapmaz."],
	"U30 Air 每次仅启用一张卡的射频。目标卡无数据且原卡有数据时，会恢复原卡；开机默认卡只在连接成功后自动保存。": ["U30 Air uses one active radio at a time. If the target gets no data and the previous SIM had data, it is restored. The boot default is saved automatically only after a successful connection.", "U30 Air aynı anda tek radyo kullanır. Hedefte veri yoksa ve önceki SIM veri taşıyorsa önceki karta dönülür. Açılış varsayılanı yalnızca bağlantı başarılı olduğunda otomatik kaydedilir."],
	"双卡开机设置已保存；需要时请手动重启": ["SIM boot settings saved; reboot manually when needed", "SIM açılış ayarları kaydedildi; gerektiğinde elle yeniden başlatın"],
	"请先启用双卡热切换": ["Enable hot switching first", "Önce canlı SIM geçişini etkinleştirin"],
	"双卡通道尚未准备完成，请先重启": ["Persistent dual-SIM brokers unavailable; reboot first", "Çift SIM kanalları hazır değil; önce yeniden başlatın"],
	"当前上网卡状态未知，请重启": ["Unknown active SIM; reboot first", "Etkin SIM bilinmiyor; yeniden başlatın"],
	"射频状态异常，请先重启": ["Unexpected radio state; reboot first", "Beklenmeyen radyo durumu; önce yeniden başlatın"],
	"目标卡槽未检测到 SIM，未进行切换": ["No card answers in the target SIM position; nothing was changed", "Hedef yuvada SIM bulunamadı; geçiş yapılmadı"],
	"目标 SIM 需要 PIN，未进行切换": ["The target SIM is locked by its PIN; nothing was changed", "Hedef SIM için PIN gerekli; geçiş yapılmadı"],
	"目标卡未建立数据连接，已恢复原卡": ["Target SIM got no data within the time; previous SIM restored", "Hedef SIM veri alamadı; önceki SIM geri yüklendi"],
	"两卡均暂无数据连接，保留当前选卡，开机默认卡未更改": ["Target SIM has no data yet and the previous SIM had none either; staying on the target, boot default unchanged", "İki SIM de veri alamadı. Hedef seçili kalır; açılış varsayılanı değişmez"],
	"当前卡暂无数据连接，可单独保存为下次开机默认卡": ["Active SIM has no data; use default for next-boot selection", "Etkin SIM veri alamıyor; sonraki açılış varsayılanı olarak kaydedebilirsiniz"],
	"无法停止蜂窝连接，已取消切换": ["Cannot stop WAN", "Hücresel bağlantı durdurulamadı; geçiş iptal edildi"],
	"上次切卡被中断，请先重启": ["Interrupted switch; reboot first", "Önceki SIM geçişi kesildi; önce yeniden başlatın"],
	"网络锁定正在应用，请稍后再试": ["Network locks are being applied", "Ağ kilitleri uygulanıyor; daha sonra yeniden deneyin"],
	"已恢复原卡，但蜂窝连接未能启动": ["Previous SIM restored but WAN could not be started", "Önceki SIM geri yüklendi ancak hücresel bağlantı başlatılamadı"],
	"请先恢复基带运行再切卡": ["Resume the modem before switching", "SIM değiştirmeden önce modemi devam ettirin"],
	"另一项 SIM 设置正在执行": ["SIM control is busy", "Başka bir SIM ayarı uygulanıyor"],
	"上网卡已切换，但开机默认卡保存失败": ["SIM switched but the boot default could not be saved", "SIM değiştirildi ancak açılış varsayılanı kaydedilemedi"],
	"切卡失败，已恢复原卡": ["Switch failed; previous SIM restored", "SIM geçişi başarısız; önceki SIM geri yüklendi"],
	"切卡失败且射频状态未知，需要重启": ["Switch failed; radio state unknown, reboot required", "SIM geçişi başarısız ve radyo durumu bilinmiyor; yeniden başlatın"],
	"目标卡无数据且原卡恢复失败，需要重启": ["Target SIM got no data and the previous one could not be restored; reboot required", "Hedefte veri yok ve önceki SIM geri yüklenemedi; yeniden başlatın"],
	"目标卡无数据，已恢复原卡但蜂窝连接未启动": ["Target SIM got no data; previous SIM restored but WAN could not be started", "Hedefte veri yok; önceki SIM geri yüklendi ancak hücresel bağlantı başlatılamadı"],
	"基带正忙，未进行切换，请重试": ["The modem is busy; nothing was changed, try again", "Modem meşgul; geçiş yapılmadı, yeniden deneyin"],
	"目标 SIM 无法上电，未进行切换": ["The target SIM could not be powered; nothing was changed", "Hedef SIM açılamadı; geçiş yapılmadı"],
	"射频状态未知，请先重启": ["Unknown radio state; reboot first", "Radyo durumu bilinmiyor; önce yeniden başlatın"],
	"缺少 SIM 互斥组件，请更新完整系统": ["flock is required for SIM control", "SIM kilitleme bileşeni eksik; tam sistemi güncelleyin"],
	"射频状态需要重启恢复，请保留当前开机默认卡": ["Reboot to recover the radio; keep the current boot default", "Radyoyu kurtarmak için yeniden başlatın; mevcut açılış varsayılanını koruyun"],
	"上次双卡启动未完成，已回退 SIM1": ["The previous dual-SIM startup failed; reverted to SIM1", "Önceki çift SIM açılışı başarısız; SIM1 kartına dönüldü"],
	"双卡通道尚未就绪；首次启用后请重启": ["Dual-SIM channels are not ready; reboot after first enabling", "Çift SIM kanalları hazır değil; ilk etkinleştirmeden sonra yeniden başlatın"],
	'默认上网卡': ['Default data SIM', 'Varsayılan veri SIM kartı'],
	'当前上网卡': ['Active data SIM', 'Etkin veri SIM kartı'],
	'欠费或无信号不影响选卡；数据连接失败不会自动切回。': ['A SIM can be selected without credit or signal. A data connection failure does not switch back automatically.', 'Bakiye veya sinyal olmadan SIM seçilebilir. Veri bağlantısı başarısız olursa otomatik geri geçiş yapılmaz.'],
	'数据连接': ['Data connection', 'Veri bağlantısı'],
	'最低频率': ['Minimum frequency', 'En düşük frekans'],
	'最高频率': ['Maximum frequency', 'En yüksek frekans'],
	'开机默认': ['Boot default', 'Açılış varsayılanı'],
	'切换上网卡？': ['Switch data SIM?', 'Veri SIM kartı değiştirilsin mi?'],
	'目标卡槽': ['Target SIM slot', 'Hedef SIM yuvası'],
	'正在切换上网卡…': ['Switching data SIM…', 'Veri SIM kartı değiştiriliyor…'],
	'上网卡切换完成': ['Data SIM switch completed', 'Veri SIM kartı değiştirildi'],
	'切换会短暂中断蜂窝网络，成功后作为开机默认上网卡；不会切换 USB 或 Wi-Fi。': ['Switching briefly interrupts cellular service. On success the card becomes the boot default. USB and Wi-Fi are unchanged.', 'Geçiş hücresel hizmeti kısa süreli keser. Başarılı olursa kart açılış varsayılanı olur. USB ve Wi-Fi değişmez.'],
	'当前配置不支持双卡切换': ['Dual SIM switching is not supported by this configuration', 'Bu yapılandırma çift SIM geçişini desteklemiyor'],
	'双卡尚未共同初始化，请重启后检查状态': ['Both SIMs have not been initialized together; reboot and check status', 'İki SIM birlikte başlatılmadı; yeniden başlatıp durumu kontrol edin'],
	'SIM 卡槽无效': ['Invalid SIM slot', 'Geçersiz SIM yuvası'],
	'另一项模组操作正在执行': ['Another modem operation is running', 'Başka bir modem işlemi çalışıyor'],
	'无法核验当前上网卡': ['Could not verify the current data SIM', 'Geçerli veri SIM kartı doğrulanamadı'],
	'目标 SIM 尚未就绪': ['The target SIM is not ready', 'Hedef SIM hazır değil'],
	'旧数据连接未停止，已取消切换': ['The old data connection did not stop; switch cancelled', 'Eski veri bağlantısı durmadı; geçiş iptal edildi'],
	'切换失败，已恢复原上网卡': ['Switch failed; the original SIM was restored', 'Geçiş başarısız; önceki SIM geri yüklendi'],
	'切换失败且恢复不完整，请检查 SIM 和信号': ['Switch failed and recovery is incomplete; check the SIM and signal', 'Geçiş başarısız ve kurtarma eksik; SIM kartı ve sinyali kontrol edin'],
	'上网卡切换适配器': ['Data SIM switch adapter', 'Veri SIM geçiş bağdaştırıcısı'],
	'分卡网卡映射': ['Per-SIM network device mapping', 'SIM başına ağ aygıtı eşlemesi'],
	'两张 SIM 映射到同一网卡，已暂停第二卡统计以避免重复计费': ['Both SIMs map to the same device; SIM 2 metering is paused to avoid duplicate counting', 'İki SIM aynı aygıta eşlenmiş; çift sayımı önlemek için SIM 2 ölçümü duraklatıldı'],
	'启用双卡后请重启设备，以建立独立的第二卡短信接收通道。': ['After enabling dual SIM, reboot to establish the second SIM’s independent SMS receive channel.', 'Çift SIM etkinleştirildikten sonra ikinci SIM için bağımsız SMS alma kanalını oluşturmak üzere yeniden başlatın.'],
	'留空使用平台默认映射，不影响已有流量账本。': ['Leave empty for platform defaults; existing traffic ledgers are preserved.', 'Platform varsayılanları için boş bırakın; mevcut trafik kayıtları korunur.'],
	'正在查看': ['Viewing', 'Görüntülenen'],
	'查看卡槽': ['View SIM slot', 'SIM yuvasını görüntüle'],
	'仅切换查看与控制对象，不切换上网卡。': ['Changes the SIM being viewed and controlled, not the mobile data SIM.', 'Görüntülenen ve kontrol edilen SIM değişir; mobil veri SIM kartı değişmez.'],
	'SIM 卡槽不可用': ['SIM slot unavailable', 'SIM yuvası kullanılamıyor'],
	'请使用 SIM 管理切换卡槽，终端禁止改写卡槽寻址': ['Use SIM management to switch cards; changing SIM addressing in the terminal is blocked', 'Kart değiştirmek için SIM yönetimini kullanın; terminalde SIM adresleme değişikliği engellendi'],
	'SIM 卡槽数量': ['Number of SIM slots', 'SIM yuvası sayısı'],
	'单卡': ['Single SIM', 'Tek SIM'],
	'双卡': ['Dual SIM', 'Çift SIM'],
	'仅在硬件与 AT 适配器支持独立双卡寻址时开启双卡；查看卡槽不会切换上网卡。': ['Enable dual SIM only when the hardware and AT adapter support independent SIM addressing. Viewing a slot does not switch mobile data.', 'Çift SIM yalnızca donanım ve AT bağdaştırıcısı bağımsız SIM adreslemeyi destekliyorsa etkinleştirilmelidir. Bir yuvayı görüntülemek mobil veriyi değiştirmez.'],
	'调速器': ['Governor', 'Frekans yöneticisi'],
	'最低频率（kHz）': ['Minimum frequency (kHz)', 'En düşük frekans (kHz)'],
	'最高频率（kHz）': ['Maximum frequency (kHz)', 'En yüksek frekans (kHz)'],
	'应用设置': ['Apply settings', 'Ayarları uygula'],
	'仅调整驱动支持的调速器与频率范围，不修改电压或温控。': ['Only changes driver-supported governors and frequency limits, not voltage or thermal controls.', 'Yalnızca sürücünün desteklediği yöneticileri ve frekans sınırlarını değiştirir; voltaj veya sıcaklık denetimini değiştirmez.'],
	'CPU 调频驱动尚未就绪': ['CPU frequency driver is not ready', 'CPU frekans sürücüsü hazır değil'],
	'未勾选时仅本次运行生效，并取消之前保存的开机应用设置。': ['When unchecked, applies only until reboot and removes any previously saved boot settings.', 'İşaretlenmezse yalnızca yeniden başlatmaya kadar geçerlidir ve önceden kaydedilen açılış ayarlarını kaldırır.'],
	'应用 CPU 设置？': ['Apply CPU settings?', 'CPU ayarları uygulansın mı?'],
	'降低频率可能降低吞吐量；提高最低频率可能增加功耗和温度。': ['Lower frequencies may reduce throughput; raising the minimum may increase power use and temperature.', 'Düşük frekanslar aktarım hızını azaltabilir; alt sınırı yükseltmek güç tüketimini ve sıcaklığı artırabilir.'],
	'正在应用 CPU 设置…': ['Applying CPU settings…', 'CPU ayarları uygulanıyor…'],
	'CPU 设置已应用': ['CPU settings applied', 'CPU ayarları uygulandı'],
	'CPU 设置失败且回滚不完整，请检查实际状态': ['CPU settings failed and rollback was incomplete; check actual state', 'CPU ayarları başarısız ve geri alma eksik; gerçek durumu kontrol edin'],
	'CPU 配置无效': ['Invalid CPU settings', 'Geçersiz CPU ayarları'],
	'调速器与频率即时应用；电压偏移独立保存，重启后生效，不修改温控。': ['Governors and frequency limits apply immediately. Voltage offsets are saved separately and apply after reboot; thermal controls are unchanged.', 'Yönetici ve frekans sınırları hemen uygulanır. Voltaj ofsetleri ayrı kaydedilir ve yeniden başlatmada uygulanır; sıcaklık denetimi değişmez.'],
	'CPU 电压偏移': ['CPU voltage offsets', 'CPU voltaj ofsetleri'],
	'频率与调速策略': ['Frequency & governor', 'Frekans ve yönetici'],
	'即时生效': ['Applies immediately', 'Hemen uygulanır'],
	'重启生效': ['Applies after reboot', 'Yeniden başlatmada uygulanır'],
	'当前频率': ['Current frequency', 'Geçerli frekans'],
	'按 CPU 簇设置，步进 3.125 mV。偏移会同时作用于固件对应的 CPU/SRAM 电压表；不增加超频档位。': ['Set each CPU cluster in 3.125 mV steps. Firmware adjusts the corresponding CPU/SRAM voltage tables together; no overclock states are added.', 'Her CPU kümesini 3.125 mV adımlarla ayarlayın. Donanım yazılımı ilgili CPU/SRAM voltaj tablolarını birlikte ayarlar; hız aşırtma kademesi eklenmez.'],
	'已保存，将在下次重启时应用': ['Saved; applies on next reboot', 'Kaydedildi; sonraki yeniden başlatmada uygulanır'],
	'当前电压偏移与保存值一致': ['Active voltage offsets match the saved values', 'Etkin voltaj ofsetleri kayıtlı değerlerle aynı'],
	'当前内核或固件不支持电压调整': ['This kernel or firmware does not support voltage adjustment', 'Bu çekirdek veya donanım yazılımı voltaj ayarını desteklemiyor'],
	'上次电压设置未完成启动确认，已使用默认电压；设置仍保留，重新保存可重试。': ['The previous voltage trial did not finish boot confirmation. Factory voltage is active; your settings are kept. Save again to retry.', 'Önceki voltaj denemesinin açılış onayı tamamlanmadı. Varsayılan voltaj kullanılıyor; ayarlar korundu. Yeniden denemek için tekrar kaydedin.'],
	'电压启动记录损坏，已暂停自动应用；设置仍保留，重新保存可重试。': ['The voltage boot record is invalid. Automatic application is paused; your settings are kept. Save again to retry.', 'Voltaj açılış kaydı geçersiz. Otomatik uygulama duraklatıldı; ayarlar korundu. Yeniden denemek için tekrar kaydedin.'],
	'当前偏移': ['Active offset', 'Etkin ofset'],
	'偏移预设': ['Offset presets', 'Ofset ön ayarları'],
	'默认电压': ['Default voltage', 'Varsayılan voltaj'],
	'预设仅填入小幅偏移，不会直接保存；非零偏移仍需验证稳定性，不保证每颗芯片都安全。': ['Presets only fill in small offsets; they do not save. Non-zero offsets still require stability testing and are not guaranteed safe for every chip.', 'Ön ayarlar yalnızca küçük ofsetleri doldurur; kaydetmez. Sıfır dışı ofsetler kararlılık testi gerektirir ve her çip için güvenli olduğu garanti edilmez.'],
	'当前偏移 → 下次启动偏移': ['Active offset → Next boot offset', 'Etkin ofset → Sonraki açılış ofseti'],
	'请核对各簇偏移。非零偏移可能导致死机或数据损坏，预设也不保证稳定。确认仅保存配置，下次重启生效，不会立即调压。': ['Check the offsets for each cluster. Non-zero offsets may cause crashes or data corruption; presets do not guarantee stability. Confirming only saves the configuration for the next reboot; voltage will not change now.', 'Her kümenin ofsetini kontrol edin. Sıfır dışı ofsetler çökmeye veya veri bozulmasına yol açabilir; ön ayarlar kararlılığı garanti etmez. Onaylama yalnızca sonraki yeniden başlatma için yapılandırmayı kaydeder; voltaj şimdi değişmez.'],
	'确认保存': ['Confirm save', 'Kaydetmeyi onayla'],
	'下次启动偏移（mV）': ['Next boot offset (mV)', 'Sonraki açılış ofseti (mV)'],
	'当前固件电压表': ['Current firmware voltage table', 'Geçerli donanım yazılımı voltaj tablosu'],
	'保存电压设置': ['Save voltage settings', 'Voltaj ayarlarını kaydet'],
	'恢复默认电压': ['Restore default voltage', 'Varsayılan voltajı geri yükle'],
	'调压可能导致死机或数据丢失，请从小幅调整开始。完成启动确认后，直接断电会保留设置；首次应用未完成确认时暂停重试。保存不会立即调压或自动重启。': ['Voltage changes may cause crashes or data loss; start with small adjustments. Confirmed settings survive unplugging. An unfinished first boot pauses retries. Saving does not change live voltage or reboot.', 'Voltaj değişiklikleri çökmeye veya veri kaybına yol açabilir; küçük ayarlarla başlayın. Onaylanan ayarlar güç kesilince korunur. Tamamlanmamış ilk açılış yeniden denemeleri duraklatır. Kaydetmek anlık voltajı değiştirmez veya yeniden başlatmaz.'],
	'CPU 电压配置无效': ['Invalid CPU voltage settings', 'Geçersiz CPU voltaj ayarları'],
	'恢复默认电压？': ['Restore default voltage?', 'Varsayılan voltaj geri yüklensin mi?'],
	'保存电压设置？': ['Save voltage settings?', 'Voltaj ayarları kaydedilsin mi?'],
	'电压设置仅在下次重启时应用；请确认已了解调压风险。': ['Voltage settings apply only after reboot. Please confirm that you understand the risks.', 'Voltaj ayarları yalnızca yeniden başlatmada uygulanır. Riskleri anladığınızı onaylayın.'],
	'正在保存电压设置…': ['Saving voltage settings…', 'Voltaj ayarları kaydediliyor…'],
	'电压设置已保存，重启后生效': ['Voltage settings saved; reboot to apply', 'Voltaj ayarları kaydedildi; uygulamak için yeniden başlatın'],
	'CPU 设置正忙，请稍后重试': ['CPU settings are busy; try again later', 'CPU ayarları meşgul; daha sonra tekrar deneyin'],
	'CPU 配置回读不一致': ['CPU settings did not match readback', 'CPU ayarları geri okumayla eşleşmedi'],
	'无法保存 CPU 设置': ['Could not save CPU settings', 'CPU ayarları kaydedilemedi'],
	'测试消息': ['Test message', 'Test mesajı'],
	'仅保留本次开机最近 30 次投递结果，不记录号码、地址或短信内容。': ['Keeps the last 30 delivery results since boot; no numbers, addresses or message content are recorded.', 'Açılıştan bu yana son 30 teslimat sonucu tutulur; numara, adres veya mesaj içeriği kaydedilmez.'],
	'最近电源通知：': ['Last power notice: ', 'Son güç bildirimi: '],
	"成功响应条件（可选）": ["Success response condition (optional)", "Başarılı yanıt koşulu (isteğe bağlı)"],
	"留空时任意 HTTP 2xx 为成功；填写 errcode=0 或 ok=true 时，还需响应 JSON 顶层字段匹配。": ["Empty accepts any HTTP 2xx; errcode=0 or ok=true also checks a top-level JSON field.", "Boşken tüm HTTP 2xx yanıtları başarılıdır; errcode=0 veya ok=true üst düzey JSON alanını da denetler."],
	"成功响应条件格式无效": ["Invalid success response condition", "Başarılı yanıt koşulu geçersiz"],
	"服务返回失败结果": ["The service rejected the delivery", "Hizmet gönderimi reddetti"],
	"服务响应无法确认成功": ["The service response does not confirm success", "Hizmet yanıtı başarıyı doğrulamıyor"],
	"请等待 5 秒后再测试": ["Please wait 5 seconds before testing again", "Tekrar denemeden önce 5 saniye bekleyin"],
	"清空投递记录": ["Clear delivery history", "Teslimat geçmişini temizle"],
	"清空投递记录？": ["Clear delivery history?", "Teslimat geçmişi temizlensin mi?"],
	"只清除最近投递记录，不影响短信、转发配置或已处理状态。": ["Only clears delivery history; messages, settings and processed state stay unchanged.", "Yalnızca teslimat geçmişini temizler; mesajlar, ayarlar ve işlenen kayıtlar korunur."],
	'暂无投递': ['No delivery yet', 'Henüz teslimat yok'],
	'发送成功': ['Sent successfully', 'Başarıyla gönderildi'],
	'已拦截向原发件人转发': ['Forwarding to original sender blocked', 'Asıl gönderene yönlendirme engellendi'],
	'短信过长，未发送': ['SMS too long; not sent', 'SMS çok uzun; gönderilmedi'],
	'设备保存失败': ['Device storage failed', 'Cihaza kaydedilemedi'],
	'短信服务暂不可用': ['SMS service temporarily unavailable', 'SMS hizmeti geçici olarak kullanılamıyor'],
	'配置无效': ['Invalid configuration', 'Geçersiz yapılandırma'],
	'目标地址无效': ['Invalid destination', 'Geçersiz hedef adresi'],
	'投递失败，请检查配置和设备网络': ['Delivery failed; check the settings and device network', 'Teslimat başarısız; ayarları ve cihaz ağını kontrol edin'],
	'邮箱服务器拒绝登录，请检查授权码': ['SMTP login rejected; check the app password', 'SMTP girişi reddedildi; uygulama şifresini kontrol edin'],
	'读取失败，请刷新页面': ['Loading failed; refresh the page', 'Yükleme başarısız; sayfayı yenileyin'],
	'号码无效、重复或超过数量上限': ['Invalid, duplicate or too many numbers', 'Geçersiz, yinelenen veya fazla sayıda numara'],
	'关键词无效、重复或超过数量上限': ['Invalid, duplicate or too many keywords', 'Geçersiz, yinelenen veya fazla sayıda anahtar kelime'],
	'请先配置当前转发渠道': ['Configure the selected forwarding channel first', 'Önce seçilen yönlendirme kanalını yapılandırın'],
	'保存短信转发设置？': ['Save SMS forwarding settings?', 'SMS yönlendirme ayarları kaydedilsin mi?'],
	'开启后只处理新收到的短信；历史短信不会补发。': ['Only newly received SMS will be processed; old messages will not be forwarded.', 'Yalnızca yeni alınan SMS’ler işlenecek; eski mesajlar yönlendirilmeyecek.'],
	'正在保存短信转发设置…': ['Saving SMS forwarding settings…', 'SMS yönlendirme ayarları kaydediliyor…'],
	'保存失败': ['Save failed', 'Kaydetme başarısız'],
	'已保存': ['Saved', 'Kaydedildi'],
	'请先保存修改再测试': ['Save changes before testing', 'Testten önce değişiklikleri kaydedin'],
	'请先开启并保存短信转发': ['Enable and save forwarding first', 'Önce yönlendirmeyi açıp kaydedin'],
	'将使用本机 SIM 向已保存号码发送，可能产生短信费用。': ['The device SIM will send to the saved numbers; SMS charges may apply.', 'Cihazın SIM kartı kayıtlı numaralara gönderecek; SMS ücreti doğabilir.'],
	'将向当前已保存渠道发送一条固定测试消息。': ['A fixed test message will be sent to the saved channel.', 'Kayıtlı kanala sabit bir test mesajı gönderilecek.'],
	'发送测试消息？': ['Send a test message?', 'Test mesajı gönderilsin mi?'],
	'测试已开始，请稍后刷新状态': ['Test started; refresh status shortly', 'Test başladı; kısa süre sonra durumu yenileyin'],
	'测试失败：': ['Test failed: ', 'Test başarısız: '],
	'未知错误': ['Unknown error', 'Bilinmeyen hata'],
	'设备管理': ['Device management', 'Cihaz yönetimi'],
	'USB 角色': ['USB role', 'USB rolü'],
	'当前角色': ['Current role', 'Geçerli rol'],
	'切换 USB 角色': ['Switch USB role', 'USB rolünü değiştir'],
	'设备模式': ['Device mode', 'Cihaz modu'],
	'主机模式': ['Host mode', 'Ana makine modu'],
	'开机自动启用主机模式': ['Enable host mode at boot', 'Açılışta ana makine modunu etkinleştir'],
	'应用角色': ['Apply role', 'Rolü uygula'],
	'主机模式会断开本端口的 USB 网络与串口。F50 没有电池；切换后可能失去管理连接，外接 USB 网卡通常需要自供电 Hub。': ['Host mode disconnects USB networking and serial on this port. F50 has no battery; management may be lost and a USB adapter usually needs a powered hub.', 'Ana makine modu bu bağlantı noktasındaki USB ağını ve seri bağlantıyı keser. F50 bataryasızdır; yönetim bağlantısı kaybolabilir ve USB bağdaştırıcısı genellikle harici güçlü bir hub gerektirir.'],
	'USB 网络模式': ['USB network mode', 'USB ağ modu'],
	'网络协议': ['Network protocol', 'Ağ protokolü'],
	'生效期限': ['Apply duration', 'Uygulama süresi'],
	'仅下次重启': ['Next reboot only', 'Yalnızca sonraki yeniden başlatma'],
	'永久生效': ['Permanent', 'Kalıcı'],
	'启用所选协议': ['Enable selected protocol', 'Seçilen protokolü etkinleştir'],
	'保存，重启后生效': ['Save; apply after reboot', 'Kaydet; yeniden başlatınca uygula'],
	'NCM 为默认模式。Windows 不原生支持 ECM；RNDIS 会改变枚举方式。关闭“启用所选协议”时仅保存选择；选择“仅下次重启”则成功应用一次后恢复默认 NCM。': ['NCM is the default. Windows does not natively support ECM; RNDIS changes enumeration. With Enable selected protocol off, only your choice is saved. Next reboot only applies once, then returns to NCM.', 'NCM varsayılandır. Windows ECM’yi yerel olarak desteklemez; RNDIS aygıt tanımayı değiştirir. Seçilen protokolü etkinleştir kapalıysa yalnızca seçim kaydedilir. Yalnızca sonraki yeniden başlatma bir kez uygulanır, ardından NCM’ye dönülür.'],
	'主机模式下不可选择 USB 网络模式；主机开机自启会自动关闭 USB 网络开机自启。': ['USB network mode is unavailable in host mode; host auto-start also disables USB network auto-start.', 'Ana makine modunda USB ağ modu kullanılamaz; ana makine otomatik başlatma USB ağının otomatik başlatmasını da kapatır.'],
	'USB 网卡': ['USB network adapters', 'USB ağ bağdaştırıcıları'],
	'仅主机模式可用。刷新时会尝试启用发现的 USB 网卡；添加到 LAN 后将保存到网桥并重新加载网络。': ['Available only in host mode. Refresh brings discovered USB adapters up; adding to LAN saves the bridge and reloads networking.', 'Yalnızca ana makine modunda kullanılabilir. Yenileme bulunan USB bağdaştırıcılarını açar; LAN’a ekleme köprüyü kaydeder ve ağı yeniden yükler.'],
	'切换到主机模式后显示 USB 网卡。': ['Switch to host mode to see USB adapters.', 'USB bağdaştırıcılarını görmek için ana makine moduna geçin.'],
	'正在扫描 USB 网卡…': ['Scanning USB adapters…', 'USB bağdaştırıcıları taranıyor…'],
	'没有发现 USB 网卡。': ['No USB adapters found.', 'USB bağdaştırıcısı bulunamadı.'],
	'链路已连接': ['Link connected', 'Bağlantı kuruldu'],
	'链路未连接，已尝试启用': ['No link; enable attempted', 'Bağlantı yok; etkinleştirme denendi'],
	'已加入 LAN': ['Added to LAN', 'LAN’a eklendi'],
	'添加到 LAN': ['Add to LAN', 'LAN’a ekle'],
	'确认切换 USB 角色？': ['Switch USB role?', 'USB rolü değiştirilsin mi?'],
	'切换主机模式会立即断开 USB 管理连接。F50 没有电池，外设可能需要自供电；请确认有其他管理途径。': ['Host mode immediately disconnects USB management. F50 has no battery and peripherals may need external power; make sure another management path exists.', 'Ana makine modu USB yönetimini hemen keser. F50 bataryasızdır ve çevre birimleri harici güç gerektirebilir; başka bir yönetim yolu olduğundan emin olun.'],
	'切回设备模式后 USB 网络和串口会重新枚举。': ['USB networking and serial will re-enumerate in device mode.', 'Cihaz modunda USB ağı ve seri bağlantı yeniden tanınır.'],
	'正在切换 USB 角色…': ['Switching USB role…', 'USB rolü değiştiriliyor…'],
	'切换请求已接收，USB 连接可能短暂中断。': ['Switch request accepted; the USB connection may briefly disconnect.', 'Geçiş isteği alındı; USB bağlantısı kısa süreli kesilebilir.'],
	'管理连接已中断；请重新连接后确认 USB 角色。': ['Management connection lost; reconnect to confirm the USB role.', 'Yönetim bağlantısı kesildi; USB rolünü doğrulamak için yeniden bağlanın.'],
	'暂时无法确认角色；请重新连接后刷新页面。': ['Unable to confirm the role yet; reconnect and refresh the page.', 'Rol henüz doğrulanamadı; yeniden bağlanıp sayfayı yenileyin.'],
	'USB 角色已应用': ['USB role applied', 'USB rolü uygulandı'],
	'保存 USB 网络模式？': ['Save USB network mode?', 'USB ağ modu kaydedilsin mi?'],
	'网络模式将在下次重启时生效，USB 管理连接可能需要重新识别。': ['The network mode applies on the next reboot; USB management may need to reconnect.', 'Ağ modu sonraki yeniden başlatmada uygulanır; USB yönetimi yeniden bağlanabilir.'],
	'只保存选择；未启用所选协议，下次重启仍使用默认 NCM。': ['Save the selection only; with the selected protocol disabled, the next boot still uses default NCM.', 'Yalnızca seçim kaydedilir; seçilen protokol etkin değilse sonraki açılışta varsayılan NCM kullanılır.'],
	'正在保存 USB 网络设置…': ['Saving USB network settings…', 'USB ağ ayarları kaydediliyor…'],
	'设置已保存': ['Settings saved', 'Ayarlar kaydedildi'],
	'添加 USB 网卡到 LAN？': ['Add USB adapter to LAN?', 'USB bağdaştırıcısı LAN’a eklensin mi?'],
	'这会保存网桥配置并重新加载网络，现有连接可能短暂中断。': ['This saves the bridge configuration and reloads networking; existing connections may briefly drop.', 'Bu işlem köprü yapılandırmasını kaydedip ağı yeniden yükler; mevcut bağlantılar kısa süreli kesilebilir.'],
	'正在添加 USB 网卡…': ['Adding USB adapter…', 'USB bağdaştırıcısı ekleniyor…'],
	'自动将空闲 USB 网卡加入 LAN': ['Automatically add unused USB adapters to LAN', 'Boştaki USB bağdaştırıcılarını otomatik olarak LAN’a ekle'],
	'保存策略': ['Save policy', 'İlkeyi kaydet'],
	'默认关闭。启用后在网卡接入或 LAN 启动时自动加入空闲 USB 有线网卡；不会接管其他网络、USB Wi-Fi 或本机共享接口。关闭不会删除已保存端口。': ['Off by default. On adapter attachment or LAN startup, unused USB Ethernet adapters are added automatically. Other networks, USB Wi-Fi and local tethering interfaces are excluded. Disabling does not remove saved ports.', 'Varsayılan olarak kapalıdır. Bağdaştırıcı bağlandığında veya LAN başladığında boştaki USB Ethernet bağdaştırıcıları otomatik eklenir. Diğer ağlar, USB Wi-Fi ve yerel paylaşım arayüzleri hariç tutulur. Kapatmak kayıtlı bağlantı noktalarını silmez.'],
	'仅主机模式可用。刷新只尝试启用未被其他网络占用的网卡；现有网卡可手动添加到 LAN。': ['Host mode only. Refresh only brings up adapters not owned by other networks; existing adapters can be added to LAN manually.', 'Yalnızca ana makine modunda. Yenileme yalnızca başka ağlara ait olmayan bağdaştırıcıları açar; mevcut bağdaştırıcılar LAN’a elle eklenebilir.'],
	'保存 USB 网卡自动加入策略？': ['Save USB adapter auto-add policy?', 'USB bağdaştırıcısı otomatik ekleme ilkesi kaydedilsin mi?'],
	'空闲 USB 有线网卡会成为 LAN 端口，向所连接网络提供局域网访问。仅连接可信网络；已连接的网卡可手动添加。': ['Unused USB Ethernet adapters will become LAN ports and grant LAN access to the connected network. Connect trusted networks only; already connected adapters can be added manually.', 'Boştaki USB Ethernet bağdaştırıcıları LAN bağlantı noktası olur ve bağlı ağa LAN erişimi sağlar. Yalnızca güvenilir ağları bağlayın; zaten bağlı bağdaştırıcılar elle eklenebilir.'],
	'停止自动加入新网卡；已保存的 LAN 端口及热插拔恢复保持不变。': ['Stop adding new adapters automatically; saved LAN ports and hotplug recovery remain unchanged.', 'Yeni bağdaştırıcıları otomatik eklemeyi durdurur; kayıtlı LAN bağlantı noktaları ve yeniden bağlanma korunur.'],
	'USB 网卡自动加入策略已保存': ['USB adapter auto-add policy saved', 'USB bağdaştırıcısı otomatik ekleme ilkesi kaydedildi'],
	'驱动': ['Driver', 'Sürücü'],
	'链路未连接': ['Link disconnected', 'Bağlantı yok'],
	'当前归属': ['Assigned to', 'Atandığı ağ'],
	'未分配': ['Unassigned', 'Atanmamış'],
	'已被其他网络占用': ['In use by another network', 'Başka bir ağ tarafından kullanılıyor'],
	'已保存，等待接入网桥': ['Saved; waiting for bridge attachment', 'Kaydedildi; köprüye bağlanması bekleniyor'],
	'不可添加': ['Unavailable', 'Eklenemez'],
	'USB 网卡配置正忙，请稍后重试': ['USB adapter configuration is busy; try again later', 'USB bağdaştırıcısı yapılandırması meşgul; daha sonra tekrar deneyin'],
	'配置有未保存更改，请先处理后重试': ['Resolve pending configuration changes before retrying', 'Tekrar denemeden önce bekleyen yapılandırma değişikliklerini çözün'],
	'无法保存 USB 网卡自动加入设置': ['Could not save USB adapter auto-add settings', 'USB bağdaştırıcısı otomatik ekleme ayarları kaydedilemedi'],
	'无法确认网卡归属，请稍后重试': ['Could not determine adapter ownership; try again later', 'Bağdaştırıcının ait olduğu ağ belirlenemedi; daha sonra tekrar deneyin'],
	'网卡已被其他网络占用，不可加入 LAN': ['Adapter belongs to another network and cannot join LAN', 'Bağdaştırıcı başka bir ağa ait ve LAN’a eklenemez'],
	'添加失败：': ['Add failed: ', 'Ekleme başarısız: '],
	'切换失败：': ['Switch failed: ', 'Değiştirme başarısız: '],
	'保存失败：': ['Save failed: ', 'Kaydetme başarısız: '],
	'不可用': ['Unavailable', 'Kullanılamıyor'],
	'保存': ['Save', 'Kaydet'],
	'添加': ['Add', 'Ekle'],
	'USB role adapter is not executable': ['USB role adapter is not executable', 'USB rol bağdaştırıcısı çalıştırılabilir değil'],
	'USB role adapter failed': ['USB role adapter failed', 'USB rol bağdaştırıcısı başarısız'],
	'Device is the boot default; host auto-apply is the only persistent role': ['Device is the boot default; host auto-apply is the only persistent role', 'Cihaz modu açılış varsayılanıdır; yalnızca ana makine otomatik uygulaması kalıcıdır'],
	'Cannot save USB role policy': ['Cannot save USB role policy', 'USB rol ilkesi kaydedilemiyor'],
	'Cannot disable USB network boot policy': ['Cannot disable USB network boot policy', 'USB ağı açılış ilkesi kapatılamıyor'],
	'Cannot commit USB role policy': ['Cannot commit USB role policy', 'USB rol ilkesi uygulanamıyor'],
	'Cannot save USB network policy': ['Cannot save USB network policy', 'USB ağ ilkesi kaydedilemiyor'],
	'Cannot commit USB network policy': ['Cannot commit USB network policy', 'USB ağ ilkesi uygulanamıyor'],
	'Cannot create USB boot settings directory': ['Cannot create USB boot settings directory', 'USB açılış ayarları dizini oluşturulamıyor'],
	'Cannot write USB boot settings': ['Cannot write USB boot settings', 'USB açılış ayarları yazılamıyor'],
	'Cannot save USB boot settings': ['Cannot save USB boot settings', 'USB açılış ayarları kaydedilemiyor'],
	'Invalid USB role': ['Invalid USB role', 'Geçersiz USB rolü'],
	'Invalid USB network mode': ['Invalid USB network mode', 'Geçersiz USB ağ modu'],
	'Invalid USB network scope': ['Invalid USB network scope', 'Geçersiz USB ağ süresi'],
	'Invalid boot-auto value': ['Invalid boot-auto value', 'Geçersiz otomatik başlatma değeri'],
	'USB role switch is unavailable': ['USB role switch is unavailable', 'USB rol anahtarı kullanılamıyor'],
	'USB role readback did not match': ['USB role readback did not match', 'USB rolü geri okuması eşleşmedi'],
	'USB role switch was refused by platform safety checks': ['USB role switch was refused by platform safety checks', 'USB rol değişimi platform güvenlik denetimi tarafından reddedildi'],
	'USB network mode is unavailable in host role': ['USB network mode is unavailable in host role', 'Ana makine rolünde USB ağ modu kullanılamaz'],
	'Disable host boot-auto before configuring USB network': ['Disable host boot-auto before configuring USB network', 'USB ağını yapılandırmadan önce ana makine otomatik başlatmasını kapatın'],
	'USB host mode is required': ['USB host mode is required', 'USB ana makine modu gerekli'],
	'Not a USB network interface': ['Not a USB network interface', 'USB ağ arayüzü değil'],
	'LAN bridge device section was not found': ['LAN bridge device section was not found', 'LAN köprü aygıtı bölümü bulunamadı'],
	'Cannot bring USB network interface up': ['Cannot bring USB network interface up', 'USB ağ arayüzü etkinleştirilemiyor'],
	'Cannot add interface to LAN bridge': ['Cannot add interface to LAN bridge', 'Arayüz LAN köprüsüne eklenemiyor'],
	'Cannot save LAN bridge': ['Cannot save LAN bridge', 'LAN köprüsü kaydedilemiyor'],
	'Cannot attach USB interface to LAN bridge': ['Cannot attach USB interface to LAN bridge', 'USB arayüzü LAN köprüsüne bağlanamıyor'],
	'Network reload failed': ['Network reload failed', 'Ağ yeniden yüklenemedi'],
	'链路与流量': ['Link & traffic', 'Bağlantı ve trafik'],
	'下行速率': ['Download rate', 'İndirme hızı'],
	'上行速率': ['Upload rate', 'Yükleme hızı'],
	'累计接收': ['Total received', 'Toplam alınan'],
	'累计发送': ['Total sent', 'Toplam gönderilen'],
	'会话时长': ['Session duration', 'Oturum süresi'],
	'注册状态': ['Registration', 'Kayıt durumu'],
	'调制方式 下/上': ['Modulation DL/UL', 'Modülasyon İndirme/Yükleme'],
	'MCS 下/上': ['MCS DL/UL', 'MCS İndirme/Yükleme'],
	'BLER 下/上': ['BLER DL/UL', 'BLER İndirme/Yükleme'],
	'按当前 MCS 估算': ['Estimated from current MCS', 'Geçerli MCS değerinden tahmin'],
	'频宽': ['Bandwidth', 'Bant genişliği'],
	'AMBR 下/上': ['AMBR DL/UL', 'AMBR İndirme/Yükleme'],
	'无线 · 局域网 · 设备 · SIM': ['Wi-Fi · LAN · Device · SIM', 'Wi-Fi · LAN · Cihaz · SIM'],
	'CPU 占用': ['CPU usage', 'CPU kullanımı'],
	'内存': ['Memory', 'Bellek'],
	'存储': ['Storage', 'Depolama'],
	'电源': ['Power', 'Güç'],
	'信道': ['Channel', 'Kanal'],
	'加密': ['Encryption', 'Şifreleme'],
	'隐藏 SSID': ['Hidden SSID', 'Gizli SSID'],
	'国家': ['Country', 'Ülke'],
	'AP 状态': ['AP status', 'AP durumu'],
	'USB 网络': ['USB network', 'USB ağı'],
	'连接跟踪': ['Connection tracking', 'Bağlantı izleme'],
	'LAN 地址': ['LAN address', 'LAN adresi'],
	'无线客户端': ['Wi-Fi clients', 'Wi-Fi istemcileri'],
	'DHCP 租约': ['DHCP leases', 'DHCP kiraları'],
	'设备型号': ['Device model', 'Cihaz modeli'],
	'系统': ['System', 'Sistem'],
	'调制解调器': ['Modem', 'Modem'],
	'运营商': ['Carrier', 'Operatör'],
	'模组': ['Module', 'Modül'],
	'固件': ['Firmware', 'Ürün yazılımı'],
	'显示卡号信息': ['Show SIM identifiers', 'SIM kimliklerini göster'],
	'隐藏卡号信息': ['Hide SIM identifiers', 'SIM kimliklerini gizle'],
	'快捷控制': ['Quick controls', 'Hızlı denetimler'],
	'数据连接': ['Data connection', 'Veri bağlantısı'],
	'蜂窝射频': ['Cellular radio', 'Hücresel radyo'],
	'Wi-Fi 热点': ['Wi-Fi hotspot', 'Wi-Fi erişim noktası'],
	'重启调制解调器': ['Restart modem', 'Modemi yeniden başlat'],
	'重启设备': ['Restart device', 'Cihazı yeniden başlat'],
	"手动输入小区": ["Enter a cell manually", "Hücreyi elle gir"],
	"制式": ["Radio technology", "Radyo teknolojisi"],
	"频点（EARFCN / NR-ARFCN）": ["Channel number (EARFCN / NR-ARFCN)", "Kanal numarası (EARFCN / NR-ARFCN)"],
	"锁定指定小区": ["Lock specified cell", "Belirtilen hücreyi kilitle"],
	"LTE：EARFCN 0–262143，PCI 0–503；NR：NR-ARFCN 1–3279165，PCI 0–1007。": ["LTE: EARFCN 0–262143, PCI 0–503; NR: NR-ARFCN 1–3279165, PCI 0–1007.", "LTE: EARFCN 0–262143, PCI 0–503; NR: NR-ARFCN 1–3279165, PCI 0–1007."],
	"应用于当前查看的 SIM，无需小区出现在邻区列表中。请输入频点编号，不是 MHz 频率。": ["Applies to the SIM being viewed; the cell need not appear in the neighbor list. Enter the channel number, not a frequency in MHz.", "Görüntülenen SIM için uygulanır; hücrenin komşu listesinde olması gerekmez. MHz frekansı değil, kanal numarasını girin."],
	"请输入范围内的十进制频点和 PCI": ["Enter a decimal channel number and PCI within the listed ranges", "Listelenen aralıklarda ondalık kanal numarası ve PCI girin"],
	"手动锁定小区": ["Manual cell lock", "Elle hücre kilitleme"],
	"关机或重启": ["Power off or restart", "Kapat veya yeniden başlat"],
	"请选择关机或重启，执行后所有连接都会断开。": ["Choose power off or restart. All connections will be disconnected.", "Kapatmayı veya yeniden başlatmayı seçin. Tüm bağlantılar kesilecektir."],
	"关机": ["Power off", "Kapat"],
	"重启": ["Restart", "Yeniden başlat"],
	"正在关机…": ["Powering off…", "Kapatılıyor…"],
	"正在重启…": ["Restarting…", "Yeniden başlatılıyor…"],
	'切换到 Android': ['Switch to Android', 'Android’e geç'],
	'邻区': ['Neighbor cells', 'Komşu hücreler'],
	'制式/频段': ['RAT/Band', 'Teknoloji/Bant'],
	'频点': ['Frequency', 'Frekans'],
	'暂无邻区数据': ['No neighbor-cell data', 'Komşu hücre verisi yok'],
	'已锁定': ['Locked', 'Kilitli'],
	'锁定小区': ['Lock cell', 'Hücreyi kilitle'],
	'锁定': ['Lock', 'Kilitle'],
	'已运行': ['Uptime', 'Çalışma süresi'],
	'无线电已关': ['Radio off', 'Radyo kapalı'],
	'未插卡': ['No SIM card', 'SIM kart takılı değil'],
	'未驻留小区': ['No serving cell', 'Bağlı olunan hücre yok'],
	'无应答': ['No response', 'Yanıt yok'],
	'信号': ['Signal', 'Sinyal'],
	'优秀': ['Excellent', 'Mükemmel'],
	'良好': ['Good', 'İyi'],
	'一般': ['Fair', 'Orta'],
	'较差': ['Poor', 'Zayıf'],
	'未知': ['Unknown', 'Bilinmiyor'],
	'未注册': ['Not registered', 'Kayıtlı değil'],
	'已注册（漫游）': ['Registered (roaming)', 'Kayıtlı (dolaşım)'],
	'已注册': ['Registered', 'Kayıtlı'],
	'搜索中': ['Searching', 'Aranıyor'],
	'注册被拒': ['Registration denied', 'Kayıt reddedildi'],
	'仅紧急': ['Emergency only', 'Yalnızca acil arama'],
	'状态': ['Status', 'Durum'],
	'LTE 锚点': ['LTE anchor', 'LTE bağlantı noktası'],
	'LTE 链路': ['LTE link', 'LTE bağlantısı'],
	'锚点': ['Anchor', 'Bağlantı noktası'],
	'已隐藏': ['Hidden', 'Gizli'],
	'运行中': ['Running', 'Çalışıyor'],
	'未运行': ['Not running', 'Çalışmıyor'],
	'已连接': ['Connected', 'Bağlı'],
	'未连接': ['Disconnected', 'Bağlı değil'],
	'未读': ['unread', 'okunmamış'],
	' 条未读': [' unread', ' okunmamış'],
	'约半分钟': ['about 30 seconds', 'yaklaşık 30 saniye'],
	'近期 DHCP 租约': ['Recent DHCP leases', 'Son DHCP kiraları'],
	'主板': ['Board', 'Anakart'],
	'无温度读数': ['No temperature readings', 'Sıcaklık verisi yok'],
	'在线': ['Online', 'Çevrimiçi'],
	'AT 适配器不可用': ['AT adapter unavailable', 'AT bağdaştırıcısı kullanılamıyor'],
	'正在执行': ['Running', 'Çalıştırılıyor'],
	'已后台执行': ['Started in background', 'Arka planda başlatıldı'],
	'已执行': ['Done', 'Tamamlandı'],
	'失败': ['Failed', 'Başarısız'],
	'未知错误': ['Unknown error', 'Bilinmeyen hata'],
	'状态暂不可读，请刷新后重试': ['State unavailable; refresh and retry', 'Durum okunamıyor; yenileyip tekrar deneyin'],
	'关闭后只停止下次开机回放，已保存的网络、VoLTE 和 VoNR 设置不会被删除。': ['Turning this off only disables boot replay; saved network, VoLTE and VoNR settings are kept.', 'Kapatmak yalnızca açılışta uygulamayı durdurur; ağ, VoLTE ve VoNR ayarları korunur.'],
	'VoLTE 用于 4G 通话，VoNR 用于 5G SA 通话。设置按当前查看的 SIM 保存，并跟随“开机自动应用”。': ['VoLTE handles 4G calls and VoNR handles 5G SA calls. Settings are saved per viewed SIM and follow Apply at boot.', 'VoLTE 4G, VoNR 5G SA aramaları içindir. Ayarlar görüntülenen SIM için kaydedilir ve açılışta uygulama ayarını izler.'],
	'关闭 VoLTE 或 VoNR 不等于屏蔽全部来电和短信，传统网络业务仍可能到达。': ['Disabling VoLTE or VoNR does not block every call or SMS; traditional network services may still arrive.', 'VoLTE veya VoNR kapatıldığında tüm aramalar ve SMS engellenmez; geleneksel şebeke hizmetleri ulaşabilir.'],
	'调用失败': ['Request failed', 'İstek başarısız'],
	'正在断开数据连接': ['Disconnecting data', 'Veri bağlantısı kesiliyor'],
	'正在拨号': ['Connecting data', 'Veri bağlantısı kuruluyor'],
	'断开数据连接': ['Disconnect data', 'Veri bağlantısını kes'],
	'建立数据连接': ['Connect data', 'Veri bağlantısı kur'],
	'将断开蜂窝数据连接，依赖此连接的设备将无法上网。': ['Cellular data will disconnect. Devices using this connection will lose internet access.', 'Hücresel veri bağlantısı kesilecek. Bu bağlantıyı kullanan cihazlar internet erişimini kaybedecek.'],
	'将建立蜂窝数据连接，可能产生流量费用。': ['A cellular data connection will be established. Data charges may apply.', 'Hücresel veri bağlantısı kurulacak. Veri ücreti uygulanabilir.'],
	'关闭 Wi-Fi 热点': ['Turn off Wi-Fi hotspot', 'Wi-Fi erişim noktasını kapat'],
	'打开 Wi-Fi 热点': ['Turn on Wi-Fi hotspot', 'Wi-Fi erişim noktasını aç'],
	'热点客户端会断开；若正通过此热点管理设备，需要重新连接后才能继续访问。': ['Hotspot clients will disconnect. If you are managing the device through this hotspot, reconnect to regain access.', 'Erişim noktasına bağlı cihazların bağlantısı kesilecek. Cihazı bu erişim noktasından yönetiyorsanız erişmek için yeniden bağlanmanız gerekecek.'],
	'将使用已保存的配置开启 Wi-Fi 热点。': ['The Wi-Fi hotspot will start using the saved settings.', 'Wi-Fi erişim noktası kayıtlı ayarlarla açılacak.'],
	'关闭蜂窝射频': ['Turn off cellular radio', 'Hücresel radyoyu kapat'],
	'打开蜂窝射频': ['Turn on cellular radio', 'Hücresel radyoyu aç'],
	'蜂窝连接会中断': ['Cellular connectivity will be interrupted', 'Hücresel bağlantı kesilecek'],
	'将执行 SFUN 上电序列（最多约 1 分钟）': ['The SFUN power-on sequence will run (up to about 1 minute)', 'SFUN açılış sırası çalışacak (yaklaşık 1 dakikaya kadar)'],
	'蜂窝连接会中断 1-2 分钟': ['Cellular connectivity may stop for 1–2 minutes', 'Hücresel bağlantı 1–2 dakika kesilebilir'],
	'重启整个设备': ['Restart the entire device', 'Tüm cihazı yeniden başlat'],
	'所有连接会断开': ['All connections will be interrupted', 'Tüm bağlantılar kesilecek'],
	  '启动控制不可用': ['Boot control unavailable', 'Açılış kontrolü kullanılamıyor'],
 '启动控制正忙，请稍后重试': ['Boot control is busy; try again later', 'Açılış kontrolü meşgul; sonra tekrar deneyin'],
 '启动控制信息无效': ['Invalid boot control information', 'Açılış kontrol bilgisi geçersiz'],
 '启动控制回读校验失败': ['Boot control readback verification failed', 'Açılış kontrolü geri okuma doğrulaması başarısız'],
 '启动控制回退失败，请检查启动设置': ['Boot control rollback failed; check boot settings', 'Açılış kontrolü geri alma başarısız; açılış ayarlarını kontrol edin'],
 '默认启动设置失败，请重试': ['Could not save the default boot; try again', 'Varsayılan açılış kaydedilemedi; tekrar deneyin'],
'当前为临时启动 OpenWrt': ['OpenWrt was booted temporarily', 'OpenWrt geçici olarak başlatıldı'],
 '是否将 OpenWrt 设为默认启动？不会立即重启或断网。': ['Make OpenWrt the default boot? This will not reboot or interrupt networking.', 'OpenWrt varsayılan açılsın mı? Yeniden başlatma veya ağ kesintisi olmaz.'],
 '设为默认启动': ['Make default boot', 'Varsayılan başlatma yap'],
 '以后开机默认进入 OpenWrt；启动失败仍保留 Android 回退。现在不会重启。': ['Future boots will use OpenWrt, with Android fallback if startup fails. No reboot now.', 'Sonraki açılışlar OpenWrt kullanır; başarısızlıkta Android geri dönüşü korunur. Şimdi yeniden başlatılmaz.'],
 '当前启动镜像不支持设置默认启动': ['This boot image cannot set a default boot', 'Bu açılış imajı varsayılan başlatmayı ayarlayamaz'],
'切换到 Android 系统': ['Switch to Android', 'Android sistemine geç'],
	'下次启动将进入 Android 并立即重启，此管理页面与蜂窝共享都会断开': ['The next boot will enter Android and reboot now. This management page and cellular sharing will disconnect', 'Sonraki açılış Android’e geçecek ve cihaz şimdi yeniden başlayacak. Yönetim sayfası ve hücresel paylaşım kesilecek'],
	'回到 OpenWrt：在 Android 上执行 mu300-next-boot linux 后重启': ['To return to OpenWrt, run mu300-next-boot linux in Android and reboot', 'OpenWrt’ye dönmek için Android’de mu300-next-boot linux çalıştırıp yeniden başlatın'],
	'或什么都不做，连续 5 次开机未完成会自动回退': ['Or do nothing: five failed boots trigger automatic fallback', 'Ya da hiçbir şey yapmayın: beş başarısız açılışta otomatik geri dönülür'],
	'切换并重启': ['Switch and reboot', 'Geç ve yeniden başlat'],
	'正在武装 Android 引导并重启': ['Preparing Android boot and rebooting', 'Android açılışı hazırlanıyor ve yeniden başlatılıyor'],
	'协议栈会重启（SFUN），蜂窝断开约半分钟': ['The radio stack will restart (SFUN); cellular service will stop for about 30 seconds', 'Radyo yığını yeniden başlayacak (SFUN); hücresel bağlantı yaklaşık 30 saniye kesilecek'],
	'正在后台锁定': ['Locking in background', 'Arka planda kilitleniyor'],
	'已后台锁定': ['Lock started in background', 'Kilit arka planda başlatıldı'],
	'稍后自动刷新状态': ['status will refresh shortly', 'durum birazdan yenilenecek'],
	'锁定失败': ['Lock failed', 'Kilitleme başarısız'],
	'确认': ['Confirm', 'Onayla'],
	'确定': ['OK', 'Tamam'],
	'取消': ['Cancel', 'İptal'],
	'新短信': ['New SMS', 'Yeni SMS'],
	'未知号码': ['Unknown number', 'Bilinmeyen numara'],
	'中国移动': ['China Mobile', 'China Mobile'],
	'中国联通': ['China Unicom', 'China Unicom'],
	'中国电信': ['China Telecom', 'China Telecom'],
	'中国广电': ['China Broadnet', 'China Broadnet'],
	'中国铁通': ['China Tietong', 'China Tietong'],
	' 天 ': [' d ', ' gün '],
	' 小时': [' h', ' sa'],
	' 分': [' min', ' dk'],
	' 条': [' entries', ' kayıt'],
	' 台': [' clients', ' istemci'],
	' 簇': [' cluster', ' küme'],
	'共 ': ['Total ', 'Toplam '],
	'余 ': ['Free ', 'Boş '],
	'簇': ['Cluster ', 'Küme '],
	'数据漫游': ['Data roaming', 'Veri dolaşımı'],
	'使用中': ['In use', 'Kullanımda'],
	'是': ['Yes', 'Evet'],
	'否': ['No', 'Hayır'],
	'状态看板': ['Dashboard', 'Durum paneli'],
	'蜂窝': ['Cellular', 'Hücresel'],
	'网络锁定': ['Network locks', 'Ağ kilitleri'],
	'短信': ['SMS', 'SMS'],
	'AT 终端': ['AT terminal', 'AT terminali'],
	"自动网络模式编号": ["Automatic network mode code", "Otomatik şebeke modu kodu"],
	"默认": ["Default", "Varsayılan"],
	"控制各 SIM 自动模式的基带制式组合。双卡时若网络延迟很高或明显卡顿，可尝试切换 134／152；效果取决于本机基带。": ["Used by automatic network mode on each SIM. If dual-SIM service has high latency or stalls, try switching between 134 and 152; results depend on the modem firmware.", "Her SIM’in otomatik şebeke modunda kullanılır. Çift SIM ile gecikme veya takılma varsa 134 ile 152 arasında geçişi deneyin; sonuç modem yazılımına bağlıdır."],
	"保存后需在网络锁定重新应用自动模式；已开启开机自动应用的卡也会在下次启动时采用此值。显式 4G／SA／NSA 模式不变。": ["After saving, reapply automatic mode in Network Locks. SIMs with apply-at-startup enabled also use this value at the next boot. Explicit 4G/SA/NSA modes are unchanged.", "Kaydettikten sonra Şebeke Kilitleri’nde otomatik modu yeniden uygulayın. Açılışta uygulama etkin SIM’ler de sonraki açılışta bu değeri kullanır. Açıkça seçilen 4G/SA/NSA modları değişmez."],
	"SIM2 默认关闭射频；手动开启或主动切换到 SIM2 会开启并记住选择。已有明确的射频设置和 SIM2 上网选择会保留。": ["SIM2 radio is off by default. Enabling it manually or selecting SIM2 as the data SIM turns it on and remembers the choice. Existing explicit radio settings and SIM2 data selections are preserved.", "SIM2 radyosu varsayılan olarak kapalıdır. Elle açmak veya veri SIM’i olarak SIM2’yi seçmek radyoyu açar ve seçimi kaydeder. Önceden kaydedilmiş radyo ayarları ve SIM2 veri seçimleri korunur."],
	'适配设置': ['Adapter settings', 'Bağdaştırıcı ayarları'],
	'主页刷新间隔（秒）': ['Home dashboard refresh interval (seconds)', 'Ana pano yenileme aralığı (saniye)'],
	'主页状态每 2–60 秒刷新，网速独立每秒刷新；旧设置不足 2 秒时按 2 秒处理。保存后重新进入主页生效。': ['Status refreshes every 2–60 seconds; speed refreshes independently every second. Older settings below 2 seconds use 2 seconds. Reopen Home after saving.', 'Durum 2–60 saniyede bir, hız bağımsız olarak her saniye yenilenir. 2 saniyenin altındaki eski ayarlar 2 saniye olarak uygulanır. Kaydettikten sonra Ana Sayfa’yı yeniden açın.'],
	'正在更新…': ['Updating…', 'Güncelleniyor…'],
	'读取失败，稍后重试': ['Read failed; retrying shortly', 'Okuma başarısız; kısa süre sonra yeniden denenecek'],
	'收件人': ['Recipient', 'Alıcı'],
	'会话': ['Conversations', 'Görüşmeler'],
	'当前驻网': ['Serving network', 'Bağlı olunan ağ'],
	'网络模式 · EN-DC': ['Network mode · EN-DC', 'Ağ modu · EN-DC'],
	'自动（5G/4G）': ['Automatic (5G/4G)', 'Otomatik (5G/4G)'],
	'仅 4G': ['4G only', 'Yalnızca 4G'],
	'仅 5G SA': ['5G SA only', 'Yalnızca 5G SA'],
	'仅 5G NSA': ['5G NSA only', 'Yalnızca 5G NSA'],
	'自动': ['Automatic', 'Otomatik'],
	'刷新锁定状态': ['Refresh lock status', 'Kilit durumunu yenile'],
	'开机自动应用': ['Apply at startup', 'Başlangıçta uygula'],
	'开机自动应用已': ['Apply at startup is ', 'Başlangıçta uygulama '],
	'关闭后只停止下次开机回放，已保存的网络模式、EN-DC、频段和小区配置不会被删除。': ['Turning this off only stops replay at the next boot; saved network mode, EN-DC, band and cell settings remain.', 'Kapatılması yalnızca sonraki açılışta yeniden uygulamayı durdurur; kayıtlı ağ modu, EN-DC, bant ve hücre ayarları korunur.'],
	'频段锁定': ['Band locking', 'Bant kilitleme'],
	'NR 频段': ['NR bands', 'NR bantları'],
	'LTE 频段': ['LTE bands', 'LTE bantları'],
	'应用 NR 频段': ['Apply NR bands', 'NR bantlarını uygula'],
	'应用 LTE 频段': ['Apply LTE bands', 'LTE bantlarını uygula'],
	'邻区与小区锁定': ['Neighbor cells and cell locking', 'Komşu hücreler ve hücre kilidi'],
	'锁定当前服务小区': ['Lock current serving cell', 'Geçerli hizmet hücresini kilitle'],
	'解除小区锁定': ['Unlock cell', 'Hücre kilidini kaldır'],
	'已锁定小区': ['Locked cells', 'Kilitli hücreler'],
	'解锁': ['Unlock', 'Kilidi kaldır'],
	' 小区锁定': [' cell lock', ' hücre kilidi'],
	' 的小区锁定': [' cell lock', ' hücre kilidi'],
	'应用后协议栈重启（SFUN），蜂窝会短暂断开；设置会持久保存，并在启用“开机自动应用”时由插件于 AT 就绪后回放。接入平台的射频前钩子时可无重启回放。频段全不选再点应用 = 恢复自动。': ['Applying restarts the radio stack (SFUN) and briefly interrupts cellular service. Settings are saved and replayed by the plugin after AT is ready when Apply at startup is enabled. A platform pre-radio hook can replay without a restart. Apply with no bands selected to restore automatic mode.', 'Uygulama radyo yığınını (SFUN) yeniden başlatır ve hücresel bağlantıyı kısa süre keser. Ayarlar kaydedilir ve Başlangıçta uygula etkinse AT hazır olduğunda eklenti tarafından yeniden uygulanır. Platformun radyo öncesi kancasıyla yeniden başlatmadan uygulanabilir. Otomatik moda dönmek için hiçbir bant seçmeden uygulayın.'],
	'正在后台应用': ['Applying in background', 'Arka planda uygulanıyor'],
	'协议栈会重启（SFUN），约半分钟': ['The radio stack will restart (SFUN), taking about 30 seconds', 'Radyo yığını yeniden başlayacak (SFUN), yaklaşık 30 saniye sürecek'],
	'协议栈会重启（SFUN），蜂窝断开约半分钟': ['The radio stack will restart (SFUN); cellular service will stop for about 30 seconds', 'Radyo yığını yeniden başlayacak (SFUN); hücresel bağlantı yaklaşık 30 saniye kesilecek'],
	'SFUN 重启约半分钟': ['SFUN restart takes about 30 seconds', 'SFUN yeniden başlatması yaklaşık 30 saniye sürer'],
	'SFUN 重启 + 重新驻网，约半分钟': ['SFUN restart and re-registration take about 30 seconds', 'SFUN yeniden başlatması ve ağa yeniden kayıt yaklaşık 30 saniye sürer'],
	'约半分钟': ['about 30 seconds', 'yaklaşık 30 saniye'],
	'自动回读状态': ['status will be read back automatically', 'durum otomatik olarak geri okunacak'],
	'NR 频段锁定': ['NR band lock', 'NR bant kilidi'],
	'LTE 频段锁定': ['LTE band lock', 'LTE bant kilidi'],
	'选择': ['Select', 'Seç'],
	'已后台执行': ['Started in background', 'Arka planda başlatıldı'],
	'已排队：另一项锁定正在应用（SFUN 重启中），随后自动生效': ['Queued: another lock is being applied during SFUN restart; this will take effect afterward', 'Sıraya alındı: SFUN yeniden başlarken başka bir kilit uygulanıyor; ardından etkinleşecek'],
	'正在确认 EN-DC 状态': ['Confirming EN-DC status', 'EN-DC durumu doğrulanıyor'],
	'正在确认开机自动应用': ['Confirming startup setting', 'Başlangıç ayarı doğrulanıyor'],
	'正在直读调制解调器（最多几秒）': ['Reading modem directly (a few seconds at most)', 'Modem doğrudan okunuyor (en fazla birkaç saniye)'],
	'频段设置已应用，实际频段以模组回读为准': ['Band setting applied; actual bands are shown by modem readback', 'Bant ayarı uygulandı; gerçek bantlar modem geri okumasında gösterilir'],
	'已刷新': ['Refreshed', 'Yenilendi'],
	'刷新失败': ['Refresh failed', 'Yenileme başarısız'],
	'暂无驻网数据': ['No serving-network data', 'Bağlı olunan ağ verisi yok'],
	'NR 服务小区': ['NR serving cell', 'NR hizmet hücresi'],
	'回读超时，请点「刷新锁定状态」': ['Readback timed out; select “Refresh lock status”', 'Geri okuma zaman aşımına uğradı; “Kilit durumunu yenile”yi seçin'],
	'状态已回读': ['Status confirmed', 'Durum doğrulandı'],
	'已生效（EN-DC 不需要重启协议栈）': ['Applied (EN-DC does not require a radio-stack restart)', 'Uygulandı (EN-DC için radyo yığını yeniden başlatılmaz)'],
	'状态回读超时，点「刷新锁定状态」确认': ['Status readback timed out; use “Refresh lock status” to confirm', 'Durum geri okuması zaman aşımına uğradı; doğrulamak için “Kilit durumunu yenile”yi kullanın'],
	'已锁': ['Locked', 'Kilitli'],
	'支持': ['supported', 'destekleniyor'],
	' 个会话': [' conversations', ' görüşme'],
	' 个': [' bands', ' bant'],
	'网络模式': ['Network mode', 'Ağ modu'],
	'关闭 EN-DC': ['Disable EN-DC', 'EN-DC’yi kapat'],
	'开启 EN-DC': ['Enable EN-DC', 'EN-DC’yi aç'],
	'关闭开机自动应用': ['Disable apply at startup', 'Başlangıçta uygulamayı kapat'],
	'开启开机自动应用': ['Enable apply at startup', 'Başlangıçta uygulamayı aç'],
	'关闭': ['Disabled', 'Kapalı'],
	'开启': ['Enabled', 'Açık'],
	'已开启': [' enabled', ' etkin'],
	'已关闭': [' disabled', ' devre dışı'],
	'应用': ['Apply', 'Uygula'],
	'恢复自动': ['Restore automatic', 'Otomatiğe dön'],
	'解除': ['Unlock', 'Kilidi kaldır'],
	'正在解除': ['Unlocking', 'Kilit kaldırılıyor'],
	'解锁失败': ['Unlock failed', 'Kilit kaldırılamadı'],
	'已后台解除': ['Unlock started in background', 'Kilit kaldırma arka planda başlatıldı'],
	'重新驻网': ['re-registering', 'yeniden ağa kaydoluyor'],
	'回读状态': ['read back status', 'durumu geri oku'],
	'基础': ['Basic', 'Temel'],
	'注册/信号': ['Registration/signal', 'Kayıt/sinyal'],
	'承载': ['Bearer', 'Taşıyıcı'],
	'工程模式': ['Engineering mode', 'Mühendislik modu'],
	'AT 命令（↑↓ 翻历史，Enter 发送）': ['AT command (↑↓ history, Enter to send)', 'AT komutu (↑↓ geçmiş, göndermek için Enter)'],
	'发送': ['Send', 'Gönder'],
	'清屏': ['Clear screen', 'Ekranı temizle'],
	'就绪': ['Ready', 'Hazır'],
	'会话历史（点击复用）': ['Session history (click to reuse)', 'Oturum geçmişi (yeniden kullanmak için tıklayın)'],
	'无输出': ['No output', 'Çıktı yok'],
	'错误': ['Error', 'Hata'],
	'AT 通道正忙，命令未发出': ['AT channel busy; command not sent', 'AT kanalı meşgul; komut gönderilmedi'],
	'空': ['Empty', 'Boş'],
	'收件人：号码，如 10086 或 +86...': ['Recipient: number, e.g. 10086 or +86...', 'Alıcı: numara, ör. 10086 veya +86...'],
	'刷新': ['Refresh', 'Yenile'],
	'从 SIM 同步': ['Sync from SIM', 'SIM’den eşitle'],
	'SIM 存储': ['SIM storage', 'SIM depolama'],
	'SIM 存储已满；自动归档尚未释放空间，请检查短信服务。': ['SIM storage is full; automatic archiving has not freed space. Check the SMS service.', 'SIM depolaması dolu; otomatik arşivleme henüz yer açmadı. SMS hizmetini kontrol edin.'],
	'清空本地池': ['Clear local pool', 'Yerel havuzu temizle'],
	'加载中': ['Loading', 'Yükleniyor'],
	'选择左侧会话，或直接在下方输入号码发送。': ['Select a conversation on the left, or enter a number below to send.', 'Soldan bir görüşme seçin veya göndermek için aşağıya bir numara girin.'],
	'短信内容（Enter 发送，Shift+Enter 换行）': ['Message (Enter to send, Shift+Enter for newline)', 'Mesaj (göndermek için Enter, yeni satır için Shift+Enter)'],
	'发送走 AT+CMGS（PDU 模式）；通道忙会提示重试。删除单条：在气泡上右键（手机长按）。': ['Sending uses AT+CMGS (PDU mode); retry if the channel is busy. To delete one message, right-click its bubble (long-press on mobile).', 'Gönderme AT+CMGS (PDU modu) kullanır; kanal meşgulse yeniden deneyin. Bir mesajı silmek için balona sağ tıklayın (mobilde uzun basın).'],
	'正在后台从 SIM 同步（AT+CMGL）': ['Syncing from SIM in background (AT+CMGL)', 'SIM’den arka planda eşitleniyor (AT+CMGL)'],
	'SIM 同步已开始，几秒后自动刷新': ['SIM sync started; refreshing shortly', 'SIM eşitlemesi başladı; birazdan yenilenecek'],
	'清空本地短信池': ['Clear local SMS pool', 'Yerel SMS havuzunu temizle'],
	'只删本地文件，SIM 上的不动': ['Only local files will be deleted; messages on the SIM remain.', 'Yalnızca yerel dosyalar silinir; SIM’deki mesajlar korunur.'],
	'清空': ['Clear', 'Temizle'],
	'删除这条短信': ['Delete this SMS', 'Bu SMS’i sil'],
	'删除后不可恢复': ['Deletion cannot be undone', 'Silme işlemi geri alınamaz'],
	'仅删本地': ['Local only', 'Yalnızca yerel'],
	'本地 + SIM': ['Local + SIM', 'Yerel + SIM'],
	'删除失败': ['Delete failed', 'Silme başarısız'],
	'已删除': ['Deleted', 'Silindi'],
	'已删除（仅本地）': ['Deleted (local only)', 'Silindi (yalnızca yerel)'],
	'删除': ['Delete', 'Sil'],
	'号码和内容都要填': ['Enter both a number and a message', 'Numara ve mesaj girin'],
	'发送中': ['Sending', 'Gönderiliyor'],
	'已发送，稍后自动刷新': ['Sent; refreshing shortly', 'Gönderildi; birazdan yenilenecek'],
	'发送失败': ['Send failed', 'Gönderme başarısız'],
	'AT 通道正忙，稍后重试': ['AT channel busy; retry shortly', 'AT kanalı meşgul; birazdan yeniden deneyin'],
	'池子是空的：收到/发出的短信会出现在这里，或点「从 SIM 同步」。': ['The pool is empty. Incoming and sent messages appear here, or select “Sync from SIM”.', 'Havuz boş. Gelen ve gönderilen mesajlar burada görünür veya “SIM’den eşitle”yi seçin.'],
	'我: ': ['Me: ', 'Ben: '],
	'这里定义插件与当前紫光 OpenWrt 的边界。修改后无需改动看板、AT、锁定或短信页面。': ['Configure how this plugin connects to the current Unisoc OpenWrt platform. Changes do not require editing the dashboard, AT, locks or SMS pages.', 'Bu eklentinin mevcut Unisoc OpenWrt platformuna nasıl bağlandığını yapılandırın. Değişiklikler panel, AT, kilit veya SMS sayfalarını düzenlemeyi gerektirmez.'],
	'平台适配': ['Platform adapter', 'Platform bağdaştırıcısı'],
	'AT 后端': ['AT backend', 'AT arka ucu'],
	'自动检测': ['Auto-detect', 'Otomatik algıla'],
	'atinout + 串口': ['atinout + serial port', 'atinout + seri port'],
	'自定义适配器': ['Custom adapter', 'Özel bağdaştırıcı'],
	'AT 串口': ['AT serial port', 'AT seri portu'],
	'自定义 AT 适配器': ['Custom AT adapter', 'Özel AT bağdaştırıcısı'],
	'可执行文件依次接收超时秒数和完整 AT 命令。它必须与平台拨号程序共享串口锁。': ['The executable receives a timeout in seconds and the complete AT command, in that order. It must share the serial lock with the platform dialer.', 'Yürütülebilir dosya sırayla saniye cinsinden zaman aşımını ve tam AT komutunu alır. Seri port kilidini platform arama programıyla paylaşmalıdır.'],
	'短信适配器': ['SMS adapter', 'SMS bağdaştırıcısı'],
	'实现 list、show、send、delete、sync 子命令；留空时自动查找 mu300-sms。': ['Implement the list, show, send, delete and sync subcommands; leave blank to find mu300-sms automatically.', 'list, show, send, delete ve sync alt komutlarını uygulayın; mu300-sms otomatik bulunsun diye boş bırakın.'],
	'短信池目录': ['SMS pool directory', 'SMS havuzu dizini'],
	'原生收件、面板和转发共用；更改后重启生效，已有短信不自动搬移。': ['Shared by native reception, the dashboard and forwarding. Restart after changing it; existing messages are not moved automatically.', 'Yerel alım, gösterge paneli ve yönlendirme aynı dizini kullanır. Değişiklikten sonra yeniden başlatın; mevcut iletiler otomatik taşınmaz.'],
	'蜂窝逻辑接口': ['Cellular logical interface', 'Hücresel mantıksal arabirim'],
	'蜂窝 IPv6 接口': ['Cellular IPv6 interface', 'Hücresel IPv6 arabirimi'],
	'蜂窝网卡': ['Cellular network device', 'Hücresel ağ aygıtı'],
	'留空则从 netifd 自动获取': ['Leave blank to detect from netifd', 'netifd’den otomatik algılamak için boş bırakın'],
	'LAN 网桥': ['LAN bridge', 'LAN köprüsü'],
	'Wi-Fi 网卡': ['Wi-Fi device', 'Wi-Fi aygıtı'],
	'USB 网卡': ['USB device', 'USB aygıtı'],
	'等待 AT 就绪上限（秒）': ['Maximum wait for AT readiness (seconds)', 'AT hazır olma üst bekleme süresi (saniye)'],
	'持久化状态目录': ['Persistent state directory', 'Kalıcı durum dizini'],
	'，': [', ', ', '],
	'。': ['.', '.'],
	'；': ['; ', '; '],
	'：': [': ', ': '],
	'（': ['(', '('],
	'）': [')', ')'],
	'？': ['?', '?'],
	'「': ['“', '“'],
	'」': ['”', '”']
};
Object.assign(DASH_I18N, {
  "关闭后该卡无法通话、收发短信或上网，设置会在重启后保留。": [
    "This SIM will have no calls, SMS or data. The setting persists after reboot.",
    "Bu SIM arama, SMS veya veri kullanamaz. Ayar yeniden başlatınca korunur."
  ],
  "将开启所选卡的射频，设置会在重启后保留。": [
    "Enable the selected SIM radio and keep this setting after reboot.",
    "Seçili SIM radyosunu aç ve ayarı yeniden başlatınca koru."
  ],
  "SIM 射频": [
    "SIM radios",
    "SIM radyoları"
  ],
  "射频": [
    "Radio",
    "Radyo"
  ],
  "分别控制每张卡的射频并记住设置。关闭后该卡无法通话、收发短信或上网；不会自动切换到另一张卡。": [
    "Control and remember RF for each SIM separately. When off, that SIM cannot make calls, send or receive SMS, or use data. The other SIM is not selected automatically.",
    "Her SIM radyosunu ayrı kontrol edin ve kaydedin. Kapalı SIM arama, SMS veya veri kullanamaz. Diğer SIM otomatik seçilmez."
  ],
  "设置会在重启后保留。关闭当前上网卡会中断蜂窝网络。": [
    "Saved across reboots. Turning off the data SIM disconnects cellular data.",
    "Yeniden başlatmada korunur. Veri SIM radyosunu kapatmak hücresel bağlantıyı keser."
  ],
  "开启射频": [
    "Enable radio",
    "Radyoyu aç"
  ],
  "关闭射频": [
    "Disable radio",
    "Radyoyu kapat"
  ],
  "已开启": [
    "Enabled",
    "Açık"
  ],
  "已关闭": [
    "Disabled",
    "Kapalı"
  ],
  "正在应用射频设置…": [
    "Applying radio settings…",
    "Radyo ayarları uygulanıyor…"
  ],
  "射频设置已应用并保存": [
    "Radio settings applied and saved",
    "Radyo ayarları uygulandı ve kaydedildi"
  ],
  "Enable the target SIM radio before switching": [
    "Enable the target SIM radio before switching",
    "Geçişten önce hedef SIM radyosunu açın"
  ],
  "This SIM radio is disabled by the user": [
    "This SIM radio is disabled by the user",
    "Bu SIM radyosu kullanıcı tarafından kapatılmış"
  ],
  "Invalid saved SIM radio policy": [
    "Invalid saved SIM radio policy",
    "Kaydedilmiş SIM radyo ayarları geçersiz"
  ],
  "Radio setting saved; modem command failed:": [
    "Radio setting saved; modem command failed:",
    "Radyo ayarı kaydedildi; modem komutu başarısız:"
  ],
  "Radio setting saved; RF readback not confirmed": [
    "Radio setting saved; RF readback not confirmed",
    "Radyo ayarı kaydedildi; modem durumu doğrulanamadı"
  ],
  "Radio setting saved; RF enable failed": [
    "Radio setting saved; RF enable failed",
    "Radyo ayarı kaydedildi; radyo açılamadı"
  ],
  "Radio setting saved; RF disable failed": [
    "Radio setting saved; RF disable failed",
    "Radyo ayarı kaydedildi; radyo kapatılamadı"
  ],
  "请先开启目标 SIM 的射频，再切换上网卡": [
    "Enable the target SIM radio before switching",
    "Geçişten önce hedef SIM radyosunu açın"
  ],
  "该卡射频已被用户禁用": [
    "This SIM radio is disabled by the user",
    "Bu SIM radyosu kullanıcı tarafından kapatılmış"
  ],
  "已保存的 SIM 射频设置无效": [
    "Invalid saved SIM radio policy",
    "Kaydedilmiş SIM radyo ayarları geçersiz"
  ],
  "设置已保存，但基带执行失败：": [
    "Radio setting saved; modem command failed:",
    "Radyo ayarı kaydedildi; modem komutu başarısız:"
  ],
  "设置已保存，但未能确认射频状态": [
    "Radio setting saved; RF readback not confirmed",
    "Radyo ayarı kaydedildi; modem durumu doğrulanamadı"
  ],
  "设置已保存，目标 SIM 尚未就绪": [
    "Radio setting saved; target SIM is not ready",
    "Radyo ayarı kaydedildi; hedef SIM henüz hazır değil"
  ],
  "设置已保存，但射频开启失败": [
    "Radio setting saved; RF enable failed",
    "Radyo ayarı kaydedildi; radyo açılamadı"
  ],
  "设置已保存，但射频关闭失败": [
    "Radio setting saved; RF disable failed",
    "Radyo ayarı kaydedildi; radyo kapatılamadı"
  ],
  "射频设置失败": [
    "Radio setting failed",
    "Radyo ayarı başarısız"
  ],
  "射频设置请求无效": [
    "Invalid radio request",
    "Radyo isteği geçersiz"
  ]
});

var DASH_KEYS = Object.keys(DASH_I18N).sort(function(a, b) { return b.length - a.length; });
var DASH_PATTERN = new RegExp(DASH_KEYS.map(function(k) { return k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|'), 'g');
function uiLanguage() {
	var lang = (L.env && L.env.lang) || document.documentElement.lang || navigator.language || 'en';
	if (lang === 'auto') lang = document.documentElement.lang || navigator.language || 'en';
	lang = String(lang).toLowerCase().replace('_', '-');
	return lang.indexOf('zh') === 0 ? 'zh' : lang.indexOf('tr') === 0 ? 'tr' : 'en';
}
function translate(text) {
	var lang = uiLanguage();
	if (lang === 'zh' || text == null) return String(text == null ? '' : text);
	var column = lang === 'tr' ? 1 : 0;
	return String(text).replace(DASH_PATTERN, function(key) { return DASH_I18N[key][column]; });
}
function localize(root) {
	if (uiLanguage() === 'zh' || !root) return;
	var walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), node;
	while ((node = walk.nextNode())) {
		if (node.parentElement && /^(SCRIPT|STYLE|TEXTAREA)$/.test(node.parentElement.tagName)) continue;
		var translated = translate(node.nodeValue);
		if (translated !== node.nodeValue) node.nodeValue = translated;
	}
	var elements = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
	elements.forEach(function(el) {
		[ 'title', 'placeholder', 'aria-label' ].forEach(function(attr) {
			if (el.hasAttribute && el.hasAttribute(attr)) el.setAttribute(attr, translate(el.getAttribute(attr)));
		});
	});
}
var menuObserver, menuRoot;
function localizeMenu() {
	/* Bootstrap builds #topmenu asynchronously, often after the view renders.
	 * Its parent dropdown uses href="#", so link-URL matching alone misses 蜂窝. */
	var root = document.getElementById('topmenu');
	if (!root || !root.querySelectorAll) return;
	var update = function() {
		if (uiLanguage() === 'zh') return;
		Array.prototype.forEach.call(root.querySelectorAll('a[href*="/admin/home"], a[href*="/admin/modem"]'), localize);
		Array.prototype.forEach.call(root.children, function(li) {
			var a = li.querySelector('a');
			if (a && a.textContent.trim() === '蜂窝') localize(a);
		});
	};
	if (menuRoot !== root) {
		if (menuObserver) menuObserver.disconnect();
		menuRoot = root;
		if (typeof MutationObserver !== 'undefined') {
			menuObserver = new MutationObserver(update);
			menuObserver.observe(root, { childList: true, subtree: true });
		}
	}
	update();
}

/* 大陆运营商 PLMN -> 名称；COPS 给数字格式时用它还原 */
var PLMN_CN = {
	'46000': '中国移动', '46002': '中国移动', '46004': '中国移动', '46007': '中国移动', '46008': '中国移动',
	'46001': '中国联通', '46006': '中国联通', '46009': '中国联通',
	'46003': '中国电信', '46005': '中国电信', '46011': '中国电信', '46012': '中国电信',
	'46015': '中国广电', '46020': '中国铁通'
};

function carrierName(op) {
	if (!op) return '--';
	var name = op.name || PLMN_CN[op.plmn] || op.plmn || '--';
	var key = String(name).toUpperCase().replace(/[\s_\-]/g, '');
	var aliases = [
		[['中国移动', '中国移动通信', 'CHINAMOBILE', 'CHNMOBILE', 'CMCC'], '中国移动'],
		[['中国联通', 'CHINAUNICOM', 'CHNUNICOM', 'UNICOM', 'CUCC'], '中国联通'],
		[['中国电信', 'CHINATELECOM', 'CHNCT', 'CHNCTLTE', 'CTCC'], '中国电信'],
		[['中国广电', 'CHINABROADNET', 'CHNBG', 'CBN', 'CHINABROADCASTINGNETWORK'], '中国广电']
	];
	for (var i = 0; i < aliases.length; i++)
		if (aliases[i][0].indexOf(key) >= 0) return translate(aliases[i][1]);
	return translate(name);
}

/* Staged snapshots retain only slow fields that have not completed yet.
 * Explicit null in a completed stage means unavailable, not stale data. */
function mergeCell(previous, next) {
	if (!next) return previous || null;
	if (!next.partial || !previous || next.error || previous.error || previous.card !== next.card)
		return Object.assign({}, next);
	var merged = Object.assign({}, next);
	['ident', 'qos'].forEach(function(k) { merged[k] = previous[k]; });
	if (next.neigh_pending) merged.neigh = previous.neigh;
	return merged;
}

/* 信号质量分级（阈值来自 ufi_tools 的 SignalQuality.kt），返回 CSS 颜色表达式 */
function qLabel(rsrp, rsrq, sinr) {
	if (rsrp == null && sinr == null && rsrq == null) return '未知';
	if (rsrp != null) {
		if (rsrp >= -90) return '优秀';
		if (rsrp >= -100) return '良好';
		if (rsrp >= -110) return '一般';
		return '较差';
	}
	if (sinr != null) {
		if (sinr >= 20) return '优秀';
		if (sinr >= 13) return '良好';
		if (sinr >= 0) return '一般';
		return '较差';
	}
	if (rsrq >= -8) return '优秀';
	if (rsrq >= -11) return '良好';
	if (rsrq >= -14) return '一般';
	return '较差';
}
function qCol(label) {
	switch (label) {
		case '优秀': return 'var(--success, #2FBF71)';
		case '良好': return 'color-mix(in oklab, var(--success, #7BC96F) 62%, var(--text, #444))';
		case '一般': return 'var(--warning, #F2B544)';
		case '较差': return 'var(--danger, #E25555)';
		default:     return 'var(--text-subtle, var(--text-light, #8A8F98))';
	}
}
/* 10 分制：RSRP 40% / RSRQ 25% / SINR 35%，锚点插值，缺项权重重分配 */
function interp(v, pts) {
	if (v == null) return null;
	for (var i = 0; i < pts.length - 1; i++)
		if (v <= pts[i][0])
			return pts[i][1] + (v - pts[i][0]) * (pts[i+1][1] - pts[i][1]) / (pts[i+1][0] - pts[i][0]);
	return pts[pts.length - 1][1];
}
function qScore(s) {
	var parts = [
		[ interp(s.rsrp, [ [ -120, 0 ], [ -110, 3.5 ], [ -100, 6.5 ], [ -90, 8.5 ], [ -80, 10 ] ]), 0.40 ],
		[ interp(s.rsrq, [ [ -20, 0 ], [ -14, 3.5 ], [ -11, 6.5 ], [ -8, 8.5 ], [ -3, 10 ] ]), 0.25 ],
		[ interp(s.sinr, [ [ -5, 0 ], [ 0, 3.5 ], [ 13, 6.5 ], [ 20, 8.5 ], [ 30, 10 ] ]), 0.35 ]
	];
	var sum = 0, w = 0;
	parts.forEach(function(p) { if (p[0] != null) { sum += p[0] * p[1]; w += p[1]; } });
	return w == 0 ? null : Math.max(0, Math.min(10, sum / w));
}

/* ---------------------------------------------------------------- 格式化 */
function esc(s) {
	return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
		return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
	});
}
function fmtBatteryCurrent(ua) {
	if (typeof ua !== 'number' || !Number.isFinite(ua)) return '--';
	var ma = Math.round(ua / 1000);
	return (ma > 0 ? '+' : '') + ma + 'mA';
}
function fmtBatterySummary(power) {
	var p = power || {};
	var current = typeof p.ua === 'number' && Number.isFinite(p.ua) ? p.ua : null;
	var voltage = typeof p.volt === 'number' && Number.isFinite(p.volt) && p.volt > 0 ? p.volt : null;
	var amps = current == null ? null : Math.abs(current) / 1000000;
	// Net battery current determines direction even while connected to USB.
	var state = current > 0 ? '充电' : current < 0 ? '放电' :
		({ Charging: '充电', Discharging: '放电', Full: '已充满', 'Not charging': '未充电' }[p.status] || '未知');
	var watts = amps != null && voltage != null ? (amps * voltage).toFixed(1) : '--';
	var amperes = amps == null ? '--' : amps.toFixed(amps > 0 && amps < 0.01 ? 3 : 2);
	var source = p.usb === true || p.usb === 1 ? 'USB供电' :
		p.usb === false || p.usb === 0 ? '电池供电' : '供电来源未知';
	return translate(state) + (uiLanguage() === 'zh' ? '' : ' ') + watts + 'W/' + amperes + 'A·' +
		(voltage == null ? '--' : voltage.toFixed(2)) + 'V·' + translate(source);
}
function fmtBytes(b) {
	if (b == null || isNaN(b)) return '--';
	var u = [ 'B', 'KB', 'MB', 'GB', 'TB' ], i = 0;
	while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
	return (b >= 100 ? b.toFixed(0) : b.toFixed(1)) + ' ' + u[i];
}
// Traffic plans use decimal GB; keep memory/rate formatting unchanged.
function fmtTrafficBytes(b) {
	if (b == null || b === '' || !Number.isFinite(Number(b)) || Number(b) < 0) return '--';
	b = Number(b);
	var u = [ 'B', 'KB', 'MB', 'GB', 'TB' ], i = 0;
	while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
	var value = Number(b.toFixed(i === 0 ? 0 : 2));
	if (value >= 1000 && i < u.length - 1) { value = 1; i++; }
	return value + ' ' + u[i];
}
function fmtRate(bps) {
	if (bps == null || isNaN(bps) || bps < 0) return '--';
	var u = [ 'B/s', 'KB/s', 'MB/s', 'GB/s' ], i = 0;
	while (bps >= 1024 && i < u.length - 1) { bps /= 1024; i++; }
	return (bps >= 100 ? bps.toFixed(0) : bps.toFixed(1)) + ' ' + u[i];
}
function fmtUptime(s) {
	if (s == null) return '--';
	var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
	if (d > 0) return d + ' 天 ' + h + ' 小时';
	if (h > 0) return h + ' 小时 ' + m + ' 分';
	return m + ' 分';
}

/* ------------------------------------------------------------------ 样式 */
var CSS = `
/* Bootstrap exposes a different token family. Only activate this bridge when
 * Aurora's --surface token is absent, so existing Aurora styling wins intact.
 * Bootstrap's data-darkmode switch updates these aliases without a reload. */
html.mud-bootstrap-theme{--surface:var(--background-color-high);--surface-sunken:var(--background-color-low);--brand-subtle:color-mix(in srgb,var(--primary-color-high) 10%,var(--background-color-high));--hairline:var(--border-color-low);--text:var(--text-color-high);--text-muted:var(--text-color-medium);--text-subtle:var(--text-color-low);--brand:var(--primary-color-high);--success:var(--success-color-high);--warning:var(--warn-color-high);--danger:var(--error-color-high);--info:var(--primary-color-high);--on-brand:var(--on-primary-color);--hover-faint:var(--background-color-medium)}
.mud{color:var(--text,#222);font-size:.85rem;line-height:1.45}
.mud *{box-sizing:border-box}
.mud-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:10px;margin-top:6px}
.mud-card{background:var(--surface,var(--background-alt,var(--background,#fff)));border:1px solid var(--hairline,var(--border,#e3e6ea));border-radius:calc(var(--radius-base,.5rem) + .375rem);padding:14px 16px;box-shadow:var(--app-shadow-sm,0 1px 3px rgba(0,0,0,.04));transition:border-color .15s}
.mud-card:hover{border-color:color-mix(in oklab,var(--brand,var(--primary,#2f7bf6)) 30%,var(--hairline,var(--border,#e3e6ea)))}
.mud .mud-sim-toggle,.mud .mud-sim-toggle:hover{background:transparent}
.mud-card>h3{margin:0 0 8px;font-size:.7rem;font-weight:600;color:var(--text-muted,var(--text-light,#787d85));letter-spacing:.08em}
.mud-card>h3::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6));margin-right:7px;vertical-align:1px}
.mud-hero{display:flex;flex-wrap:wrap;gap:12px 28px;align-items:center;background:var(--brand-subtle,var(--surface,#fff));margin-bottom:12px;padding:16px 20px}
.mud-sec{padding:2px 2px 6px}
.mud-sec>h3{margin:16px 0 10px;font-size:.7rem;font-weight:600;color:var(--text-muted,var(--text-light,#787d85));letter-spacing:.08em}
.mud-sec>h3::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6));margin-right:7px;vertical-align:1px}
.mud-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:0 28px}
.mud-client-cols{grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px 28px;margin-top:16px;align-items:start}
.mud-client-cols>div{min-width:0}
.mud-client-cols .mud-cli .t{flex-wrap:wrap;overflow-wrap:anywhere}
.mud-client-cols .mud-cli .s{overflow-wrap:anywhere}
.mud-body>.mud-sec+.mud-sec{border-top:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 60%,transparent);margin-top:14px;padding-top:2px}
/* 首屏骨架：卡片正常占位，只让待填字段呼吸；第一份快照到达后停止。 */
@keyframes mudpulse{0%,100%{opacity:1}50%{opacity:.35}}
.mud-booting .mud-v,.mud-booting .mud-kpi b,.mud-booting .mud-rsrp,.mud-booting .mud-rat,.mud-booting .mud-temp span{animation:mudpulse 1.1s ease-in-out infinite}
/* 紧凑键值行：键与值相邻排布（不两端对齐拉开），用于驻网参照等 */
.mud-srvline{display:flex;flex-wrap:wrap;gap:4px 10px;padding:2px 0;font-size:.84rem}
.mud-srvline .k{color:var(--text-muted,var(--text-light,#777));flex:0 0 auto}
.mud-srvline .v{font-variant-numeric:tabular-nums;font-weight:500}
.mud-charts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.mud-chart{background:transparent;border-radius:var(--radius-base,.5rem);padding:2px 4px 0}
.mud-chart .t{display:flex;align-items:baseline;gap:8px}
.mud-chart .t b{font-size:1.2rem;font-weight:700;font-variant-numeric:tabular-nums}
.mud-chart .t span{font-size:.72rem;color:var(--text-muted,var(--text-light,#777))}
.mud-chart .c{height:40px;margin-top:2px}
.mud-chart .c svg{display:block;width:100%;height:100%}
.mud-lockbtn{padding:0 10px;border-radius:99px;border:1px solid var(--hairline,var(--border,#ccc));background:var(--surface,var(--background,#fff));color:var(--text,#222);font-size:.72rem;cursor:pointer;line-height:1.7}
.mud-lockbtn.on,.mud-lockbtn:active{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-lockbtn.locked{opacity:.45;pointer-events:none;background:var(--surface-sunken,rgba(127,127,127,.1));color:var(--text-muted,var(--text-light,#888));border-color:transparent}
.mud-hero-l{flex:1 1 260px;min-width:0;display:flex;flex-direction:column;gap:5px}
.mud-hero-r{flex:0 0 auto;text-align:right;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.mud-rat{font-size:1.7rem;font-weight:700;line-height:1.1;letter-spacing:.01em}
.mud-op{color:var(--text-muted,var(--text-light,#777));font-size:.85rem}
.mud-cellline{font-size:.78rem;color:var(--text-muted,var(--text-light,#777));font-variant-numeric:tabular-nums;line-height:1.55}
.mud-rsrp{font-size:2.3rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.mud-rsrp small{font-size:.9rem;font-weight:500}
.mud-chips{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}
.mud-imei-label{display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap}
.mud-imei-active{display:inline-flex;align-items:center;padding:1px 6px;border-radius:99px;font-size:.68rem;font-weight:600;line-height:1.4;color:var(--success,#2fbf71);background:color-mix(in srgb,var(--success,#2fbf71) 14%,transparent);white-space:nowrap}
.mud-imei-active[hidden]{display:none}
.mud-tag{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--surface-sunken,rgba(127,127,127,.1));font-size:.74rem;font-weight:600;font-variant-numeric:tabular-nums}
.mud-q{display:inline-block;min-width:3em;padding:1px 8px;border-radius:99px;font-size:.74rem;font-weight:600;text-align:center}
.mud-rows{display:grid;gap:2px}
.mud-r{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 55%,transparent)}
.mud-r:last-child{border-bottom:none}
.mud-k{color:var(--text-muted,var(--text-light,#777));flex:0 0 auto}
.mud-v{font-variant-numeric:tabular-nums;text-align:right;word-break:break-all;font-weight:500}
.mud-bars{display:inline-flex;align-items:flex-end;gap:2px;height:16px;margin-left:8px;vertical-align:baseline}
.mud-bars i{width:3px;border-radius:1px;background:var(--hairline,var(--border,#ccc))}
.mud-bars i.on{background:currentColor}
.mud-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(122px,1fr));gap:10px;margin-bottom:10px}
.mud-kpi{background:var(--surface-sunken,rgba(127,127,127,.06));border-radius:var(--radius-base,.5rem);padding:9px 12px}
.mud-kpi b{display:block;font-size:1.05rem;font-variant-numeric:tabular-nums;font-weight:700;line-height:1.25}
.mud-kpi span{font-size:.68rem;color:var(--text-muted,var(--text-light,#777))}
.mud-sub{font-size:.68rem;color:var(--text-subtle,var(--text-light,#999));font-variant-numeric:tabular-nums}
.mud-meter{height:5px;border-radius:3px;background:var(--surface-sunken,rgba(127,127,127,.15));overflow:hidden;margin:3px 0 1px}
.mud-meter i{display:block;height:100%;border-radius:3px}
.mud-freq{display:flex;align-items:center;font-size:.76rem;font-variant-numeric:tabular-nums;padding:1px 0}
.mud-freq .mud-k{flex:0 0 2.4em}
.mud-manual-cell{margin:12px 0;max-width:720px;min-width:0}
.mud-manual-cell summary{cursor:pointer;padding:8px 0;font-size:.85rem;font-weight:600}
.mud-manual-cell form{margin:4px 0 12px}
.mud-manual-cell-fields{display:flex;flex-wrap:wrap;align-items:end;gap:12px}
.mud-manual-cell-fields label{display:flex;flex:1 1 140px;flex-direction:column;gap:6px;margin:0;min-width:0;font-size:.82rem}
.mud-manual-cell-fields input,.mud-manual-cell-fields select{box-sizing:border-box;width:100%;max-width:100%;min-width:0;min-height:38px;margin:0;padding:8px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit}
.mud-manual-cell-fields button{min-height:38px;margin:0;white-space:normal}
.mud-manual-cell-fields input:focus,.mud-manual-cell-fields select:focus{outline:2px solid var(--brand,var(--primary,#37a));outline-offset:1px}
.mud-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:7px 12px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-size:.82rem;cursor:pointer;user-select:none}
.mud-btn:active{transform:scale(.97)}
.mud-voice-row{align-items:center;gap:12px;padding:8px 0}
.mud-voice-row .mud-v{flex:1;text-align:right;min-width:0;overflow-wrap:anywhere}
.mud-btn.mud-voice-switch{flex:0 0 48px;min-height:36px;padding:6px;border:0;border-radius:18px;background:transparent;color:var(--text,#222)}
.mud-voice-switch:focus-visible{outline:2px solid var(--brand,var(--primary,#2f7bf6));outline-offset:2px}
.mud-voice-track{position:relative;display:block;width:36px;height:22px;flex:0 0 36px;border-radius:12px;background:var(--muted,#777);transition:background .18s}
.mud-voice-track:after{content:"";position:absolute;left:3px;top:3px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 3px #0003;transition:transform .18s}
.mud-voice-switch.on .mud-voice-track{background:var(--brand,var(--primary,#2f7bf6))}
.mud-voice-switch.on .mud-voice-track:after{transform:translateX(14px)}
@media(prefers-reduced-motion:reduce){.mud-voice-track,.mud-voice-track:after{transition:none}}

.mud-btn.on:not(.mud-voice-switch){background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-btn.warn{border-color:color-mix(in oklab,var(--danger,#E25555) 55%,transparent);color:var(--danger,#E25555)}
.mud-btn[disabled]{opacity:.4;pointer-events:none}
.mud-ctl{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:7px}
.mud-note{font-size:.72rem;color:var(--text-subtle,var(--text-light,#999));margin-top:7px}
.mud-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.8rem}
.mud-table th{font-weight:500;color:var(--text-muted,var(--text-light,#777));text-align:right;padding:1px 4px;border-bottom:1px solid var(--hairline,var(--border,#ddd));font-size:.72rem;position:sticky;top:0;background:var(--surface,var(--background-alt,var(--background,#fff)))}
.mud-table td{text-align:right;padding:3px 6px;border-bottom:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 5%,transparent)}
.mud-table th:first-child,.mud-table td:first-child{text-align:left}
.mud-scroll{max-height:230px;overflow:auto;border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 45%,transparent);border-radius:var(--radius-base,.5rem);padding:4px}
.mud-cli{padding:4px 8px;border-radius:var(--radius-base,.5rem);border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 55%,transparent);margin-bottom:5px}
.mud-cli .t{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mud-cli .s{font-size:.72rem;color:var(--text-muted,var(--text-light,#888));font-variant-numeric:tabular-nums;margin-top:1px}
/* ---- 聊天式短信 ---- */
.mud-chat{display:flex;gap:12px;min-height:420px}
.mud-convs{flex:0 0 240px;overflow:auto;max-height:520px}
.mud-conv{padding:7px 9px;border-radius:var(--radius-base,.5rem);cursor:pointer;margin-bottom:4px;border:1px solid transparent}
.mud-conv:hover{background:var(--hover-faint,rgba(127,127,127,.06))}
.mud-conv.sel{background:var(--brand-subtle,var(--surface-sunken,rgba(127,127,127,.08)));border-color:color-mix(in oklab,var(--brand,var(--primary,#2f7bf6)) 30%,transparent)}
.mud-conv .n{display:flex;justify-content:space-between;gap:6px;font-weight:600;font-size:.84rem}
.mud-conv .p{font-size:.74rem;color:var(--text-muted,var(--text-light,#888));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.mud-thread{flex:1;display:flex;flex-direction:column;min-width:0;border-left:1px solid var(--hairline,var(--border,#ddd));padding-left:12px}
.mud-msgs{flex:1;overflow:auto;max-height:460px;padding:4px 2px;display:flex;flex-direction:column;gap:6px}
.mud-bub{max-width:78%;padding:6px 11px;border-radius:calc(var(--radius-base,.5rem) + .25rem);font-size:.85rem;white-space:pre-wrap;word-break:break-word;align-self:flex-start;background:var(--surface-sunken,rgba(127,127,127,.08))}
.mud-bub.out{align-self:flex-end;background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-bub .tm{display:block;font-size:.64rem;opacity:.65;margin-top:2px;text-align:right;font-variant-numeric:tabular-nums}
.mud-comp{display:flex;gap:8px;margin-top:8px}
.mud-comp input,.mud-comp textarea{padding:7px 11px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-family:inherit}
.mud-comp input{flex:0 0 170px}
.mud-comp textarea{flex:1;resize:none;min-height:40px;max-height:120px}
@media(max-width:700px){.mud-chat{flex-direction:column}.mud-convs{flex:none;max-height:150px}.mud-thread{border-left:none;padding-left:0;border-top:1px solid var(--hairline,var(--border,#ddd));padding-top:8px}}
/* ---- 专业 AT 终端 ---- */
.mud-at-grid{display:grid;grid-template-columns:1fr 220px;gap:10px}
.mud-at-grid .mud-scroll{max-height:340px}
@media(max-width:700px){.mud-at-grid{grid-template-columns:1fr}.mud-term{height:300px}.mud-at-grid .mud-scroll{max-height:120px}}
.mud-term{font-family:var(--font-mono,monospace);font-size:.8rem;line-height:1.5;background:color-mix(in oklab,var(--surface,#14161a) 92%,var(--brand,#2f7bf6) 3%);color:var(--text,#d5d9de);border:1px solid var(--hairline,var(--border,#2a2d33));border-radius:var(--radius-base,.5rem);padding:12px;height:380px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.mud-term .ln-cmd{color:var(--brand,#6ab0ff);font-weight:600}
.mud-term .ln-ok{color:var(--success,#57c98a);font-weight:600}
.mud-term .ln-err{color:var(--danger,#ff7b72);font-weight:600}
.mud-term .ln-data{color:var(--text,#d5d9de)}
.mud-term .ln-meta{color:var(--text-subtle,#7d8590);font-style:italic}
.mud-term .ln-ms{float:right;color:var(--text-subtle,#7d8590);font-size:.7rem}
.mud-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text-subtle,#8A8F98);margin-right:6px;vertical-align:1px}
.mud-dot.on{background:var(--success,#2FBF71)}
.mud-temp{display:inline-flex;gap:5px;flex-wrap:wrap}
.mud-temp span{padding:1px 8px;border-radius:var(--radius-base,.5rem);background:var(--surface-sunken,rgba(127,127,127,.08));font-variant-numeric:tabular-nums;font-size:.76rem}
.mud-at-in{display:flex;gap:7px;margin-bottom:7px}
.mud-at-in input{flex:1;min-width:0;padding:6px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-family:var(--font-mono,monospace)}
.mud-out{font-family:var(--font-mono,monospace);font-size:.78rem;white-space:pre-wrap;background:var(--surface-sunken,rgba(127,127,127,.07));border-radius:var(--radius-base,.5rem);padding:9px;max-height:260px;overflow:auto;margin:7px 0 0}
.mud-chiprow{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}
.mud-chip{padding:2px 9px;border-radius:99px;border:1px solid var(--hairline,var(--border,#ccc));font-size:.72rem;font-family:var(--font-mono,monospace);cursor:pointer}
.mud-chip.on{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-sms-item{padding:6px 8px;border-radius:var(--radius-base,.5rem);border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 60%,transparent);margin-bottom:6px;cursor:pointer}
.mud-sms-item:hover{background:var(--hover-faint,rgba(127,127,127,.05))}
.mud-sms-top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mud-badge{display:flex;align-items:center;justify-content:center;font-size:.68rem;padding:0 7px;min-width:20px;height:20px;box-sizing:border-box;border-radius:99px;background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
/* 手机端 hero 与锁定页一致：信息块在上、RSRP 块自然换行到下一行（右对齐） */
@media(max-width:600px){
.mud-hero{gap:8px}
.mud-hero-l{flex:1 1 100%}
.mud-hero-r{flex:1 0 100%;flex-direction:row;justify-content:space-between;align-items:baseline;text-align:left}
.mud-rsrp{font-size:1.9rem}
.mud-chips{justify-content:flex-end}}
/* ---- 顶部 toast 与按钮忙碌态（各页共用的反馈框架） ---- */
.mud-toasts{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;width:max-content;max-width:min(92vw,560px)}
.mud-toast{pointer-events:auto;display:flex;align-items:center;gap:9px;padding:9px 16px;border-radius:99px;background:var(--surface,var(--background,#fff));border:1px solid var(--hairline,var(--border,#ddd));box-shadow:0 6px 24px rgba(0,0,0,.14);font-size:.82rem;color:var(--text,#222);animation:mudtoast-in .22s ease-out;max-width:100%}
.mud-toast.out{animation:mudtoast-out .25s ease-in forwards}
.mud-toast .mud-tico{flex:0 0 auto;width:8px;height:8px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6))}
.mud-toast.success .mud-tico{background:var(--success,#2FBF71)}
.mud-toast.error .mud-tico{background:var(--danger,#E25555)}
.mud-toast.busy .mud-tico{width:12px;height:12px;background:transparent;border:2px solid color-mix(in oklab,var(--brand,#2f7bf6) 30%,transparent);border-top-color:var(--brand,#2f7bf6);animation:mudspin .7s linear infinite}
@keyframes mudtoast-in{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:none}}
@keyframes mudtoast-out{to{opacity:0;transform:translateY(-8px)}}
@keyframes mudspin{to{transform:rotate(360deg)}}
.mud-toast.notify{flex-direction:row;align-items:center;max-width:340px;text-align:left;border-radius:calc(var(--radius-base,.5rem) + .375rem)}
.mud-toast.notify .mud-nb{display:flex;flex-direction:column;gap:2px;min-width:0}
.mud-toast.notify .mud-nb b{font-size:.82rem;font-weight:700}
.mud-toast.notify .mud-nb span{font-size:.76rem;color:var(--text-muted,var(--text-light,#888));overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.mud-progress.is-loading::before{content:'';display:inline-block;margin-right:6px;vertical-align:middle}
.mud-locks .mud-lock-heading{display:flex;align-items:center;gap:8px;min-width:0}
.mud-locks .mud-lock-heading::before{flex-shrink:0;margin-right:0}
.mud-locks .mud-heading-label{flex:1 1 auto;min-width:0}
/* Keep an identical title-side slot for idle, loading and errors. No row is
 * inserted or removed on each live AT poll, including on narrow screens. */
.mud-locks .mud-lock-progress{flex:0 0 11em;max-width:50%;min-width:0;height:1.4em;line-height:1.4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.7rem;font-weight:400;letter-spacing:normal;text-align:right;color:var(--text-muted,var(--text-light,#777))}
.mud-locks .mud-lock-progress:empty{visibility:hidden}
.mud-locks .mud-hero .mud-heading-label{flex:0 1 auto}
.mud-locks .mud-hero .mud-lock-progress{text-align:left}
.mud-progress.is-loading::before,.mud-btn .mud-spin,.mud-lockbtn .mud-spin{flex:0 0 auto;width:12px;height:12px;border-radius:50%;border:2px solid color-mix(in oklab,currentColor 30%,transparent);border-top-color:currentColor;animation:mudspin .7s linear infinite}
.mud-btn.busy,.mud-lockbtn.busy{pointer-events:none;opacity:.75}
/* ---- 主题化对话框（替代浏览器 confirm/alert） ---- */
.mud-dlg-wrap{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.42);animation:mudfade-in .16s ease-out;padding:20px}
.mud-dlg{background:var(--surface,var(--background,#fff));border:1px solid var(--hairline,var(--border,#ddd));border-radius:calc(var(--radius-base,.5rem) + .5rem);box-shadow:0 18px 50px rgba(0,0,0,.28);max-width:420px;width:100%;padding:18px 20px 16px;animation:muddlg-in .2s cubic-bezier(.2,.9,.3,1.15)}
.mud-dlg h4{margin:0 0 8px;font-size:.95rem;font-weight:700;color:var(--text,#222)}
.mud-dlg .mud-dlg-msg{font-size:.84rem;line-height:1.6;color:var(--text-muted,var(--text-light,#666));white-space:pre-wrap;word-break:break-word}
.mud-dlg .mud-dlg-btns{display:flex;gap:8px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}
/* 短信气泡的删除按钮：红色垃圾桶，平时隐淡、悬停显形；正文留出右侧空间防重叠 */
.mud-bub{position:relative;padding-right:28px}
.mud-del{position:absolute;top:2px;right:2px;display:flex;align-items:center;justify-content:center;width:24px;height:24px;padding:0;border:none;background:transparent;color:var(--danger,#E25555);opacity:.4;cursor:pointer;border-radius:50%}
.mud-del:hover{opacity:.9;background:color-mix(in oklab,var(--danger,#E25555) 12%,transparent)}
.mud-del svg{display:block}
/* 预览 -> 全文回填的过渡动画，消除首载的生硬跳变 */
@keyframes mudfadein{from{opacity:.25}to{opacity:1}}
.mud-bub .bd.fadein{animation:mudfadein .25s ease-out}
@keyframes muddlg-in{from{opacity:0;transform:scale(.94) translateY(10px)}to{opacity:1;transform:none}}
@keyframes mudfade-in{from{opacity:0}to{opacity:1}}
/* 手机端锁定页 hero：RSRP 数字左对齐（其余屏幕保持右对齐） */
@media(max-width:600px){
.mud-rsrp{text-align:left}}
`;

function injectCss() {
	localizeMenu();
	// Discard our own aliases before probing, otherwise the second LuCI page
	// would mistake this bridge for Aurora and turn it off.
	document.documentElement.classList.remove('mud-bootstrap-theme');
	var theme = getComputedStyle(document.documentElement);
	var hasAuroraTokens = theme.getPropertyValue('--surface').trim() ||
		getComputedStyle(document.body).getPropertyValue('--surface').trim();
	document.documentElement.classList.toggle('mud-bootstrap-theme',
		!hasAuroraTokens && !!theme.getPropertyValue('--background-color-high').trim());
	if (document.getElementById('mud-style')) return;
	var st = document.createElement('style');
	st.id = 'mud-style';
	st.textContent = CSS;
	document.head.appendChild(st);
}
function v(id) { return document.getElementById('mud-' + id); }
function set(id, text, color) {
	var e = v(id);
	if (!e) return;
	e.textContent = (text == null || text === '') ? '--' : text;
	if (color !== undefined) e.style.color = color;
}
function spark(el, arr, min, max, win) {
	if (!el || !arr || arr.length < 2) return;
	var w = 100, h = 34, padX = 1, padY = 3, baseY = h - padY, pts = [];
	for (var i = 0; i < arr.length; i++) {
		pts.push([ padX + i / (win - 1) * (w - 2 * padX),
			baseY - Math.max(0, Math.min(1, (arr[i] - min) / (max - min || 1))) * (h - 2 * padY) ]);
	}
	/* Catmull-Rom 转三次贝塞尔：折线变平滑曲线 */
	var d = 'M' + pts[0][0].toFixed(1) + ',' + pts[0][1].toFixed(1);
	for (var i = 0; i < pts.length - 1; i++) {
		var p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
		// Catmull-Rom can overshoot even when every sample is inside the SVG.
		// Bound controls to their segment and leave room for the stroke itself.
		var lo = Math.min(p1[1], p2[1]), hi = Math.max(p1[1], p2[1]);
		var c1y = Math.max(lo, Math.min(hi, p1[1] + (p2[1] - p0[1]) / 6));
		var c2y = Math.max(lo, Math.min(hi, p2[1] - (p3[1] - p1[1]) / 6));
		d += 'C' + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + ',' + c1y.toFixed(1) +
			' ' + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + ',' + c2y.toFixed(1) +
			' ' + p2[0].toFixed(1) + ',' + p2[1].toFixed(1);
	}
	var last = pts.length - 1;
	var area = d + ' L' + pts[last][0].toFixed(1) + ',' + baseY + ' L' + pts[0][0].toFixed(1) + ',' + baseY + ' Z';
	var gid = 'mudg-' + (el.id || Math.floor(Math.random() * 1e6));
	el.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
		'<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
		'<stop offset="0" stop-color="currentColor" stop-opacity=".32"/>' +
		'<stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>' +
		'<path d="' + area + '" fill="url(#' + gid + ')"/>' +
		'<path d="' + d + '" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>';
}

/* 邻区表（主页与网络锁定页共用）：NR 置顶按 RSRP 排序，行尾带锁定按钮。
 * lockedCell 是 lock_get 的 cell 字段（如 "nr:627264,501"），命中行显示灰色"已锁定"。
 * 点击事件由页面用事件委托绑定（[data-lock] 属性："<rat>:<arfcn>,<pci>"）。 */
function neighborRows(c, lockedCell) {
	var nb = (c && c.neigh) || [];
	nb.sort(function(a, b) {
		if ((a.rat == 'nr') != (b.rat == 'nr')) return a.rat == 'nr' ? -1 : 1;
		return (b.rsrp || -999) - (a.rsrp || -999);
	});
	if (!nb.length)
		return '<tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">' + esc(translate('暂无邻区数据')) + '</td></tr>';
	var lk = Array.isArray(lockedCell) ? lockedCell.join('|') : (lockedCell || '');
	return nb.map(function(n) {
		var l = qLabel(n.rsrp, n.rsrq, n.sinr);
		var key = n.rat + ':' + n.arfcn + ',' + n.pci;
		var isLocked = lk.split('|').indexOf(key) >= 0;
		var nrBand = n.band != null && n.band !== 0 ? 'n' + esc(n.band) :
			(Array.isArray(n.band_candidates) && n.band_candidates.length ?
				n.band_candidates.map(function(b) { return 'n' + esc(b); }).join('/') : '--');
		var lteBand = n.band != null && n.band !== 0 ? 'B' + esc(n.band) : '--';
		return '<tr><td>' + (n.rat == 'nr' ? 'NR ' + nrBand : 'LTE ' + lteBand) + '</td>' +
			'<td>' + esc(n.pci != null ? n.pci : '--') + '</td>' +
			'<td>' + esc(n.arfcn != null ? n.arfcn : '--') + '</td>' +
			'<td style="color:' + qCol(l) + '">' + (n.rsrp != null ? n.rsrp.toFixed(1) : '--') + '</td>' +
			'<td>' + (n.rsrq != null ? n.rsrq.toFixed(1) : '--') + '</td>' +
			'<td>' + (n.sinr != null ? n.sinr.toFixed(1) : '--') + '</td>' +
			'<td><button class="mud-lockbtn' + (isLocked ? ' locked' : '') + '" data-lock="' + key + '"' +
			(isLocked ? ' disabled' : '') + '>' + esc(translate(isLocked ? '已锁定' : '锁定')) + '</button></td></tr>';
	}).join('');
}

/* ------------------------------------------------------------------ 反馈框架
 * M.toast(text, {type, timeout})：顶部弹出的统一提示，type = info|success|error|busy
 * （busy 自带转圈点，适合“正在…”进行态）。返回句柄 {update(text,type), close()}，
 * 长操作可以一路 update 下去而不堆叠。timeout=0 表示不自动消失。
 * M.busy(btn[, on])：给按钮加/去内嵌转圈并禁点；不传 on 则翻转。 */
function toast(text, opts) {
	opts = opts || {};
	if (!document.getElementById('mud-toasts')) {
		var w = document.createElement('div');
		w.id = 'mud-toasts';
		w.className = 'mud-toasts';
		document.body.appendChild(w);
	}
	var t = document.createElement('div');
	t.className = 'mud-toast ' + (opts.type || 'info');
	t.innerHTML = '<i class="mud-tico"></i><span></span>';
	t.lastChild.textContent = translate(text);
	document.getElementById('mud-toasts').appendChild(t);
	var timer = null, dead = false;
	var life = opts.timeout !== undefined ? opts.timeout : 3500;
	var arm = function() {
		if (timer) clearTimeout(timer);
		if (life > 0) timer = setTimeout(close, life);
	};
	var close = function() {
		if (dead) return;
		dead = true;
		if (timer) clearTimeout(timer);
		t.classList.add('out');
		setTimeout(function() { t.remove(); }, 260);
	};
	arm();
	return {
		update: function(text2, type2) {
			if (dead) return;
			t.lastChild.textContent = translate(text2);
			if (type2) t.className = 'mud-toast ' + type2;
			arm();
		},
		close: close
	};
}
function busy(btn, on) {
	if (!btn || !btn.classList) return;
	if (on === undefined) on = !btn.classList.contains('busy');
	if (on) {
		btn.classList.add('busy');
		if (!btn.querySelector('.mud-spin')) {
			var s = document.createElement('i');
			s.className = 'mud-spin';
			btn.insertBefore(s, btn.firstChild);
		}
	} else {
		// Repainting a button may replace className without removing its children.
		// Always clean up the spinner, even when the busy class was already lost.
		btn.classList.remove('busy');
		Array.prototype.forEach.call(btn.querySelectorAll('.mud-spin'), function(sp) { sp.remove(); });
	}
}

/* ------------------------------------------------------------------ 对话框
 * M.confirmBox(title, message, opts) -> Promise<boolean>：主题化确认框，
 * 取消/遮罩/Escape 都 resolve(false)，确定 resolve(true)，Enter 遵循当前按钮焦点。
 * M.alertBox(title, message, opts) -> Promise<true>：单按钮提示框。
 * opts: { danger:true 红色确认键, okText, cancelText, content: DOM Node }；danger 时默认焦点在取消上。 */
function dialog(opts) {
	opts = opts || {};
	return new Promise(function(resolve) {
		var wrap = document.createElement('div');
		wrap.className = 'mud-dlg-wrap';
		var withCancel = opts.cancelText !== null;
		wrap.innerHTML = '<div class="mud-dlg" role="dialog" aria-modal="true">' +
			'<h4></h4><div class="mud-dlg-msg"></div><div class="mud-dlg-btns">' +
			(withCancel ? '<button type="button" class="mud-btn" data-r="0"></button>' : '') +
			'<button type="button" class="mud-btn' + (opts.danger ? ' warn' : '') + '" data-r="1"></button>' +
			'</div></div>';
		wrap.querySelector('h4').textContent = translate(opts.title || '确认');
		wrap.querySelector('.mud-dlg-msg').textContent = translate(opts.message || '');
		if (opts.content instanceof Node) wrap.querySelector('.mud-dlg-msg').appendChild(opts.content);
		var btns = wrap.querySelectorAll('.mud-dlg-btns .mud-btn');
		btns[btns.length - 1].textContent = translate(opts.okText || '确定');
		if (withCancel) btns[0].textContent = translate(opts.cancelText || '取消');
		var done = function(r) {
			document.removeEventListener('keydown', onKey, true);
			wrap.remove();
			resolve(r);
		};
		var onKey = function(ev) {
			if (ev.key == 'Escape') { ev.preventDefault(); done(withCancel ? false : true); }
			else if (ev.key == 'Enter') { ev.preventDefault(); done(!(withCancel && document.activeElement === btns[0])); }
		};
		wrap.addEventListener('click', function(ev) {
			var b = ev.target.closest('.mud-dlg-btns button[data-r]');
			if (b) done(b.getAttribute('data-r') == '1');
			else if (ev.target === wrap && withCancel) done(false);
		});
		document.addEventListener('keydown', onKey, true);
		document.body.appendChild(wrap);
		/* 危险操作默认焦点给取消，防手滑回车 */
		(withCancel && opts.danger ? btns[0] : btns[btns.length - 1]).focus();
	});
}
/* 手机通知样式的横幅：标题（发件人）+ 两行预览，默认 6 s */
function notify(title, message, opts) {
	opts = opts || {};
	if (!document.getElementById('mud-toasts')) {
		var w = document.createElement('div');
		w.id = 'mud-toasts';
		w.className = 'mud-toasts';
		document.body.appendChild(w);
	}
	var t = document.createElement('div');
	t.className = 'mud-toast notify ' + (opts.type || 'info');
	t.innerHTML = '<i class="mud-tico"></i><div class="mud-nb"><b></b><span></span></div>';
	t.querySelector('b').textContent = translate(title);
	t.querySelector('span').textContent = message == null ? '' : String(message);
	document.getElementById('mud-toasts').appendChild(t);
	var life = opts.timeout !== undefined ? opts.timeout : 6000;
	var timer = life > 0 ? setTimeout(function() {
		t.classList.add('out');
		setTimeout(function() { t.remove(); }, 260);
	}, life) : null;
	t.addEventListener('click', function() {
		if (timer) clearTimeout(timer);
		t.remove();
	});
	return t;
}

/* 新短信监视（每个页面 render 时调用一次，内部单例）：
 * 每 5 s 读取本地池签名，仅变化时读取第 1 页；首次只记基线；
 * 之后出现更大的消息 id 且为收件（mt）时，按手机通知样式弹出
 * 「发件人 + 预览」。池子被清空（id 回落）时静默重建基线。 */
var smsWatch = null;
function watchSms() {
	if (smsWatch) return;
	smsWatch = { seen: [null,null], heads: [null,null], inflight: false };
	window.setInterval(function() {
		if(document.hidden || smsWatch.inflight)return;
		var slot=Number(selectedSlot()); smsWatch.inflight=true;
		Promise.resolve(callSmsHead()).then(function(head) {
			if (!head || head.error) throw new Error(head && head.error || 'Unable to read SMS state');
			if (Number(selectedSlot()) !== slot) return null;
			if (head.adapter === true) return callSmsList(1);
			var signature = JSON.stringify(head);
			if (smsWatch.heads[slot] === signature) return null;
			return callSmsList(1).then(function(list) { smsWatch.heads[slot] = signature; return list; });
		}).then(function(r) {
			if (!r) return;
			r = r || {};
			var msgs = r.msgs || [], max = 0;
			msgs.forEach(function(m) {
				var id = parseInt(m.id, 10) || 0;
				if (id > max) max = id;
			});
			if (!max) return;
			if (smsWatch.seen[slot] == null || max < smsWatch.seen[slot]) { smsWatch.seen[slot] = max; return; }
			if (max > smsWatch.seen[slot]) {
				msgs.forEach(function(m) {
					var id = parseInt(m.id, 10) || 0;
					if (id > smsWatch.seen[slot] && m.dir === 'mt')
						notify('SIM '+(slot+1)+' · 新短信 · ' + (m.peer || '未知号码'), m.preview || '', { type: 'success' });
				});
				smsWatch.seen[slot] = max;
			}
		}).catch(function() { /* Keep the baseline: retry the same read on the next tick. */ }).finally(function(){smsWatch.inflight=false;});
	}, 5000);
}

function confirmBox(title, message, opts) {
	opts = opts || {};
	opts.title = title;
	opts.message = message;
	return dialog(opts);
}
function alertBox(title, message, opts) {
	opts = opts || {};
	opts.title = title;
	opts.message = message;
	opts.cancelText = null;
	return dialog(opts);
}

/* 多选对话框：M.choiceBox(title, message, [{label, value, danger}], opts)
 * -> Promise(选中项的 value)；取消/遮罩/Escape resolve(undefined)。
 * choices 里的按钮从左到右排，danger 项红色。 */
function choiceBox(title, message, choices, opts) {
	opts = opts || {};
	return new Promise(function(resolve) {
		var wrap = document.createElement('div');
		wrap.className = 'mud-dlg-wrap';
		var btns = (choices || []).map(function(c, i) {
			return '<button type="button" class="mud-btn' + (c.danger ? ' warn' : '') +
				'" data-i="' + i + '"></button>';
		}).join('');
		wrap.innerHTML = '<div class="mud-dlg" role="dialog" aria-modal="true">' +
			'<h4></h4><div class="mud-dlg-msg"></div>' +
			'<div class="mud-dlg-btns">' + btns + '</div></div>';
		wrap.querySelector('h4').textContent = translate(title || '选择');
		wrap.querySelector('.mud-dlg-msg').textContent = translate(message || '');
		(choices || []).forEach(function(c, i) {
			wrap.querySelector('[data-i="' + i + '"]').textContent = translate(c.label || '?');
		});
		var done = function(v) {
			document.removeEventListener('keydown', onKey, true);
			wrap.remove();
			resolve(v);
		};
		var onKey = function(ev) {
			if (ev.key == 'Escape') { ev.preventDefault(); done(undefined); }
		};
		wrap.addEventListener('click', function(ev) {
			var b = ev.target.closest('button');
			if (b) done(choices[parseInt(b.getAttribute('data-i'), 10)].value);
			else if (ev.target === wrap) done(undefined);
		});
		document.addEventListener('keydown', onKey, true);
		document.body.appendChild(wrap);
		var first = wrap.querySelector('.mud-dlg-btns .mud-btn');
		if (first) first.focus();
	});
}

/* LuCI 的 require 把模块当类工厂：必须返回 baseclass 派生的类，加载后拿到的是它的实例 */
return baseclass.extend({
	callRates: callRates, callCells: callCells, mergeCell: mergeCell, callStatus: callStatus, callSignal: callSignal, callSysinfo: callSysinfo, callAct: callAct, callAt: callAt, callAtHist: callAtHist,
	callLockGet: callLockGet, callLockFresh: callLockFresh, callLockSet: callLockSet,
	callLockStatus: callLockStatus, waitLockJob: waitLockJob,
	callSmsList: callSmsList, callSmsShow: callSmsShow, callSmsSend: callSmsSend, callSmsJob: callSmsJob,
	callSmsDel: callSmsDel, callSmsSync: callSmsSync,
	callForwardGet: callForwardGet, callForwardStatus: callForwardStatus,
	callSimGet: callSimGet, simSelector: simSelector, selectedSlot: selectedSlot, selectViewedSlot: selectViewedSlot,
	callForwardSet: callForwardSet, callForwardTest: callForwardTest, callForwardClear: callForwardClear,
	callTrafficGet: callTrafficGet, callTrafficSet: callTrafficSet,
	callTrafficClear: callTrafficClear,
	callTtlGet: callTtlGet, callTtlSet: callTtlSet, callUsbGet: callUsbGet, callUsbSet: callUsbSet,
	callUsbNetList: callUsbNetList, callUsbNetAdd: callUsbNetAdd,
	carrierName: carrierName, qLabel: qLabel, qCol: qCol, qScore: qScore,
	esc: esc, fmtBatteryCurrent: fmtBatteryCurrent, fmtBatterySummary: fmtBatterySummary, fmtBytes: fmtBytes, fmtTrafficBytes: fmtTrafficBytes, fmtRate: fmtRate, fmtUptime: fmtUptime, PLMN_CN: PLMN_CN,
	uiLanguage: uiLanguage, translate: translate, localize: localize, localizeMenu: localizeMenu,
	injectCss: injectCss, v: v, set: set, spark: spark, neighborRows: neighborRows,
	toast: toast, busy: busy, confirmBox: confirmBox, alertBox: alertBox, choiceBox: choiceBox,
	notify: notify, watchSms: watchSms
});
