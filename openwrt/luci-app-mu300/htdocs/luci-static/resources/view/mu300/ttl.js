'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* Cellular > TTL: the TTL (IPv6: hop limit) of everything that leaves through mobile data. The backend is mu300dash
 * ttl_get/ttl_set (unisoc-modem/ttl, mu300-ttl): tc on the mainline kernels, nftables (with the firewall's
 * fast-path off while a TTL is set) on the 5.4 vendor kernel. */
return view.extend({
	load: function() { return L.resolveDefault(M.callTtlGet(), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callTtlGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* run a ttl_set (a number from 1 to 255, or off) and show its result */
	set: function(btn, value) {
		var self = this;
		M.busy(btn, true);
		return M.callTtlSet(String(value)).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	paint: function(st) {
		var self = this, ifs = st.iface || [];

		var rule = st.backend === 'tc' ? 'tc (' + ifs.join(', ') + ')' : st.backend === 'nft' ? 'nftables' : '--';
		var rows = [
			[ _('Current setting'), st.enabled ? _('Fixed at %d').format(st.value) : _('Not changed (default)') ],
			[ _('Rule in force'), rule ]
		];
		if (st.offload === 0 || st.offload === 1)
			rows.push([ _('Flow offloading'), st.offload ? _('Enabled') : _('Disabled') ]);
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-ttl' }, [
			E('h3', {}, _('TTL of mobile data')),
			E('div', { 'class': 'mud-note' },
				_('Every packet that leaves through mobile data gets this TTL (IPv6: hop limit). A phone or laptop behind the hotspot sends 64 (Windows: 128) and the device forwards it with one less, which is how an operator can tell tethered traffic apart.'))
		].concat(rows.map(function(r) {
			return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ r[0] ]), E('span', { 'class': 'mud-v' }, [ r[1] ]) ]);
		})));
		if (st.backend === 'nft')
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-ttl-nft' },
				_('On this kernel the firewall fast-path is off while a TTL is set')));

		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' });
		[ 64, 65, 128 ].forEach(function(n) {
			var b = E('button', { 'class': 'mud-btn', 'id': 'mud-ttl-' + n }, [ _('Set %d').format(n) ]);
			b.addEventListener('click', function() { self.set(b, n); });
			actions.appendChild(b);
		});
		var input = E('input', { 'type': 'number', 'min': '1', 'max': '255', 'step': '1', 'class': 'cbi-input-text',
			'id': 'mud-ttl-value', 'style': 'width:6em', 'placeholder': '1-255' });
		var custom = E('button', { 'class': 'mud-btn', 'id': 'mud-ttl-custom' }, _('Set'));
		custom.addEventListener('click', function() {
			var v = String(input.value).trim();
			if (!/^[1-9][0-9]{0,2}$/.test(v) || +v > 255) {
				M.toast(_('Enter a number from 1 to 255'), { type: 'error' });
				return;
			}
			self.set(custom, +v);
		});
		actions.appendChild(input);
		actions.appendChild(custom);
		var off = E('button', { 'class': 'mud-btn warn', 'id': 'mud-ttl-off', 'disabled': st.enabled ? null : '' }, _('Turn off'));
		off.addEventListener('click', function() { self.set(off, 'off'); });
		actions.appendChild(off);
		card.appendChild(actions);
		card.appendChild(E('div', { 'class': 'mud-note' }, _("64 makes every packet leave like the device's own traffic")));

		dom.content(this.root, card);
	}
});
