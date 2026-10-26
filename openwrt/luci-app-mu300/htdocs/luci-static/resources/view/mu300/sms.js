'use strict';
'require view';
'require poll';
'require mu300.common as M';

/* SMS -- a chat-style page. The data is in the device's local pool (mu300-smsd keeps it in sync with the SIM):
 *   left, the conversations (grouped by contact, newest first, an unread badge)
 *   right, the thread: bubbles (received on the left in grey / sent on the right in the brand colour) + times
 *   bottom, the input bar: number + text, Enter sends, Shift+Enter is a new line
 * Opening a conversation fetches each message's full text (sms_show also marks an unread one read). Listing and
 * fetching are plain file reads; sending and the SIM sync go through AT (the sync in the background). One message
 * is deleted with the bin icon on its bubble. */

var MAX_PAGES = 5;   /* pool pages gathered at once (10 messages a page); plain file reads, cheap */
var JOB_POLL_MS = 1000;   /* how often a send in the backend's background is asked after (sms_job) */

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<div class="mud-sec" style="margin-top:0">
  <h3>${_('SMS')} <span id="mud-sms-stat" style="font-weight:400"></span></h3>
  <input id="mud-sms-num" placeholder="${_('Recipient: number, e.g. 10086 or +86...')}" spellcheck="false"
    style="width:100%;margin-bottom:8px;padding:7px 11px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222)"/>
  <div class="mud-ctl" style="max-width:460px;margin-bottom:8px">
    <button class="mud-btn" id="mud-sms-refresh">${_('Refresh')}</button>
    <button class="mud-btn" id="mud-sms-sync">${_('Sync from SIM')}</button>
    <button class="mud-btn warn" id="mud-sms-clear">${_('Clear local pool')}</button>
  </div>
  <div class="mud-chat">
    <div class="mud-convs" id="mud-sms-convs"><div class="mud-note">${_('Loading…')}</div></div>
    <div class="mud-thread">
      <div class="mud-msgs" id="mud-sms-msgs"><div class="mud-note" style="margin:8px 2px">${_('Select a conversation on the left, or enter a number below to send.')}</div></div>
      <div class="mud-comp">
        <textarea id="mud-sms-text" placeholder="${_('Message (Enter to send, Shift+Enter for newline)')}" rows="1"></textarea>
        <button class="mud-btn" id="mud-sms-send" style="align-self:flex-end;padding:8px 18px">${_('Send')}</button>
      </div>
    </div>
  </div>
  <div class="mud-note" id="mud-sms-note">${_('Sending uses AT+CMGS (PDU mode); retry if the channel is busy. To delete one message, use the bin icon on its bubble.')}</div>
