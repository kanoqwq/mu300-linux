'use strict';
'require view';
'require dom';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Settings: the settings of mu300-vpn this panel sets (settings get/set: DNS, the LAN ranges that bypass the
 * tunnel, IPv6, Tailscale, the kill switch) and the options of each profile's type (profile set ID KEY VALUE: the
 * TUN stack of mihomo, the TLS pin and upstream proxy of link profiles, OpenVPN's user name and password). The rest
 * of an engine's settings (mihomo's mode, log levels, dae's groups and routing) lives in the profile's own config:
 * VPN > Profiles, Show / edit the config. */

var IP4 = /^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$/;
var IP6 = /^[0-9A-Fa-f]{0,4}(:[0-9A-Fa-f]{0,4}){2,7}$/;
function cidrOk(c) {
	var m = /^(.+)\/([0-9]{1,3})$/.exec(c);
	if (!m) return false;
	if (IP4.test(m[1])) return +m[2] <= 32;
	return IP6.test(m[1]) && +m[2] <= 128;
}

/* the options of each profile type, as the module's drivers name them (DRV_KEYS) */
var OPTIONS = {
	'mihomo': [ 'MIHOMO_STACK' ],
	'xray': [ 'UPSTREAM_HTTP_PROXY', 'TLS_PIN_SHA256' ],
	'sing-box': [ 'UPSTREAM_HTTP_PROXY' ],
	'openvpn': [ 'OVPN_USER', 'OVPN_PASS' ]
};

