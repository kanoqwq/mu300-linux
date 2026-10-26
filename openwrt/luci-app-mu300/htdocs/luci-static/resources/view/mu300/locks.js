'use strict';
'require view';
'require poll';
'require mu300.common as M';

/* Network locks -- mode / bands / cell / EN-DC, all through ubus mu300dash lock_set -> the backend
 * mu300-dash-lock (encoding after the ufi_tools reference implementation); applying restarts the radio stack
 * (SFUN) and saves the setting, and at boot the plugin's own procd service replays it once the AT adapter is
 * ready, without the platform's dial-up scripts.
 *
 * The serving-cell hero takes its own live AT snapshot every 2 seconds and never reads the cellular cache; slow
 * metadata such as the carrier and the neighbors comes from status once, when the page opens.
 * Every neighbor row has a Lock button, shared with the dashboard through M.neighborRows. */

/* auto152: the automatic mode 152 (no 2G) instead of 134, from kanoqwq's fork (FINDINGS 198) */
var MODES = [ [ 'auto', _('Automatic') ], [ 'auto152', _('Automatic without 2G (152)') ], [ '4g', _('4G only') ], [ 'sa', '5G SA' ], [ 'nsa', '5G NSA' ] ];
var NR_CAND = [ 1, 5, 6, 8, 28, 41, 78 ];   /* measured with SP5GCMDS on this device; LTE has no capability query, so a fixed table */
var LTE_CAND = [ 1, 3, 5, 8, 34, 38, 39, 40, 41 ];

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<!-- The same hero structure as the dashboard: mud-hero-l/mud-hero-r let the phone media query apply the same way
     (the information on top, the RSRP row left-aligned, the chips right-aligned); on a desktop the RSRP block stays right-aligned -->
<div class="mud-card mud-hero">
  <div class="mud-hero-l">
    <div style="font-size:.78rem;color:var(--text-muted,var(--text-light,#777))">${_('Serving network')}</div>
    <div style="font-size:1.25rem;font-weight:700;margin-top:2px" id="mud-srv-rat">--</div>
    <div class="mud-cellline" id="mud-srv"></div>
  </div>
  <div class="mud-hero-r">
    <div class="mud-rsrp" id="mud-srv-rsrp" style="font-size:1.9rem">--</div>
    <div class="mud-chips" id="mud-srv-chips"></div>
  </div>
</div>

<div class="mud-sec">
  <h3>${_('Network mode · EN-DC')}</h3>
  <div class="mud-ctl" id="mud-lock-modes" style="grid-template-columns:repeat(4,1fr);max-width:520px"></div>
  <div class="mud-ctl" style="margin-top:7px;grid-template-columns:1fr 1fr;max-width:340px">
    <button class="mud-btn" id="mud-lock-endc">EN-DC</button>
    <button class="mud-btn" id="mud-lock-refresh">${_('Refresh lock status')}</button>
  </div>
  <div class="mud-ctl" style="margin-top:7px;max-width:340px">
    <button class="mud-btn" id="mud-lock-auto-apply">${_('Apply at startup')}</button>
  </div>
  <div class="mud-note">${_('Turning this off only stops replay at the next boot; saved network mode, EN-DC, band and cell settings remain.')}</div>
</div>

<div class="mud-sec">
  <h3>${_('Band locking')}</h3>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">${_('NR bands')}</span><span class="mud-v" id="mud-lock-nrline">--</span></div>
      </div>
      <div class="mud-chiprow" id="mud-lock-nr"></div>
    </div>
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">${_('LTE bands')}</span><span class="mud-v" id="mud-lock-lteline">--</span></div>
      </div>
      <div class="mud-chiprow" id="mud-lock-lte"></div>
    </div>
  </div>
  <div class="mud-ctl" style="margin-top:8px;max-width:360px">
    <button class="mud-btn" id="mud-lock-nr-apply">${_('Apply NR bands')}</button>
    <button class="mud-btn" id="mud-lock-lte-apply">${_('Apply LTE bands')}</button>
  </div>
</div>

