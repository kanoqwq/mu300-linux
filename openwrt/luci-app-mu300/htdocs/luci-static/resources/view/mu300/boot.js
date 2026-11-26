'use strict';
'require view';
'require mu300.common as M';
'require mu300.boot-controls as Boot';

return view.extend({
	render: function() {
		M.injectCss();
		var self = this, root = document.createElement('div');
		root.className = 'mud mud-boot-page';
		root.innerHTML = '<div class="mud-card mud-boot-settings"><h3>启动方式</h3>' +
			'<div class="mud-boot-mode-row"><b id="mud-boot-mode">正在读取…</b>' +
			'<label class="mud-boot-toggle"><span>永久启动 OpenWrt</span>' +
			'<input id="mud-boot-permanent" type="checkbox" role="switch" disabled><i aria-hidden="true"></i></label></div></div>' +
			'<div class="mud-card mud-boot-actions"><div class="mud-ctl">' +
			'<button class="mud-btn warn" id="mud-btn-reboot">关机或重启</button>' +
			'<button class="mud-btn warn" id="mud-btn-android" disabled>切换到 Android</button></div></div>';
		M.localize(root);
		this._root = root; this._disposed = false;
		var active = function() { return !self._disposed && root.isConnected; };
		var toggle = root.querySelector('#mud-boot-permanent');
		var refresh = function() {
			if (self._disposed) return Promise.resolve();
			if (self._reading) return self._reading;
			self._reading = M.callSysinfo().then(function(info) {
				if (!self._disposed) self.paint(info || {});
			}, function() {
				if (!self._disposed) self.paint({});
			}).finally(function() { self._reading = null; });
			return self._reading;
		};
		var act = function(op, arg, note, button) {
			var isButton = button.tagName === 'BUTTON';
			button.disabled = true;
			if (isButton) M.busy(button, true);
			return M.callBootAct(op, arg).then(function(reply) {
				if (!active()) return;
				M.toast(reply && reply.ok ? (note || '启动方式已保存') :
					'失败：' + ((reply && reply.error) || '未知错误'), { type: reply && reply.ok ? 'success' : 'error' });
			}, function(error) {
				if (active()) M.toast('调用失败', {type:'error'});
			}).finally(function() {
				button.disabled = false;
				if (isButton) M.busy(button, false);
			});
		};
		this.bootActionsDone = refresh;
		Boot.bind(self, root, act, active);
		toggle.onchange = function() {
			var wanted = this.checked;
			this.checked = self._permanent;
			var request = wanted ? Boot.permanent(self, active, this, act) : Boot.temporary(self, active, this, act);
			return request;
		};
		this._onFocus = function() { if (active() && !self._quickPending) refresh(); };
		window.addEventListener('focus', this._onFocus);
		// One refresh after the startup promotion; no extra polling lane.
		this._bootDeadline = setTimeout(function() {
			if (active() && !self._quickPending) refresh();
		}, 65000);
		return refresh().then(function() { return root; });
	},
	paint: function(info) {
		var boot = info.boot || {}, root = this._root;
		var known = boot.temporary === true || boot.temporary === false;
		this._permanent = boot.temporary === false;
		root.querySelector('#mud-boot-mode').textContent = M.translate(!known ? '当前启动方式未知' :
			this._permanent ? '当前为永久启动 OpenWrt' : '当前为临时启动 OpenWrt');
		var toggle = root.querySelector('#mud-boot-permanent');
		toggle.checked = this._permanent;
		toggle.disabled = !known || !boot.can_set_default || !!this._quickPending;
		root.querySelector('#mud-btn-android').disabled = !(info.capabilities && info.capabilities.dualboot) || !!this._quickPending;
	},
	unload: function() {
		this._disposed = true;
		window.removeEventListener('focus', this._onFocus);
		clearTimeout(this._bootDeadline);
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
