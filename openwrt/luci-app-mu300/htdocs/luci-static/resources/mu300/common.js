'use strict';
'require rpc';
'require baseclass';
/* The mu300 panel's shared module: rpc declarations, formatting and signal-quality helpers, a theme-aware style
 * sheet. Pages load it with 'require mu300.common' (LuCI maps the dotted name to
 * /luci-static/resources/mu300/common.js).
 *
 * Every text the user sees is an English _() message translated by po/<lang>/mu300.po (spec D6).
 *
 * The styles use theme tokens only: luci-theme-aurora's (--surface/--hairline/--brand/--text-muted/--success...,
 * html[data-darkmode] flips light and dark), falling back step by step to LuCI's standard variables
 * (--background-alt/--border/--primary...) under other themes. Quality colours are not fixed values: they use
 * --success/--warning/--danger and color-mix, so both modes follow the theme.
 *
 * Every call goes on its own (nobatch). LuCI otherwise sends the calls a page makes in the same moment as one HTTP
 * request, which uhttpd answers one call after the other: the battery and CPU of the status snapshot then waited for
 * a lock reading behind it, and when the browser gave up on the request (20 s) every call in it failed together - the
 * whole page went blank while one AT command was stuck. */

var callStatus = rpc.declare({ object: 'mu300dash', method: 'status', expect: { '': {} }, nobatch: true });
var callSignal = rpc.declare({ object: 'mu300dash', method: 'signal', expect: { '': {} }, nobatch: true });
var callSysinfo = rpc.declare({ object: 'mu300dash', method: 'sysinfo', expect: { '': {} }, nobatch: true });
var callAct    = rpc.declare({ object: 'mu300dash', method: 'act', params: [ 'op', 'arg' ], expect: { '': {} }, nobatch: true });
var callAt     = rpc.declare({ object: 'mu300dash', method: 'at', params: [ 'cmd', 'confirm' ], expect: { '': {} }, nobatch: true });
var callAtJob  = rpc.declare({ object: 'mu300dash', method: 'at_job', expect: { '': {} }, nobatch: true });
var callOpsLog = rpc.declare({ object: 'mu300dash', method: 'ops_log', expect: { '': {} }, nobatch: true });
var callAtHist = rpc.declare({ object: 'mu300dash', method: 'at_history', expect: { '': {} }, nobatch: true });
var callLockGet = rpc.declare({ object: 'mu300dash', method: 'lock_get', expect: { '': {} }, nobatch: true });
var callLockFresh = rpc.declare({ object: 'mu300dash', method: 'lock_get', params: [ 'fresh' ], expect: { '': {} }, nobatch: true });
var callLockSet = rpc.declare({ object: 'mu300dash', method: 'lock_set', params: [ 'kind', 'val' ], expect: { '': {} }, nobatch: true });
var callSmsList = rpc.declare({ object: 'mu300dash', method: 'sms_list', params: [ 'page' ], expect: { '': {} }, nobatch: true });
var callSmsShow = rpc.declare({ object: 'mu300dash', method: 'sms_show', params: [ 'id' ], expect: { '': {} }, nobatch: true });
var callSmsSend = rpc.declare({ object: 'mu300dash', method: 'sms_send', params: [ 'num', 'text' ], expect: { '': {} }, nobatch: true });
var callSmsJob  = rpc.declare({ object: 'mu300dash', method: 'sms_job', expect: { '': {} }, nobatch: true });
var callSmsDel  = rpc.declare({ object: 'mu300dash', method: 'sms_delete', params: [ 'id', 'sim' ], expect: { '': {} }, nobatch: true });
var callSmsSync = rpc.declare({ object: 'mu300dash', method: 'sms_sync', expect: { '': {} }, nobatch: true });
var callUsbGet = rpc.declare({ object: 'mu300dash', method: 'usb_get', expect: { '': {} }, nobatch: true });
var callUsbSet = rpc.declare({ object: 'mu300dash', method: 'usb_set', params: [ 'kind', 'value', 'scope', 'auto' ], expect: { '': {} }, nobatch: true });
var callUsbNetList = rpc.declare({ object: 'mu300dash', method: 'usb_net_list', expect: { '': {} }, nobatch: true });
var callUsbNetAdd = rpc.declare({ object: 'mu300dash', method: 'usb_net_add', params: [ 'iface' ], expect: { '': {} }, nobatch: true });
var callLangGet = rpc.declare({ object: 'mu300dash', method: 'lang_get', expect: { '': {} }, nobatch: true });
var callLangSet = rpc.declare({ object: 'mu300dash', method: 'lang_set', params: [ 'op', 'codes', 'source' ], expect: { '': {} }, nobatch: true });
var callPowerGet = rpc.declare({ object: 'mu300dash', method: 'power_get', expect: { '': {} }, nobatch: true });
var callPowerSet = rpc.declare({ object: 'mu300dash', method: 'power_set', params: [ 'op', 'key', 'value' ], expect: { '': {} }, nobatch: true });
var callSimGet = rpc.declare({ object: 'mu300dash', method: 'sim_get', expect: { '': {} }, nobatch: true });
var callSimSet = rpc.declare({ object: 'mu300dash', method: 'sim_set', params: [ 'op', 'value' ], expect: { '': {} }, nobatch: true });
var callSimcardGet = rpc.declare({ object: 'mu300dash', method: 'simcard_get', expect: { '': {} }, nobatch: true });
var callSimcardSet = rpc.declare({ object: 'mu300dash', method: 'simcard_set', params: [ 'kind', 'slot', 'state' ], expect: { '': {} }, nobatch: true });
var callTtlGet = rpc.declare({ object: 'mu300dash', method: 'ttl_get', expect: { '': {} }, nobatch: true });
var callTtlSet = rpc.declare({ object: 'mu300dash', method: 'ttl_set', params: [ 'value' ], expect: { '': {} }, nobatch: true });
var callFwdGet = rpc.declare({ object: 'mu300dash', method: 'forward_get', expect: { '': {} }, nobatch: true });
var callFwdSet = rpc.declare({ object: 'mu300dash', method: 'forward_set', params: [ 'enabled', 'template', 'allow',
	'deny', 'keywords', 'clear', 'webhook', 'webhook_url', 'webhook_format', 'webhook_headers', 'telegram',
	'telegram_token', 'telegram_chat', 'email', 'email_host', 'email_port', 'email_tls', 'email_user', 'email_password',
	'email_from', 'email_to', 'sms', 'sms_number', 'webhook_body', 'webhook_expect' ], expect: { '': {} }, nobatch: true });
