'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Power: the state of the power daemon (mu300-power), the profile in use, the charge limit and the knobs of
 * the three profiles. The backend is mu300dash power_get/power_set (unisoc-modem/power); the values are validated
 * there, a refused one comes back as ok != 1 and the page reloads to show what is really set. */
/* mu300-power's reason for the last state change (set_state's second argument, English in the daemon's log) in the
 * page's language; a reason this list does not know is shown as it is */
function reasonText(r) {
	var m, fixed = {
		'started': _('started'),
		'idle now (asked)': _('asleep now (asked)'),
		'daemon stopping': _('woken: power daemon stopping'),
		'woken by a key': _('woken by a key'),
		'woken by the Wi-Fi key': _('woken by the Wi-Fi key'),
		'woken (mu300-power wake)': _('woken (mu300-power wake)'),
		'woken by a computer on USB': _('woken by a computer on USB'),
		'woken: plugged in': _('woken: plugged in'),
		'woken by a Wi-Fi station': _('woken by a Wi-Fi client'),
		'charging boot (LK boot mode charger)': _('charging boot (powered on by the charger)'),
		'the hotspot is down': _('the hotspot is down'),
		'mu300-vpn is still stopped': _('the VPN is still stopped'),
		'EN-DC is still off after a failed resume': _('5G (EN-DC) is still off after a failed resume')
	};
	if (fixed[r]) return fixed[r];
	if ((m = /^self-check: (.*)$/.exec(r))) return _('self-check: %s').format(reasonText(m[1]));
	if ((m = /^idle: no client for ([0-9]+) min \((.*)\)$/.exec(r))) return _('asleep: no client for %d min (%s)').format(+m[1], m[2]);
	if ((m = /^idle: profile now (.*)$/.exec(r))) return _('asleep: profile now %s').format(m[1]);
	if ((m = /^woken by the (.*) key$/.exec(r))) return _('woken by the %s key').format(m[1]);
	if ((m = /^the modem is suspended \((.*)\)$/.exec(r))) return _('the modem is suspended (%s)').format(m[1]);
	return r;
}

