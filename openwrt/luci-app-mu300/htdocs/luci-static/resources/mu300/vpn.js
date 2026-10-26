'use strict';
'require rpc';
'require ui';
'require dom';
'require baseclass';
'require mu300.common as M';
/* The VPN pages' shared module (VPN > Overview, Profiles, Subscriptions, Engines, Settings, Logs): the rpc
 * declarations of the mu300vpn object, the tab strip between the pages, the card shown when the VPN module is not
 * installed, the background-task watcher and a few formatting helpers. Pages load it with 'require mu300.vpn as V'.
 *
 * Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */

function decl(method, params) {
	return rpc.declare({ object: 'mu300vpn', method: method, params: params || [], expect: { '': {} } });
}
var call = {
	status: decl('status', [ 'exit_ip' ]),
	profiles: decl('profiles'),
	engines: decl('engines', [ 'check' ]),
	subscriptions: decl('subscriptions'),
	settings: decl('settings'),
	job: decl('job'),
	service: decl('service', [ 'op' ]),
	killswitch: decl('killswitch', [ 'value' ]),
	use: decl('use', [ 'id' ]),
	profileAdd: decl('profile_add', [ 'source', 'data', 'name', 'type' ]),
	profileRename: decl('profile_rename', [ 'id', 'name' ]),
	profileRemove: decl('profile_remove', [ 'id' ]),
	profileCheck: decl('profile_check', [ 'id' ]),
	profileOption: decl('profile_option', [ 'id', 'key', 'value' ]),
	profileReveal: decl('profile_reveal', [ 'id' ]),
	profileEdit: decl('profile_edit', [ 'id', 'config' ]),
	latency: decl('latency', [ 'id' ]),
	subAdd: decl('sub_add', [ 'name', 'url', 'type', 'interval' ]),
	subRefresh: decl('sub_refresh', [ 'id' ]),
	subRemove: decl('sub_remove', [ 'id', 'keep' ]),
	engineInstall: decl('engine_install', [ 'name', 'source', 'unverified' ]),
	engineRemove: decl('engine_remove', [ 'name' ]),
	coreInstall: decl('core_install', [ 'source', 'unverified' ]),
	settingSet: decl('setting_set', [ 'key', 'value' ]),
	logs: decl('logs', [ 'engine', 'lines' ])
};

/* the two paths the ACL lets the panel upload to (the backend moves the file out of /tmp before it reads it) */
var UPLOAD_CFG = '/tmp/mu300-vpn-upload';
var UPLOAD_PKG = '/tmp/mu300-vpn-extra.tar.gz';

var ENGINES = [ 'xray', 'sing-box', 'mihomo', 'dae', 'wireguard', 'openvpn' ];
var ENGINE_NAMES = { 'xray': 'Xray', 'sing-box': 'sing-box', 'mihomo': 'mihomo', 'dae': 'dae', 'wireguard': 'WireGuard',
	'openvpn': 'OpenVPN', 'hev-socks5-tunnel': 'hev-socks5-tunnel' };
function engineName(n) { return ENGINE_NAMES[n] || String(n || '--'); }