var callFwdTest = rpc.declare({ object: 'mu300dash', method: 'forward_test', expect: { '': {} }, nobatch: true });
var callFwdLogClear = rpc.declare({ object: 'mu300dash', method: 'forward_log_clear', expect: { '': {} }, nobatch: true });
var callCpuGet = rpc.declare({ object: 'mu300dash', method: 'cpu_get', expect: { '': {} }, nobatch: true });
var callCpuSet = rpc.declare({ object: 'mu300dash', method: 'cpu_set', params: [ 'profile' ], expect: { '': {} }, nobatch: true });
/* a core online/offline (op core, cpu 1-63) or CPU0's low latency (op lowlatency), state on|off; saved */
var callCpuTune = rpc.declare({ object: 'mu300dash', method: 'cpu_tune', params: [ 'op', 'cpu', 'state' ], expect: { '': {} }, nobatch: true });
/* the CPU voltage offset, EXPERIMENTAL (op set|off|stress; the backend checks every field again) */
var callCpuVoltageGet = rpc.declare({ object: 'mu300dash', method: 'cpu_voltage_get', expect: { '': {} }, nobatch: true });
var callCpuVoltageSet = rpc.declare({ object: 'mu300dash', method: 'cpu_voltage_set',
	params: [ 'op', 'offsets', 'confirm', 'minutes' ], expect: { '': {} }, nobatch: true });
var callPackagesGet = rpc.declare({ object: 'mu300dash', method: 'packages_get', expect: { '': {} }, nobatch: true });
var callPackagesAct = rpc.declare({ object: 'mu300dash', method: 'packages_act', params: [ 'op' ], expect: { '': {} }, nobatch: true });
/* System > Update: what is installed and offered, the one update task, and every action (each parameter a fixed word,
 * a release tag, a size or a SHA-256; the backend checks them again) */
var callUpdateGet = rpc.declare({ object: 'mu300dash', method: 'update_get', params: [ 'sizes' ], expect: { '': {} }, nobatch: true });
/* System > Backup of settings (mu300-api backup.export / backup.import, FINDINGS 155) */
var callBackupExport = rpc.declare({ object: 'mu300dash', method: 'backup_export', params: [ 'passphrase', 'with_password' ],
	expect: { '': {} }, nobatch: true });
var callBackupImport = rpc.declare({ object: 'mu300dash', method: 'backup_import',
	params: [ 'file', 'passphrase', 'dry_run', 'force', 'skip' ], expect: { '': {} }, nobatch: true });
var callBackupJob = rpc.declare({ object: 'mu300dash', method: 'backup_job', expect: { '': {} }, nobatch: true });
var callUpdateJob = rpc.declare({ object: 'mu300dash', method: 'update_job', expect: { '': {} }, nobatch: true });
var callUpdateAct = rpc.declare({ object: 'mu300dash', method: 'update_act',
	params: [ 'op', 'tag', 'systems', 'kernel', 'sha256', 'size', 'flags', 'confirm', 'url' ], expect: { '': {} }, nobatch: true });
var callTrafficGet = rpc.declare({ object: 'mu300dash', method: 'traffic_get', expect: { '': {} }, nobatch: true });
var callTrafficSet = rpc.declare({ object: 'mu300dash', method: 'traffic_set', params: [ 'op', 'value' ], expect: { '': {} }, nobatch: true });
/* System > Terminal: a one-time token for ttyd (rpcd mu300term, ACL luci-app-mu300-terminal) */
var callTermToken = rpc.declare({ object: 'mu300term', method: 'token', expect: { '': {} }, nobatch: true });
/* The live rates' one read: the mu300rates object (rpcd's ucode plugin) returns /proc/net/dev, in rpcd itself (no
 * process started, no AT), every second. Sent on its own (nobatch): a declared call is batched with whatever else is
 * pending, and then waits for the status snapshot, which takes a few hundred milliseconds and made the rates come
 * every two seconds. Resolves to the text, rejects on any failure. */
function callNetDev() {
	return new Promise(function(resolve, reject) {
		rpc.call({ jsonrpc: '2.0', id: Date.now(), method: 'call',
			params: [ rpc.getSessionID(), 'mu300rates', 'netdev', {} ] }, function(res) {
			try {
				var r = res && res.ok ? res.json().result : null;
				if (Array.isArray(r) && r[0] === 0 && r[1] && typeof r[1].data === 'string') resolve(r[1].data);
				else reject(new Error('mu300rates.netdev failed'));
			} catch (e) { reject(e); }
		}, true);
	});
}

/* netDev(text of /proc/net/dev, interface) -> { rx, tx } bytes, or null when it is not there */
function netDev(text, dev) {
	var lines = String(text || '').split('\n');
	for (var i = 0; i < lines.length; i++) {
		var c = lines[i].indexOf(':');
		if (c < 0 || lines[i].substring(0, c).trim() !== dev) continue;
		var f = lines[i].substring(c + 1).trim().split(/\s+/);
		if (f.length < 9) return null;
		return { rx: Number(f[0]), tx: Number(f[8]) };
	}
	return null;
}

/* Mainland carriers by PLMN, for when COPS gives the numeric format. The names are messages: translated once, when
 * the module loads (a page's language does not change without a reload). */
var PLMN_CN = {
	'46000': _('China Mobile'), '46002': _('China Mobile'), '46004': _('China Mobile'), '46007': _('China Mobile'), '46008': _('China Mobile'),
	'46001': _('China Unicom'), '46006': _('China Unicom'), '46009': _('China Unicom'),
	'46003': _('China Telecom'), '46005': _('China Telecom'), '46011': _('China Telecom'), '46012': _('China Telecom'),
	'46015': _('China Broadnet'), '46020': _('China Tietong')
};

/* The same carriers by the name the modem gives, whatever its case and spacing ("CHINA BROADNET", "China  Mobile"): a
 * catalog key matches only exactly, and the modems give these names in capitals (#231). */
var CARRIER_NAMES = {
	'china mobile': _('China Mobile'), 'china unicom': _('China Unicom'), 'china telecom': _('China Telecom'),
	'china broadnet': _('China Broadnet'), 'china tietong': _('China Tietong')
};

/* COPS gives a name (shown through the catalogs, so a known English name is translated) or a numeric PLMN. A name the
 * catalog does not have exactly is looked up without case, then by its PLMN; one that is in neither is shown as the
 * modem gave it (#231) */
function carrierName(carrier) {
	if (!carrier) return '--';
	var name = carrier.name ? String(carrier.name) : '';
	if (name) {
		var t = _(carrier.name);
		if (t !== name) return t;
		var key = name.replace(/\s+/g, ' ').trim().toLowerCase();
		if (Object.prototype.hasOwnProperty.call(CARRIER_NAMES, key)) return CARRIER_NAMES[key];
	}
	return PLMN_CN[carrier.plmn] || name || carrier.plmn || '--';
}

/* Signal quality grade (thresholds from ufi_tools' SignalQuality.kt) as a level key: 'excellent', 'good', 'fair',
 * 'poor' or 'unknown'. qCol gives a level's CSS colour expression, the same in every language; qLabel the level's
 * translated name (qLabel(rsrp, rsrq, sinr) grades first, qLevelLabel names a level a page graded itself). */
