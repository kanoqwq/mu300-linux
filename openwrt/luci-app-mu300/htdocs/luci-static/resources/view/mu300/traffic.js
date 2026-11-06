'use strict';
'require view';
'require poll';
'require mu300.common as M';

function formatUsage(n) { return M.fmtTrafficBytes(n); }
function trafficSlot() { return String(M.selectedSlot()); }
var ERRORS = { invalid_config: '配置无效', traffic_unavailable: '流量统计服务暂不可用',
	storage_failed: '统计数据保存失败', clock_unsynced: '系统时间尚未同步', confirmation_required: '请先确认清空操作' };

return view.extend({
	load: function() { return L.resolveDefault(M.callTrafficGet(trafficSlot()), {}); },
	render: function(data) {
		M.injectCss(); M.localizeMenu();
		this._disposed = false; this._epoch = 0; this._mutating = false; this._confirming = false;
		this._slot = data && data.config ? String(data.status && data.status.slot != null ? data.status.slot : trafficSlot()) : null; this._dirty = false;
		var root = document.createElement('div'); root.className = 'mud';
		root.innerHTML = `
<style>
.mud-tr-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}
.mud-tr-summary .mud-card{min-width:0}.mud-tr-summary b{display:block;font-size:1.3rem;overflow-wrap:anywhere;margin:7px 0}
.mud-tr-label{font-size:.8rem;color:var(--text-muted,#777)}.mud-tr-sub{font-size:.75rem;color:var(--text-subtle,var(--text-muted,#777));overflow-wrap:anywhere}
.mud-tr-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}.mud-tr-grid>.mud-card{min-width:0}
.mud-tr-field{display:flex;flex-direction:column;gap:6px;margin:12px 0;min-width:0}
.mud-tr-field label{font-size:.8rem;overflow-wrap:anywhere}
.mud .mud-tr-field input,.mud .mud-tr-field select{box-sizing:border-box;width:100%;min-width:0;max-width:100%;margin:0;padding:8px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit}
.mud-tr-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}
.mud-tr-line{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:10px 0}.mud-tr-line>span{overflow-wrap:anywhere}
.mud-tr-warn{color:var(--warning,#b76d00)}.mud-tr-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.mud-tr-scroll{overflow-x:auto}.mud-tr-table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:.8rem}
.mud-tr-table th,.mud-tr-table td{padding:8px 4px;text-align:right;overflow-wrap:anywhere;border-bottom:1px solid var(--hairline,var(--border,#ddd))}.mud-tr-table th:first-child,.mud-tr-table td:first-child{text-align:left;width:30%}
@media(max-width:1000px){.mud-tr-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.mud-tr-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:480px){.mud-tr-form{grid-template-columns:minmax(0,1fr)}.mud-tr-summary b{font-size:1.1rem}}
</style>
<div class="mud-tr-summary">
 <section class="mud-card"><span class="mud-tr-label">今日流量</span><b id="mud-tr-today">--</b><span class="mud-tr-sub" id="mud-tr-today-detail"></span></section>
 <section class="mud-card"><span class="mud-tr-label">当月流量</span><b id="mud-tr-month">--</b><span class="mud-tr-sub">自然月统计</span></section>
 <section class="mud-card"><span class="mud-tr-label">套餐周期已用</span><b id="mud-tr-cycle">--</b><span class="mud-tr-sub" id="mud-tr-cycle-date"></span></section>
 <section class="mud-card"><span class="mud-tr-label">套餐剩余</span><b id="mud-tr-remaining">--</b><span class="mud-tr-sub" id="mud-tr-plan-name"></span></section>
</div>
<section class="mud-card">
 <div class="mud-tr-line"><span id="mud-tr-quota"></span><span id="mud-tr-reset"></span></div>
 <div class="mud-meter"><i id="mud-tr-meter" style="width:0;background:var(--brand,#2f7bf6)"></i></div>
 <div class="mud-tr-line mud-tr-sub"><span id="mud-tr-daily"></span><span id="mud-tr-updated"></span></div>
 <div class="mud-tr-warn" id="mud-tr-warning"></div>
</section>
<div class="mud-tr-grid">
 <section class="mud-card">
  <h3>套餐与流量池</h3>
  <div class="mud-tr-field"><label for="mud-tr-plan_name">套餐名称</label><input id="mud-tr-plan_name" maxlength="128"></div>
  <div class="mud-tr-form">
   <div class="mud-tr-field"><label for="mud-tr-monthly_gb">每周期额度（GB）</label><input id="mud-tr-monthly_gb" type="number" min="0" max="100000" step="0.001"></div>
   <div class="mud-tr-field"><label for="mud-tr-daily_gb">每日参考额度（GB）</label><input id="mud-tr-daily_gb" type="number" min="0" max="100000" step="0.001"></div>
   <div class="mud-tr-field"><label for="mud-tr-reset_day">每月重置日（1–31）</label><input id="mud-tr-reset_day" type="number" min="1" max="31" step="1"></div>
   <div class="mud-tr-field"><label for="mud-tr-count_mode">套餐计量方式</label><select id="mud-tr-count_mode"><option value="total">上下行合计</option><option value="rx">仅下行</option><option value="tx">仅上行</option></select></div>
  </div>
  <div class="mud-note">额度为 0 表示不限量；1 GB = 1000 MB。重置日在设备当地时间零点生效，短月份使用当月最后一天。</div>
  <div class="mud-tr-field"><label for="mud-tr-device">统计网卡</label><input id="mud-tr-device" placeholder="自动使用蜂窝 WAN 网卡" maxlength="15"></div>
  <div class="mud-tr-field"><label for="mud-tr-used_gb">校准本周期已用（GB，可选）</label><input id="mud-tr-used_gb" type="number" min="0" max="100000" step="0.001" placeholder="留空保持现有统计"></div>
  <div class="mud-note">校准只调整当前套餐周期，不修改每日和自然月历史；下个周期自动恢复实际统计。</div>
  <div class="mud-tr-actions"><button class="mud-btn on" id="mud-tr-save">保存设置</button><button class="mud-btn" id="mud-tr-refresh">刷新状态</button></div>
 </section>
 <section class="mud-card">
  <h3>最近每日用量</h3>
  <div class="mud-tr-scroll"><table class="mud-tr-table"><thead><tr><th>日期</th><th>下行</th><th>上行</th><th>合计</th></tr></thead><tbody id="mud-tr-days"></tbody></table></div>
 </section>
</div>
<section class="mud-card" style="margin-top:14px">
 <h3>每月用量历史</h3>
 <div class="mud-tr-scroll"><table class="mud-tr-table"><thead><tr><th>月份</th><th>下行</th><th>上行</th><th>合计</th></tr></thead><tbody id="mud-tr-months"></tbody></table></div>
 <div class="mud-note" style="margin-top:12px">从启用统计时开始记录；统计约每 10 秒更新、每 60 秒保存。突然断电可能损失最近未保存的记录。</div>
 <div class="mud-note">本地网卡统计供参考，计费以运营商为准。额度用于显示和提醒，不会自动断网。</div>
 <div class="mud-tr-actions"><button class="mud-btn warn" id="mud-tr-clear">清空流量记录</button></div>
 <div class="mud-note">仅清除本地统计，不会重置运营商账单，也不会断开网络。</div>
</section>`;
		M.localize(root); this.root = root; this.data = data || {};
		this.fill(this.data.config || {}); this.paint(); this.wire();
		var self = this; this._poll = function() { return self.refresh(); }; poll.add(this._poll, 10);
		M.simSelector(root);
		return root;
	},
	unload: function() {
		this._disposed = true; this._epoch++;
		if (this._poll) poll.remove(this._poll);
		if (this._toast) this._toast.close();
	},
	q: function(k) { return this.root.querySelector('#mud-tr-' + k); },
	fill: function(c) {
		var self = this;
		['plan_name','device'].forEach(function(k) { self.q(k).value = c[k] || ''; });
		['monthly_gb','daily_gb'].forEach(function(k) { self.q(k).value = c[k] || 0; });
		this.q('reset_day').value = c.reset_day || 1; this.q('count_mode').value = c.count_mode || 'total';
		this.q('used_gb').value = '';
	},
	accept: function(d, slot) {
		if (slot !== trafficSlot()) return;
		var actual = d && (d.slot != null ? d.slot : d.status && d.status.slot);
		if (actual != null && String(actual) !== slot) return;
		var changed = this._slot !== slot;
		this.data = d || {};
		if (this.data.config) {
			this._slot = slot;
			if (changed || !this._dirty) { this.fill(this.data.config); this._dirty = false; }
		}
		this.paint();
	},
	paint: function() {
		var d = this.data, s = d.status || {}, c = d.config || {}, self = this;
		this.q('today').textContent = formatUsage(s.today_used); this.q('month').textContent = formatUsage(s.month_used);
		this.q('cycle').textContent = formatUsage(s.cycle_used);
		this.q('remaining').textContent = s.remaining == null ? M.translate('不限量') : formatUsage(s.remaining);
		this.q('today-detail').textContent = M.translate('下行') + ' ' + formatUsage(s.today && s.today.rx) + ' / ' + M.translate('上行') + ' ' + formatUsage(s.today && s.today.tx);
		this.q('cycle-date').textContent = s.cycle_start || '--';
		this.q('plan-name').textContent = c.plan_name || M.translate('未设置套餐名称');
		this.q('quota').textContent = M.translate('每周期额度：') + (s.monthly_limit ? formatUsage(s.monthly_limit) : M.translate('不限量'));
		this.q('reset').textContent = M.translate('下次重置：') + (s.cycle_end || '--');
		this.q('daily').textContent = M.translate('每日参考额度：') + (s.daily_limit ? formatUsage(s.daily_limit) : M.translate('不限量'));
		this.q('updated').textContent = (s.device || '--') + ' · ' + M.translate('更新于：') + (s.updated_at ? new Date(s.updated_at * 1000).toLocaleTimeString() : '--');
		this.q('meter').style.width = (s.monthly_limit ? Math.min(100, 100 * s.cycle_used / s.monthly_limit) : 0) + '%';
		this.q('meter').style.background = s.over_limit ? 'var(--danger,#d45)' : 'var(--brand,#2f7bf6)';
		var warnings = [];
		if (!d.status) warnings.push('流量统计服务暂不可用');
		else {
			if (!s.available) warnings.push('统计网卡暂不可用');
			if (!s.clock_ok) warnings.push('系统时间尚未同步');
			if (s.storage_error) warnings.push('统计数据保存失败');
			if (s.mapping_error) warnings.push('两张 SIM 映射到同一网卡，已暂停第二卡统计以避免重复计费');
			if (s.over_limit) warnings.push('已达到套餐额度');
			if (s.daily_over_limit) warnings.push('已达到每日参考额度');
		}
		this.q('warning').textContent = warnings.map(function(x) { return M.translate(x); }).join(' · ');
		['days','months'].forEach(function(k) {
			self.q(k).innerHTML = (d[k] || []).map(function(row) {
				return '<tr><td>' + M.esc(row.date) + '</td><td>' + formatUsage(row.rx) + '</td><td>' + formatUsage(row.tx) + '</td><td>' + formatUsage(row.rx + row.tx) + '</td></tr>';
			}).join('') || '<tr><td colspan="4">' + M.esc(M.translate('暂无统计记录')) + '</td></tr>';
		});
	},
	refresh: function() {
		if (this._disposed || this._mutating) return Promise.resolve();
		var self = this, slot = trafficSlot();
		if (this._refresh && this._refresh.epoch === this._epoch && this._refresh.slot === slot) return this._refresh.promise;
		if (this._slot !== slot) { this._slot = null; this._dirty = false; this.data = {}; this.fill({}); this.paint(); }
		var epoch = ++this._epoch, request = { epoch: epoch, slot: slot }; this._refresh = request;
		request.promise = L.resolveDefault(M.callTrafficGet(slot), {}).then(function(d) {
			if (self._disposed || self._epoch !== epoch) return;
			self.accept(d, slot);
		}).finally(function() { if (self._refresh === request) self._refresh = null; });
		return request.promise;
	},
	mutate: function(button, invoke, clearing, slot) {
		if (this._disposed || this._mutating) return Promise.resolve();
		var self = this;
		this._mutating = true; this._epoch++;
		this.q('save').disabled = this.q('clear').disabled = true;
		M.busy(button, true);
		if (this._toast) this._toast.close();
		var toast = this._toast = M.toast(clearing ? '正在清空流量记录…' : '正在保存流量池设置…', {type:'busy', timeout:0});
		return Promise.resolve().then(invoke).then(function(r) {
			if (self._disposed) return;
			if (!r || !r.ok) toast.update(ERRORS[r && r.error] || (clearing ? '清空失败' : '保存失败'), 'error');
			else {
				if (slot === trafficSlot()) {
					self.q('used_gb').value = '';
					if (!clearing) self._dirty = false;
					if (r.data) self.accept(r.data, slot);
				}
				toast.update(clearing ? '流量记录已清空' : '已保存', 'success');
			}
		}, function() {
			if (!self._disposed) toast.update(clearing ? '清空失败' : '保存失败', 'error');
		}).finally(function() {
			self._mutating = false; M.busy(button, false);
			if (!self._disposed) {
				self.q('save').disabled = self.q('clear').disabled = false;
				self.refresh();
				setTimeout(function() { toast.close(); }, 3000);
			}
		});
	},
	wire: function() {
		var self = this;
		this.root.querySelectorAll('input,select').forEach(function(input) { input.addEventListener('input', function() { self._dirty = true; }); });
		this.q('refresh').onclick = function() { self.refresh(); };
		this.q('clear').onclick = function() {
			if (self._mutating || self._confirming) return;
			var slot = self._slot;
			if (slot !== trafficSlot()) { self.refresh(); return; }
			self._confirming = true;
			M.confirmBox(M.translate('清空流量记录') + ' · ' + M.slotName(slot),
				'将清空今日、每月历史和套餐周期校准，并从当前网卡计数重新开始。已保存的套餐额度、结算日、计量方式和统计网卡保持不变。此操作不可撤销。',
				{danger:true, okText:'清空'}).then(function(yes) {
				if (yes && !self._disposed) return self.mutate(self.q('clear'), function() { return M.callTrafficClear(true, slot); }, true, slot);
			}).finally(function() { self._confirming = false; });
		};
		this.q('save').onclick = function() {
			if (self._mutating || self._confirming) return;
			var slot = self._slot;
			if (slot !== trafficSlot()) { self.refresh(); return; }
			var p = { plan_name:self.q('plan_name').value.trim(), device:self.q('device').value.trim(), count_mode:self.q('count_mode').value,
				monthly_gb:Number(self.q('monthly_gb').value), daily_gb:Number(self.q('daily_gb').value), reset_day:Number(self.q('reset_day').value) };
			if (self.q('used_gb').value.trim() !== '') p.used_gb = Number(self.q('used_gb').value);
			if (!Number.isInteger(p.reset_day) || p.reset_day < 1 || p.reset_day > 31 ||
				['monthly_gb','daily_gb','used_gb'].some(function(k) { return p[k] != null && (!Number.isFinite(p[k]) || p[k] < 0 || p[k] > 100000); })) {
				M.toast('配置无效', {type:'error'}); return;
			}
			self._confirming = true;
			var confirmed = p.used_gb == null ? Promise.resolve(true) :
				M.confirmBox('校准本周期已用流量？', '这会将当前套餐周期已用值调整为输入值，每日和自然月记录不变。', {okText:'保存'});
			confirmed.then(function(yes) {
				if (yes && !self._disposed) return self.mutate(self.q('save'), function() { return M.callTrafficSet(JSON.stringify(p), slot); }, false, slot);
			}).finally(function() { self._confirming = false; });
		};
	}
});