<div class="mud-sec">
  <h3>${_('Neighbor cells and cell locking')}</h3>
  <div id="mud-lockedcells"></div>
  <div class="mud-ctl" style="max-width:400px;margin-bottom:8px">
    <button class="mud-btn" id="mud-lock-cell">${_('Lock current serving cell')}</button>
    <button class="mud-btn warn" id="mud-lock-cell-off">${_('Unlock cell')}</button>
  </div>
  <details class="mud-manual-cell">
    <summary>${_('Enter a cell')}</summary>
    <form id="mud-lock-manual-form" novalidate>
      <div class="mud-manual-cell-fields">
        <label><span>${_('Type')}</span><select id="mud-lock-manual-rat"><option value="lte">LTE (4G)</option><option value="nr">NR (5G)</option></select></label>
        <label><span>${_('Channel number (EARFCN / NR-ARFCN)')}</span><input id="mud-lock-manual-frequency" type="text" inputmode="numeric" autocomplete="off" required></label>
        <label><span>PCI</span><input id="mud-lock-manual-pci" type="text" inputmode="numeric" autocomplete="off" required></label>
        <button type="submit" class="mud-btn" id="mud-lock-manual-apply">${_('Lock this cell')}</button>
      </div>
      <div class="mud-note">${_('LTE: EARFCN 0–262143, PCI 0–503; NR: NR-ARFCN 1–3279165, PCI 0–1007. A channel number, not a frequency in MHz; the cell need not be in the neighbor list.')}</div>
    </form>
  </details>
  <div class="mud-scroll">
  <table class="mud-table"><thead><tr><th>${_('RAT/Band')}</th><th>PCI</th><th>${_('Frequency')}</th><th>RSRP</th><th>RSRQ</th><th>SINR</th><th></th></tr></thead>
  <tbody id="mud-neigh"><tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">--</td></tr></tbody></table>
  </div>
  <div class="mud-note">${_('Applying restarts the radio stack (SFUN) and briefly interrupts cellular service. Settings are saved and replayed by the plugin after AT is ready when Apply at startup is enabled. A platform pre-radio hook can replay without a restart. Apply with no bands selected to restore automatic mode.')}</div>
</div>

<div class="mud-sec">
  <h3>${_('Back to automatic')}</h3>
  <div class="mud-note">${_('The network mode and band locks are stored in the modem and stay in force in Android too. If you are not sure, reset them before switching to Android.')}</div>
  <div class="mud-ctl" style="margin-top:7px;max-width:340px">
    <button class="mud-btn warn" id="mud-lock-reset">${_('Reset all to automatic')}</button>
  </div>