function qLevel(rsrp, rsrq, sinr) {
	if (rsrp == null && sinr == null && rsrq == null) return 'unknown';
	if (rsrp != null) {
		if (rsrp >= -90) return 'excellent';
		if (rsrp >= -100) return 'good';
		if (rsrp >= -110) return 'fair';
		return 'poor';
	}
	if (sinr != null) {
		if (sinr >= 20) return 'excellent';
		if (sinr >= 13) return 'good';
		if (sinr >= 0) return 'fair';
		return 'poor';
	}
	if (rsrq >= -8) return 'excellent';
	if (rsrq >= -11) return 'good';
	if (rsrq >= -14) return 'fair';
	return 'poor';
}
function qLevelLabel(level) {
	switch (level) {
		case 'excellent': return _('Excellent');
		case 'good': return _('Good');
		case 'fair': return _('Fair');
		case 'poor': return _('Poor');
		default: return _('Unknown');
	}
}
function qLabel(rsrp, rsrq, sinr) { return qLevelLabel(qLevel(rsrp, rsrq, sinr)); }
function qCol(level) {
	switch (level) {
		case 'excellent': return 'var(--success, #2FBF71)';
		case 'good': return 'color-mix(in oklab, var(--success, #7BC96F) 62%, var(--text, #444))';
		case 'fair': return 'var(--warning, #F2B544)';
		case 'poor': return 'var(--danger, #E25555)';
		default: return 'var(--text-subtle, var(--text-light, #8A8F98))';
	}
}
/* A 0-10 score: RSRP 40% / RSRQ 25% / SINR 35%, interpolated between anchors, a missing value's weight shared out */
function interp(v, pts) {
	if (v == null) return null;
	for (var i = 0; i < pts.length - 1; i++)
		if (v <= pts[i][0])
			return pts[i][1] + (v - pts[i][0]) * (pts[i+1][1] - pts[i][1]) / (pts[i+1][0] - pts[i][0]);
	return pts[pts.length - 1][1];
}
function qScore(s) {
	var parts = [
		[ interp(s.rsrp, [ [ -120, 0 ], [ -110, 3.5 ], [ -100, 6.5 ], [ -90, 8.5 ], [ -80, 10 ] ]), 0.40 ],
		[ interp(s.rsrq, [ [ -20, 0 ], [ -14, 3.5 ], [ -11, 6.5 ], [ -8, 8.5 ], [ -3, 10 ] ]), 0.25 ],
		[ interp(s.sinr, [ [ -5, 0 ], [ 0, 3.5 ], [ 13, 6.5 ], [ 20, 8.5 ], [ 30, 10 ] ]), 0.35 ]
	];
	var sum = 0, w = 0;
	parts.forEach(function(p) { if (p[0] != null) { sum += p[0] * p[1]; w += p[1]; } });
	return w == 0 ? null : Math.max(0, Math.min(10, sum / w));
}

/* ------------------------------------------------------------- formatting */
function esc(s) {
	return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
		return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
	});
}
function fmtBytes(b) {
	if (b == null || isNaN(b)) return '--';
	var u = [ 'B', 'KB', 'MB', 'GB', 'TB' ], i = 0;
	while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
	return (b >= 100 ? b.toFixed(0) : b.toFixed(1)) + ' ' + u[i];
}
function fmtRate(bps) {
	if (bps == null || isNaN(bps) || bps < 0) return '--';
	var u = [ 'B/s', 'KB/s', 'MB/s', 'GB/s' ], i = 0;
	while (bps >= 1024 && i < u.length - 1) { bps /= 1024; i++; }
	return (bps >= 100 ? bps.toFixed(0) : bps.toFixed(1)) + ' ' + u[i];
}
function fmtUptime(s) {
	if (s == null) return '--';
	var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
	if (d > 0) return _('%d d %d h').format(d, h);
	if (h > 0) return _('%d h %d min').format(h, m);
	return _('%d min').format(m);
}

