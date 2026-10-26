'use strict';
'require view';
'require mu300.common as M';

return view.extend({
	load: function() { return L.resolveDefault(M.callUsbGet(), {}); },
	render: function(state) {
		M.injectCss();
		var root = document.createElement('div');
		root.className = 'mud';
		root.innerHTML = `
<style>
.mud-device-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.mud-device-field{display:flex;flex-direction:column;gap:7px;margin:12px 0}
.mud-device-field label{font-size:.8rem;color:var(--text-muted,var(--text-light,#777))}
.mud-device-field select{width:100%;min-height:38px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);padding:6px 10px;background:var(--surface,var(--background,#fff));color:var(--text,#222)}
.mud-device-toggle{display:flex;align-items:center;gap:9px;font-size:.82rem;line-height:1.45;cursor:pointer}
.mud-device-toggle input{accent-color:var(--brand,var(--primary,#2f7bf6))}
.mud-device-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.mud-device-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.mud-device-head h3{margin:0;font-size:.85rem}
.mud-device-list{display:grid;gap:8px;margin-top:12px}
.mud-device-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;border:1px solid var(--hairline,var(--border,#ddd));border-radius:var(--radius-base,.5rem)}
.mud-device-item-main{min-width:0;display:flex;align-items:center;gap:10px}
.mud-device-dot{width:9px;height:9px;border-radius:50%;background:var(--text-muted,#888);flex:none}
.mud-device-dot.up{background:var(--success,#2fbf71)}
.mud-device-item-name{font-weight:650;overflow-wrap:anywhere}
.mud-device-item-sub{font-size:.72rem;color:var(--text-muted,var(--text-light,#777))}
@media(max-width:720px){.mud-device-grid{grid-template-columns:1fr}.mud-device-item{align-items:flex-start}.mud-device-item .mud-btn{white-space:nowrap}}
</style>
<div class="mud-device-grid">
 <section class="mud-card">
  <h3>${_('USB role')}</h3>
  <div class="mud-r"><span class="mud-k">${_('Current role')}</span><span class="mud-v" id="mud-usb-role-now">--</span></div>
  <div class="mud-device-field"><label for="mud-usb-role">${_('Switch USB role')}</label>
   <select id="mud-usb-role"><option value="device">${_('Device mode')}</option><option value="host">${_('Host mode')}</option></select></div>
  <label class="mud-device-toggle"><input type="checkbox" id="mud-usb-role-auto">${_('Enable host mode at boot')}</label>
  <div class="mud-device-actions"><button class="mud-btn" id="mud-usb-role-apply">${_('Apply role')}</button></div>
  <div class="mud-note">${_('Host mode disconnects USB networking and serial on this port. F50 has no battery; management may be lost and a USB adapter usually needs a powered hub.')}</div>
 </section>
 <section class="mud-card" id="mud-usb-net-card">
  <h3>${_('USB network mode')}</h3>
  <div class="mud-device-field"><label for="mud-usb-net-mode">${_('Network protocol')}</label>
   <select id="mud-usb-net-mode"><option value="ncm">NCM</option><option value="ecm">ECM</option><option value="rndis">RNDIS</option></select></div>
  <div class="mud-device-field"><label for="mud-usb-net-scope">${_('Apply duration')}</label>
   <select id="mud-usb-net-scope"><option value="once">${_('Next reboot only')}</option><option value="permanent">${_('Permanent')}</option></select></div>
  <label class="mud-device-toggle"><input type="checkbox" id="mud-usb-net-auto">${_('Enable selected protocol')}</label>
  <div class="mud-device-actions"><button class="mud-btn" id="mud-usb-net-apply">${_('Save; apply after reboot')}</button></div>
  <div class="mud-note" id="mud-usb-net-note">${_('NCM is the default. Windows does not natively support ECM; RNDIS changes enumeration. With Enable selected protocol off, only your choice is saved. Next reboot only applies once, then returns to NCM.')}</div>
 </section>
</div>
<section class="mud-card" style="margin-top:14px" id="mud-usb-adapters-card">
 <div class="mud-device-head"><h3>${_('USB adapters')}</h3><button class="mud-btn" id="mud-usb-refresh">${_('Refresh')}</button></div>
 <div class="mud-note">${_('Available only in host mode. Refresh brings discovered USB adapters up; adding to LAN saves the bridge and reloads networking.')}</div>
 <div class="mud-device-list" id="mud-usb-adapters"></div>
</section>
<section class="mud-card" style="margin-top:14px">
 <div class="mud-device-head"><h3>${_('Recent modem operations')}</h3><button class="mud-btn" id="mud-usb-ops-show">${_('Show')}</button></div>
 <div class="mud-note">${_('The control panel\'s recent calls, of this boot and the one before: when, which page call or AT command, how long it took and how it ended (ok, error, busy, killed). No values, replies or messages are kept. Useful when the panel was stuck.')}</div>
 <div id="mud-usb-ops" style="display:none"></div>
</section>`;
		this.root = root;
		this.state = state || {};
		this.wire();
		this.paint();
		return root;
	},
	q: function(id) { return this.root.querySelector('#mud-usb-' + id); },
	paint: function() {
		var s = this.state || {};
		this.q('role-now').textContent = s.role === 'host' ? _('Host mode') : s.role === 'device' ? _('Device mode') : _('Unavailable');
		this.q('role').querySelector('option[value="host"]').disabled = s.host_supported === 0;
		this.q('role').value = s.role === 'host' || s.role_auto ? 'host' : 'device';
		this.q('role-auto').checked = !!s.role_auto;
		this.q('net-mode').value = s.net_mode || 'ncm';
		this.q('net-scope').value = s.net_scope || 'permanent';
		this.q('net-auto').checked = !!s.net_auto;
		this.updateDisabled();
		this.refreshAdapters();
	},
	updateDisabled: function() {
		var host = this.state.role === 'host', hostAuto = this.q('role-auto').checked && this.q('role').value === 'host';
		this.q('role-auto').disabled = this.q('role').value !== 'host';
		if (this.q('role').value !== 'host') this.q('role-auto').checked = false;
		var locked = host || !!this.state.role_auto || hostAuto;
		[ 'net-mode', 'net-scope', 'net-auto', 'net-apply' ].forEach(function(id) { this.q(id).disabled = locked; }, this);
		if (locked) this.q('net-auto').checked = false;
		this.q('net-note').textContent = locked ? _('USB network mode is unavailable in host mode; host auto-start also disables USB network auto-start.') : _('NCM is the default. Windows does not natively support ECM; RNDIS changes enumeration. With Enable selected protocol off, only your choice is saved. Next reboot only applies once, then returns to NCM.');
		this.q('refresh').disabled = !host;
	},
	wire: function() {
		var self = this;
		this.q('role').addEventListener('change', function() { self.updateDisabled(); });
		this.q('role-auto').addEventListener('change', function() { self.updateDisabled(); });
		this.q('net-mode').addEventListener('change', function() { self.q('net-auto').checked = true; });
		this.q('net-scope').addEventListener('change', function() { self.q('net-auto').checked = true; });
		this.q('role-apply').addEventListener('click', function() {
			var role = self.q('role').value, auto = self.q('role-auto').checked ? '1' : '0';
			var warning = role === 'host' ? _('Host mode immediately disconnects USB management. F50 has no battery and peripherals may need external power; make sure another management path exists.') : _('USB networking and serial will re-enumerate in device mode.');
			M.confirmBox(_('Switch USB role?'), warning, { danger: role === 'host', okText: _('Apply') }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('role-apply'); M.busy(btn, true);
				var msg = M.toast(_('Switching USB role…'), { type: 'busy', timeout: 0 });
				M.callUsbSet('role', role, '', auto).then(function(r) {
					M.busy(btn, false);
					if (!r || r.ok !== 1) {
						msg.update(r && r.ok === 0 ? _('Switch failed: %s').format(M.errText(r)) : _('Management connection lost; reconnect to confirm the USB role.'), r && r.ok === 0 ? 'error' : 'info');
						setTimeout(function() { msg.close(); }, 6000);
						return;
					}
					if (r.pending) {
						msg.update(_('Switch request accepted; the USB connection may briefly disconnect.'), 'info');
						self.watchRole(role, msg, 0);
					} else {
						msg.update(_('USB role applied'), 'success'); setTimeout(function() { msg.close(); }, 2500);
						self.reloadState();
					}
				}, function() {
					M.busy(btn, false);
					msg.update(_('Management connection lost; reconnect to confirm the USB role.'), 'info');
					setTimeout(function() { msg.close(); }, 6000);
				});
			});
		});
		this.q('net-apply').addEventListener('click', function() {
			var mode = self.q('net-mode').value, scope = self.q('net-scope').value, auto = self.q('net-auto').checked ? '1' : '0';
			M.confirmBox(_('Save USB network mode?'), auto === '1' ? _('The network mode applies on the next reboot; USB management may need to reconnect.') : _('Save the selection only; with the selected protocol disabled, the next boot still uses default NCM.'), { okText: _('Save') }).then(function(yes) {
				if (!yes) return;
				var btn = self.q('net-apply'); M.busy(btn, true);
				var msg = M.toast(_('Saving USB network settings…'), { type: 'busy', timeout: 0 });
				L.resolveDefault(M.callUsbSet('net', mode, scope, auto), {}).then(function(r) {
					M.busy(btn, false); msg.update(r.ok ? _('Settings saved') : _('Save failed: %s').format(M.errText(r)), r.ok ? 'success' : 'error');
					setTimeout(function() { msg.close(); }, 3500);
					if (r.ok) self.reloadState();
				});
			});
		});
		this.q('refresh').addEventListener('click', function() { self.refreshAdapters(0); });
		this.q('ops-show').addEventListener('click', function() { self.showOps(); });
	},
	/* "Recent modem operations": the backend's log of its calls (ops_log), newest first, this boot and the one before */
	showOps: function() {
		var self = this, box = this.q('ops'), btn = this.q('ops-show');
		M.busy(btn, true);
		L.resolveDefault(M.callOpsLog(), {}).then(function(r) {
			M.busy(btn, false);
			box.replaceChildren();
			box.style.display = '';
			[ [ _('This boot'), r.now ], [ _('Previous boot'), r.prev ] ].forEach(function(part) {
				var h = document.createElement('div'); h.className = 'mud-note'; h.style.margin = '10px 0 4px';
				h.textContent = part[0]; box.appendChild(h);
				var pre = document.createElement('pre'); pre.className = 'mud-term';
				pre.style.maxHeight = '260px'; pre.style.overflow = 'auto'; pre.style.margin = '0';
				var lines = String(part[1] || '').split('\n').filter(Boolean).reverse();
				pre.textContent = lines.length ? lines.join('\n') : _('(empty)');
				box.appendChild(pre);
			});
		});
	},
	reloadState: function() {
		var self = this;
		L.resolveDefault(M.callUsbGet(), {}).then(function(s) { if (s.ok) { self.state = s; self.paint(); } });
	},
	watchRole: function(role, msg, attempt) {
		var self = this;
		setTimeout(function() {
			M.callUsbGet().then(function(s) {
				if (s && s.ok && s.role === role) {
					msg.update(_('USB role applied'), 'success');
					setTimeout(function() { msg.close(); }, 2500);
					self.state = s; self.paint();
					return;
				}
				self.finishRoleWatch(role, msg, attempt);
			}, function() { self.finishRoleWatch(role, msg, attempt); });
		}, attempt ? 1000 : 350);
	},
	finishRoleWatch: function(role, msg, attempt) {
		if (attempt < 4) { this.watchRole(role, msg, attempt + 1); return; }
		msg.update(_('Unable to confirm the role yet; reconnect and refresh the page.'), 'info');
		setTimeout(function() { msg.close(); }, 5000);
	},
	refreshAdapters: function(attempt) {
		attempt = attempt || 0;
		var self = this, list = this.q('adapters');
		list.replaceChildren();
		if (this.state.role !== 'host') {
			list.textContent = _('Switch to host mode to see USB adapters.'); return;
		}
		var wait = document.createElement('div'); wait.className = 'mud-note mud-booting';
		wait.textContent = _('Scanning USB adapters…'); list.appendChild(wait);
		M.busy(this.q('refresh'), true);
		L.resolveDefault(M.callUsbNetList(), {}).then(function(r) {
			M.busy(self.q('refresh'), false);
			list.replaceChildren();
			if (!r.ok || !r.devices || !r.devices.length) {
				list.textContent = _('No USB adapters found.');
				if (self.state.role === 'host' && attempt < 2)
					setTimeout(function() { if (self.state.role === 'host') self.refreshAdapters(attempt + 1); }, 1600);
				return;
			}
			r.devices.forEach(function(d) {
				var row = document.createElement('div'); row.className = 'mud-device-item';
				var main = document.createElement('div'); main.className = 'mud-device-item-main';
				var dot = document.createElement('i'); dot.className = 'mud-device-dot' + (d.carrier ? ' up' : ''); main.appendChild(dot);
				var info = document.createElement('div');
				var name = document.createElement('div'); name.className = 'mud-device-item-name'; name.textContent = d.name;
				var sub = document.createElement('div'); sub.className = 'mud-device-item-sub'; sub.textContent = d.carrier ? _('Link connected') : _('No link; enable attempted');
				info.appendChild(name); info.appendChild(sub); main.appendChild(info); row.appendChild(main);
				var btn = document.createElement('button'); btn.className = 'mud-btn'; btn.textContent = d.in_lan ? _('Added to LAN') : _('Add to LAN'); btn.disabled = !!d.in_lan;
				btn.addEventListener('click', function() {
					M.confirmBox(_('Add USB adapter to LAN?'), _('This saves the bridge configuration and reloads networking; existing connections may briefly drop.'), { okText: _('Add') }).then(function(yes) {
						if (!yes) return;
						M.busy(btn, true); var msg = M.toast(_('Adding USB adapter…'), { type: 'busy', timeout: 0 });
						L.resolveDefault(M.callUsbNetAdd(d.name), {}).then(function(a) {
							M.busy(btn, false); msg.update(a.ok ? _('Added to LAN') : _('Add failed: %s').format(M.errText(a)), a.ok ? 'success' : 'error');
							setTimeout(function() { msg.close(); }, 3500); self.refreshAdapters();
						});
					});
				});
				row.appendChild(btn); list.appendChild(row);
			});
		});
	}
});
