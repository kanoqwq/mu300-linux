'use strict';
'require view';
'require dom';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Overview: the state of the VPN (mu300-vpn status --json), start/stop/restart, the kill switch, the active
 * profile and the IPv6 rule of the module (spec 2026-10-11-engine-extras, sections 4, 6 and 7). The tunnel's traffic
 * comes from /proc/net/dev (the dashboard's mu300rates read), the uptime from the backend's "since". */
return view.extend({
	load: function() {
		return Promise.all([ V.status(false), L.resolveDefault(V.call.profiles(), {}) ]);
	},

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(data) {
		this.root = V.frame('overview');
		this.exitIp = null;
		this.rates = { t: 0, rx: null, tx: null, rxr: null, txr: null };
		this.paint(data[0], data[1]);
		var self = this;
		this._timer = setTimeout(function again() {
			if (!document.documentElement.contains(self.root)) return;
			self.reload().finally(function() { self._timer = setTimeout(again, 5000); });
		}, 5000);
		this._rate = setInterval(function() { self.tickRates(); }, 2000);
		V.resume(null, function() { self.reload(); });
		return this.root;
	},

	unload: function() { clearTimeout(this._timer); clearInterval(this._rate); },

	reload: function() {
		var self = this;
		return Promise.all([ V.status(false), L.resolveDefault(V.call.profiles(), {}) ]).then(function(d) {
			/* not while a choice is being made in one of the page's selects: the repaint would replace it */
			var a = document.activeElement;
			if (a && a.tagName === 'SELECT' && self.root.contains(a)) return;
			self.paint(d[0], d[1]);
		});
	},

	/* the tunnel's bytes and rates, every 2 s while the tunnel is up */
	tickRates: function() {
		var self = this, iface = this.iface;
		if (!iface || !document.documentElement.contains(this.root)) return;
		M.callNetDev().then(function(text) {
			var c = M.netDev(text, iface), now = Date.now(), r = self.rates;
			if (!c) return;
			if (r.t && r.rx != null && now > r.t) {
				r.rxr = Math.max(0, (c.rx - r.rx) * 1000 / (now - r.t));
				r.txr = Math.max(0, (c.tx - r.tx) * 1000 / (now - r.t));
			}
			r.t = now; r.rx = c.rx; r.tx = c.tx;
			M.set('vpn-rx', M.fmtBytes(c.rx));
			M.set('vpn-tx', M.fmtBytes(c.tx));
			M.set('vpn-rate', r.rxr == null ? '--' : '↓ ' + M.fmtRate(r.rxr) + '  ↑ ' + M.fmtRate(r.txr));
		}, function() {});
	},

	act: function(btn, op) {
		var self = this, text = { on: _('The VPN is starting'), off: _('The VPN is stopped'), restart: _('The VPN is restarting') }[op];
		return V.run(btn, function() { return V.call.service(op); }, text).then(function() { return self.reload(); });
	},

	killswitch: function(btn, on) {
		var self = this;
		var msg = on
			? _('With the kill switch on, nothing leaves the device or the LAN outside the tunnel: while the VPN is down, starting or broken, every connection fails instead of going out directly. It stays up until you stop the VPN here (Stop removes it).')
			: _('Without the kill switch, traffic goes out directly, unprotected, whenever the tunnel is down (while it starts, after a crash, when the engine is missing).');
		return M.confirmBox(on ? _('Turn the kill switch on?') : _('Turn the kill switch off?'), msg,
			{ danger: !on, okText: on ? _('Turn on') : _('Turn off') }).then(function(yes) {
			if (!yes) return;
			return V.run(btn, function() { return V.call.killswitch(on ? 'on' : 'off'); }, _('Saved')).then(function() { return self.reload(); });
		});
	},

	checkExitIp: function(btn) {
		var self = this;
		M.busy(btn, true);
		return V.status(true).then(function(st) {
			M.busy(btn, false);
			var ip = st && st.status ? st.status.exit_ip : null;
			self.exitIp = ip || '';
			M.set('vpn-exit', ip ? String(ip) : _('unknown'));
			if (!ip) M.toast(_('The exit address could not be found (is the tunnel up?)'), { type: 'error', timeout: 6000 });
		});
	},

	paint: function(st, pl) {
		var self = this;
		this.iface = null;
		if (!st || st.installed !== 1 || st.legacy || !st.status) {
			V.paintFrame(this.root, 'overview', st, [], function() { self.reload(); });
			return;
		}
		var s = st.status, prof = s.profile, eng = s.engine || {}, tun = s.tunnel || {}, ks = s.kill_switch || {},
			v6 = s.ipv6 || {}, profiles = (pl && pl.profiles) || [];
		var up = !!(s.running && tun.up);
		this.iface = up && tun.iface ? String(tun.iface) : null;
		if (!this.iface) this.rates = { t: 0, rx: null, tx: null, rxr: null, txr: null };

		var stateText, stateCol;
		if (up) { stateText = _('Connected'); stateCol = 'var(--success,#2FBF71)'; }
		else if (s.enabled && s.running) { stateText = _('Connecting…'); stateCol = 'var(--warning,#F2B544)'; }
		else if (s.enabled) { stateText = _('On, but not running'); stateCol = 'var(--danger,#E25555)'; }
		else { stateText = _('Stopped'); stateCol = 'var(--text-muted,#888)'; }

		var alerts = [];
		if (s.missing_engine) {
			var inst = E('button', { 'class': 'mud-btn on', 'id': 'mvp-missing-install' }, [ _('Install it') ]);
			var engN = String(s.missing_engine).replace(/^vpn-/, '').replace(/^singbox$/, 'sing-box');
			inst.addEventListener('click', function() {
				V.start(inst, function() { return V.call.engineInstall(engN, 'release', ''); }, null, function() { self.reload(); });
			});
			alerts.push(E('div', { 'class': 'mvp-alert bad', 'id': 'mvp-missing' }, [
				E('b', {}, [ _('The engine of the active profile is not installed: %s').format(String(s.missing_engine)) ]),
				E('div', { 'class': 'mud-note' }, [ ks.setting
					? _('The kill switch keeps everything closed until it is there; the service downloads it by itself when it can.')
					: _('The VPN cannot start until it is installed.') ]),
				E('div', { 'class': 'mvp-row' }, [ inst ])
			]));
		}
		if (eng.installed && eng.usable === false)
			alerts.push(E('div', { 'class': 'mvp-alert bad', 'id': 'mvp-unusable' }, [
				E('b', {}, [ _('%s cannot run on this system').format(V.engineName(eng.name)) ]),
				E('div', { 'class': 'mud-note' }, [ String(eng.reason || '') ])
			]));
		if (s.enabled && !prof)
			alerts.push(E('div', { 'class': 'mvp-alert', 'id': 'mvp-noprofile' }, [ _('The VPN is on, but no profile is active: choose one below.') ]));

		/* the hero: the state, the profile, the tunnel */
		var hero = E('section', { 'class': 'mud-card mud-hero', 'id': 'mvp-hero' }, [
			E('div', { 'class': 'mud-hero-l' }, [
				E('div', { 'class': 'mvp-state', 'id': 'mud-vpn-state', 'style': 'color:' + stateCol }, [
					E('span', { 'class': 'mud-dot' + (up ? ' on' : ''), 'style': 'width:12px;height:12px;background:' + stateCol }), stateText ]),
				E('div', { 'class': 'mud-op', 'id': 'mud-vpn-profile' }, [ prof
					? _('Profile: %s').format(String(prof.name || prof.id))
					: _('No active profile') ]),
				E('div', { 'class': 'mud-cellline' }, [
					prof ? V.typeName(prof.type) + ' · ' + _('engine %s').format(V.engineName(eng.name || prof.engine)) : '',
					tun.iface ? ' · ' + _('interface %s').format(String(tun.iface)) : '',
					(tun.addresses || []).length ? ' · ' + (tun.addresses || []).map(String).join(', ') : ''
				])
			]),
			E('div', { 'class': 'mud-hero-r' }, [
				E('div', { 'class': 'mud-chips' }, [
					ks.active ? V.pill(_('Kill switch active'), 'ok') : ks.setting ? V.pill(_('Kill switch on'), 'warn') : V.pill(_('Kill switch off')),
					v6.tunnel ? V.pill(_('IPv6 in the tunnel'), 'ok') : v6.guard ? V.pill(_('IPv6 blocked'), 'warn') : V.pill(_('IPv6 untouched'))
				]),
				E('div', { 'class': 'mud-sub' }, [ s.version ? _('Module %s').format(String(s.version)) : '' ])
			])
		]);

		/* the CLI's own "since" when it gives one, else the backend's (when the tunnel's interface file was written) */
		var since = typeof s.since === 'number' ? s.since : st.since;
		var uptime = up && since && st.now ? M.fmtUptime(Math.max(0, st.now - since)) : '--';
		var kpi = function(id, label, val) {
			return E('div', { 'class': 'mud-kpi' }, [ E('b', { 'id': 'mud-' + id }, [ val ]), E('span', {}, [ label ]) ]);
		};
		var exitBtn = E('button', { 'class': 'mud-btn', 'id': 'mvp-exit-check', 'disabled': up ? null : '' }, [ _('Check') ]);
		exitBtn.addEventListener('click', function() { self.checkExitIp(exitBtn); });
		var r = this.rates;
		var kpis = E('div', { 'class': 'mud-kpis', 'style': 'margin-top:12px' }, [
			kpi('vpn-uptime', _('Uptime'), uptime),
			kpi('vpn-rx', _('Received'), up && r.rx != null ? M.fmtBytes(r.rx) : '--'),
			kpi('vpn-tx', _('Sent'), up && r.tx != null ? M.fmtBytes(r.tx) : '--'),
			kpi('vpn-rate', _('Rate'), up && r.rxr != null ? '↓ ' + M.fmtRate(r.rxr) + '  ↑ ' + M.fmtRate(r.txr) : '--'),
			E('div', { 'class': 'mud-kpi' }, [
				E('b', { 'id': 'mud-vpn-exit' }, [ this.exitIp == null ? '--' : (this.exitIp || _('unknown')) ]),
				E('span', { 'style': 'display:flex;justify-content:space-between;align-items:center;gap:6px' }, [ _('Exit address'), exitBtn ])
			])
		]);

		/* start / stop / restart */
		var bOn = E('button', { 'class': 'mud-btn on', 'id': 'mvp-start', 'disabled': s.enabled ? '' : null }, [ _('Start') ]);
		var bOff = E('button', { 'class': 'mud-btn warn', 'id': 'mvp-stop', 'disabled': s.enabled ? null : '' }, [ _('Stop') ]);
		var bRe = E('button', { 'class': 'mud-btn', 'id': 'mvp-restart', 'disabled': s.enabled ? null : '' }, [ _('Restart') ]);
		bOn.addEventListener('click', function() { self.act(bOn, 'on'); });
		bOff.addEventListener('click', function() {
			M.confirmBox(_('Stop the VPN?'), _('Traffic then goes out directly, without the tunnel; the kill switch is taken down with it.'),
				{ danger: true, okText: _('Stop') }).then(function(yes) { if (yes) self.act(bOff, 'off'); });
		});
		bRe.addEventListener('click', function() { self.act(bRe, 'restart'); });
		var ctl = V.card(_('VPN'), [
			E('div', { 'class': 'mud-ctl' }, [ bOn, bOff, bRe ]),
			V.note(s.enabled ? _('The VPN is on: it starts again by itself after a reboot.') : _('The VPN is off.'))
		], 'mvp-control');

		/* the kill switch */
		var ksBtn = E('button', { 'class': 'mud-btn' + (ks.setting ? ' warn' : ' on'), 'id': 'mvp-ks-toggle' },
			[ ks.setting ? _('Turn off') : _('Turn on') ]);
		ksBtn.addEventListener('click', function() { self.killswitch(ksBtn, !ks.setting); });
		var ksCard = V.card(_('Kill switch'), [
			V.row(_('Setting'), ks.setting ? _('On') : _('Off'), 'mud-vpn-ks-setting'),
			V.row(_('Now'), ks.active ? _('Active: only the tunnel can reach the internet') : _('Not active'), 'mud-vpn-ks-active'),
			E('div', { 'class': 'mvp-row' }, [ ksBtn ]),
			V.note(_('The kill switch blocks all traffic of the device and of the LAN while the VPN is down, so nothing ever leaves without the tunnel. If the tunnel cannot come up (a dead server, an expired account), the device stays offline until you fix the profile or stop the VPN. The panel itself stays reachable from the LAN.'))
		], 'mvp-killswitch');

		/* the active profile */
		var sel = E('select', { 'class': 'cbi-input-select mvp-grow', 'id': 'mvp-switch-sel' },
			[ E('option', { 'value': '' }, [ profiles.length ? _('Choose a profile') : _('No profiles yet') ]) ].concat(profiles.map(function(p) {
				return E('option', { 'value': p.id }, [ String(p.name || p.id) + ' · ' + V.typeName(p.type) +
					(p.engine_installed === false ? ' · ' + _('engine not installed') : '') ]);
			})));
		sel.value = prof ? prof.id : '';
		var useBtn = E('button', { 'class': 'mud-btn', 'id': 'mvp-switch-use' }, [ _('Use') ]);
		useBtn.addEventListener('click', function() {
			if (!sel.value || (prof && sel.value === prof.id)) return;
			V.run(useBtn, function() { return V.call.use(sel.value); },
				s.enabled ? _('Switched: the VPN restarts on the new profile') : _('Saved')).then(function() { self.reload(); });
		});
		var swCard = V.card(_('Active profile'), [
			E('div', { 'class': 'mvp-row' }, [ sel, useBtn ]),
			V.note(_('A running VPN restarts on the profile you choose; the kill switch stays up in between.')),
			E('div', { 'class': 'mud-note' }, [ E('a', { 'href': L.url('admin/vpn/mu300-profiles') }, [ _('Manage the profiles') ]) ])
		], 'mvp-switch');

		/* IPv6 (spec section 7) */
		var v6sel = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-ipv6-sel' }, [
			E('option', { 'value': 'auto' }, [ _('Automatic: through the tunnel where the engine can carry it') ]),
			E('option', { 'value': '1' }, [ _('Ask the engine for IPv6 in the tunnel') ]),
			E('option', { 'value': '0' }, [ _('Never through the tunnel (blocked while the VPN is up)') ])
		]);
		v6sel.value = [ 'auto', '0', '1' ].indexOf(String(v6.setting)) >= 0 ? String(v6.setting) : 'auto';
		v6sel.addEventListener('change', function() {
			V.run(null, function() { return V.call.settingSet('IPV6', v6sel.value); },
				s.running ? _('Saved: it applies from the next start (Restart)') : _('Saved')).then(function() { self.reload(); });
		});
		var v6Card = V.card(_('IPv6'), [
			E('div', { 'class': 'mvp-row' }, [ v6sel ]),
			V.row(_('The tunnel carries IPv6'), v6.tunnel ? _('Yes') : _('No'), 'mud-vpn-v6-tunnel'),
			V.row(_('IPv6 guard'), v6.guard ? _('Active: IPv6 outside the tunnel is refused') : _('Not active'), 'mud-vpn-v6-guard'),
			V.note(_('While the VPN is up, IPv6 never leaves beside a tunnel that does not carry it: it is refused at once, so programs and LAN clients fall back to IPv4 instead of leaking or waiting. Automatic: WireGuard and OpenVPN use the IPv6 addresses of their config; Xray, sing-box and mihomo are asked at every start whether IPv6 works through the tunnel, and carry it only then; dae stays IPv4.'))
		], 'mvp-ipv6');

		var grid = E('div', { 'class': 'mud-grid' }, [ ctl, ksCard, swCard, v6Card ]);
		Array.prototype.forEach.call(grid.children, function(c) { c.style.marginTop = '0'; });
		V.paintFrame(this.root, 'overview', st, alerts.concat([ hero, kpis, grid ]));
	}
});