/* ----------------------------------------------------------------- styles */
var CSS = `
/* Bootstrap exposes a different token family. Only activate this bridge when
 * Aurora's --surface token is absent, so existing Aurora styling wins intact.
 * Bootstrap's data-darkmode switch updates these aliases without a reload. */
html.mud-bootstrap-theme{--surface:var(--background-color-high);--surface-sunken:var(--background-color-low);--brand-subtle:color-mix(in srgb,var(--primary-color-high) 10%,var(--background-color-high));--hairline:var(--border-color-low);--text:var(--text-color-high);--text-muted:var(--text-color-medium);--text-subtle:var(--text-color-low);--brand:var(--primary-color-high);--success:var(--success-color-high);--warning:var(--warn-color-high);--danger:var(--error-color-high);--info:var(--primary-color-high);--on-brand:var(--on-primary-color);--hover-faint:var(--background-color-medium)}
.mud{color:var(--text,#222);font-size:.85rem;line-height:1.45}
.mud *{box-sizing:border-box}
.mud-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:10px;margin-top:6px}
.mud-card{background:var(--surface,var(--background-alt,var(--background,#fff)));border:1px solid var(--hairline,var(--border,#e3e6ea));border-radius:calc(var(--radius-base,.5rem) + .375rem);padding:14px 16px;box-shadow:var(--app-shadow-sm,0 1px 3px rgba(0,0,0,.04));transition:border-color .15s}
.mud-card:hover{border-color:color-mix(in oklab,var(--brand,var(--primary,#2f7bf6)) 30%,var(--hairline,var(--border,#e3e6ea)))}
.mud-card>h3{margin:0 0 8px;font-size:.7rem;font-weight:600;color:var(--text-muted,var(--text-light,#787d85));letter-spacing:.08em}
.mud-card>h3::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6));margin-right:7px;vertical-align:1px}
.mud-hero{display:flex;flex-wrap:wrap;gap:12px 28px;align-items:center;background:var(--brand-subtle,var(--surface,#fff));margin-bottom:12px;padding:16px 20px}
.mud-sec{padding:2px 2px 6px}
.mud-sec>h3{margin:16px 0 10px;font-size:.7rem;font-weight:600;color:var(--text-muted,var(--text-light,#787d85));letter-spacing:.08em}
.mud-sec>h3::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6));margin-right:7px;vertical-align:1px}
.mud-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:0 28px}
.mud-body>.mud-sec+.mud-sec{border-top:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 60%,transparent);margin-top:14px;padding-top:2px}
/* First-paint skeleton: the cards keep their place, only the fields still to fill pulse; it stops at the first
 * snapshot. */
@keyframes mudpulse{0%,100%{opacity:1}50%{opacity:.35}}
.mud-booting .mud-v,.mud-booting .mud-kpi b,.mud-booting .mud-rsrp,.mud-booting .mud-rat,.mud-booting .mud-temp span{animation:mudpulse 1.1s ease-in-out infinite}
/* Compact key-value line: key and value side by side (not justified apart), e.g. for the serving network */
.mud-srvline{display:flex;flex-wrap:wrap;gap:4px 10px;padding:2px 0;font-size:.84rem}
.mud-srvline .k{color:var(--text-muted,var(--text-light,#777));flex:0 0 auto}
.mud-srvline .v{font-variant-numeric:tabular-nums;font-weight:500}
.mud-charts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.mud-chart{background:transparent;border-radius:var(--radius-base,.5rem);padding:2px 4px 0}
.mud-chart .t{display:flex;align-items:baseline;gap:8px}
.mud-chart .t b{font-size:1.2rem;font-weight:700;font-variant-numeric:tabular-nums}
.mud-chart .t span{font-size:.72rem;color:var(--text-muted,var(--text-light,#777))}
.mud-chart .c{height:40px;margin-top:2px}
.mud-chart .c svg{display:block;width:100%;height:100%}
.mud-lockbtn{padding:0 10px;border-radius:99px;border:1px solid var(--hairline,var(--border,#ccc));background:var(--surface,var(--background,#fff));color:var(--text,#222);font-size:.72rem;cursor:pointer;line-height:1.7}
.mud-lockbtn.on,.mud-lockbtn:active{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-lockbtn.locked{opacity:.45;pointer-events:none;background:var(--surface-sunken,rgba(127,127,127,.1));color:var(--text-muted,var(--text-light,#888));border-color:transparent}
/* the IMEI rows: a click copies the value; the active card's tag */
.mud-copy{cursor:pointer;border-bottom:1px dotted color-mix(in oklab,var(--hairline,var(--border,#ddd)) 90%,transparent)}
.mud-copy:hover,.mud-copy.ok{color:var(--success,#1e7e34)}
.mud-imei-active{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;background:color-mix(in oklab,var(--success,#2FBF71) 15%,transparent);color:var(--success,#1e7e34);font-size:11px;line-height:18px;font-weight:600}
.mud-hero-l{flex:1 1 260px;min-width:0;display:flex;flex-direction:column;gap:5px}
.mud-hero-r{flex:0 0 auto;text-align:right;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
/* The USB link pill in the hero's status line */
.mud-vsep{display:inline-block;width:1px;height:11px;margin:0 8px 0 2px;vertical-align:-1px;background:color-mix(in oklab,var(--hairline,var(--border,#ddd)) 85%,transparent)}
.mud-usb{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;border-radius:999px;background:var(--surface,var(--background,#fff));border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 80%,transparent);font-size:.72rem;color:var(--text-muted,var(--text-light,#777));white-space:nowrap;vertical-align:1px}
.mud-usb[hidden],.mud-vsep[hidden]{display:none}
.mud-usb i{width:6px;height:6px;border-radius:50%;flex:0 0 auto;background:var(--text-subtle,var(--text-light,#999))}
.mud-usb b{font-weight:600;color:var(--text,#222);font-variant-numeric:tabular-nums}
.mud-usb.warn i{background:var(--warning,#f59e0b)}
.mud-usb.ok i{background:var(--success,#2FBF71)}
.mud-rat{font-size:1.7rem;font-weight:700;line-height:1.1;letter-spacing:.01em}
.mud-op{color:var(--text-muted,var(--text-light,#777));font-size:.85rem}
.mud-cellline{font-size:.78rem;color:var(--text-muted,var(--text-light,#777));font-variant-numeric:tabular-nums;line-height:1.55}
.mud-rsrp{font-size:2.3rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.mud-rsrp small{font-size:.9rem;font-weight:500}
.mud-chips{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}
.mud-tag{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--surface-sunken,rgba(127,127,127,.1));font-size:.74rem;font-weight:600;font-variant-numeric:tabular-nums}
.mud-q{display:inline-block;min-width:3em;padding:1px 8px;border-radius:99px;font-size:.74rem;font-weight:600;text-align:center}
.mud-rows{display:grid;gap:2px}
.mud-r{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 55%,transparent)}
.mud-r:last-child{border-bottom:none}
.mud-k{color:var(--text-muted,var(--text-light,#777));flex:0 0 auto}
.mud-v{font-variant-numeric:tabular-nums;text-align:right;word-break:break-all;font-weight:500}
.mud-bars{display:inline-flex;align-items:flex-end;gap:2px;height:16px;margin-left:8px;vertical-align:baseline}
.mud-bars i{width:3px;border-radius:1px;background:var(--hairline,var(--border,#ccc))}
.mud-bars i.on{background:currentColor}
.mud-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(122px,1fr));gap:10px;margin-bottom:10px}
.mud-kpi{background:var(--surface-sunken,rgba(127,127,127,.06));border-radius:var(--radius-base,.5rem);padding:9px 12px}
.mud-kpi b{display:block;font-size:1.05rem;font-variant-numeric:tabular-nums;font-weight:700;line-height:1.25}
.mud-kpi span{font-size:.68rem;color:var(--text-muted,var(--text-light,#777))}
.mud-sub{font-size:.68rem;color:var(--text-subtle,var(--text-light,#999));font-variant-numeric:tabular-nums}
.mud-meter{height:5px;border-radius:3px;background:var(--surface-sunken,rgba(127,127,127,.15));overflow:hidden;margin:3px 0 1px}
.mud-meter i{display:block;height:100%;border-radius:3px}
.mud-freq{display:flex;align-items:center;font-size:.76rem;font-variant-numeric:tabular-nums;padding:1px 0}
.mud-freq .mud-k{flex:0 0 2.4em}
.mud-manual-cell{margin:12px 0;max-width:720px;min-width:0}
.mud-manual-cell summary{cursor:pointer;padding:8px 0;font-size:.85rem;font-weight:600}
.mud-manual-cell form{margin:4px 0 12px}
.mud-manual-cell-fields{display:flex;flex-wrap:wrap;align-items:end;gap:12px}
.mud-manual-cell-fields label{display:flex;flex:1 1 140px;flex-direction:column;gap:6px;margin:0;min-width:0;font-size:.82rem}
.mud-manual-cell-fields input,.mud-manual-cell-fields select{box-sizing:border-box;width:100%;max-width:100%;min-width:0;min-height:38px;margin:0;padding:8px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit}
.mud-manual-cell-fields button{min-height:38px;margin:0;white-space:normal}
.mud-manual-cell-fields input:focus,.mud-manual-cell-fields select:focus{outline:2px solid var(--brand,var(--primary,#37a));outline-offset:1px}
.mud-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:7px 12px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-size:.82rem;cursor:pointer;user-select:none}
.mud-btn:active{transform:scale(.97)}
.mud-btn.on{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-btn.warn{border-color:color-mix(in oklab,var(--danger,#E25555) 55%,transparent);color:var(--danger,#E25555)}
.mud-btn[disabled]{opacity:.4;pointer-events:none}
.mud-ctl{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:7px}
.mud-note{font-size:.72rem;color:var(--text-subtle,var(--text-light,#999));margin-top:7px}
.mud-danger{border:2px solid var(--danger,#E25555);border-radius:var(--radius-base,.5rem);padding:9px 12px;margin:8px 0;color:var(--danger,#E25555);font-weight:600;font-size:.84rem;background:color-mix(in oklab,var(--danger,#E25555) 8%,transparent)}
.mud-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.8rem}
.mud-table th{font-weight:500;color:var(--text-muted,var(--text-light,#777));text-align:right;padding:1px 4px;border-bottom:1px solid var(--hairline,var(--border,#ddd));font-size:.72rem;position:sticky;top:0;background:var(--surface,var(--background-alt,var(--background,#fff)))}
.mud-table td{text-align:right;padding:3px 6px;border-bottom:1px dashed color-mix(in oklab,var(--hairline,var(--border,#ddd)) 5%,transparent)}
.mud-table th:first-child,.mud-table td:first-child{text-align:left}
.mud-scroll{max-height:230px;overflow:auto;border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 45%,transparent);border-radius:var(--radius-base,.5rem);padding:4px}
.mud-cli{padding:4px 8px;border-radius:var(--radius-base,.5rem);border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 55%,transparent);margin-bottom:5px}
.mud-cli .t{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mud-cli .s{font-size:.72rem;color:var(--text-muted,var(--text-light,#888));font-variant-numeric:tabular-nums;margin-top:1px}
/* ---- chat-style SMS ---- */
.mud-chat{display:flex;gap:12px;min-height:420px}
.mud-convs{flex:0 0 240px;overflow:auto;max-height:520px}
.mud-conv{padding:7px 9px;border-radius:var(--radius-base,.5rem);cursor:pointer;margin-bottom:4px;border:1px solid transparent}
.mud-conv:hover{background:var(--hover-faint,rgba(127,127,127,.06))}
.mud-conv.sel{background:var(--brand-subtle,var(--surface-sunken,rgba(127,127,127,.08)));border-color:color-mix(in oklab,var(--brand,var(--primary,#2f7bf6)) 30%,transparent)}
.mud-conv .n{display:flex;justify-content:space-between;gap:6px;font-weight:600;font-size:.84rem}
.mud-conv .p{font-size:.74rem;color:var(--text-muted,var(--text-light,#888));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.mud-thread{flex:1;display:flex;flex-direction:column;min-width:0;border-left:1px solid var(--hairline,var(--border,#ddd));padding-left:12px}
.mud-msgs{flex:1;overflow:auto;max-height:460px;padding:4px 2px;display:flex;flex-direction:column;gap:6px}
.mud-bub{max-width:78%;padding:6px 11px;border-radius:calc(var(--radius-base,.5rem) + .25rem);font-size:.85rem;white-space:pre-wrap;word-break:break-word;align-self:flex-start;background:var(--surface-sunken,rgba(127,127,127,.08))}
.mud-bub.out{align-self:flex-end;background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-bub .tm{display:block;font-size:.64rem;opacity:.65;margin-top:2px;text-align:right;font-variant-numeric:tabular-nums}
.mud-comp{display:flex;gap:8px;margin-top:8px}
.mud-comp input,.mud-comp textarea{padding:7px 11px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-family:inherit}
.mud-comp input{flex:0 0 170px}
.mud-comp textarea{flex:1;resize:none;min-height:40px;max-height:120px}
@media(max-width:700px){.mud-chat{flex-direction:column}.mud-convs{flex:none;max-height:150px}.mud-thread{border-left:none;padding-left:0;border-top:1px solid var(--hairline,var(--border,#ddd));padding-top:8px}}
/* ---- AT terminal ---- */
.mud-at-grid{display:grid;grid-template-columns:1fr 220px;gap:10px}
.mud-at-grid .mud-scroll{max-height:340px}
@media(max-width:700px){.mud-at-grid{grid-template-columns:1fr}.mud-term{height:300px}.mud-at-grid .mud-scroll{max-height:120px}}
.mud-term{font-family:var(--font-mono,monospace);font-size:.8rem;line-height:1.5;background:color-mix(in oklab,var(--surface,#14161a) 92%,var(--brand,#2f7bf6) 3%);color:var(--text,#d5d9de);border:1px solid var(--hairline,var(--border,#2a2d33));border-radius:var(--radius-base,.5rem);padding:12px;height:380px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.mud-term .ln-cmd{color:var(--brand,#6ab0ff);font-weight:600}
.mud-term .ln-ok{color:var(--success,#57c98a);font-weight:600}
.mud-term .ln-err{color:var(--danger,#ff7b72);font-weight:600}
.mud-term .ln-data{color:var(--text,#d5d9de)}
.mud-term .ln-meta{color:var(--text-subtle,#7d8590);font-style:italic}
.mud-term .ln-ms{float:right;color:var(--text-subtle,#7d8590);font-size:.7rem}
.mud-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text-subtle,#8A8F98);margin-right:6px;vertical-align:1px}
.mud-dot.on{background:var(--success,#2FBF71)}
.mud-temp{display:inline-flex;gap:5px;flex-wrap:wrap}
.mud-temp span{padding:1px 8px;border-radius:var(--radius-base,.5rem);background:var(--surface-sunken,rgba(127,127,127,.08));font-variant-numeric:tabular-nums;font-size:.76rem}
.mud-at-in{display:flex;gap:7px;margin-bottom:7px}
.mud-at-in input{flex:1;min-width:0;padding:6px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-family:var(--font-mono,monospace)}
.mud-out{font-family:var(--font-mono,monospace);font-size:.78rem;white-space:pre-wrap;background:var(--surface-sunken,rgba(127,127,127,.07));border-radius:var(--radius-base,.5rem);padding:9px;max-height:260px;overflow:auto;margin:7px 0 0}
.mud-chiprow{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}
.mud-chip{padding:2px 9px;border-radius:99px;border:1px solid var(--hairline,var(--border,#ccc));font-size:.72rem;font-family:var(--font-mono,monospace);cursor:pointer}
.mud-chip.on{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-sms-item{padding:6px 8px;border-radius:var(--radius-base,.5rem);border:1px solid color-mix(in oklab,var(--hairline,var(--border,#ddd)) 60%,transparent);margin-bottom:6px;cursor:pointer}
.mud-sms-item:hover{background:var(--hover-faint,rgba(127,127,127,.05))}
.mud-sms-top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mud-badge{display:flex;align-items:center;justify-content:center;font-size:.68rem;padding:0 7px;min-width:20px;height:20px;box-sizing:border-box;border-radius:99px;background:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
/* Phone-width hero, as on the lock page: the info block on top, the RSRP block wrapping to the next line (right-
 * aligned) */
@media(max-width:600px){
.mud-hero{gap:8px}
.mud-hero-l{flex:1 1 100%}
.mud-hero-r{flex:1 0 100%;flex-direction:row;justify-content:space-between;align-items:baseline;text-align:left}
.mud-rsrp{font-size:1.9rem}
.mud-chips{justify-content:flex-end}}
/* ---- top toasts and busy buttons (the feedback framework every page shares) ---- */
.mud-toasts{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;width:max-content;max-width:min(92vw,560px)}
.mud-toast{pointer-events:auto;display:flex;align-items:center;gap:9px;padding:9px 16px;border-radius:99px;background:var(--surface,var(--background,#fff));border:1px solid var(--hairline,var(--border,#ddd));box-shadow:0 6px 24px rgba(0,0,0,.14);font-size:.82rem;color:var(--text,#222);animation:mudtoast-in .22s ease-out;max-width:100%}
.mud-toast.out{animation:mudtoast-out .25s ease-in forwards}
.mud-toast .mud-tico{flex:0 0 auto;width:8px;height:8px;border-radius:50%;background:var(--brand,var(--primary,#2f7bf6))}
.mud-toast.success .mud-tico{background:var(--success,#2FBF71)}
.mud-toast.error .mud-tico{background:var(--danger,#E25555)}
.mud-toast.busy .mud-tico{width:12px;height:12px;background:transparent;border:2px solid color-mix(in oklab,var(--brand,#2f7bf6) 30%,transparent);border-top-color:var(--brand,#2f7bf6);animation:mudspin .7s linear infinite}
@keyframes mudtoast-in{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:none}}
@keyframes mudtoast-out{to{opacity:0;transform:translateY(-8px)}}
@keyframes mudspin{to{transform:rotate(360deg)}}
.mud-toast.notify{flex-direction:row;align-items:center;max-width:340px;text-align:left;border-radius:calc(var(--radius-base,.5rem) + .375rem)}
.mud-toast.notify .mud-nb{display:flex;flex-direction:column;gap:2px;min-width:0}
.mud-toast.notify .mud-nb b{font-size:.82rem;font-weight:700}
.mud-toast.notify .mud-nb span{font-size:.76rem;color:var(--text-muted,var(--text-light,#888));overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.mud-btn .mud-spin,.mud-lockbtn .mud-spin{flex:0 0 auto;width:12px;height:12px;border-radius:50%;border:2px solid color-mix(in oklab,currentColor 30%,transparent);border-top-color:currentColor;animation:mudspin .7s linear infinite}
.mud-btn.busy,.mud-lockbtn.busy{pointer-events:none;opacity:.75}
/* ---- themed dialogs (instead of the browser's confirm/alert) ---- */
.mud-dlg-wrap{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.42);animation:mudfade-in .16s ease-out;padding:20px}
.mud-dlg{background:var(--surface,var(--background,#fff));border:1px solid var(--hairline,var(--border,#ddd));border-radius:calc(var(--radius-base,.5rem) + .5rem);box-shadow:0 18px 50px rgba(0,0,0,.28);max-width:420px;width:100%;padding:18px 20px 16px;animation:muddlg-in .2s cubic-bezier(.2,.9,.3,1.15)}
.mud-dlg h4{margin:0 0 8px;font-size:.95rem;font-weight:700;color:var(--text,#222)}
.mud-dlg .mud-dlg-msg{font-size:.84rem;line-height:1.6;color:var(--text-muted,var(--text-light,#666));white-space:pre-wrap;word-break:break-word}
.mud-dlg .mud-dlg-btns{display:flex;gap:8px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}
/* An SMS bubble's delete button: a red bin, faint until hovered; the text keeps room on the right so they do not
 * overlap */
.mud-bub{position:relative;padding-right:28px}
.mud-del{position:absolute;top:2px;right:2px;display:flex;align-items:center;justify-content:center;width:24px;height:24px;padding:0;border:none;background:transparent;color:var(--danger,#E25555);opacity:.4;cursor:pointer;border-radius:50%}
.mud-del:hover{opacity:.9;background:color-mix(in oklab,var(--danger,#E25555) 12%,transparent)}
.mud-del svg{display:block}
/* Fade from the preview to the full text, so the first load does not jump */
@keyframes mudfadein{from{opacity:.25}to{opacity:1}}
.mud-bub .bd.fadein{animation:mudfadein .25s ease-out}
@keyframes muddlg-in{from{opacity:0;transform:scale(.94) translateY(10px)}to{opacity:1;transform:none}}
@keyframes mudfade-in{from{opacity:0}to{opacity:1}}
/* Phone-width lock page hero: the RSRP number left-aligned (right-aligned on other screens) */
@media(max-width:600px){
.mud-rsrp{text-align:left}}
`;

