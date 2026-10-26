'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > Backup of settings: the user's settings as one file (the hotspot, mobile data, the locks, the VPN, SMS
 * forwarding, the LAN, the host name and time zone, SSH keys, the password if asked) and back. The backend is
 * mu300dash backup_export/backup_import/backup_job, which hand the request to mu300-api (mu300-backup, FINDINGS 155):
 * every value of a file is checked by the rules of the setter that takes it, a preview shows what would change, and
 * the restore runs as a task the page follows. The file is made in the browser from the reply and never stored on
 * the device. */
return view.extend({
	render: function() {
		M.injectCss();
		var self = this;
		this.root = E('div', { 'class': 'mud' });
		this.file = null;

		/* ---- export */
		var pass1 = E('input', { 'type': 'password', 'class': 'cbi-input-text', 'id': 'mud-bk-pass1', 'autocomplete': 'new-password',
			'placeholder': _('Passphrase (8 characters or more)') });
		var pass2 = E('input', { 'type': 'password', 'class': 'cbi-input-text', 'id': 'mud-bk-pass2', 'autocomplete': 'new-password',
			'placeholder': _('The passphrase again') });
		var withpw = E('input', { 'type': 'checkbox', 'id': 'mud-bk-withpw' });
		var ebtn = E('button', { 'class': 'mud-btn', 'id': 'mud-bk-export' }, _('Download the backup'));
		ebtn.addEventListener('click', function() { self.doExport(ebtn, pass1, pass2, withpw); });
		var exp = E('section', { 'class': 'mud-card', 'id': 'mud-bk-exp' }, [
			E('h3', {}, _('Back up the settings')),
			E('div', { 'class': 'mud-note' }, _('One file with the hotspot, mobile data and the APN, the network locks, the VPN profiles and settings, SMS forwarding, the LAN address, the host name, the time zone and the SSH keys. It holds passwords: with a passphrase it is encrypted, and only that passphrase opens it again.')),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [ pass1, pass2 ]),
			E('label', { 'style': 'display:flex;gap:6px;align-items:center;margin:6px 0' }, [ withpw, _('Include the login password') ]),
			E('div', { 'style': 'margin:10px 0' }, [ ebtn ])
		]);

		/* ---- import */
		var pick = E('input', { 'type': 'file', 'id': 'mud-bk-pick', 'accept': '.json,.enc,application/json,text/plain' });
		var ipass = E('input', { 'type': 'password', 'class': 'cbi-input-text', 'id': 'mud-bk-ipass', 'autocomplete': 'off',
			'placeholder': _('Passphrase, if the file is encrypted') });
		var force = E('input', { 'type': 'checkbox', 'id': 'mud-bk-force' });
		var skipPw = E('input', { 'type': 'checkbox', 'id': 'mud-bk-skippw', 'checked': '' });
		var pbtn = E('button', { 'class': 'mud-btn', 'id': 'mud-bk-preview' }, _('Show what would change'));
		var rbtn = E('button', { 'class': 'mud-btn warn', 'id': 'mud-bk-restore', 'disabled': '' }, _('Restore'));
		this.out = E('pre', { 'id': 'mud-bk-out', 'style': 'white-space:pre-wrap;max-height:28em;overflow:auto' }, '');
		pick.addEventListener('change', function() {
			rbtn.setAttribute('disabled', '');
			var f = pick.files && pick.files[0];
			if (!f) return;
			if (f.size > 4194304) { M.toast(_('The file is larger than 4 MiB'), { type: 'error' }); return; }
			var rd = new FileReader();
			rd.onload = function() { self.file = String(rd.result || ''); };
			rd.readAsText(f);
		});
		pbtn.addEventListener('click', function() { self.doImport(pbtn, rbtn, ipass, force, skipPw, true); });
		rbtn.addEventListener('click', function() {
			M.confirmBox(_('Restore'), _('The settings of the file replace these. The hotspot, mobile data or the LAN may restart, and this page may lose its connection for a moment.'), {}).then(function(yes) {
				if (yes) self.doImport(rbtn, rbtn, ipass, force, skipPw, false);
			});
		});
		var imp = E('section', { 'class': 'mud-card', 'id': 'mud-bk-imp', 'style': 'margin-top:14px' }, [
			E('h3', {}, _('Restore from a backup')),
			E('div', { 'class': 'mud-note' }, _('Every value of the file is checked first; one that is refused and nothing is changed. What is the same is left alone, and no VPN profile, subscription or SSH key of this device is removed.')),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' }, [ pick, ipass ]),
			E('label', { 'style': 'display:flex;gap:6px;align-items:center;margin:6px 0' }, [ skipPw, _('Keep this device\'s login password') ]),
			E('label', { 'style': 'display:flex;gap:6px;align-items:center;margin:6px 0' }, [ force, _('Take a file of another device model') ]),
			E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0' }, [ pbtn, rbtn ]),
			this.out
		]);
		this.rbtn = rbtn;
		dom.content(this.root, [ exp, imp ]);
		return this.root;
	},

	doExport: function(btn, pass1, pass2, withpw) {
		var p = pass1.value;
		if (p !== pass2.value) { M.toast(_('The two passphrases differ'), { type: 'error' }); return; }
		if (p && p.length < 8) { M.toast(_('The passphrase needs 8 characters or more'), { type: 'error' }); return; }
		M.busy(btn, true);
		return M.callBackupExport(p || undefined, withpw.checked ? true : undefined).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1 || typeof r.file !== 'string') {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 8000 });
				return;
			}
			var a = E('a', { 'href': URL.createObjectURL(new Blob([ r.file ], { type: r.encrypted ? 'text/plain' : 'application/json' })),
				'download': r.name || 'mu300-backup.json' });
			document.body.appendChild(a);
			a.click();
			setTimeout(function() { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
			M.toast(r.encrypted ? _('Saved (encrypted)') : _('Saved. The file is not encrypted: it holds passwords, keep it safe.'),
				{ type: 'success', timeout: 8000 });
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	doImport: function(btn, rbtn, ipass, force, skipPw, dry) {
		var self = this;
		if (!this.file) { M.toast(_('Choose a backup file first'), { type: 'error' }); return; }
		M.busy(btn, true);
		return M.callBackupImport(this.file, ipass.value || undefined, dry ? true : undefined, force.checked ? true : undefined,
				skipPw.checked ? 'password' : undefined).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 8000 });
				return;
			}
			if (dry) {
				dom.content(self.out, [ r.text || '' ]);
				if (r.valid) rbtn.removeAttribute('disabled'); else rbtn.setAttribute('disabled', '');
				return;
			}
			rbtn.setAttribute('disabled', '');
			dom.content(self.out, [ _('Restoring…') ]);
			self.follow();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	/* the restore's task, until it ends (the hotspot or the LAN restarting may drop a call or two: keep asking) */
	follow: function() {
		var self = this, misses = 0;
		var tick = function() {
			return M.callBackupJob().then(function(r) {
				var j = r && r.job;
				if (!j || j.kind !== 'backup-import') return;
				dom.content(self.out, [ j.text || '' ]);
				if (j.state === 'running') { setTimeout(tick, 2000); return; }
				M.toast(j.rc === 0 ? _('Restored') : _('The restore did not finish: see the lines above'),
					{ type: j.rc === 0 ? 'success' : 'error', timeout: 8000 });
			}, function() {
				if (++misses < 30) setTimeout(tick, 3000);
			});
		};
		setTimeout(tick, 1500);
	}
});
