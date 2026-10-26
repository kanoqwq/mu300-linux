'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Packages after an update: what mu300-update wrote down of the packages installed on the device, and what
 * the first boot's mu300-user-packages made of them (mu300dash packages_get/packages_act, unisoc-modem/packages).
 * Retry and Repair DNS run in the background; the page looks again a few seconds later. */
return view.extend({
	load: function() { return L.resolveDefault(M.callPackagesGet(), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callPackagesGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	act: function(btn, op, label) {
		var self = this;
		M.busy(btn, true);
		return M.callPackagesAct(String(op)).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(r.started ? _('Started in background: %s').format(label || '') : _('Saved'), { type: 'success' });
			if (r.started)
				setTimeout(function() { self.reload(); }, 8000);
			return self.reload();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	/* Repair DNS can send the device's DNS to the carrier: only after the user said so */
	repairDns: function(btn) {
		var self = this;
		return M.confirmBox(_('Repair DNS?'),
			_('This removes the DNS forwarder of a package that is no longer installed. If no other DNS server is left, the device then uses the DNS servers of the mobile connection: the carrier sees the names you look up, outside any proxy or VPN.'),
			{ danger: true, okText: _('Repair DNS') }).then(function(yes) {
			if (yes) return self.act(btn, 'repair_dns', _('Repair DNS'));
		});
	},

	paint: function(st) {
		var self = this;
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-pkg' }, [ E('h3', {}, _('Packages after an update')) ]);
		card.appendChild(E('div', { 'class': 'mud-note' },
			_('Packages installed on the device are not part of a release image. An update writes them down, and the new system installs them again at its first boot, once it is online. Until a package is back its settings are set aside.')));
		if (st.ok !== 1) {
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-pkg-unavailable' },
				[ _('Unavailable') + (st.error ? ': ' + M.errText(st) : '') ]));
			dom.content(this.root, [ card ]);
			return;
		}
		if (!st.recorded) {
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-pkg-none' }, [ _('No packages to install again.') ]));
			dom.content(this.root, [ card ]);
			return;
		}
		var states = {
			done: _('All back'), pending: _('Not installed yet'), running: _('Running'),
			failed: _('Could not be installed again'), offline: _('Waiting for the internet'),
			'gave-up': _('Given up: no internet at five boots'), skipped: _('Skipped')
		};
		card.appendChild(E('div', { 'id': 'mud-pkg-state' }, [ E('strong', {}, [ states[st.state] || String(st.state || '') ]) ]));
		if (st.from)
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-pkg-from' },
				[ _('Written down by the update from %s').format(String(st.from)) ]));

		var list = function(id, title, items) {
			if (!Array.isArray(items) || !items.length) return;
			card.appendChild(E('div', { 'id': 'mud-pkg-' + id, 'style': 'margin-top:8px' }, [
				E('div', {}, [ E('strong', {}, [ title ]) ]),
				E('div', { 'class': 'mud-note' }, [ items.map(String).join(' ') ])
			]));
		};
		list('done', _('Installed again'), st.done);
		list('pending', _('Not installed yet'), st.pending);
		if (Array.isArray(st.failed) && st.failed.length) {
			var rows = st.failed.map(function(f) {
				return E('tr', {}, [ E('td', {}, [ String(f.name || '') ]), E('td', {}, [ String(f.why || '') ]) ]);
			});
			card.appendChild(E('div', { 'id': 'mud-pkg-failed', 'style': 'margin-top:8px' }, [
				E('div', {}, [ E('strong', {}, [ _('Could not be installed again') ]) ]),
				E('div', { 'class': 'mud-note' }, [ _('Install them yourself from where you got them (a package file: apk add --allow-untrusted FILE.apk).') ]),
				E('table', { 'class': 'mud-table' }, rows)
			]));
		}
		list('orphaned', _('Settings set aside in /etc/mu300/orphaned-config'), (st.orphaned || []).map(function(p) { return '/' + p; }));
		if (st.dns) {
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-pkg-dns', 'style': 'margin-top:8px' }, [
				E('strong', {}, [ _('DNS still goes to a package that is not installed: the device has no DNS through it.') ]),
				E('div', {}, [ String(st.dns) ])
			]));
		}

		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0' });
		var retry = E('button', { 'class': 'mud-btn', 'id': 'mud-pkg-retry' }, [ _('Try again') ]);
		retry.addEventListener('click', function() { self.act(retry, 'retry', _('Try again')); });
		var dismiss = E('button', { 'class': 'mud-btn', 'id': 'mud-pkg-dismiss' }, [ _('Dismiss the note') ]);
		dismiss.addEventListener('click', function() { self.act(dismiss, 'dismiss'); });
		var dns = E('button', { 'class': 'mud-btn warn', 'id': 'mud-pkg-dns-btn' }, [ _('Repair DNS') ]);
		dns.addEventListener('click', function() { self.repairDns(dns); });
		if (st.running) retry.disabled = dns.disabled = true;
		if (st.state !== 'done') actions.appendChild(retry);
		if (st.dns) actions.appendChild(dns);
		actions.appendChild(dismiss);
		card.appendChild(actions);
		dom.content(this.root, [ card ]);
	}
});