function injectCss() {
	// Discard our own aliases before probing, otherwise the second LuCI page
	// would mistake this bridge for Aurora and turn it off.
	document.documentElement.classList.remove('mud-bootstrap-theme');
	var theme = getComputedStyle(document.documentElement);
	var hasAuroraTokens = theme.getPropertyValue('--surface').trim() ||
		getComputedStyle(document.body).getPropertyValue('--surface').trim();
	document.documentElement.classList.toggle('mud-bootstrap-theme',
		!hasAuroraTokens && !!theme.getPropertyValue('--background-color-high').trim());
	if (document.getElementById('mud-style')) return;
	var st = document.createElement('style');
	st.id = 'mud-style';
	st.textContent = CSS;
	document.head.appendChild(st);
}
function v(id) { return document.getElementById('mud-' + id); }
function set(id, text, color) {
	var e = v(id);
	if (!e) return;
	e.textContent = (text == null || text === '') ? '--' : text;
	if (color !== undefined) e.style.color = color;
}
function spark(el, arr, min, max, win) {
	if (!el || !arr || arr.length < 2) return;
	var w = 100, h = 34, pts = [];
	for (var i = 0; i < arr.length; i++) {
		pts.push([ i / (win - 1) * w,
			h - Math.max(0, Math.min(1, (arr[i] - min) / (max - min || 1))) * (h - 3) - 1.5 ]);
	}
	/* Catmull-Rom to cubic Bezier: the polyline becomes a smooth curve */
	var d = 'M' + pts[0][0].toFixed(1) + ',' + pts[0][1].toFixed(1);
	for (var i = 0; i < pts.length - 1; i++) {
		var p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
		d += 'C' + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + ',' + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(1) +
			' ' + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + ',' + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(1) +
			' ' + p2[0].toFixed(1) + ',' + p2[1].toFixed(1);
	}
	var last = pts.length - 1;
	var area = d + ' L' + pts[last][0].toFixed(1) + ',' + h + ' L' + pts[0][0].toFixed(1) + ',' + h + ' Z';
	var gid = 'mudg-' + (el.id || Math.floor(Math.random() * 1e6));
	el.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
		'<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
		'<stop offset="0" stop-color="currentColor" stop-opacity=".32"/>' +
		'<stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>' +
		'<path d="' + area + '" fill="url(#' + gid + ')"/>' +
		'<path d="' + d + '" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>';
}

