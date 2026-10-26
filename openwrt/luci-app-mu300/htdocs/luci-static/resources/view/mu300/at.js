'use strict';
'require view';
'require mu300.common as M';

/* AT terminal -- in the style of a serial debugging tool: a dark output pane, lines highlighted by kind
 * (command/OK/error/data), the time each command took, up/down through the local history, the session history on
 * the right to click and reuse, common commands in groups.
 * Every command goes rpcd -> mu300-at over the nr1 channel (the collectors are on nr6/nr7, out of the way); the
 * guards are in the backend: it must start with AT, no ";" chaining, AT+SPENGMD=0,1,0 refused outright (it wedges
 * the AT port until a reboot). A command that takes minutes (a network or frequency scan) or changes the radio, the
 * SIM or a setting the modem keeps comes back as "confirm" with its kind: the page says what it does and sends it
 * again only when asked to. A confirmed scan runs in the background (at_job), its reply printed when it is in. */

/* What each kind of command (the backend's at_class) does beyond answering */
var WARN = {
	scan: _('This is a network or frequency scan: it takes one to three minutes, holds the modem\'s AT channel all that time, and mobile data may pause meanwhile. It runs in the background; the reply appears here when it is done.'),
	nv: _('This changes a network mode, band or cell setting the modem stores: it stays in force after a reboot, and in Android too, where a network that does not match it shows as No Service. The Locks page shows these settings and resets them.'),
	radio: _('This switches the radio, the data connection or the modem itself: mobile data drops until it is restored, and this page may lose its connection for a while.'),
	apn: _('This changes the data profiles (APNs) stored in the modem, which mobile data and Android use.'),
	sim: _('This changes the SIM\'s PIN lock or enters a PIN: wrong PINs block the SIM until its PUK is entered.')
};
var JOB_POLL_MS = 3000;

var GROUPS = [
	[ _('Basic'), [ 'AT', 'AT+CFUN?', 'AT+CPIN?', 'AT+CGMR', 'AT+CCID', 'AT+CIMI', 'AT+CGSN', 'AT+CNUM' ] ],
	[ _('Registration/signal'), [ 'AT+CSQ', 'AT+CESQ', 'AT+CEREG?', 'AT+C5GREG?', 'AT+COPS?', 'AT+CGATT?', 'AT+CGACT?' ] ],
	[ _('Bearer'), [ 'AT+CGDCONT?', 'AT+CGPADDR=1', 'AT+CGCONTRDP=1', 'AT+CGEQOSRDP=1' ] ],
	[ _('Engineering mode'), [ 'AT+SPENGMD=0,6,0', 'AT+SPENGMD=0,14,1', 'AT+SPENGMD=0,6,6', 'AT+SPQ5GNCELLEX', 'AT+SPENDC?', 'AT+SPTESTMODE?', 'AT+SP5GRAN?' ] ]
];

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<div class="mud-sec" style="margin-top:0">
  <h3>${_('AT terminal')}</h3>
  <div style="display:flex;flex-direction:column;gap:8px">
    <div style="display:flex;gap:8px">
      <input id="mud-at-cmd" spellcheck="false" autocomplete="off"
        style="flex:1;min-width:0;padding:8px 12px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font-family:var(--font-mono,monospace);font-size:.85rem"
        placeholder="${_('AT command (↑↓ history, Enter to send)')}"/>
      <button class="mud-btn" id="mud-at-go" style="padding:8px 18px">${_('Send')}</button>
      <button class="mud-btn" id="mud-at-clear" style="padding:8px 12px">${_('Clear screen')}</button>
    </div>
    <div class="mud-at-grid">
      <div class="mud-term" id="mud-at-out"><span class="ln-meta">${_('Ready.')}
</span></div>
      <div>
        <div class="mud-note" style="margin:0 0 4px">${_('Session history (click to reuse)')}</div>
        <div class="mud-scroll" id="mud-at-hist" style="font-family:var(--font-mono,monospace);font-size:.74rem"></div>
      </div>
    </div>
    <div>
      ${GROUPS.map(function(g) {
        return '<div class="mud-note" style="margin:4px 0 2px">' + g[0] + '</div>' +
          '<div class="mud-chiprow" style="margin-top:2px">' +
          g[1].map(function(c) { return '<span class="mud-chip">' + c + '</span>'; }).join('') + '</div>';
      }).join('')}
    </div>
  </div>
