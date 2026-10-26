'use strict';
'require view';
'require uci';
'require mu300.common as M';

/* Status > Channel Analysis, in front of LuCI's own page (menu.d/luci-mu300-status-overrides.json: a file named after
 * luci-mod-status.json, whose action it replaces). LuCI's page scans for networks as soon as it opens and again on
 * every poll, and on this Wi-Fi driver a scan takes the access point off its channel: the hotspot's clients drop for a
 * moment, and a Mac on it did not rejoin by itself. With an access point configured, this page says so and starts
 * LuCI's page only on a click; without one (client mode only) it starts it at once. */

return view.extend({
	load: function() { return L.resolveDefault(uci.load('wireless')); },

	/* LuCI's own page: requiring a view module renders it into #view */
	original: function() { return L.require('view.status.channel_analysis'); },

	render: function() {
		var ap = (uci.sections('wireless', 'wifi-iface') || []).some(function(s) {
			return s.mode === 'ap' && s.disabled !== '1';
		});
		if (!ap) {
			this.original();
			return E('div', { 'class': 'spinning' }, _('Loading…'));
		}
		M.injectCss();
		var self = this, root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = '<div class="mud-sec" style="margin-top:0"><h3 id="mud-ca-title"></h3><div class="mud-note" id="mud-ca-note"></div>' +
			'<button class="mud-btn warn" id="mud-ca-go" style="margin-top:10px"></button></div>';
		root.querySelector('#mud-ca-title').textContent = _('Channel Analysis');
		root.querySelector('#mud-ca-note').textContent = _('A scan takes the Wi-Fi hotspot off its channel for a moment: connected devices drop, and some (macOS in particular) may not reconnect by themselves. This page scans as soon as it opens and again every few seconds. Reach the panel over USB or another way first if you depend on the hotspot.');
		var btn = root.querySelector('#mud-ca-go');
		btn.textContent = _('Scan anyway');
		btn.onclick = function() { M.busy(btn, true); self.original(); };
		return root;
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