/* The neighbour-cell table (home and lock pages): NR first, by RSRP, a lock button at the end of each row.
 * lockedCell is lock_get's cell field (e.g. "nr:627264,501"); a matching row shows a grey "Locked".
 * Pages bind the clicks by event delegation ([data-lock] attribute: "<rat>:<arfcn>,<pci>"). */
function neighborRows(c, lockedCell) {
	var nb = (c && Array.isArray(c.neigh) ? c.neigh : []).filter(function(n) {
		return n && typeof n === 'object' && (n.rat === 'nr' || n.rat === 'lte');
	});
	nb.sort(function(a, b) {
		if ((a.rat == 'nr') != (b.rat == 'nr')) return a.rat == 'nr' ? -1 : 1;
		return (b.rsrp || -999) - (a.rsrp || -999);
	});
	if (!nb.length)
		return '<tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">' + esc(_('No neighbor-cell data')) + '</td></tr>';
	var lk = Array.isArray(lockedCell) ? lockedCell.join('|') : (lockedCell || '');
	return nb.map(function(n) {
		var level = qLevel(n.rsrp, n.rsrq, n.sinr);
		var key = n.rat + ':' + n.arfcn + ',' + n.pci;
		var isLocked = lk.split('|').indexOf(key) >= 0;
		return '<tr><td>' + (n.rat == 'nr' ? 'NR n' + esc(n.band) : 'LTE B' + esc(n.band)) + '</td>' +
			'<td>' + esc(n.pci != null ? n.pci : '--') + '</td>' +
			'<td>' + esc(n.arfcn != null ? n.arfcn : '--') + '</td>' +
			'<td style="color:' + qCol(level) + '">' + (n.rsrp != null ? n.rsrp.toFixed(1) : '--') + '</td>' +
			'<td>' + (n.rsrq != null ? n.rsrq.toFixed(1) : '--') + '</td>' +
			'<td>' + (n.sinr != null ? n.sinr.toFixed(1) : '--') + '</td>' +
			'<td><button class="mud-lockbtn' + (isLocked ? ' locked' : '') + '" data-lock="' + key + '"' +
			(isLocked ? ' disabled' : '') + '>' + esc(isLocked ? _('Locked') : _('Lock')) + '</button></td></tr>';
	}).join('');
}

