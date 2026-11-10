'use strict';
'require view';
'require poll';
'require rpc';
'require mu300.common as M';

/* Call notifications forward ringing / answered / ended / missed events to one
 * webhook. The channel may be inherited from the SMS forwarder, so one webhook
 * serves both. Nothing on this page talks to the modem. */
var callGet = rpc.declare({ object: 'mu300dash', method: 'call_get', expect: { '': {} } });
var callStatus = rpc.declare({ object: 'mu300dash', method: 'call_status', expect: { '': {} } });
var callSet = rpc.declare({ object: 'mu300dash', method: 'call_set', params: ['payload'], expect: { '': {} }, nobatch: true });
var callTest = rpc.declare({ object: 'mu300dash', method: 'call_test', expect: { '': {} }, nobatch: true });
var callClear = rpc.declare({ object: 'mu300dash', method: 'call_log_clear', expect: { '': {} }, nobatch: true });

var RESULTS = {
	idle: '暂无投递', sent: '发送成功', service_rejected: '服务返回失败结果',
	service_answer_missing: '服务响应无法确认成功', invalid_destination: '目标地址无效',
	delivery_failed: '投递失败，请检查配置和设备网络', storage_failed: '设备保存失败',
	invalid_config: '配置无效'
};
var PRESETS = {
	bark: ['https://api.day.app/push', { device_key: 'YOUR_KEY', title: '来自{device}-来电', body: '来电号码: {number}\n来电时间: {time}\n{tag}', group: 'CALL' }],
	pushplus: ['https://www.pushplus.plus/send', { token: 'YOUR_TOKEN', title: '来自{device}-来电', content: '来电号码: {number}\n来电时间: {time}\n{tag}\n\n本机号码: {self}\n运营商: {operator}\n信号: {signal}', template: 'txt' }],
	serverchan: ['https://sctapi.ftqq.com/YOUR_SENDKEY.send', 'title=来自{device}-来电&desp=来电号码:%20{number}%0A来电时间:%20{time}%0A{tag}%0A%0A运营商:%20{operator}%0A信号:%20{signal}'],
	telegram: ['https://api.telegram.org/botYOUR_TOKEN/sendMessage', { chat_id: 'YOUR_CHAT_ID', text: '来自{device}-来电\n来电号码: {number}\n来电时间: {time}\n{tag}' }],
	wecom: ['https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY', { msgtype: 'text', text: { content: '来自{device}-来电\n来电号码: {number}\n来电时间: {time}\n{tag}' } }],
	dingtalk: ['https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN', { msgtype: 'text', text: { content: '来自{device}-来电\n来电号码: {number}\n来电时间: {time}\n{tag}' } }]
};
function result(code) { return M.translate(RESULTS[code] || code || '暂无投递'); }
function lines(v) { return String(v || '').split(/\r?\n/).map(function(s) { return s.trim(); }).filter(Boolean); }

