'use strict';
'require view';
'require dom';
'require rpc';
'require poll';
'require mu300.common as M';

return view.extend({
	load: function() { return L.resolveDefault(M.callSimGet(), {}); },
	render: function(st) {
		var self = this;
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st);
		poll.add(function() { return M.callSimGet().then(function(s) { self.paint(s); }); }, 3);
		return this.root;
	},
	set: function(op, value) {
		var self = this;
		return M.callSimSet(op, String(value)).then(function(r) {
			if (!r || r.ok !== 1) M.toast(M.errText(r), { type: 'error', timeout: 6000 });
			else if (r.started) M.toast(_('Switching SIM. Mobile data will disconnect briefly.'));
			return M.callSimGet().then(function(s) { self.paint(s); });
		});
	},
	paint: function(st) {
		var self = this;
		var live = st && st.capabilities ? st.capabilities.live_switch === true : !!(st && st.supported);
		var results = {
			failed: _('SIM switch failed; check SIM readiness and hot switching.'),
			idle: _('Ready'), switching: _('Switching SIM'), switched: _('SIM switched; saved as boot default'),
			rolled_back: _('Switch failed; previous SIM restored'),
			no_data: _('The target SIM got no mobile data; the previous SIM is restored and the saved boot preference is unchanged.'),
			no_data_kept: _('The target SIM has no mobile data yet, and the previous SIM had none either: the device stays on the target. The saved boot preference is unchanged.'),
			reboot_required: _('Radio state unknown; reboot required'),
			restarting: _('Radio state unknown; the device is restarting.')
		};
		/* The shared controller exposes boot selection on F50 and live switching only on verified U30 Air/OpenWrt. */
		if (!st || (st.capabilities && !st.capabilities.boot_selection) || (st.ok !== 1 && !st.result)) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('Dual SIM') ]),
				E('p', {}, [ _('Dual SIM control is only for the U30 Air on OpenWrt.') ])
			]));
			return;
		}
		/* the last boot's cold start of the chosen SIM failed: the device is on SIM1 without live switching */
		/* (the reasons are identifiers of mu300-sim2-start and mu300-sim2-warmup; one it does not know is shown as it is) */
		var generic = _('The chosen SIM could not be started at the last boot (no card, PIN, or the modem refused it).');
		var fallbacks = {
			'cold-start-failed': generic,
			'no-sim': _('No card answered in the chosen SIM position at the last boot.'),
			'sim-pin': _('The chosen SIM is locked by its PIN.'),
			'no-data': _('The chosen SIM got no mobile data within the time at its first boot.'),
			'brokers-missing': _('The second SIM\'s modem channels did not come up at the last boot.'),
			'unconfirmed': _('The last dual-SIM boot never confirmed itself.')
		};
		var card = E('section', { 'class': 'mud-card' }, [
			E('h3', {}, [ _('Dual SIM') ]),
			E('p', {}, [ _('A live switch saves the target as the next boot default only after it gets mobile data. Without data, the previous SIM is restored and the saved boot preference is unchanged. Each SIM uses its own APN/PDP profile from the WAN settings.') ]),
			E('p', {}, [ _('Active SIM') + ': ' + (st.active ? 'SIM' + st.active : _('Unknown')) ]),
			E('p', {}, [ _('Cold-boot default') + ': ' + (st.default ? 'SIM' + st.default : _('Unknown')) ]),
			E('p', {}, [ results[st.result] || _('SIM control unavailable') ])
		]);
		if (st.fallback)
			card.appendChild(E('p', { 'style': 'color:#b45309;font-weight:600' }, [
				(fallbacks[st.fallback] || (generic + ' (' + st.fallback + ')')) + ' ' + _('The device is back on SIM1 without live switching; choose again below to try once more.') ]));
		/* a second SIM that never had data under Linux is on trial for its first boot (mu300-sim2-warmup) */
		if (st.boot_check === 'waiting')
			card.appendChild(E('p', { 'style': 'color:#b45309;font-weight:600' }, [
				_('Waiting for the first mobile data on SIM2: %d s left. Without data the device goes back to SIM1 and restarts.').format(st.boot_check_left || 0) ]));
		var hot = E('input', { 'type': 'checkbox', 'checked': st.hot ? '' : null, 'disabled': (!live || st.busy) ? '' : null });
		hot.addEventListener('change', function() { self.set('hot', hot.checked ? 1 : 0); });
		card.appendChild(E('label', {}, [ hot, ' ', _('Enable hot switching') ]));
		if (!live)
			card.appendChild(E('p', {}, [ _('This device supports next-boot SIM selection only; live switching is unavailable.') ]));
		else if (!st.available)
			card.appendChild(E('p', {}, [ _('Reboot after enabling hot switching to prepare both SIM channels.') ]));
		[ 1, 2 ].forEach(function(n) {
			var row = E('div', { 'style': 'display:flex;gap:8px;margin:12px 0' });
			var b = E('button', { 'class': 'mud-btn',
				'disabled': (!live || !st.hot || !st.available || st.busy || !st.active || st.active === n) ? '' : null },
				[ _('Switch to SIM%d').format(n) ]);
			b.addEventListener('click', function() { self.set('switch', n); });
			var d = E('button', { 'class': 'mud-btn', 'disabled': st.busy || st.default === n ? '' : null },
				[ _('Use SIM%d at next boot').format(n) ]);
			d.addEventListener('click', function() { self.set('default', n); });
			row.appendChild(b); row.appendChild(d); card.appendChild(row);
		});
		dom.content(this.root, st.v50 ? [ this.v50(st.v50), card ] : card);
	},
	/* The ZTE V50's cards (mu300-sim card, docs/FINDINGS.md 204): ZTE's card IDs, two of them behind a switch on one
	   of the modem's two SIM interfaces. The choice is switched in before the modem starts, at the next boot. */
	v50: function(v) {
		var self = this;
		var names = [ _('Built-in SIM 1'), _('Built-in SIM 2'), _('Built-in SIM 3'), _('External SIM') ];
		var name = function(n) { return (n === null || n === undefined || !names[n]) ? _('Unknown') : names[n]; };
		var box = E('section', { 'class': 'mud-card' }, [
			E('h3', {}, [ _('SIM card') ]),
			E('p', {}, [ _('Built-in SIM 3 shares SIM2, the external SIM shares SIM1. The card chosen here is switched in before the modem starts, at the next boot; without a choice the cards stay as the bootloader sets them.') ]),
			E('p', {}, [ _('Active SIM') + ': ' + name(v.card) ]),
			E('p', {}, [ _('Cold-boot default') + ': ' + (v.saved_card === null || v.saved_card === undefined ? '-' : name(v.saved_card)) ])
		]);
		if (!v.cards || !v.switches) {
			box.appendChild(E('p', {}, [ _('SIM control unavailable') ]));
			return box;
		}
		var row = E('div', { 'style': 'display:flex;flex-wrap:wrap;gap:8px;margin:12px 0' });
		v.cards.forEach(function(n) {
			var b = E('button', { 'class': 'mud-btn', 'disabled': v.saved_card === n ? '' : null }, [ name(n) ]);
			b.addEventListener('click', function() { self.set('card', n); });
			row.appendChild(b);
		});
		var none = E('button', { 'class': 'mud-btn', 'disabled': (v.saved_card === null || v.saved_card === undefined) ? '' : null },
			[ _('As the bootloader sets them') ]);
		none.addEventListener('click', function() { self.set('card', 'none'); });
		row.appendChild(none);
		box.appendChild(row);
		return box;
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