/* M.errText(res): a backend reply's error for the page. The backend sends one fixed English sentence in res.error
 * (its catalog entries are extracted from the backend's sources) and the value it carried, if any, in res.detail:
 * the sentence in the page's language, then the value as data. No error: 'Unknown error'. */
function errText(res) {
	if (!res || !res.error) return _('Unknown error');
	return res.detail ? _(res.error) + ' (' + res.detail + ')' : _(res.error);
}

/* --------------------------------------------------------------- feedback
 * M.toast(text, {type, timeout}): one notice at the top, type = info|success|error|busy (busy has a spinner, for
 * work in progress). Returns {update(text, type), close()}, so a long operation updates one toast instead of
 * stacking them. timeout = 0: it stays. text is shown as given (callers pass _() messages).
 * M.busy(btn[, on]): adds/removes a spinner in a button and disables it; without on, toggles. */
function toast(text, opts) {
	opts = opts || {};
	if (!document.getElementById('mud-toasts')) {
		var w = document.createElement('div');
		w.id = 'mud-toasts';
		w.className = 'mud-toasts';
		document.body.appendChild(w);
	}
	var t = document.createElement('div');
	t.className = 'mud-toast ' + (opts.type || 'info');
	t.innerHTML = '<i class="mud-tico"></i><span></span>';
	t.lastChild.textContent = text == null ? '' : text;
	document.getElementById('mud-toasts').appendChild(t);
	var timer = null, dead = false;
	var life = opts.timeout !== undefined ? opts.timeout : 3500;
	var arm = function() {
		if (timer) clearTimeout(timer);
		if (life > 0) timer = setTimeout(close, life);
	};
	var close = function() {
		if (dead) return;
		dead = true;
		if (timer) clearTimeout(timer);
		t.classList.add('out');
		setTimeout(function() { t.remove(); }, 260);
	};
	arm();
	return {
		update: function(text2, type2) {
			if (dead) return;
			t.lastChild.textContent = text2 == null ? '' : text2;
			if (type2) t.className = 'mud-toast ' + type2;
			arm();
		},
		close: close
	};
}
function busy(btn, on) {
	if (!btn || !btn.classList) return;
	if (on === undefined) on = !btn.classList.contains('busy');
	if (on && !btn.classList.contains('busy')) {
		btn.classList.add('busy');
		var s = document.createElement('i');
		s.className = 'mud-spin';
		btn.insertBefore(s, btn.firstChild);
	} else if (!on && btn.classList.contains('busy')) {
		btn.classList.remove('busy');
		var sp = btn.querySelector('.mud-spin');
		if (sp) sp.remove();
	}
}

/* ---------------------------------------------------------------- dialogs
 * M.confirmBox(title, message, opts) -> Promise<boolean>: a themed confirmation; Cancel, the backdrop and Escape
 * resolve false, OK and Enter resolve true.
 * M.alertBox(title, message, opts) -> Promise<true>: one button.
 * opts: { danger: true for a red OK button, okText, cancelText }; with danger, the focus starts on Cancel. */
function dialog(opts) {
	opts = opts || {};
	return new Promise(function(resolve) {
		var wrap = document.createElement('div');
		wrap.className = 'mud-dlg-wrap';
		var withCancel = opts.cancelText !== null;
		wrap.innerHTML = '<div class="mud-dlg" role="dialog" aria-modal="true">' +
			'<h4></h4><div class="mud-dlg-msg"></div><div class="mud-dlg-btns">' +
			(withCancel ? '<button type="button" class="mud-btn" data-r="0"></button>' : '') +
			'<button type="button" class="mud-btn' + (opts.danger ? ' warn' : '') + '" data-r="1"></button>' +
			'</div></div>';
		wrap.querySelector('h4').textContent = opts.title || _('Confirm');
		wrap.querySelector('.mud-dlg-msg').textContent = opts.message || '';
		var btns = wrap.querySelectorAll('.mud-dlg-btns .mud-btn');
		btns[btns.length - 1].textContent = opts.okText || _('OK');
		if (withCancel) btns[0].textContent = opts.cancelText || _('Cancel');
		var done = function(r) {
			document.removeEventListener('keydown', onKey, true);
			wrap.remove();
			resolve(r);
		};
		var onKey = function(ev) {
			if (ev.key == 'Escape') { ev.preventDefault(); done(withCancel ? false : true); }
			else if (ev.key == 'Enter') { ev.preventDefault(); done(true); }
		};
		wrap.addEventListener('click', function(ev) {
			var b = ev.target.closest('button');
			if (b) done(b.getAttribute('data-r') == '1');
			else if (ev.target === wrap && withCancel) done(false);
		});
		document.addEventListener('keydown', onKey, true);
		document.body.appendChild(wrap);
		/* a dangerous action starts focused on Cancel, so a stray Enter does nothing */
		(withCancel && opts.danger ? btns[0] : btns[btns.length - 1]).focus();
	});
}
/* A phone-notification style banner: a title (the sender) and a two-line preview, 6 s by default */
function notify(title, message, opts) {
	opts = opts || {};
	if (!document.getElementById('mud-toasts')) {
		var w = document.createElement('div');
		w.id = 'mud-toasts';
		w.className = 'mud-toasts';
		document.body.appendChild(w);
	}
	var t = document.createElement('div');
	t.className = 'mud-toast notify ' + (opts.type || 'info');
	t.innerHTML = '<i class="mud-tico"></i><div class="mud-nb"><b></b><span></span></div>';
	t.querySelector('b').textContent = title == null ? '' : title;
	t.querySelector('span').textContent = message == null ? '' : String(message);
	document.getElementById('mud-toasts').appendChild(t);
	var life = opts.timeout !== undefined ? opts.timeout : 6000;
	var timer = life > 0 ? setTimeout(function() {
		t.classList.add('out');
		setTimeout(function() { t.remove(); }, 260);
	}, life) : null;
	t.addEventListener('click', function() {
		if (timer) clearTimeout(timer);
		t.remove();
	});
	return t;
}

/* M.backoff(base, fails) -> the wait before the next poll: base while calls succeed, doubled after each failure in a
 * row, at most twelve times base (30 s for the home page's 2.5 s and less). A page that keeps polling a backend that
 * cannot answer only adds to what it waits for. */
function backoff(base, fails) {
	var n = Math.max(0, Math.min(Number(fails) || 0, 4));
	return Math.min(base * Math.pow(2, n), base * 12);
}

