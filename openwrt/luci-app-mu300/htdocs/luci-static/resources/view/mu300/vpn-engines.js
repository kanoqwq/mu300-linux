'use strict';
'require view';
'require dom';
'require ui';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Engines: every engine of mu300-vpn (engines --json [--check]): the extras vpn-xray, vpn-singbox, vpn-mihomo,
 * vpn-dae and the system's packages wireguard-tools and openvpn. Install, update and remove wrap the CLI's
 * engine install/remove (mu300-extra underneath) as the background task; its output shows while it runs. */
return view.extend({
	load: function() {
		return Promise.all([ V.status(false), L.resolveDefault(V.call.engines(''), {}), L.resolveDefault(V.call.profiles(), {}) ]);
	},

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(data) {
		this.root = V.frame('engines');
		this.checked = false;
		this.log = null;
		this.paint(data[0], data[1], data[2]);
		var self = this;
		V.resume(function(job) { self.showLog(job); }, function(job) { self.showLog(job); self.reload(); });
		return this.root;
	},

	reload: function() {
		var self = this;
		return Promise.all([ V.status(false), L.resolveDefault(V.call.engines(this.checked ? '1' : ''), {}),
			L.resolveDefault(V.call.profiles(), {}) ]).then(function(d) { self.paint(d[0], d[1], d[2]); });
	},

	/* the task's output under the cards while it runs, and after */
	showLog: function(job) {
		this.log = job;
		var box = this.root.querySelector('#mvp-eng-log');
		if (!box || !job || !job.kind) return;
		if (job.kind !== 'engine-install' && job.kind !== 'core-install') return;
		box.style.display = '';
		dom.content(box.querySelector('h3'), [ V.jobLabel(job.kind) + ' · ' +
			(job.state === 'running' ? _('running') : job.state === 'done' ? _('done') : _('failed')) ]);
		var pre = box.querySelector('pre');
		pre.textContent = String(job.log || '');
		pre.scrollTop = pre.scrollHeight;
	},

	install: function(btn, e, source) {
		var self = this;
		var size = e.size ? ' ' + _('(about %s)').format(M.fmtBytes(e.size)) : '';
		var tick = function(job) { self.showLog(job); }, done = function(job) { self.showLog(job); self.reload(); };
		var go = function() {
			return V.start(btn, function() { return V.call.engineInstall(e.name, source, ''); }, tick, done);
		};
		if (source === 'file')
			return V.uploadPkg(btn, function(unv) { return V.call.engineInstall(e.name, 'file', unv); }, tick, done);
		return M.confirmBox(_('Install %s?').format(V.engineName(e.name)),
			(e.kind === 'package'
				? _('It is installed from this system\'s package feed.')
				: _('It is downloaded from the release of the VPN module and checked against its SHA256SUMS.') + size) + '\n\n' +
			_('An engine is tens of MB: on mobile data the download counts towards your plan.'),
			{ okText: _('Install') }).then(function(yes) { if (yes) return go(); });
	},

	remove: function(btn, e) {
		var self = this;
		return M.confirmBox(_('Remove %s?').format(V.engineName(e.name)),
			(e.needed_by || []).length
				? _('Profiles run on it: %s. They cannot start until it is installed again (the module refuses while the running profile needs it).').format(V.names(e.needed_by, this.profiles).join(', '))
				: _('No profile runs on it.'),
			{ danger: true, okText: _('Remove') }).then(function(yes) {
			if (!yes) return;
			return V.run(btn, function() { return V.call.engineRemove(e.name); }, _('Removed')).then(function() { return self.reload(); });
		});
	},

	paint: function(st, el, pl) {
		var self = this;
		if (!st || st.installed !== 1 || st.legacy) {
			V.paintFrame(this.root, 'engines', st, [], function() { self.reload(); });
			return;
		}
		var engines = (el && el.engines) || [];
		this.profiles = (pl && pl.profiles) || [];
		var running = this.log && this.log.state === 'running';

		var check = E('button', { 'class': 'mud-btn', 'id': 'mvp-eng-check' }, [ _('Check for updates') ]);
		check.addEventListener('click', function() {
			self.checked = true;
			M.busy(check, true);
			self.reload().finally(function() { M.busy(check, false); });
		});
		var top = V.card(_('Engines'), [
			V.note(_('Each engine is an extra of its own: install only the ones your profiles need. Xray runs vless, vmess, trojan and ss links (with hev-socks5-tunnel), sing-box the same links and its own JSON, mihomo Clash subscriptions, dae hysteria2, tuic and the rest on newer kernels. WireGuard and OpenVPN are packages of the system.')),
			E('div', { 'class': 'mvp-alert', 'style': 'margin-top:10px' }, [
				_('Downloads are tens of MB per engine: on mobile data they count towards your plan. Offline, upload the engine\'s tarball from the release page of the VPN module.') ]),
			E('div', { 'class': 'mvp-row' }, [ check, this.checked ? V.note(_('The available versions come from the latest release of the VPN module.')) : '' ])
		], 'mvp-eng-top');
		top.style.marginTop = '0';

		var cards;
		if (el && el.ok === 0) cards = [ V.note(M.errText(el), 'mvp-eng-error') ];
		else cards = engines.map(function(e) {
			var newer = e.installed && e.available && e.version && String(e.available) !== String(e.version);
			var acts = E('div', { 'class': 'mvp-row' });
			if (!e.installed || newer) {
				var ib = E('button', { 'class': 'mud-btn on', 'data-install': e.name, 'disabled': running ? '' : null },
					[ e.installed ? _('Update') : _('Install') ]);
				ib.addEventListener('click', function() { self.install(ib, e, 'release'); });
				acts.appendChild(ib);
			}
			if (e.kind !== 'package') {
				var ub = E('button', { 'class': 'mud-btn', 'data-upload': e.name, 'disabled': running ? '' : null }, [ _('Upload the file…') ]);
				ub.addEventListener('click', function() { self.install(ub, e, 'file'); });
				acts.appendChild(ub);
			}
			if (e.installed && e.kind !== 'bundle') {
				var rb = E('button', { 'class': 'mud-btn warn', 'data-remove': e.name, 'disabled': running ? '' : null }, [ _('Remove') ]);
				rb.addEventListener('click', function() { self.remove(rb, e); });
				acts.appendChild(rb);
			}
			var kind = e.kind === 'package' ? _('system package') : e.kind === 'bundle' ? _('in the vpn bundle') : _('extra %s').format(String(e.extra || ''));
			var stateP = !e.installed ? V.pill(_('not installed')) : e.usable === false ? V.pill(_('cannot run here'), 'bad') :
				newer ? V.pill(_('update available'), 'warn') : V.pill(_('installed'), 'ok');
			var c = E('section', { 'class': 'mud-card', 'data-engine': e.name }, [
				E('h3', {}, [ V.engineName(e.name) ]),
				E('div', { 'style': 'margin:-2px 0 6px' }, [ stateP ]),
				V.row(_('Kind'), kind),
				V.row(_('Version'), e.version || '--'),
				V.row(_('Available'), e.available || (self.checked ? '--' : _('not checked'))),
				V.row(_('Size'), e.size ? M.fmtBytes(e.size) : '--'),
				V.row(_('Needed by'), (e.needed_by || []).length ? V.names(e.needed_by, self.profiles).join(', ') : _('no profile'))
			]);
			if (e.usable === false && e.reason)
				c.appendChild(E('div', { 'class': 'mvp-alert bad', 'style': 'margin:8px 0 0' }, [ String(e.reason) ]));
			if (e.name === 'dae')
				c.appendChild(V.note(_('dae needs a kernel with BTF, 5.17 or newer: not the 5.4 vendor kernel (mu300-update kernel 6.18). It can be installed anyway.')));
			if (e.path) c.appendChild(E('div', { 'class': 'mvp-sub mvp-mono' }, [ String(e.path) ]));
			c.appendChild(acts);
			return c;
		});

		var log = E('section', { 'class': 'mud-card', 'id': 'mvp-eng-log', 'style': 'margin-top:12px;display:none' }, [
			E('h3', {}, [ '' ]), E('pre', { 'class': 'mvp-log' }, [ '' ]) ]);
		V.paintFrame(this.root, 'engines', st, [ top, E('div', { 'class': 'mvp-eng', 'style': 'margin-top:12px' }, cards), log ]);
		if (this.log) this.showLog(this.log);
	}
});
