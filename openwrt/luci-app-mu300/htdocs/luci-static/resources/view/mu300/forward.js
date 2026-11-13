'use strict';
'require view';
'require poll';
'require mu300.common as M';

/* All destinations and rules are private to this authenticated editor. The
 * status poll never includes a URL, phone number, password or SMS body. */
var METHODS = { webhook: 'Webhook', dingtalk: '钉钉机器人', smtp: '邮件 SMTP', sms: '本机短信' };
var RESULTS = {
	service_rejected: '服务返回失败结果', service_answer_missing: '服务响应无法确认成功',
	idle: '暂无投递', sent: '发送成功', disabled: '转发未开启',
	self_forward_blocked: '已拦截向原发件人转发',
	message_too_long: '短信过长，未发送', storage_failed: '设备保存失败',
	source_unavailable: '短信服务暂不可用', power_unavailable: '设备暂未提供有效电池状态',
	invalid_config: '配置无效', invalid_forward_config: '配置无效',
    invalid_number_filter_regex: '号码正则表达式无效', invalid_text_filter_regex: '正文正则表达式无效',
    number_filter_empty: '号码白名单至少填写一条正则', text_filter_empty: '正文白名单至少填写一条正则',
    invalid_forward_filter: '短信筛选规则无效',
	invalid_destination: '目标地址无效', delivery_failed: '投递失败，请检查配置和设备网络',
	smtp_auth_failed: '邮箱服务器拒绝登录，请检查授权码'
};
var ERRORS = {
	forward_test_wait: '请等待 5 秒后再测试',
	forward_busy: '转发正在执行，请稍后再试', disabled: '转发未开启',
	invalid_config: '配置无效', invalid_forward_config: '配置无效',
    invalid_number_filter_regex: '号码正则表达式无效', invalid_text_filter_regex: '正文正则表达式无效',
    number_filter_empty: '号码白名单至少填写一条正则', text_filter_empty: '正文白名单至少填写一条正则',
    invalid_forward_filter: '短信筛选规则无效',
	invalid_destination: '目标地址无效', forward_storage_failed: '设备保存失败',
	sms_list_unavailable: '短信服务暂不可用', power_unavailable: '设备暂未提供有效电池状态'
};
function result(code) { return M.translate(RESULTS[code] || '暂无投递'); }
function lines(value) { return String(value || '').split(/\r?\n/).map(function(s) { return s.trim(); }).filter(Boolean); }
function count(value) { return lines(value).length; }
function preset(name) {
	var title = M.translate('短信') + ' {from}', content = '{text}\n{time} · {sim} · {device}';
	var presets = {
		json: ['https://example.com/sms', { from: '{from}', text: '{text}', time: '{time}', sim: '{sim}', device: '{device}' }],
		bark: ['https://api.day.app/push', { device_key: 'YOUR_KEY', title: title, body: content, group: 'MU300' }],
		pushplus: ['https://www.pushplus.plus/send', { token: 'YOUR_TOKEN', title: title, content: content, template: 'txt' }],
		serverchan: ['https://sctapi.ftqq.com/YOUR_SENDKEY.send', 'title={from}&desp={text}%0A{time}%20{sim}'],
		telegram: ['https://api.telegram.org/botYOUR_TOKEN/sendMessage', { chat_id: 'YOUR_CHAT_ID', text: title + '\n' + content }],
		wecom: ['https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY', { msgtype: 'text', text: { content: title + '\n' + content } }],
		dingtalk: ['https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN', { msgtype: 'text', text: { content: title + '\n' + content } }]
	};
	return presets[name];
}
function validPhones(value, max) {
	var a = lines(value), seen = {};
	return a.length <= max && a.every(function(p) {
		var key = p.replace(/^\+/, '');
		if (!/^\+?[0-9]{3,32}$/.test(p) || seen[key]) return false;
		seen[key] = true; return true;
	});
}