var CSS = `
.mvp-tabs{display:flex;flex-wrap:wrap;gap:4px;margin:0 0 12px;padding:0;list-style:none;border-bottom:1px solid var(--hairline,var(--border,#ddd))}
.mvp-tabs a{display:block;padding:6px 12px;border-radius:var(--radius-base,.5rem) var(--radius-base,.5rem) 0 0;color:var(--text-muted,var(--text-light,#777));text-decoration:none;font-size:.84rem;border:1px solid transparent;border-bottom:none;margin-bottom:-1px}
.mvp-tabs a:hover{color:var(--text,#222)}
.mvp-tabs a.on{color:var(--text,#222);font-weight:600;background:var(--surface,var(--background,#fff));border-color:var(--hairline,var(--border,#ddd))}
.mvp-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.mvp-row>label{color:var(--text-muted,var(--text-light,#777));min-width:9em}
.mvp-in{padding:6px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit;min-width:0}
.mvp-grow{flex:1 1 220px}
.mvp-ta{width:100%;min-height:300px;font-family:var(--font-mono,monospace);font-size:.78rem;line-height:1.45;padding:9px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface-sunken,var(--background,#fff));color:var(--text,#222);resize:vertical;white-space:pre;overflow:auto;tab-size:4}
.mvp-state{font-size:1.6rem;font-weight:700;line-height:1.15}
.mvp-pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:.72rem;font-weight:600;background:var(--surface-sunken,rgba(127,127,127,.1));color:var(--text-muted,var(--text-light,#777));white-space:nowrap}
.mvp-pill.ok{background:color-mix(in oklab,var(--success,#2FBF71) 16%,transparent);color:var(--success,#2FBF71)}
.mvp-pill.warn{background:color-mix(in oklab,var(--warning,#F2B544) 18%,transparent);color:color-mix(in oklab,var(--warning,#F2B544) 70%,var(--text,#222))}
.mvp-pill.bad{background:color-mix(in oklab,var(--danger,#E25555) 14%,transparent);color:var(--danger,#E25555)}
.mvp-pill.brand{background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mvp-alert{border-left:3px solid var(--warning,#F2B544);padding:8px 12px;margin:0 0 12px;background:color-mix(in oklab,var(--warning,#F2B544) 9%,var(--surface,#fff));border-radius:var(--radius-base,.5rem)}
.mvp-alert.bad{border-left-color:var(--danger,#E25555);background:color-mix(in oklab,var(--danger,#E25555) 8%,var(--surface,#fff))}
.mvp-log{font-family:var(--font-mono,monospace);font-size:.74rem;white-space:pre-wrap;word-break:break-all;background:var(--surface-sunken,rgba(127,127,127,.07));border-radius:var(--radius-base,.5rem);padding:9px;max-height:520px;overflow:auto;margin:8px 0 0}
.mvp-tbl{width:100%;border-collapse:collapse;font-size:.82rem}
.mvp-tbl th{font-weight:500;color:var(--text-muted,var(--text-light,#777));text-align:left;padding:4px 6px;border-bottom:1px solid var(--hairline,var(--border,#ddd));font-size:.72rem;white-space:nowrap}
.mvp-tbl td{padding:7px 6px;border-bottom:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 55%,transparent);vertical-align:middle}
.mvp-tbl tr.on td{background:var(--brand-subtle,color-mix(in oklab,var(--brand,#2f7bf6) 6%,transparent))}
.mvp-tbl .mvp-acts{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.mvp-tbl .mvp-acts .mud-btn{padding:4px 9px;font-size:.76rem}
.mvp-sub{font-size:.72rem;color:var(--text-subtle,var(--text-light,#999));margin-top:1px;word-break:break-all}
.mvp-mono{font-family:var(--font-mono,monospace);font-size:.78rem;word-break:break-all}
.mvp-eng .mud-v,.mvp-wrap{word-break:normal;overflow-wrap:anywhere}
.mud-sec .mvp-row>label{min-width:14em}
.mvp-eng{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:10px}
.mvp-big .mud-dlg{max-width:820px}
.mvp-dlg-body{margin-top:6px}
.mvp-seg{display:inline-flex;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);overflow:hidden;flex-wrap:wrap}
.mvp-seg button{border:none;background:transparent;color:var(--text,#222);padding:6px 12px;font:inherit;font-size:.8rem;cursor:pointer}
.mvp-seg button.on{background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
@media(max-width:700px){
.mvp-tbl thead{display:none}
.mvp-tbl,.mvp-tbl tbody,.mvp-tbl tr,.mvp-tbl td{display:block;width:100%}
.mvp-tbl tr{border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 70%,transparent);border-radius:var(--radius-base,.5rem);margin-bottom:8px;padding:4px 8px}
.mvp-tbl td{border:none;padding:3px 0;display:flex;justify-content:space-between;gap:10px;text-align:right}
.mvp-tbl td::before{content:attr(data-k);color:var(--text-muted,var(--text-light,#777));text-align:left;flex:0 0 auto}
.mvp-tbl td.mvp-first{display:block;text-align:left;font-weight:600}
.mvp-tbl td.mvp-first::before{content:none}
.mvp-tbl .mvp-acts{justify-content:flex-start}
.mvp-row>label,.mud-sec .mvp-row>label{min-width:0;flex:1 1 100%}
.mvp-state{font-size:1.35rem}}
`;
function injectCss() {
	M.injectCss();
	if (document.getElementById('mvp-style')) return;
	var st = document.createElement('style');
	st.id = 'mvp-style';
	st.textContent = CSS;
	document.head.appendChild(st);
}

