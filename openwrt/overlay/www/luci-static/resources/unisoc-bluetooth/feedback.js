'use strict';
'require baseclass';
'require unisoc-bluetooth.i18n as I';

// Keep protocol codes in expandable details; the primary message explains the action.
var errors = {
 invalid_advertising_profile: ['广播定义格式不正确。', 'Invalid advertisement definition.', 'Geçersiz yayın tanımı.'],
 invalid_advertising_profiles: ['广播定义应为列表，最多保存 32 条。', 'Provide a list of up to 32 advertisement definitions.', 'En fazla 32 yayın tanımından oluşan liste girin.'],
 invalid_advertising_id: ['广播标识无效或重复。', 'Invalid or duplicate advertisement ID.', 'Geçersiz veya yinelenen yayın kimliği.'],
 invalid_advertising_name: ['请填写实验名称；广播名称不能超过 29 个 UTF-8 字节。', 'Enter an experiment label. The advertised name must fit in 29 UTF-8 bytes.', 'Deney etiketi girin. Yayın adı 29 UTF-8 baytını aşmamalıdır.'],
 invalid_advertising_data: ['请检查广播数据的标识、十六进制内容及重复项。', 'Check data identifiers, hexadecimal payloads and duplicate entries.', 'Veri kimliklerini, onaltılık içeriği ve yinelenen kayıtları kontrol edin.'],
 invalid_advertising_interval: ['广播间隔请填写 20 至 10240 毫秒的整数。', 'Enter an integer advertising interval from 20 to 10240 ms.', '20 ile 10240 ms arasında tam sayı yayın aralığı girin.'],
 invalid_advertising_timeout: ['运行时长请填写 0 至 65535 秒的整数，0 表示持续。', 'Enter a lifetime from 0 to 65535 seconds; 0 continues until stopped.', '0 ile 65535 saniye arasında tam sayı süre girin; 0 durdurulana kadar sürer.'],
 advertising_payload_too_long: ['广播数据超过 31 字节，请缩短 UUID 或数据内容。', 'Advertisement payload exceeds 31 bytes. Reduce UUIDs or data.', 'Yayın verisi 31 baytı aşıyor. UUID veya verileri azaltın.'],
 advertising_payload_empty: ['请至少填写广播名称、UUID 或一项数据。', 'Provide a name, UUID or data field.', 'Ad, UUID veya veri alanı girin.'],
 stop_advertising_before_edit: ['请先停止这条广播，再编辑或删除。', 'Stop this advertisement before editing or deleting it.', 'Düzenlemeden veya silmeden önce yayını durdurun.'],
 advertising_profile_not_found: ['这条广播定义不存在，请刷新列表。', 'Advertisement definition not found. Refresh the list.', 'Yayın tanımı bulunamadı. Listeyi yenileyin.'],
 advertising_not_supported: ['当前控制器没有提供 BLE 广播能力。', 'BLE advertising is not available on this controller.', 'Bu denetleyicide BLE yayın desteği yok.'],
 advertising_requires_power: ['请先手动开启蓝牙，再开始实验广播。', 'Turn on Bluetooth before starting a test advertisement.', 'Test yayınını başlatmadan önce Bluetooth açın.'],
 advertising_instances_full: ['控制器的广播实例已用完，请先停止一条广播。', 'All controller advertising slots are in use. Stop an advertisement first.', 'Tüm yayın yuvaları dolu. Önce bir yayını durdurun.'],
 advertising_session_ended: ['广播已被控制器结束，请检查蓝牙状态后重试。', 'The controller ended the advertisement. Check Bluetooth and retry.', 'Denetleyici yayını sonlandırdı. Bluetooth durumunu kontrol edin.'],
 'org.bluez.Error.InvalidLength': ['控制器拒绝了数据长度，请缩短广播内容。', 'The controller rejected the data length. Shorten the advertisement.', 'Denetleyici veri uzunluğunu reddetti. Yayını kısaltın.'],
 invalid_bindkey: ['BTHome 密钥需要 32 位十六进制字符。', 'A BTHome key must contain 32 hexadecimal characters.', 'BTHome anahtarı 32 onaltılık karakter olmalıdır.'],
 bthome_key_required: ['这是加密的 BTHome 设备，请在“管理设备”中填写密钥。', 'This BTHome device is encrypted. Enter its key under Manage device.', 'Bu BTHome cihazı şifreli. Cihazı yönet bölümüne anahtarını girin.'],
 bthome_authentication_failed: ['BTHome 数据验证失败，请核对设备密钥。', 'BTHome authentication failed. Check the device key.', 'BTHome doğrulaması başarısız. Cihaz anahtarını kontrol edin.'],
 invalid_bluetooth_backup: ['备份文件格式或大小不正确，请选择本页面导出的 JSON 文件。', 'Invalid backup format or size. Select a JSON backup exported by this page.', 'Yedek biçimi veya boyutu geçersiz. Bu sayfadan dışa aktarılan JSON yedeği seçin.'],
 invalid_bluetooth_template: ['模板格式无效或标识重复，请使用规定的模板 JSON。', 'The template format is invalid or its ID is duplicated. Use the documented template JSON.', 'Şablon biçimi geçersiz veya kimliği yinelenmiş. Belgelenen JSON biçimini kullanın.'],
 bluetooth_decoder_unavailable: ['设备解析程序暂不可用，请检查安装是否完整。原始广播仍可查看。', 'The device decoder is unavailable. Check installation completeness. Raw advertisements remain available.', 'Cihaz çözücüsü kullanılamıyor. Kurulumu kontrol edin. Ham yayınlar görüntülenebilir.'],
 bluetooth_services_timed_out: ['设备已连接，但服务发现超时，请确认设备未被其他程序占用后重试。', 'The device connected but service discovery timed out. Check for another application using it and retry.', 'Cihaz bağlandı ancak servis keşfi zaman aşımına uğradı. Başka uygulamaları kontrol edip tekrar deneyin.'],
 device_disconnected: ['设备连接已断开，请重新连接。', 'The device disconnected. Connect it again.', 'Cihaz bağlantısı kesildi. Yeniden bağlayın.'],
 invalid_report_intervals: ['最长上报间隔不能小于最短间隔。', 'The maximum report interval must not be shorter than the minimum.', 'En uzun rapor aralığı en kısadan az olamaz.'],
 invalid_proxy_key: ['代理密钥需要是 32 字节密钥的 Base64 编码，可点击“生成新密钥”。', 'Use a Base64-encoded 32-byte key, or select Generate new key.', 'Base64 kodlu 32 bayt anahtar kullanın veya Yeni anahtar üret seçin.'],
 proxy_requires_gateway_key_and_ble: ['请启用数据网关、选择 BLE 或双模，并填写代理加密密钥。', 'Enable the gateway, select BLE or both modes, and provide a proxy encryption key.', 'Ağ geçidini açın, BLE veya çift mod seçin ve vekil anahtarını girin.'],
 proxy_active_requires_allowlist: ['开启 HA 主动连接前，请填写允许连接的设备地址。', 'Set the device allowlist before enabling HA active connections.', 'HA aktif bağlantıları için önce izin verilen cihazları belirtin.'],
 proxy_bind_must_be_local: ['请选择明确的本机或局域网 IP，不能监听所有接口。', 'Select a specific local or LAN IP instead of all interfaces.', 'Tüm arayüzler yerine belirli bir yerel veya LAN IP seçin.'],
 bluetooth_proxy_start_failed: ['代理未能启动，请核对监听 IP、端口及系统依赖；现有蓝牙服务保留。', 'The proxy could not start. Check its bind IP, port and dependencies. Existing Bluetooth services remain.', 'Vekil başlatılamadı. IP, port ve bağımlılıkları kontrol edin. Mevcut Bluetooth servisleri korunur.'],
 invalid_command_value: ['控制数值或开关数据无效，请核对范围、步长和编码。', 'Invalid control value. Check the range, step and encoding.', 'Kontrol değeri geçersiz. Aralık, adım ve kodlamayı kontrol edin.'],
 invalid_command_id: ['控制标识无效或重复，请使用字母、数字、连字符或下划线。', 'The control ID is invalid or duplicated. Use letters, digits, hyphens or underscores.', 'Kontrol kimliği geçersiz veya yinelenmiş. Harf, rakam, tire veya alt çizgi kullanın.'],
 invalid_command_values: ['开关的开启和关闭数据必须填写，且不能相同。', 'ON and OFF data are required and must differ.', 'AÇIK ve KAPALI verileri gerekli ve farklı olmalıdır.'],
 bluetooth_invalid_response: ['操作返回格式异常，尚不能确认结果。请刷新状态查看；原始返回见技术详情。', 'The response format is invalid; the result is unconfirmed. Refresh status and inspect the details.', 'Yanıt biçimi geçersiz; sonuç doğrulanamadı. Durumu yenileyip ayrıntıları kontrol edin.'],
 bluetooth_service_unavailable: ['蓝牙管理服务暂不可用，请稍后刷新重试。', 'Bluetooth management is unavailable. Refresh and try again shortly.', 'Bluetooth yönetimi kullanılamıyor. Biraz sonra yenileyip tekrar deneyin.'],
 no_bluetooth_adapter: ['尚未检测到蓝牙控制器，请先点击“开启蓝牙”。', 'No Bluetooth adapter detected. Turn on Bluetooth first.', 'Bluetooth bağdaştırıcısı yok. Önce Bluetooth açın.'],
 invalid_adapter: ['所选控制器不可用，请重新选择。', 'The selected adapter is unavailable. Select it again.', 'Seçilen bağdaştırıcı yok. Tekrar seçin.'],
 bluetooth_operation_in_progress: ['另一项操作尚未结束，请等待结果后再试。', 'Another operation is still running. Wait for its result.', 'Başka bir işlem sürüyor. Sonucunu bekleyin.'],
 disable_gateway_before_power_off: ['请先关闭数据网关并保存，再关闭蓝牙。', 'Disable and save the data gateway before turning off Bluetooth.', 'Bluetooth kapatmadan önce ağ geçidini kapatıp kaydedin.'],
 gateway_owns_discovery_change_gateway_settings: ['数据网关正在持续扫描，请在网关设置中修改扫描方式或关闭网关。', 'The gateway owns continuous scanning. Change or disable it in gateway settings.', 'Sürekli taramayı ağ geçidi yönetiyor. Ağ geçidi ayarlarını değiştirin veya kapatın.'],
 device_not_found_scan_first: ['设备已不在列表中，请重新扫描。', 'The device is no longer available. Scan again.', 'Cihaz artık bulunamıyor. Tekrar tarayın.'],
 bluetooth_hard_blocked: ['蓝牙被硬件禁用，网页无法解除。', 'Bluetooth is blocked by hardware and cannot be unblocked here.', 'Bluetooth donanım tarafından engellenmiş; burada açılamaz.'],
 bluetooth_transport_start_failed: ['蓝牙驱动启动失败，请展开详情并检查系统日志。', 'Bluetooth transport failed to start. Check the details and system log.', 'Bluetooth sürücüsü başlatılamadı. Ayrıntıları ve sistem günlüğünü kontrol edin.'],
 bluetooth_transport_start_timed_out: ['蓝牙驱动启动超时，请检查系统日志后重试。', 'Bluetooth transport startup timed out. Check the system log and retry.', 'Bluetooth sürücüsü zaman aşımına uğradı. Günlüğü kontrol edip tekrar deneyin.'],
 bluetooth_power_transition_timed_out: ['未能确认蓝牙开关结果，请刷新状态后再试。', 'Bluetooth power change could not be confirmed. Refresh status before retrying.', 'Bluetooth güç değişimi doğrulanamadı. Yenileyip tekrar deneyin.'],
 bluetooth_adapter_removed: ['蓝牙控制器已断开，请重新选择或开启蓝牙。', 'The Bluetooth adapter was removed. Select an adapter or turn Bluetooth on.', 'Bluetooth bağdaştırıcısı çıkarıldı. Tekrar seçin veya Bluetooth açın.'],
 bluetooth_activation_failed: ['配置已保留，但蓝牙启动失败。请查看系统日志。', 'Settings were kept, but Bluetooth activation failed. Check the system log.', 'Ayarlar korundu ancak Bluetooth başlatılamadı. Sistem günlüğünü kontrol edin.'],
 bluetooth_alias_apply_failed: ['名称已保存，但尚未应用到蓝牙控制器，请重新保存重试。', 'The name was saved but could not be applied to the controller. Save again to retry.', 'Ad kaydedildi ancak denetleyiciye uygulanamadı. Tekrar kaydetmeyi deneyin.'],
 bluetooth_bus_unavailable: ['蓝牙系统服务暂不可用，请检查 BlueZ 和 D-Bus 服务。', 'Bluetooth services are unavailable. Check BlueZ and D-Bus.', 'Bluetooth servisleri kullanılamıyor. BlueZ ve D-Bus kontrol edin.'],
 pairing_request_expired: ['配对请求已结束或超时，请重新发起配对。', 'The pairing request ended or expired. Start pairing again.', 'Eşleştirme isteği bitti veya süresi doldu. Tekrar başlatın.'],
 invalid_passkey: ['请输入 0 至 999999 之间的配对码。', 'Enter a passkey between 0 and 999999.', '0 ile 999999 arasında eşleştirme kodu girin.'],
 pin_required: ['请输入对方设备的 PIN 码。', 'Enter the PIN from the other device.', 'Diğer cihazın PIN kodunu girin.'],
 invalid_bluetooth_address: ['蓝牙地址格式不正确，例如 AA:BB:CC:DD:EE:FF。', 'Enter a Bluetooth address such as AA:BB:CC:DD:EE:FF.', 'AA:BB:CC:DD:EE:FF biçiminde Bluetooth adresi girin.'],
 invalid_uuid: ['服务或特征 UUID 格式不正确。', 'The service or characteristic UUID is invalid.', 'Servis veya özellik UUID değeri geçersiz.'],
 invalid_hex: ['请输入成对的十六进制数字，例如 01 A2 FF。', 'Enter pairs of hexadecimal digits, such as 01 A2 FF.', '01 A2 FF gibi çift haneli onaltılık veri girin.'],
 mqtt_host_required: ['启用 MQTT 前请填写服务器地址。', 'Enter a server address before enabling MQTT.', 'MQTT açmadan önce sunucu adresi girin.'],
 invalid_mqtt_host: ['MQTT 服务器地址无效，请填写主机名或 IP 地址。', 'Enter a valid MQTT hostname or IP address.', 'Geçerli MQTT sunucu adı veya IP adresi girin.'],
 invalid_mqtt_topic: ['MQTT 主题前缀无效，请检查通配符和空字符。', 'The MQTT topic prefix is invalid. Check wildcards and empty characters.', 'MQTT konu öneki geçersiz. Joker ve boş karakterleri kontrol edin.'],
 webhook_url_required: ['启用 HTTP 转发前请填写接收地址。', 'Enter a destination URL before enabling HTTP forwarding.', 'HTTP iletimi için hedef URL girin.'],
 invalid_webhook_url: ['请填写有效的 HTTP 或 HTTPS 接收地址。', 'Enter a valid HTTP or HTTPS destination URL.', 'Geçerli HTTP veya HTTPS hedef URL girin.'],
 serial_not_connected: ['蓝牙串口尚未连接，请检查网关和串口配置。', 'The serial link is not connected. Check gateway and serial settings.', 'Seri bağlantı kurulmadı. Ağ geçidi ve seri ayarlarını kontrol edin.'],
 rfcomm_connection_failed: ['串口连接失败，请确认对方已开启 SPP 服务且通道正确。', 'Serial connection failed. Check the peer SPP service and channel.', 'Seri bağlantı başarısız. Karşı cihazın SPP servisini ve kanalını kontrol edin.'],
 rfcomm_connect_timeout: ['串口连接超时，请确认对方在线并支持 SPP。', 'Serial connection timed out. Check that the peer is online and supports SPP.', 'Seri bağlantı zaman aşımı. Karşı cihazın açık ve SPP destekli olduğunu kontrol edin.'],
 sdp_query_timed_out: ['查询串口服务超时，请确认对方可连接后重试。', 'Serial service lookup timed out. Check the peer and retry.', 'Seri servis sorgusu zaman aşımı. Karşı cihazı kontrol edip tekrar deneyin.'],
 characteristic_not_unique_or_not_found: ['未找到唯一的 BLE 特征，请检查服务和特征 UUID。', 'The BLE characteristic is missing or ambiguous. Check both UUIDs.', 'BLE özelliği yok veya belirsiz. Her iki UUID değerini kontrol edin.'],
 characteristic_not_writable: ['此 BLE 特征不支持写入。', 'This BLE characteristic is not writable.', 'Bu BLE özelliği yazılabilir değil.'],
 serial_bind_must_be_local: ['串口监听地址必须是本机或局域网接口的地址。', 'Bind the serial port to a local or LAN interface address.', 'Seri port için yerel veya LAN arayüz adresi kullanın.'],
 serial_port_must_be_unprivileged: ['串口 TCP 端口请使用 1024 以上，或填 0 仅在网页使用。', 'Use a TCP port of 1024 or above, or 0 for web-only access.', '1024 veya üzeri TCP portu, yalnız web için 0 kullanın.'],
 duplicate_serial_link: ['串口配置重复，请保留一条相同设备的配置。', 'Duplicate serial configuration. Keep one entry for the device.', 'Yinelenen seri ayarı. Cihaz için tek kayıt bırakın.'],
 'org.bluez.Error.NotReady': ['蓝牙尚未就绪，请先开启蓝牙并等待状态更新。', 'Bluetooth is not ready. Turn it on and wait for the status update.', 'Bluetooth hazır değil. Açın ve durum güncellemesini bekleyin.'],
 'org.bluez.Error.ConnectionAttemptFailed': ['连接未建立，对方未响应或当前不可连接。请确认对方状态后重试。', 'Connection could not be established. The peer did not respond or is not connectable. Check the peer and retry.', 'Bağlantı kurulamadı. Karşı cihaz yanıt vermedi veya bağlanabilir değil. Cihazı kontrol edip tekrar deneyin.'],
 'org.bluez.Error.InProgress': ['设备正在处理上一项请求，请稍后再试。', 'The device is processing another request. Try again shortly.', 'Cihaz başka bir isteği işliyor. Biraz sonra deneyin.'],
 'org.bluez.Error.Busy': ['蓝牙正在忙，请等待当前操作完成。', 'Bluetooth is busy. Wait for the current operation.', 'Bluetooth meşgul. Mevcut işlemi bekleyin.'],
 'org.bluez.Error.AuthenticationFailed': ['配对认证失败，请核对配对码后重新配对。', 'Pairing authentication failed. Check the passkey and pair again.', 'Eşleştirme doğrulaması başarısız. Kodu kontrol edip tekrar eşleştirin.'],
 'org.bluez.Error.AuthenticationRejected': ['对方拒绝了配对请求。', 'The other device rejected pairing.', 'Diğer cihaz eşleştirmeyi reddetti.'],
 'org.bluez.Error.AuthenticationCanceled': ['配对已取消。', 'Pairing was canceled.', 'Eşleştirme iptal edildi.'],
 'org.bluez.Error.AuthenticationTimeout': ['配对超时，请让对方保持可被发现后重试。', 'Pairing timed out. Make the peer discoverable and retry.', 'Eşleştirme zaman aşımı. Karşı cihazı görünür yapıp tekrar deneyin.'],
 'org.bluez.Error.NotSupported': ['对方或本机不支持这项蓝牙功能。', 'This Bluetooth feature is not supported by the peer or adapter.', 'Bu Bluetooth özelliği karşı cihaz veya bağdaştırıcı tarafından desteklenmiyor.'],
 'org.bluez.Error.Failed': ['蓝牙操作未成功，请确认对方在线并支持所选服务。', 'Bluetooth operation failed. Check that the peer is online and supports the service.', 'Bluetooth işlemi başarısız. Karşı cihazı ve servis desteğini kontrol edin.']
};
function error(raw) {
 raw = String(raw || '');
 var code = raw.split(': ')[0], row = errors[code];
 var lang = I.locale();
 return row ? row[lang.indexOf('zh') === 0 ? 0 : lang.indexOf('tr') === 0 ? 2 : 1] : I.t('error');
}
return baseclass.extend({
 error: error,
 details: function(raw) { return E('details', {'class':'bt-details'}, [E('summary',{},I.t('details')), E('pre',{},String(raw))]); },
 message: function(message, raw, kind) {
  return E('div', {'class':'bt-message bt-'+(kind || 'info'), role:kind === 'error' ? 'alert' : 'status'},
   [E('span',{},message)].concat(raw ? [this.details(raw)] : []));
 },
 state: function(value) { return I.t({running:'job_running', done:'job_done', failed:'job_failed'}[value] || 'working'); },
 event: function(x) {
  var d=x.data || {}, mac=d.Address || d.address || '', text=I.t('event_'+x.type);
  if(text === 'event_'+x.type) text=I.t('event_other');
  if(x.type === 'job') text=this.state(d.state);
  if(x.type === 'device') text=(d.Name || d.Alias || I.t('event_device')) +
   ' · '+I.t(d.Connected ? 'connected' : 'disconnected');
  if(x.type === 'serial_status') text=I.t('event_serial_status')+' · '+I.t(d.state === 'connected' ? 'connected' : 'disconnected');
  if(d.error) text+=' · '+error(d.error);
  return text+(mac ? ' · '+mac : '');
 }
});
