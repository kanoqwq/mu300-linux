'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Terminal: a root shell in the browser, ttyd (/etc/config/ttyd). The page asks rpcd's mu300term for a
 * one-time token (valid for a minute and one connection) and loads ttyd with it; ttyd's command,
 * mu300-terminal-login, opens the shell for that token only, so the terminal is exactly as protected as this LuCI
 * login. ttyd listens on the LAN address only, over HTTP (port 7681); the cellular side never reaches it. An https
 * page cannot frame it (mixed content), so there the terminal opens in a tab of its own. */
return view.extend({
	load: function() { return L.resolveDefault(M.callTermToken(), {}); },

	render: function(r) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(r || {});
		return this.root;
	},

	/* the terminal URL of a token: the host the browser used for the panel (an IPv6 literal is replaced by the LAN's
	 * IPv4 address: ttyd listens on IPv4) */
	target: function(r) {
		if (!r || r.ok !== 1) return null;
		var host = String(window.location.hostname || '');
		if (host.indexOf(':') >= 0 || host.charAt(0) === '[') host = String(r.lan || '');
		if (!/^[A-Za-z0-9.-]+$/.test(host)) return null;
		if (!/^[0-9a-f]{32}$/.test(String(r.token || ''))) return null;
		var port = Number(r.port);
		if (!(port > 0 && port < 65536)) return null;
		return 'http://' + host + ':' + port + '/?arg=' + r.token;
	},

	reason: function(r) {
		var codes = {
			missing: _('ttyd is not installed.'),
			off: _('The terminal is turned off (/etc/config/ttyd).'),
			busy: _('Too many terminals are opening at once. Try again in a minute.')
		};
		return codes[r && r.code] || _('The terminal is not available.');
	},

	/* a fresh token into the frame (a token serves one connection: ttyd's own reconnect is refused) */
	reconnect: function(btn) {
		var self = this;
		M.busy(btn, true);
		return L.resolveDefault(M.callTermToken(), {}).then(function(r) {
			M.busy(btn, false);
			self.paint(r || {});
		});
	},

	/* the terminal in a tab of its own, with a token of its own */
	popout: function(btn) {
		var self = this, w = window.open('', '_blank');
		M.busy(btn, true);
		return L.resolveDefault(M.callTermToken(), {}).then(function(r) {
			M.busy(btn, false);
			var url = self.target(r);
			if (!url) {
				if (w) w.close();
				M.toast(self.reason(r), { type: 'error', timeout: 6000 });
				return;
			}
			if (w) { w.opener = null; w.location.href = url; }
		});
	},

	paint: function(r) {
		var self = this;
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-term' }, [ E('h3', {}, _('Terminal')) ]);
		card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-term-root' }, [
			_('This is a root shell on the device: every command runs with full rights, and a mistake can make the device unbootable.')
		]));
		card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-term-scope' }, [
			_('It is reachable from the LAN only (USB and the hotspot), never from mobile data, and only from this logged-in page.')
		]));
		/* a minute or less has a message of its own: the panel's catalogs have no plural forms (po2lmo, luci-i18n) */
		var mins = Math.round(Number(r && r.idle) / 60);
		if (r && Number(r.idle) > 0)
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-term-idle' }, [
				mins <= 1 ? _('It closes after a minute without activity.')
					: _('It closes after %d minutes without activity.').format(mins)
			]));
		var url = this.target(r);
		if (!url) {
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-term-unavailable' },
				[ r && r.ok === 1 ? _('The terminal is not available.') : this.reason(r) ]));
			dom.content(this.root, [ card ]);
			return;
		}
		var framed = window.location.protocol !== 'https:';
		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' });
		if (framed) {
			var again = E('button', { 'class': 'mud-btn', 'id': 'mud-term-reconnect' }, [ _('Reconnect') ]);
			again.addEventListener('click', function() { self.reconnect(again); });
			actions.appendChild(again);
		}
		var pop = E('button', { 'class': framed ? 'mud-btn' : 'mud-btn cbi-button-positive', 'id': 'mud-term-popout' },
			[ _('Open in a new tab') ]);
		pop.addEventListener('click', function() { self.popout(pop); });
		actions.appendChild(pop);
		card.appendChild(actions);
		if (!framed) {
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-term-tls' }, [
				_('This page was opened over HTTPS, and the terminal is served over HTTP on the LAN: it opens in a tab of its own.')
			]));
		} else {
			card.appendChild(E('iframe', {
				'id': 'mud-term-frame',
				'src': url,
				'referrerpolicy': 'no-referrer',
				'style': 'width:100%;min-height:520px;border:none;border-radius:6px;resize:vertical;background:#000'
			}));
		}
		dom.content(this.root, [ card ]);
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
