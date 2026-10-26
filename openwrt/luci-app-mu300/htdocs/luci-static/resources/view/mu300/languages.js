'use strict';
'require view';
'require ui';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Languages: the language of the web interface, and the lang extra (more languages for LuCI and this panel)
 * - installed from the release or from an uploaded mu300-extra-lang.tar.gz, each language enabled or disabled. The
 * backend is mu300dash lang_get/lang_set (unisoc-modem/languages, mu300-extra). */
var UPLOAD = '/tmp/mu300-extra-lang.tar.gz';

return view.extend({
	load: function() { return L.resolveDefault(M.callLangGet(), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callLangGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* run a lang_set and show its result; a change of the interface language reloads the page */
	set: function(btn, op, codes, source, done) {
		var self = this;
		M.busy(btn, true);
		return M.callLangSet(op, codes || '', source || '').then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			if (r.started) return self.watch();
			if (done) return done();
			M.toast(_('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	/* an installation runs detached: ask until it is over */
	watch: function() {
		var self = this, msg = M.toast(_('Installing the language pack…'), { type: 'busy', timeout: 0 });
		var tick = function() {
			return L.resolveDefault(M.callLangGet(), {}).then(function(st) {
				var job = (st && st.job) || {};
				if (job.state === 'running') { setTimeout(tick, 2000); return; }
				if (job.state === 'done') msg.update(_('The language pack is installed'), 'success');
				else msg.update(_('The language pack could not be installed') + (job.log ? ': ' + job.log : ''), 'error');
				setTimeout(function() { msg.close(); }, 6000);
				self.paint(st || {});
			});
		};
		setTimeout(tick, 1500);
	},

	upload: function(btn) {
		var self = this;
		return ui.uploadFile(UPLOAD).then(function() {
			return self.set(btn, 'install', '', 'file');
		}, function(e) {
			if (e && e.message && !/cancel/i.test(e.message)) M.toast(_('Upload failed: %s').format(e.message), { type: 'error' });
		});
	},

	paint: function(st) {
		var self = this, ex = st.extra || {}, langs = ex.languages || [], luci = st.luci || [];
		var job = st.job || {}, running = job.state === 'running';

		/* the interface language: LuCI's own setting (System > System > Language and Style) */
		var pick = E('select', { 'class': 'cbi-input-select', 'id': 'mud-lang-use' }, [
			E('option', { 'value': 'en' }, 'English'),
			E('option', { 'value': 'auto' }, _('Language of the browser'))
		].concat(luci.slice().sort(function(a, b) { return a.name.localeCompare(b.name); }).map(function(l) {
			return E('option', { 'value': l.code }, [ l.name ]);
		})));
		/* a language LuCI no longer offers shows as English, not as an empty choice */
		pick.value = [ 'en', 'auto' ].concat(luci.map(function(l) { return l.code; })).indexOf(st.current) >= 0 ? st.current : 'en';
		var useBtn = E('button', { 'class': 'mud-btn', 'id': 'mud-lang-apply' }, _('Apply'));
		useBtn.addEventListener('click', function() {
			self.set(useBtn, 'use', pick.value, '', function() { window.location.reload(); });
		});
		var first = E('section', { 'class': 'mud-card' }, [
			E('h3', {}, _('Language of the web interface')),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [ pick, useBtn ]),
			E('div', { 'class': 'mud-note' }, [
				_('English, Turkish and Chinese come with the system; the language pack adds the others.') + ' ',
				E('a', { 'href': L.url('admin/system/system') }, _('The same setting is in System > System > Language and Style.'))
			])
		]);

		/* the language pack */
		var pack = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-lang-pack' }, [
			E('h3', {}, _('Language pack'))
		]);
		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0' });
		if (!ex.installed) {
			pack.appendChild(E('div', { 'class': 'mud-note' },
				_('The language pack (mu300-extra-lang.tar.gz, about 2 MB) adds LuCI in about 40 more languages and this panel in 29 of them. Download it from the release of this system, or upload the file from the release page when the device has no internet.')));
			var dl = E('button', { 'class': 'mud-btn', 'id': 'mud-lang-download', 'disabled': running ? '' : null }, _('Download from the release'));
			dl.addEventListener('click', function() { self.set(dl, 'install', '', 'release'); });
			var up = E('button', { 'class': 'mud-btn', 'id': 'mud-lang-upload', 'disabled': running ? '' : null }, _('Upload the file…'));
			up.addEventListener('click', function() { self.upload(up); });
			actions.appendChild(dl);
			actions.appendChild(up);
		} else {
			pack.appendChild(E('div', { 'class': 'mud-r' }, [
				E('span', { 'class': 'mud-k' }, _('Release')), E('span', { 'class': 'mud-v' }, [ ex.release || '--' ])
			]));
			[ [ 'enable', _('Enable all') ], [ 'disable', _('Disable all') ] ].forEach(function(a) {
				var b = E('button', { 'class': 'mud-btn', 'id': 'mud-lang-' + a[0] + '-all' }, [ a[1] ]);
				b.addEventListener('click', function() { self.set(b, a[0], 'all'); });
				actions.appendChild(b);
			});
			var rm = E('button', { 'class': 'mud-btn warn', 'id': 'mud-lang-remove', 'disabled': running ? '' : null }, _('Remove the language pack'));
			rm.addEventListener('click', function() {
				M.confirmBox(_('Remove the language pack?'), _('Its languages disappear from the list; a page in one of them switches to English.'), { danger: true, okText: _('Remove') })
					.then(function(yes) { if (yes) self.set(rm, 'remove'); });
			});
			actions.appendChild(rm);
		}
		pack.appendChild(actions);
		if (running) pack.appendChild(E('div', { 'class': 'mud-note' }, _('Installing the language pack…')));
		else if (job.state === 'failed') pack.appendChild(E('div', { 'class': 'mud-note' },
			[ _('The language pack could not be installed') + (job.log ? ': ' + job.log : '') ]));

		if (ex.installed) {
			var rows = langs.slice().sort(function(a, b) { return a.name.localeCompare(b.name); }).map(function(l) {
				var b = E('button', { 'class': 'mud-btn', 'data-code': l.code }, [ l.enabled ? _('Disable') : _('Enable') ]);
				b.addEventListener('click', function() { self.set(b, l.enabled ? 'disable' : 'enable', l.code); });
				return E('tr', {}, [
					E('td', {}, [ l.name ]),
					E('td', {}, [ l.code ]),
					E('td', {}, [ l.panel ? _('LuCI and this panel') : _('LuCI only') ]),
					E('td', {}, [ l.enabled ? _('Enabled') : _('Disabled') ]),
					E('td', {}, [ b ])
				]);
			});
			pack.appendChild(E('table', { 'class': 'mud-table', 'id': 'mud-lang-table' }, [
				E('tr', {}, [ E('th', {}, _('Language')), E('th', {}, _('Code')), E('th', {}, _('Translated')),
					E('th', {}, _('State')), E('th', {}, '') ])
			].concat(rows)));
		}
		pack.appendChild(E('div', { 'class': 'mud-note' },
			_('LuCI\'s own translations come from OpenWrt. This panel\'s translations other than Turkish and Chinese are machine (AI) translations; corrections are welcome.')));

		dom.content(this.root, [ first, pack ]);
	}
});
