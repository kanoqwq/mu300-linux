'use strict';
'require view';
'require dom';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Logs: the VPN service's log (mu300-vpn logs [ENGINE] -n N: journalctl or logread, filtered to one engine's
 * lines), the last N lines, refreshed on demand or every 5 s. It can name the hosts the device talked to: the
 * backend serves it to the write grant only, as the AT history. */
return view.extend({
	load: function() { return V.status(false); },

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(st) {
		this.root = V.frame('logs');
		var self = this;
		if (!st || st.installed !== 1 || st.legacy) {
			V.paintFrame(this.root, 'logs', st, [], function() { window.location.reload(); });
			return this.root;
		}
		var eng = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-log-engine' },
			[ E('option', { 'value': 'all' }, [ _('All engines') ]) ].concat(V.ENGINES.map(function(e) {
				return E('option', { 'value': e }, [ V.engineName(e) ]);
			})));
		var lines = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-log-lines' }, [ '50', '100', '200', '500', '1000' ].map(function(n) {
			return E('option', { 'value': n }, [ _('Last %s lines').format(n) ]);
		}));
		lines.value = '200';
		var refresh = E('button', { 'class': 'mud-btn on', 'id': 'mvp-log-refresh' }, [ _('Refresh') ]);
		var auto = E('input', { 'type': 'checkbox', 'id': 'mvp-log-auto' });
		this.pre = E('pre', { 'class': 'mvp-log', 'id': 'mvp-log-text' }, [ _('Loading…') ]);
		this.eng = eng; this.lines = lines;
		refresh.addEventListener('click', function() { M.busy(refresh, true); self.fetch().finally(function() { M.busy(refresh, false); }); });
		eng.addEventListener('change', function() { self.fetch(); });
		lines.addEventListener('change', function() { self.fetch(); });
		auto.addEventListener('change', function() {
			clearInterval(self._timer);
			if (auto.checked) self._timer = setInterval(function() {
				if (!document.documentElement.contains(self.root)) return clearInterval(self._timer);
				self.fetch();
			}, 5000);
		});
		var c = V.card(_('VPN service log'), [
			E('div', { 'class': 'mvp-row', 'style': 'margin-top:0' }, [ eng, lines, refresh,
				E('label', { 'for': 'mvp-log-auto', 'style': 'display:inline-flex;gap:6px;align-items:center;min-width:0' }, [ auto, _('Every 5 s') ]) ]),
			this.pre
		], 'mvp-log');
		c.style.marginTop = '0';
		V.paintFrame(this.root, 'logs', st, [ c ]);
		this.fetch();
		return this.root;
	},

	unload: function() { clearInterval(this._timer); },

	fetch: function() {
		var self = this, pre = this.pre;
		var atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
		return L.resolveDefault(V.call.logs(this.eng.value, this.lines.value), null).then(function(r) {
			if (!r || r.ok !== 1) {
				pre.textContent = r ? M.errText(r) : _('Management connection lost');
				return;
			}
			pre.textContent = String(r.log || '').trim() || _('The log is empty.');
			if (atEnd || !self.seen) pre.scrollTop = pre.scrollHeight;
			self.seen = true;
		});
	}
});
