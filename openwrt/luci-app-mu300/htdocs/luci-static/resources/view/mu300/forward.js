'use strict';
'require view';
'require dom';
'require poll';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* Cellular > SMS forwarding: received messages sent on to a webhook, a Telegram chat, an e-mail address (when the image
 * has msmtp) or another phone. The backend is mu300dash forward_get/forward_set/forward_test/forward_log_clear
 * (unisoc-modem/forward, mu300-sms-forward), driven by the SMS pool: nothing here asks the modem anything.
 * The secrets (webhook address and headers, bot token, mail password) are never sent back: an empty field keeps the
 * saved one, "Remove the saved value" deletes it. The webhook's custom body is a template like the message template:
 * it is sent back to be edited, and an empty one means the standard body. So is the answer check (key=value the
 * service's answer must have): the device compares it, and the log gets its verdict, never the answer. */

var DEFAULT_TEMPLATE = '{text}\n-- {sender}, {time} ({device})';

return view.extend({
	/* the page has its own Save button: no LuCI footer (Save & Apply would save nothing here) */
	handleSaveApply: null,
	handleSave: null,
	handleReset: null,

	load:function() { return L.resolveDefault(M.callFwdGet(), {}); },

	render: function(st) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st || {});
		var self = this;
		/* the log and the queue every 5 s (file reads on the device); the form is left alone */
		poll.add(function() {
			return L.resolveDefault(M.callFwdGet(), null).then(function(r) { if (r && r.ok) self.paintLog(r); });
		}, 5);
		return this.root;
	},

	field: function(label, input, note) {
		return E('div', { 'class': 'mud-fwd-f', 'style': 'margin:8px 0' }, [
			E('label', { 'style': 'display:block;font-size:.8rem;margin-bottom:3px' }, [ label ]),
			input,
			note ? E('div', { 'class': 'mud-note', 'style': 'margin-top:3px' }, [ note ]) : ''
		]);
	},

	text: function(id, value, opts) {
		opts = opts || {};
		return E('input', { 'type': opts.type || 'text', 'class': 'cbi-input-text', 'id': 'mud-fwd-' + id,
			'value': value == null ? '' : String(value), 'placeholder': opts.placeholder || '', 'autocomplete': 'off',
			'spellcheck': 'false', 'style': 'width:100%;max-width:520px', 'disabled': opts.disabled ? '' : null });
	},

	area: function(id, value, rows, placeholder) {
		var a = E('textarea', { 'class': 'cbi-input-textarea', 'id': 'mud-fwd-' + id, 'rows': String(rows),
			'spellcheck': 'false', 'placeholder': placeholder || '', 'style': 'width:100%;max-width:520px' });
		a.value = value || '';
		return a;
	},

	check: function(id, on, label, disabled) {
		return E('label', { 'style': 'display:flex;gap:8px;align-items:center;margin:6px 0' }, [
			E('input', { 'type': 'checkbox', 'id': 'mud-fwd-' + id, 'checked': on ? '' : null, 'disabled': disabled ? '' : null }),
			E('span', {}, [ label ])
		]);
	},

	/* a secret: an empty field keeps the saved value; when one is saved, a box to remove it */
	secret: function(id, isSet, hint, label, opts) {
		opts = opts || {};
		var input = opts.area ? this.area(id, '', 3, isSet ? _('Leave empty to keep the saved value') : opts.placeholder)
			: this.text(id, '', { type: opts.password ? 'password' : 'text', disabled: opts.disabled,
				placeholder: isSet ? _('Leave empty to keep the saved value') : (opts.placeholder || '') });
		var parts = [ input ];
		if (isSet) {
			if (hint) parts.push(E('div', { 'class': 'mud-note', 'style': 'margin-top:3px' }, [ _('Saved: %s').format(hint) ]));
			parts.push(this.check(id + '-clear', false, _('Remove the saved value'), opts.disabled));
		}
		return this.field(label, E('div', {}, parts));
	},

	val: function(id) { var el = this.root.querySelector('#mud-fwd-' + id); return el ? el.value : ''; },
	on: function(id) { var el = this.root.querySelector('#mud-fwd-' + id); return el && el.checked ? '1' : '0'; },

	paint: function(st) {
		var self = this;
		if (!st.ok) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, _('SMS forwarding')),
				E('div', { 'class': 'mud-note' }, [ M.errText(st) ])
			]));
			return;
		}
		var mail = !!st.email_available;
		var fmt = (st.webhook_format === 'form' || st.webhook_format === 'text') ? st.webhook_format : 'json';

		/* what the service's answer must have (mu300-sms-forward answer_verdict); the presets only fill the field in */
		var expect = this.text('webhook_expect', st.webhook_expect, { placeholder: 'errcode=0' });
		var preset = E('select', { 'class': 'cbi-input-select', 'id': 'mud-fwd-webhook_expect_preset',
			'style': 'display:block;margin-top:4px' }, [
			E('option', { 'value': '' }, [ _('Presets') ]),
			E('option', { 'value': 'errcode=0' }, [ 'DingTalk, WeCom: errcode=0' ]),
			E('option', { 'value': 'code=0' }, [ 'Feishu, Lark: code=0' ]),
			E('option', { 'value': 'code=200' }, [ 'Bark: code=200' ]),
			E('option', { 'value': 'ok=true' }, [ 'Slack API, Telegram API: ok=true' ])
		]);
		preset.addEventListener('change', function() {
			if (preset.value) expect.value = preset.value;
			preset.value = '';
		});

		/* the body a chat service takes (#259): a preset fills the custom body in, sets the type to JSON and, when the
		 * answer check is empty, the check that service's answer needs (the services answer HTTP 200 to a body they
		 * refuse). Nothing else: the body stays the user's to edit. The placeholder is no service's format in
		 * particular (it was Feishu's, which WeCom refuses with errcode 40008 behind an HTTP 200). */
		var format = E('select', { 'class': 'cbi-input-select', 'id': 'mud-fwd-webhook_format' }, [
			E('option', { 'value': 'json', 'selected': fmt === 'json' ? '' : null }, [ 'JSON' ]),
			E('option', { 'value': 'form', 'selected': fmt === 'form' ? '' : null }, [ _('Form (URL-encoded)') ]),
			E('option', { 'value': 'text', 'selected': fmt === 'text' ? '' : null }, [ _('Plain text') ])
		]);
		var body = this.area('webhook_body', st.webhook_body, 4, '{"text":"{message}"}');
		var BODIES = {
			wecom: [ '{"msgtype":"text","text":{"content":"{message}"}}', 'errcode=0' ],
			feishu: [ '{"msg_type":"text","content":{"text":"{message}"}}', 'code=0' ]
		};
		var bodyPreset = E('select', { 'class': 'cbi-input-select', 'id': 'mud-fwd-webhook_body_preset',
			'style': 'display:block;margin-top:4px' }, [
			E('option', { 'value': '' }, [ _('Presets') ]),
			E('option', { 'value': 'wecom' }, [ 'WeCom, DingTalk' ]),
			E('option', { 'value': 'feishu' }, [ 'Feishu, Lark' ])
		]);
		bodyPreset.addEventListener('change', function() {
			var b = Object.prototype.hasOwnProperty.call(BODIES, bodyPreset.value) ? BODIES[bodyPreset.value] : null;
			if (b) {
				body.value = b[0];
				format.value = 'json';
				if (!expect.value.trim()) expect.value = b[1];
			}
			bodyPreset.value = '';
		});

		var general = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-general' }, [
			E('h3', {}, _('SMS forwarding')),
			E('div', { 'class': 'mud-note' }, [ _('Received messages are sent on as they reach the SMS pool. A failed delivery is retried after 30 s, then after twice as long each time (an hour at most), ten times in all.') ]),
			this.check('enabled', st.enabled, _('Forward received messages')),
			this.field(_('Message template'), this.area('template', st.template || DEFAULT_TEMPLATE, 3),
				_('Placeholders: {sender}, {time}, {text}, {device}')),
			this.field(_('Only these senders'), this.area('allow', st.allow, 2),
				_('One per line; empty: every sender. A * at the end matches the start of a sender.')),
			this.field(_('Never these senders'), this.area('deny', st.deny, 2)),
			this.field(_('Only messages containing one of these words'), this.area('keywords', st.keywords, 2),
				_('One per line; empty: every message.'))
		]);

		var webhook = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-webhook' }, [
			E('h3', {}, _('Webhook')),
			this.check('webhook', st.webhook, _('Enabled')),
			this.secret('webhook_url', st.webhook_url_set, st.webhook_url_hint, _('Address (http:// or https://)'),
				{ placeholder: 'https://example.com/sms' }),
			this.field(_('Body'), format, _('Fields: sender, time, text, device, message (the template filled in)')),
			/* the device fills this in (mu300-sms-forward fill): a template, not a secret, so it comes back */
			this.field(_('Custom body'), E('div', {}, [ body, bodyPreset ]),
				_('Empty: the standard fields (plain text: the message). Placeholders: {sender}, {time}, {text}, {device}, {message}. Each value is escaped for the chosen type. This body is shown again here: keep secrets in the address or a header.')),
			this.secret('webhook_headers', st.webhook_headers_set, st.webhook_header_names,
				_('Extra headers (one "Name: value" per line)'), { area: true, placeholder: 'Authorization: Bearer ...' }),
			this.field(_('The answer must contain'), E('div', {}, [ expect, preset ]),
				_('Empty: every HTTP 2xx answer counts as delivered. With key=value (for example errcode=0), the answer must also be a JSON object that has this value under this key at its top level; otherwise the delivery fails. Only the result of the check is logged, never the answer.'))
		]);

		var telegram = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-telegram' }, [
			E('h3', {}, [ 'Telegram' ]),
			this.check('telegram', st.telegram, _('Enabled')),
			this.secret('telegram_token', st.telegram_token_set, st.telegram_token_hint, _('Bot token'),
				{ password: true, placeholder: '123456789:AA...' }),
			this.field(_('Chat ID (a number, or @channel)'), this.text('telegram_chat', st.telegram_chat))
		]);

		var email = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-email' }, [
			E('h3', {}, _('E-mail')),
			mail ? '' : E('div', { 'class': 'mud-note' }, [ _('E-mail needs msmtp, which this image does not have') ]),
			this.check('email', st.email && mail, _('Enabled'), !mail),
			this.field(_('Mail server'), this.text('email_host', st.email_host, { disabled: !mail, placeholder: 'smtp.example.com' })),
			this.field(_('Port'), this.text('email_port', st.email_port || '587', { disabled: !mail })),
			this.field(_('Security'), E('select', { 'class': 'cbi-input-select', 'id': 'mud-fwd-email_tls', 'disabled': mail ? null : '' },
				[ [ 'starttls', 'STARTTLS' ], [ 'tls', 'TLS' ], [ 'off', _('None') ] ].map(function(o) {
					return E('option', { 'value': o[0], 'selected': (st.email_tls || 'starttls') === o[0] ? '' : null }, [ o[1] ]);
				}))),
			this.field(_('User name'), this.text('email_user', st.email_user, { disabled: !mail })),
			this.secret('email_password', st.email_password_set, '', _('Password'), { password: true, disabled: !mail }),
			this.field(_('From'), this.text('email_from', st.email_from, { disabled: !mail })),
			this.field(_('To'), this.text('email_to', st.email_to, { disabled: !mail }))
		]);

		var sms = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-sms' }, [
			E('h3', {}, _('SMS to another phone')),
			this.check('sms', st.sms, _('Enabled')),
			this.field(_('Phone number'), this.text('sms_number', st.sms_number, { placeholder: '+90...' }),
				_('Sent as one SMS: a longer message is cut short.'))
		]);

		var save = E('button', { 'class': 'mud-btn on', 'id': 'mud-fwd-save' }, [ _('Save') ]);
		save.addEventListener('click', function() { self.save(save); });
		var test = E('button', { 'class': 'mud-btn', 'id': 'mud-fwd-test' }, [ _('Send test') ]);
		test.addEventListener('click', function() { self.test(test); });
		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin:10px 0' }, [ save, test ]);

		this.logBox = E('section', { 'class': 'mud-card', 'id': 'mud-fwd-card-log' });
		dom.content(this.root, [
			E('div', { 'class': 'mud-grid' }, [ general, webhook, telegram, email, sms ]),
			actions,
			E('div', { 'class': 'mud-note' }, [ _('Tokens, passwords and the webhook address and headers stay on the device and are never shown again.') ]),
			this.logBox
		]);
		this.paintLog(st);
	},

	paintLog: function(st) {
		if (!this.logBox) return;
		var targets = { webhook: _('Webhook'), telegram: 'Telegram', email: _('E-mail'), sms: _('SMS to another phone') };
		var results = { sent: _('Delivered'), retry: _('Will retry'), failed: _('Failed'), skipped: _('Filtered out'),
			dropped: _('Dropped') };
		var log = st.log || [], self = this;
		var clear = E('button', { 'class': 'mud-btn', 'id': 'mud-fwd-log-clear', 'style': 'margin-top:8px' }, [ _('Clear') ]);
		clear.addEventListener('click', function() { self.clearLog(clear); });
		var rows = log.map(function(e) {
			var d = new Date((e.time || 0) * 1000);
			return E('tr', {}, [
				E('td', {}, [ d.toLocaleString() ]),
				E('td', {}, [ e.id === 'test' ? _('Test') : '#' + e.id ]),
				E('td', {}, [ targets[e.target] || '-' ]),
				E('td', {}, [ results[e.result] || e.result || '' ]),
				E('td', { 'style': 'text-align:left;word-break:break-word' }, [ e.detail || '' ])
			]);
		});
		dom.content(this.logBox, [
			E('h3', {}, _('Delivery log')),
			E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ _('Waiting to be delivered') ]),
				E('span', { 'class': 'mud-v', 'id': 'mud-fwd-queued' }, [ String(st.queued || 0) ]) ]),
			log.length ? E('div', { 'style': 'overflow-x:auto' }, E('table', { 'class': 'mud-table' }, [
				E('tr', {}, [ E('th', {}, [ _('Time') ]), E('th', {}, [ _('Message') ]), E('th', {}, [ _('Target') ]),
					E('th', {}, [ _('Result') ]), E('th', { 'style': 'text-align:left' }, [ _('Detail') ]) ])
			].concat(rows))) : E('div', { 'class': 'mud-note' }, [ _('No deliveries yet') ]),
			log.length ? clear : ''
		]);
	},

	/* the log is in RAM on the device (the last 100 lines); the queue is not touched */
	clearLog: function(btn) {
		var self = this;
		M.busy(btn, true);
		return M.callFwdLogClear().then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			return L.resolveDefault(M.callFwdGet(), null).then(function(st) { if (st && st.ok) self.paintLog(st); });
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	save: function(btn) {
		var self = this, clear = [];
		[ 'webhook_url', 'webhook_headers', 'telegram_token', 'email_password' ].forEach(function(k) {
			if (self.on(k + '-clear') === '1') clear.push(k);
		});
		M.busy(btn, true);
		return M.callFwdSet(this.on('enabled'), this.val('template'), this.val('allow'), this.val('deny'),
			this.val('keywords'), clear.join(' '), this.on('webhook'), this.val('webhook_url').trim(),
			this.val('webhook_format'), this.val('webhook_headers'), this.on('telegram'), this.val('telegram_token').trim(),
			this.val('telegram_chat').trim(), this.on('email'), this.val('email_host').trim(), this.val('email_port').trim(),
			this.val('email_tls'), this.val('email_user').trim(), this.val('email_password'), this.val('email_from').trim(),
			this.val('email_to').trim(), this.on('sms'), this.val('sms_number').trim(), this.val('webhook_body'),
			this.val('webhook_expect').trim()).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			return L.resolveDefault(M.callFwdGet(), {}).then(function(st) { self.paint(st || {}); });
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	/* the saved settings are tested, not the form: save first */
	test: function(btn) {
		var self = this;
		M.busy(btn, true);
		return M.callFwdTest().then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Test started with the saved settings; the result appears in the delivery log.'), { type: 'info', timeout: 6000 });
			setTimeout(function() {
				L.resolveDefault(M.callFwdGet(), null).then(function(st) { if (st && st.ok) self.paintLog(st); });
			}, 3000);
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	}
});