</div>`;
		this.wire(root);
		return root;
	},

	wire: function(root) {
		var self = this;
		this.lockSel = { nr: {}, lte: {} };
		this.lockCand = { nr: NR_CAND, lte: LTE_CAND };
		this.Q = function(id) { return root.querySelector('#mud-' + id); };

		/* A themed confirmation box instead of the browser's confirm; the action runs only once confirmed */
		var apply = function(kind, val, what, opts) {
			opts = opts || {};
			M.confirmBox(_('Apply “%s”?').format(what),
				opts.noSfun ? '' : _('The radio stack will restart (SFUN); cellular service will stop for about 30 seconds.'),
				{ danger: !opts.noSfun, okText: _('Apply') })
				.then(function(go) { if (go) applyNow(kind, val, what, opts); });
		};
		var applyNow = function(kind, val, what, opts) {
			var btn = opts.btn, prevEnd = ((self.lastLock || {}).reset || {}).end || 0;
			if (opts.optimistic) opts.optimistic();   /* the button shows the target state at once; the readback corrects it */
			M.busy(btn, true);   /* after optimistic: it may reset the button's className */
			self.note((opts.noSfun ? _('Applying %s in the background…')
				: _('Applying %s in the background… (SFUN restart and re-registration, about 30 seconds)')).format(what), 'busy');
			L.resolveDefault(M.callLockSet(kind, val)).then(function(r) {
				r = r || {};
				if (!r.ok) {
					M.busy(btn, false);
					self.note(_('Failed: %s').format(M.errText(r)), 'error');
					return;
				}
				if (kind == 'endc') {
					self.note(r.queued
						? _('Queued: another lock is being applied during SFUN restart; this will take effect afterward')
						: _('Confirming the EN-DC status…'), r.queued ? 'info' : 'busy');
					return self.confirmToggle(kind, val, btn);
				}
				if (kind == 'auto_apply') {
					self.note(_('Confirming apply at startup…'), 'busy');
					return self.confirmToggle(kind, val, btn);
				}
				if (kind == 'reset') {
					self.note(_('Started in background: %s (SFUN restart, about 30 seconds); reading the status back…').format(r.op || kind), 'busy');
					return self.readbackReset(prevEnd, btn);
				}
				self.note(_('Started in background: %s (SFUN restart, about 30 seconds); reading the status back…').format(r.op || kind), 'busy');
				self.readback(Date.now(), btn);
			}, function() { M.busy(btn, false); self.note(_('Request failed'), 'error'); });
		};

		this.Q('lock-modes').innerHTML = MODES.map(function(m) {
			return '<button class="mud-btn" data-mode="' + m[0] + '">' + M.esc(m[1]) + '</button>';
		}).join('');
		root.querySelectorAll('#mud-lock-modes .mud-btn').forEach(function(b) {
			b.onclick = function() {
				var m = b.getAttribute('data-mode');
				if (m === (self.lastLock && self.lastLock.mode && self.lastLock.mode.label)) return;
				var btn = b;
				apply('mode', m, _('Network mode: %s').format(b.textContent), { btn: btn, optimistic: function() {
					Array.prototype.forEach.call(self.Q('lock-modes').querySelectorAll('.mud-btn'), function(x) {
						x.className = x === btn ? 'mud-btn on' : 'mud-btn';
					});
				} });
			};
		});
		this.Q('lock-endc').onclick = function() {
			var on = self.lastLock && self.lastLock.endc === '1';
			var btn = this;
			apply('endc', on ? 'off' : 'on', on ? _('Disable EN-DC (NSA anchor)') : _('Enable EN-DC (NSA anchor)'),
				{ btn: btn, noSfun: true, optimistic: function() {
					btn.className = 'mud-btn' + (on ? '' : ' on');
					btn.textContent = on ? 'EN-DC' : 'EN-DC ✓';
				} });
		};
		this.Q('lock-auto-apply').onclick = function() {
			var on = !self.lastLock || self.lastLock.auto_apply !== 0;
			var btn = this;
			apply('auto_apply', on ? 'off' : 'on', on ? _('Disable apply at startup') : _('Enable apply at startup'),
				{ btn: btn, noSfun: true, optimistic: function() {
					btn.className = 'mud-btn' + (on ? '' : ' on');
					btn.textContent = on ? _('Apply at startup') : _('Apply at startup') + ' ✓';
				} });
		};
		this.Q('lock-refresh').onclick = function() {
			var btn = this;
			M.busy(btn, true);
			self.note(_('Reading the modem directly (a few seconds at most)…'), 'busy');
			L.resolveDefault(M.callLockFresh('1')).then(function(l) {
				M.busy(btn, false);
				/* a busy modem: the reading goes on in the background, the last state stays on the page */
				if (!l || !l.mode) { self.note(l && l.error ? M.errText(l) : _('Refresh failed'), 'error'); return; }
				self.lastLock = l;
				self.paint();
				self.note(_('Refreshed'), 'success');
				var nb = self.Q('neigh');
				if (nb && self.lastCell) nb.innerHTML = M.neighborRows(self.lastCell, self.lastLock.cells || []);
			}, function() { M.busy(btn, false); self.note(_('Refresh failed'), 'error'); });
			self.loadServing();
		};

		this.chipRow = function(el, rat, cand) {
			el.innerHTML = cand.map(function(b) {
				return '<span class="mud-chip" data-rat="' + rat + '" data-b="' + b + '">' + (rat === 'nr' ? 'n' : 'B') + b + '</span>';
			}).join('');
			/* restore the selection after the chips are rebuilt */
			Array.prototype.forEach.call(el.children, function(ch) {
				var b = ch.getAttribute('data-b');
				if (self.lockSel[rat][b]) ch.className = 'mud-chip on';
			});
		};
		this.chipRow(this.Q('lock-nr'), 'nr', NR_CAND);
		this.chipRow(this.Q('lock-lte'), 'lte', LTE_CAND);
		/* event delegation on the container: clicks still work after paint() rebuilds the chips from the module's capabilities */
		[ 'nr', 'lte' ].forEach(function(rat) {
			self.Q('lock-' + rat).addEventListener('click', function(ev) {
				var ch = ev.target;
				if (!ch.getAttribute || !ch.getAttribute('data-b')) return;
				var b = ch.getAttribute('data-b');
				self.lockSel[rat][b] = !self.lockSel[rat][b];
				ch.className = self.lockSel[rat][b] ? 'mud-chip on' : 'mud-chip';
			});
		});
		var selBands = function(rat) {
			return Object.keys(self.lockSel[rat]).filter(function(b) { return self.lockSel[rat][b]; })
				.map(Number).sort(function(a, b) { return a - b; });
		};
		this.Q('lock-nr-apply').onclick = function() {
			var sel = selBands('nr');
			if (!sel.length) return apply('nr', '', _('NR bands: back to automatic'), { btn: this });
			apply('nr', sel.join(','), _('NR band lock: %s').format('n' + sel.join(' n')), { btn: this });
		};
		this.Q('lock-lte-apply').onclick = function() {
			var sel = selBands('lte');
			if (!sel.length) return apply('lte', '', _('LTE bands: back to automatic'), { btn: this });
			apply('lte', sel.join(','), _('LTE band lock: %s').format('B' + sel.join(' B')), { btn: this });
		};
		/* every lock back to automatic in one go (backend: unisoc-modem/lock reset): one SFUN restart, and the saved
		 * settings are cleared so the boot replay does not put them back */
		this.Q('lock-reset').onclick = function() {
			var btn = this;
			M.confirmBox(_('Reset all to automatic'),
				[ _('Network mode automatic, EN-DC on, all LTE and NR bands, no cell lock. The saved lock settings are cleared, so nothing is replayed at startup.'),
				  _('The radio stack will restart (SFUN); cellular service will stop for about 30 seconds.') ].join('\n'),
				{ danger: true, okText: _('Reset') })
				.then(function(go) { if (go) applyNow('reset', 'auto', _('Reset all to automatic'), { btn: btn }); });
		};
		this.Q('lock-cell').onclick = function() { apply('cell', 'auto', _('Lock current serving cell'), { btn: this }); };
		this.Q('lock-cell-off').onclick = function() { apply('cell', 'off', _('Unlock cell'), { btn: this }); };

		/* the unlock buttons of the locked-cell table (delegated) */
		this.Q('lockedcells').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-unlock')) return;
			var rat = btn.getAttribute('data-unlock');
			M.confirmBox(_('Remove the %s cell lock').format(rat.toUpperCase()),
				_('The radio stack will restart (SFUN), taking about 30 seconds.'), { danger: true })
				.then(function(go) {
				if (!go) return;
				M.busy(btn, true);
			self.note(_('Removing the %s cell lock…').format(rat.toUpperCase()), 'busy');
			L.resolveDefault(M.callLockSet('cell', 'off-' + rat)).then(function(r) {
				r = r || {};
				M.busy(btn, false);
				if (!r.ok) { self.note(_('Unlock failed: %s').format(M.errText(r)), 'error'); return; }
					self.note(_('Unlock started in background (SFUN restart, about 30 seconds); reading the status back…'), 'busy');
					self.readback(Date.now());
				});
			});
		});

		/* Lock a cell: from a neighbor row (event delegation, as on the dashboard) or entered by hand */
		var lockCell = function(key, btn) {
			return M.confirmBox(_('Lock cell %s?').format(key.replace(':', ' ')),
				_('The radio stack will restart (SFUN); cellular service will stop for about 30 seconds.'), { danger: true })
				.then(function(go) {
				if (!go) return;
				M.busy(btn, true);
			self.note(_('Locking %s in the background…').format(key), 'busy');
			L.resolveDefault(M.callLockSet('cell', key)).then(function(r) {
				r = r || {};
				M.busy(btn, false);
				if (!r.ok) { self.note(_('Lock failed: %s').format(M.errText(r)), 'error'); return; }
					self.note(_('Locked %s in the background (SFUN restart, about 30 seconds); reading the status back…').format(key), 'busy');
					self.readback(Date.now());
				});
			});
		};
		this.Q('neigh').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-lock')) return;
			lockCell(btn.getAttribute('data-lock'), btn);
		});
		/* By hand (kanoqwq's b8d4f222): a cell that is not in the neighbor list. The backend takes the same
		 * RAT:ARFCN,PCI as a row's button; the ranges are the standards' (36.101 EARFCN, 38.101 NR-ARFCN, PCI 503/1007). */
		this.Q('lock-manual-form').onsubmit = function(ev) {
			ev.preventDefault();
			var rat = self.Q('lock-manual-rat').value, nr = rat === 'nr';
			var freq = self.Q('lock-manual-frequency').value.trim(), pci = self.Q('lock-manual-pci').value.trim();
			if ((rat !== 'lte' && !nr) || !/^[0-9]{1,7}$/.test(freq) || !/^[0-9]{1,4}$/.test(pci) ||
				Number(freq) < (nr ? 1 : 0) || Number(freq) > (nr ? 3279165 : 262143) || Number(pci) > (nr ? 1007 : 503)) {
				self.note(_('Enter a channel number and a PCI in the ranges below'), 'error');
				return;
			}
			lockCell(rat + ':' + Number(freq) + ',' + Number(pci), self.Q('lock-manual-apply'));
		};

		this.refresh();
		this.loadServingMeta();
		this.loadServing();
		poll.add(function() { return self.loadServing(); }, 2);
	},

	/* One kind of feedback: every note is a toast at the top (M.toast), one in progress spins (busy); only one at a
	 * time (a new note replaces the old one, so progress -> result updates in place instead of piling up). */
	note: function(txt, type) {
		if (this._toast) this._toast.close();
		this._toast = M.toast(txt, { type: type || 'info' });
	},

	/* The carrier and the neighbors are slow metadata; the live signal is never read from here. */
	loadServingMeta: function() {
		var self = this;
		return L.resolveDefault(M.callStatus()).then(function(st) {
			self.servingMeta = (st || {}).cell || {};
		});
	},

	/* The backend takes a new AT snapshot every time; the last signal/cell cache is never accepted. */
	/* Polled every 2 s: after a failed read (the modem busy or silent: the backend gives up within 14 s) the next ones
	 * are skipped, more of them after each failure in a row (M.backoff), so a stuck modem is not asked twice as often. */
	loadServing: function() {
		var self = this;
		if (this._skip > 0) { this._skip--; return Promise.resolve(); }
		return Promise.resolve(M.callSignal()).catch(function() { return null; }).then(function(live) {
			self._fails = live && !live.error ? 0 : (self._fails || 0) + 1;
			self._skip = Math.round(M.backoff(1, self._fails)) - 1;
			if (!live && self.lastCell && !self.lastCell.error) return;   // a lost request: keep what is shown
			live = live || {};
			var c = {}, meta = self.servingMeta || {};
			Object.keys(meta).forEach(function(k) { c[k] = meta[k]; });
			Object.keys(live).forEach(function(k) { c[k] = live[k]; });
			self.paintServing(c);
		});
	},

	paintServing: function(c) {
		var e = this.Q('srv'); if (!e) return;
		this.lastCell = c;
		if (!c || c.error) {
			this.Q('srv-rat').textContent = c && c.error ? M.errText(c) : _('No serving-network data');
			e.innerHTML = '';
			return;
		}
		var ratTxt = (c.nr && c.nr.band) ? ((c.lte && c.lte.band) ? '5G NSA' : '5G SA') : 'LTE';
		var sig = c.sig || {}, level = M.qLevel(sig.rsrp, sig.rsrq, sig.sinr);
		var operName = M.carrierName(c.operator);
			if (operName === '--' && c.ident && c.ident.imsi)
				operName = M.PLMN_CN[c.ident.imsi.substring(0, 5)] || c.ident.imsi.substring(0, 5);
			this.Q('srv-rat').textContent = ratTxt + ' · ' + operName;
		this.Q('srv-rat').style.color = M.qCol(level);
		var rsrpEl = this.Q('srv-rsrp');
		if (sig.rsrp != null) { rsrpEl.innerHTML = sig.rsrp.toFixed(1) + '<small> dBm</small>'; rsrpEl.style.color = M.qCol(level); }
		this.Q('srv-chips').innerHTML =
			(sig.rsrq != null ? '<span class="mud-tag">RSRQ ' + sig.rsrq.toFixed(1) + '</span>' : '') +
			(sig.sinr != null ? '<span class="mud-tag">SINR ' + sig.sinr.toFixed(1) + '</span>' : '');
		var rows = [];
		if (c.nr && c.nr.band) rows.push([ _('NR serving cell'), 'n' + c.nr.band + ' · PCI ' + c.nr.pci + ' · ARFCN ' + c.nr.arfcn + (c.nr.bw_mhz ? ' · ' + c.nr.bw_mhz + ' MHz' : '') ]);
		if (c.lte && c.lte.band) rows.push([ _('LTE anchor'), 'B' + c.lte.band + ' · PCI ' + c.lte.pci + ' · EARFCN ' + c.lte.earfcn ]);
		e.innerHTML = rows.map(function(x) {
			return '<div class="mud-srvline"><span class="k">' + M.esc(x[0]) + '</span><span class="v">' + M.esc(x[1]) + '</span></div>';
		}).join('');
		var nb = this.Q('neigh');
		if (nb) nb.innerHTML = M.neighborRows(c, (this.lastLock || {}).cells || []);
	},

	/* Readback after applying: poll the lock_get cache until its ts is after the click on Apply (SFUN restart + a fresh
	 * read take up to a minute or two; meanwhile the backend never overwrites the cache with an empty reading), and
	 * redraw the highlights only from the new state */
	readback: function(t0ms, btn) {
		var self = this, tries = 0, t0 = t0ms || Date.now();
		var step = function() {
			L.resolveDefault(M.callLockGet()).then(function(l) {
				l = l || {};
				if ((l.ts && l.ts * 1000 > t0) || ++tries > 40) {
					M.busy(btn, false);
					self.lastLock = l; self.paint(); self.paintServing(self.lastCell);
					self.note((l.ts && l.ts * 1000 > t0) ? _('Status confirmed') : _('Readback timed out; select “Refresh lock status”'),
						(l.ts && l.ts * 1000 > t0) ? 'success' : 'error');
				} else setTimeout(step, 2500);
			});
		};
		step();
	},
	/* Read-back after a reset: the backend's reset is verified (every write's answer checked, the five settings read
	 * back before and after the stack restart, the saved locks deleted only then) and runs detached; its result
	 * comes with lock_get as "reset" ({state: ok|failed|interrupted|running, end, error, step, cmd}). A result is
	 * new when its end is after the one shown before the click (the device's clock on both sides, never the
	 * browser's); a failed reset says why and that the saved locks were kept. Up to 150 s: the restart waits for
	 * the radio lock, and the read-back after it retries for half a minute. */
	readbackReset: function(prevEnd, btn) {
		var self = this, tries = 0;
		var step = function() {
			L.resolveDefault(M.callLockGet()).then(function(l) {
				l = l || {};
				var res = l.reset || {};
				var done = res.state && res.state !== 'running' && (res.end || 0) > prevEnd;
				if (done || ++tries > 60) {
					M.busy(btn, false);
					self.lastLock = l; self.paint(); self.paintServing(self.lastCell);
					if (!done)
						self.note(_('Readback timed out; select “Refresh lock status”'), 'error');
					else if (res.state === 'ok')
						self.note(_('Reset verified: network mode automatic, EN-DC on, all bands, no cell lock; the saved lock settings are cleared'), 'success');
					else
						self.note(_('Reset failed: %s. The saved lock settings were kept.').format(
							M.errText({ error: res.error, detail: res.cmd || res.step })), 'error');
				} else setTimeout(step, 2500);
			});
		};
		setTimeout(step, 1500);
	},
	refreshSoon: function() {
		var self = this;
		setTimeout(function() { self.refresh(); }, 1500);
	},

	/* Confirming readback of the instant switches (EN-DC / apply at startup): the backend writes them into the lock
	 * cache at once, so the first round (1.5 s) normally matches; if not (say, queued behind an SFUN), keep the
	 * optimistic state and wait, rolling back only after 15 seconds, so the button does not flash back as if the
	 * click was lost. */
	confirmToggle: function(kind, val, btn) {
		var self = this, tries = 0;
		var matches = function(l) {
			if (kind == 'endc') return (l.endc === '1') == (val == 'on');
			if (kind == 'auto_apply') return (l.auto_apply !== 0) == (val == 'on');
			return true;
		};
		var step = function() {
			L.resolveDefault(M.callLockGet()).then(function(l) {
				l = l || {};
				if (matches(l) || ++tries > 10) {
					M.busy(btn, false);
					self.lastLock = l;
					self.paint();
					if (matches(l))
						self.note(kind == 'endc' ? _('Applied (EN-DC does not require a radio-stack restart)')
							: val == 'on' ? _('Apply at startup is on') : _('Apply at startup is off'), 'success');
					else
						self.note(kind == 'endc' ? _('EN-DC status readback timed out; use “Refresh lock status” to confirm')
							: _('Apply-at-startup status readback timed out; use “Refresh lock status” to confirm'), 'error');
				} else setTimeout(step, 1500);
			});
		};
		setTimeout(step, 1500);
	},

	refresh: function() {
		var self = this;
		L.resolveDefault(M.callLockGet()).then(function(l) {
			l = l || {};
			/* An empty result is mostly a transient rpcd failure under load (measured: the same request again works): retry
			 * lightly. "busy": no reading yet (after a reboot), one runs in the background - ask again a little later. */
			if (!l.mode && (!l.error || l.busy) && (self._retry = (self._retry || 0) + 1) <= (l.busy ? 8 : 3))
				return setTimeout(function() { self.refresh(); }, l.busy ? 4000 : 1800);
			self._retry = 0;
			self.lastLock = l;
			self.paint();
			/* the lock state is back: refresh the neighbor table's Locked marks too */
			var nb = self.Q('neigh');
			if (nb && self.lastCell) nb.innerHTML = M.neighborRows(self.lastCell, self.lastLock.cells || []);
		});
	},

	paint: function() {
		var l = this.lastLock || {};
		var self = this;
		var setR = function(id, txt) { var e = self.Q(id); if (e) e.textContent = (txt == null || txt === '') ? '--' : txt; };
		if (l.error) { setR('lock-nrline', M.errText(l)); setR('lock-lteline', ''); return; }
		var cur = this.Q('lock-modes');
		if (cur) Array.prototype.forEach.call(cur.querySelectorAll('.mud-btn'), function(b) {
			b.className = (b.getAttribute('data-mode') === (l.mode && l.mode.label)) ? 'mud-btn on' : 'mud-btn';
		});
		var eb = this.Q('lock-endc');
		if (eb) { eb.className = 'mud-btn' + (l.endc === '1' ? ' on' : ''); eb.textContent = l.endc === '1' ? 'EN-DC' : 'EN-DC'; }
		var ab = this.Q('lock-auto-apply'), autoApply = l.auto_apply !== 0;
		if (ab) { ab.className = 'mud-btn' + (autoApply ? ' on' : ''); ab.textContent = autoApply ? _('Apply at startup') + ' ✓' : _('Apply at startup'); }

		/* the supported bands come from the module's capabilities (SPLBAND=4 / =0 decoded) first, the static table only without them */
		var caps = l.caps || {};
		/* a table of its own for the locked cells: every cell listed, one unlock button per RAT */
		var lc = self.Q('lockedcells');
		if (lc) {
			var cells = l.cells || [];
			lc.innerHTML = cells.length
				? '<div class="mud-note" style="margin:0 0 4px">' + M.esc(_('Locked cells')) + '</div><table class="mud-table"><tbody>' +
					cells.map(function(k) {
						var parts = k.split(':'), rat = parts[0], fp = (parts[1] || '').split(',');
						return '<tr><td>' + (rat == 'nr' ? 'NR' : 'LTE') + '</td><td>' + M.esc(fp[0] || '?') + '</td>' +
							'<td>' + M.esc(fp[1] || '?') + '</td>' +
							'<td><button class="mud-lockbtn" data-unlock="' + rat + '">' + M.esc(_('Unlock %s').format(rat == 'nr' ? 'NR' : 'LTE')) + '</button></td></tr>';
					}).join('') + '</tbody></table>'
				: '';
		}
		[ 'nr', 'lte' ].forEach(function(rat) {
			var capList = (caps[rat] || '').split(',').map(Number).filter(function(b) { return b > 0; });
			if (capList.length) {
				capList.sort(function(a, b) { return a - b; });
				self.lockCand[rat] = capList;
				var box = self.Q('lock-' + rat);
				if (box) self.chipRow(box, rat, capList);
			}
			var locked = (l[rat] && l[rat].locked) || '';
			var arr = locked ? locked.split(',').map(Number) : [];
			var isAuto = arr.length === 0 || arr.length >= self.lockCand[rat].length;
			setR('lock-' + rat + 'line', isAuto
				? _('Automatic (%d supported)').format(self.lockCand[rat].length)
				: _('%d locked: %s').format(arr.length, (rat === 'nr' ? 'n' : 'B') + arr.join(' ' + (rat === 'nr' ? 'n' : 'B'))));
			var box = self.Q('lock-' + rat);
			if (box) Array.prototype.forEach.call(box.children, function(ch) {
				var b = ch.getAttribute('data-b');
				var on = !isAuto && arr.indexOf(Number(b)) >= 0;
				self.lockSel[rat][b] = on;
				ch.className = on ? 'mud-chip on' : 'mud-chip';
			});
		});
	}
});