/* the strip of the six VPN pages, the current one marked (LuCI's own menu has them too: VPN in the top bar) */
function tabs(active) {
	var pages = [
		[ 'overview', _('Overview') ], [ 'profiles', _('Profiles') ], [ 'subscriptions', _('Subscriptions') ],
		[ 'engines', _('Engines') ], [ 'settings', _('Settings') ], [ 'logs', _('Logs') ]
	];
	return E('nav', { 'class': 'mvp-tabs', 'id': 'mvp-tabs' }, pages.map(function(p) {
		return E('a', { 'href': L.url('admin/vpn/mu300-' + p[0]), 'class': p[0] === active ? 'on' : null,
			'data-page': p[0] }, [ p[1] ]);
	}));
}

/* one key-value row of a card */
function row(k, v, id) {
	return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ k ]),
		E('span', { 'class': 'mud-v', 'id': id || null }, Array.isArray(v) ? v : [ v == null || v === '' ? '--' : String(v) ]) ]);
}
function pill(text, kind) { return E('span', { 'class': 'mvp-pill' + (kind ? ' ' + kind : '') }, [ text ]); }
function card(title, children, id) {
	return E('section', { 'class': 'mud-card', 'id': id || null, 'style': 'margin-top:12px' },
		[ E('h3', {}, [ title ]) ].concat(children || []));
}
function note(text, id) { return E('div', { 'class': 'mud-note', 'id': id || null }, [ text ]); }

/* a time stamp (seconds) as "5 min ago"; null: "never" */
function ago(ts) {
	if (!ts) return _('never');
	var s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
	if (s < 60) return _('just now');
	return _('%s ago').format(M.fmtUptime(s));
}

/* run(btn, promise-returning fn, success message): the button busy meanwhile, a toast with the outcome; resolves to
 * the reply when it is ok, else null */
function run(btn, fn, okText) {
	M.busy(btn, true);
	return Promise.resolve().then(fn).then(function(r) {
		M.busy(btn, false);
		if (!r || r.ok !== 1) {
			M.toast(r ? _('Failed: %s').format(M.errText(r)) : _('Management connection lost'), { type: 'error', timeout: 8000 });
			return null;
		}
		if (okText) M.toast(okText, { type: 'success' });
		return r;
	}, function() {
		M.busy(btn, false);
		M.toast(_('Management connection lost'), { type: 'error', timeout: 8000 });
		return null;
	});
}

/* The background task (an engine installation, a subscription, a latency round): one at a time, kept by the backend.
 * watch(label, onTick, onDone) asks for its state every 2 s until it ends; a busy toast says what runs, onTick(job)
 * sees every state (a page shows the log), onDone(job) the last one. A page that opens while a task runs calls
 * resume(), which watches it too. */
var JOB_LABELS = null;
function jobLabel(kind) {
	if (!JOB_LABELS) JOB_LABELS = {
		'core-install': _('Installing the VPN module'), 'engine-install': _('Installing the engine'),
		'sub-add': _('Adding the subscription'), 'sub-refresh': _('Refreshing the subscriptions'),
		'latency-all': _('Testing the latency of every profile'), 'import-url': _('Downloading the config')
	};
	return JOB_LABELS[kind] || _('Working…');
}
var watching = false;
function watch(onTick, onDone) {
	if (watching) return;
	watching = true;
	var msg = null;
	var tick = function() {
		return L.resolveDefault(call.job(), {}).then(function(r) {
			var job = (r && r.job) || {};
			if (onTick) onTick(job);
			if (job.state === 'running') {
				if (!msg) msg = M.toast(jobLabel(job.kind) + '…', { type: 'busy', timeout: 0 });
				setTimeout(tick, 2000);
				return;
			}
			watching = false;
			var last = String(job.log || '').trim().split('\n').pop();
			if (!msg) msg = M.toast('', { type: 'info', timeout: 0 });
			if (job.state === 'done') msg.update(jobLabel(job.kind) + ': ' + _('done'), 'success');
			else msg.update(jobLabel(job.kind) + ': ' + _('failed') + (last ? ' (' + last + ')' : ''), 'error');
			setTimeout(function() { msg.close(); }, job.state === 'done' ? 4000 : 10000);
			if (onDone) onDone(job);
		});
	};
	setTimeout(tick, 1200);
}
function resume(onTick, onDone) {
	return L.resolveDefault(call.job(), {}).then(function(r) {
		var job = (r && r.job) || {};
		if (job.state === 'running') watch(onTick, onDone);
		return job;
	});
}
/* start(btn, fn, onTick, onDone): run fn (a call that starts a task) and watch the task it started */
function start(btn, fn, onTick, onDone) {
	return run(btn, fn).then(function(r) {
		if (r && r.started) watch(onTick, onDone);
		return r;
	});
}