/* M.ageText(seconds) -> "42 s" / "3 min" / "2 h": how old a reading is */
function ageText(s) {
	s = Math.max(0, Math.round(Number(s) || 0));
	if (s < 120) return _('%d s').format(s);
	if (s < 7200) return _('%d min').format(Math.round(s / 60));
	return _('%d h').format(Math.round(s / 3600));
}

/* New-SMS watcher (each page's render calls it; one instance): every 5 s it reads page 1 of the local pool (a file
 * read, no AT), the first time only to set the baseline. A higher message id that is incoming (mt) then shows a
 * notification with the sender and a preview. When the pool is cleared (the id drops) the baseline is reset
 * silently. */
var smsWatch = null;
function watchSms() {
	if (smsWatch) return;
	smsWatch = { seen: null, busy: false, fails: 0, skip: 0 };
	window.setInterval(function() {
		/* never a second read while one is out (a slow backend would otherwise collect a request every 5 s), and
		 * fewer reads after failures (backoff) */
		if (smsWatch.busy) return;
		if (smsWatch.skip > 0) { smsWatch.skip--; return; }
		smsWatch.busy = true;
		Promise.resolve(callSmsList(1)).then(function(r) {
			smsWatch.fails = r && !r.error ? 0 : smsWatch.fails + 1;
			return r;
		}, function() {
			smsWatch.fails++;
			return {};
		}).then(function(r) {
			smsWatch.busy = false;
			smsWatch.skip = Math.round(backoff(1, smsWatch.fails)) - 1;
			r = r || {};
			var msgs = r.msgs || [], max = 0;
			msgs.forEach(function(m) {
				var id = parseInt(m.id, 10) || 0;
				if (id > max) max = id;
			});
			if (!max) return;
			if (smsWatch.seen == null || max < smsWatch.seen) { smsWatch.seen = max; return; }
			if (max > smsWatch.seen) {
				msgs.forEach(function(m) {
					var id = parseInt(m.id, 10) || 0;
					if (id > smsWatch.seen && m.dir === 'mt')
						notify(_('New SMS · %s').format(m.peer || _('Unknown number')), m.preview || '', { type: 'success' });
				});
				smsWatch.seen = max;
			}
		});
	}, 5000);
}

function confirmBox(title, message, opts) {
	opts = opts || {};
	opts.title = title;
	opts.message = message;
	return dialog(opts);
}
function alertBox(title, message, opts) {
	opts = opts || {};
	opts.title = title;
	opts.message = message;
	opts.cancelText = null;
	return dialog(opts);
}

/* A multiple-choice dialog: M.choiceBox(title, message, [{label, value, danger}], opts) -> Promise(the chosen
 * value); the backdrop and Escape resolve undefined. The buttons go left to right, danger ones red. */
function choiceBox(title, message, choices, opts) {
	opts = opts || {};
	return new Promise(function(resolve) {
		var wrap = document.createElement('div');
		wrap.className = 'mud-dlg-wrap';
		var btns = (choices || []).map(function(c, i) {
			return '<button type="button" class="mud-btn' + (c.danger ? ' warn' : '') +
				'" data-i="' + i + '"></button>';
		}).join('');
		wrap.innerHTML = '<div class="mud-dlg" role="dialog" aria-modal="true">' +
			'<h4></h4><div class="mud-dlg-msg"></div>' +
			'<div class="mud-dlg-btns">' + btns + '</div></div>';
		wrap.querySelector('h4').textContent = title || _('Select');
		wrap.querySelector('.mud-dlg-msg').textContent = message || '';
		(choices || []).forEach(function(c, i) {
			wrap.querySelector('[data-i="' + i + '"]').textContent = c.label || '?';
		});
		var done = function(v) {
			document.removeEventListener('keydown', onKey, true);
			wrap.remove();
			resolve(v);
		};
		var onKey = function(ev) {
			if (ev.key == 'Escape') { ev.preventDefault(); done(undefined); }
		};
		wrap.addEventListener('click', function(ev) {
			var b = ev.target.closest('button');
			if (b) done(choices[parseInt(b.getAttribute('data-i'), 10)].value);
			else if (ev.target === wrap) done(undefined);
		});
		document.addEventListener('keydown', onKey, true);
		document.body.appendChild(wrap);
		var first = wrap.querySelector('.mud-dlg-btns .mud-btn');
		if (first) first.focus();
	});
}

/* LuCI's require treats a module as a class factory: it must return a baseclass subclass, and the loader hands out
 * an instance of it */
return baseclass.extend({
	callStatus: callStatus, callSignal: callSignal, callSysinfo: callSysinfo, callAct: callAct, callAt: callAt, callAtHist: callAtHist,
	callAtJob: callAtJob, callOpsLog: callOpsLog, backoff: backoff, ageText: ageText,
	callLockGet: callLockGet, callLockFresh: callLockFresh, callLockSet: callLockSet,
	callSmsList: callSmsList, callSmsShow: callSmsShow, callSmsSend: callSmsSend, callSmsJob: callSmsJob,
	callSmsDel: callSmsDel, callSmsSync: callSmsSync,
	callUsbGet: callUsbGet, callUsbSet: callUsbSet,
	callUsbNetList: callUsbNetList, callUsbNetAdd: callUsbNetAdd,
	callLangGet: callLangGet, callLangSet: callLangSet,
	callPowerGet: callPowerGet, callPowerSet: callPowerSet,
	callSimGet: callSimGet, callSimSet: callSimSet, callSimcardGet: callSimcardGet, callSimcardSet: callSimcardSet,
	callTtlGet: callTtlGet, callTtlSet: callTtlSet,
	callFwdGet: callFwdGet, callFwdSet: callFwdSet, callFwdTest: callFwdTest, callFwdLogClear: callFwdLogClear,
	callCpuGet: callCpuGet, callCpuSet: callCpuSet, callCpuTune: callCpuTune, callCpuVoltageGet: callCpuVoltageGet, callCpuVoltageSet: callCpuVoltageSet,
	callPackagesGet: callPackagesGet, callPackagesAct: callPackagesAct,
	callUpdateGet: callUpdateGet, callUpdateJob: callUpdateJob, callUpdateAct: callUpdateAct,
	callBackupExport: callBackupExport, callBackupImport: callBackupImport, callBackupJob: callBackupJob,
	callTrafficGet: callTrafficGet, callTrafficSet: callTrafficSet, callNetDev: callNetDev, netDev: netDev,
	callTermToken: callTermToken,
	carrierName: carrierName, qLevel: qLevel, qLevelLabel: qLevelLabel, qLabel: qLabel, qCol: qCol, qScore: qScore,
	esc: esc, fmtBytes: fmtBytes, fmtRate: fmtRate, fmtUptime: fmtUptime, PLMN_CN: PLMN_CN,
	injectCss: injectCss, v: v, set: set, spark: spark, neighborRows: neighborRows,
	errText: errText, toast: toast, busy: busy, confirmBox: confirmBox, alertBox: alertBox, choiceBox: choiceBox,
	notify: notify, watchSms: watchSms
});
