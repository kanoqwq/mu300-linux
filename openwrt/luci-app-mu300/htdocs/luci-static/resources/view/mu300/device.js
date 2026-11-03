'use strict';
'require view';
'require mu300.common as M';
'require mu300.network-performance as NetworkPerformance';
'require mu300.nfc as NFC';

return view.extend({
	load: function() { return L.resolveDefault(M.callUsbGet(), {}); },
	render: function(state) {
		this._disposed = false; this._timers = new Set(); this._busy = null; this._confirming = false; this._scan = null; this._epoch = 0; this._edits = 0;
		M.injectCss();
		M.localizeMenu();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<style>
.mud-device-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.mud-device-field{display:flex;flex-direction:column;gap:7px;margin:12px 0}
.mud-device-field label{font-size:.8rem;color:var(--text-muted,var(--text-light,#777))}
.mud-device-field select{width:100%;min-width:0;box-sizing:border-box;min-height:38px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);padding:6px 10px;background:var(--surface,var(--background,#fff));color:var(--text,#222)}
.mud-device-grid>.mud-card{min-width:0}.mud-device-grid .mud-v{overflow-wrap:anywhere}.mud-device-state{margin-top:10px;overflow-wrap:anywhere;color:var(--warning,#b76d00)}
.mud-device-toggle{display:flex;align-items:center;gap:9px;font-size:.82rem;line-height:1.45;cursor:pointer}
.mud-device-toggle input{accent-color:var(--brand,var(--primary,#2f7bf6))}
.mud-device-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.mud-device-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.mud-device-head h3{margin:0;font-size:.85rem}
.mud-device-list{display:grid;gap:8px;margin-top:12px}
.mud-device-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;border:1px solid var(--hairline,var(--border,#ddd));border-radius:var(--radius-base,.5rem)}
.mud-device-item-main{min-width:0;display:flex;align-items:center;gap:10px}
.mud-device-dot{width:9px;height:9px;border-radius:50%;background:var(--text-muted,#888);flex:none}
.mud-device-dot.up{background:var(--success,#2fbf71)}
.mud-device-item-name{font-weight:650;overflow-wrap:anywhere}
.mud-device-item-sub{font-size:.72rem;color:var(--text-muted,var(--text-light,#777));overflow-wrap:anywhere}.mud-device-item-main>div{min-width:0}.mud-device-item .mud-btn{flex-shrink:0}.mud-device-lan-policy{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px}
@media(max-width:720px){.mud-device-grid{grid-template-columns:1fr}.mud-device-item{align-items:flex-start}.mud-device-item .mud-btn{white-space:nowrap}}
</style>
<div class="mud-note mud-device-state" id="mud-usb-state-error" role="status"></div>
<div class="mud-device-grid">
 <section class="mud-card">
  <h3>USB 角色</h3>
  <div class="mud-r"><span class="mud-k">当前角色</span><span class="mud-v" id="mud-usb-role-now">--</span></div>
  <div class="mud-r"><span class="mud-k">开机角色策略</span><span class="mud-v" id="mud-usb-role-policy">--</span></div>
  <div class="mud-device-field"><label for="mud-usb-role">切换 USB 角色</label>
   <select id="mud-usb-role"><option value="device">设备模式</option><option value="host">主机模式</option></select></div>
  <label class="mud-device-toggle"><input type="checkbox" id="mud-usb-role-auto">开机自动启用主机模式</label>
  <div class="mud-device-actions"><button class="mud-btn" id="mud-usb-role-apply">应用角色</button></div>
  <div class="mud-note">主机模式会断开本端口的 USB 网络与串口。F50 没有电池；切换后可能失去管理连接，外接 USB 网卡通常需要自供电 Hub。</div>
 </section>
 <section class="mud-card" id="mud-usb-net-card">
  <h3>USB 网络模式</h3>
  <div class="mud-r"><span class="mud-k">当前共享协议</span><span class="mud-v" id="mud-usb-net-current">--</span></div>
  <div class="mud-r"><span class="mud-k">下次启动协议</span><span class="mud-v" id="mud-usb-net-next">--</span></div>
  <div class="mud-device-field"><label for="mud-usb-net-mode">网络协议</label>
   <select id="mud-usb-net-mode"><option value="ncm">NCM</option><option value="ecm">ECM</option><option value="rndis">RNDIS</option></select></div>
  <div class="mud-device-field"><label for="mud-usb-net-scope">生效期限</label>
   <select id="mud-usb-net-scope"><option value="once">仅下次重启</option><option value="permanent">永久生效</option></select></div>
  <label class="mud-device-toggle"><input type="checkbox" id="mud-usb-net-auto">启用所选协议</label>
  <div class="mud-device-actions"><button class="mud-btn" id="mud-usb-net-apply">保存，重启后生效</button></div>
  <div class="mud-note" id="mud-usb-net-note">NCM 为默认模式。Windows 不原生支持 ECM；RNDIS 会改变枚举方式。关闭“启用所选协议”时仅保存选择；选择“仅下次重启”则成功应用一次后恢复默认 NCM。</div>
 </section>
</div>
<section class="mud-card" style="margin-top:14px" id="mud-usb-adapters-card">
 <div class="mud-device-head"><h3>USB 网卡</h3><button class="mud-btn" id="mud-usb-refresh">刷新</button></div>
 <div class="mud-device-lan-policy"><label class="mud-device-toggle"><input type="checkbox" id="mud-usb-lan-auto">自动将空闲 USB 网卡加入 LAN</label><button class="mud-btn" id="mud-usb-lan-save">保存策略</button></div>
 <div class="mud-note">默认关闭。启用后在网卡接入或 LAN 启动时自动加入空闲 USB 有线网卡；不会接管其他网络、USB Wi-Fi 或本机共享接口。关闭不会删除已保存端口。</div>
 <div class="mud-note">仅主机模式可用。刷新只尝试启用未被其他网络占用的网卡；现有网卡可手动添加到 LAN。</div>
 <div class="mud-device-list" id="mud-usb-adapters"></div>
</section>`;
		M.localize(root);
		this.root = root;
		this.state = state || {};
		this.wire();
		this.paint();
		this.refreshAdapters();
		this._networkPerformance = NetworkPerformance.mount(root);
		this._nfc = NFC.mount(root);
		return root;
	},
	q: function(id) { return this.root.querySelector('#mud-usb-' + id); },
	unload: function() {
		if (this._networkPerformance) this._networkPerformance.dispose();
		if (this._nfc) this._nfc.dispose();
		this._disposed = true; this._epoch++;
		this._timers.forEach(clearTimeout); this._timers.clear();
		if (this._toast) this._toast.close();
	},
	later: function(fn, ms) {
		var self = this, timer = setTimeout(function() { self._timers.delete(timer); if (!self._disposed) fn(); }, ms);
		this._timers.add(timer); return timer;
	},
	request: function(fn, ms) {
		var self = this;
		return new Promise(function(resolve, reject) {
			var timer = self.later(function() { reject(new Error('timeout')); }, ms || 5000);
			Promise.resolve().then(fn).then(resolve, reject).finally(function() { clearTimeout(timer); self._timers.delete(timer); });
		});
	},
	paint: function(fill) {
		var s = this.state || {};
		var protocol = function(v) { return /^(ncm|ecm|rndis)$/.test(v || '') ? v.toUpperCase() : M.translate(v === 'none' ? '主机模式不使用共享协议' : v === 'mixed' ? '多个共享协议' : '未能确认'); };
		this.q('net-current').textContent = protocol(s.net_current);
		this.q('net-next').textContent = protocol(s.net_next);
		this.q('role-policy').textContent = s.ok ? M.translate(s.role_auto ? '开机自动启用主机模式' : '设备模式（默认）') : '--';
		this.q('state-error').textContent = M.translate(!s.ok ? 'USB 状态读取失败，请刷新重试' : s.role_error ? 'USB 角色后台切换失败，请重试' : s.net_policy_error ? '启动协议文件与保存设置不一致，请重新保存' : s.role_pending ? '切换请求已接收，USB 连接可能短暂中断。' : '');
		this.q('role-now').textContent = s.role === 'host' ? M.translate('主机模式') : s.role === 'device' ? M.translate('设备模式') : M.translate('不可用');
		this.q('role').querySelector('option[value="host"]').disabled = s.host_supported === 0;
		if (fill !== false) {
		this.q('role').value = s.role === 'host' || s.role_auto ? 'host' : 'device';
		this.q('role-auto').checked = !!s.role_auto;
		this.q('net-mode').value = s.net_mode || 'ncm';
		this.q('net-scope').value = s.net_scope || 'permanent';
		this.q('net-auto').checked = !!s.net_auto;
		this.q('lan-auto').checked = !!s.lan_auto;
		}
		this.updateDisabled();
	},
	updateDisabled: function() {
		var host = this.state.role === 'host', hostAuto = this.q('role-auto').checked && this.q('role').value === 'host';
		var busy = !!this._busy || !this.state.ok;
		this.q('role').disabled = this.q('role-apply').disabled = busy || this.state.host_supported === 0;
		this.q('role-auto').disabled = busy || this.q('role').value !== 'host';
		if (this.q('role').value !== 'host') this.q('role-auto').checked = false;
		var locked = host || !!this.state.role_auto || hostAuto;
		[ 'net-mode', 'net-scope', 'net-auto', 'net-apply' ].forEach(function(id) { this.q(id).disabled = locked || busy; }, this);
		if (locked) this.q('net-auto').checked = false;
		this.q('net-note').textContent = M.translate(locked ? '主机模式下不可选择 USB 网络模式；主机开机自启会自动关闭 USB 网络开机自启。' : 'NCM 为默认模式。Windows 不原生支持 ECM；RNDIS 会改变枚举方式。关闭“启用所选协议”时仅保存选择；选择“仅下次重启”则成功应用一次后恢复默认 NCM。');
		this.q('refresh').disabled = !!this._busy;
		// Outside host mode an existing policy may still be disabled.
		this.q('lan-auto').disabled = this.q('lan-save').disabled = busy || (!host && !this.state.lan_auto);
	},
	wire: function() {
		var self = this;
		this.root.addEventListener('change', function() { self._edits++; });
		this.q('role').addEventListener('change', function() { self.updateDisabled(); });
		this.q('role-auto').addEventListener('change', function() { self.updateDisabled(); });
		this.q('role-apply').addEventListener('click', function() {
			var role = self.q('role').value, auto = self.q('role-auto').checked ? '1' : '0';
			var warning = role === 'host' ? '切换主机模式会立即断开 USB 管理连接。F50 没有电池，外设可能需要自供电；请确认有其他管理途径。' : '切回设备模式后 USB 网络和串口会重新枚举。';
			self.confirm('确认切换 USB 角色？', warning, { danger: role === 'host', okText: '应用' }, function() {
				return self.perform('role', self.q('role-apply'), function() { return M.callUsbSet('role', role, '', auto); }, function(r, msg) {
					if (!r || r.ok !== 1) { msg.update(r && r.ok === 0 ? '切换失败：' + (r.error || '未知错误') : '管理连接已中断；请重新连接后确认 USB 角色。', r && r.ok === 0 ? 'error' : 'info'); return; }
					if (r.pending) return self.watchRole(role, msg, Date.now() + 8000);
					msg.update('USB 角色已应用', 'success');
				});
			});
		});
		this.q('net-apply').addEventListener('click', function() {
			var mode = self.q('net-mode').value, scope = self.q('net-scope').value, auto = self.q('net-auto').checked ? '1' : '0';
			self.confirm('保存 USB 网络模式？', auto === '1' ? '网络模式将在下次重启时生效，USB 管理连接可能需要重新识别。' : '只保存选择；未启用所选协议，下次重启仍使用默认 NCM。', { okText: '保存' }, function() {
				return self.perform('net', self.q('net-apply'), function() { return M.callUsbSet('net', mode, scope, auto); }, function(r, msg) {
					msg.update(r && r.ok ? (auto === '1' ? '设置已保存，当前协议不变，重启后生效' : '仅保存选择，未启用开机应用') : '保存失败：' + (r && r.error || '未知错误'), r && r.ok ? 'success' : 'error');
				});
			});
		});
		this.q('refresh').addEventListener('click', function() { self.reloadState(false); });
		this.q('lan-save').addEventListener('click', function() {
			var enabled = self.q('lan-auto').checked ? '1' : '0';
			self.confirm('保存 USB 网卡自动加入策略？', enabled === '1' ? '空闲 USB 有线网卡会成为 LAN 端口，向所连接网络提供局域网访问。仅连接可信网络；已连接的网卡可手动添加。' : '停止自动加入新网卡；已保存的 LAN 端口及热插拔恢复保持不变。', {okText:'保存'}, function() {
				return self.perform('lan-auto', self.q('lan-save'), function() { return M.callUsbSet('lan-auto', enabled, '', ''); }, function(r, msg) {
					msg.update(r && r.ok ? 'USB 网卡自动加入策略已保存' : '保存失败：' + (r && r.error || '未知错误'), r && r.ok ? 'success' : 'error');
				});
			});
		});
	},
	confirm: function(title, text, opts, apply) {
		if (this._disposed || this._busy || this._confirming) return;
		var self = this;
		this._confirming = true;
		return M.confirmBox(title, text, opts).then(function(yes) {
			if (yes && !self._disposed) return apply();
		}).finally(function() { self._confirming = false; });
	},
	perform: function(kind, btn, invoke, result) {
		var self = this;
		if (this._disposed || this._busy) return Promise.resolve();
		this._busy = kind; this._epoch++; this.updateDisabled(); M.busy(btn, true);
		if (this._toast) this._toast.close();
		var msg = this._toast = M.toast(kind === 'role' ? '正在切换 USB 角色…' : kind === 'net' || kind === 'lan-auto' ? '正在保存 USB 网络设置…' : '正在添加 USB 网卡…', {type:'busy',timeout:0});
		return this.request(invoke, 6000).then(function(r) {
			if (!self._disposed) return result(r, msg);
		}, function() {
			if (!self._disposed) msg.update(kind === 'role' ? '管理连接已中断；请重新连接后确认 USB 角色。' : '请求结果未能确认，请刷新后检查，勿重复提交', 'info');
		}).finally(function() {
			self._busy = null; M.busy(btn, false);
			if (!self._disposed) { self.updateDisabled(); self.later(function() { msg.close(); }, 5000); self.reloadState(); }
		});
	},
	reloadState: function(fill) {
		if (this._disposed || this._busy) return Promise.resolve();
		var self = this, epoch = this._epoch, edits = this._edits;
		if (this._stateReq && this._stateReq.epoch === epoch) return this._stateReq.promise;
		var req = this._stateReq = {epoch:epoch};
		req.promise = this.request(function() { return M.callUsbGet(); }).then(function(s) {
			if (self._disposed || self._epoch !== epoch) return;
			if (!s || !s.ok) throw new Error('unavailable');
			self.state = s; self.paint(fill !== false && edits === self._edits); return self.refreshAdapters();
		}).catch(function() {
			if (!self._disposed && self._epoch === epoch) self.q('state-error').textContent = M.translate('USB 状态读取失败，请刷新重试');
		}).finally(function() { if (self._stateReq === req) self._stateReq = null; });
		return req.promise;
	},
	watchRole: function(role, msg, deadline) {
		var self = this;
		if (this._disposed) return Promise.resolve();
		if (Date.now() >= deadline) { msg.update('暂时无法确认角色；请重新连接后刷新页面。', 'info'); return Promise.resolve(); }
		return this.request(function() { return M.callUsbGet(); }, Math.min(1500, deadline - Date.now())).catch(function() { return null; }).then(function(s) {
			if (self._disposed) return;
			if (!s || !s.ok) { msg.update('暂时无法确认角色；请重新连接后刷新页面。', 'info'); return; }
			if (s && s.ok && s.role === role) { self.state = s; self.paint(); msg.update('USB 角色已应用', 'success'); return; }
			if (s && s.role_target === role && s.role_error) { self.state = s; self.paint(); msg.update('USB 角色后台切换失败，请重试', 'error'); return; }
			return new Promise(function(resolve) { self.later(resolve, Math.min(700, Math.max(0, deadline - Date.now()))); }).then(function() { return self.watchRole(role, msg, deadline); });
		});
	},
	refreshAdapters: function(attempt) {
		attempt = attempt || 0;
		if (this._disposed || this._busy) return Promise.resolve();
		if (this._scanRetry) { clearTimeout(this._scanRetry); this._timers.delete(this._scanRetry); this._scanRetry = null; }
		if (this._scan && this._scan.epoch === this._epoch) return this._scan.promise;
		var self = this, list = this.q('adapters');
		list.replaceChildren();
		if (this.state.role !== 'host') {
			list.textContent = M.translate('切换到主机模式后显示 USB 网卡。'); return;
		}
		var wait = document.createElement('div'); wait.className = 'mud-note mud-booting';
		wait.textContent = M.translate('正在扫描 USB 网卡…'); list.appendChild(wait);
		M.busy(this.q('refresh'), true);
		var scan = this._scan = {epoch:this._epoch};
		scan.promise = this.request(function() { return M.callUsbNetList(); }).then(function(r) {
			if (self._disposed || self._epoch !== scan.epoch || self.state.role !== 'host') return;
			M.busy(self.q('refresh'), false);
			list.replaceChildren();
			if (!r || !r.ok) throw new Error('scan failed');
			if (!r.devices || !r.devices.length) {
				list.textContent = M.translate('没有发现 USB 网卡。');
				if (self.state.role === 'host' && attempt < 2)
					self._scanRetry = self.later(function() { if (self._epoch === scan.epoch && self.state.role === 'host') self.refreshAdapters(attempt + 1); }, 1600);
				return;
			}
			r.devices.forEach(function(d) {
				var row = document.createElement('div'); row.className = 'mud-device-item';
				var main = document.createElement('div'); main.className = 'mud-device-item-main';
				var dot = document.createElement('i'); dot.className = 'mud-device-dot' + (d.carrier ? ' up' : ''); main.appendChild(dot);
				var info = document.createElement('div');
				var name = document.createElement('div'); name.className = 'mud-device-item-name'; name.textContent = d.name;
				var sub = document.createElement('div'); sub.className = 'mud-device-item-sub'; sub.textContent = M.translate(d.carrier ? '链路已连接' : d.eligible === 0 ? '链路未连接' : '链路未连接，已尝试启用');
				info.appendChild(name); info.appendChild(sub); main.appendChild(info); row.appendChild(main);
				[ M.translate('驱动') + ': ' + (d.driver || '--'), 'MAC: ' + (d.mac || '--'), M.translate('当前归属') + ': ' + (d.owner || M.translate('未分配')),
				  d.eligible === 0 ? M.translate('已被其他网络占用') : d.in_lan && !d.attached ? M.translate('已保存，等待接入网桥') : '' ].filter(Boolean).forEach(function(text) {
					var detail = document.createElement('div'); detail.className = 'mud-device-item-sub'; detail.textContent = text; info.appendChild(detail);
				});
				var btn = document.createElement('button'); btn.className = 'mud-btn'; btn.textContent = M.translate(d.eligible === 0 ? '不可添加' : d.in_lan && d.attached ? '已加入 LAN' : '添加到 LAN'); btn.disabled = d.eligible === 0 || !!(d.in_lan && d.attached);
				btn.addEventListener('click', function() {
					self.confirm('添加 USB 网卡到 LAN？', '这会保存网桥配置并重新加载网络，现有连接可能短暂中断。', { okText: '添加' }, function() {
						return self.perform('add', btn, function() { return M.callUsbNetAdd(d.name); }, function(a, msg) {
							msg.update(a && a.ok ? '已添加到 LAN' : '添加失败：' + (a && a.error || '未知错误'), a && a.ok ? 'success' : 'error');
						});
					});
				});
				row.appendChild(btn); list.appendChild(row);
			});
		}).catch(function() {
			if (!self._disposed && self._epoch === scan.epoch) list.textContent = M.translate('读取 USB 网卡失败，请刷新重试');
		}).finally(function() {
			if (self._scan === scan) { self._scan = null; M.busy(self.q('refresh'), false); }
		});
		return scan.promise;
	}
});
