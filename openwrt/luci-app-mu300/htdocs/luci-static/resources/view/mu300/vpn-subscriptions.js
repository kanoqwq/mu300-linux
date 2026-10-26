'use strict';
'require view';
'require dom';
'require mu300.common as M';
'require mu300.vpn as V';

/* VPN > Subscriptions: mu300-vpn's subscriptions (subscription list --json shows the URL's host only; the URL itself
 * holds a token and is never shown). Adding one and refreshing are downloads: they run as the background task. */
return view.extend({
	load: function() {
		return Promise.all([ V.status(false), L.resolveDefault(V.call.subscriptions(), {}), L.resolveDefault(V.call.profiles(), {}) ]);
	},

	handleSaveApply: null, handleSave: null, handleReset: null,

	render: function(data) {
		this.root = V.frame('subscriptions');
		this.paint(data[0], data[1], data[2]);
		var self = this;
		V.resume(null, function() { self.reload(); }).then(function(job) {
			/* The CLI installs no timer: subscriptions with an interval are refreshed when it has passed and this page
			 * is opened (subscription refresh --due), unless a task runs already */
			var subs = (data[1] && data[1].subscriptions) || [], now = Date.now() / 1000;
			var due = subs.some(function(s) { return s.interval > 0 && (s.updated || 0) + s.interval * 3600 <= now; });
			if (due && job.state !== 'running')
				V.start(null, function() { return V.call.subRefresh('due'); }, null, function() { self.reload(); });
		});
		return this.root;
	},

	reload: function() {
		var self = this;
		return Promise.all([ V.status(false), L.resolveDefault(V.call.subscriptions(), {}), L.resolveDefault(V.call.profiles(), {}) ])
			.then(function(d) { self.paint(d[0], d[1], d[2]); });
	},

	remove: function(s) {
		var self = this;
		return M.choiceBox(_('Delete the subscription %s?').format(String(s.name || s.id)),
			_('Its profiles can go with it or stay as profiles of their own. The active profile is never removed while the VPN is on.'), [
				{ label: _('Cancel'), value: null },
				{ label: _('Keep the profiles'), value: '1' },
				{ label: _('Delete with its profiles'), value: '0', danger: true }
			]).then(function(keep) {
			if (keep == null) return;
			return V.run(null, function() { return V.call.subRemove(s.id, keep); }, _('Deleted')).then(function() { return self.reload(); });
		});
	},

	paint: function(st, sl, pl) {
		var self = this;
		if (!st || st.installed !== 1 || st.legacy) {
			V.paintFrame(this.root, 'subscriptions', st, [], function() { self.reload(); });
			return;
		}
		var subs = (sl && sl.subscriptions) || [], profiles = (pl && pl.profiles) || [];

		/* add */
		var name = E('input', { 'class': 'mvp-in mvp-grow', 'id': 'mvp-sub-name', 'maxlength': '64' });
		var url = E('input', { 'class': 'mvp-in mvp-grow', 'id': 'mvp-sub-url', 'type': 'password', 'autocomplete': 'off',
			'spellcheck': 'false', 'placeholder': 'https://…' });
		var showUrl = E('button', { 'class': 'mud-btn', 'type': 'button', 'id': 'mvp-sub-url-show', 'title': _('Show') }, [ _('Show') ]);
		showUrl.addEventListener('click', function() { url.type = url.type === 'password' ? 'text' : 'password'; });
		var every = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-sub-interval' }, [
			E('option', { 'value': '' }, [ _('Only by hand') ]) ].concat([ 6, 12, 24, 48, 168 ].map(function(h) {
				return E('option', { 'value': String(h) }, [ _('Every %d h').format(h) ]);
			})));
		every.value = '24';
		var type = E('select', { 'class': 'cbi-input-select', 'id': 'mvp-sub-type' }, [
			E('option', { 'value': 'auto' }, [ _('Detect') ]),
			E('option', { 'value': 'links' }, [ _('Share links: one profile per link') ]),
			E('option', { 'value': 'mihomo' }, [ _('Clash/mihomo YAML: one mihomo profile') ]),
			E('option', { 'value': 'dae' }, [ _('dae: one profile with every node in one group') ])
		]);
		var addBtn = E('button', { 'class': 'mud-btn on', 'id': 'mvp-sub-add' }, [ _('Add') ]);
		addBtn.addEventListener('click', function() {
			var n = name.value.trim(), u = url.value.trim();
			if (!n || /[\u0000-\u001f\u007f]/.test(n) || /^-/.test(n)) {
				M.toast(_('Invalid name: 1-64 characters, no control characters'), { type: 'error', timeout: 6000 });
				return;
			}
			if (!/^https:\/\/[A-Za-z0-9.-]+(:[0-9]+)?(\/\S*)?$/.test(u)) {
				M.toast(_('Not an https URL'), { type: 'error', timeout: 6000 });
				return;
			}
			V.start(addBtn, function() { return V.call.subAdd(n, u, type.value, every.value); }, null, function() { self.reload(); })
				.then(function(r) { if (r) { url.value = ''; name.value = ''; } });
		});
		var addCard = V.card(_('Add a subscription'), [
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-sub-name' }, [ _('Name') ]), name ]),
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-sub-url' }, [ _('URL') ]), url, showUrl ]),
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-sub-type' }, [ _('Format') ]), type ]),
			E('div', { 'class': 'mvp-row' }, [ E('label', { 'for': 'mvp-sub-interval' }, [ _('Refresh') ]), every ]),
			E('div', { 'class': 'mvp-row' }, [ addBtn ]),
			V.note(_('The URL usually holds your account token: it is stored on the device readable by root only, and never shown again. The subscription is fetched over the device\'s own connection: through the tunnel when the VPN is up; with the kill switch up and the tunnel down it cannot be fetched.'))
		], 'mvp-sub-addcard');

		/* the list */
		var refreshAll = E('button', { 'class': 'mud-btn', 'id': 'mvp-sub-refresh-all', 'disabled': subs.length ? null : '' }, [ _('Refresh all') ]);
		refreshAll.addEventListener('click', function() {
			V.start(refreshAll, function() { return V.call.subRefresh('all'); }, null, function() { self.reload(); });
		});
		var body;
		if (sl && sl.ok === 0) body = V.note(M.errText(sl), 'mvp-subs-error');
		else if (!subs.length) body = V.note(_('No subscriptions yet.'), 'mvp-subs-empty');
		else body = E('table', { 'class': 'mvp-tbl', 'id': 'mvp-subs-table' }, [
			E('thead', {}, [ E('tr', {}, [ E('th', {}, [ _('Name') ]), E('th', {}, [ _('Format') ]), E('th', {}, [ _('Host') ]),
				E('th', {}, [ _('Updated') ]), E('th', {}, [ _('Refresh') ]), E('th', {}, [ _('Profiles') ]), E('th', {}, '') ]) ]),
			E('tbody', {}, subs.map(function(s) {
				var ref = E('button', { 'class': 'mud-btn', 'data-refresh': s.id }, [ _('Refresh') ]);
				ref.addEventListener('click', function() {
					V.start(ref, function() { return V.call.subRefresh(s.id); }, null, function() { self.reload(); });
				});
				var del = E('button', { 'class': 'mud-btn warn', 'data-remove': s.id }, [ _('Delete') ]);
				del.addEventListener('click', function() { self.remove(s); });
				var pn = V.names(s.profiles, profiles);
				var problem = s.error || (s.status && s.status !== 'ok' ? s.status : null);
				return E('tr', { 'data-id': s.id }, [
					E('td', { 'class': 'mvp-first' }, [ E('div', {}, [ String(s.name || s.id) ]),
						E('div', { 'class': 'mvp-sub' }, [ String(s.id) ]),
						problem ? E('div', {}, [ V.pill(String(problem), 'bad') ]) : '' ]),
					E('td', { 'data-k': _('Format') }, [ (function(f) {
						/* format: what the last fetch found; type: what was asked for (auto) */
						return f === 'links' ? _('Share links') : f === 'mihomo' ? 'mihomo' : f === 'dae' ? 'dae' :
							f === 'auto' ? _('Detect') : String(f || '--');
					})(s.format || s.type) ]),
					E('td', { 'data-k': _('Host'), 'class': 'mvp-mono' }, [ String(s.host || '--') ]),
					E('td', { 'data-k': _('Updated') }, [ V.ago(s.updated) ]),
					E('td', { 'data-k': _('Refresh') }, [ s.interval > 0 ? _('Every %d h').format(s.interval) : _('Only by hand') ]),
					E('td', { 'data-k': _('Profiles'), 'title': pn.join(', ') }, [ String(s.count != null ? s.count : pn.length) ]),
					E('td', {}, [ E('div', { 'class': 'mvp-acts' }, [ ref, del ]) ])
				]);
			}))
		]);
		var listCard = V.card(_('Subscriptions'), [ E('div', { 'class': 'mvp-row', 'style': 'margin-top:0' }, [ refreshAll ]), body,
			V.note(_('A refresh keeps the profiles whose name is unchanged, adds the new ones and removes the ones that are gone (the active one stays while the VPN is on, marked as gone). A refresh of the active profile restarts the running VPN.'))
		], 'mvp-subs');
		addCard.style.marginTop = '0';
		V.paintFrame(this.root, 'subscriptions', st, [ addCard, listCard ]);
	}
});
