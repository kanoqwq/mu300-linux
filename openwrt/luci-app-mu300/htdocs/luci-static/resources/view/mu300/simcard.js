'use strict';
'require view';
'require dom';
'require poll';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* Cellular > SIM settings: per card, its radio on or off (mu300-sim-radio, docs/FINDINGS.md 185: kept across reboots;
 * off is no calls, SMS or data on that card; both off is the persistent airplane mode), and VoLTE and VoNR (mobile-data voice, docs/FINDINGS.md 184; after kanoqwq's
 * per-SIM voice switches). The backend is mu300dash simcard_get/simcard_set. A choice for the card in use is sent at
 * once and read back (a modem that does not take it saves nothing); one for the other card waits for its next start.
 * Every bring-up of a card sends its choice again: VoLTE is on unless switched off, VoNR is the modem's own until set. */
return view.extend({
	load: function() { return L.resolveDefault(M.callSimcardGet(), {}); },

	render: function(st) {
		var self = this;
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st || {});
		/* a radio change runs in the background: follow it until it is over */
		poll.add(function() {
			if (!self.busy) return;
			return self.reload();
		}, 3);
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callSimcardGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	set: function(btn, kind, slot, state) {
		var self = this;
		M.busy(btn, true);
		return M.callSimcardSet(kind, String(slot), state).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(r.started ? _('Applying the radio setting…') : _('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	paint: function(st) {
		var self = this;
		if (st.ok !== 1 || !st.voice || !st.voice.slots) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('SIM settings') ]),
				E('p', {}, [ st.error ? M.errText(st) : _('SIM settings are not available') ])
			]));
			return;
		}
		var v = st.voice, live = v.live || null, rad = st.radio || null;
		this.busy = !!(rad && rad.busy);
		var word = function(b) { return b === true ? _('On') : b === false ? _('Off') : _('Modem default'); };
		var cards = [];
		[ 0, 1 ].forEach(function(slot) {
			if (slot === 1 && st.slot1_supported !== 1 && v.slot !== 1) return;
			var saved = v.slots[slot] || {}, inUse = v.slot === slot;
			var card = E('section', { 'class': 'mud-card', 'id': 'mud-simcard-' + slot }, [
				E('h3', {}, [ _('SIM%d').format(slot + 1) + (inUse ? ' (' + _('in use') + ')' : '') ])
			]);
			if (rad && rad.enabled) {
				var en = rad.enabled[slot] === true;
				var ron = E('button', { 'class': 'mud-btn' + (en ? ' on' : ''), 'id': 'mud-radio-' + slot + '-on', 'disabled': rad.busy ? '' : null }, [ _('On') ]);
				var roff = E('button', { 'class': 'mud-btn warn' + (!en ? ' on' : ''), 'id': 'mud-radio-' + slot + '-off', 'disabled': rad.busy ? '' : null }, [ _('Off') ]);
				ron.addEventListener('click', function() { self.set(ron, 'radio', slot, 'on'); });
				roff.addEventListener('click', function() {
					M.confirmBox(_('Turn off the radio of SIM%d').format(slot + 1),
						_('This SIM will have no calls, SMS or data, and its radio stays off after a reboot until you turn it on again.'), { danger: true })
						.then(function(go) { if (go) self.set(roff, 'radio', slot, 'off'); });
				});
				card.appendChild(E('div', { 'class': 'mud-r' }, [
					E('span', { 'class': 'mud-k' }, [ _('Radio') ]),
					E('span', { 'class': 'mud-v' }, [ en ? _('On') : _('Off') ])
				]));
				card.appendChild(E('div', { 'style': 'display:flex;gap:8px;margin:6px 0 12px' }, [ ron, roff ]));
			}
			[ [ 'volte', _('VoLTE') ], [ 'vonr', _('VoNR') ] ].forEach(function(k) {
				var now = saved[k[0]];
				var text = _('Saved: %s').format(word(now));
				if (inUse && live && (live[k[0]] === true || live[k[0]] === false))
					text += ' · ' + _('Modem: %s').format(word(live[k[0]]));
				var on = E('button', { 'class': 'mud-btn' + (now === true ? ' on' : ''), 'id': 'mud-' + k[0] + '-' + slot + '-on' }, [ _('On') ]);
				var off = E('button', { 'class': 'mud-btn' + (now === false ? ' on' : ''), 'id': 'mud-' + k[0] + '-' + slot + '-off' }, [ _('Off') ]);
				on.addEventListener('click', function() { self.set(on, k[0], slot, 'on'); });
				off.addEventListener('click', function() { self.set(off, k[0], slot, 'off'); });
				card.appendChild(E('div', { 'class': 'mud-r' }, [
					E('span', { 'class': 'mud-k' }, [ k[1] ]),
					E('span', { 'class': 'mud-v' }, [ text ])
				]));
				card.appendChild(E('div', { 'style': 'display:flex;gap:8px;margin:6px 0 12px' }, [ on, off ]));
			});
			if (!inUse)
				card.appendChild(E('div', { 'class': 'mud-note' }, [ _('Sent to this SIM when its radio next starts.') ]));
			cards.push(card);
		});
		var head = E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('SIM settings') ]),
				E('div', { 'class': 'mud-note' }, [ _('VoLTE and VoNR for each SIM, kept across reboots and sent again whenever that SIM starts. VoLTE is on unless switched off; VoNR stays as the modem has it until set.') ])
			]);
		if (rad && rad.enabled) {
			var allOff = E('button', { 'class': 'mud-btn warn' + (rad.all_off ? ' on' : ''), 'id': 'mud-radio-all-off', 'disabled': rad.busy ? '' : null }, [ _('All radios off (airplane mode)') ]);
			var allOn = E('button', { 'class': 'mud-btn', 'id': 'mud-radio-all-on', 'disabled': rad.busy ? '' : null }, [ _('All radios on') ]);
			allOff.addEventListener('click', function() {
				M.confirmBox(_('All radios off (airplane mode)'),
					_('No SIM will have calls, SMS or data, also after a reboot, until a radio is turned on again here or with the dashboard\'s radio button.'), { danger: true })
					.then(function(go) { if (go) self.set(allOff, 'radio', 'all', 'off'); });
			});
			allOn.addEventListener('click', function() { self.set(allOn, 'radio', 'all', 'on'); });
			head.appendChild(E('div', { 'class': 'mud-note' }, [ _('Each SIM\'s radio stays as set here after a reboot. A SIM whose radio is off has no calls, SMS or data, and the device does not switch to it.') ]));
			head.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0' }, [ allOff, allOn ]));
			if (rad.busy)
				head.appendChild(E('div', { 'class': 'mud-note' }, [ _('Applying the radio setting…') ]));
			else if (rad.result === 'failed')
				head.appendChild(E('div', { 'class': 'mud-note', 'style': 'color:#b45309' }, [ _('The setting is saved, but the radio did not follow it yet.') ]));
		}
		dom.content(this.root, [ head ].concat(cards));
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