return view.extend({
	load: function() { return L.resolveDefault(M.callForwardGet('shared'), {}); },
	render: function(data) {
		M.injectCss();
		M.localizeMenu();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<style>
.mud-fwd-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.mud-fwd-grid>.mud-card,.mud-fwd-channel{min-width:0}
.mud-fwd-field{display:flex;flex-direction:column;min-width:0;gap:6px;margin:12px 0}
.mud-fwd-field>label,.mud-fwd-label{font-size:.79rem;overflow-wrap:anywhere;color:var(--text-muted,var(--text-light,#777))}
.mud .mud-fwd-field input,.mud .mud-fwd-field textarea,.mud .mud-fwd-field select{box-sizing:border-box;width:100%;min-width:0;max-width:100%;min-height:38px;margin:0;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);padding:8px 10px;background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit}
.mud-fwd-field textarea{min-height:90px;resize:vertical}
.mud-fwd-toggle{display:flex;align-items:center;gap:9px;margin:10px 0;font-size:.82rem;line-height:1.45;cursor:pointer}
.mud-fwd-toggle input{accent-color:var(--brand,var(--primary,#2f7bf6))}
.mud-fwd-row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.mud-fwd-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.mud-fwd-state{font-weight:650}.mud-fwd-state.on{color:var(--success,#2fbf71)}
.mud-fwd-channel[hidden],.mud-fwd-error[hidden]{display:none}
.mud-fwd-error{color:var(--danger,#d45);margin-top:10px}
.mud-fwd-pair{display:flex;flex-wrap:wrap;align-items:flex-end;gap:0 12px}
.mud-fwd-pair>.mud-fwd-field{flex:1 1 14rem}
.mud-fwd-pair>.mud-fwd-field:last-child{flex:1 1 10rem}
.mud-fwd-history{max-height:20rem;overflow:auto;overscroll-behavior:contain;margin-top:12px}
.mud-fwd-event{display:grid;grid-template-columns:minmax(8rem,1fr) minmax(6rem,1fr) minmax(0,2fr);gap:8px;padding:10px 0;border-bottom:1px solid var(--hairline,var(--border,#8883));font-size:.82rem;overflow-wrap:anywhere}
@media(max-width:480px){.mud-fwd-event{grid-template-columns:1fr 1fr}.mud-fwd-event>:last-child{grid-column:1/-1}}
@media(max-width:760px){.mud-fwd-grid{grid-template-columns:minmax(0,1fr)}.mud-fwd-pair{display:grid;grid-template-columns:minmax(0,1fr)}.mud-fwd-actions .mud-btn{flex:1}}
</style>
<section class="mud-card" style="margin-bottom:14px">
 <h3>转发配置</h3>
 <div class="mud-fwd-pair">
  <div class="mud-fwd-field"><label for="mud-fwd-mode">配置模式</label><select id="mud-fwd-mode"><option value="shared">所有 SIM 共用</option><option value="per_sim">按 SIM 独立配置</option></select></div>
  <div class="mud-fwd-field"><label for="mud-fwd-profile">正在编辑</label><select id="mud-fwd-profile"><option value="shared">共用配置 / 电源通知</option><option value="sim0">SIM 1</option><option value="sim1">SIM 2</option></select></div>
 </div>
 <div class="mud-note">独立模式按收到短信的卡选择渠道和筛选规则，不受上网卡切换影响。电源通知始终使用共用配置。</div>
 <div class="mud-note" id="mud-fwd-profile-note"></div>
</section>
<div class="mud-fwd-grid">
 <section class="mud-card">
  <div class="mud-fwd-row"><h3>短信转发</h3><span class="mud-fwd-state" id="mud-fwd-state">--</span></div>
  <div class="mud-note" id="mud-fwd-last">暂无投递</div>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-fwd-enabled">启用当前配置</label>
  <div class="mud-fwd-field"><label for="mud-fwd-method">转发方式</label>
   <select id="mud-fwd-method"><option value="webhook">Webhook</option><option value="dingtalk">钉钉机器人</option><option value="smtp">邮件 SMTP</option><option value="sms">本机短信</option></select></div>
  <div class="mud-fwd-field"><label for="mud-fwd-template_language">模板语言</label>
   <select id="mud-fwd-template_language"><option value="zh">简体中文</option><option value="en">English</option><option value="tr">Türkçe</option></select></div>
  <div class="mud-note">保存后用于转发标题、提示文字和电源通知；短信原文及设备备注保持不变。</div>
  <div class="mud-fwd-channel" data-method="webhook">
   <div class="mud-fwd-field"><label for="mud-fwd-webhook_url">HTTPS 地址</label><input id="mud-fwd-webhook_url" type="url" autocomplete="off" placeholder="https://example.com/hook"></div>
   <div class="mud-fwd-field"><label for="mud-fwd-preset">填入推送预设</label><select id="mud-fwd-preset"><option value="">自定义 / 兼容旧版</option><option value="json">JSON Webhook</option><option value="bark">Bark</option><option value="pushplus">PushPlus</option><option value="serverchan">ServerChan</option><option value="telegram">Telegram</option><option value="wecom">企业微信</option><option value="dingtalk">钉钉 Webhook</option></select></div>
   <div class="mud-note">预设只填入表单，请替换密钥后保存；需要钉钉加签时请选择“钉钉机器人”。</div>
   <div class="mud-fwd-pair">
    <div class="mud-fwd-field"><label for="mud-fwd-webhook_method">请求方法</label><select id="mud-fwd-webhook_method"><option>POST</option><option>GET</option></select></div>
    <div class="mud-fwd-field"><label for="mud-fwd-webhook_timeout">请求超时（秒，1–120）</label><input type="number" min="1" max="120" step="1" id="mud-fwd-webhook_timeout"></div>
   </div>
   <div class="mud-fwd-field"><label for="mud-fwd-webhook_content_type">正文格式</label><select id="mud-fwd-webhook_content_type"><option value="application/json">JSON</option><option value="application/x-www-form-urlencoded">Form URL-encoded</option><option value="text/plain">纯文本</option></select></div>
   <div class="mud-fwd-field"><label for="mud-fwd-webhook_body">正文模板</label><textarea rows="6" id="mud-fwd-webhook_body" spellcheck="false"></textarea></div>
   <div class="mud-note">占位符：{from}、{text}、{time}、{sim}、{device}、{kind}。JSON 占位符放在字符串内；按格式自动转义，绝不执行命令。</div>
   <div class="mud-note">GET 只发送 URL 参数；JSON 模板留空时沿用旧版 from/text/date 格式。仅支持公网 HTTPS，验证证书且不跟随重定向。</div>
   <div class="mud-fwd-field"><label for="mud-fwd-webhook_expect">成功响应条件（可选）</label><input id="mud-fwd-webhook_expect" maxlength="128" placeholder="errcode=0"></div>
   <div class="mud-note">留空时任意 HTTP 2xx 为成功；填写 errcode=0 或 ok=true 时，还需响应 JSON 顶层字段匹配。</div>
   <div class="mud-fwd-field"><label for="mud-fwd-webhook_headers">附加请求头（每行 Name: value）</label><textarea rows="3" id="mud-fwd-webhook_headers" autocomplete="off" spellcheck="false"></textarea></div>
  </div>
  <div class="mud-fwd-channel" data-method="dingtalk">
   <div class="mud-fwd-field"><label for="mud-fwd-dingtalk_webhook">机器人 Webhook 地址</label><input id="mud-fwd-dingtalk_webhook" type="url" autocomplete="off" placeholder="https://oapi.dingtalk.com/robot/send?..."></div>
   <div class="mud-fwd-field"><label for="mud-fwd-dingtalk_secret">加签密钥 · 留空保留</label><input id="mud-fwd-dingtalk_secret" type="password" autocomplete="new-password"></div>
   <label class="mud-fwd-toggle"><input id="mud-fwd-clear_dingtalk_secret" type="checkbox">清除加签密钥</label>
   <label class="mud-fwd-toggle"><input id="mud-fwd-dingtalk_forward_device_info" type="checkbox">附带设备信息</label>
  </div>
  <div class="mud-fwd-channel" data-method="smtp">
   <div class="mud-fwd-pair">
    <div class="mud-fwd-field"><label for="mud-fwd-smtp_host">SMTP 服务器域名</label><input id="mud-fwd-smtp_host" autocomplete="off"></div>
    <div class="mud-fwd-field"><label for="mud-fwd-smtp_port">端口 · 465 / 587</label><select id="mud-fwd-smtp_port"><option value="465">465 TLS</option><option value="587">587 STARTTLS</option></select></div>
   </div>
   <div class="mud-fwd-field"><label for="mud-fwd-smtp_username">发件邮箱</label><input id="mud-fwd-smtp_username" type="email" autocomplete="off"></div>
   <div class="mud-fwd-field"><label for="mud-fwd-smtp_to">收件邮箱</label><input id="mud-fwd-smtp_to" type="email" autocomplete="off"></div>
   <div class="mud-fwd-field"><label for="mud-fwd-smtp_password">授权码 / 密码 · 留空保留</label><input id="mud-fwd-smtp_password" type="password" autocomplete="new-password"></div>
   <label class="mud-fwd-toggle"><input id="mud-fwd-clear_smtp_password" type="checkbox">清除邮件配置</label>
   <label class="mud-fwd-toggle"><input id="mud-fwd-smtp_forward_device_info" type="checkbox">附带设备信息</label>
   <div class="mud-note">使用邮箱提供的 SMTP 授权码；始终验证 TLS 证书。</div>
  </div>
  <div class="mud-fwd-channel" data-method="sms">
   <div class="mud-fwd-field"><label for="mud-fwd-sms_to_phone">短信接收号码 · 每行一个，最多 3 个</label><textarea id="mud-fwd-sms_to_phone" placeholder="+8613800000000"></textarea></div>
   <label class="mud-fwd-toggle"><input id="mud-fwd-sms_forward_device_info" type="checkbox">附带设备信息</label>
   <div class="mud-note">经本机 SIM 转发，可能产生短信费用；每条最多 70 个 UCS-2 单元，不限制每日转发条数。</div>
  </div>
 </section>
 <section class="mud-card">
  <h3>来电提醒</h3>
  <label class="mud-fwd-toggle"><input id="mud-fwd-call_forward_enabled" type="checkbox">转发来电提醒</label>
  <div class="mud-note">默认关闭。两个槽位的来电按当前配置模式转发，包含卡源和来电号码；每通电话只提醒一次。</div>
  <h3 style="margin-top:22px">电源通知</h3>
  <div class="mud-note">电源通知使用共用配置；请同时开启该配置并设置渠道。</div>
  <label class="mud-fwd-toggle"><input id="mud-fwd-power_forward_enabled" type="checkbox">电源状态通知</label>
  <div class="mud-note" id="mud-fwd-power-note">充电状态变化，或电量跨过 5%、20%、40%、60%、80%、100% 时通知。</div>
  <div class="mud-note" id="mud-fwd-power-last"></div>
  <div class="mud-fwd-field"><label for="mud-fwd-nickname">设备备注</label><input id="mud-fwd-nickname" maxlength="255" autocomplete="off"></div>
  <div id="mud-fwd-regex-details" style="margin-top:18px">
   <h3>正则筛选（可选）</h3>
   <div class="mud-note" id="mud-fwd-legacy-filter-note" hidden>旧版号码与关键词黑名单已停用；如需继续拦截，请在下方正则筛选中设置。</div>
   <div class="mud-note">号码和正文的筛选须同时通过。仅影响新收到的短信，不影响电源或来电通知。</div>
   <div class="mud-fwd-field"><label for="mud-fwd-number_filter_mode">号码筛选</label><select id="mud-fwd-number_filter_mode"><option value="off">关闭筛选</option><option value="blacklist">黑名单：匹配则不转发</option><option value="whitelist">白名单：仅匹配才转发</option></select></div>
   <div class="mud-fwd-field"><label for="mud-fwd-number_filter_regex">号码正则 · 每行一条</label><textarea id="mud-fwd-number_filter_regex" spellcheck="false" placeholder="^106[0-9]+$"></textarea></div>
   <div class="mud-fwd-field"><label for="mud-fwd-text_filter_mode">正文筛选</label><select id="mud-fwd-text_filter_mode"><option value="off">关闭筛选</option><option value="blacklist">黑名单：匹配则不转发</option><option value="whitelist">白名单：仅匹配才转发</option></select></div>
   <div class="mud-fwd-field"><label for="mud-fwd-text_filter_regex">正文正则 · 每行一条</label><textarea id="mud-fwd-text_filter_regex" spellcheck="false" placeholder="OTP|code|kod|验证码"></textarea></div>
   <label class="mud-fwd-toggle"><input id="mud-fwd-filter_ignore_case" type="checkbox">忽略英文字母大小写</label>
   <div class="mud-note">每项最多 32 条，每条最多 512 字节。同项内任一行匹配即命中；白名单至少填写一条。</div>
   <div class="mud-note">使用 POSIX 扩展正则，不加 / 分隔符；数字用 [0-9]。匹配原始号码（包含 +）和完整正文，点号可跨行。</div>
  </div>
 </section>
</div>
<section class="mud-card" style="margin-top:14px">
 <div class="mud-fwd-row"><h3>测试与状态</h3></div>
 <div class="mud-note">设备独立执行，关闭页面仍生效；仅转发新短信，失败不自动重发。</div>
 <h3 style="margin-top:16px">最近投递记录</h3>
 <div class="mud-note">仅保留本次开机最近 30 次投递结果，不记录号码、地址或短信内容。</div>
 <div class="mud-fwd-history" id="mud-fwd-history" tabindex="0"></div>
 <div class="mud-fwd-error" id="mud-fwd-error" hidden></div>
 <div class="mud-fwd-actions">
  <button class="mud-btn on" id="mud-fwd-save">保存设置</button>
  <button class="mud-btn" id="mud-fwd-test">发送测试消息</button>
  <button class="mud-btn" id="mud-fwd-refresh">刷新状态</button>
  <button class="mud-btn" id="mud-fwd-clear">清空投递记录</button>
 </div>
</section>`;
		M.localize(root);
		this.root = root;
		this.data = data || {};
		this.profile = this.data.profile || 'shared';
		this.saved = this.data.config || null;
		this.q('mode').value = this.data.mode || 'shared';
		this.q('profile').value = this.profile;
		this.q('profile').querySelector('[value="sim1"]').disabled = this.data.sim_slots !== 2;
		this.status = this.data.status || {};
		this.wire();
		if (this.saved) this.fill(this.saved);
		else this.error(this.data.error || '读取失败，请刷新页面');
		// A legacy config has no saved template language. The UI offers its
		// current locale, but it must be saved before a matching test can run.
		this.savedPayload = this.saved && this.saved.template_language ? JSON.stringify(this.payload()) : null;
		this.paintStatus();
		this.paintMethod();
		var self = this;
		this._refresh = function() {
			if (document.hidden || !root.isConnected) return Promise.resolve();
			return self.refreshStatus();
		};
		poll.add(this._refresh, 5);
		return root;
	},
	q: function(id) { return this.root.querySelector('#mud-fwd-' + id); },
	error: function(s) {
		this.q('error').textContent = s ? M.translate(ERRORS[s] || s) : '';
		this.q('error').hidden = !s;
	},
	fill: function(c) {
		var self = this;
		[ 'enabled', 'call_forward_enabled', 'power_forward_enabled', 'smtp_forward_device_info',
			'dingtalk_forward_device_info', 'sms_forward_device_info' ].forEach(function(k) { self.q(k).checked = !!c[k]; });
		[ 'method', 'webhook_url', 'dingtalk_webhook', 'sms_to_phone', 'nickname' ].forEach(function(k) { self.q(k).value = c[k] || ''; });
		this.q('method').value = c.method || 'webhook';
		this.q('webhook_method').value = c.webhook_method || 'POST';
		this.q('webhook_content_type').value = c.webhook_content_type || 'application/json';
		this.q('webhook_timeout').value = c.webhook_timeout || 12;
		this.q('webhook_expect').value = c.webhook_expect || '';
		this.q('webhook_body').value = c.webhook_body || '';
		this.q('webhook_headers').value = c.webhook_headers || '';
		this.q('preset').value = '';
		this.q('template_language').value = c.template_language || M.uiLanguage();
		[ 'host', 'port', 'username', 'to' ].forEach(function(k) { self.q('smtp_' + k).value = c.smtp && c.smtp[k] || (k === 'port' ? '465' : ''); });
		[ 'smtp_password', 'dingtalk_secret' ].forEach(function(k) { self.q(k).value = ''; self.q('clear_' + k).checked = false; });
		this.q('smtp_password').placeholder = M.translate(c.smtp && c.smtp.password_configured ? '已配置，留空保留' : '请输入授权码');
		this.q('dingtalk_secret').placeholder = M.translate(c.dingtalk_secret_configured ? '已配置，留空保留' : '可选');
        [ 'number_filter_mode', 'text_filter_mode' ].forEach(function(k) { self.q(k).value = c[k] || 'off'; });
        [ 'number_filter_regex', 'text_filter_regex' ].forEach(function(k) { self.q(k).value = c[k] || ''; });
        this.q('filter_ignore_case').checked = !!c.filter_ignore_case;
        this.q('legacy-filter-note').hidden = !c.legacy_blacklist_present;
		this.paintMethod();
	},
	payload: function() {
		var self = this, p = { profile: this.profile, mode: this.q('mode').value };
		[ 'enabled', 'call_forward_enabled', 'power_forward_enabled', 'smtp_forward_device_info',
			'dingtalk_forward_device_info', 'sms_forward_device_info',
			'clear_smtp_password', 'clear_dingtalk_secret' ].forEach(function(k) { p[k] = self.q(k).checked ? 1 : 0; });
		[ 'method', 'template_language', 'webhook_url', 'dingtalk_webhook', 'dingtalk_secret', 'sms_to_phone',
			'smtp_host', 'smtp_port', 'smtp_username', 'smtp_to', 'smtp_password',
			'nickname' ].forEach(function(k) { p[k] = self.q(k).value.trim(); });
		[ 'sms_to_phone' ].forEach(function(k) { p[k] = lines(p[k]).join('\n'); });
		[ 'webhook_method', 'webhook_content_type', 'webhook_timeout', 'webhook_headers', 'webhook_body', 'webhook_expect' ].forEach(function(k) { p[k] = self.q(k).value; });
        [ 'number_filter_mode', 'text_filter_mode', 'number_filter_regex', 'text_filter_regex' ].forEach(function(k) { p[k] = self.q(k).value; });
        p.filter_ignore_case = self.q('filter_ignore_case').checked ? 1 : 0;
		if (p.clear_smtp_password) {
			p.smtp_host = ''; p.smtp_username = ''; p.smtp_to = ''; p.smtp_password = ''; p.smtp_port = '465';
		}
		return p;
	},
	validate: function(p) {
        for (var f of ['number', 'text']) {
            var mode = p[f + '_filter_mode'], rules = String(p[f + '_filter_regex'] || '').replace(/\r\n/g, '\n').split('\n').filter(function(s) { return s.length > 0; });
            if (['off', 'blacklist', 'whitelist'].indexOf(mode) < 0) return '短信筛选规则无效';
            if (mode === 'whitelist' && !rules.length) return f === 'number' ? '号码白名单至少填写一条正则' : '正文白名单至少填写一条正则';
            if (rules.length > 32 || rules.some(function(s) { return unescape(encodeURIComponent(s)).length > 512; })) return '正则条数或长度超过上限';
        }
		if (!validPhones(p.sms_to_phone, 3)) return '号码无效、重复或超过数量上限';
		if (p.power_forward_enabled && !this.status.power_supported) return '设备暂未提供有效电池状态';
		if (p.webhook_expect && !/^[A-Za-z_][A-Za-z0-9_]{0,63}=(-?[0-9]{1,16}|[a-z]{1,16})$/.test(p.webhook_expect)) return '成功响应条件格式无效';
		if (!/^\d+$/.test(p.webhook_timeout) || +p.webhook_timeout < 1 || +p.webhook_timeout > 120) return '请求超时必须为 1–120 秒';
		if (p.webhook_body && p.webhook_content_type === 'application/json') {
			try { JSON.parse(p.webhook_body); } catch (e) { return '正文模板必须是有效 JSON'; }
		}
		if (p.enabled && ((p.method === 'webhook' && !p.webhook_url) ||
			(p.method === 'dingtalk' && !p.dingtalk_webhook) ||
			(p.method === 'sms' && !p.sms_to_phone) ||
			(p.method === 'smtp' && !p.smtp_host))) return '请先配置当前转发渠道';
		return '';
	},
	paintMethod: function() {
		var method = this.q('method').value;
		this.root.querySelectorAll('.mud-fwd-channel').forEach(function(el) { el.hidden = el.getAttribute('data-method') !== method; });
		this.q('webhook_body').disabled = this.q('webhook_method').value === 'GET';
		var active = this.q('mode').value === 'shared' ? this.profile === 'shared' : this.profile !== 'shared';
		this.q('profile-note').textContent = M.translate(active ? '当前配置用于新短信转发。' : '当前配置不用于短信；保存后仍保留，可单独测试。');
	},
	paintStatus: function() {
		var s = this.status || {};
		this.q('state').textContent = M.translate(s.enabled ? '转发已开启' : '转发未开启');
		this.q('state').classList.toggle('on', !!s.enabled);
		this.q('last').textContent = M.translate('最近转发：') + result(s.last_result);
		this.q('power-last').textContent = M.translate('最近电源通知：') + result(s.power_last_result);
		var history = this.q('history');
		history.replaceChildren();
		(s.history || []).slice(0, 30).forEach(function(item) {
			var row = document.createElement('div'); row.className = 'mud-fwd-event';
			var kind = { sms: '短信', call: '来电提醒', power: '电源通知', test: '测试消息' }[item.kind] || '短信';
			[new Date(Number(item.time) * 1000).toLocaleString(), M.translate(kind) + (item.slot == null ? '' : ' · SIM ' + (Number(item.slot) + 1)) + ' · ' + M.translate(METHODS[item.method] || '未知'), result(item.result) + (item.http_code ? ' · HTTP ' + item.http_code : '')].forEach(function(value) {
				var el = document.createElement('span'); el.textContent = value; row.appendChild(el);
			});
			history.appendChild(row);
		});
		if (!history.childNodes.length) history.textContent = M.translate('暂无投递');
		this.q('power_forward_enabled').disabled = this.profile !== 'shared' || (!s.power_supported && !this.q('power_forward_enabled').checked);
		this.q('power-note').textContent = M.translate(this.profile !== 'shared' ? '电源通知请在共用配置中设置，独立模式下仍生效。' : s.power_supported ?
			'充电状态变化，或电量跨过 5%、20%、40%、60%、80%、100% 时通知。' : '设备暂未提供有效电池状态');
	},
	refreshStatus: function() {
		var self = this;
		if (this._inflight) return this._inflight;
		var profile = this.profile;
		this._inflight = L.resolveDefault(M.callForwardStatus(profile), {}).then(function(r) {
			if (self.root.isConnected && self.profile === profile && r.status) { self.status = r.status; self.paintStatus(); }
		}).finally(function() { self._inflight = null; });
		return this._inflight;
	},
	unload: function() { poll.remove(this._refresh); },
	wire: function() {
		var self = this;
		this.q('clear').onclick = function() {
            M.confirmBox('清空投递记录？', '只清除最近投递记录，不影响短信、转发配置或已处理状态。', { okText: '确定' }).then(function(yes) {
                if (!yes) return;
                self.q('clear').disabled = true;
                M.callForwardClear().then(function(r) {
                    if (!r.ok) { self.error(r.error || '设备保存失败'); return; }
                    self.error(''); return self.refreshStatus();
                }).catch(function() { self.error('读取失败，请刷新页面'); }).finally(function() { self.q('clear').disabled = false; });
            });
        };
		this.q('mode').onchange = function() { self.paintMethod(); };
		this.q('webhook_method').onchange = function() { self.paintMethod(); };
		this.q('preset').onchange = function() {
			var name = this.value, p = preset(name); if (!p) return;
			M.confirmBox('填入推送预设？', '将替换当前 Webhook 地址、正文及请求头，保存后才会生效。', { okText: '确定' }).then(function(yes) {
				if (!yes) return;
				self.q('webhook_url').value = p[0];
				self.q('webhook_method').value = 'POST';
				self.q('webhook_content_type').value = typeof p[1] === 'string' ? 'application/x-www-form-urlencoded' : 'application/json';
				self.q('webhook_body').value = typeof p[1] === 'string' ? p[1] : JSON.stringify(p[1], null, 2);
				self.q('webhook_expect').value = ({bark:'code=200',pushplus:'code=200',serverchan:'code=0',telegram:'ok=true',wecom:'errcode=0',dingtalk:'errcode=0'})[name] || '';
				self.q('webhook_headers').value = ''; self.paintMethod();
			});
		};
		this.q('profile').onchange = function() {
			var next = this.value; this.value = self.profile;
			var dirty = JSON.stringify(self.payload()) !== self.savedPayload;
			(dirty ? M.confirmBox('切换编辑配置？', '未保存的修改将被丢弃。', { okText: '确定' }) : Promise.resolve(true)).then(function(yes) {
				if (!yes) return;
				self.q('profile').disabled = true; self.q('save').disabled = true; self.q('test').disabled = true;
				L.resolveDefault(M.callForwardGet(next), {}).then(function(v) {
					if (!v.config) { self.error('读取失败，请刷新页面'); return; }
					self.profile = next; self.q('profile').value = next; self.q('mode').value = v.mode || 'shared';
					self.saved = v.config; self.status = v.status || {}; self.fill(v.config);
					self.savedPayload = JSON.stringify(self.payload()); self.paintStatus(); self.error('');
				}).finally(function() { self.q('profile').disabled = false; self.q('save').disabled = false; self.q('test').disabled = false; });
			});
		};
		this.q('method').onchange = function() { self.paintMethod(); };
		this.q('save').onclick = function() {
			var p = self.payload(), error = self.validate(p);
			if (error) { self.error(error); return; }
			M.confirmBox('保存短信转发设置？', '开启后只处理新收到的短信；历史短信不会补发。', { okText: '保存' }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('save'), toast = M.toast('正在保存短信转发设置…', { type: 'busy', timeout: 0 });
				M.busy(btn, true); self.q('profile').disabled = true; self.q('test').disabled = true; self.error('');
				L.resolveDefault(M.callForwardSet(JSON.stringify(p)), {}).then(function(r) {
					if (!r.ok) { self.error(r.line ? M.translate(ERRORS[r.error] || r.error) + ' · ' + M.translate('第 %d 行').replace('%d', String(r.line)) : r.error || '保存失败'); toast.update('保存失败', 'error'); }
					else {
						self.savedPayload = null;
						return L.resolveDefault(M.callForwardGet(self.profile), {}).then(function(v) {
							if (v.config) {
								self.saved = v.config;
								self.status = v.status || {};
								self.fill(v.config);
								self.savedPayload = JSON.stringify(self.payload());
								self.paintStatus();
							}
							toast.update('已保存', 'success');
						});
					}
				}).finally(function() {
					M.busy(btn, false); self.q('profile').disabled = false; self.q('test').disabled = false;
					setTimeout(function() { toast.close(); }, 3000);
				});
			});
		};
		this.q('test').onclick = function() {
			var p = self.payload();
			if (JSON.stringify(p) !== self.savedPayload) {
				self.error('请先保存修改再测试'); return;
			}
			if (!self.status.enabled) { self.error('请先开启并保存短信转发'); return; }
			var msg = p.method === 'sms' ? '将使用本机 SIM 向已保存号码发送，可能产生短信费用。' :
				'将向当前已保存渠道发送一条固定测试消息。';
			M.confirmBox('发送测试消息？', msg, { okText: '发送' }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('test'); M.busy(btn, true);
				L.resolveDefault(M.callForwardTest(self.profile), {}).then(function(r) {
					M.busy(btn, false);
					M.toast(r.ok ? '测试已开始，请稍后刷新状态' : M.translate('测试失败：') + M.translate(ERRORS[r.error] || '未知错误'),
						{ type: r.ok ? 'success' : 'error' });
					setTimeout(function() { self.refreshStatus(); }, 1500);
				});
			});
		};
		this.q('refresh').onclick = function() { self.refreshStatus(); };
	}
});
