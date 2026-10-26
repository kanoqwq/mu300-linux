'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Update: the device updated from the browser. What is installed and what is kept for going back, the
 * releases on GitHub, an update from an uploaded release file for a device that is not online, going back, the
 * kernel. Nothing here is an updater of its own: every action is one mu300-update command that the backend
 * (mu300dash update_get/update_job/update_act, unisoc-modem/update) runs as a task of its own, which goes on when
 * this page is closed. The page asks after the task and shows it to whoever opens it. */
var WIKI = 'https://github.com/dikeckaan/mu300-linux/wiki/Updating';
var UPLOAD_URL = '/cgi-upload';

function row(k, v) {
	var cell = E('span', { 'class': 'mud-v' }, []);
	dom.append(cell, Array.isArray(v) ? v : [ v ]);
	return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ k ]), cell ]);
}
function when(s) { return s ? new Date(s * 1000).toLocaleString() : '--'; }
function kb(n) { return n == null ? '--' : M.fmtBytes(n * 1024); }
// The release tags (docs/FINDINGS.md 125): vYYYY.MM.DD, and from after v2026.10.27 on vYYYY.MM.DD.N (the day of the
// release, N the release of that day), newer than every three-field tag. The web installer's version.js, word for word.
function releaseKey(t) {
	var m = /^v(\d{4})\.(\d\d)\.(\d\d)$/.exec(typeof t === 'string' ? t : '');
	if (m) return [ 1, +(m[1] + m[2] + m[3]), 0 ];
	m = /^v(\d{4})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.([1-9]\d?)$/.exec(typeof t === 'string' ? t : '');
	return m ? [ 2, +(m[1] + m[2] + m[3]), +m[4] ] : null;
}
function newerTag(a, b) {
	var x = releaseKey(a), y = releaseKey(b);
	if (!x || !y) return false;
	for (var i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
	return false;
}
function newer(a, b) { return newerTag(a, b); }
function check(id, label, on) {
	var box = E('input', { 'type': 'checkbox', 'id': id });
	box.checked = !!on;
	return { box: box, el: E('label', { 'style': 'display:flex;gap:8px;align-items:flex-start;margin:6px 0', 'for': id }, [ box, E('span', {}, [ label ]) ]) };
}

return view.extend({
	/* nothing here is a form: every button acts at once, so LuCI's Save & Apply, Save and Reset are not shown */
	handleSaveApply: null, handleSave: null, handleReset: null,

	load: function() { return L.resolveDefault(M.callUpdateGet('1'), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.beta = false;
		this.logOpen = false;
		this.fails = 0;
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callUpdateGet('1'), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* one update_act; a started task is followed until it ends. The source address goes only where it belongs (the
	 * Mirror field's Save): every other operation sends its own arguments alone, as its requests have always done */
	act: function(btn, a) {
		var self = this;
		M.busy(btn, true);
		var cargs = [ a.op, a.tag || '', a.systems || '', a.kernel || '', a.sha256 || '', a.size || '', a.flags || '', a.confirm || '' ];
		if (a.url !== undefined)
			cargs.push(a.url);
		return M.callUpdateAct.apply(null, cargs).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 8000 });
				return self.reload().then(function() { return null; });
			}
			return self.reload().then(function() { return r; });
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
			return null;
		});
	},

	/* the task runs on the device, whatever happens to this page: ask every two seconds, and go on asking when a
	 * call fails (the session ran out, rpcd was restarted) */
	follow: function() {
		var self = this;
		if (this.following) return;
		this.following = true;
		var tick = function() {
			return L.resolveDefault(M.callUpdateJob(), null).then(function(r) {
				var job = r && r.job;
				if (!job) {
					self.fails++;
					setTimeout(tick, M.backoff(2000, self.fails));
					return;
				}
				self.fails = 0;
				if (job.state === 'running') {
					self.paintJob(job);
					setTimeout(tick, 2000);
					return;
				}
				self.following = false;
				return self.reload();
			});
		};
		setTimeout(tick, 1500);
	},

	kindText: function(kind) {
		return ({
			check: _('Check for updates'), inspect: _('Checking the file'), apply: _('Update'),
			file: _('Update from a file'), rollback: _('Back to the previous version'),
			rollback_boot: _('Back to the previous boot image'), clean: _('Remove the previous version'),
			kernel: _('Kernel change')
		})[kind] || kind || '';
	},

	stageText: function(job) {
		return ({
			start: _('Starting…'), download: _('Downloading'), verified: _('All files are here and verified'),
			unpack: _('Unpacking the new system'), keep: _('Carrying over your settings'),
			packages: _('Writing down the packages you installed'), 'switch': _('Switching to the new system'),
			boot: _('Kernel and boot image')
		})[job.stage] || job.stage_text || '';
	},

	restart: function(btn) {
		var self = this, android = this.st && this.st.boot_default !== 'linux';
		return M.confirmBox(_('Restart the device?'),
			android ? _('The device restarts now. The default boot is Android, so it starts Android: switch to Linux from there to start the updated system.')
				: _('The device restarts now and is away for about a minute. If the new system does not start, the device goes back to Android by itself after the failed attempts.'),
			{ danger: true, okText: _('Restart') }).then(function(yes) {
			if (!yes) return;
			M.busy(btn, true);
			return M.callAct('reboot', '').then(function() {
				M.toast(_('Restarting… open this page again in about a minute.'), { type: 'busy', timeout: 0 });
			}, function() { M.busy(btn, false); });
		});
	},

	/* the task's card, repainted alone while it runs (the rest of the page does not change meanwhile) */
	paintJob: function(job) {
		var self = this, box = this.jobBox;
		if (!box) return;
		if (!job || job.state === 'none' || job.kind === 'check' || job.kind === 'inspect') { dom.content(box, []); return; }
		var running = job.state === 'running';
		var kids = [ E('h3', {}, [ this.kindText(job.kind) ]) ];
		if (running) {
			kids.push(E('div', { 'id': 'mud-upd-stage', 'style': 'font-weight:600;margin:6px 0' }, [ this.stageText(job) ]));
			if (job.file) {
				var pct = job.file.total ? Math.min(100, Math.round(job.file.have * 100 / job.file.total)) : 0;
				kids.push(E('div', { 'class': 'mud-note', 'id': 'mud-upd-file' }, [ job.file.name + ': ' + M.fmtBytes(job.file.have) +
					(job.file.total ? ' / ' + M.fmtBytes(job.file.total) + ' (' + pct + '%)' : '') ]));
				kids.push(E('div', { 'class': 'mud-meter' }, [ E('i', { 'style': 'width:' + pct + '%;background:var(--brand,var(--primary,#3b82f6))' }) ]));
			}
			kids.push(E('div', { 'class': 'mud-note' }, [ _('Running for %s. It goes on when this page is closed; do not switch the device off.').format(M.ageText(job.elapsed)) ]));
		} else if (job.state === 'done') {
			kids.push(E('div', { 'id': 'mud-upd-result', 'style': 'font-weight:600;margin:6px 0;color:var(--success,#16a34a)' },
				[ job.changed ? _('Done. Restart the device to start it.') : _('Done. Nothing needs a restart.') ]));
		} else {
			kids.push(E('div', { 'id': 'mud-upd-result', 'style': 'font-weight:600;margin:6px 0;color:var(--danger,#E25555)' },
				[ job.changed ? _('It did not finish, and part of it was done: read the log before you restart.') : _('It did not finish. Nothing was changed; the log says why.') ]));
		}
		var det = E('details', { 'id': 'mud-upd-log', 'style': 'margin-top:8px' }, [
			E('summary', { 'style': 'cursor:pointer' }, _('Log')),
			E('pre', { 'style': 'max-height:22em;overflow:auto;white-space:pre-wrap;font-size:.72rem;margin:6px 0 0' }, [ job.log || '' ])
		]);
		det.open = this.logOpen || job.state === 'failed';
		det.addEventListener('toggle', function() { self.logOpen = det.open; });
		kids.push(det);
		if (!running) {
			var btns = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px' });
			if (job.changed) {
				var rb = E('button', { 'class': 'mud-btn on', 'id': 'mud-upd-restart' }, _('Restart now'));
				rb.addEventListener('click', function() { self.restart(rb); });
				btns.appendChild(rb);
			}
			var later = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-dismiss' }, [ job.changed ? _('Later') : _('Close') ]);
			later.addEventListener('click', function() { self.act(later, { op: 'dismiss' }); });
			btns.appendChild(later);
			kids.push(btns);
		}
		dom.content(box, [ E('section', { 'class': 'mud-card', 'id': 'mud-upd-job', 'style': 'margin-bottom:14px' }, [].concat(kids)) ]);
	},

	/* a button that does something that cannot be taken back: a tick first, then a dialog that says what happens */
	guarded: function(id, label, tickText, title, message, okText, args, off) {
		var self = this;
		var tick = check(id + '-sure', tickText, false);
		var btn = E('button', { 'class': 'mud-btn warn', 'id': id, 'disabled': '' }, [ label ]);
		if (off) tick.box.disabled = true;
		tick.box.addEventListener('change', function() { btn.disabled = !tick.box.checked; });
		btn.addEventListener('click', function() {
			if (!tick.box.checked) return;
			M.confirmBox(title, message, { danger: true, okText: okText }).then(function(yes) {
				if (yes) self.act(btn, args).then(function(r) { if (r && r.started) self.follow(); });
			});
		});
		return E('div', { 'style': 'margin:10px 0' }, [ tick.el, btn ]);
	},

	paint: function(res) {
		var self = this;
		if (res.ok !== 1) {
			dom.content(this.root, [ E('section', { 'class': 'mud-card' }, [
				E('h3', {}, _('Update')),
				E('div', { 'class': 'mud-note', 'id': 'mud-upd-error' }, [ res.error ? M.errText(res) : _('Updating is not available on this system') ])
			]) ]);
			return;
		}
		var st = this.st = res.status || {}, job = res.job || {}, chk = res.check, up = res.upload || {};
		var systems = st.systems || [], kernel = st.kernel || {}, disk = st.disk || {}, lock = st.lock || {};
		var running = job.state === 'running';
		/* someone else's mu300-update (a shell, the toolkit): the updater's own lock is what decides */
		var foreign = !running && lock.held;
		var off = running || foreign;
		var me = systems.filter(function(s) { return s.name === st.running; })[0] || {};

		this.jobBox = E('div', { 'id': 'mud-upd-jobbox' });
		var cards = [ this.jobBox ];

		if (foreign) cards.push(E('section', { 'class': 'mud-card', 'id': 'mud-upd-foreign', 'style': 'margin-bottom:14px' }, [
			E('h3', {}, _('Update')),
			E('div', {}, [ _('Another update is running on the device') + ': ' + (lock.command || '') + (lock.pid ? ' (pid ' + lock.pid + ')' : '') ]),
			E('div', { 'class': 'mud-note' }, _('It was started outside this page (a terminal, the toolkit). Nothing can be started here until it has finished.'))
		]));

		if (st.restart_pending && !(job.state === 'done' && job.changed)) {
			var prb = E('button', { 'class': 'mud-btn on', 'id': 'mud-upd-pending-restart', 'disabled': off ? '' : null }, _('Restart now'));
			prb.addEventListener('click', function() { self.restart(prb); });
			cards.push(E('section', { 'class': 'mud-card', 'id': 'mud-upd-pending', 'style': 'margin-bottom:14px' }, [
				E('h3', {}, _('Restart needed')),
				E('div', {}, _('This system was updated and still runs the old version. The new one starts at the next restart.')),
				E('div', { 'style': 'margin-top:10px' }, [ prb ])
			]));
		}

		/* ---- what is installed */
		var inst = E('section', { 'class': 'mud-card', 'id': 'mud-upd-status' }, [ E('h3', {}, _('Installed')) ]);
		inst.appendChild(row(_('This system'), (st.running || '--') + ' · ' + (me.release || st.installed || '--')));
		systems.forEach(function(s) {
			if (s.name !== st.running) inst.appendChild(row(_('Also on this device'), s.name + (s.ubuntu ? ' ' + s.ubuntu : '') + ' · ' + s.release));
		});
		inst.appendChild(row(_('Kernel'), (kernel.running || '--') + ' · ' + _('choice %s').format(kernel.choice || '--')));
		inst.appendChild(row(_('Boot image'), kernel.boot || _('from the installer')));
		inst.appendChild(row(_('Default boot'), st.boot_default === 'linux' ? 'Linux' : 'Android'));
		inst.appendChild(row(_('Free space'), kb(disk.free_kb) + ' / ' + kb(disk.total_kb)));
		inst.appendChild(row(_('Release signatures'), st.signing && st.signing.key ? _('checked with the project\'s key') : _('this system has no release key')));
		inst.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-upd-bootnote' }, [
			st.boot_default !== 'linux' ? _('The default boot is Android: a restart goes to Android, and Linux is started from there.')
				: st.boot_locked ? _('Linux is locked as the default boot: a failed start does not send the device to Android by itself, except the first starts of a new boot image.')
				: _('If Linux fails to start %d times in a row, the device starts Android by itself.').format(st.boot_attempts || 5)
		]));
		/* what the last updates carried over, set aside or put back (/etc/mu300/update-report): open while it is new */
		if (res.report) inst.appendChild(E('details', { 'id': 'mud-upd-report', 'style': 'margin-top:8px', 'open': res.report_new ? '' : null }, [
			E('summary', { 'style': 'cursor:pointer' }, _('Report of the last update')),
			E('pre', { 'style': 'max-height:18em;overflow:auto;white-space:pre-wrap;font-size:.72rem;margin:6px 0 0' }, [ res.report ])
		]));
		cards.push(inst);

		/* ---- online */
		var online = E('section', { 'class': 'mud-card', 'id': 'mud-upd-online', 'style': 'margin-top:14px' }, [ E('h3', {}, _('Online update')) ]);
		var checking = running && job.kind === 'check';
		var cb = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-check', 'disabled': off ? '' : null }, _('Check for updates'));
		cb.addEventListener('click', function() {
			self.act(cb, { op: 'check' }).then(function(r) { if (r && r.started) self.follow(); });
		});
		online.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0' }, [ cb,
			E('span', { 'class': 'mud-note', 'style': 'margin:0', 'id': 'mud-upd-checked' }, [
				checking ? _('Asking GitHub…') : res.check_age != null ? _('Checked %s ago').format(M.ageText(res.check_age)) : _('Not checked yet') ]) ]));
		/* where the release files come from: one address, written to /etc/mu300/update-source through mu300-update
		 * source (the adapter runs that one command; the environment is not a channel to it). Empty: GitHub, which is
		 * also what the placeholder says while the field is left alone. */
		var src = st.source || {};
		var sfield = E('input', { 'type': 'text', 'id': 'mud-upd-source', 'spellcheck': 'false', 'autocomplete': 'off',
			'style': 'flex:1 1 20em;min-width:12em;padding:4px 6px', 'placeholder': src.files || 'GitHub' });
		var sbtn = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-source-save', 'disabled': off ? '' : null }, [ _('Save') ]);
		sbtn.addEventListener('click', function() {
			self.act(sbtn, { op: 'source', url: sfield.value.trim(), confirm: 'source' });
		});
		online.appendChild(E('div', { 'style': 'margin:8px 0' }, [
			E('label', { 'class': 'mud-note', 'style': 'display:block;margin:0', 'for': 'mud-upd-source' }, [ _('Mirror (release files)') ]),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0' }, [ sfield, sbtn,
				E('span', { 'class': 'mud-note', 'style': 'margin:0' }, [ _('Leave empty for GitHub.') ]) ]) ]));
		var reinstall = check('mud-upd-reinstall', _('Install the packages I added again after the update (needs the device online at the first start)'), true);
		var sysBoxes = systems.map(function(s) { return { name: s.name, c: check('mud-upd-sys-' + s.name, s.name + ' · ' + s.release, true) }; });
		var offered = false;	/* an Update button is on the page: the choices that go with it are shown */
		var offer = function(rel, pre) {
			var todo = newer(rel.tag, st.installed) || systems.some(function(s) { return newer(rel.tag, s.release); }) ||
				(rel.tag === st.installed && kernel.boot !== rel.tag);
			var box = E('div', { 'id': pre ? 'mud-upd-beta' : 'mud-upd-latest', 'style': 'margin:10px 0' }, [
				row(pre ? _('Pre-release') : _('Newest release'), [ rel.tag + (rel.published ? ' · ' + rel.published : '') + ' ',
					E('a', { 'href': rel.url, 'target': '_blank', 'rel': 'noopener noreferrer' }, _('Release notes')) ]),
				row(_('Download'), M.fmtBytes(rel.download)),
				row(_('Free space needed'), kb(rel.need_kb) + (disk.free_kb != null && rel.need_kb > disk.free_kb ? ' · ' + _('not enough: remove the previous version first') : ''))
			]);
			if (pre) box.appendChild(E('div', { 'class': 'mud-note' }, _('A pre-release is a release still being tried: it can have faults a release does not. The previous version is kept all the same.')));
			if (!todo) {
				box.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-upd-uptodate' }, [
					newer(st.installed, rel.tag) ? _('This system is newer than the newest release (a test release).') : _('Up to date.') ]));
				return box;
			}
			/* a system with the release key takes no unsigned release from the internet (mu300-update refuses it, and
			 * the page has no word to give for that): say so instead of offering an update that can only fail */
			if (rel.signed !== 1 && st.signing && st.signing.key) {
				box.appendChild(E('div', { 'id': pre ? 'mud-upd-beta-unsigned' : 'mud-upd-unsigned-online', 'style': 'margin:8px 0;color:var(--danger,#E25555)' },
					_('This release is not signed, and this system checks signatures: it is not installed from here. Update from its file below, which asks you to confirm.')));
				return box;
			}
			var ub = E('button', { 'class': 'mud-btn on', 'id': pre ? 'mud-upd-apply-beta' : 'mud-upd-apply', 'disabled': off ? '' : null },
				[ _('Update to %s').format(rel.tag) ]);
			ub.addEventListener('click', function() {
				var sel = sysBoxes.filter(function(b) { return b.c.box.checked; }).map(function(b) { return b.name; });
				if (!sel.length) { M.toast(_('Choose at least one system'), { type: 'error' }); return; }
				M.confirmBox(_('Update to %s?').format(rel.tag),
					_('The device downloads %s, puts the new system beside the running one and switches to it at the next restart. Your settings are carried over and the previous version is kept. It takes several minutes; do not switch the device off.').format(M.fmtBytes(rel.download)),
					{ okText: _('Update') }).then(function(yes) {
					if (!yes) return;
					self.act(ub, { op: 'apply', tag: rel.tag, systems: sel.length === systems.length ? '' : sel.join(' '),
						flags: reinstall.box.checked ? '' : 'no_reinstall', confirm: 'apply' })
						.then(function(r) { if (r && r.started) self.follow(); });
				});
			});
			box.appendChild(E('div', { 'style': 'margin-top:8px' }, [ ub ]));
			offered = true;
			return box;
		};
		if (chk && chk.ok === 1 && chk.latest) {
			online.appendChild(offer(chk.latest, false));
			if (chk.beta) {
				var betaTick = check('mud-upd-beta-on', _('Also offer the pre-release %s').format(chk.beta.tag), this.beta);
				betaTick.box.addEventListener('change', function() { self.beta = betaTick.box.checked; self.paint(res); });
				online.appendChild(betaTick.el);
				if (this.beta) online.appendChild(offer(chk.beta, true));
			}
			if (offered && systems.length > 1) {
				online.appendChild(E('div', { 'class': 'mud-note' }, _('Systems to update:')));
				sysBoxes.forEach(function(b) { online.appendChild(b.c.el); });
			}
			if (offered) online.appendChild(reinstall.el);
		} else if (chk && !checking) {
			online.appendChild(E('div', { 'id': 'mud-upd-offline', 'style': 'margin:8px 0;color:var(--warning,#f59e0b)' },
				_('GitHub could not be reached: the device has no internet connection, or GitHub did not answer. Without internet, update from a file below.')));
		}
		cards.push(online);

		/* ---- from a file */
		var want = 'mu300-magisk-<' + _('release') + '>-' + (me.name === 'ubuntu' ? 'ubuntu-' + (me.ubuntu || '24.04') : (me.name || 'openwrt-luci')) + '-k' + (kernel.choice || '5.4') + '.zip';
		var filec = E('section', { 'class': 'mud-card', 'id': 'mud-upd-file', 'style': 'margin-top:14px' }, [
			E('h3', {}, _('Update from a file')),
			E('div', { 'class': 'mud-note' }, [ _('For a device without internet: download the Magisk zip of a release for this system and this kernel on another computer, from the release page on GitHub, and upload it here. For this device that is:') + ' ', E('code', {}, [ want ]) ])
		]);
		var insp = up.inspect, inspecting = running && job.kind === 'inspect';
		if (!up.present) {
			var pick = E('input', { 'type': 'file', 'id': 'mud-upd-pick', 'accept': '.zip,application/zip' });
			var meter = E('div', { 'class': 'mud-note', 'id': 'mud-upd-upmeter' }, '');
			var ubtn = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-upload', 'disabled': off ? '' : null }, _('Upload the file…'));
			ubtn.addEventListener('click', function() { self.upload(ubtn, pick, meter); });
			filec.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [ pick, ubtn ]));
			filec.appendChild(meter);
		} else {
			filec.appendChild(row(_('Uploaded file'), M.fmtBytes(up.size)));
			var flags = [], ok = false;
			if (inspecting) filec.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-upd-inspecting' }, _('Checking the file…')));
			else if (insp && insp.ok === 1) {
				filec.appendChild(row('SHA-256', E('code', { 'id': 'mud-upd-sha', 'style': 'word-break:break-all' }, [ insp.sha256 || '--' ])));
				filec.appendChild(row(_('It says it is'), (insp.tag || '--') + ' · ' + (insp.system || '--') + (insp.ubuntu ? ' ' + insp.ubuntu : '') + (insp.kernel ? ' · ' + _('kernel %s').format(insp.kernel) : '')));
				filec.appendChild(row(_('Signature'), insp.signature === 'valid' ? _('signed with the project\'s release key')
					: insp.signature === 'bad' ? _('WRONG: it does not match the project\'s release key')
					: insp.signature === 'unsigned' ? _('not signed') : _('cannot be checked on this system')));
				var why = ({
					'system-missing': _('Its system is not installed on this device.'),
					'ubuntu-release': _('It is the other Ubuntu release, not the one installed.'),
					'kernel': _('It carries another kernel than this device uses. Take the zip for kernel %s, or change the kernel first.').format(kernel.choice || ''),
					'device': _('Its kernel does not run on this model.'),
					'checksum': _('The file is damaged: its contents do not match their checksums.'),
					'signature': _('Its signature is wrong. This file does not come from the project as it is; it is never installed.')
				})[insp.why];
				if (!insp.fits) filec.appendChild(E('div', { 'id': 'mud-upd-refused', 'style': 'margin:8px 0;color:var(--danger,#E25555)' },
					[ _('This file cannot be used here.') + ' ' + (why || '') ]));
				else {
					ok = true;
					/* (a signed file is proven by its signature: the note is for one that is not - on the U30 Air it showed for both) */
					if (insp.signature !== 'valid') filec.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-upd-compare' }, _('Compare the SHA-256 with the one in SHA256SUMS-magisk on the release page: it is the only proof of where an unsigned file comes from.')));
					if (insp.same) filec.appendChild(E('div', { 'class': 'mud-note' }, _('This release is installed already; the update would only refresh the boot image.')));
					if (insp.signature !== 'valid') flags.push({ name: 'unsigned', c: check('mud-upd-unsigned',
						_('I know this file is not signed: nothing proves that it comes from the project. I compared its SHA-256 myself and want it installed.'), false) });
					if (insp.downgrade) flags.push({ name: 'downgrade', c: check('mud-upd-downgrade',
						_('I know this is %s, older than the installed %s, and want to go back to it.').format(insp.tag, insp.installed || ''), false) });
					flags.forEach(function(f) { filec.appendChild(f.c.el); });
				}
			} else if (insp) {
				filec.appendChild(E('div', { 'id': 'mud-upd-refused', 'style': 'margin:8px 0;color:var(--danger,#E25555)' },
					_('This is not a release file of this project: a Magisk zip of a release is expected.')));
			}
			var fb = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px' });
			if (ok) {
				var fa = E('button', { 'class': 'mud-btn on', 'id': 'mud-upd-apply-file', 'disabled': off ? '' : null }, [ _('Update from this file') ]);
				fa.addEventListener('click', function() {
					if (flags.some(function(f) { return !f.c.box.checked; })) { M.toast(_('Tick the confirmations above first'), { type: 'error' }); return; }
					M.confirmBox(_('Update to %s?').format(insp.tag),
						_('The device puts the system of this file beside the running one and switches to it at the next restart. Your settings are carried over and the previous version is kept. It takes several minutes; do not switch the device off.'),
						{ okText: _('Update'), danger: insp.signature !== 'valid' }).then(function(yes) {
						if (!yes) return;
						self.act(fa, { op: 'apply_file', sha256: insp.sha256, confirm: 'apply',
							flags: flags.map(function(f) { return f.name; }).concat(reinstall.box.checked ? [] : [ 'no_reinstall' ]).join(' ') })
							.then(function(r) { if (r && r.started) self.follow(); });
					});
				});
				fb.appendChild(fa);
			}
			if (!insp && !inspecting) {
				var ib = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-inspect', 'disabled': off ? '' : null }, _('Check the file'));
				ib.addEventListener('click', function() { self.act(ib, { op: 'upload_done' }).then(function(r) { if (r && r.started) self.follow(); }); });
				fb.appendChild(ib);
			}
			var del = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-upload-cancel', 'disabled': running && job.kind === 'file' ? '' : null }, _('Remove the file'));
			del.addEventListener('click', function() { self.act(del, { op: 'upload_cancel' }); });
			fb.appendChild(del);
			filec.appendChild(fb);
		}
		cards.push(filec);

		/* ---- going back */
		var back = E('section', { 'class': 'mud-card', 'id': 'mud-upd-back', 'style': 'margin-top:14px' }, [ E('h3', {}, _('Previous version')) ]);
		var kept = systems.filter(function(s) { return s.previous; });
		var prevBoot = kernel.previous;
		if (!kept.length && !prevBoot) back.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-upd-noprev' }, _('No previous version is kept on this device.')));
		kept.forEach(function(s) {
			back.appendChild(row(s.name, s.previous.release + ' · ' + _('replaced %s').format(when(s.since)) + (s.previous.kb != null ? ' · ' + kb(s.previous.kb) : '')));
		});
		if (prevBoot) back.appendChild(row(_('Boot image'), (prevBoot.release || '--') + (prevBoot.kernel ? ' · ' + _('kernel %s').format(prevBoot.kernel) : '') + ' · ' + _('replaced %s').format(when(prevBoot.since))));
		var usable = kept.filter(function(s) { return !s.previous.running; });
		if (usable.length) back.appendChild(this.guarded('mud-upd-rollback', _('Back to the previous version'),
			_('I want the previous system back; what I changed since the update stays in the version I leave.'),
			_('Back to the previous version?'),
			_('The previous system (%s) becomes the one that starts at the next restart. The version you leave is not kept; settings you changed since the update are not carried back.').format(usable.map(function(s) { return s.name + ' ' + s.previous.release; }).join(', ')),
			_('Go back'), { op: 'rollback', confirm: 'rollback' }, off));
		else if (kept.length) back.appendChild(E('div', { 'class': 'mud-note' }, _('The previous version is the one running now: restart first.')));
		if (prevBoot && prevBoot.usable) back.appendChild(this.guarded('mud-upd-rollback-boot', _('Back to the previous boot image'),
			_('I want the previous kernel and boot image back.'),
			_('Back to the previous boot image?'),
			_('The previous boot image (%s) is written to the Linux slot and starts at the next restart. If it does not start, the device goes back to Android by itself.').format(prevBoot.release || '--'),
			_('Go back'), { op: 'rollback_boot', confirm: 'rollback' }, off));
		if (kept.length) back.appendChild(this.guarded('mud-upd-clean', _('Remove the previous version'),
			_('I do not need to go back: delete the previous system to free its space.'),
			_('Remove the previous version?'),
			_('The kept copy of the previous system is deleted for good. After that there is no going back to it from this page.'),
			_('Remove'), { op: 'clean', confirm: 'clean' }, off));
		cards.push(back);

		/* ---- the kernel, and Android */
		var kc = E('section', { 'class': 'mud-card', 'id': 'mud-upd-kernel', 'style': 'margin-top:14px' }, [ E('h3', {}, _('Kernel')) ]);
		var ksel = E('select', { 'class': 'cbi-input-select', 'id': 'mud-upd-kernel-pick' }, (kernel.choices || []).map(function(k) {
			return E('option', { 'value': k.name, 'disabled': k.ok ? null : '' }, [ k.name + (k.name === '5.4' ? ' · ' + _('the vendor kernel') : ' · ' + _('mainline')) +
				(k.why === 'ubuntu-26.04' ? ' · ' + _('not with Ubuntu 26.04') : '') ]);
		}));
		ksel.value = kernel.choice || '5.4';
		var kb2 = E('button', { 'class': 'mud-btn warn', 'id': 'mud-upd-kernel-set', 'disabled': off ? '' : null }, _('Change the kernel'));
		kb2.addEventListener('click', function() {
			var k = ksel.value;
			if (k === kernel.choice) { M.toast(_('This kernel is the one in use'), { type: 'info' }); return; }
			M.confirmBox(_('Change the kernel to %s?').format(k),
				_('The device downloads that kernel from the newest release and writes a new boot image; it starts at the next restart. The previous boot image is kept, and if the new one does not start the device goes back to Android by itself. The device must be online.'),
				{ danger: true, okText: _('Change the kernel') }).then(function(yes) {
				if (yes) self.act(kb2, { op: 'kernel', kernel: k, confirm: 'kernel' }).then(function(r) { if (r && r.started) self.follow(); });
			});
		});
		kc.appendChild(E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [ ksel, kb2 ]));
		var ab = E('button', { 'class': 'mud-btn', 'id': 'mud-upd-android', 'disabled': off ? '' : null }, _('Start Android at the next restart'));
		ab.addEventListener('click', function() {
			M.confirmBox(_('Start Android at the next restart?'),
				_('The next restart, and the ones after it, start Android instead of Linux. Linux is started again from Android (the Magisk module\'s Action button, or mu300-linux).'),
				{ danger: true, okText: _('Start Android next') }).then(function(yes) {
				if (!yes) return;
				M.busy(ab, true);
				return M.callAct('os', 'android').then(function(r) {
					M.busy(ab, false);
					if (!r || r.ok !== 1) M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
					else M.toast(_('Saved'), { type: 'success' });
					return self.reload();
				}, function() { M.busy(ab, false); });
			});
		});
		kc.appendChild(E('div', { 'style': 'margin-top:10px' }, [ ab ]));
		cards.push(kc);

		/* ---- what an update keeps */
		cards.push(E('section', { 'class': 'mud-card', 'id': 'mud-upd-kept', 'style': 'margin-top:14px' }, [
			E('h3', {}, _('What an update keeps')),
			E('div', {}, [ _('An update puts the system of the new release in place and carries over what is yours: the settings, the accounts and their passwords, the device\'s keys and the home directories. Packages you installed yourself are written down and installed again at the first start, once the device is online. The previous version stays on the device until you remove it, so you can go back.') + ' ',
				E('a', { 'href': WIKI, 'target': '_blank', 'rel': 'noopener noreferrer' }, _('The wiki\'s Updating page lists exactly what is kept.')) ])
		]));

		dom.content(this.root, cards);
		this.paintJob(job);
		if (running) this.follow();
	},

	/* the upload: room is made and checked first (upload_begin), then the file goes to the one path the panel may
	 * write, through LuCI's own upload endpoint (cgi-io, with this session), then the backend looks at it */
	upload: function(btn, pick, meter) {
		var self = this, file = pick.files && pick.files[0];
		if (!file) { M.toast(_('Choose a file first'), { type: 'error' }); return; }
		M.busy(btn, true);
		return M.callUpdateAct('upload_begin', '', '', '', '', String(file.size), '', '').then(function(r) {
			if (!r || r.ok !== 1) throw new Error(M.errText(r));
			return new Promise(function(resolve, reject) {
				var xhr = new XMLHttpRequest(), fd = new FormData();
				fd.append('sessionid', L.env.sessionid);
				fd.append('filename', r.path);
				fd.append('filedata', file);
				xhr.open('POST', (L.env.cgi_base || '/cgi-bin') + UPLOAD_URL);
				xhr.upload.onprogress = function(ev) {
					if (ev.lengthComputable) meter.textContent = _('Uploading: %s of %s').format(M.fmtBytes(ev.loaded), M.fmtBytes(ev.total));
				};
				xhr.onload = function() {
					var res = null;
					try { res = JSON.parse(xhr.responseText); } catch (e) { res = null; }
					if (xhr.status === 200 && res && !res.failure && res.size != null) resolve(res);
					else reject(new Error((res && res.message) || 'HTTP ' + xhr.status));
				};
				xhr.onerror = function() { reject(new Error(_('Management connection lost'))); };
				xhr.send(fd);
			});
		}).then(function() {
			M.busy(btn, false);
			return self.act(btn, { op: 'upload_done' }).then(function(r) { if (r && r.started) self.follow(); });
		}, function(e) {
			M.busy(btn, false);
			meter.textContent = '';
			M.toast(_('Upload failed: %s').format(e && e.message ? e.message : ''), { type: 'error', timeout: 8000 });
		});
	}
});