return view.extend({
	load: function() {
		return Promise.all([ V.status(false), L.resolveDefault(V.call.settings(), {}), L.resolveDefault(V.call.profiles(), {}) ]);
	},

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(data) {
		this.root = V.frame('settings');
		this.paint(data[0], data[1], data[2]);
		return this.root;
	},

	reload: function() {
		var self = this;
		return Promise.all([ V.status(false), L.resolveDefault(V.call.settings(), {}), L.resolveDefault(V.call.profiles(), {}) ])
			.then(function(d) { self.paint(d[0], d[1], d[2]); });
	},

	save: function(btn, key, value) {
		var self = this, running = this.running;
		return V.run(btn, function() { return V.call.settingSet(key, value); },
			running ? _('Saved: it applies from the next start (Restart)') : _('Saved')).then(function() { return self.reload(); });
	},

	option: function(btn, p, key, value) {
		var self = this;
		return V.run(btn, function() { return V.call.profileOption(p.id, key, value); },
			p.active && this.running ? _('Saved: it applies from the next start (Restart)') : _('Saved')).then(function() { return self.reload(); });
	},

	/* one setting: a label, the input, a Save button; check(value) says why a value is refused, or null */
	field: function(id, label, input, key, check, help) {
		var self = this;
		var btn = E('button', { 'class': 'mud-btn', 'id': id + '-save' }, [ _('Save') ]);
		btn.addEventListener('click', function() {
			var v = input.value.trim();
			var why = check ? check(v) : null;
			if (why) { M.toast(why, { type: 'error', timeout: 6000 }); return; }
			self.save(btn, key, v);
		});
		return E('div', {}, [
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': id }, [ label ]), input, btn ]),
			help ? E('div', { 'class': 'mud-note', 'style': 'margin:-4px 0 6px' }, [ help ]) : ''
		]);
	},

	paint: function(st, sl, pl) {
		var self = this;
		if (!st || st.installed !== 1 || st.legacy) {
			V.paintFrame(this.root, 'settings', st, [], function() { self.reload(); });
			return;
		}
		var s = (sl && sl.settings) || {}, profiles = (pl && pl.profiles) || [];
		this.running = !!(st.status && st.status.running);
		var input = function(id, value, ph) {
			return E('input', { 'class': 'mvp-in mvp-grow', 'id': id, 'value': value == null ? '' : String(value),
				'placeholder': ph || '', 'spellcheck': 'false', 'autocomplete': 'off' });
		};
		var dnsCheck = function(v) { return !v || IP4.test(v) || IP6.test(v) ? null : _('Enter an IPv4 or IPv6 address'); };

		/* the kill switch and the routing */
		var ks = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-set-ks' }, [
			E('option', { 'value': 'on' }, [ _('On: nothing leaves outside the tunnel') ]),
			E('option', { 'value': 'off' }, [ _('Off: traffic goes out directly while the tunnel is down') ]) ]);
		ks.value = s.KILL_SWITCH === '0' ? 'off' : 'on';
		ks.addEventListener('change', function() {
			V.run(null, function() { return V.call.killswitch(ks.value); }, _('Saved')).then(function() { self.reload(); });
		});
		var v6 = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-set-ipv6' }, [
			E('option', { 'value': 'auto' }, [ _('Automatic: through the tunnel where the engine can carry it') ]),
			E('option', { 'value': '1' }, [ _('Ask the engine for IPv6 in the tunnel') ]),
			E('option', { 'value': '0' }, [ _('Never through the tunnel (blocked while the VPN is up)') ]) ]);
		v6.value = [ 'auto', '0', '1' ].indexOf(String(s.IPV6)) >= 0 ? String(s.IPV6) : 'auto';
		v6.addEventListener('change', function() { self.save(null, 'IPV6', v6.value); });
		var ts = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-set-tailscale' }, [
			E('option', { 'value': '1' }, [ _('Yes: Tailscale\'s own connections go through the tunnel') ]),
			E('option', { 'value': '0' }, [ _('No: Tailscale connects directly') ]) ]);
		ts.value = s.TAILSCALE === '0' ? '0' : '1';
		ts.addEventListener('change', function() { self.save(null, 'TAILSCALE', ts.value); });

		var lan = input('mvp-set-lan', s.LAN_CIDRS, '192.168.0.0/16,10.0.0.0/8');
		var routing = V.card(_('Routing'), [
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-set-ks' }, [ _('Kill switch') ]), ks ]),
			E('div', { 'class': 'mud-note', 'style': 'margin:-4px 0 6px' }, [ _('The kill switch blocks all traffic of the device and of the LAN while the VPN is down.') ]),
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-set-ipv6' }, [ _('IPv6') ]), v6 ]),
			this.field('mvp-set-lan', _('Bypass the tunnel'), lan, 'LAN_CIDRS', function(v) {
				if (!v) return null;
				var parts = v.split(',');
				if (parts.length > 32 || !parts.every(cidrOk)) return _('Enter address ranges like 192.168.0.0/16, separated by commas');
				return null;
			}, _('Address ranges that never go into the tunnel, besides the LAN itself (which never does). Empty: the LAN only.')),
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-set-tailscale' }, [ _('Tailscale through the VPN') ]), ts ]),
			E('div', { 'class': 'mud-note', 'style': 'margin:-4px 0 6px' }, [ _('When Tailscale runs on the device, its WireGuard, DERP and control connections go through whichever tunnel runs (dae catches them like any other).') ])
		], 'mvp-set-routing');
		routing.style.marginTop = '0';

		var dns = V.card(_('DNS'), [
			this.field('mvp-set-remote', _('DNS through the tunnel'), input('mvp-set-remote', s.REMOTE_DNS, '1.1.1.1'), 'REMOTE_DNS', dnsCheck,
				_('The resolver the device\'s and the LAN\'s lookups go to, through the tunnel. Nothing goes to the carrier\'s resolver.')),
			this.field('mvp-set-bootstrap', _('Bootstrap DNS'), input('mvp-set-bootstrap', s.BOOTSTRAP_DNS, '1.1.1.1'), 'BOOTSTRAP_DNS', dnsCheck,
				_('Finds the VPN server\'s own address before the tunnel is up (directly, on the engine\'s own connection).')),
			V.note(_('Empty puts a setting back to its default.'))
		], 'mvp-set-dns');

		/* the options of each profile */
		var withOpts = profiles.filter(function(p) { return OPTIONS[p.type]; });
		var optRows = withOpts.map(function(p) {
			var o = p.options || {};
			var fields = OPTIONS[p.type].map(function(k) {
				var id = 'mvp-opt-' + p.id + '-' + k, el, label;
				if (k === 'MIHOMO_STACK') {
					label = _('TUN stack');
					el = E('select', { 'class': 'cbi-input-select', 'id': id }, [
						E('option', { 'value': '' }, [ _('Default (gvisor)') ]), E('option', { 'value': 'gvisor' }, [ 'gvisor' ]),
						E('option', { 'value': 'system' }, [ _('system (not on the 5.4 kernel)') ]), E('option', { 'value': 'mixed' }, [ 'mixed' ]) ]);
					el.value = o[k] || '';
				} else if (k === 'OVPN_PASS') {
					label = _('Password');
					el = E('input', { 'class': 'mvp-in mvp-grow', 'id': id, 'type': 'password', 'autocomplete': 'new-password',
						'placeholder': o[k] === 'set' ? _('set (unchanged unless you type a new one)') : _('not set') });
				} else {
					label = { OVPN_USER: _('User name'), UPSTREAM_HTTP_PROXY: _('Upstream HTTP proxy'), TLS_PIN_SHA256: _('TLS certificate pin (SHA-256)') }[k];
					el = input(id, o[k], k === 'UPSTREAM_HTTP_PROXY' ? 'host:port' : '');
				}
				var b = E('button', { 'class': 'mud-btn', 'id': id + '-save' }, [ _('Save') ]);
				b.addEventListener('click', function() {
					var v = el.tagName === 'SELECT' ? el.value : (k === 'OVPN_PASS' ? el.value : el.value.trim());
					if (k === 'UPSTREAM_HTTP_PROXY' && v && !/^([A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9A-Fa-f:.]+\]):[0-9]{1,5}$/.test(v)) {
						M.toast(_('Enter host:port'), { type: 'error', timeout: 6000 }); return;
					}
					if (k === 'TLS_PIN_SHA256' && !/^[0-9A-Fa-f:,]*$/.test(v)) {
						M.toast(_('Enter the SHA-256 in hex'), { type: 'error', timeout: 6000 }); return;
					}
					self.option(b, p, k, v);
				});
				var extra = [];
				if (k === 'OVPN_PASS' && o[k] === 'set') {
					var clr = E('button', { 'class': 'mud-btn warn', 'id': id + '-clear' }, [ _('Clear') ]);
					clr.addEventListener('click', function() { self.option(clr, p, k, ''); });
					extra.push(clr);
				}
				return E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': id }, [ label ]), el, b ].concat(extra));
			});
			return E('div', { 'class': 'mud-sec', 'data-profile': p.id }, [
				E('h3', {}, [ String(p.name || p.id) + ' · ' + V.typeName(p.type) ]) ].concat(fields));
		});
		var opts = V.card(_('Profile options'), (optRows.length ? optRows : [ V.note(_('None of the profiles has options of its type.')) ]).concat([
			V.note(_('mihomo\'s mode (rule, global, direct), the engines\' log levels, dae\'s groups and routing are part of each profile\'s own config: VPN > Profiles, Show / edit the config.'))
		]), 'mvp-set-options');

		V.paintFrame(this.root, 'settings', st, [ E('div', { 'class': 'mud-grid' }, [ routing, dns ]), opts ]);
		dns.style.marginTop = '0';
	}
});