return view.extend({
	load: function() { return L.resolveDefault(callGet(), {}); },
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
 <h3>推送渠道</h3>
 <div class="mud-fwd-pair">
  <div class="mud-fwd-field"><label for="mud-call-source">渠道来源</label><select id="mud-call-source"><option value="sms">跟随短信转发（推荐）</option><option value="own">本页单独配置</option></select></div>
  <div class="mud-fwd-field"><label for="mud-call-preset">填入推送预设</label><select id="mud-call-preset"><option value="">自定义 / 兼容旧版</option><option value="json">JSON Webhook</option><option value="bark">Bark</option><option value="pushplus">PushPlus</option><option value="serverchan">ServerChan</option><option value="telegram">Telegram</option><option value="wecom">企业微信</option><option value="dingtalk">钉钉 Webhook</option></select></div>
 </div>
 <div class="mud-note" id="mud-call-source-note">跟随短信转发时，地址、请求方法、正文格式、超时和请求头都直接用短信那边的设置，这里只给通话事件单独准备正文模板。</div>
 <div class="mud-note">预设只填入表单，请把密钥换成自己的再保存。</div>
</section>
<div class="mud-fwd-grid">
 <section class="mud-card">
  <div class="mud-fwd-row"><h3>通话通知</h3><span class="mud-fwd-state" id="mud-call-state">--</span></div>
  <div class="mud-note" id="mud-call-last">暂无投递</div>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-call-enabled">启用通话通知</label>
  <div class="mud-note">关闭后不再推送任何通话事件，转发进程仍保持运行，随时可以再打开。</div>
  <h3 style="margin-top:18px">推送哪些状态</h3>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-call-notify_ringing">来电时推送 · 一响铃就发</label>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-call-notify_answered">接通时推送</label>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-call-notify_ended">挂断时推送 · 带上对方号码</label>
  <label class="mud-fwd-toggle"><input type="checkbox" id="mud-call-notify_missed">响铃未接时推送</label>
  <h3 style="margin-top:18px">请求</h3>
  <div class="mud-fwd-channel" id="mud-call-channel">
   <div class="mud-fwd-field"><label for="mud-call-url">HTTPS 地址</label><input id="mud-call-url" type="url" autocomplete="off" placeholder="https://example.com/hook"></div>
   <div class="mud-fwd-pair">
    <div class="mud-fwd-field"><label for="mud-call-method">请求方法</label><select id="mud-call-method"><option>POST</option><option>GET</option></select></div>
    <div class="mud-fwd-field"><label for="mud-call-timeout">请求超时（秒，1–120）</label><input type="number" min="1" max="120" step="1" id="mud-call-timeout"></div>
   </div>
   <div class="mud-fwd-field"><label for="mud-call-content_type">正文格式</label><select id="mud-call-content_type"><option value="application/json">JSON</option><option value="application/x-www-form-urlencoded">Form URL-encoded</option><option value="text/plain">纯文本</option></select></div>
   <div class="mud-fwd-field"><label for="mud-call-expect">成功响应条件（可选）</label><input id="mud-call-expect" maxlength="128" placeholder="code=200"></div>
   <div class="mud-note">留空时任意 HTTP 2xx 为成功；填写 code=200 这类条件时，还需响应 JSON 顶层字段匹配。</div>
   <div class="mud-fwd-field"><label for="mud-call-headers">附加请求头（每行 Name: value）</label><textarea rows="3" id="mud-call-headers" autocomplete="off" spellcheck="false"></textarea></div>
  </div>
  <div class="mud-fwd-field"><label for="mud-call-device_name">设备备注</label><input id="mud-call-device_name" maxlength="64" autocomplete="off"></div>
  <div class="mud-fwd-field"><label for="mud-call-body">正文模板</label><textarea rows="6" id="mud-call-body" spellcheck="false"></textarea></div>
  <div class="mud-note">占位符：{number} 来电号码、{time} 来电时间、{tag} 事件标签（#CALL #CALL_IN / #CALL_ANSWER / #CALL_END / #CALL_MISSED）、{self} 本机号码、{operator} 运营商、{signal} 信号、{uptime} 开机时长、{device} 设备备注。JSON 里占位符要放在字符串内；按格式自动转义，绝不执行命令。</div>
  <div class="mud-note">事件取值：ringing 来电、answered 接通、ended 挂断、missed 响铃未接、test 测试。设备信息每 5 分钟刷新一次，通话期间不查询 modem。</div>
 </section>
 <section class="mud-card">
  <h3>状态</h3>
  <div class="mud-note" id="mud-call-service">--</div>
  <div class="mud-note" id="mud-call-channel-note">--</div>
  <div class="mud-note">转发由设备独立执行，关掉网页仍然生效。只在有新通话时推送，失败不自动重发。</div>
  <h3 style="margin-top:22px">号码说明</h3>
  <div class="mud-note">号码来自 modem 的通话列表（AT+CLCC），可能为空（未识别来电号码）；投递记录里不保存号码，只记时间、事件和结果。</div>
  <div class="mud-note">双卡设备会分别查询两张卡的通话列表，同一时刻只跟踪一路来电。</div>
 </section>
</div>
<section class="mud-card" style="margin-top:14px">
 <div class="mud-fwd-row"><h3>测试与状态</h3></div>
 <div class="mud-note">测试会走真实渠道发一条固定消息（号码为示例号码），用于确认地址和密钥是否可用。</div>
 <h3 style="margin-top:16px">最近投递记录</h3>
 <div class="mud-note">仅保留本次开机最近 30 次投递结果，不记录号码、地址或通话内容。</div>
 <div class="mud-fwd-history" id="mud-call-history" tabindex="0"></div>
 <div class="mud-fwd-error" id="mud-call-error" hidden></div>
 <div class="mud-fwd-actions">
  <button class="mud-btn on" id="mud-call-save">保存设置</button>
  <button class="mud-btn" id="mud-call-test">发送测试通知</button>
  <button class="mud-btn" id="mud-call-refresh">刷新状态</button>
  <button class="mud-btn" id="mud-call-clear">清空投递记录</button>
 </div>
</section>`;
		M.localize(root);
		this.root = root;
		this.data = data || {};
		this.status = this.data.status || {};
		this.savedPayload = null;
		if (this.data.config) {
			this.fill(this.data.config);
			this.paintStatus(this.status);
			this.paintSource();
			this.savedPayload = JSON.stringify(this.payload());
		} else {
			this.error(this.data.error || '读取失败，请刷新页面');
		}
		this.wire();
		var self = this;
		this._refresh = function() {
			if (document.hidden || !root.isConnected) return Promise.resolve();
			return self.refreshStatus();
		};
		poll.add(this._refresh, 5);
		return root;
	},
	q: function(id) { return this.root.querySelector('#mud-call-' + id); },
	error: function(s) { this.q('error').textContent = s ? M.translate(RESULTS[s] || s) : ''; this.q('error').hidden = !s; },
	fill: function(c) {
		var self = this;
		['enabled', 'notify_ringing', 'notify_answered', 'notify_ended', 'notify_missed'].forEach(function(k) {
			self.q(k).checked = c[k] === 1 || c[k] === '1';
		});
		self.q('url').value = c.url || '';
		self.q('method').value = c.method || 'POST';
		self.q('content_type').value = c.content_type || 'application/json';
		self.q('timeout').value = c.timeout || 12;
		self.q('expect').value = c.expect || '';
		self.q('headers').value = c.headers || '';
		self.q('device_name').value = c.device_name || 'MU300';
		self.q('body').value = c.body || '';
		self.q('preset').value = '';
		self.q('source').value = (c.inherit_sms === 1 || c.inherit_sms === '1') ? 'sms' : 'own';
	},
	payload: function() {
		var self = this, p = {};
		['enabled', 'notify_ringing', 'notify_answered', 'notify_ended', 'notify_missed'].forEach(function(k) {
			p[k] = self.q(k).checked ? 1 : 0;
		});
		p.inherit_sms = self.q('source').value === 'sms' ? 1 : 0;
		['url', 'method', 'content_type', 'expect', 'device_name'].forEach(function(k) { p[k] = self.q(k).value.trim(); });
		p.timeout = self.q('timeout').value.trim();
		p.headers = self.q('headers').value.replace(/\r/g, '');
		p.body = self.q('body').value;
		p.poll = 2;
		return p;
	},
	validate: function(p) {
		if (!/^\d+$/.test(p.timeout) || +p.timeout < 1 || +p.timeout > 120) return '请求超时必须为 1–120 秒';
		if (p.expect && !/^[A-Za-z_][A-Za-z0-9_]{0,63}=(-?[0-9]{1,16}|[a-z]{1,16})$/.test(p.expect)) return '成功响应条件格式无效';
		if (p.body && p.content_type === 'application/json') {
			try { JSON.parse(p.body.replace(/\{[a-z_]*\}/g, 'x')); } catch (e) { return '正文模板必须是有效 JSON'; }
		}
		if (p.url && !/^https?:\/\/[^\s]+$/.test(p.url)) return '推送地址必须以 http:// 或 https:// 开头';
		if (p.inherit_sms === 0 && !p.url && p.enabled) return '本页单独配置时必须填写推送地址';
		if (p.inherit_sms === 1 && !p.url && !(this.status.sms_url || '').trim()) return '还没有推送地址：请先在短信转发里填好渠道，或在本页单独填一个';
		if (p.enabled && !p.notify_ringing && !p.notify_answered && !p.notify_ended && !p.notify_missed) return '至少勾选一种要推送的通话状态';
		return '';
	},
	paintSource: function() {
		var follow = this.q('source').value === 'sms';
		this.q('channel').hidden = follow;
		this.q('source-note').textContent = follow
			? '跟随短信转发：地址、方法、正文格式、超时和请求头都用短信那边的设置，这里只给通话事件单独准备正文模板。'
			: '本页单独配置：地址、方法、正文格式、超时和请求头都以本页为准。';
	},
	paintStatus: function(s) {
		s = s || {};
		this.status = s;
		this.q('state').textContent = M.translate(s.enabled === 1 || s.enabled === '1' ? '通知已开启' : '通知未开启');
		this.q('state').classList.toggle('on', s.enabled === 1 || s.enabled === '1');
		this.q('last').textContent = M.translate('最近投递：') + result(s.last_result) + (s.last_code ? ' · HTTP ' + s.last_code : '');
		this.q('service').textContent = M.translate(s.running === 1 || s.running === '1' ? '转发服务运行中，配置改动保存后立即生效。' : '转发服务未运行，请检查服务状态。');
		this.q('channel-note').textContent = M.translate('当前渠道：' + (s.channel === 'own' ? '本页单独配置' : '跟随短信转发')) + (s.sms_url ? M.translate(' · 短信渠道：') + s.sms_url : M.translate(' · 短信渠道尚未填写地址'));
		var history = this.q('history');
		history.replaceChildren();
		(s.history || []).slice(0, 30).forEach(function(item) {
			var row = document.createElement('div');
			row.className = 'mud-fwd-event';
			var kind = { ringing: '来电', answered: '接通', ended: '挂断', missed: '响铃未接', test: '测试消息' }[item.kind] || item.kind || '事件';
			[new Date(Number(item.time) * 1000).toLocaleString(), M.translate(kind), result(item.result) + (item.http_code ? ' · HTTP ' + item.http_code : '')].forEach(function(value) {
				var el = document.createElement('span');
				el.textContent = value;
				row.appendChild(el);
			});
			history.appendChild(row);
		});
		if (!history.childNodes.length) history.textContent = M.translate('暂无投递');
	},
	refreshStatus: function() {
		var self = this;
		if (this._inflight) return this._inflight;
		this._inflight = L.resolveDefault(callStatus(), {}).then(function(r) {
			if (self.root.isConnected && r && r.status) self.paintStatus(r.status);
		}).finally(function() { self._inflight = null; });
		return this._inflight;
	},
	unload: function() { poll.remove(this._refresh); },
	wire: function() {
		var self = this;
		this.q('source').onchange = function() { self.paintSource(); };
		this.q('preset').onchange = function() {
			var name = this.value, p = PRESETS[name];
			if (!p) return;
			M.confirmBox('填入推送预设？', '将替换当前地址、正文格式和正文模板，保存后才会生效。', { okText: '确定' }).then(function(yes) {
				if (!yes) return;
				self.q('source').value = 'own';
				self.q('url').value = p[0];
				self.q('method').value = 'POST';
				self.q('content_type').value = typeof p[1] === 'string' ? 'application/x-www-form-urlencoded' : 'application/json';
				self.q('body').value = typeof p[1] === 'string' ? p[1] : JSON.stringify(p[1], null, 2);
				self.q('expect').value = ({ bark: 'code=200', pushplus: 'code=200', serverchan: 'code=0', telegram: 'ok=true', wecom: 'errcode=0', dingtalk: 'errcode=0' })[name] || '';
				self.q('headers').value = '';
				self.paintSource();
			});
		};
		this.q('method').onchange = function() {
			self.q('body').disabled = self.q('method').value === 'GET';
		};
		this.q('save').onclick = function() {
			var p = self.payload(), problem = self.validate(p);
			if (problem) { self.error(problem); return; }
			M.confirmBox('保存通话通知设置？', '保存后立即生效，只处理保存之后的新通话。', { okText: '保存' }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('save'), toast = M.toast('正在保存通话通知设置…', { type: 'busy', timeout: 0 });
				M.busy(btn, true);
				self.q('test').disabled = true;
				self.error('');
				L.resolveDefault(callSet(JSON.stringify(p)), {}).then(function(r) {
					if (!r || !r.ok) { self.error((r && r.error) || '保存失败'); toast.update('保存失败', 'error'); return; }
					return L.resolveDefault(callGet(), {}).then(function(v) {
						if (v && v.config) {
							self.fill(v.config);
							self.paintSource();
							self.paintStatus(v.status || {});
							self.savedPayload = JSON.stringify(self.payload());
						}
						toast.update('已保存', 'success');
					});
				}).finally(function() {
					M.busy(btn, false);
					self.q('test').disabled = false;
					setTimeout(function() { toast.close(); }, 3000);
				});
			});
		};
		this.q('test').onclick = function() {
			if (JSON.stringify(self.payload()) !== self.savedPayload) { self.error('请先保存修改再测试'); return; }
			if (!(self.status.enabled === 1 || self.status.enabled === '1')) { self.error('请先开启并保存通话通知'); return; }
			M.confirmBox('发送测试通知？', '将向当前已保存渠道发送一条固定测试消息。', { okText: '发送' }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('test');
				M.busy(btn, true);
				L.resolveDefault(callTest(), {}).then(function(r) {
					M.busy(btn, false);
					M.toast(r && r.ok ? '测试已开始，请稍后刷新状态' : '测试失败：' + result(r && r.error), { type: r && r.ok ? 'success' : 'error' });
					setTimeout(function() { self.refreshStatus(); }, 2000);
				});
			});
		};
		this.q('refresh').onclick = function() { self.refreshStatus(); };
		this.q('clear').onclick = function() {
			M.confirmBox('清空投递记录？', '只清除最近投递记录，不影响转发配置。', { okText: '确定' }).then(function(yes) {
				if (!yes) return;
				self.q('clear').disabled = true;
				L.resolveDefault(callClear(), {}).then(function(r) {
					if (!r || !r.ok) { self.error((r && r.error) || '清空失败'); return; }
					return self.refreshStatus();
				}).finally(function() { self.q('clear').disabled = false; });
			});
		};
	}
});