</div>`;
		this.Q = function(id) { return root.querySelector('#mud-' + id); };
		this.sel = null;          /* the open conversation's peer */
		this.convs = {};          /* peer -> {msgs:[], unread:n} */
		this.wire(root);
		this.reload();
		/* a send started before this page was opened (or before a reload) is still followed */
		this.watchJob(true);
		return root;
	},

	/* The send runs in the backend's background (sms_send starts it, sms_job tells how it went): a send can take
	 * longer than one request may (rpcd ends a call at 30 s), and its result was lost with the call. Ask every
	 * second until it is done, then show the verdict once. quiet: say nothing when there is none (the page just
	 * opened). */
	watchJob: function(quiet) {
		var self = this;
		if (this._jobWatch) return;
		this._jobWatch = true;
		var announced = !quiet;
		var btn = this.Q('sms-send');
		var tick = function() {
			L.resolveDefault(M.callSmsJob(), null).then(function(j) {
				j = j || {};
				if (j.running) {
					if (!announced) { M.busy(btn, true); self.note(_('Sending…'), 'busy'); announced = true; }
					return setTimeout(tick, JOB_POLL_MS);
				}
				self._jobWatch = false;
				M.busy(btn, false);
				if (!j.done || !announced) return;
				self.sent(j);
			}, function() { self._jobWatch = false; M.busy(btn, false); self.note(_('Request failed'), 'error'); });
		};
		tick();
	},

	/* the verdict of a send */
	sent: function(r) {
		if (r.ok) {
			this.note(_('Sent; the list refreshes shortly.'), 'success');
			this.Q('sms-text').value = '';
			setTimeout(L.bind(this.reload, this), 2500);
		} else {
			/* the backend's error is a whole sentence (the busy channel, a refused number, "Sending failed" with
			 * the modem's last line in detail): shown alone, so nothing is said twice */
			this.note(r.error ? M.errText(r) : _('Sending failed'), 'error');
		}
	},

	wire: function(root) {
		var self = this;

		this.Q('sms-refresh').onclick = function() { self.reload(); };
		/* refresh every 5 s (a plain read of the local pool); the open conversation is redrawn by build(), keeping its scroll position */
		poll.add(function() { return self.reload(); }, 5);
		this.Q('sms-sync').onclick = function() {
			var btn = this;
			M.busy(btn, true);
			M.toast(_('Syncing from the SIM in the background (AT+CMGL)…'), { type: 'busy' });
			L.resolveDefault(M.callSmsSync()).then(function() {
				M.busy(btn, false);
				M.toast(_('SIM sync started; the list refreshes in a few seconds.'), { type: 'success' });
				setTimeout(function() { self.reload(); }, 8000);
			});
		};
		this.Q('sms-clear').onclick = function() {
			M.confirmBox(_('Clear local SMS pool'), _('Only local files will be deleted; messages on the SIM remain.'), { danger: true, okText: _('Clear') })
				.then(function(go) {
					if (!go) return;
					L.resolveDefault(M.callSmsDel('all')).then(function() { self.sel = null; self.reload(); });
				});
		};
		this.Q('sms-send').onclick = function() { self.send(); };
		var txt = this.Q('sms-text');
		txt.addEventListener('keydown', function(ev) {
			if (ev.key == 'Enter' && !ev.shiftKey) { ev.preventDefault(); self.send(); }
		});
		txt.addEventListener('input', function() {
			this.style.height = 'auto';
			this.style.height = Math.min(120, this.scrollHeight) + 'px';
		});
	},

	/* Deleting goes through the bin icon at the bubble's top right; a choice box tells local only from local + SIM */
	delMsg: function(id) {
			var self = this;
			M.choiceBox(_('Delete this SMS'), _('Deletion cannot be undone.'),
				[ { label: _('Cancel'), value: null },
				  { label: _('Local only'), value: 'local' },
				  { label: _('Local + SIM'), value: 'sim', danger: true } ])
				.then(function(choice) {
					if (!choice) return;
					L.resolveDefault(M.callSmsDel(id, choice === 'sim')).then(function(r) {
						r = r || {};
						if (!r.ok) { self.note(r.error ? M.errText(r) : _('Delete failed'), 'error'); return; }
						delete self.cache[id];
						self.note(choice === 'sim' ? _('Deleted (local + SIM)') : _('Deleted (local only)'), 'success');
						self.reload();
					});
				});
		},

	/* Notes go to a toast at the top; the line at the bottom of the page is static help only */
	note: function(t, type) { M.toast(t, { type: type || 'info' }); },

	send: function() {
		var self = this;
		var num = (this.Q('sms-num').value || '').trim();
		var text = (this.Q('sms-text').value || '').replace(/\s+$/, '');
		if (!num || !text) { this.note(_('Enter both a number and a message.'), 'error'); return; }
		var btn = this.Q('sms-send');
		M.busy(btn, true);
		this.note(_('Sending…'), 'busy');
		L.resolveDefault(M.callSmsSend(num, text)).then(function(r) {
			r = r || {};
			/* accepted: the send runs in the background, and the verdict comes through sms_job (watchJob) */
			if (r.ok && r.job) { self.watchJob(); return; }
			M.busy(btn, false);
			self.sent(r);
		}, function() { M.busy(btn, false); self.note(_('Request failed'), 'error'); });
	},

	/* gather a few pages of the pool, grouped by contact */
	reload: function() {
		var self = this;
		var all = [], page = 1;
		var step = function() {
			return L.resolveDefault(M.callSmsList(page)).then(function(r) {
				r = r || {};
				if (r.error) {
					self.Q('sms-convs').innerHTML = '<div class="mud-note">' + M.esc(M.errText(r)) + '</div>';
					return;
				}
				all = all.concat(r.msgs || []);
				var pages = r.pages || 1;
				if (page < pages && page < MAX_PAGES) { page++; return step(); }
				self.stat = r;
					self.build(all);
			});
		};
		return step();
	},

	build: function(all) {
		var self = this;
		/* No flicker on the 5 s poll: while the data's signature is unchanged (no new message, no unread flag flipped)
		 * nothing is redrawn -- rebuilding the whole innerHTML and refilling the full texts flickers visibly */
		var sig = (this.stat && this.stat.total) + '|' + all.map(function(m) {
			return m.id + ':' + m.status;
		}).join(',');
		if (sig === this._sig) return;
		this._sig = sig;
		this.convs = {};
		all.forEach(function(m) {
			var peer = m.peer || '?';
			if (!self.convs[peer]) self.convs[peer] = { msgs: [], unread: 0 };
			self.convs[peer].msgs.push(m);
			if (m.status === 'unread') self.convs[peer].unread++;
		});
		var peers = Object.keys(this.convs).sort(function(a, b) {
			var ma = self.convs[a].msgs[0], mb = self.convs[b].msgs[0];
			return (mb && mb.time || '').localeCompare(ma && ma.time || '');
		});
		var st = this.stat || {};
		this.Q('sms-stat').textContent = st.unread
			? _('· %d messages, %d unread · %d conversations').format(st.total || all.length, st.unread, peers.length)
			: _('· %d messages · %d conversations').format(st.total || all.length, peers.length);
		var box = this.Q('sms-convs');
		if (!peers.length) {
			box.innerHTML = '<div class="mud-note" style="margin:6px">' + M.esc(_('The pool is empty. Incoming and sent messages appear here, or select “Sync from SIM”.')) + '</div>';
			return;
		}
		box.innerHTML = peers.map(function(p) {
			var cv = self.convs[p];
			var last = cv.msgs[0];
			return '<div class="mud-conv' + (p === self.sel ? ' sel' : '') + '" data-peer="' + M.esc(p) + '">' +
				'<div class="n"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + M.esc(p) + '</span>' +
				(cv.unread ? '<span class="mud-badge">' + cv.unread + '</span>' : '') + '</div>' +
				'<div class="p">' + M.esc(last.dir === 'mo' ? _('Me: %s').format(last.preview || '') : (last.preview || '')) + '</div>' +
				'<div class="p" style="opacity:.7">' + M.esc(last.time || '') + '</div></div>';
		}).join('');
		box.querySelectorAll('.mud-conv').forEach(function(el) {
			el.onclick = function() { self.open(el.getAttribute('data-peer')); };
		});
		if (this.sel && this.convs[this.sel]) this.open(this.sel);
	},

	/* Open a conversation: draw the bubbles; full texts are cached by id (fetched once, so a redraw does not flash the preview) */
	open: function(peer) {
		var self = this;
		var keepScroll = this.Q('sms-msgs') ? this.Q('sms-msgs').scrollTop : 0;
		this.sel = peer;
		this.cache = this.cache || {};
		this.Q('sms-num').value = peer.replace(/[^+0-9]/g, '');
		this.Q('sms-convs').querySelectorAll('.mud-conv').forEach(function(el) {
			el.className = (el.getAttribute('data-peer') === peer ? 'mud-conv sel' : 'mud-conv');
		});
		var cv = this.convs[peer];
		var msgsEl = this.Q('sms-msgs');
		var readFailed = false;
		msgsEl.innerHTML = '';
		cv.msgs.slice().reverse().forEach(function(m) {   /* old -> new */
			var div = document.createElement('div');
			div.className = 'mud-bub' + (m.dir === 'mo' ? ' out' : '');
			div.setAttribute('data-id', m.id);
			var full = self.cache[m.id];
			div.innerHTML = '<span class="bd">' + M.esc(full || m.preview || '') +
				(!full && m.preview && m.preview.length >= 44 ? '…' : '') + '</span>' +
				'<span class="tm">' + M.esc((m.time || '').split(' ').pop() || '') + '</span>';
			msgsEl.appendChild(div);
			if (!full && m.status === 'unread') div.querySelector('.bd').style.fontWeight = '600';
			msgsEl.scrollTop = keepScroll;
			/* fetch the full text only when not cached (a local pool read); cache it for the next redraw */
			if (!full) {
				L.resolveDefault(M.callSmsShow(m.id)).then(function(r) {
					r = r || {};
					/* a failed read says why, as send and delete do (once per conversation drawn, not once per bubble) */
					if (!r.ok) {
						if (!readFailed) { readFailed = true; self.note(M.errText(r), 'error'); }
						return;
					}
					/* show prints the header + "---" + the body (single newlines, no blank line): splitting at \n\n
					 * gave an empty string -- which used to empty the bubble */
					var parts = (r.text || '').split('\n---\n');
					var body = parts.slice(1).join('\n---\n').trim();
					if (!body) return;
					/* a long message pooled without all its parts (mu300-sms: "incomplete:  2 of 3 parts arrived");
					 * the gaps are "[...]" in the text */
					var inc = /^incomplete: +([0-9]+) of ([0-9]+)/m.exec(parts[0]);
					if (inc) body += '\n' + _('Incomplete: %d of %d parts arrived').format(+inc[1], +inc[2]);
					self.cache[m.id] = body;
					var bd = div.querySelector('.bd');
					if (bd && document.contains(div)) {
						bd.textContent = body;
						bd.style.fontWeight = '';
						bd.classList.add('fadein');   /* fade from the preview to the full text */
					}
				});
			}
			/* the bin button: the bubble's top right, shown on hover (always shown on touch screens through its opacity) */
			var del = document.createElement('button');
			del.className = 'mud-del';
			del.title = _('Delete');
			del.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
				'<path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>' +
				'<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>';
			del.onclick = function(ev) { ev.stopPropagation(); self.delMsg(m.id); };
			div.appendChild(del);
		});
		msgsEl.scrollTop = msgsEl.scrollHeight;
	}
});