/* An uploaded tarball runs as root (its install hooks): the backend installs it only when its SHA-256 is in the
 * SHA256SUMS of the VPN module's latest release (fetched over HTTPS), unless the user allows an unverified file here,
 * in a step of its own. uploadPkg(btn, fn(unverified), onTick, onDone): that choice, the upload, then the task. */
function uploadPkg(btn, fn, onTick, onDone) {
	return M.choiceBox(_('Verify the uploaded file'),
		_('The uploaded file is checked against the SHA256SUMS of the VPN module\'s latest release, downloaded over HTTPS. When that is not possible (offline, an older release), it is installed only if you allow an unverified file: it then runs as root with no check.'), [
			{ label: _('Cancel'), value: null },
			{ label: _('Install it even if it cannot be verified'), value: '1', danger: true },
			{ label: _('Install only a verified file'), value: '0' }
		]).then(function(unv) {
		if (unv == null) return;
		return ui.uploadFile(UPLOAD_PKG).then(function() {
			return start(btn, function() { return fn(unv); }, onTick, onDone);
		}, function(e) {
			if (e && e.message && !/cancel/i.test(e.message)) M.toast(_('Upload failed: %s').format(e.message), { type: 'error' });
		});
	});
}

/* The card a page shows instead of itself when the VPN module is not installed (or is a v1 module without the
 * machine-readable CLI): what it is, and the button that runs mu300-extra install vpn as a task. */
function installCard(st, onDone) {
	var legacy = st && st.legacy;
	var c = E('section', { 'class': 'mud-card', 'id': 'mvp-install' }, [
		E('h3', {}, [ legacy ? _('The VPN module is too old for this panel') : _('The VPN module is not installed') ]),
		note(legacy
			? _('This page needs the VPN module with the machine-readable mu300-vpn (module v2 or newer). Updating it keeps the profiles and the settings.')
			: _('The VPN is a module of its own: mu300-vpn with its kill switch and profiles, and one extra per engine (Xray, sing-box, mihomo, dae). It is installed with mu300-extra install vpn.')),
		note(_('The download is a few MB for the module and up to tens of MB per engine: on mobile data it counts towards your plan.'))
	]);
	var acts = E('div', { 'class': 'mvp-row' });
	if (st && st.extra) {
		var dl = E('button', { 'class': 'mud-btn on', 'id': 'mvp-install-release' }, [ legacy ? _('Update the VPN module') : _('Install the VPN module') ]);
		dl.addEventListener('click', function() {
			start(dl, function() { return call.coreInstall('release', ''); }, null, onDone);
		});
		var up = E('button', { 'class': 'mud-btn', 'id': 'mvp-install-file' }, [ _('Upload the file…') ]);
		up.addEventListener('click', function() {
			uploadPkg(up, function(unv) { return call.coreInstall('file', unv); }, null, onDone);
		});
		acts.appendChild(dl);
		acts.appendChild(up);
		c.appendChild(acts);
		c.appendChild(note(_('Offline: upload mu300-linux-vpn-core.tar.gz (or the bundle mu300-linux-vpn.tar.gz) from the release page of the VPN module.')));
	} else {
		c.appendChild(E('pre', { 'class': 'mud-out' }, [ 'mu300-extra install vpn' ]));
	}
	return c;
}