return view.extend({
	load: function() { return L.resolveDefault(M.callPowerGet(), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callPowerGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* power_set 'set' KEY VALUE; on failure the error is shown and the page reloads to the real values */
	save: function(key, value) {
		var self = this;
		return M.callPowerSet('set', key, String(value)).then(function(r) {
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return self.reload();
			}
			M.toast(_('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
			return self.reload();
		});
	},

	/* sleep now / wake: the daemon needs a moment to switch the radios */
	act: function(btn, op) {
		var self = this;
		M.busy(btn, true);
		return M.callPowerSet(op, '', '').then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return self.reload();
			}
			setTimeout(function() { self.reload(); }, 2000);
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
			return self.reload();
		});
	},

	/* a select whose change is saved as KEY */
	choice: function(key, current, options, id) {
		var self = this;
		var sel = E('select', { 'class': 'cbi-input-select', 'id': id }, options.map(function(o) {
			return E('option', { 'value': o[0] }, [ o[1] ]);
		}));
		sel.value = String(current);
		sel.addEventListener('change', function() { self.save(key, sel.value); });
		return sel;
	},

	/* a whole number min..max saved as KEY; a value outside the range is refused here and the page reloads */
	number: function(key, current, max, id) {
		var self = this;
		var inp = E('input', { 'type': 'number', 'class': 'cbi-input-text', 'min': '0', 'max': String(max), 'step': '1',
			'style': 'width:6em', 'id': id, 'value': current == null ? '' : String(current) });
		inp.addEventListener('change', function() {
			var v = inp.value.trim();
			if (!/^[0-9]+$/.test(v) || +v > max) {
				M.toast(_('Enter a whole number from 0 to %d').format(max), { type: 'error', timeout: 6000 });
				return self.reload();
			}
			self.save(key, String(+v));
		});
		return inp;
	},

	paint: function(st) {
		var self = this, conf = st.conf || {}, bat = st.battery, chg = st.charger;
		/* no state in the reply: the backend did not answer; do not paint defaults as if they were real */
		if (!st.state) {
			dom.content(this.root, [ E('section', { 'class': 'mud-card', 'id': 'mud-power-unavailable' }, [
				E('h3', {}, _('Power')),
				E('div', { 'class': 'mud-note' }, [ _('Power status unavailable') + (st.error ? ': ' + M.errText(st) : '') ])
			]) ]);
			return;
		}
		var names = { plugged: _('Plugged in'), battery: _('On battery'), saver: _('Saver') };
		var row = function(k, v) {
			return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, k), E('span', { 'class': 'mud-v' }, v) ]);
		};
		var num = function(x) { return typeof x === 'number'; };

		/* 1. the state */
		var stateText = st.state === 'idle' ? _('Asleep: hotspot off')
			: st.state === 'charging-boot' ? _('Charging boot: press the Wi-Fi key to start') : _('Awake');
		var why = String(st.why || ''), under = /under ([0-9]+)/.exec(why);
		var whyText = why === 'forced' ? _('forced')
			: under ? _('automatic, battery under %d %%').format(+under[1]) : _('automatic');
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-power-state' }, [ E('h3', {}, _('Power')) ]);
		card.appendChild(row(_('State'), [ stateText ]));
		if (st.reason) card.appendChild(E('div', { 'class': 'mud-note' }, [ reasonText(String(st.reason)) ]));
		if (st.state === 'active' && num(st.idle_since_s) && st.idle_since_s > 10)
			card.appendChild(E('div', { 'class': 'mud-note' }, [ _('Nobody connected for %d s').format(st.idle_since_s) ]));
		if (st.supply === 'plugged' || st.supply === 'battery')
			card.appendChild(row(_('Power source'), [ st.supply === 'plugged' ? names.plugged : names.battery ]));
		if (names[st.profile])
			card.appendChild(row(_('Profile'), [ _('Profile in use: %s (%s)').format(names[st.profile], whyText) ]));
		if (bat) {
			var ok = num(bat.capacity) && num(bat.mv) && num(bat.ma) && num(bat.temp);
			card.appendChild(row(_('Battery'), [ ok
				? _('%d %%, %s, %d mV, %d mA, %.1f °C').format(bat.capacity, String(bat.status || '--'), bat.mv, bat.ma, bat.temp / 10)
				: '--' ]));
		}
		if (chg)
			card.appendChild(row(_('Charger'), [ _('Charger: %s, %s port').format(String(chg.status || '--'), String(chg.usb_type || '--'))
				+ (num(chg.input_ma) && chg.online === 1 ? ', ' + chg.input_ma + ' mA' : '') ]));
		if (st.charge_off === 'temp') card.appendChild(E('div', { 'class': 'mud-note' }, _('Charging paused: battery temperature')));
		else if (st.charge_off === 'limit') card.appendChild(E('div', { 'class': 'mud-note' }, _('Charging paused: charge limit')));
		var sleep = E('button', { 'class': 'mud-btn', 'id': 'mud-power-sleep' }, _('Sleep now'));
		sleep.addEventListener('click', function() { self.act(sleep, 'idle'); });
		var wake = E('button', { 'class': 'mud-btn', 'id': 'mud-power-wake' }, _('Wake'));
		wake.addEventListener('click', function() { self.act(wake, 'wake'); });
		card.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0' }, [ sleep, wake ]));
		card.appendChild(E('div', { 'class': 'mud-note' },
			_('Nobody connected means no Wi-Fi client and no computer on USB. The Wi-Fi key, a computer on USB or the charger wake the device.')));

		/* 2. the profile and the charge limit */
		var prof = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-power-profile' }, [
			E('h3', {}, _('Profile')),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [
				self.choice('PROFILE', conf.PROFILE || 'auto', [
					[ 'auto', _('Automatic: plugged in / on battery') ], [ 'plugged', names.plugged ],
					[ 'battery', names.battery ], [ 'saver', names.saver ]
				], 'mud-power-profile-sel')
			]),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [
				E('label', { 'for': 'mud-power-saver' }, _('Switch to Saver under %')),
				self.number('SAVER_BELOW', conf.SAVER_BELOW, 100, 'mud-power-saver')
			])
		]);
		/* the charge limit (CHARGE_TO 0-100), only where there is a battery (not the F50) */
		if (bat) {
			prof.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [
				E('label', { 'for': 'mud-power-chargeto' }, _('Stop charging at')),
				self.number('CHARGE_TO', num(conf.CHARGE_TO) ? conf.CHARGE_TO : 100, 100, 'mud-power-chargeto'), '%'
			]));
			prof.appendChild(E('div', { 'class': 'mud-note' }, _('0 % never charges the battery (the device runs on the charger), 100 % charges it full; in between, charging stops at that level and starts again 5 points lower. Under 5 % the battery always charges.')));
			prof.appendChild(E('div', { 'class': 'mud-note' }, _('80 % keeps the battery healthier on a device that stays plugged in.')));
			/* POWER_ON_CHARGER: a boot the charger started stays a charging boot (0) or is a normal boot (1) */
			prof.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [
				E('label', { 'for': 'mud-power-poweron' }, _('When the charger switches the device on')),
				self.choice('POWER_ON_CHARGER', conf.POWER_ON_CHARGER === 1 ? '1' : '0', [
					[ '0', _('Charge only, until a key starts it') ], [ '1', _('Start normally') ]
				], 'mud-power-poweron')
			]));
		}

		/* 3. the knobs of the three profiles */
		var knobs = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-power-knobs' });
		var rows = [ 'plugged', 'battery', 'saver' ].map(function(p) {
			var c = conf[p] || {}, id = 'mud-power-' + p + '-';
			return E('tr', {}, [
				E('td', {}, [ names[p] ]),
				E('td', {}, [ self.number(p + '.WIFI_IDLE', c.WIFI_IDLE, 9999, id + 'WIFI_IDLE') ]),
				E('td', {}, [ self.choice(p + '.RADIO_IDLE', c.RADIO_IDLE || 'keep', [
					[ 'keep', _('Keep as is') ], [ 'lte', _('LTE only (5G off)') ], [ 'off', _('Off') ] ], id + 'RADIO_IDLE') ]),
				E('td', {}, [ self.choice(p + '.LEDS_IDLE', c.LEDS_IDLE || 'on', [
					[ 'on', _('On') ], [ 'off', _('Off') ] ], id + 'LEDS_IDLE') ]),
				E('td', {}, [ self.choice(p + '.CPU', c.CPU || 'full', [
					[ 'full', _('Full') ], [ 'eco', _('Eco: big core off') ] ], id + 'CPU') ])
			]);
		});
		knobs.appendChild(E('table', { 'class': 'mud-table', 'id': 'mud-power-table' }, [
			E('tr', {}, [ E('th', {}, _('Profile')), E('th', {}, _('Hotspot off after (min, 0 = never)')),
				E('th', {}, _('Modem when idle')), E('th', {}, _('LEDs when idle')), E('th', {}, _('CPU')) ])
		].concat(rows)));

		var out = [ card, prof, knobs ];
		/* 4. lines of power.conf the daemon did not understand (it ran with the defaults for them) */
		var ign = (st.ignored || []).map(String);
		if (ign.length)
			out.push(E('div', { 'class': 'mud-note', 'id': 'mud-power-ignored', 'style': 'margin-top:14px' },
				[ _('Ignored lines in power.conf: %s').format(ign.join(', ')) ]));
		dom.content(this.root, out);
	}
});
