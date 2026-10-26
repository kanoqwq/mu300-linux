'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > CPU: the CPU performance profile. The backend is mu300dash cpu_get/cpu_set (unisoc-modem/cpu, mu300-cpu):
 * a profile is a governor and a per-cluster scaling_min/max inside the hardware frequency table. It never changes
 * voltage and never touches the kernel thermal trips, so the device still throttles at its limit in every profile. */
/* The CPU voltage offset (EXPERIMENTAL: it can damage the device for good; mu300dash cpu_voltage_get/cpu_voltage_set,
 * mu300-cpu voltage, docs/FINDINGS.md 133). The firmware applies an offset once per boot, so a saved value waits for the
 * next reboot; that boot is its trial, and the initramfs drops it (back to 0) after a boot that ends without a clean
 * shutdown. Saving anything but 0,0,0 needs the confirmation word typed; the backend and the kernel check every value
 * again. */
var VOLT_WORD = 'EXPERIMENTAL';

return view.extend({
	load: function() {
		return Promise.all([ L.resolveDefault(M.callCpuGet(), {}), L.resolveDefault(M.callCpuVoltageGet(), {}) ]);
	},

	render: function(res) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.vbox = E('div', { 'id': 'mud-cpuv-box' });
		this.paint((res && res[0]) || {});
		this.paintVoltage((res && res[1]) || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callCpuGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* a core online/offline (op core) or CPU0's low latency (op lowlatency); saved, the reply is the new status */
	tune: function(btn, op, cpu, state) {
		var self = this;
		M.busy(btn, true);
		return M.callCpuTune(op, cpu == null ? '' : String(cpu), state).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			return self.paint(r);
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	/* apply a profile and show the result */
	set: function(btn, name) {
		var self = this;
		M.busy(btn, true);
		return M.callCpuSet(String(name)).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			return self.paint(r);
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	paint: function(st) {
		var self = this;
		if (!st.profile || !Array.isArray(st.policies)) {
			dom.content(this.root, [ E('section', { 'class': 'mud-card', 'id': 'mud-cpu-unavailable' }, [
				E('h3', {}, _('CPU')),
				E('div', { 'class': 'mud-note' }, [ _('CPU profile unavailable') + (st.error ? ': ' + M.errText(st) : '') ])
			]) ]);
			return;
		}
		var names = { saving: _('Power saving'), balanced: _('Balanced'), performance: _('Performance') };
		var descs = {
			saving: _('Cores capped at about 60 %% of their maximum (cooler, less power).'),
			balanced: _('Schedutil across the full hardware range (the stock behaviour).'),
			performance: _('Every cluster pinned at its hardware maximum.')
		};

		/* 1. the profile buttons */
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-cpu-profile' }, [ E('h3', {}, _('CPU profile')) ]);
		card.appendChild(E('div', { 'class': 'mud-note' },
			_('A profile sets the CPU governor and the per-cluster frequency limits. It stays within the hardware frequency table and never changes the voltage, so it cannot overclock this SoC. The kernel keeps throttling at its thermal limit in every profile.')));
		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' });
		[ 'saving', 'balanced', 'performance' ].forEach(function(p) {
			var b = E('button', {
				'class': p === st.profile ? 'mud-btn cbi-button-positive' : 'mud-btn',
				'id': 'mud-cpu-' + p
			}, [ names[p] ]);
			b.addEventListener('click', function() { self.set(b, p); });
			actions.appendChild(b);
		});
		card.appendChild(actions);
		card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-cpu-saved' },
			[ _('Saved profile: %s').format(names[st.profile] || String(st.profile)) ]));
		card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-cpu-desc' }, [ descs[st.profile] || '' ]));
		if (st.throttling)
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-cpu-throttle' },
				[ _('Thermal throttling is active right now: the frequencies below are below the profile ceiling.') ]));

		/* 2. the clusters */
		var num = function(x) { return typeof x === 'number' ? x : null; };
		var mhz = function(x) { return num(x) == null ? '--' : String(x); };
		var rows = st.policies.map(function(p) {
			return E('tr', {}, [
				E('td', {}, [ 'cpu' + String(p.cpus || '') ]),
				E('td', {}, [ String(p.governor || '--') ]),
				E('td', {}, [ mhz(p.cur) ]),
				E('td', {}, [ mhz(p.min) ]),
				E('td', {}, [ mhz(p.max) ]),
				E('td', {}, [ mhz(p.hwmax) ])
			]);
		});
		var table = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-cpu-clusters' }, [
			E('h3', {}, _('Clusters')),
			E('table', { 'class': 'mud-table', 'id': 'mud-cpu-table' }, [
				E('tr', {}, [
					E('th', {}, _('Cores')), E('th', {}, _('Governor')), E('th', {}, _('Now')),
					E('th', {}, _('Min')), E('th', {}, _('Max')), E('th', {}, _('Hardware max'))
				])
			].concat(rows)),
			E('div', { 'class': 'mud-note' }, _('Frequencies in MHz.'))
		]);

		/* 2b. the cores (CPU hotplug) and CPU0's low latency (FINDINGS 200): both saved and applied again at boot */
		var cards = [ card, table ];
		if (Array.isArray(st.cores) && st.cores.length) {
			var crow = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0', 'id': 'mud-cpu-cores' });
			st.cores.forEach(function(c) {
				var on = c.online === 1;
				var b = E('button', {
					'class': on ? 'mud-btn cbi-button-positive' : 'mud-btn',
					'id': 'mud-cpu-core-' + String(c.cpu)
				}, [ 'CPU' + String(c.cpu) ]);
				if (c.switchable !== 1) b.disabled = true;
				else b.addEventListener('click', function() { self.tune(b, 'core', c.cpu, on ? 'off' : 'on'); });
				crow.appendChild(b);
			});
			var ccard = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-cpu-corecard' }, [
				E('h3', {}, _('Cores')),
				E('div', { 'class': 'mud-note' },
					_('Highlighted cores are online. A core taken offline stays offline, also after a reboot, until it is brought back. CPU0 always stays online.')),
				crow
			]);
			var ll = st.lowlatency || {};
			if (ll.supported === 1) {
				var lon = ll.saved === 1;
				var lb = E('button', { 'class': lon ? 'mud-btn cbi-button-positive' : 'mud-btn', 'id': 'mud-cpu-lowlat' },
					[ lon ? _('Low latency: on') : _('Low latency: off') ]);
				lb.addEventListener('click', function() { self.tune(lb, 'lowlatency', null, lon ? 'off' : 'on'); });
				ccard.appendChild(E('div', { 'class': 'mud-note', 'style': 'margin-top:10px' },
					_('Low latency keeps CPU0 out of its idle states, as ZTE\'s U30 Air firmware does: Wi-Fi delays are much shorter, and the device draws more power. Off by default.')));
				ccard.appendChild(E('div', { 'style': 'margin:8px 0' }, [ lb ]));
			}
			cards.push(ccard);
		}

		/* 3. the kernel's thermal zones, read only (mu300-cpu status --json "thermal": /sys/class/thermal; on mainline the
		 * stock soc-thmzone with its trips and the cpufreq cooling devices, docs/FINDINGS.md 48). Nothing here writes. */
		if (Array.isArray(st.thermal) && st.thermal.length) {
			var deg = function(mc) { return num(mc) == null ? '--' : (mc / 1000).toFixed(1) + ' \u00b0C'; };
			var cnt = function(x) { return num(x) == null ? '--' : String(x); };
			var zones = st.thermal.slice().sort(function(a, b) {   /* the zones with trip points first */
				return ((b.trips || []).length ? 1 : 0) - ((a.trips || []).length ? 1 : 0);
			});
			var zrows = zones.map(function(z) {
				var trips = (z.trips || []).map(function(t) { return String(t.type || '?') + ' ' + deg(t.temp); }).join(', ');
				var cool = (z.cooling || []).map(function(c) { return String(c.type || '?') + ' ' + cnt(c.state) + '/' + cnt(c.max); }).join(', ');
				return E('tr', {}, [
					E('td', {}, [ String(z.zone || '') ]),
					E('td', {}, [ deg(z.temp) ]),
					E('td', {}, [ trips || '--' ]),
					E('td', {}, [ cool || '--' ])
				]);
			});
			cards.push(E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-cpu-thermal' }, [
				E('h3', {}, _('Thermal')),
				E('div', { 'class': 'mud-note' },
					_('The kernel\'s thermal zones, read only. A zone with trip points throttles the cooling devices bound to it at its passive trip; a cooling state of 0 is no throttling.')),
				E('table', { 'class': 'mud-table', 'id': 'mud-cpu-thermal-table' }, [
					E('tr', {}, [
						E('th', {}, _('Zone')), E('th', {}, _('Temperature')), E('th', {}, _('Trip points')), E('th', {}, _('Cooling devices'))
					])
				].concat(zrows))
			]));
		}

		cards.push(this.vbox);
		dom.content(this.root, cards);
	},

	/* the voltage request: op set (offsets "A,B,C", the word), off, or stress (minutes) */
	voltage: function(btn, op, offsets, word, minutes) {
		var self = this;
		M.busy(btn, true);
		return M.callCpuVoltageSet(op, offsets || '', word || '', minutes || '').then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 8000 });
				return;
			}
			M.toast(op === 'stress' ? _('Stress test started') : _('Saved: it takes effect at the next reboot'),
				{ type: 'success', timeout: 6000 });
			return self.paintVoltage(r);
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	paintVoltage: function(v) {
		var self = this;
		var card = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-cpuv' }, [
			E('h3', {}, _('CPU voltage offset (EXPERIMENTAL)')),
			E('div', { 'class': 'mud-danger', 'id': 'mud-cpuv-warning' },
				[ _('Experimental: changing the CPU voltage can make the device unstable, corrupt your data and permanently damage your device. Use it entirely at your own risk.') ])
		]);
		dom.content(this.vbox, [ card ]);
		if (v.ok !== 1) {
			card.appendChild(E('div', { 'class': 'mud-note' }, [ _('CPU voltage offset unavailable') + (v.error ? ': ' + M.errText(v) : '') ]));
			return;
		}
		if (v.phase === 'unsupported' || !v.kernel) {
			card.appendChild(E('div', { 'class': 'mud-note' }, [ _('Not available with this kernel (a mainline kernel of this release or later has it).') ]));
			return;
		}
		var lim = v.limits || {};
		var lo = typeof lim.min === 'number' ? lim.min : -25000, hi = typeof lim.max === 'number' ? lim.max : 12500;
		var step = typeof lim.step === 'number' && lim.step > 0 ? lim.step : 3125;
		var arr = function(a) { return Array.isArray(a) && a.length === 3 ? a.map(function(x) { return String(x); }).join(', ') : '--'; };
		var phases = {
			off: _('Off: the stock voltages'),
			pending: _('Changes at the next reboot'),
			trial: _('Trial boot: kept once this boot has been up 10 minutes'),
			good: _('Kept: this offset passed its trial boot')
		};
		var reasons = {
			'unclean-end': _('a boot with it ended without a clean shutdown'),
			'no-good-mark': _('its trial boot ended before 10 minutes'),
			'invalid': _('invalid value'),
			'unknown-firmware': _('unknown SoC firmware'),
			'kernel-refused': _('refused by the kernel'),
			'kernel-failed': _('the kernel could not start CPU scaling with it')
		};
		card.appendChild(E('div', { 'class': 'mud-note' },
			_('Offsets in µV for the three CPU domains (cores 0-3, 4-6 and 7), in whole steps. A new offset takes effect at the next reboot, as a trial: it is kept once that boot has been up 10 minutes, and dropped (back to 0) if a boot with it ends in a crash, a watchdog reset or a power cut.')));
		card.appendChild(E('div', { 'class': 'mud-r', 'id': 'mud-cpuv-now' }, [
			E('span', { 'class': 'mud-k' }, _('Running now (µV)')), E('span', { 'class': 'mud-v' }, [ arr(v.applied) ]) ]));
		card.appendChild(E('div', { 'class': 'mud-r', 'id': 'mud-cpuv-asked' }, [
			E('span', { 'class': 'mud-k' }, _('Saved for the next boot (µV)')), E('span', { 'class': 'mud-v' }, [ arr(v.requested) ]) ]));
		card.appendChild(E('div', { 'class': 'mud-r', 'id': 'mud-cpuv-phase' }, [
			E('span', { 'class': 'mud-k' }, _('State')), E('span', { 'class': 'mud-v' }, [ phases[v.phase] || String(v.phase || '--') ]) ]));
		if (v.rejected && typeof v.rejected === 'object')
			card.appendChild(E('div', { 'class': 'mud-danger', 'id': 'mud-cpuv-rejected' }, [
				_('Taken back automatically: %s µV (%s)').format(arr(v.rejected.offsets),
					reasons[v.rejected.reason] || String(v.rejected.reason || '?')) ]));
		var fwOk = v.firmware === 'known';
		if (!fwOk)
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-cpuv-firmware' },
				[ _('The SoC firmware of this device is not one whose voltage encoding is known: only the stock voltages are possible.') ]));

		/* the domains: what runs now, the table's top voltage, and the new value */
		var inputs = [];
		var cur = Array.isArray(v.requested) && v.requested.length === 3 ? v.requested : [ 0, 0, 0 ];
		var rows = (Array.isArray(v.domains) ? v.domains : []).map(function(d) {
			var idx = typeof d.domain === 'number' ? d.domain : -1;
			var tbl = Array.isArray(d.table) ? d.table : [];
			var top = tbl.length && Array.isArray(tbl[tbl.length - 1]) ? tbl[tbl.length - 1] : null;
			var inp = E('input', { 'type': 'number', 'class': 'cbi-input-text', 'id': 'mud-cpuv-in' + idx,
				'min': String(lo), 'max': String(hi), 'step': String(step), 'style': 'width:8em',
				'value': String(idx >= 0 && idx < 3 && typeof cur[idx] === 'number' ? cur[idx] : 0) });
			if (idx >= 0 && idx < 3) inputs[idx] = inp;
			return E('tr', {}, [
				E('td', {}, [ String(idx) ]),
				E('td', {}, [ 'cpu' + String(d.cpus || '') ]),
				E('td', {}, [ typeof d.offset === 'number' ? String(d.offset) : '--' ]),
				E('td', {}, [ top ? (top[0] / 1000).toFixed(0) + ' MHz / ' + (top[1] / 1000).toFixed(2) + ' mV' : '--' ]),
				E('td', {}, [ inp ])
			]);
		});
		card.appendChild(E('table', { 'class': 'mud-table', 'id': 'mud-cpuv-table', 'style': 'margin-top:10px' }, [
			E('tr', {}, [ E('th', {}, _('Domain')), E('th', {}, _('Cores')), E('th', {}, _('Offset now (µV)')),
				E('th', {}, _('Highest step')), E('th', {}, _('New offset (µV)')) ])
		].concat(rows)));
		card.appendChild(E('div', { 'class': 'mud-note' },
			[ _('Limits: %d to %d µV, in steps of %d µV.').format(lo, hi, step) ]));

		var word = E('input', { 'type': 'text', 'class': 'cbi-input-text', 'id': 'mud-cpuv-word', 'autocomplete': 'off',
			'spellcheck': 'false', 'style': 'width:12em', 'placeholder': VOLT_WORD });
		var save = E('button', { 'class': 'mud-btn warn', 'id': 'mud-cpuv-save', 'disabled': fwOk && inputs.length === 3 ? null : '' },
			[ _('Save for the next boot') ]);
		save.addEventListener('click', function() {
			var vals = [];
			for (var i = 0; i < 3; i++) {
				var n = inputs[i] ? Number(inputs[i].value) : NaN;
				if (!isFinite(n) || Math.floor(n) !== n || n < lo || n > hi || n % step !== 0) {
					M.toast(_('Each offset must be a multiple of %d µV between %d and %d.').format(step, lo, hi), { type: 'error', timeout: 8000 });
					return;
				}
				vals.push(String(n));
			}
			var offs = vals.join(',');
			if (offs === '0,0,0') return self.voltage(save, 'off');
			if (word.value !== VOLT_WORD) {
				M.toast(_('Type %s to confirm.').format(VOLT_WORD), { type: 'error', timeout: 8000 });
				word.focus();
				return;
			}
			return self.voltage(save, 'set', offs, VOLT_WORD);
		});
		var off = E('button', { 'class': 'mud-btn', 'id': 'mud-cpuv-off' }, [ _('Back to the stock voltages') ]);
		off.addEventListener('click', function() { self.voltage(off, 'off'); });
		card.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [
			E('label', { 'for': 'mud-cpuv-word' }, [ _('Type %s to confirm:').format(VOLT_WORD) ]), word, save, off ]));

		/* the stress test, the way to find out during the trial boot */
		var stress = v.stress || {};
		var sb = E('button', { 'class': 'mud-btn', 'id': 'mud-cpuv-stress', 'disabled': stress.running ? '' : null },
			[ _('Run a 10-minute CPU stress test') ]);
		sb.addEventListener('click', function() { self.voltage(sb, 'stress', '', '', '10'); });
		card.appendChild(E('div', { 'class': 'mud-note' },
			[ _('Test a new offset under load during its trial boot. The device may crash if the voltage is too low; the next boot then runs at the stock voltages.') ]));
		card.appendChild(E('div', { 'style': 'margin:8px 0' }, [ sb ]));
		if (stress.running)
			card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-cpuv-stress-running' }, [ _('A stress test is running.') ]));
		if (stress.last)
			card.appendChild(E('pre', { 'class': 'mud-out', 'id': 'mud-cpuv-stress-last' }, [ String(stress.last) ]));
	}
});