/* A dialog of the panel's style with any content: modal(title, nodes, buttons[{label, value, cls}]) ->
 * Promise(value); Escape and the backdrop resolve undefined. buttons' click may be vetoed: a button with a check(fn)
 * runs it first and keeps the dialog open when it resolves false. */
function modal(title, nodes, buttons, opts) {
	opts = opts || {};
	return new Promise(function(resolve) {
		var wrap = E('div', { 'class': 'mud-dlg-wrap' + (opts.big ? ' mvp-big' : '') });
		var btns = E('div', { 'class': 'mud-dlg-btns' });
		var box = E('div', { 'class': 'mud-dlg', 'role': 'dialog', 'aria-modal': 'true' }, [
			E('h4', {}, [ title ]), E('div', { 'class': 'mvp-dlg-body' }, nodes), btns ]);
		wrap.appendChild(box);
		var done = function(v) {
			document.removeEventListener('keydown', onKey, true);
			wrap.remove();
			resolve(v);
		};
		var onKey = function(ev) { if (ev.key == 'Escape') { ev.preventDefault(); done(undefined); } };
		(buttons || []).forEach(function(b) {
			var el = E('button', { 'type': 'button', 'class': 'mud-btn' + (b.cls ? ' ' + b.cls : ''), 'id': b.id || null }, [ b.label ]);
			el.addEventListener('click', function() {
				if (!b.check) return done(b.value);
				M.busy(el, true);
				Promise.resolve(b.check()).then(function(ok) {
					M.busy(el, false);
					if (ok !== false) done(b.value);
				});
			});
			btns.appendChild(el);
		});
		wrap.addEventListener('click', function(ev) { if (ev.target === wrap && !opts.sticky) done(undefined); });
		document.addEventListener('keydown', onKey, true);
		document.body.appendChild(wrap);
		var f = box.querySelector('input,textarea,select');
		if (f) f.focus();
	});
}

/* the profiles a status, an engine or a subscription names by id, as their names */
function names(ids, profiles) {
	var by = {};
	(profiles || []).forEach(function(p) { by[p.id] = p.name || p.id; });
	return (ids || []).map(function(i) { return by[i] || i; });
}

/* a profile's server as host:port (an IPv6 address in brackets) */
function server(p) {
	var s = p && p.server;
	if (!s || !s.host) return '--';
	var h = String(s.host);
	return (h.indexOf(':') >= 0 ? '[' + h + ']' : h) + (s.port != null ? ':' + s.port : '');
}

/* a type's name as the page shows it */
function typeName(t) {
	switch (t) {
		case 'xray': return 'Xray';
		case 'sing-box': return 'sing-box';
		case 'mihomo': return 'mihomo (Clash)';
		case 'wireguard': return 'WireGuard';
		case 'openvpn': return 'OpenVPN';
		case 'dae': return 'dae';
		default: return String(t || '--');
	}
}

/* status(exit_ip) -> the backend's status reply, never a rejection */
function status(exitIp) { return L.resolveDefault(call.status(exitIp ? '1' : ''), {}); }

/* the page frame: the tabs, then either the install card or what paint() makes; returns the root element */
function frame(active) {
	injectCss();
	var root = E('div', { 'class': 'mud', 'id': 'mvp-' + active });
	return root;
}
function paintFrame(root, active, st, body, onInstalled) {
	if (!st || st.installed !== 1 || st.legacy) {
		if (st && st.ok === 0) {
			dom.content(root, [ tabs(active), E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('VPN') ]), note(M.errText(st)) ]) ]);
			return false;
		}
		dom.content(root, [ tabs(active), installCard(st || {}, onInstalled) ]);
		return false;
	}
	dom.content(root, [ tabs(active) ].concat(body));
	return true;
}

return baseclass.extend({
	call: call, UPLOAD_CFG: UPLOAD_CFG, UPLOAD_PKG: UPLOAD_PKG, ENGINES: ENGINES,
	engineName: engineName, typeName: typeName, injectCss: injectCss, tabs: tabs, row: row, pill: pill, card: card,
	note: note, ago: ago, run: run, watch: watch, resume: resume, start: start, jobLabel: jobLabel, uploadPkg: uploadPkg,
	installCard: installCard, modal: modal, names: names, server: server, status: status, frame: frame,
	paintFrame: paintFrame
});
