'use strict';
'require view';
'require uci';
'require mu300.common as M';

/* MU300 status dashboard -- the LuCI landing page (menu.d hangs it at admin/home).
 *
 * Layout: the serving-cell card at the top is the only card; the rest are full-width sections (link & traffic /
 * neighbor cells / Wi-Fi, LAN, device, SIM / quick controls). Locks, SMS and the AT terminal are in the Cellular
 * submenu.
 *
 * One data source: ubus mu300dash status (the info snapshot + the fast-tier cellular cache sig + the slow-tier
 * cache cell). The page polls every 1.5 s by default: signal, rates and CPU refresh every round; neighbors, QoS
 * and identity are redrawn only when the slow tier's timestamp changes.
 * A neighbor row's Lock goes through lock_set cell (SFUN restarts the radio stack, about half a minute without
 * service); the lock state comes from lock_get (read once when the page loads, again after a lock). */

var DEFAULT_POLL_S = 1.5;
var RATE_WIN = 40;
var RATE_MS = 1000;

function pollSeconds(value) {
	var seconds = Number(value);
	return Number.isFinite(seconds) && seconds >= 0.5 && seconds <= 60 ? seconds : DEFAULT_POLL_S;
}

/* NR bands the modem's SPLBAND masks can carry (unisoc-modem/lock NR_A/NR_B/NR_S): n6 is supported but not in the
 * tables, so an automatic NR read-back can never list it */
var NR_CODEC = [ 1, 2, 3, 5, 7, 8, 12, 20, 25, 28, 66, 70, 71, 74, 34, 38, 39, 40, 41, 50, 51, 77, 78, 79,
	75, 76, 80, 81, 82, 83, 84, 86 ];
var BAND_CAPS = { nr: [ 1, 5, 6, 8, 28, 41, 78 ], lte: [ 1, 3, 5, 8, 34, 38, 39, 40, 41 ] };

/* lockDeviations(lock_get reply) -> the settings that are not automatic, as short labels ([] = all automatic), or
 * null when the state is unknown. A band lock is automatic when nothing is locked or every supported band (that the
 * masks can carry) is in it - the modem reads the cleared mask back as the full set. */
function lockDeviations(l) {
	if (!l || l.error || !l.mode) return null;
	var out = [], caps = l.caps || {};
	var names = { auto152: _('Automatic without 2G (152)'), '4g': _('4G only'), sa: '5G SA', nsa: '5G NSA' };
	if (l.mode.label && l.mode.label !== 'auto')
		out.push(_('Network mode: %s').format(names[l.mode.label] || l.mode.label));
	if (l.endc && l.endc !== '1') out.push(_('EN-DC off'));
	[ 'lte', 'nr' ].forEach(function(rat) {
		var locked = String((l[rat] && l[rat].locked) || '').split(',').map(Number).filter(function(b) { return b > 0; });
		if (!locked.length) return;
		var cap = String(caps[rat] || '').split(',').map(Number).filter(function(b) { return b > 0; });
		if (!cap.length) cap = BAND_CAPS[rat];
		if (rat === 'nr') cap = cap.filter(function(b) { return NR_CODEC.indexOf(b) >= 0; });
		if (cap.every(function(b) { return locked.indexOf(b) >= 0; })) return;
		var p = rat === 'nr' ? 'n' : 'B';
		out.push((rat === 'nr' ? _('NR band lock: %s') : _('LTE band lock: %s')).format(p + locked.join(' ' + p)));
	});
	if ((l.cells || []).length) out.push(_('Cell lock: %s').format(l.cells.join(' ')));
	return out;
}

/* The engineering port gives only MCS/BLER; the modulation is derived here from the common 3GPP MCS table 1,
 * without an extra AT request for a display item. LTE uplink has other MCS boundaries than downlink/NR. */
function modulation(mcs, rat, uplink) {
	if (mcs == null || isNaN(Number(mcs))) return '--';
	mcs = Number(mcs);
	if (rat === 'lte' && uplink) {
		if (mcs >= 0 && mcs <= 10) return 'QPSK';
		if (mcs <= 20) return '16QAM';
		if (mcs <= 28) return '64QAM';
	} else {
		if (mcs >= 0 && mcs <= 9) return 'QPSK';
		if (mcs <= 16) return '16QAM';
		if (mcs <= 28) return '64QAM';
	}
	return '--';
}