</div>`;
		this.wire(root);
		this.loadHist();
		/* a scan started before this page was opened (or before a reload) is still followed */
		this.watchJob(true);
		return root;
	},

	/* Follow the background job (a confirmed scan) until it ends, then print its reply - once per job. quiet: say
	 * nothing when there is none (the page just opened). */
	watchJob: function(quiet) {
		var self = this;
		if (this._jobWatch) return;
		this._jobWatch = true;
		var announced = !quiet;
		var tick = function() {
			L.resolveDefault(M.callAtJob(), null).then(function(j) {
				j = j || {};
				if (j.running) {
					if (!announced) {
						self.line('ln-cmd', '> ' + (j.cmd || ''));
						self.line('ln-meta', _('Running in the background (at most %d s); the reply appears here when it is done…').format(j.limit || 180));
						announced = true;
					}
					return setTimeout(tick, JOB_POLL_MS);
				}
				self._jobWatch = false;
				if (!j.done || !announced) return;
				if (j.ok) self.reply(j.reply);
				else self.line('ln-err', _('The modem gave no final answer in time') + (j.reply ? ': ' + j.reply : ''));
				self.line('ln-meta', '—— ' + _('%d s in the background').format(j.elapsed || 0));
			}, function() { self._jobWatch = false; });
		};
		tick();
	},

	wire: function(root) {
		var self = this;
		this.Q = function(id) { return root.querySelector('#mud-' + id); };
		this.localHist = [];
		this.histIdx = -1;

		var line = function(cls, text) {
			var el = self.Q('at-out');
			var span = document.createElement('span');
			span.className = cls;
			span.textContent = text + '\n';
			el.appendChild(span);
			el.scrollTop = el.scrollHeight;
		};

		var reply = function(text) {
			(text || _('(no output)')).split('\n').forEach(function(l) {
				if (/^OK$/.test(l)) line('ln-ok', l);
				else if (/ERROR|^NO CARRIER/.test(l)) line('ln-err', l);
				else if (l) line('ln-data', l);
			});
		};
		this.line = line;
		this.reply = reply;

		/* one command; confirm: the user has read the warning for it */
		var run = function(cmd, confirm) {
			var t0 = Date.now();
			L.resolveDefault(M.callAt(cmd, confirm ? '1' : '')).then(function(r) {
				r = r || {};
				var ms = Date.now() - t0;
				if (r.confirm && !confirm) {
					var why = WARN[r.kind] || _('This command changes the modem\'s state.');
					line('ln-meta', _('Not sent yet: waiting for confirmation'));
					return M.confirmBox(_('Send this AT command?'), cmd + '\n\n' + why,
						{ danger: true, okText: _('Send anyway') }).then(function(yes) {
						if (yes) run(cmd, true);
						else line('ln-meta', _('Not sent'));
					});
				}
				if (r.ok && r.job) {
					line('ln-meta', _('Running in the background (at most %d s); the reply appears here when it is done…').format(r.limit || 180));
					self.loadHist();
					self.watchJob();
				} else if (r.ok) {
					reply(r.reply);
					line('ln-meta', '—— ' + ms + ' ms');
					self.loadHist();
				} else {
					/* a busy channel or a lock apply in flight: the backend's sentence says so (and that it was not sent);
					 * it is a whole sentence already, so nothing goes in front of it */
					line('ln-err', M.errText(r));
					if (r.job) self.watchJob();
				}
			}, function() { line('ln-err', _('Request failed')); });
		};

		this.send = function() {
			var cmd = (self.Q('at-cmd').value || '').trim();
			if (!cmd) return;
			self.localHist.push(cmd);
			self.histIdx = self.localHist.length;
			line('ln-cmd', '> ' + cmd);
			run(cmd, false);
			self.Q('at-cmd').value = '';
		};

		this.Q('at-go').onclick = function() { self.send(); };
		this.Q('at-clear').onclick = function() { self.Q('at-out').innerHTML = ''; };
		this.Q('at-cmd').addEventListener('keydown', function(ev) {
			if (ev.key == 'Enter') { ev.preventDefault(); self.send(); }
			else if (ev.key == 'ArrowUp') {
				ev.preventDefault();
				if (self.histIdx > 0) { self.histIdx--; self.Q('at-cmd').value = self.localHist[self.histIdx] || ''; }
			} else if (ev.key == 'ArrowDown') {
				ev.preventDefault();
				if (self.histIdx < self.localHist.length - 1) { self.histIdx++; self.Q('at-cmd').value = self.localHist[self.histIdx] || ''; }
				else { self.histIdx = self.localHist.length; self.Q('at-cmd').value = ''; }
			}
		});
		root.querySelectorAll('.mud-chip').forEach(function(ch) {
			ch.onclick = function() { self.Q('at-cmd').value = ch.textContent; self.send(); };
		});
		this.Q('at-hist').addEventListener('click', function(ev) {
			if (ev.target && ev.target.getAttribute && ev.target.getAttribute('data-cmd')) {
				self.Q('at-cmd').value = ev.target.getAttribute('data-cmd');
				self.send();
			}
		});
	},

	loadHist: function() {
		var self = this;
		L.resolveDefault(M.callAtHist()).then(function(r) {
			r = r || {};
			var h = (r.history || '').split('\n').filter(Boolean).slice().reverse();
			self.Q('at-hist').innerHTML = h.length
				? h.map(function(l) {
					var cmd = l.replace(/^[0-9-]+ [0-9:]+ /, '');
					return '<div style="padding:2px 4px;border-radius:6px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" data-cmd="' + M.esc(cmd) + '" title="' + M.esc(cmd) + '">' + M.esc(cmd) + '</div>';
				}).join('')
				: '<div class="mud-note">' + _('(empty)') + '</div>';
		});
	}
});
