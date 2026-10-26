'use strict';
'require view';
'require dom';
'require ui';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Profiles: every profile of mu300-vpn (profile list --json: the CLI masks every secret), added from a share
 * link, pasted text, an uploaded file or a URL; renamed, removed, made the active one, tested (latency), checked, and
 * its raw config shown and edited behind an explicit reveal step (profile show --reveal / profile edit: the CLI checks
 * a config before it replaces anything). */

var TYPES = [ 'xray', 'sing-box', 'mihomo', 'wireguard', 'openvpn', 'dae' ];

/* the name a share link gives itself: its #fragment */
function linkName(link) {
	var i = String(link).indexOf('#');
	if (i < 0) return '';
	try { return decodeURIComponent(link.substring(i + 1)).trim().substring(0, 64); } catch (e) { return ''; }
}
/* a file's name without its extension, as a profile name */
function fileName(n) {
	return String(n || '').replace(/^.*[\\/]/, '').replace(/\.[^.]*$/, '').replace(/[\u0000-\u001f\u007f]/g, '').trim().substring(0, 64);
}

return view.extend({
	load: function() {
		return Promise.all([ V.status(false), L.resolveDefault(V.call.profiles(), {}) ]);
	},

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(data) {
		this.root = V.frame('profiles');
		this.lat = {};
		this.paint(data[0], data[1]);
		var self = this;
		V.resume(null, function(job) { self.jobDone(job); });
		return this.root;
	},

	reload: function() {
		var self = this;
		return Promise.all([ V.status(false), L.resolveDefault(V.call.profiles(), {}) ]).then(function(d) { self.paint(d[0], d[1]); });
	},

	/* a finished task: a latency round fills the column, an import shows the new profile */
	jobDone: function(job) {
		var self = this;
		/* (the CLI gives one object rather than an array when there is a single profile) */
		var res = job && job.kind === 'latency-all' ? job.result : null;
		if (res && !Array.isArray(res)) res = [ res ];
		(res || []).forEach(function(r) { if (r && r.id) self.lat[r.id] = r; });
		return this.reload();
	},

	latencyCell: function(p) {
		var r = this.lat[p.id];
		if (!r) return '--';
		if (r.ms != null) {
			var col = r.ms < 150 ? 'ok' : r.ms < 400 ? 'warn' : 'bad';
			return V.pill(_('%d ms').format(r.ms), col);
		}
		var why = r.error === 'udp' ? _('UDP: not testable') : r.error === 'kill switch' ? _('blocked by the kill switch') : String(r.error || _('failed'));
		return E('span', { 'class': 'mvp-pill bad', 'title': why }, [ why ]);
	},

	test: function(btn, p) {
		var self = this;
		return V.run(btn, function() { return V.call.latency(p.id); }).then(function(r) {
			if (!r || !r.result) return;
			self.lat[p.id] = r.result;
			var cell = self.root.querySelector('[data-lat="' + p.id + '"]');
			if (cell) dom.content(cell, [ self.latencyCell(p) ]);
		});
	},

	/* add: the dialog collects the source; a file is uploaded after it closes (LuCI's upload dialog is its own) */
	add: function() {
		var self = this, mode = 'link';
		var seg = E('div', { 'class': 'mvp-seg', 'id': 'mvp-add-mode' });
		var linkIn = E('input', { 'class': 'mvp-in', 'id': 'mvp-add-link', 'style': 'width:100%', 'autocomplete': 'off', 'spellcheck': 'false',
			'placeholder': 'vless://… vmess://… trojan://… ss://… hysteria2://… tuic://…' });
		var text = E('textarea', { 'class': 'mvp-ta', 'id': 'mvp-add-text', 'style': 'min-height:200px', 'spellcheck': 'false',
			'placeholder': _('A WireGuard or OpenVPN config, a Clash/mihomo YAML, an Xray or sing-box JSON, a dae config') });
		var url = E('input', { 'class': 'mvp-in', 'id': 'mvp-add-url', 'style': 'width:100%', 'autocomplete': 'off', 'spellcheck': 'false',
			'placeholder': 'https://…' });
		var fileNote = V.note(_('After OK, choose the file: a WireGuard .conf, an OpenVPN .ovpn, a Clash/mihomo .yaml, an Xray or sing-box .json or a dae config (at most 1 MiB).'));
		var name = E('input', { 'class': 'mvp-in mvp-grow', 'id': 'mvp-add-name', 'maxlength': '64', 'placeholder': _('From the source') });
		var type = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-add-type' },
			[ E('option', { 'value': 'auto' }, [ _('Detect') ]) ].concat(TYPES.map(function(t) { return E('option', { 'value': t }, [ V.typeName(t) ]); })));
		var panes = { link: [ linkIn ], text: [ text ], file: [ fileNote ], url: [ url,
			V.note(_('The URL may hold a token: it goes to the device on its own, never into a command line or a log.')) ] };
		var body = E('div', { 'id': 'mvp-add-body', 'style': 'margin:10px 0' });
		var typeRow = E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-add-type' }, [ _('Type') ]), type ]);
		var show = function(m) {
			mode = m;
			Array.prototype.forEach.call(seg.children, function(b) { b.classList.toggle('on', b.getAttribute('data-m') === m); });
			dom.content(body, panes[m]);
			typeRow.style.display = m === 'url' ? 'none' : '';
		};
		[ [ 'link', _('Share link') ], [ 'text', _('Paste') ], [ 'file', _('File') ], [ 'url', _('URL') ] ].forEach(function(m) {
			var b = E('button', { 'type': 'button', 'data-m': m[0] }, [ m[1] ]);
			b.addEventListener('click', function() { show(m[0]); });
			seg.appendChild(b);
		});
		show('link');
		var collect = function() {
			var r = { source: mode, name: name.value.trim(), type: mode === 'url' ? 'auto' : type.value, data: '' };
			if (mode === 'link') {
				r.data = linkIn.value.trim();
				if (!/^(vless|vmess|trojan|ss|hysteria2|hy2|tuic|wireguard|wg):\/\/\S+$/.test(r.data)) {
					M.toast(_('Not a share link this panel takes'), { type: 'error', timeout: 6000 });
					return null;
				}
				if (!r.name) r.name = linkName(r.data);
				/* hysteria2 and tuic run on dae (the other engines of the module take vless, vmess, trojan, ss) */
				if (r.type === 'auto' && /^(hysteria2|hy2|tuic):/.test(r.data)) r.type = 'dae';
			} else if (mode === 'text') {
				r.data = text.value;
				if (!r.data.trim()) { M.toast(_('The config is empty'), { type: 'error' }); return null; }
			} else if (mode === 'url') {
				r.data = url.value.trim();
				if (!/^https:\/\/[A-Za-z0-9.-]+(:[0-9]+)?(\/\S*)?$/.test(r.data)) {
					M.toast(_('Not an https URL'), { type: 'error', timeout: 6000 });
					return null;
				}
			}
			if (/[\u0000-\u001f\u007f]/.test(r.name) || /^-/.test(r.name)) {
				M.toast(_('Invalid name: 1-64 characters, no control characters'), { type: 'error', timeout: 6000 });
				return null;
			}
			return r;
		};
		var req = null;
		return V.modal(_('Add a profile'), [
			seg, body,
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-add-name' }, [ _('Name') ]), name ]),
			typeRow,
			V.note(_('Detect: the module tells the kind from the link or the file. Choose the type for a JSON that could be either Xray or sing-box, or for a link that should run on another engine.'))
		], [
			{ label: _('Cancel'), value: false },
			{ label: _('OK'), value: true, cls: 'on', id: 'mvp-add-ok', check: function() { req = collect(); return req != null; } }
		], { big: true, sticky: true }).then(function(ok) {
			if (!ok || !req) return;
			if (req.source === 'file') {
				return ui.uploadFile(V.UPLOAD_CFG).then(function(res) {
					if (!req.name) req.name = fileName(res && res.name);
					return self.submit(null, req);
				}, function(e) {
					if (e && e.message && !/cancel/i.test(e.message)) M.toast(_('Upload failed: %s').format(e.message), { type: 'error' });
				});
			}
			return self.submit(null, req);
		});
	},

	submit: function(btn, req) {
		var self = this;
		if (req.source === 'url')
			return V.start(btn, function() { return V.call.profileAdd('url', req.data, req.name, 'auto'); }, null,
				function() { self.reload(); });
		return V.run(btn, function() { return V.call.profileAdd(req.source, req.data, req.name, req.type); },
			_('The profile is added')).then(function() { return self.reload(); });
	},

	rename: function(p) {
		var self = this;
		var inp = E('input', { 'class': 'mvp-in', 'id': 'mvp-rename-in', 'style': 'width:100%', 'maxlength': '64', 'value': String(p.name || '') });
		return V.modal(_('Rename the profile'), [ inp ], [
			{ label: _('Cancel'), value: false }, { label: _('Save'), value: true, cls: 'on', id: 'mvp-rename-ok' }
		]).then(function(ok) {
			var n = inp.value.trim();
			if (!ok || !n || n === p.name) return;
			return V.run(null, function() { return V.call.profileRename(p.id, n); }, _('Saved')).then(function() { return self.reload(); });
		});
	},

	remove: function(p) {
		var self = this;
		return M.confirmBox(_('Delete the profile %s?').format(String(p.name || p.id)),
			p.active ? _('It is the active profile: with the VPN on, the module refuses; stop the VPN or choose another profile first.')
				: _('Its config and credentials are deleted from the device.'),
			{ danger: true, okText: _('Delete') }).then(function(yes) {
			if (!yes) return;
			return V.run(null, function() { return V.call.profileRemove(p.id); }, _('Deleted')).then(function() { return self.reload(); });
		});
	},

	check: function(p) {
		return V.run(null, function() { return V.call.profileCheck(p.id); }, _('The profile passes the check'));
	},

	/* the raw config: first the warning, then the backend's reveal; Save goes through profile edit, which checks it */
	config: function(p) {
		var self = this;
		return M.confirmBox(_('Show the config?'),
			_('The config holds the credentials of the profile (keys, passwords, UUIDs). It is shown on this screen only; anyone who can see the screen can read it.'),
			{ okText: _('Show') }).then(function(yes) {
			if (!yes) return;
			return L.resolveDefault(V.call.profileReveal(p.id), null).then(function(r) {
				if (!r || r.ok !== 1) {
					M.toast(r ? _('Failed: %s').format(M.errText(r)) : _('Management connection lost'), { type: 'error', timeout: 8000 });
					return;
				}
				var ta = E('textarea', { 'class': 'mvp-ta', 'id': 'mvp-cfg-text', 'spellcheck': 'false', 'autocomplete': 'off' });
				ta.value = String(r.config || '');
				var notes = [ V.note(_('Saving checks the config with the engine first (profile edit); a config that does not pass is not saved and the old one stays.')) ];
				if (p.source === 'subscription')
					notes.unshift(E('div', { 'class': 'mvp-alert' }, [ _('This profile comes from a subscription: its next refresh replaces your changes.') ]));
				return V.modal(_('Config of %s').format(String(p.name || p.id)) + ' · ' + V.typeName(p.type), [ ta ].concat(notes), [
					{ label: _('Close'), value: false },
					{ label: _('Save'), value: true, cls: 'on', id: 'mvp-cfg-save', check: function() {
						if (ta.value === r.config) return true;
						return V.run(null, function() { return V.call.profileEdit(p.id, ta.value); }, _('Saved')).then(function(x) { return !!x; });
					} }
				], { big: true, sticky: true }).then(function() {
					ta.value = '';
					r.config = null;
					return self.reload();
				});
			});
		});
	},

	more: function(p) {
		var self = this;
		return M.choiceBox(String(p.name || p.id), '', [
			{ label: _('Rename'), value: 'rename' }, { label: _('Show / edit the config'), value: 'config' },
			{ label: _('Check'), value: 'check' }, { label: _('Delete'), value: 'remove', danger: true }
		]).then(function(v) { if (v && self[v]) return self[v](p); });
	},

	paint: function(st, pl) {
		var self = this;
		if (!st || st.installed !== 1 || st.legacy) {
			V.paintFrame(this.root, 'profiles', st, [], function() { self.reload(); });
			return;
		}
		var profiles = (pl && pl.profiles) || [];
		var add = E('button', { 'class': 'mud-btn on', 'id': 'mvp-add' }, [ _('Add a profile') ]);
		add.addEventListener('click', function() { self.add(); });
		var all = E('button', { 'class': 'mud-btn', 'id': 'mvp-test-all', 'disabled': profiles.length ? null : '' }, [ _('Test the latency of all') ]);
		all.addEventListener('click', function() {
			V.start(all, function() { return V.call.latency('all'); }, null, function(job) { self.jobDone(job); });
		});
		var bar = E('div', { 'class': 'mvp-row', 'style': 'margin-top:0' }, [ add, all ]);

		var body;
		if (pl && pl.ok === 0) body = V.note(M.errText(pl), 'mvp-profiles-error');
		else if (!profiles.length) body = V.note(_('No profiles yet: add one from a share link, a config file or a subscription.'), 'mvp-profiles-empty');
		else {
			var rows = profiles.map(function(p) {
				var use = E('button', { 'class': 'mud-btn', 'data-use': p.id, 'disabled': p.active ? '' : null }, [ p.active ? _('Active') : _('Use') ]);
				use.addEventListener('click', function() {
					V.run(use, function() { return V.call.use(p.id); }, _('This profile is the active one now')).then(function() { self.reload(); });
				});
				var test = E('button', { 'class': 'mud-btn', 'data-test': p.id }, [ _('Test') ]);
				test.addEventListener('click', function() { self.test(test, p); });
				var more = E('button', { 'class': 'mud-btn', 'data-more': p.id, 'title': _('More') }, [ '⋯' ]);
				more.addEventListener('click', function() { self.more(p); });
				var chips = [];
				if (p.active) chips.push(V.pill(_('active'), 'brand'));
				if (p.source === 'subscription') chips.push(V.pill(_('subscription')));
				if (p.source === 'legacy') chips.push(V.pill(_('migrated')));
				if (p.stale) chips.push(V.pill(_('gone from its subscription'), 'warn'));
				var detail = [ p.protocol, p.transport, p.security ].filter(function(x) { return x; }).map(String).join(' · ');
				return E('tr', { 'class': p.active ? 'on' : null, 'data-id': p.id }, [
					E('td', { 'class': 'mvp-first' }, [ E('div', {}, [ String(p.name || p.id), ' ' ].concat(chips)),
						E('div', { 'class': 'mvp-sub' }, [ p.id + (detail ? ' · ' + detail : '') ]) ]),
					E('td', { 'data-k': _('Type') }, [ V.typeName(p.type) ]),
					E('td', { 'data-k': _('Server'), 'class': 'mvp-mono' }, [ V.server(p) ]),
					E('td', { 'data-k': _('Engine') }, [ V.engineName(p.engine),
						p.engine_installed === false ? E('div', {}, [ V.pill(_('not installed'), 'bad') ]) : '' ]),
					E('td', { 'data-k': _('Latency'), 'data-lat': p.id }, [ self.latencyCell(p) ]),
					E('td', {}, [ E('div', { 'class': 'mvp-acts' }, [ use, test, more ]) ])
				]);
			});
			body = E('table', { 'class': 'mvp-tbl', 'id': 'mvp-profiles-table' }, [
				E('thead', {}, [ E('tr', {}, [ E('th', {}, [ _('Name') ]), E('th', {}, [ _('Type') ]), E('th', {}, [ _('Server') ]),
					E('th', {}, [ _('Engine') ]), E('th', {}, [ _('Latency') ]), E('th', {}, '') ]) ]),
				E('tbody', {}, rows)
			]);
		}
		var c = V.card(_('Profiles'), [ bar, body,
			V.note(_('The latency is a TCP connection to the server, through the tunnel when the VPN is up. WireGuard and OpenVPN servers usually speak UDP and cannot be tested this way.'))
		], 'mvp-profiles');
		c.style.marginTop = '0';
		V.paintFrame(this.root, 'profiles', st, [ c ]);
	}
});