function radioMetricRows(label, rat, cell) {
	var dlMcs = cell && cell.dl_mcs != null ? cell.dl_mcs : null;
	var ulMcs = cell && cell.ul_mcs != null ? cell.ul_mcs : null;
	var dlBler = cell && cell.dl_bler != null ? cell.dl_bler : null;
	var ulBler = cell && cell.ul_bler != null ? cell.ul_bler : null;
	var prefix = label ? label + ' ' : '';
	var pair = function(a, b, suffix) {
		return (a != null ? a + suffix : '--') + ' / ' + (b != null ? b + suffix : '--');
	};
	return '<div class="mud-r"><span class="mud-k">' + prefix + _('Modulation DL/UL') + '</span>' +
		'<span class="mud-v" title="' + _('Estimated from current MCS') + '">' + modulation(dlMcs, rat, false) + ' / ' + modulation(ulMcs, rat, true) + '</span></div>' +
		'<div class="mud-r"><span class="mud-k">' + prefix + _('MCS DL/UL') + '</span><span class="mud-v">' + pair(dlMcs, ulMcs, '') + '</span></div>' +
		'<div class="mud-r"><span class="mud-k">' + prefix + _('BLER DL/UL') + '</span><span class="mud-v">' + pair(dlBler, ulBler, '%') + '</span></div>';
}

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		this._root = root;
		this._bootEl = root;
		root.className = 'mud mud-booting';
		root.innerHTML = this.html();
		this.wire(root);
		var self = this;
		/* WPA3 on the hotspot was switched off at boot (openwrt-wifi-wpa2-only, FINDINGS 80): the notice of Status >
		 * Overview, from the same module (its own English, Turkish and Chinese), above the cards until dismissed */
		if (typeof L.require === 'function')
			Promise.resolve().then(function() { return L.require('view.status.include.05_mu300_wpa2'); }).then(function(m) {
				return m && m.load().then(function(n) {
					var box = m.render(n);
					if (box) root.insertBefore(box, root.firstChild);
				});
			}).catch(function() {});
		var intervalMs = DEFAULT_POLL_S * 1000;
		var config = L.resolveDefault(uci.load('unisoc_modem')).then(function() {
			intervalMs = pollSeconds(uci.get('unisoc_modem', 'main', 'home_refresh_interval')) * 1000;
		});
		/* Read the full status once now; never run the sysinfo and status collections twice at the same time. */
		var first = L.resolveDefault(M.callStatus()).then(function(st) {
			self.update(st || {});
		});
		/* LuCI poll.add() truncates intervals to whole seconds. Use a one-shot
		 * timer so 1.5 s remains 1.5 s and slow requests never overlap; after a
		 * failed snapshot the next one waits longer (M.backoff), up to 18 s. */
		var fails = 0;
		function refresh() {
			if (!document.documentElement.contains(root)) return;
			Promise.resolve(M.callStatus()).then(function(st) {
				fails = st && st.info ? 0 : fails + 1;
				self.update(st || {});
			}, function() { fails++; }).finally(function() {
				if (document.documentElement.contains(root))
					self._refreshTimer = setTimeout(refresh, M.backoff(intervalMs, fails));
			});
		}
		Promise.all([first, config]).then(function() {
			self._refreshTimer = setTimeout(refresh, intervalMs);
		});
		/* The live rates have a lane of their own, every second: one read of /proc/net/dev in rpcd, no process and
		 * no AT. The time is the browser's, taken halfway through the request (the status snapshot's whole seconds made
		 * the rates jump by half at a 1.5 s poll). */
		var clock = function() { return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now(); };
		function rates() {
			if (!document.documentElement.contains(root)) return;
			var t0 = clock();
			L.resolveDefault(M.callNetDev(), null).then(function(text) {
				self.rate(text, (t0 + clock()) / 2);
			}).finally(function() {
				if (document.documentElement.contains(root))
					self._rateTimer = setTimeout(rates, RATE_MS);
			});
		}
		/* (render() runs before the page is in the document: the lane starts once the first snapshot is in) */
		first.then(function() { self._rateTimer = setTimeout(rates, 0); });
		return root;
	},

	unload: function() {
		clearTimeout(this._refreshTimer);
		clearTimeout(this._rateTimer);
	},

	/* One reading of the fast lane: rates over the time since the last one (a counter that went down - the interface
	 * was made again - starts over), the sparklines and the totals since the interface came up. */
	rate: function(text, t) {
		var dev = this.dataDev || 'sipa_eth0', c = text ? M.netDev(text, dev) : null;
		if (!c || !isFinite(c.rx) || !isFinite(c.tx)) return;
		this.fastNet = true;
		var last = this.lastRate;
		this.lastRate = { dev: dev, t: t, rx: c.rx, tx: c.tx };
		M.set('rx', M.fmtBytes(c.rx)); M.set('tx', M.fmtBytes(c.tx));
		if (!last || last.dev !== dev || t - last.t < 200 || c.rx < last.rx || c.tx < last.tx) return;
		var dt = (t - last.t) / 1000;
		this.pushRate((c.rx - last.rx) / dt, (c.tx - last.tx) / dt);
	},

	pushRate: function(dl, ul) {
		M.set('dl', M.fmtRate(dl)); M.set('ul', M.fmtRate(ul));
		this.dlHist.push(dl); this.ulHist.push(ul);
		if (this.dlHist.length > RATE_WIN) { this.dlHist.shift(); this.ulHist.shift(); }
		var peak = Math.max(1, Math.max.apply(null, this.dlHist.concat(this.ulHist)));
		M.spark(M.v('spark-dl'), this.dlHist, 0, peak, RATE_WIN);
		M.spark(M.v('spark-ul'), this.ulHist, 0, peak, RATE_WIN);
	},

	/* Today's and this billing cycle's mobile data (mu300-traffic's summary in the status snapshot), against the cap */
	paintUsage: function(u) {
		var box = M.v('usage');
		if (!box) return;
		box.style.display = u ? '' : 'none';
		if (!u) return;
		M.set('usage-today', M.fmtBytes(u.today));
		var cap = u.cap > 0 ? u.cap : 0, pct = cap ? Math.min(100, Math.round(u.used * 100 / cap)) : null;
		M.set('usage-cycle', cap ? _('%s of %s').format(M.fmtBytes(u.used), M.fmtBytes(cap)) : M.fmtBytes(u.used));
		var col = u.level === 'over' ? 'var(--danger,#E25555)' : u.level === 'warn' ? 'var(--warning,#f59e0b)' : '';
		M.v('usage-cycle').style.color = col;
		var bar = M.v('usage-bar');
		M.v('usage-meter').style.display = cap ? '' : 'none';
		bar.style.width = (pct || 0) + '%';
		bar.style.background = col || 'var(--brand,var(--primary,#3b82f6))';
		M.set('usage-note', u.level === 'over' ? _('The monthly cap is reached') :
			u.level === 'warn' ? _('%d%% of the monthly cap is used').format(pct) : _('This billing cycle'));
	},

	html: function() {
		return `
<div class="mud-card mud-hero">
  <div class="mud-hero-l">
    <div style="font-size:.78rem;color:var(--text-muted,var(--text-light,#777))">
      <span class="mud-dot" id="mud-dot"></span><b id="mud-host" style="color:var(--text,#222)">--</b>
      <span id="mud-uptime"></span><span class="mud-vsep" id="mud-vsep" hidden></span><span class="mud-usb" id="mud-usb" hidden></span></div>
    <div class="mud-rat" id="mud-rat">--<span class="mud-bars" id="mud-bars"><i style="height:25%"></i><i style="height:45%"></i><i style="height:65%"></i><i style="height:85%"></i><i style="height:100%"></i></span></div>
    <div class="mud-op" id="mud-op">--</div>
    <div class="mud-cellline" id="mud-cellline"></div>
    <div class="mud-note" id="mud-stale" style="display:none;margin:4px 0 0;color:var(--warning,#f59e0b)"></div>
    <a class="mud-note" id="mud-update" href="${L.url('admin/system/mu300-update')}" style="display:none;margin:4px 0 0;color:var(--brand,var(--primary,#2f7bf6))"></a>
  </div>
  <div class="mud-hero-r">
    <div class="mud-rsrp" id="mud-rsrp">--</div>
    <div class="mud-chips" id="mud-metric-chips"></div>
  </div>
</div>

<div class="mud-card mud-body">

<div class="mud-sec">
  <h3>${_('Link & traffic')}</h3>
  <div class="mud-charts">
    <div class="mud-chart" style="color:var(--brand,var(--primary,#2f7bf6))">
      <div class="t"><b id="mud-dl">--</b><span>${_('Download rate')}</span></div>
      <div class="c" id="mud-spark-dl"></div>
    </div>
    <div class="mud-chart" style="color:var(--success,#2FBF71)">
      <div class="t"><b id="mud-ul">--</b><span>${_('Upload rate')}</span></div>
      <div class="c" id="mud-spark-ul"></div>
    </div>
  </div>
  <div class="mud-kpis">
    <div class="mud-kpi"><b id="mud-rx">--</b><span>${_('Total received')}</span></div>
    <div class="mud-kpi"><b id="mud-tx">--</b><span>${_('Total sent')}</span></div>
  </div>
  <a class="mud-kpis" id="mud-usage" href="${L.url('admin/modem/traffic')}" style="display:none;color:inherit;text-decoration:none">
    <div class="mud-kpi"><b id="mud-usage-today">--</b><span>${_('Used today')}</span></div>
    <div class="mud-kpi"><b id="mud-usage-cycle">--</b><span id="mud-usage-note">${_('This billing cycle')}</span><div class="mud-meter" id="mud-usage-meter"><i id="mud-usage-bar"></i></div></div>
  </a>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">IPv4 / IPv6</span><span class="mud-v" id="mud-ip">--</span></div>
        <div class="mud-r"><span class="mud-k">APN</span><span class="mud-v" id="mud-apn">--</span></div>
        <div class="mud-r" id="mud-simsrc-r" style="display:none"><span class="mud-k">${_('SIM source')}</span><span class="mud-v" id="mud-simsrc">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Session duration')}</span><span class="mud-v" id="mud-sess">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Registration')}</span><span class="mud-v" id="mud-reg">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Data roaming')}</span><span class="mud-v" id="mud-roam">--</span></div>
        <div class="mud-r"><span class="mud-k">DNS</span><span class="mud-v" id="mud-dns">--</span></div>
      </div>
    </div>
    <div>
      <div class="mud-rows" id="mud-lteanchor"></div>
      <div class="mud-rows" id="mud-radio-metrics">
        <div class="mud-r"><span class="mud-k">${_('Modulation DL/UL')}</span><span class="mud-v">-- / --</span></div>
        <div class="mud-r"><span class="mud-k">${_('MCS DL/UL')}</span><span class="mud-v">-- / --</span></div>
        <div class="mud-r"><span class="mud-k">${_('BLER DL/UL')}</span><span class="mud-v">-- / --</span></div>
      </div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">${_('Bandwidth')}</span><span class="mud-v" id="mud-bw">--</span></div>
        <div class="mud-r"><span class="mud-k">QCI</span><span class="mud-v" id="mud-qci">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('AMBR DL/UL')}</span><span class="mud-v" id="mud-ambr">--</span></div>
      </div>
    </div>
  </div>
</div>


<div class="mud-sec">
  <h3>${_('Wi-Fi · LAN · Device · SIM')}</h3>
  <div class="mud-temp" id="mud-temps"></div>
  <div class="mud-kpis" style="margin-top:8px">
    <div class="mud-kpi"><b id="mud-cpu">--</b><span>${_('CPU usage')}</span><div class="mud-meter"><i id="mud-cpu-bar" style="background:var(--brand,var(--primary,#3b82f6))"></i></div></div>
    <div class="mud-kpi"><b id="mud-ram">--</b><span>${_('Memory')} · <span class="mud-sub" id="mud-ram-sub">--</span></span><div class="mud-meter"><i id="mud-ram-bar" style="background:var(--info,#0ea5e9)"></i></div></div>
    <div class="mud-kpi"><b id="mud-disk">--</b><span>${_('Storage')}</span><div class="mud-meter"><i id="mud-disk-bar" style="background:var(--warning,#f59e0b)"></i></div></div>
    <div class="mud-kpi" id="mud-batt-kpi" style="display:none"><b id="mud-batt">--</b><span id="mud-batt-l">${_('Battery')}</span><div class="mud-meter" id="mud-batt-meter" hidden aria-hidden="true"><i id="mud-batt-bar" style="width:0%;background:var(--success,#16a34a)"></i></div></div>
  </div>
  <div id="mud-freqs" class="mud-freqs"></div>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">SSID</span><span class="mud-v" id="mud-ssid">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Channel')}</span><span class="mud-v" id="mud-chan">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Encryption')}</span><span class="mud-v" id="mud-wenc">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Hidden SSID')}</span><span class="mud-v" id="mud-whid">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Country')}</span><span class="mud-v" id="mud-wcountry">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('AP status')}</span><span class="mud-v" id="mud-whostapd">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('USB network')}</span><span class="mud-v" id="mud-wusb">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Connection tracking')}</span><span class="mud-v" id="mud-conntrack">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('LAN address')}</span><span class="mud-v" id="mud-lanip">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Wi-Fi clients')}</span><span class="mud-v" id="mud-wcl">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('DHCP leases')}</span><span class="mud-v" id="mud-wleases">--</span></div>
      </div>
      <div id="mud-clist" style="margin-top:8px"></div>
    </div>
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">${_('Device model')}</span><span class="mud-v" id="mud-model">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('System')}</span><span class="mud-v" id="mud-fwos">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Modem')}</span><span class="mud-v" id="mud-modem">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Carrier')}</span><span class="mud-v" id="mud-carr">--</span></div>
        <div class="mud-r"><span class="mud-k">PLMN</span><span class="mud-v" id="mud-plmn">--</span></div>
        <div class="mud-r" id="mud-imei1-r"><span class="mud-k" id="mud-imei1-k">IMEI1 (SIM1)</span><span class="mud-v mud-copy" id="mud-imei1">--</span></div>
        <div class="mud-r" id="mud-imei2-r" style="display:none"><span class="mud-k" id="mud-imei2-k">IMEI2 (SIM2)</span><span class="mud-v mud-copy" id="mud-imei2">--</span></div>
        <div class="mud-r"><span class="mud-k">IMSI</span><span class="mud-v" id="mud-imsi">--</span></div>
        <div class="mud-r"><span class="mud-k">ICCID</span><span class="mud-v" id="mud-iccid">--</span></div>
        <div class="mud-r" id="mud-msisdn-r" style="display:none"><span class="mud-k">${_('Own number')}</span><span class="mud-v" id="mud-msisdn">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Module')}</span><span class="mud-v" id="mud-fwmodel">--</span></div>
        <div class="mud-r"><span class="mud-k">${_('Firmware')}</span><span class="mud-v" id="mud-fw">--</span></div>
      </div>
      <div class="mud-chiprow"><span class="mud-chip" id="mud-reveal">${_('Show SIM identifiers')}</span></div>
    </div>
  </div>
  <div id="mud-leases" style="margin-top:10px"></div>
</div>


<div class="mud-sec">
  <h3>${_('Quick controls')}</h3>
  <div class="mud-ctl" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
    <button class="mud-btn" id="mud-btn-data">${_('Data connection')}</button>
    <button class="mud-btn" id="mud-btn-radio">${_('Cellular radio')}</button>
    <button class="mud-btn" id="mud-btn-roam">${_('Data roaming')}</button>
    <button class="mud-btn" id="mud-btn-wifi">${_('Wi-Fi hotspot')}</button>
    <button class="mud-btn warn" id="mud-btn-modem">${_('Restart modem')}</button>
    <button class="mud-btn warn" id="mud-btn-reboot">${_('Power off or restart')}</button>
    <button class="mud-btn warn" id="mud-btn-android">${_('Switch to Android')}</button>
    <button class="mud-btn" id="mud-btn-bootlock">${_('Lock Linux')}</button>
  </div>
</div>
<div class="mud-sec">
  <h3>${_('Neighbor cells')}</h3>
  <div class="mud-scroll">
  <table class="mud-table"><thead><tr><th>${_('RAT/Band')}</th><th>PCI</th><th>${_('Frequency')}</th><th>RSRP</th><th>RSRQ</th><th>SINR</th><th></th></tr></thead>
  <tbody id="mud-neigh"><tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">--</td></tr></tbody></table>
  </div>
</div>

</div><!-- /mud-body -->`;
	},

	wire: function(root) {
		var self = this;
		this.identShown = false;
		this.dlHist = []; this.ulHist = [];
		this.lastNet = null; this.lastCpu = null; this.lastFullTs = 0;
		this.lastRate = null; this.fastNet = false;
		this.lockedCell = '';
		/* render() runs before the node is in the document, so look up relative to root (update, once mounted, looks in the whole document) */
		var q = function(id) { return root.querySelector('#mud-' + id); };

		/* One kind of feedback: a spinning button (M.busy) + a toast at the top, the same as on the lock and SMS pages */
		var act = function(op, arg, note, btn) {
			M.busy(btn, true);
			M.toast(note || _('Running %s…').format(op), { type: 'busy' });
			return L.resolveDefault(M.callAct(op, arg)).then(function(r) {
				r = r || {};
				M.busy(btn, false);
				M.toast(r.ok ? (r.started ? _('Started in background: %s') : _('Done: %s')).format(r.op || op)
					: _('Failed: %s').format(M.errText(r)),
					{ type: r.ok ? 'success' : 'error' });
			}, function() { M.busy(btn, false); M.toast(_('Request failed'), { type: 'error' }); });
		};
		q('btn-data').onclick = function() {
			var up = self.lastInfo && self.lastInfo.wan && self.lastInfo.wan.up;
			act('data', up ? 'down' : 'up', up ? _('Disconnecting data…') : _('Connecting data…'), this);
		};
		q('btn-radio').onclick = function() {
			var on = self.lastCell && self.lastCell.cfun === 1;
			var btn = this;
			(on
				? M.confirmBox(_('Turn off cellular radio'), _('This SIM will have no calls, SMS or data, and its radio stays off after a reboot until you turn it on again.'), { danger: true })
				: M.confirmBox(_('Turn on cellular radio'), _('The radio of the SIM in use comes on and stays on after a reboot (up to about 1 minute).'))
			).then(function(go) { if (go) act('radio', on ? 'off' : 'on', null, btn); });
		};
		/* data roaming of the SIM in use (mobile-data, FINDINGS 120): off keeps data down while the SIM is registered
		 * as roaming. Some SIMs say "roaming" at home (an MVNO on another operator's network, national roaming). */
		q('btn-roam').onclick = function() {
			var ro = self.lastInfo && self.lastInfo.wan && self.lastInfo.wan.roaming;
			var on = !ro || !!ro.allowed;
			var btn = this;
			(on
				? M.confirmBox(_('Turn data roaming off'), _('While the SIM is registered on a roaming network, mobile data stays off; SMS, calls and the registration are not affected. Some SIMs report roaming at home (an MVNO, national roaming): there, data would stop as well.'))
				: M.confirmBox(_('Allow data roaming'), _('Mobile data is used on roaming networks too, which can cost extra. Some SIMs report roaming at home (an MVNO, national roaming) and need this to have data at all.'))
			).then(function(go) { if (go) act('roaming', on ? 'off' : 'on', null, btn); });
		};
		q('btn-wifi').onclick = function() {
			var on = self.lastInfo && self.lastInfo.wifi && self.lastInfo.wifi.up;
			act('wifi', on ? 'off' : 'on', null, this);
		};
		q('btn-modem').onclick = function() {
			var btn = this;
			M.confirmBox(_('Restart modem'), _('Cellular connectivity may stop for 1–2 minutes.'), { danger: true })
				.then(function(go) { if (go) act('modem-reset', null, null, btn); });
		};
		/* power off or restart (kanoqwq's b8d4f222): both the orderly way, after the reply is out */
		q('btn-reboot').onclick = function() {
			var btn = this;
			M.choiceBox(_('Power off or restart'), _('All connections will be interrupted.'), [
				{ label: _('Cancel'), value: undefined },
				{ label: _('Power off'), value: 'poweroff', danger: true },
				{ label: _('Restart'), value: 'reboot', danger: true }
			]).then(function(op) { if (op === 'poweroff' || op === 'reboot') act(op, null, null, btn); });
		};
		/* The network mode and band locks live in the modem's NV, which Android shares: they stay in force there (#96).
		 * When the last lock state shows anything not automatic, the dialog says so and offers to reset first; when
		 * the state cannot be read, it is the plain dialog. The switch itself is never blocked. */
		q('btn-android').onclick = function() {
			var btn = this;
			var msg = [ _('The next boot will enter Android and reboot now. This management page and cellular sharing will disconnect.'),
				_('To return to OpenWrt, run mu300-next-boot linux in Android and reboot.'),
				_('Or do nothing: after five boots that do not finish, it falls back automatically.') ].join('\n');
			var go = function() { act('os', 'android', _('Preparing Android boot and rebooting…'), btn); };
			L.resolveDefault(M.callLockGet(), null).then(function(l) {
				if (l && l.mode) self.lastLock = l;
				var lk = lockDeviations(self.lastLock);
				if (!lk || !lk.length)
					return M.confirmBox(_('Switch to Android'), msg, { danger: true, okText: _('Switch and reboot') })
						.then(function(ok) { if (ok) go(); });
				return M.choiceBox(_('Switch to Android'),
					_('Not automatic: %s. The network mode and band locks are stored in the modem and stay in force in Android too; if the network does not match them, Android shows No Service.').format(lk.join(', ')) +
						'\n\n' + msg,
					[ { label: _('Cancel'), value: '' },
					  { label: _('Switch and reboot'), value: 'plain', danger: true },
					  { label: _('Reset locks and switch'), value: 'reset', danger: true } ])
					.then(function(choice) {
						if (choice === 'plain') go();
						else if (choice === 'reset') self.resetLocksThen(self.lastLock, btn, go);
					});
			});
		};
		q('btn-bootlock').onclick = function() {
			var btn = this;
			var locked = !!(self.lastInfo && self.lastInfo.capabilities && self.lastInfo.capabilities.bootlock);
			(locked
				? M.confirmBox(_('Unlock Linux'),
					_('After five boots in a row that do not finish, the device falls back to Android again.'),
					{ okText: _('Unlock') })
				: M.confirmBox(_('Lock Linux'),
					[ _('The device will never switch to Android by itself, not even after failed boots: Android only from this page (Switch to Android) or with mu300-next-boot android.'),
					  _('A new kernel from an update still gets its first boots on trial, and locks again once one of them succeeds.'),
					  _('If the system itself no longer starts, the device waits on USB (telnet) instead of going to Android.') ].join('\n'),
					{ danger: true, okText: _('Lock') })
			).then(function(go) { if (go) act('os', locked ? 'unlock' : 'lock', null, btn); });
		};
		q('reveal').onclick = function() {
			self.identShown = !self.identShown;
			M.v('reveal').textContent = self.identShown ? _('Hide SIM identifiers') : _('Show SIM identifiers');
			self.paintIdent(self.lastCell);
		};
		/* a click on an IMEI copies the whole value; the panel is plain HTTP (no secure context), so execCommand is
		 * the fallback of the clipboard API */
		[ 0, 1 ].forEach(function(n) {
			var el = q('imei' + (n + 1));
			if (!el) return;
			el.onclick = function() {
				var v = self.imeiFull && self.imeiFull[n];
				if (!v) return;
				var copy = navigator.clipboard && window.isSecureContext ? navigator.clipboard.writeText(v)
					: new Promise(function(res, rej) {
						var ta = document.createElement('textarea');
						ta.value = v; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.top = '-1000px';
						document.body.appendChild(ta); ta.select();
						try { document.execCommand('copy') ? res() : rej(new Error('copy')); } catch (e) { rej(e); }
						document.body.removeChild(ta);
					});
				copy.then(function() {
					el.classList.add('ok');
					setTimeout(function() { el.classList.remove('ok'); }, 900);
					M.toast('IMEI' + (n + 1) + ' \u2713', { type: 'success' });
				}, function() { M.toast('\u2717', { type: 'error' }); });
			};
		});

		/* Lock from a neighbor row (event delegation) */
		q('neigh').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-lock')) return;
			var key = btn.getAttribute('data-lock');
			M.confirmBox(_('Lock cell %s?').format(key.replace(':', ' ')),
				_('The radio stack will restart (SFUN); cellular service will stop for about 30 seconds.'), { danger: true })
				.then(function(go) {
				if (!go) return;
				M.busy(btn, true);
			M.toast(_('Locking %s in the background, about 30 seconds').format(key), { type: 'busy' });
			L.resolveDefault(M.callLockSet('cell', key)).then(function(r) {
				r = r || {};
				M.busy(btn, false);
					M.toast(r.ok ? _('Locked %s in the background; the status will refresh shortly').format(key)
						: _('Lock failed: %s').format(M.errText(r)),
						{ type: r.ok ? 'success' : 'error' });
					setTimeout(function() { self.refreshLock(); }, 35000);
				});
			});
		});
		this.refreshLock();
	},

	refreshLock: function() {
		var self = this;
		L.resolveDefault(M.callLockGet()).then(function(l) {
			if (l && l.mode) self.lastLock = l;
			/* no reading yet (a reboot): the backend reads in the background; ask again a little later */
			if (l && l.busy && (self._lockTries = (self._lockTries || 0) + 1) <= 8) {
				setTimeout(function() { self.refreshLock(); }, 5000);
				return;
			}
			self.lockedCell = (l || {}).cells || [];
			self.repaintNeigh();
		});
	},

	/* Reset every lock to automatic (lock_set reset: one SFUN restart, the saved settings cleared), wait until the
	 * lock cache shows a read newer than the one before (or two minutes pass), then run then(). A refused reset stops
	 * here: the user asked for the reset first. */
	/* The reset is verified by the backend (unisoc-modem/lock reset: the writes' answers checked, the settings read
	 * back before and after the restart, the saved locks deleted only then) and its result comes with lock_get as
	 * "reset"; a result newer than the one shown before the click decides. A failed reset stops the switch: Android
	 * would come up with the locks the modem still has. */
	resetLocksThen: function(before, btn, then) {
		var base = (before && before.ts) || 0, prevEnd = ((before || {}).reset || {}).end || 0, tries = 0;
		M.busy(btn, true);
		M.toast(_('Resetting the network locks to automatic (SFUN restart, about 30 seconds)…'), { type: 'busy' });
		L.resolveDefault(M.callLockSet('reset', 'auto'), null).then(function(r) {
			r = r || {};
			if (!r.ok) {
				M.busy(btn, false);
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error' });
				return;
			}
			var step = function() {
				L.resolveDefault(M.callLockGet(), null).then(function(l) {
					l = l || {};
					var res = l.reset || {}, done = res.state && res.state !== 'running' && (res.end || 0) > prevEnd;
					if (done && res.state !== 'ok') {
						M.busy(btn, false);
						M.toast(_('Reset failed: %s. The saved lock settings were kept.').format(
							M.errText({ error: res.error, detail: res.cmd || res.step })), { type: 'error' });
						return;
					}
					var fresh = l.ts && l.ts > base, auto = done || (fresh && !(lockDeviations(l) || [ 1 ]).length);
					if (auto || ++tries > 60) {
						M.busy(btn, false);
						if (!auto)
							M.toast(_('The lock reset was not confirmed in time; switching anyway.'), { type: 'error' });
						then();
					} else setTimeout(step, 2500);
				});
			};
			setTimeout(step, 2500);
		});
	},

	/* The SIM the configuration asks for, when it is not the one in use (dashboard-info's "sim", from mu300-sim).
	 * On a device without slot 1 the selector uses the external card and every figure on this page is that card's:
	 * say so, instead of showing it as if the internal SIM had been taken (issue #194). A row only then. */
	paintSim: function(sim) {
		var row = M.v('simsrc-r');
		if (!row) return;
		var text = '';
		if (sim && sim.wanted === 1 && sim.slot !== 1)
			text = sim.slot1_supported ? _('Internal SIM selected: in use after a reboot')
				: _('Internal SIM selected, but not supported on this device: the external SIM is in use');
		row.style.display = text ? '' : 'none';
		M.set('simsrc', text || '--');
	},

	paintIdent: function(cell) {
		var id = cell && cell.ident;
		var mask = function(s) {
			if (!s) return '--';
			return this.identShown ? s : s.substring(0, 4) + '****' + s.substring(s.length - 3);
		}.bind(this);
		/* The module has two IMEIs, one per card (cell's imei1/imei2, read over diag, FINDINGS 221). Both are shown,
		 * the active one tagged, and a click copies the full value (the page may be masking it). Where diag gave
		 * nothing (another modem, the 5.4 kernel), the AT+CGSN IMEI stands in for the active card's, and a row with
		 * no value at all is hidden. */
		var slot = id && typeof id.slot === 'number' ? id.slot : 0;
		var imei = [ id && id.imei1, id && id.imei2 ];
		if (!imei[slot] && id && id.imei) imei[slot] = id.imei;
		this.imeiFull = imei;
		for (var n = 0; n < 2; n++) {
			var k = M.v('imei' + (n + 1) + '-k'), r = M.v('imei' + (n + 1) + '-r');
			if (k) k.innerHTML = 'IMEI' + (n + 1) + ' (SIM' + (n + 1) + ')' +
				(slot === n && imei[n] ? ' <span class="mud-imei-active">\u2713 ' + _('in use') + '</span>' : '');
			if (r) r.style.display = imei[n] || n === slot ? '' : 'none';
			M.set('imei' + (n + 1), mask(imei[n]));
		}
		M.set('imsi', mask(id && id.imsi));
		M.set('iccid', mask(id && id.iccid));
		/* The SIM's own number (CNUM, else the IMS identity; full rounds only): a row only when there is one, masked
		 * like the other identifiers. Personal data: shown here, never logged (the backend keeps it out of its logs). */
		var num = cell && !cell.error && typeof cell.msisdn === 'string' ? cell.msisdn : '';
		var numRow = M.v('msisdn-r');
		if (numRow) numRow.style.display = num ? '' : 'none';
		M.set('msisdn', mask(num));
		M.set('fwmodel', id ? (id.model || '--') : '--');
		M.set('fw', id ? (id.fw || '--') : '--');
	},

	repaintNeigh: function() {
		var el = M.v('neigh');
		if (el && this.lastCell) el.innerHTML = M.neighborRows(this.lastCell, this.lockedCell);
	},

	/* The USB cable's link speed (dashboard-info's usblink). Device mode reports the gadget's negotiated
	 * speed, host mode the attached device's. Hidden with nothing plugged in; amber when the link is below what the
	 * port can do -- a USB 2.0 cable or host port caps a 10 Gbps port at 480 Mbps. */
	paintUsb: function(l) {
		var el = M.v('usb'), sep = M.v('vsep');
		if (!el) return;
		var mbps = l && typeof l.mbps === 'number' ? l.mbps : null;
		if (mbps == null) { el.hidden = true; if (sep) sep.hidden = true; return; }
		var names = { 'low-speed': 'Low Speed', 'full-speed': 'Full Speed', 'high-speed': 'High Speed',
			'super-speed': 'SuperSpeed', 'super-speed-plus': 'SuperSpeed+' };
		var rate = function(n) { return n >= 1000 ? (n / 1000) + ' Gbps' : n + ' Mbps'; };
		var max = typeof l.max_mbps === 'number' ? l.max_mbps : null;
		var name = names[l.speed] || (l.role === 'host' ? 'Host' : 'link');
		el.className = 'mud-usb' + (max == null ? '' : (mbps >= max ? ' ok' : ' warn'));
		el.innerHTML = '<i></i><b>' + M.esc('USB ' + name + ' \u00b7 ' + rate(mbps)) + '</b>';
		el.title = l.role === 'host'
			? 'USB host mode, attached device ' + rate(mbps)
			: 'USB ' + rate(mbps) + ' now, port maximum ' + (max == null ? '--' : (names[l.max] ? names[l.max] + ' ' : '') + rate(max));
		el.hidden = false;
		if (sep) sep.hidden = false;
	},
	update: function(st) {
		var i = st.info || {};
		this.lastInfo = i;
		if (i.ts && this._bootEl) {
			this._bootEl.classList.remove('mud-booting');
			this._bootEl = null;
		}
		/* Fast tier on top: sig (serving cell/registration, every 1.5 s) overrides the same fields of the slow-tier cache c */
		var c = st.cell || null;
		var s = st.sig || null;
		if (s && !s.error && (!c || !c.ts || (s.ts || 0) >= c.ts)) {
			if (c && s.partial) {
				/* The first core reply updates the registration, but a transient empty CESQ does not wipe the last engineering signal. */
				c = Object.assign({}, c, {
					ts: s.ts, cfun: s.cfun, reg: s.reg, reg5g: s.reg5g
				});
			} else c = c ? Object.assign({}, c, {
				ts: s.ts, cfun: s.cfun, reg: s.reg, reg5g: s.reg5g,
				sig_src: s.sig_src, sig: s.sig, lte: s.lte, nr: s.nr
			}) : s;
		}
		this.lastCell = c;
		/* Slow-tier data (neighbors/carrier/identity) is redrawn only when the whole cache's timestamp changes */
		var fullTs = (st.cell && st.cell.ts) || 0;
		var slowChanged = fullTs !== this.lastFullTs;
		this.lastFullTs = fullTs;

		M.set('host', i.host);
		M.set('uptime', i.uptime ? _('Uptime %s').format(M.fmtUptime(i.uptime)) : '');
		M.v('dot').className = 'mud-dot' + (i.modem && i.modem.alive ? ' on' : '');
		this.paintUsb(i.usblink);

		var sig = c && !c.error ? (c.sig || {}) : {};
		var rsrp = sig.rsrp, rsrq = sig.rsrq, sinr = sig.sinr;
		/* On 4G sig is the LTE block (the backend's sig_src prefers NR > LTE > CESQ); c.lte.sinr is the same source
		 * at another timestamp -- only a fallback when sinr is missing, never shown next to it */
		if (sinr == null && c && c.lte && !c.nr && c.lte.sinr != null) sinr = c.lte.sinr;
		var label = M.qLabel(rsrp, rsrq, sinr), score = M.qScore({ rsrp: rsrp, rsrq: rsrq, sinr: sinr });
		var col = M.qCol(M.qLevel(rsrp, rsrq, sinr));

		var rat = '--';
		if (c && !c.error) {
			var nr = c.nr && c.nr.band ? true : false;
			var lte = c.lte && c.lte.band ? true : false;
			if (nr && lte) rat = '5G NSA';
			else if (nr) rat = '5G SA';
			else if (lte) {
				var act = (c.operator && c.operator.act) || (c.reg && c.reg.act);
				rat = (act == 13) ? '5G NSA' : (act == 11 || act == 18 || act == 19) ? '5G' : (act == 7 || act == 10) ? '4G' : (act >= 2 && act <= 6) ? '3G' : '4G';
			} else if (c.cfun === 0 || c.cfun === 4) rat = _('Radio off');
		}
		if (c && c.error) { rat = _('No response'); col = M.qCol('poor'); }
		var ratEl = M.v('rat');
		ratEl.firstChild.nodeValue = rat;
		ratEl.style.color = col;
		/* The cellular readings come from a cache the backend refreshes in the background, never inside this call: when
		 * the modem is busy (a long AT command, a lock apply, a restart) they stay as they were, and say how old they are
		 * rather than pass for live ones. */
		var staleEl = M.v('stale');
		if (staleEl) {
			var age = Number(st.sig_age);
			var stale = st.info && !(c && c.error) && age > 15;
			staleEl.style.display = stale ? '' : 'none';
			staleEl.textContent = stale ? _('Modem busy: cellular readings from %s ago').format(M.ageText(age)) : '';
		}
		var bars = M.v('bars');
		if (bars) {
			var n = score == null ? 0 : Math.max(1, Math.round(score / 2));
			Array.prototype.forEach.call(bars.children, function(b, idx) { b.className = idx < n ? 'on' : ''; });
		}

		var oper = M.carrierName(c && c.operator);
		/* With COPS empty (the transition after a restart), the PLMN comes from the first 5-6 digits of the IMSI */
		if (oper === '--' && c && c.ident && c.ident.imsi) {
			var imsi = c.ident.imsi;
			var plmn5 = imsi.substring(0, 5), plmn6 = imsi.substring(0, 6);
			oper = M.PLMN_CN[plmn5] || M.PLMN_CN[plmn6] || plmn5;
		}
		M.set('op', score != null ? _('%s · Signal %s %s/10').format(oper, label, score.toFixed(1)) : _('%s · Signal %s').format(oper, label));

		var cl = [];
		if (c && c.nr && c.nr.band) cl.push('n' + c.nr.band + (c.nr.bw_mhz ? ' · ' + c.nr.bw_mhz + ' MHz' : '') + ' · PCI ' + c.nr.pci + ' · ARFCN ' + c.nr.arfcn);
		if (c && c.lte && c.lte.band) cl.push(_('Anchor B%s').format(c.lte.band) + ' · PCI ' + c.lte.pci + ' · EARFCN ' + c.lte.earfcn +
			(c.lte.sinr != null ? ' · SINR ' + c.lte.sinr.toFixed(1) + ' dB' : ''));
		M.v('cellline').innerHTML = cl.map(M.esc).join('<br>') || '<span style="color:var(--text-muted,var(--text-light,#777))">' + _('No serving cell') + '</span>';

		M.set('rsrp', '--');
		if (rsrp != null) M.v('rsrp').innerHTML = rsrp.toFixed(1) + '<small> dBm</small>';
		M.v('rsrp').style.color = col;
		M.v('metric-chips').innerHTML =
			'<span class="mud-q" style="background:color-mix(in oklab,' + col + ' 16%,transparent);color:' + col + '">' + label + '</span>' +
			(rsrq != null ? '<span class="mud-tag">RSRQ ' + rsrq.toFixed(1) + '</span>' : '') +
			(sinr != null ? '<span class="mud-tag">SINR ' + sinr.toFixed(1) + '</span>' : '');

		/* -- link & traffic */
		var nrk = (c && c.nr) || null;
		var radioMetrics = '';
		if (c && c.nr && c.nr.band) radioMetrics += radioMetricRows('5G', 'nr', c.nr);
		if (c && c.lte && c.lte.band) radioMetrics += radioMetricRows('4G', 'lte', c.lte);
		M.v('radio-metrics').innerHTML = radioMetrics || radioMetricRows('', 'nr', null);
		M.set('bw', nrk && nrk.bw_mhz ? nrk.bw_mhz + ' MHz' : (c && c.lte && c.lte.bw) || '--');
		var qos = c && c.qos;
		M.set('qci', qos && qos.qci != null ? qos.qci : '--');
		M.set('ambr', qos && qos.dl != null ? qos.dl + ' / ' + qos.ul + ' Mbps' : '--');

		/* the rates and totals are the fast lane's (rate); only while it has nothing (a panel whose ACL predates it),
		 * the snapshot's counters stand in, over its whole seconds */
		if (i.data_device) this.dataDev = i.data_device;
		var net = (i.net && (i.net.mobile || i.net.sipa_eth0)) || null;
		if (net && !this.fastNet) {
			if (this.lastNet && i.ts && this.lastNet.ts && i.ts > this.lastNet.ts && net.rx >= this.lastNet.rx && net.tx >= this.lastNet.tx)
				this.pushRate((net.rx - this.lastNet.rx) / (i.ts - this.lastNet.ts), (net.tx - this.lastNet.tx) / (i.ts - this.lastNet.ts));
			M.set('rx', M.fmtBytes(net.rx)); M.set('tx', M.fmtBytes(net.tx));
			this.lastNet = { ts: i.ts, rx: net.rx, tx: net.tx };
		}
		this.paintUsage(i.traffic || null);
		/* a newer release: the updater's own note (mu300-update notice, at boot and every 6 hours), a link to System > Update */
		M.v('update').style.display = i.update ? '' : 'none';
		if (i.update) M.set('update', _('Version %s is out: update').format(i.update));
		var w = i.wan || {};
		M.v('ip').innerHTML = M.esc(w.ip4 || '--') + (w.ip6 ? '<br>' + M.esc(w.ip6) : '');
		M.set('dns', w.dns || '--');
		M.set('apn', w.apn || '--');
		this.paintSim(i.sim);
		M.set('sess', w.uptime ? M.fmtUptime(w.uptime) : '--');
		var regmap = { 0: _('Not registered'), 1: _('Registered'), 2: _('Searching'), 3: _('Registration denied'), 4: _('Unknown'), 5: _('Registered (roaming)'), 7: _('Emergency only'), 8: _('Emergency only'), 10: _('Registered') };
		var reg = '--';
		if (c && c.reg) {
			reg = regmap[c.reg.stat] || _('Status %s').format(c.reg.stat);
			if (c.reg.tac) reg += ' · TAC ' + c.reg.tac;
			if (c.reg5g && c.reg5g.stat == 1) reg += ' · 5G ' + regmap[c.reg5g.stat];
		} else if (c && c.error) reg = M.errText(c);
		M.set('reg', reg);
		var ro = w.roaming || null;
		M.set('roam', !ro ? '--' : ro.held ? _('Off: data held back while roaming') : ro.allowed ? _('Allowed') : _('Off'));
		M.v('roam').style.color = ro && ro.held ? 'var(--warning,#f59e0b)' : '';

		var anchor = (c && c.lte && c.lte.band) ? c.lte : null;
		M.v('lteanchor').innerHTML = anchor ?
			'<div class="mud-r"><span class="mud-k">' + (c.nr && c.nr.band ? _('LTE anchor') : _('LTE link')) + '</span><span class="mud-v">B' + M.esc(anchor.band) +
			' · RSRP ' + (anchor.rsrp != null ? anchor.rsrp.toFixed(1) : '--') +
			(anchor.sinr != null ? ' · SINR ' + anchor.sinr.toFixed(1) : '') +
			(anchor.ca ? ' · ' + M.esc(anchor.ca) : '') + '</span></div>' : '';

		/* -- slow-tier sections */
		if (slowChanged) {
			this.repaintNeigh();
			M.set('carr', oper);
			M.set('plmn', (c && c.operator && c.operator.plmn) ||
				(c && c.ident && c.ident.imsi ? c.ident.imsi.substring(0, 5) : '--'));
			this.paintIdent(c);
		}

		/* -- Wi-Fi, LAN, device, SIM */
		var wf = i.wifi || {};
		M.set('ssid', wf.ssid || '--');
		M.set('chan', (wf.channel || '--') + (wf.band ? ' (' + wf.band + (wf.width ? ' · ' + wf.width : '') + ')' : ''));
		M.set('wenc', wf.enc || '--');
		M.set('whid', wf.hidden == 1 ? _('Hidden') : _('No'));
		M.set('wcountry', wf.country || '--');
		M.set('whostapd', wf.hostapd ? _('Running') : _('Not running'));
		M.set('wusb', (i.net && i.net.usb0 && i.net.usb0.up) ? _('Connected') : _('Disconnected'));
		M.set('conntrack', i.conns != null ? _('%s entries').format(i.conns) : '--');
		M.set('lanip', (i.lan && i.lan.ip) || '--');
		M.set('wcl', _('%s clients').format(wf.clients_n != null ? wf.clients_n : '--'));
		M.set('wleases', _('%s entries').format(i.lan ? i.lan.leases : '--'));
		M.v('clist').innerHTML = (wf.clients || []).map(function(cl) {
			var l = cl.signal != null ? (cl.signal >= -55 ? 'excellent' : cl.signal >= -67 ? 'good' : cl.signal >= -80 ? 'fair' : 'poor') : 'unknown';
			return '<div class="mud-cli"><div class="t"><b>' + M.esc(cl.host || cl.ip || cl.mac) + '</b>' +
				(cl.signal != null ? '<span style="color:' + M.qCol(l) + ';font-variant-numeric:tabular-nums">' + cl.signal + ' dBm</span>' : '') +
				'</div><div class="s">' + (cl.ip ? M.esc(cl.ip) + ' · ' : '') + M.esc(cl.mac) +
				((cl.tx || cl.rx) ? ' · ↑' + M.esc(cl.tx || '--') + ' ↓' + M.esc(cl.rx || '--') : '') +
				(cl.conn ? ' · ' + M.esc(cl.conn) : '') + '</div></div>';
		}).join('') || '';
		/* Recent DHCP leases: shows who has been here even when nobody is connected */
		M.v('leases').innerHTML = (i.lan && i.lan.list && i.lan.list.length)
			? '<div class="mud-note" style="margin:0 0 4px">' + _('Recent DHCP leases') + '</div>' +
				'<table class="mud-table"><tbody>' +
				i.lan.list.slice(0, 8).map(function(l) {
					return '<tr><td>' + M.esc(l.host || l.ip || '?') + '</td><td>' + M.esc(l.ip || '') + '</td>' +
						'<td style="color:var(--text-subtle,var(--text-light,#999))">' + M.esc(l.mac) + '</td>' +
						'<td>' + (l.left >= 3600 ? _('%d h').format(Math.round(l.left / 3600)) : _('%d min').format(Math.max(0, Math.round(l.left / 60)))) + '</td></tr>';
				}).join('') + '</tbody></table>'
			: '';

		var t = i.temps || {};
		M.v('temps').innerHTML = [ [ 'SoC', t.soc ], [ 'CPU', t.cpu ], [ _('Modem'), t.modem ], [ _('Board'), t.board ] ]
			.filter(function(x) { return x[1] != null; })
			.map(function(x) {
				var lab = x[1] >= 75 ? 'poor' : x[1] >= 60 ? 'fair' : 'good';
				return '<span style="color:' + M.qCol(lab) + '">' + x[0] + ' ' + x[1] + '°C</span>';
			}).join('') || '<span style="color:var(--text-muted,var(--text-light,#777))">' + _('No temperature readings') + '</span>';

		if (i.cpu && this.lastCpu && i.cpu.total != null && this.lastCpu.total != null) {
			var dt2 = i.cpu.total - this.lastCpu.total, di = i.cpu.idle - this.lastCpu.idle;
			var pct = dt2 > 0 ? Math.max(0, Math.min(100, Math.round((dt2 - di) * 100 / dt2))) : null;
			if (pct != null) {
				M.set('cpu', pct + '%');
				M.v('cpu-bar').style.width = pct + '%';
			}
		}
		this.lastCpu = i.cpu || null;
		/* One cur/max frequency bar per cluster */
		M.v('freqs').innerHTML = ((i.cpu && i.cpu.freqs) || []).map(function(f, n) {
			if (f.cur == null || f.max == null || !f.max) return '';
			var w = Math.max(2, Math.round(f.cur * 100 / f.max));
			return '<div class="mud-freq"><span class="mud-k">' + _('Cluster %d').format(n) + '</span>' +
				'<div class="mud-meter" style="flex:1;margin:4px 8px 0"><i style="width:' + w + '%;background:var(--brand,var(--primary,#3b82f6))"></i></div>' +
				'<span class="mud-v" style="flex:0 0 auto">' + (f.cur / 1000).toFixed(0) + ' <span style="opacity:.55">/ ' + (f.max / 1000).toFixed(0) + ' MHz</span></span></div>';
		}).join('');

		if (i.mem && i.mem.total_kb) {
			var used = i.mem.total_kb - i.mem.avail_kb, pct = Math.round(used * 100 / i.mem.total_kb);
			M.set('ram', pct + '%'); M.v('ram-bar').style.width = pct + '%';
			M.set('ram-sub', _('Total %s · free %s').format(M.fmtBytes(i.mem.total_kb * 1024), M.fmtBytes(i.mem.avail_kb * 1024)));
		}
		if (i.storage && i.storage.total_kb) {
			var pct2 = Math.round(i.storage.used_kb * 100 / i.storage.total_kb);
			M.set('disk', pct2 + '%'); M.v('disk-bar').style.width = pct2 + '%';
		}
		/* Battery (U30 Air): capacity, then what it is doing and how many watts, the
		 * voltage, and the USB input where the charger reports it. No battery (F50):
		 * no tile. */
		var p = i.power || {}, bk = M.v('batt-kpi');
		if (bk) bk.style.display = p.present ? '' : 'none';
		if (p.present) {
			/* direction from status; for Unknown and the like from the sign of the current (positive into the
			 * battery), with the gauge's own 20 mA dead band. The 5.4 SQC charger says Unknown once full. */
			var pst = p.status, pcur = p.ua || 0, parts = [ _('Battery') ];
			var pdir = pst === 'Charging' ? 1 : pst === 'Discharging' ? -1 :
				(pst === 'Full' || pst === 'Not charging') ? 0 : (pcur >= 20000 ? 1 : pcur <= -20000 ? -1 : 0);
			var pw = p.w != null ? Number(p.w).toFixed(1) : null;
			if (pdir > 0) parts.push(pw != null ? _('charging %s W').format(pw) : _('charging'));
			else if (pdir < 0) parts.push(pw != null ? _('drawing %s W').format(pw) : p.usb ? _('not charging') : _('on battery'));
			else if (pst === 'Full' || (p.usb && p.capacity >= 100)) parts.push(_('full'));
			else if (pst === 'Not charging') parts.push(_('not charging'));
			if (p.volt != null && p.capacity != null) parts.push(p.volt + ' V');
			if (p.usb) parts.push(p.in_w != null ? _('USB in %s W').format(Number(p.in_w).toFixed(1)) +
				(p.in_volt != null ? ' (' + p.in_volt + ' V)' : '') : 'USB');
			M.set('batt', p.capacity != null ? p.capacity + '%' : (p.volt != null ? p.volt + ' V' : '--'));
			M.set('batt-l', parts.join(' · '));
		}
		/* the capacity as a bar, as CPU, memory and storage have (kanoqwq's edcdeb27) */
		var bpct = p.present && typeof p.capacity === 'number' && isFinite(p.capacity) ? Math.max(0, Math.min(100, p.capacity)) : null;
		if (M.v('batt-meter')) {
			M.v('batt-meter').hidden = bpct == null;
			M.v('batt-bar').style.width = (bpct == null ? 0 : bpct) + '%';
		}
		M.set('model', i.model || '--');
		M.set('fwos', i.fw || '--');
		M.set('modem', (i.modem && i.modem.alive ? _('Online') : _('No response')) + (i.modem && i.modem.atd ? '' : ' · ' + _('AT adapter unavailable')));
		var androidBtn = M.v('btn-android');
		if (androidBtn) androidBtn.style.display = i.capabilities && i.capabilities.dualboot ? '' : 'none';
		var lockBtn = M.v('btn-bootlock');
		if (lockBtn) {
			lockBtn.style.display = i.capabilities && i.capabilities.dualboot ? '' : 'none';
			var lk = !!(i.capabilities && i.capabilities.bootlock);
			lockBtn.className = 'mud-btn' + (lk ? ' on' : '');
			lockBtn.textContent = lk ? _('Linux locked') : _('Lock Linux');
		}

		var b;
		b = M.v('btn-data'); b.className = 'mud-btn' + (w.up ? ' on' : ''); b.textContent = _('Data connection');
		b = M.v('btn-radio'); b.className = 'mud-btn' + (c && c.cfun === 1 ? ' on' : ''); b.textContent = _('Cellular radio');
		b = M.v('btn-roam'); b.className = 'mud-btn' + (!w.roaming || w.roaming.allowed ? ' on' : ''); b.textContent = _('Data roaming');
		b = M.v('btn-wifi'); b.className = 'mud-btn' + (wf.up ? ' on' : ''); b.textContent = _('Wi-Fi hotspot');
	}
});
