'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* Cellular > Data usage: mobile data per day and per billing cycle, the cap and its warning, from mu300dash
 * traffic_get/traffic_set (unisoc-modem/traffic, mu300-traffic: the same count as the mu300-traffic command, shared
 * by every system on the Linux disk). Sizes are binary (1 GB = 1024 MB), as everywhere on the panel. */

var GB = 1073741824;
var DAYS = 31;

/* the date d (YYYY-MM-DD) moved by n days, in UTC so that no time zone or DST shifts it */
function addDays(d, n) {
	var t = new Date(Date.UTC(+d.substring(0, 4), +d.substring(5, 7) - 1, +d.substring(8, 10)) + n * 86400000);
	return t.toISOString().substring(0, 10);
}

/* a size in GB as the user types it (comma or point), as whole bytes; null when it is not one */
function gbBytes(text) {
	var v = String(text).trim().replace(',', '.');
	if (!/^[0-9]+(\.[0-9]+)?$/.test(v) || +v > 1000000) return null;
	return String(Math.round(+v * GB));
}

return view.extend({
	load: function() { return L.resolveDefault(M.callTrafficGet(), {}); },

	render: function(st) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st || {});
		var self = this;
		/* a minute is the service's own pace; the page follows it */
		this._timer = setTimeout(function again() {
			if (!document.documentElement.contains(self.root)) return;
			self.reload().finally(function() { self._timer = setTimeout(again, 30000); });
		}, 30000);
		return this.root;
	},

	unload: function() { clearTimeout(this._timer); },

	/* nothing here goes through LuCI's Save & Apply */
	handleSaveApply: null, handleSave: null, handleReset: null,

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callTrafficGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* run a traffic_set and show its result */
	set: function(btn, op, value, done) {
		var self = this;
		M.busy(btn, true);
		return L.resolveDefault(M.callTrafficSet(op, String(value)), null).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(r ? _('Failed: %s').format(M.errText(r)) : _('Management connection lost'), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			if (done) done();
			return self.reload();
		});
	},

	row: function(k, v, id) {
		return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ k ]),
			E('span', { 'class': 'mud-v', 'id': id || null }, [ v ]) ]);
	},

	paint: function(st) {
		if (st.ok === 0 || !st.cycle) {
			dom.content(this.root, E('section', { 'class': 'mud-card', 'id': 'mud-traffic' }, [
				E('h3', {}, _('Data usage')),
				E('div', { 'class': 'mud-note', 'id': 'mud-traffic-error' }, [ st.error ? M.errText(st) : _('The data usage could not be read') ])
			]));
			return;
		}
		dom.content(this.root, [ this.summary(st), this.days(st), this.cycles(st), this.settings(st) ]);
	},

	summary: function(st) {
		var c = st.cycle, cap = st.cap || {}, today = st.today || {}, pend = st.pending || {};
		var capB = cap.bytes > 0 ? cap.bytes : 0, pct = capB ? Math.min(100, Math.round(c.used * 100 / capB)) : 0;
		var col = cap.level === 'over' ? 'var(--danger,#E25555)' : cap.level === 'warn' ? 'var(--warning,#f59e0b)' :
			'var(--brand,var(--primary,#3b82f6))';
		var kpis = E('div', { 'class': 'mud-kpis' }, [
			E('div', { 'class': 'mud-kpi' }, [ E('b', { 'id': 'mud-traffic-today' }, [ M.fmtBytes(today.rx + today.tx) ]),
				E('span', {}, [ _('Today') + ' · ↓ ' + M.fmtBytes(today.rx) + ' ↑ ' + M.fmtBytes(today.tx) ]) ]),
			E('div', { 'class': 'mud-kpi' }, [
				E('b', { 'id': 'mud-traffic-used', 'style': cap.level === 'ok' || cap.level === 'none' ? '' : 'color:' + col },
					[ capB ? _('%s of %s').format(M.fmtBytes(c.used), M.fmtBytes(capB)) : M.fmtBytes(c.used) ]),
				E('span', {}, [ _('This billing cycle') + ' · ' + c.start + ' – ' + c.last ]),
				capB ? E('div', { 'class': 'mud-meter' }, [ E('i', { 'style': 'width:' + pct + '%;background:' + col }) ]) : '' ])
		]);
		var rows = [
			this.row(_('Days left in this cycle'), String(c.days_left), 'mud-traffic-left'),
			this.row(_('Received'), M.fmtBytes(c.rx)),
			this.row(_('Sent'), M.fmtBytes(c.tx))
		];
		if (c.adj) rows.push(this.row(_('Corrected by'), (c.adj < 0 ? '−' : '+') + M.fmtBytes(Math.abs(c.adj))));
		rows.push(this.row(_('Monthly cap'), capB ? _('%s, warning at %d%%').format(M.fmtBytes(capB), cap.warn) : _('None'), 'mud-traffic-cap'));
		var notes = [];
		if (cap.level === 'over')
			notes.push(E('div', { 'class': 'mud-note', 'id': 'mud-traffic-alert', 'style': 'color:' + col }, [
				cap.cut_active ? _('The monthly cap is reached: mobile data was turned off. It comes back on when the next cycle starts.')
					: _('The monthly cap is reached') ]));
		else if (cap.level === 'warn')
			notes.push(E('div', { 'class': 'mud-note', 'id': 'mud-traffic-alert', 'style': 'color:' + col }, [
				_('%d%% of the monthly cap is used').format(pct) ]));
		if (pend.rx + pend.tx > 0)
			notes.push(E('div', { 'class': 'mud-note', 'id': 'mud-traffic-pending' }, [
				_('%s waits for the clock: it has not been set over the network yet. It already counts towards this cycle and goes to its day once the clock is set.').format(M.fmtBytes(pend.rx + pend.tx)) ]));
		if (!st.available)
			notes.push(E('div', { 'class': 'mud-note' }, [ _('The interface %s is not there right now: nothing is counted until it comes back.').format(st.device) ]));
		notes.push(E('div', { 'class': 'mud-note' }, [
			_('Counted on %s, both ways, as the operator counts it (a VPN\'s overhead included).').format(st.device) + ' ' +
			_('The count is kept in memory and written to the disk every 30 minutes and at shutdown: a power cut loses at most the last half hour.') +
			(st.since ? ' ' + _('Counting since %s.').format(new Date(st.since * 1000).toISOString().substring(0, 10)) : '') ]));
		return E('section', { 'class': 'mud-card', 'id': 'mud-traffic' }, [ E('h3', {}, _('Data usage')), kpis ].concat(rows, notes));
	},

	/* the last DAYS days to today (none before counting started), a day without traffic as zero, each with a bar of
	 * its share of the busiest */
	days: function(st) {
		var by = {}, list = [], peak = 1, first = null;
		(st.days || []).forEach(function(d) { by[d.date] = d; if (!first || d.date < first) first = d.date; });
		if (st.since) {
			var s = new Date((st.since + 0) * 1000).toISOString().substring(0, 10);
			if (!first || s < first) first = s;
		}
		for (var i = 0; i < DAYS; i++) {
			var date = addDays(st.today.date, -i), d = by[date] || { rx: 0, tx: 0 };
			if (i > 0 && (!first || date < first)) break;
			list.push({ date: date, rx: d.rx, tx: d.tx });
			peak = Math.max(peak, d.rx + d.tx);
		}
		var body = list.map(function(d) {
			var w = Math.round((d.rx + d.tx) * 100 / peak);
			return E('tr', {}, [ E('td', {}, [ d.date ]),
				E('td', { 'style': 'width:35%;min-width:40px' }, [ E('div', { 'class': 'mud-meter', 'style': 'margin:0' },
					[ E('i', { 'style': 'width:' + w + '%;background:var(--brand,var(--primary,#3b82f6))' }) ]) ]),
				E('td', {}, [ M.fmtBytes(d.rx) ]), E('td', {}, [ M.fmtBytes(d.tx) ]), E('td', {}, [ M.fmtBytes(d.rx + d.tx) ]) ]);
		});
		return E('section', { 'class': 'mud-card', 'style': 'margin-top:10px' }, [ E('h3', {}, _('Per day')),
			E('div', { 'class': 'mud-scroll', 'style': 'max-height:340px' }, [ E('table', { 'class': 'mud-table', 'id': 'mud-traffic-days', 'style': 'white-space:nowrap' }, [
				E('thead', {}, [ E('tr', {}, [ E('th', {}, _('Date')), E('th', {}, ''), E('th', {}, _('Received')),
					E('th', {}, _('Sent')), E('th', {}, _('Total')) ]) ]),
				E('tbody', {}, body) ]) ]) ]);
	},

	cycles: function(st) {
		var body = (st.cycles || []).map(function(c) {
			return E('tr', {}, [ E('td', {}, [ c.start + ' – ' + c.last ]), E('td', {}, [ M.fmtBytes(c.rx) ]),
				E('td', {}, [ M.fmtBytes(c.tx) ]), E('td', {}, [ M.fmtBytes(Math.max(0, c.rx + c.tx + (c.adj || 0))) ]) ]);
		});
		return E('section', { 'class': 'mud-card', 'style': 'margin-top:10px' }, [ E('h3', {}, _('Per billing cycle')),
			E('table', { 'class': 'mud-table', 'id': 'mud-traffic-cycles', 'style': 'white-space:nowrap' }, [
				E('thead', {}, [ E('tr', {}, [ E('th', {}, _('Billing cycle')), E('th', {}, _('Received')),
					E('th', {}, _('Sent')), E('th', {}, _('Total')) ]) ]),
				E('tbody', {}, body) ]) ]);
	},

	settings: function(st) {
		var self = this, cap = st.cap || {};
		var field = function(label, input, btn, note) {
			return E('div', { 'style': 'margin:8px 0' }, [
				E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center' },
					[ E('span', { 'class': 'mud-k', 'style': 'flex:1 1 220px' }, [ label ]), input, btn ]),
				note ? E('div', { 'class': 'mud-note', 'style': 'margin-top:3px' }, [ note ]) : '' ]);
		};
		var input = function(id, value, width) {
			return E('input', { 'type': 'text', 'inputmode': 'decimal', 'class': 'cbi-input-text', 'id': id,
				'style': 'width:' + (width || '7em'), 'value': value });
		};

		var day = E('select', { 'class': 'cbi-input-select', 'id': 'mud-traffic-reset' });
		for (var d = 1; d <= 31; d++)
			day.appendChild(E('option', { 'value': String(d), 'selected': d === st.reset_day ? '' : null }, [ String(d) ]));
		var dayBtn = E('button', { 'class': 'mud-btn' }, _('Save'));
		dayBtn.addEventListener('click', function() { self.set(dayBtn, 'reset_day', day.value); });

		var capIn = input('mud-traffic-capin', cap.bytes > 0 ? String(+(cap.bytes / GB).toFixed(2)) : '0');
		var capBtn = E('button', { 'class': 'mud-btn' }, _('Save'));
		capBtn.addEventListener('click', function() {
			var b = gbBytes(capIn.value);
			if (b == null) { M.toast(_('Enter a size in GB, for example 50 or 2.5'), { type: 'error' }); return; }
			self.set(capBtn, 'cap', b);
		});

		var warnIn = input('mud-traffic-warn', String(cap.warn || 90), '5em');
		var warnBtn = E('button', { 'class': 'mud-btn' }, _('Save'));
		warnBtn.addEventListener('click', function() {
			var v = String(warnIn.value).trim();
			if (!/^[1-9][0-9]?$|^100$/.test(v)) { M.toast(_('Enter a percentage from 1 to 100'), { type: 'error' }); return; }
			self.set(warnBtn, 'warn', v);
		});

		var cutBtn = E('button', { 'class': 'mud-btn' + (cap.cut ? ' on' : ''), 'id': 'mud-traffic-cut' },
			cap.cut ? _('On') : _('Off'));
		cutBtn.addEventListener('click', function() {
			if (cap.cut) { self.set(cutBtn, 'cut', 'off'); return; }
			M.confirmBox(_('Turn mobile data off at the cap'),
				_('When this cycle reaches the cap, mobile data is turned off: no internet through the operator until the next cycle starts, the cap is raised or this is turned off again. Turning mobile data back on by hand keeps it on until the next reboot.'),
				{ danger: true, okText: _('On') }).then(function(go) { if (go) self.set(cutBtn, 'cut', 'on'); });
		});

		var usedIn = input('mud-traffic-usedin', '');
		var usedBtn = E('button', { 'class': 'mud-btn' }, _('Set'));
		usedBtn.addEventListener('click', function() {
			var b = gbBytes(usedIn.value);
			if (b == null) { M.toast(_('Enter a size in GB, for example 50 or 2.5'), { type: 'error' }); return; }
			self.set(usedBtn, 'used', b, function() { usedIn.value = ''; });
		});

		var clearBtn = E('button', { 'class': 'mud-btn warn', 'id': 'mud-traffic-clear' }, _('Clear'));
		clearBtn.addEventListener('click', function() {
			M.confirmBox(_('Clear the count'), _('Every day and billing cycle counted so far is forgotten. Counting goes on from now.'),
				{ danger: true, okText: _('Clear') }).then(function(go) { if (go) self.set(clearBtn, 'clear', 'yes'); });
		});

		return E('section', { 'class': 'mud-card', 'style': 'margin-top:10px', 'id': 'mud-traffic-settings' }, [
			E('h3', {}, _('Billing cycle and cap')),
			field(_('A billing cycle starts on day'), day, dayBtn, _('31: on the last day of each month')),
			field(_('Monthly cap (GB, 0: none)'), capIn, capBtn),
			field(_('Warning at this percentage of the cap'), warnIn, warnBtn),
			field(_('Turn mobile data off at the cap'), cutBtn, ''),
			field(_('Used so far in this cycle (GB)'), usedIn, usedBtn,
				_('What the operator shows for this cycle (for example when counting started in the middle of it); the count is corrected to it.')),
			field(_('Clear the count'), clearBtn, '')
		]);
	}
});
