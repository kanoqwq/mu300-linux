/* The MU300 page of Cockpit (Ubuntu): the device's own settings and tools, the pages of the OpenWrt control panel
 * (openwrt-luci) and of the web installer's device manager, through the same backend: mu300-api (one method per call,
 * the request a JSON object on its stdin, the answer one JSON object; docs/ADB-INTERFACE.md). Every call is a
 * cockpit.spawn with an argument array (never a shell: nothing typed here is ever parsed as a command line) with
 * Cockpit's administrative access ("try": without it mu300-api answers not-root and the page says what to turn on).
 * Secrets (the hotspot's, a Wi-Fi network's, the APN's and the login password, VPN links) travel in the request on
 * stdin, never in an argument, and are never sent back. Every value from the device is put in as text
 * (textContent), never as HTML. Translations: po.<lang>.js (cockpit.locale), English is the source. */
/* global cockpit */
(function () {
    'use strict';
    var _ = cockpit.gettext;
    var API = '/opt/mu300/bin/mu300-api';

    /* ---- small helpers ---------------------------------------------------------------------------------- */
    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            var v = attrs[k];
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') e.className = v;
            else if (k === 'text') e.textContent = v;
            else if (k === 'value') e.value = v;
            else if (k === 'checked') e.checked = !!v;
            else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
            else e.setAttribute(k, v === true ? '' : v);
        });
        (children || []).forEach(function (c) {
            if (c === null || c === undefined) return;
            e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        });
        return e;
    }
    function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
    function card(title, id) { return el('section', { 'class': 'mu-card', id: id }, [ el('h2', { text: title }) ]); }
    function show(v) { return v === null || v === undefined || v === '' ? '-' : String(v); }
    function row(label, value) {
        return el('div', { 'class': 'mu-row' }, [ el('span', { 'class': 'mu-k', text: label }),
            el('span', { 'class': 'mu-v', text: show(value) }) ]);
    }
    function note(text, kind) { return el('p', { 'class': 'mu-note' + (kind ? ' mu-' + kind : ''), text: text }); }
    function button(text, onclick, kind) {
        return el('button', { 'class': 'mu-btn' + (kind ? ' mu-' + kind : ''), type: 'button', onclick: onclick, text: text });
    }
    function pre(text) { return el('pre', { 'class': 'mu-pre', text: text }); }
    function actions(list) { return el('div', { 'class': 'mu-actions' }, list); }
    function input(id, attrs) { var a = attrs || {}; a.id = id; if (!a.type) a.type = 'text'; return el('input', a); }
    function field(label, control) {
        return el('div', { 'class': 'mu-field' }, [ el('label', { 'for': control.id, text: label }), control ]);
    }
    /* select(id, [[value, label], ...], current) */
    function select(id, opts, cur) {
        var s = el('select', { id: id });
        opts.forEach(function (o) {
            var op = el('option', { value: String(o[0]), text: o[1] });
            if (String(o[0]) === String(cur)) op.selected = true;
            s.appendChild(op);
        });
        s.value = String(cur === undefined || cur === null ? opts[0][0] : cur);
        return s;
    }
    function check(id, label, on) {
        var c = el('input', { type: 'checkbox', id: id, checked: on });
        return { box: c, node: el('label', { 'class': 'mu-check', 'for': id }, [ c, ' ' + label ]) };
    }
    function table(head, rows) {
        var t = el('table', { 'class': 'mu-table' });
        t.appendChild(el('thead', {}, [ el('tr', {}, head.map(function (h) { return el('th', { text: h }); })) ]));
        var b = t.appendChild(el('tbody'));
        rows.forEach(function (r) {
            b.appendChild(el('tr', {}, r.map(function (c) {
                return el('td', {}, [ c !== null && typeof c === 'object' ? c : show(c) ]);
            })));
        });
        return t;
    }
    function busy(btn, on) { if (btn) { btn.disabled = !!on; btn.classList.toggle('mu-busy', !!on); } }
    function toast(text, kind) {
        var box = document.getElementById('mu-toast');
        if (!box) return;
        clear(box).appendChild(el('div', { 'class': 'mu-toast mu-' + (kind || 'ok'), text: text }));
        clearTimeout(toast.t);
        toast.t = setTimeout(function () { clear(box); }, 8000);
    }
    function confirmed(text) { return !text || window.confirm(text); }
    function on(v) { return v ? _('On') : _('Off'); }
    function yes(v) { return v ? _('Yes') : _('No'); }

    function fmtBytes(n) {
        n = Number(n);
        if (!isFinite(n) || n < 0) return '-';
        var u = [ 'B', 'KiB', 'MiB', 'GiB', 'TiB' ], i = 0;
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return (i ? n.toFixed(1) : String(Math.round(n))) + ' ' + u[i];
    }
    function fmtRate(bps) {
        if (!isFinite(bps) || bps < 0) return '-';
        var bits = bps * 8, u = [ 'bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s' ], i = 0;
        while (bits >= 1000 && i < u.length - 1) { bits /= 1000; i++; }
        return (i ? bits.toFixed(1) : String(Math.round(bits))) + ' ' + u[i];
    }
    function fmtDuration(s) {
        s = Number(s);
        if (!isFinite(s) || s < 0) return '-';
        var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
        return (d ? d + 'd ' : '') + (d || h ? h + 'h ' : '') + m + 'min';
    }
    /* /proc/net/dev: {iface: {rx, tx}} */
    function netDev(text) {
        var out = {};
        String(text || '').split('\n').forEach(function (line) {
            var m = line.match(/^\s*([^:\s]+):\s*(.*)$/);
            if (!m) return;
            var f = m[2].trim().split(/\s+/);
            if (f.length >= 9) out[m[1]] = { rx: Number(f[0]), tx: Number(f[8]) };
        });
        return out;
    }
    /* an identifier (IMEI, IMSI, ICCID, a phone number) as LuCI's dashboard shows it: the first 4 and the last 3 */
    function mask(s) {
        s = String(s || '');
        return s.length > 8 ? s.substring(0, 4) + '****' + s.substring(s.length - 3) : s ? '****' : '-';
    }

    /* ---- the backend: mu300-api ---------------------------------------------------------------------------- */
    /* the error of a call, worded: Cockpit's own problems, mu300-api's codes (its detail is a field's name or the
     * tool's last line) */
    function errText(ex) {
        if (!ex) return _('Unknown error');
        var d = ex.detail ? String(ex.detail) : '';
        switch (ex.code) {
        case 'not-root': return _('This needs administrative access: turn it on at the top of Cockpit ("Limited access").');
        case 'not-installed': return _('The VPN module is not installed.');
        case 'busy': return d ? cockpit.format(_('Another task is running ($0): wait until it has ended.'), d)
            : _('Another task is running: wait until it has ended.');
        case 'bad-value': return cockpit.format(_('Not a valid value: $0'), d);
        case 'missing': return cockpit.format(_('Missing: $0'), d);
        case 'unknown-field': case 'bad-request': return cockpit.format(_('The device refused the request ($0).'), d || ex.code);
        case 'unsupported': return cockpit.format(_('Not available on this system: $0'), d);
        case 'unknown-method': return _('The device\'s mu300-api is older than this page: update the device.');
        case 'failed': return d || _('Failed');
        }
        if (ex.problem === 'not-found') return _('The command is not on this system.');
        if (ex.problem === 'access-denied' || ex.problem === 'authentication-failed')
            return _('This needs administrative access: turn it on at the top of Cockpit ("Limited access").');
        return String(ex.message || ex.problem || ex);
    }
    /* api(METHOD, REQUEST): the answer ({"ok":1,...}), or a rejection {code, detail} (mu300-api's) or Cockpit's
     * problem. The answer is read from the output as it comes (a refusal ends with exit status 1 and still says
     * why on stdout). */
    function api(method, req) {
        return new Promise(function (resolve, reject) {
            var buf = '';
            var p = cockpit.spawn([ API, method ], { superuser: 'try', err: 'message' });
            p.stream(function (d) { buf += d; });
            p.input(JSON.stringify(req || {}));
            function done(ex) {
                var o = null;
                try { o = JSON.parse(String(buf).trim().split('\n').pop()); } catch (e) { o = null; }
                if (o && o.ok === 1) resolve(o);
                else if (o && o.ok === 0) reject({ code: String(o.error || 'failed'), detail: o.detail });
                else reject(ex || { code: 'failed', detail: _('No answer from mu300-api') });
            }
            p.then(function () { done(null); }, function (ex) { done(ex); });
        });
    }

    var S = { hello: null, methods: {}, features: {}, admin: null };
    function has(m) { return !!S.methods[m]; }

    /* act(BUTTON, METHOD, REQUEST, {confirm, done, ok}): one change, after a confirmation when it has one; a method that
     * starts the background job is followed in the job box (done when it has ended) */
    function act(btn, method, req, o) {
        o = o || {};
        if (!confirmed(o.confirm)) return Promise.resolve(null);
        busy(btn, true);
        return api(method, req).then(function (r) {
            busy(btn, false);
            if (r.job) { job.follow(o.done); return r; }
            toast(o.ok || (r.text ? String(r.text).trim() : '') || _('Done'), 'ok');
            if (o.done) o.done(r);
            return r;
        }, function (ex) {
            busy(btn, false);
            toast(errText(ex), 'error');
            return null;
        });
    }
    /* load(BODY, METHOD, REQUEST, PAINT): BODY says "Loading", then PAINT(answer) or the error */
    function load(body, method, req, paint) {
        if (!body.firstChild) body.appendChild(note(_('Loading...')));
        return api(method, req).then(function (r) { clear(body); paint(r); return r; },
            function (ex) { clear(body).appendChild(note(errText(ex), 'error')); return null; });
    }

    /* ---- the background job (an update, a kernel, an extra, a VPN download, joining a Wi-Fi network) ---------- */
    var JOBS = { 'update-check': _('Looking for updates'), 'update-apply': _('Installing the update'),
        kernel: _('Changing the kernel'), 'extra-install': _('Installing an extra'), 'extra-remove': _('Removing an extra'),
        'vpn-install': _('Installing the VPN module'), 'vpn-engine': _('Installing a VPN engine'),
        'vpn-import': _('Importing VPN profiles'), 'vpn-sub-add': _('Adding a subscription'),
        'vpn-sub-refresh': _('Refreshing subscriptions'), 'wifi-join': _('Joining a Wi-Fi network'),
        'wifi-leave': _('Leaving the Wi-Fi network'), 'backup-import': _('Restoring the settings'),
        'packages-retry': _('Installing the packages again') };
    var job = {
        next: 0, timer: null, after: [], shown: false,
        box: function () { return document.getElementById('mu-job'); },
        follow: function (after) {
            if (after) job.after.push(after);
            if (job.timer) return;
            job.next = 0;
            job.log = '';
            job.poll();
        },
        poll: function () {
            job.timer = null;
            api('job.status', { from: job.next }).then(function (o) {
                var j = o.job || {};
                if (!j.kind) { clear(job.box()); return; }
                job.log = (job.log || '') + (j.text ? String(j.text) + '\n' : '');
                job.next = j.next || job.next;
                job.paint(j);
                if (j.state === 'running') job.timer = setTimeout(job.poll, 1500);
                else {
                    var a = job.after;
                    job.after = [];
                    a.forEach(function (f) { try { f(j); } catch (e) { /* the page went on */ } });
                }
            }, function () { job.timer = setTimeout(job.poll, 5000); });
        },
        paint: function (j) {
            var box = clear(job.box());
            var c = box.appendChild(card(JOBS[j.kind] || String(j.kind), 'mu-job-card'));
            var states = { running: _('Running...'), done: j.rc === 0 ? _('Finished') : cockpit.format(_('Ended with an error (status $0)'), show(j.rc)),
                interrupted: _('Interrupted (the device restarted?)') };
            c.appendChild(note(states[j.state] || String(j.state), j.state === 'done' && j.rc !== 0 ? 'error' : ''));
            var out = c.appendChild(pre(job.log.replace(/\n+$/, '')));
            out.scrollTop = out.scrollHeight;
            var bar = c.appendChild(actions([]));
            if (j.state === 'running' && j.cancel)
                bar.appendChild(button(_('Stop'), function (e) { act(e.target, 'job.cancel', {}); }, 'danger'));
            if (j.state !== 'running')
                bar.appendChild(button(_('Close'), function (e) {
                    act(e.target, 'job.clear', {}, { ok: ' ', done: function () { clear(job.box()); } });
                }));
        }
    };

    /* ---- the pages -------------------------------------------------------------------------------------- */
    var pages = {}, current = null, timers = [];
    function every(ms, fn) { fn(); timers.push(setInterval(fn, ms)); }
    function stopTimers() { timers.forEach(clearInterval); timers = []; }

    var REG = function () {
        return { home: _('Registered (home network)'), roaming: _('Registered (roaming)'), searching: _('Searching'),
            denied: _('Registration denied'), none: _('Not registered'), emergency: _('Emergency calls only') };
    };
    function signalRows(body, s) {
        s = s || {};
        body.appendChild(row(_('Operator'), s.operator));
        body.appendChild(row(_('Technology'), s.rat));
        body.appendChild(row(_('Registration'), REG()[s.registration] || s.registration));
        body.appendChild(row(_('RSRP'), s.rsrp === null || s.rsrp === undefined ? null : s.rsrp + ' dBm'));
        body.appendChild(row(_('RSRQ'), s.rsrq === null || s.rsrq === undefined ? null : s.rsrq + ' dB'));
    }
    function dataState(s) { return { up: _('Connected'), down: _('Not connected'), off: _('Off') }[s] || s; }

    /* the live rates of the interfaces, from /proc/net/dev every 2 s (read in the browser's session: no process, no
     * root) */
    function rateCard(root) {
        var rates = card(_('Live rates'), 'mu-rates');
        root.appendChild(rates);
        var body = rates.appendChild(el('div')), last = null;
        every(2000, function () {
            return cockpit.file('/proc/net/dev').read().then(function (text) {
                var now = { t: Date.now(), dev: netDev(text) };
                clear(body);
                var known = [ [ 'sipa_eth0', _('Mobile data') ], [ 'sipa_eth8', _('Mobile data (SIM 2)') ],
                    [ 'br-lan', _('LAN') ], [ 'wlan0', _('Wi-Fi') ], [ 'mu300vpn', _('VPN') ] ];
                known = known.filter(function (p) { return now.dev[p[0]]; });
                if (!known.length)
                    known = Object.keys(now.dev).filter(function (n) {
                        return n !== 'lo' && now.dev[n].rx + now.dev[n].tx > 0;
                    }).map(function (n) { return [ n, n ]; });
                known.forEach(function (p) {
                    var a = last && last.dev[p[0]], c = now.dev[p[0]];
                    var dt = last ? (now.t - last.t) / 1000 : 0;
                    var down = a && dt > 0 ? Math.max(0, (c.rx - a.rx) / dt) : NaN;
                    var up = a && dt > 0 ? Math.max(0, (c.tx - a.tx) / dt) : NaN;
                    body.appendChild(row(p[1], '↓ ' + fmtRate(down) + '   ↑ ' + fmtRate(up)));
                });
                last = now;
            }, function () {});
        });
    }

    /* Overview: the device in one call (overview), the VPN's state, the live rates, the two recovery buttons */
    pages.overview = function (root) {
        var grid = root.appendChild(el('div', { 'class': 'mu-grid' }));
        var sig = card(_('Mobile network'), 'mu-signal'), data = card(_('Mobile data'), 'mu-data');
        var dev = card(_('Device'), 'mu-device'), usage = card(_('Data usage'), 'mu-usage');
        var wifi = card(_('Wi-Fi hotspot'), 'mu-ov-wifi'), vpn = card(_('VPN'), 'mu-ov-vpn');
        [ sig, data, dev, usage, wifi, vpn ].forEach(function (c) { grid.appendChild(c); });
        rateCard(grid);
        var rec = grid.appendChild(card(_('Recovery'), 'mu-recovery'));
        var sb = sig.appendChild(el('div')), db = data.appendChild(el('div')), vb = dev.appendChild(el('div'));
        var ub = usage.appendChild(el('div')), wb = wifi.appendChild(el('div')), pb = vpn.appendChild(el('div'));
        recovery(rec);

        function paint(o) {
            clear(sb); signalRows(sb, o.signal);
            if (!o.signal) sb.appendChild(note(_('No readings from the modem.')));
            var d = o.data || {}, up = d.state === 'up';
            clear(db).appendChild(row(_('State'), dataState(d.state)));
            db.appendChild(row(_('Interface'), d.iface));
            db.appendChild(row(_('Address'), d.address));
            var b = button(up ? _('Turn mobile data off') : _('Turn mobile data on'), function () {
                act(b, 'data.power', { state: up ? 'off' : 'on' }, { done: refresh,
                    confirm: up ? _('Turn mobile data off? Clients of the device lose the internet until it is on again.') : null });
            });
            db.appendChild(b);
            clear(vb).appendChild(row(_('Device'), { f50: 'ZTE F50', u30air: 'ZTE U30 Air', v50: 'V50' }[o.device] || o.device));
            vb.appendChild(row(_('System'), o.os));
            vb.appendChild(row(_('Release'), o.release));
            vb.appendChild(row(_('Kernel'), o.kernel));
            vb.appendChild(row(_('Host name'), o.hostname));
            vb.appendChild(row(_('Up for'), fmtDuration(o.uptime)));
            var m = o.memory || {};
            if (m.total_kb) vb.appendChild(row(_('Memory available'), fmtBytes(m.avail_kb * 1024) + ' / ' + fmtBytes(m.total_kb * 1024)));
            var st = o.storage || {};
            if (st.disk) vb.appendChild(row(_('Linux partition free'), fmtBytes(st.disk.avail * 1024) + ' / ' + fmtBytes(st.disk.size * 1024)));
            if (o.battery) {
                var bt = o.battery;
                vb.appendChild(row(_('Battery'), (bt.capacity === null ? '-' : bt.capacity + ' %') + (bt.status ? ' (' + bt.status + ')' : '')));
            }
            var temps = (o.temperatures || []).filter(function (t) { return t.millicelsius > 0; });
            if (temps.length) {
                var max = temps.reduce(function (a, t) { return t.millicelsius > a.millicelsius ? t : a; });
                vb.appendChild(row(_('Hottest sensor'), (max.millicelsius / 1000).toFixed(1) + ' °C (' + max.name + ')'));
            }
            var t = o.traffic;
            clear(ub);
            if (t && t.today) ub.appendChild(row(_('Today'), fmtBytes(Number(t.today.rx) + Number(t.today.tx))));
            if (t && t.cycle) {
                ub.appendChild(row(_('This billing cycle'), fmtBytes(t.cycle.used)));
                ub.appendChild(row(_('Cycle'), t.cycle.start + ' - ' + t.cycle.last));
            }
            if (t && t.cap && t.cap.bytes) ub.appendChild(row(_('Monthly cap'), fmtBytes(t.cap.bytes)));
            if (!t) ub.appendChild(note(_('No data usage count on this system.')));
            var h = o.hotspot || {};
            clear(wb).appendChild(row(_('Name (SSID)'), h.ssid));
            wb.appendChild(row(_('State'), on(h.enabled)));
            wb.appendChild(row(_('LAN address'), o.lan));
        }
        function refresh() { return api('overview').then(paint, function (ex) { clear(sb).appendChild(note(errText(ex), 'error')); }); }
        function vpnRefresh() {
            return api('vpn.status').then(function (r) {
                clear(pb);
                if (!r.installed) { pb.appendChild(note(_('The VPN module is not installed: see the VPN tab.'))); return; }
                var s = r.status || {};
                pb.appendChild(row(_('Switch'), on(s.enabled)));
                pb.appendChild(row(_('Running'), yes(s.running)));
                pb.appendChild(row(_('Profile'), s.profile ? (s.profile.name || s.profile.id) : null));
                pb.appendChild(row(_('Tunnel'), s.tunnel && s.tunnel.up ? _('up') : _('down')));
                pb.appendChild(row(_('Kill switch'), s.kill_switch && s.kill_switch.active ? _('active') : _('inactive')));
            }, function (ex) { clear(pb).appendChild(note(errText(ex), 'error')); });
        }
        every(10000, refresh);
        every(30000, vpnRefresh);
    };

    /* network recovery: the dial again (modem.restart), the whole device (system.reboot) */
    function recovery(c) {
        c.appendChild(note(_('When the connection is stuck: reconnect mobile data first (about half a minute without it); a restart of the device ends every connection for a minute or two.')));
        var m = button(_('Reconnect mobile data'), function () {
            act(m, 'modem.restart', {}, { confirm: _('Reconnect mobile data? Cellular connectivity stops for about half a minute.'),
                ok: _('Mobile data is reconnecting.') });
        });
        var r = button(_('Restart the device'), function () {
            act(r, 'system.reboot', {}, { confirm: _('Restart the device? Every connection is interrupted, this page too, for a minute or two.'),
                ok: _('The device is restarting.') });
        }, 'danger');
        if (!has('modem.restart')) m.disabled = true;
        c.appendChild(actions([ m, r ]));
    }

    /* Network, band and cell locks (lock.get / lock.set): the lock program of the OpenWrt control panel, on Ubuntu too
     * (mu300-lock-replay.service sends the saved locks again at every boot unless "apply at startup" is off). A change
     * runs in the background and restarts the modem's protocol stack (half a minute without cellular), EN-DC and the
     * startup switch excepted; the reading is cached and taken again on demand. Mode and band locks stay in the modem
     * (Android too). */
    var NR_CAND = [ 1, 5, 6, 8, 28, 41, 78 ], LTE_CAND = [ 1, 3, 5, 8, 34, 38, 39, 40, 41 ];
    function bandList(s) {
        return String(s || '').split(',').map(function (x) { return x.trim(); })
            .filter(function (x) { return /^[0-9]{1,3}$/.test(x); }).map(Number);
    }
    function locksCard(root) {
        var lc = root.appendChild(card(_('Network, band and cell locks'), 'mu-locks'));
        var lb = lc.appendChild(el('div'));
        var MODES = [ [ 'auto', _('Automatic') ], [ 'auto152', _('Automatic without 2G (152)') ], [ '4g', _('4G only') ], [ 'sa', '5G SA' ], [ 'nsa', '5G NSA' ] ];
        var RESTART = _('The modem\'s protocol stack restarts: cellular service stops for about half a minute.');
        function set(btn, kind, val, what, quick) {
            act(btn, 'lock.set', { kind: kind, val: val }, {
                confirm: quick ? null : cockpit.format(_('Apply: $0?'), what) + '\n\n' + RESTART,
                ok: quick ? _('Saved') : _('Started in the background; the reading below follows when the modem is back.'),
                done: function () { setTimeout(function () { refresh(true); }, quick ? 300 : 3000); } });
        }
        function bandBox(id, cand, locked) {
            var box = el('div', { 'class': 'mu-actions', id: id }), boxes = {};
            cand.forEach(function (b) {
                var c = check(id + '-' + b, (id.indexOf('nr') >= 0 ? 'n' : 'B') + b, locked.indexOf(b) >= 0);
                c.node.classList.add('mu-band');
                boxes[b] = c.box;
                box.appendChild(c.node);
            });
            return { node: box, chosen: function () { return cand.filter(function (b) { return boxes[b].checked; }); } };
        }
        function bandsLine(locked, cand, prefix) {
            return !locked.length || locked.length >= cand.length ? cockpit.format(_('Automatic ($0 supported)'), cand.length)
                : prefix + locked.join(' ' + prefix);
        }
        function paint(d) {
            if (d.busy) {
                lb.appendChild(note(_('Reading the locks from the modem; try again in a few seconds.')));
                lb.appendChild(actions([ button(_('Refresh'), function () { refresh(false); }) ]));
                return;
            }
            var m = d.mode || {}, caps = d.caps || {};
            var nrCand = bandList(caps.nr).length ? bandList(caps.nr) : NR_CAND;
            var lteCand = bandList(caps.lte).length ? bandList(caps.lte) : LTE_CAND;
            var nrLocked = bandList((d.nr || {}).locked), lteLocked = bandList((d.lte || {}).locked);
            if (!d.mode) lb.appendChild(note(_('No reading of the locks yet.'), 'warn'));
            lb.appendChild(note(_('Mode and band locks stay in the modem, in Android too: reset them before you switch to Android if you are not sure. Every change below restarts the modem\'s protocol stack (about half a minute without cellular), except EN-DC and the startup switch.')));
            var lm = MODES.filter(function (x) { return x[0] === m.label; })[0];
            lb.appendChild(row(_('Network mode'), lm ? lm[1] : m.label));
            lb.appendChild(row('EN-DC', d.endc === '1' ? _('On') : d.endc ? _('Off') : null));
            lb.appendChild(row(_('NR bands'), bandsLine(nrLocked, nrCand, 'n')));
            lb.appendChild(row(_('LTE bands'), bandsLine(lteLocked, lteCand, 'B')));
            var cells = (d.cells || []).filter(function (c) { return /^(nr|lte):[0-9]+,[0-9]+$/.test(c); });
            lb.appendChild(row(_('Cell lock'), cells.length ? cells.map(function (c) {
                var p = c.split(/[:,]/);
                return cockpit.format(_('$0 ARFCN $1, PCI $2'), p[0] === 'nr' ? 'NR' : 'LTE', p[1], p[2]);
            }).join('; ') : _('none')));
            lb.appendChild(row(_('Apply at startup'), on(d.auto_apply === 1 || d.auto_apply === true)));
            var rs = d.reset;
            if (rs && rs.state) {
                var st = { running: _('running'), ok: _('done'), done: _('done'), failed: _('failed'), interrupted: _('interrupted') }[rs.state] || rs.state;
                lb.appendChild(row(_('Last reset to automatic'), st + (rs.error ? ' (' + rs.error + ')' : '')));
            }
            if (d.ts) lb.appendChild(row(_('Read from the modem'), new Date(d.ts * 1000).toLocaleString()));
            lb.appendChild(actions([ button(_('Read again from the modem'), function () { refresh(true); }) ]));

            lb.appendChild(el('h3', { text: _('Network mode') }));
            var mode = select('mu-lock-mode', MODES, m.label || 'auto');
            lb.appendChild(field(_('Network mode'), mode));
            var mb = button(_('Apply'), function () {
                var ml = MODES.filter(function (x) { return x[0] === mode.value; })[0];
                set(mb, 'mode', mode.value, cockpit.format(_('network mode $0'), ml ? ml[1] : mode.value));
            }, 'primary');
            var eon = d.endc === '1';
            var eb = button(eon ? _('Turn EN-DC off') : _('Turn EN-DC on'), function () {
                set(eb, 'endc', eon ? 'off' : 'on', '', true);
            });
            lb.appendChild(actions([ mb, eb ]));
            lb.appendChild(note(_('EN-DC lets a 5G NSA connection use its LTE anchor; off keeps the modem on LTE alone where it would use NSA.')));

            lb.appendChild(el('h3', { text: _('Bands') }));
            var nr = bandBox('mu-lock-nr', nrCand, nrLocked.length >= nrCand.length ? [] : nrLocked);
            var lte = bandBox('mu-lock-lte', lteCand, lteLocked.length >= lteCand.length ? [] : lteLocked);
            lb.appendChild(el('label', { text: _('NR bands') })); lb.appendChild(nr.node);
            lb.appendChild(el('label', { text: _('LTE bands') })); lb.appendChild(lte.node);
            var nb = button(_('Apply NR bands'), function () {
                var c = nr.chosen();
                set(nb, 'nr', c.join(','), c.length ? cockpit.format(_('NR band lock $0'), 'n' + c.join(' n')) : _('NR bands automatic'));
            });
            var tb = button(_('Apply LTE bands'), function () {
                var c = lte.chosen();
                set(tb, 'lte', c.join(','), c.length ? cockpit.format(_('LTE band lock $0'), 'B' + c.join(' B')) : _('LTE bands automatic'));
            });
            lb.appendChild(actions([ nb, tb ]));
            lb.appendChild(note(_('No band ticked: automatic (every band the modem supports).')));

            lb.appendChild(el('h3', { text: _('Cell lock') }));
            var rat = select('mu-lock-rat', [ [ 'nr', 'NR' ], [ 'lte', 'LTE' ] ], 'nr');
            var arfcn = input('mu-lock-arfcn', { type: 'number', min: '0', max: '9999999', placeholder: '627264' });
            var pci = input('mu-lock-pci', { type: 'number', min: '0', max: '1007', placeholder: '393' });
            lb.appendChild(field(_('Technology'), rat));
            lb.appendChild(field(_('ARFCN (NR-ARFCN or EARFCN)'), arfcn));
            lb.appendChild(field(_('PCI'), pci));
            var cb = button(_('Lock this cell'), function () {
                var a = String(arfcn.value).trim(), p = String(pci.value).trim();
                if (!/^[0-9]{1,7}$/.test(a) || !/^[0-9]{1,4}$/.test(p)) { toast(_('The ARFCN is a number of up to 7 digits, the PCI one of up to 4.'), 'error'); return; }
                set(cb, 'cell', rat.value + ':' + a + ',' + p, cockpit.format(_('$0 cell lock: ARFCN $1, PCI $2'), rat.value === 'nr' ? 'NR' : 'LTE', a, p));
            });
            var sb = button(_('Lock the serving cell'), function () { set(sb, 'cell', 'auto', _('a lock on the serving cell')); });
            var ub = button(_('Unlock every cell'), function () { set(ub, 'cell', 'off', _('no cell lock')); });
            lb.appendChild(actions([ cb, sb, ub ]));
            lb.appendChild(note(_('A cell lock is not kept in the modem across a restart: with "Apply at startup" on, the device sends it again at every boot.')));

            lb.appendChild(el('h3', { text: _('Startup and reset') }));
            var aon = d.auto_apply === 1 || d.auto_apply === true;
            var ab = button(aon ? _('Do not apply at startup') : _('Apply at startup'), function () {
                set(ab, 'auto_apply', aon ? 'off' : 'on', '', true);
            });
            var rb = button(_('Reset all to automatic'), function () {
                act(rb, 'lock.set', { kind: 'reset', val: 'auto' }, {
                    confirm: _('Network mode automatic, EN-DC on, every LTE and NR band, no cell lock; the saved locks are cleared, so nothing is applied at startup.') + '\n\n' + RESTART,
                    ok: _('Started in the background; the reading below follows when the modem is back.'),
                    done: function () { setTimeout(function () { refresh(true); }, 5000); } });
            }, 'danger');
            lb.appendChild(actions([ ab, rb ]));
            lb.appendChild(note(_('Off: nothing is sent at startup; the saved settings stay.')));
        }
        function refresh(fresh) {
            if (fresh) clear(lb).appendChild(note(_('Reading the locks from the modem...')));
            return load(lb, 'lock.get', fresh ? { fresh: true } : {}, function (r) { paint(r.data || {}); });
        }
        refresh(false);
    }

    /* SMS forwarding (sms.forward, sms.forward_set, sms.forward_test, sms.forward_log_clear): received messages sent on
     * to a webhook, a Telegram chat, an e-mail address (with msmtp) or another phone. The secrets (the webhook address
     * and headers, the bot token, the mail password) are never sent back: an empty field keeps the saved one, a box
     * removes it. The webhook's own body and the answer check are templates, not secrets: they come back. */
    var FWD_TEMPLATE = '{text}\n-- {sender}, {time} ({device})';
    function forwardCard(root) {
        var fc = root.appendChild(card(_('SMS forwarding'), 'mu-sms-forward'));
        var fb = fc.appendChild(el('div'));
        var lc = root.appendChild(card(_('Delivery log'), 'mu-sms-forward-log'));
        var logb = lc.appendChild(el('div'));
        var F = {};
        function area(id, value, rows, ph) { var a = el('textarea', { id: id, rows: String(rows), spellcheck: 'false', placeholder: ph || '' }); a.value = value || ''; return a; }
        function secret(key, isSet, hint, label, opts) {
            opts = opts || {};
            var ctl = opts.area ? area('mu-fwd-' + key, '', 3, isSet ? _('Empty: keep the saved value') : opts.ph)
                : input('mu-fwd-' + key, { type: opts.password ? 'password' : 'text', autocomplete: 'new-password', spellcheck: 'false',
                    placeholder: isSet ? _('Empty: keep the saved value') : (opts.ph || '') });
            F[key] = ctl;
            fb.appendChild(field(label, ctl));
            if (isSet) {
                if (hint) fb.appendChild(note(cockpit.format(_('Saved: $0'), hint)));
                var c = check('mu-fwd-' + key + '-clear', _('Remove the saved value'), false);
                F[key + '_clear'] = c.box;
                fb.appendChild(c.node);
            }
        }
        function text(key, label, value, ph) { F[key] = input('mu-fwd-' + key, { value: value || '', placeholder: ph || '', spellcheck: 'false' }); fb.appendChild(field(label, F[key])); }
        function box(key, label, v, disabled) { var c = check('mu-fwd-' + key, label, !!v); if (disabled) c.box.disabled = true; F[key] = c.box; fb.appendChild(c.node); }
        function paintLog(st) {
            clear(logb);
            logb.appendChild(row(_('Waiting to be delivered'), String(st.queued || 0)));
            var targets = { webhook: _('Webhook'), telegram: 'Telegram', email: _('E-mail'), sms: _('SMS to another phone') };
            var results = { sent: _('Delivered'), retry: _('Will retry'), failed: _('Failed'), skipped: _('Filtered out'), dropped: _('Dropped') };
            var log = st.log || [];
            if (!log.length) logb.appendChild(note(_('No deliveries yet.')));
            else logb.appendChild(table([ _('Time'), _('Message'), _('Target'), _('Result'), _('Detail') ], log.map(function (e) {
                return [ new Date((e.time || 0) * 1000).toLocaleString(), e.id === 'test' ? _('Test') : '#' + e.id,
                    targets[e.target] || '-', results[e.result] || e.result, e.detail || '' ];
            })));
            var cl = button(_('Clear the log'), function () { act(cl, 'sms.forward_log_clear', {}, { done: refresh }); });
            var rf = button(_('Refresh'), function () { refresh(); });
            logb.appendChild(actions([ rf ].concat(log.length ? [ cl ] : [])));
        }
        function paint(st) {
            F = {};
            var mail = !!st.email_available;
            fb.appendChild(note(_('Messages that reach the SIM while forwarding is on are sent on (the device looks for new ones every 20 seconds). A failed delivery is tried again after 30 s, then after twice as long each time (an hour at most), ten times in all.')));
            box('enabled', _('Forward received messages'), st.enabled);
            F.template = area('mu-fwd-template', st.template || FWD_TEMPLATE, 3);
            fb.appendChild(field(_('Message template (placeholders: {sender}, {time}, {text}, {device})'), F.template));
            F.allow = area('mu-fwd-allow', st.allow, 2);
            fb.appendChild(field(_('Only these senders (one per line; empty: every sender; a * at the end matches the start)'), F.allow));
            F.deny = area('mu-fwd-deny', st.deny, 2);
            fb.appendChild(field(_('Never these senders'), F.deny));
            F.keywords = area('mu-fwd-keywords', st.keywords, 2);
            fb.appendChild(field(_('Only messages with one of these words (one per line; empty: every message)'), F.keywords));

            fb.appendChild(el('h3', { text: _('Webhook') }));
            box('webhook', _('On'), st.webhook);
            secret('webhook_url', st.webhook_url_set, st.webhook_url_hint, _('Address (http:// or https://)'), { ph: 'https://example.com/sms' });
            var fmt = st.webhook_format === 'form' || st.webhook_format === 'text' ? st.webhook_format : 'json';
            F.webhook_format = select('mu-fwd-webhook_format', [ [ 'json', 'JSON' ], [ 'form', _('Form (URL-encoded)') ], [ 'text', _('Plain text') ] ], fmt);
            fb.appendChild(field(_('Body (fields: sender, time, text, device, message)'), F.webhook_format));
            F.webhook_body = area('mu-fwd-webhook_body', st.webhook_body, 4, '{"text":"{message}"}');
            fb.appendChild(field(_('Custom body (empty: the standard fields; placeholders {sender}, {time}, {text}, {device}, {message}, each escaped for the type; shown again here: keep secrets in the address or a header)'), F.webhook_body));
            var BODIES = { wecom: [ '{"msgtype":"text","text":{"content":"{message}"}}', 'errcode=0' ],
                feishu: [ '{"msg_type":"text","content":{"text":"{message}"}}', 'code=0' ] };
            var bp = select('mu-fwd-body-preset', [ [ '', _('Presets') ], [ 'wecom', 'WeCom, DingTalk' ], [ 'feishu', 'Feishu, Lark' ] ], '');
            bp.addEventListener('change', function () {
                var b = Object.prototype.hasOwnProperty.call(BODIES, bp.value) ? BODIES[bp.value] : null;
                if (b) {
                    F.webhook_body.value = b[0];
                    F.webhook_format.value = 'json';
                    if (!F.webhook_expect.value.trim()) F.webhook_expect.value = b[1];
                }
                bp.value = '';
            });
            fb.appendChild(field(_('Body presets'), bp));
            secret('webhook_headers', st.webhook_headers_set, st.webhook_header_names, _('Extra headers (one "Name: value" per line)'), { area: true, ph: 'Authorization: Bearer ...' });
            text('webhook_expect', _('The answer must contain (key=value; empty: every HTTP 2xx answer counts)'), st.webhook_expect, 'errcode=0');
            var ep = select('mu-fwd-expect-preset', [ [ '', _('Presets') ], [ 'errcode=0', 'DingTalk, WeCom: errcode=0' ], [ 'code=0', 'Feishu, Lark: code=0' ],
                [ 'code=200', 'Bark: code=200' ], [ 'ok=true', 'Slack API, Telegram API: ok=true' ] ], '');
            ep.addEventListener('change', function () { if (ep.value) F.webhook_expect.value = ep.value; ep.value = ''; });
            fb.appendChild(field(_('Answer presets'), ep));
            fb.appendChild(note(_('With key=value the answer must be a JSON object with that value under that key at its top level, or the delivery fails. Only the result of the check is logged, never the answer.')));

            fb.appendChild(el('h3', { text: 'Telegram' }));
            box('telegram', _('On'), st.telegram);
            secret('telegram_token', st.telegram_token_set, st.telegram_token_hint, _('Bot token'), { password: true, ph: '123456789:AA...' });
            text('telegram_chat', _('Chat ID (a number, or @channel)'), st.telegram_chat);

            fb.appendChild(el('h3', { text: _('E-mail') }));
            if (!mail) fb.appendChild(note(_('E-mail needs msmtp, which this system does not have (sudo apt install msmtp).')));
            box('email', _('On'), st.email && mail, !mail);
            text('email_host', _('Mail server'), st.email_host, 'smtp.example.com');
            text('email_port', _('Port'), st.email_port || '587');
            F.email_tls = select('mu-fwd-email_tls', [ [ 'starttls', 'STARTTLS' ], [ 'tls', 'TLS' ], [ 'off', _('None') ] ], st.email_tls || 'starttls');
            fb.appendChild(field(_('Security'), F.email_tls));
            text('email_user', _('User name'), st.email_user);
            secret('email_password', st.email_password_set, '', _('Password'), { password: true });
            text('email_from', _('From'), st.email_from);
            text('email_to', _('To'), st.email_to);

            fb.appendChild(el('h3', { text: _('SMS to another phone') }));
            box('sms', _('On'), st.sms);
            text('sms_number', _('Phone number (sent as one SMS: a longer message is cut short)'), st.sms_number, '+90...');

            var save = button(_('Save'), function () {
                var req = {}, clear = [];
                [ 'enabled', 'webhook', 'telegram', 'email', 'sms' ].forEach(function (k) { req[k] = !!F[k].checked; });
                [ 'template', 'allow', 'deny', 'keywords', 'webhook_body' ].forEach(function (k) { req[k] = F[k].value; });
                [ 'webhook_url', 'webhook_expect', 'telegram_token', 'telegram_chat', 'email_host', 'email_port', 'email_user',
                    'email_from', 'email_to', 'sms_number' ].forEach(function (k) { req[k] = F[k].value.trim(); });
                req.webhook_format = F.webhook_format.value; req.email_tls = F.email_tls.value;
                req.webhook_headers = F.webhook_headers.value; req.email_password = F.email_password.value;
                [ 'webhook_url', 'webhook_headers', 'telegram_token', 'email_password' ].forEach(function (k) {
                    if (F[k + '_clear'] && F[k + '_clear'].checked) clear.push(k);
                });
                req.clear = clear.join(' ');
                act(save, 'sms.forward_set', req, { ok: _('Saved'), done: reload });
            }, 'primary');
            var test = button(_('Send a test'), function () {
                act(test, 'sms.forward_test', {}, { ok: _('A test with the saved settings started; its result comes in the delivery log.'),
                    done: function () { setTimeout(refresh, 4000); } });
            });
            fb.appendChild(actions([ save, test ]));
            fb.appendChild(note(_('Tokens, passwords and the webhook address and headers stay on the device and are never shown again. A test uses the saved settings: save first.')));
            paintLog(st);
        }
        function refresh() {
            return api('sms.forward', {}).then(function (r) {
                var st = r.data || {};
                if (!fb.firstChild) paint(st); else paintLog(st);
            }, function (ex) { clear(fb).appendChild(note(errText(ex), 'error')); });
        }
        /* the whole form again (after a save: the secrets' fields empty, what is saved said again) */
        function reload() {
            clear(fb).appendChild(note(_('Loading...')));
            return api('sms.forward', {}).then(function (r) { clear(fb); paint(r.data || {}); },
                function (ex) { clear(fb).appendChild(note(errText(ex), 'error')); });
        }
        reload();
    }

    /* Mobile: mobile data and the APN, data roaming, the SIM (dual SIM) and its card's state, the radio per SIM,
     * VoLTE/VoNR, TTL, USSD, the network, band and cell locks, recovery */
    pages.mobile = function (root) {
        var dc = root.appendChild(card(_('Mobile data'), 'mu-md'));
        var db = dc.appendChild(el('div'));
        var ac = root.appendChild(card(_('APN'), 'mu-apn'));
        var ab = ac.appendChild(el('div'));
        var sc = root.appendChild(card(_('SIM'), 'mu-sim'));
        var sb = sc.appendChild(el('div'));
        function refresh() {
            return load(db, 'data.get', {}, function (o) {
                var up = o.state === 'up';
                db.appendChild(row(_('State'), dataState(o.state)));
                db.appendChild(row(_('Interface'), o.iface));
                db.appendChild(row(_('Address'), o.address));
                signalRows(db, o.signal);
                var b = button(up ? _('Turn mobile data off') : _('Turn mobile data on'), function () {
                    act(b, 'data.power', { state: up ? 'off' : 'on' }, { done: function () { setTimeout(refresh, 3000); },
                        confirm: up ? _('Turn mobile data off? Clients of the device lose the internet until it is on again.') : null });
                });
                db.appendChild(actions([ b ]));
                /* data roaming (FINDINGS 120): off keeps data down while the SIM is registered as roaming */
                var ro = o.roaming;
                if (ro && o.roaming_switch) {
                    db.appendChild(row(_('Data roaming'), ro.held ? _('Off: data held back while roaming') : ro.allowed ? _('Allowed') : _('Off')));
                    var rb = button(ro.allowed ? _('Turn data roaming off') : _('Allow data roaming'), function () {
                        act(rb, 'data.set', { roaming: !ro.allowed }, { done: refresh, confirm: ro.allowed
                            ? _('While the SIM is registered on a roaming network, mobile data stays off; SMS, calls and the registration are not affected. Some SIMs report roaming at home (an MVNO, national roaming): there, data would stop as well.')
                            : _('Mobile data is used on roaming networks too, which can cost extra. Some SIMs report roaming at home (an MVNO, national roaming) and need this to have data at all.') });
                    });
                    db.appendChild(actions([ rb ]));
                }
                apn(o);
                sim(o.sim);
            });
        }
        function apn(o) {
            clear(ab);
            ab.appendChild(note(_('Empty: the network\'s own APN. A change reconnects mobile data.')));
            var a = input('mu-apn-name', { value: o.apn || '', maxlength: '100', placeholder: 'internet' });
            var pdp = select('mu-apn-pdp', [ [ 'IP', 'IPv4' ], [ 'IPV6', 'IPv6' ], [ 'IPV4V6', 'IPv4 + IPv6' ] ], o.pdp || 'IP');
            var auth = select('mu-apn-auth', [ [ 'none', _('None') ], [ 'pap', 'PAP' ], [ 'chap', 'CHAP' ], [ 'both', 'PAP / CHAP' ] ], o.auth || 'none');
            var user = input('mu-apn-user', { value: o.user || '', maxlength: '64', autocomplete: 'off' });
            var pw = input('mu-apn-pw', { type: 'password', maxlength: '64', autocomplete: 'new-password',
                placeholder: o.password_set ? _('(unchanged)') : '' });
            var cl = check('mu-apn-clear', _('Remove the user name and password'), false);
            [ field(_('APN'), a), field(_('IP type'), pdp), field(_('Authentication'), auth), field(_('User name'), user),
                field(_('Password'), pw), cl.node ].forEach(function (n) { ab.appendChild(n); });
            var b = button(_('Save'), function () {
                var v = a.value.trim();
                if (v && !/^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$/.test(v)) { toast(_('The APN may hold letters, digits, dots and hyphens.'), 'error'); return; }
                var req = { apn: v, pdp: pdp.value };
                if (cl.box.checked) req.clear_credentials = true;
                else {
                    req.auth = auth.value;
                    req.user = user.value;
                    if (pw.value) req.password = pw.value;
                }
                act(b, 'data.set', req, { confirm: _('Save the APN settings? Mobile data reconnects (about half a minute).'),
                    done: function () { pw.value = ''; setTimeout(refresh, 3000); } });
            }, 'primary');
            ab.appendChild(actions([ b ]));
        }
        /* what the card in use says of itself (sim.state): kept for the boot, asked again on demand */
        function simState(box, fresh) {
            if (!has('sim.state')) return;
            var r0 = box.appendChild(row(_('Card'), '...'));
            api('sim.state', fresh ? { fresh: true } : {}).then(function (o) {
                var t = { ready: _('Ready'), locked: _('Locked (PIN or PUK needed)'), absent: _('No card in this slot') }[o.state] || _('Unknown');
                var nr = row(o.slot ? cockpit.format(_('Card (SIM $0)'), o.slot) : _('Card'), t);
                var ab = button(_('Ask the card'), function () { box.removeChild(nr); simState(box, true); });
                nr.appendChild(ab);
                box.insertBefore(nr, r0); box.removeChild(r0);
            }, function (ex) { box.removeChild(r0); box.appendChild(note(errText(ex), 'error')); });
        }
        function sim(s) {
            clear(sb);
            if (!s) { sb.appendChild(note(_('No SIM control on this device.'))); simState(sb); return; }
            simState(sb);
            sb.appendChild(row(_('SIM in use'), s.active ? cockpit.format(_('SIM $0'), s.active) : null));
            sb.appendChild(row(_('SIM at the next boot'), s.default ? cockpit.format(_('SIM $0'), s.default) : null));
            if (s.slot1_supported) {
                var bar = actions([]);
                [ 1, 2 ].forEach(function (n) {
                    var b = button(cockpit.format(_('Use SIM $0'), n), function () {
                        act(b, 'sim.select', { slot: n }, { done: refresh,
                            confirm: _('Change the SIM? Mobile data uses the other card from then on (at the next reboot where the device cannot switch live).') });
                    }, n === s.default ? 'primary' : '');
                    bar.appendChild(b);
                });
                sb.appendChild(bar);
            } else sb.appendChild(note(_('This device has one SIM slot in use.')));
            /* the ZTE V50's cards (sim.card, FINDINGS 204): two of them behind a switch on one SIM interface */
            var v = s.v50;
            if (v && has('sim.card')) {
                var names = [ _('Built-in SIM 1'), _('Built-in SIM 2'), _('Built-in SIM 3'), _('External SIM') ];
                var nm = function (n) { return (n === null || n === undefined) ? null : names[n] || null; };
                var none = v.saved_card === null || v.saved_card === undefined;
                sb.appendChild(row(_('Card in use'), nm(v.card)));
                sb.appendChild(row(_('Card at the next boot'), none ? _('As the bootloader sets them') : nm(v.saved_card)));
                sb.appendChild(note(_('Built-in SIM 3 shares SIM2, the external SIM shares SIM1. The card chosen here is switched in before the modem starts, at the next boot; without a choice the cards stay as the bootloader sets them.')));
                if (v.cards && v.switches) {
                    var cb = actions([]);
                    v.cards.forEach(function (n) {
                        var b = button(nm(n), function () {
                            act(b, 'sim.card', { card: n }, { done: refresh, confirm: _('Use this card from the next boot on?') });
                        }, n === v.saved_card ? 'primary' : '');
                        cb.appendChild(b);
                    });
                    var nb = button(_('As the bootloader sets them'), function () {
                        act(nb, 'sim.card', { card: 'none' }, { done: refresh });
                    }, none ? 'primary' : '');
                    cb.appendChild(nb);
                    sb.appendChild(cb);
                }
            }
        }
        refresh();

        if (has('radio.get')) {
            var rc = root.appendChild(card(_('Radio per SIM'), 'mu-radio'));
            var rb = rc.appendChild(el('div'));
            var rload = function () {
                return load(rb, 'radio.get', {}, function (o) {
                    var r = o.radio || {};
                    var en = Array.isArray(r.enabled) ? r.enabled : [];
                    en.forEach(function (v, i) {
                        if (i === 1 && !r.slot1_supported) return;
                        var b = button(v ? _('Turn off') : _('Turn on'), function () {
                            act(b, 'radio.set', { slot: i + 1, state: v ? 'off' : 'on' }, { done: rload,
                                confirm: v ? _('This SIM will have no calls, SMS or data, and its radio stays off after a reboot until you turn it on again.') : null });
                        });
                        rb.appendChild(el('div', { 'class': 'mu-row' }, [ el('span', { 'class': 'mu-k',
                            text: cockpit.format(_('SIM $0'), i + 1) + (r.active === i ? ' (' + _('in use') + ')' : '') }),
                            el('span', { 'class': 'mu-v', text: on(v) }), b ]));
                    });
                    var off = button(_('All radios off (airplane mode)'), function () {
                        act(off, 'radio.set', { slot: 'all', state: 'off' }, { done: rload,
                            confirm: _('Turn every SIM\'s radio off? No calls, SMS or mobile data until you turn them on again, also after a reboot.') });
                    }, 'danger');
                    var all = button(_('All radios on'), function () { act(all, 'radio.set', { slot: 'all', state: 'on' }, { done: rload }); });
                    rb.appendChild(actions([ r.all_off ? all : off ]));
                    rb.appendChild(note(_('Kept across reboots: a radio that is off stays off until it is turned on here.')));
                    if (r.busy) rb.appendChild(note(_('A radio change is under way.'), 'warn'));
                });
            };
            rload();
        }
        if (has('voice.get')) {
            var vc = root.appendChild(card(_('Voice over LTE and 5G'), 'mu-voice'));
            var vb = vc.appendChild(el('div'));
            var vload = function () {
                return load(vb, 'voice.get', {}, function (o) {
                    var v = o.voice || {};
                    vb.appendChild(note(_('VoLTE (on by default) and VoNR (the modem\'s own choice until set) per SIM; the card in use takes a change at once, the other one at its next start.')));
                    (v.slots || []).forEach(function (s, i) {
                        [ [ 'volte', 'VoLTE' ], [ 'vonr', 'VoNR' ] ].forEach(function (k) {
                            var cur = s[k[0]];
                            var b = button(cur ? _('Turn off') : _('Turn on'), function () {
                                act(b, 'voice.set', { slot: i + 1, kind: k[0], state: cur ? 'off' : 'on' }, { done: vload });
                            });
                            var live = v.live && v.slot === i ? v.live[k[0]] : undefined;
                            vb.appendChild(el('div', { 'class': 'mu-row' }, [
                                el('span', { 'class': 'mu-k', text: cockpit.format(_('SIM $0'), i + 1) + ' ' + k[1] }),
                                el('span', { 'class': 'mu-v', text: (cur === null || cur === undefined ? _('modem default') : on(cur)) +
                                    (live === undefined || live === null ? '' : ' (' + cockpit.format(_('now: $0'), on(live)) + ')') }), b ]));
                        });
                    });
                });
            };
            vload();
        }
        if (has('ttl.get')) {
            var tc = root.appendChild(card(_('TTL'), 'mu-ttl'));
            var tb = tc.appendChild(el('div'));
            var tload = function () {
                return load(tb, 'ttl.get', {}, function (o) {
                    tb.appendChild(note(_('Every packet that leaves through mobile data gets this TTL (IPv6: hop limit), so tethered devices look like the device itself (64).')));
                    tb.appendChild(row(_('TTL'), o.enabled ? String(o.value) : _('not changed')));
                    tb.appendChild(row(_('Method'), o.backend));
                    var v = input('mu-ttl-value', { type: 'number', min: '1', max: '255', value: String(o.value || 64) });
                    tb.appendChild(field(_('New TTL'), v));
                    var s = button(_('Set'), function () {
                        var n = Number(v.value);
                        if (!(n >= 1 && n <= 255 && Math.floor(n) === n)) { toast(_('The TTL is a whole number from 1 to 255.'), 'error'); return; }
                        act(s, 'ttl.set', { value: String(n) }, { done: tload });
                    }, 'primary');
                    var off = button(_('Leave the TTL alone'), function () { act(off, 'ttl.set', { value: 'off' }, { done: tload }); });
                    tb.appendChild(actions([ s, off ]));
                });
            };
            tload();
        }
        if (has('ussd.send')) {
            var uc = root.appendChild(card(_('USSD'), 'mu-ussd'));
            var code = input('mu-ussd-code', { maxlength: '40', placeholder: '*100#' });
            var out = pre('');
            var us = button(_('Send'), function () {
                var c = code.value.trim();
                if (!/^[0-9*#+]{1,40}$/.test(c)) { toast(_('A USSD code holds digits, * and # (an answer in a menu: its number).'), 'error'); return; }
                busy(us, true);
                out.textContent = _('Waiting for the network...');
                api('ussd.send', { code: c }).then(function (r) { busy(us, false); out.textContent = r.text || ''; },
                    function (ex) { busy(us, false); out.textContent = errText(ex); });
            }, 'primary');
            var ux = button(_('End the session'), function () { act(ux, 'ussd.cancel', {}); });
            uc.appendChild(field(_('Code, or the answer to a menu'), code));
            uc.appendChild(actions([ us, ux ]));
            uc.appendChild(out);
        }
        if (has('lock.get') && S.features.locks) locksCard(root);
        else {
            var lc = root.appendChild(card(_('Network, band and cell locks'), 'mu-locks'));
            lc.appendChild(note(_('This system has no lock program yet (an older image): update it. Mode and band locks set elsewhere stay in the modem and apply here too.')));
        }
        recovery(root.appendChild(card(_('Recovery'), 'mu-recovery')));
    };

    /* SMS: the SIM's messages (reassembled), send, delete (every part of a message) */
    pages.sms = function (root) {
        var send = root.appendChild(card(_('Send a message'), 'mu-sms-send'));
        var list = root.appendChild(card(_('Messages'), 'mu-sms-list'));
        var num = input('mu-sms-number', { type: 'tel', placeholder: '+90...', maxlength: '21' });
        var text = el('textarea', { id: 'mu-sms-text', rows: '3', maxlength: '480' });
        var sb = button(_('Send'), function () {
            var n = num.value.trim(), t = text.value;
            if (!/^\+?[0-9]{1,20}$/.test(n)) { toast(_('Enter a phone number (digits, an optional + first).'), 'error'); return; }
            if (!t.trim()) { toast(_('The message is empty.'), 'error'); return; }
            act(sb, 'sms.send', { number: n, text: t }, { ok: _('Sent'), done: function () { text.value = ''; } });
        }, 'primary');
        send.appendChild(field(_('Number'), num));
        send.appendChild(field(_('Text'), text));
        send.appendChild(actions([ sb ]));
        var rb = button(_('Refresh'), function () { refresh(); });
        list.appendChild(actions([ rb ]));
        var body = list.appendChild(el('div'));
        function refresh() {
            clear(body).appendChild(note(_('Reading the SIM...')));
            busy(rb, true);
            return api('sms.list', {}).then(function (r) {
                busy(rb, false);
                clear(body);
                var msgs = (r.data && r.data.messages) || [];
                if (!msgs.length) { body.appendChild(note(_('No messages.'))); return; }
                msgs.slice().reverse().forEach(function (m) {
                    var idx = (m.index || []).filter(function (i) { return /^[0-9]{1,6}$/.test(String(i)); });
                    var del = button(_('Delete'), function () {
                        if (!confirmed(_('Delete this message from the SIM?'))) return;
                        busy(del, true);
                        idx.reduce(function (p, i) { return p.then(function () { return api('sms.delete', { id: String(i) }); }); },
                            Promise.resolve()).then(refresh, function (ex) { busy(del, false); toast(errText(ex), 'error'); });
                    }, 'danger');
                    var head = show(m.sender) + '  ' + show(m.time) + (m.parts > 1 ? '  (' + cockpit.format(_('$0 parts'), m.parts) + ')' : '');
                    body.appendChild(el('div', { 'class': 'mu-msg' }, [ el('div', { 'class': 'mu-msg-head', text: head }),
                        el('div', { 'class': 'mu-msg-text', text: show(m.text) }), del ]));
                });
            }, function (ex) { busy(rb, false); clear(body).appendChild(note(errText(ex), 'error')); });
        }
        refresh();
        if (has('sms.forward_set') && S.features.forward) forwardCard(root);
        else {
            var fc = root.appendChild(card(_('SMS forwarding'), 'mu-sms-forward'));
            fc.appendChild(note(_('This system has no SMS forwarding yet (an older image): update it.')));
        }
    };

    /* AT terminal: one command at a time through mu300-at (mu300-atd owns the modem's channel), the answers kept below */
    pages.at = function (root) {
        var c = root.appendChild(card(_('AT terminal'), 'mu-at'));
        c.appendChild(note(_('Commands go to the modem as they are: a wrong one can drop the connection or change the modem\'s settings for good (also for Android).'), 'warn'));
        var cmd = input('mu-at-cmd', { maxlength: '512', placeholder: 'AT+CSQ', autocomplete: 'off', spellcheck: 'false' });
        var to = select('mu-at-timeout', [ [ 10, '10 s' ], [ 30, '30 s' ], [ 60, '60 s' ] ], 10);
        var out = pre('');
        function send(text) {
            var t = String(text).trim();
            if (!/^[Aa][Tt][\x20-\x7e]*$/.test(t)) { toast(_('An AT command starts with AT and holds printable ASCII only.'), 'error'); return; }
            busy(sb, true);
            out.textContent += '> ' + t + '\n';
            api('at.send', { command: t, timeout: Number(to.value) }).then(function (r) {
                busy(sb, false);
                out.textContent += (r.text || '') + '\n\n';
                out.scrollTop = out.scrollHeight;
                history();
            }, function (ex) { busy(sb, false); out.textContent += errText(ex) + '\n\n'; history(); });
        }
        var sb = button(_('Send'), function () { send(cmd.value); }, 'primary');
        cmd.addEventListener('keydown', function (e) { if (e.key === 'Enter') send(cmd.value); });
        c.appendChild(field(_('Command'), cmd));
        c.appendChild(field(_('Wait for the answer at most'), to));
        var quick = [ 'AT+CGMR', 'AT+CSQ', 'AT+CESQ', 'AT+COPS?', 'AT+CEREG?', 'AT+C5GREG?', 'AT+CGDCONT?', 'AT+CGPADDR', 'AT+CPIN?' ];
        c.appendChild(actions([ sb, button(_('Clear'), function () { out.textContent = ''; }) ]));
        c.appendChild(actions(quick.map(function (q) { return button(q, function () { cmd.value = q; send(q); }); })));
        c.appendChild(out);
        /* the commands sent this boot (at.history, the same list as the OpenWrt control panel's AT terminal): one click
         * puts a command back in the field, it is not sent again by itself. A PIN or password is never in it. */
        var hc = has('at.history') ? root.appendChild(card(_('History (this boot)'), 'mu-at-history')) : null;
        var hb = hc ? hc.appendChild(el('div')) : null;
        function history() {
            if (!hb) return;
            api('at.history', {}).then(function (r) {
                clear(hb);
                var h = r.history || [];
                if (!h.length) { hb.appendChild(note(_('No commands yet.'))); return; }
                hb.appendChild(note(_('Newest first; a click puts the command in the field above.')));
                hb.appendChild(table([ _('Time'), _('Command') ], h.map(function (x) {
                    var b = button(String(x.command || ''), function () { cmd.value = String(x.command || ''); });
                    return [ x.time, b ];
                })));
            }, function (ex) { clear(hb).appendChild(note(errText(ex), 'error')); });
        }
        history();
    };

    /* Data usage: mu300-traffic's count (per day, per billing cycle) and the monthly cap */
    pages.usage = function (root) {
        var sc = root.appendChild(card(_('Data usage'), 'mu-traffic'));
        var sb = sc.appendChild(el('div'));
        var cc = root.appendChild(card(_('Billing cycle and cap'), 'mu-traffic-set'));
        var cb = cc.appendChild(el('div'));
        var hc = root.appendChild(card(_('History'), 'mu-traffic-days'));
        var hb = hc.appendChild(el('div'));
        var GIB = 1073741824;
        function set(btn, key, value, conf) { act(btn, 'traffic.set', { key: key, value: value }, { done: refresh, confirm: conf }); }
        function refresh() {
            return load(sb, 'traffic.get', {}, function (o) {
                var t = o.traffic || {};
                if (t.today) sb.appendChild(row(_('Today'), fmtBytes(Number(t.today.rx) + Number(t.today.tx)) + '  (↓ ' + fmtBytes(t.today.rx) + ', ↑ ' + fmtBytes(t.today.tx) + ')'));
                if (t.cycle) {
                    sb.appendChild(row(_('This billing cycle'), fmtBytes(t.cycle.used)));
                    sb.appendChild(row(_('Cycle'), t.cycle.start + ' - ' + t.cycle.last + ' (' + cockpit.format(_('$0 days left'), show(t.cycle.days_left)) + ')'));
                }
                var cap = t.cap || {};
                sb.appendChild(row(_('Monthly cap'), cap.bytes ? fmtBytes(cap.bytes) : _('none')));
                if (cap.bytes && t.cycle) sb.appendChild(row(_('Of the cap'), Math.round(100 * t.cycle.used / cap.bytes) + ' %'));
                if (cap.cut_active) sb.appendChild(note(_('The cap is reached: mobile data is off until the next cycle.'), 'warn'));
                else if (cap.level === 'warn') sb.appendChild(note(_('Close to the monthly cap.'), 'warn'));
                sb.appendChild(row(_('Interface counted'), t.device));
                if (!t.clock) sb.appendChild(note(_('The clock is not set yet (no network time): new traffic waits to be put on its day.')));

                clear(cb);
                var rd = input('mu-tr-reset', { type: 'number', min: '1', max: '31', value: String(t.reset_day || 1) });
                var capIn = input('mu-tr-cap', { type: 'number', min: '0', step: '0.5', value: cap.bytes ? String(+(cap.bytes / GIB).toFixed(2)) : '0' });
                var warn = input('mu-tr-warn', { type: 'number', min: '1', max: '100', value: String(cap.warn || 90) });
                var cut = check('mu-tr-cut', _('Turn mobile data off at the cap until the next cycle'), !!cap.cut);
                cb.appendChild(field(_('The day a billing cycle starts (31: the month\'s last day)'), rd));
                cb.appendChild(field(_('Monthly cap in GiB (0: none)'), capIn));
                cb.appendChild(field(_('Warn at this percentage of the cap'), warn));
                cb.appendChild(cut.node);
                var save = button(_('Save'), function () {
                    var d = Number(rd.value), g = Number(capIn.value), w = Number(warn.value);
                    if (!(d >= 1 && d <= 31 && Math.floor(d) === d) || !(g >= 0) || !(w >= 1 && w <= 100 && Math.floor(w) === w)) {
                        toast(_('Check the values: a day from 1 to 31, a cap of 0 or more, a percentage from 1 to 100.'), 'error'); return;
                    }
                    var steps = [ [ 'reset_day', d ], [ 'cap', String(Math.round(g * GIB)) ], [ 'warn', w ], [ 'cut', cut.box.checked ? 'on' : 'off' ] ];
                    busy(save, true);
                    steps.reduce(function (p, s) { return p.then(function () { return api('traffic.set', { key: s[0], value: s[1] }); }); },
                        Promise.resolve()).then(function () { busy(save, false); toast(_('Saved'), 'ok'); refresh(); },
                        function (ex) { busy(save, false); toast(errText(ex), 'error'); });
                }, 'primary');
                cb.appendChild(actions([ save ]));
                var used = input('mu-tr-used', { type: 'number', min: '0', step: '0.01' });
                cb.appendChild(field(_('What the operator says this cycle has used, in GiB (corrects the count)'), used));
                var ub = button(_('Correct the count'), function () {
                    var g = Number(used.value);
                    if (!(g >= 0) || used.value === '') { toast(_('Enter an amount in GiB.'), 'error'); return; }
                    set(ub, 'used', String(Math.round(g * GIB)));
                });
                var clr = button(_('Forget every count'), function () {
                    set(clr, 'clear', 'yes', _('Forget every count (days and cycles)? Counting goes on from now.'));
                }, 'danger');
                cb.appendChild(actions([ ub, clr ]));

                clear(hb);
                hb.appendChild(el('h3', { text: _('Days') }));
                hb.appendChild(table([ _('Day'), _('Down'), _('Up'), _('Total') ], (t.days || []).map(function (d) {
                    return [ d.date, fmtBytes(d.rx), fmtBytes(d.tx), fmtBytes(Number(d.rx) + Number(d.tx)) ];
                })));
                hb.appendChild(el('h3', { text: _('Billing cycles') }));
                hb.appendChild(table([ _('Cycle'), _('Down'), _('Up'), _('Total') ], (t.cycles || []).map(function (d) {
                    return [ d.start + ' - ' + d.last, fmtBytes(d.rx), fmtBytes(d.tx), fmtBytes(Number(d.rx) + Number(d.tx) + Number(d.adj || 0)) ];
                })));
            });
        }
        refresh();
    };

    /* Wi-Fi: the hotspot (name, password, band, channel, hidden, on/off, its clients) and joining another network as
     * the uplink (wifi-client) */
    pages.wifi = function (root) {
        var hs = root.appendChild(card(_('Hotspot'), 'mu-hotspot'));
        var hb = hs.appendChild(el('div'));
        var cl = root.appendChild(card(_('Join a Wi-Fi network'), 'mu-wifi-client'));
        function refresh() {
            return load(hb, 'wifi.get', {}, function (o) {
                hb.appendChild(row(_('State'), on(o.enabled) + (o.client_mode ? ' (' + _('the radio is a client of another network') + ')' : '')));
                hb.appendChild(row(_('Name (SSID)'), o.ssid));
                hb.appendChild(row(_('Band'), o.band === '2.4' ? '2.4 GHz' : '5 GHz'));
                hb.appendChild(row(_('Channel'), o.channel + (o.channel_live ? ' (' + cockpit.format(_('now $0'), o.channel_live) + ')' : '')));
                hb.appendChild(row(_('Security'), String(o.security || '').toUpperCase()));
                hb.appendChild(row(_('Country'), o.country));
                var p = button(o.enabled ? _('Turn the hotspot off') : _('Turn the hotspot on'), function () {
                    act(p, 'wifi.power', { state: o.enabled ? 'off' : 'on' }, { done: function () { setTimeout(refresh, 4000); },
                        confirm: o.enabled ? _('Turn the hotspot off? Devices on it lose the connection; if you reach this page over the hotspot, you lose it too.') : null });
                }, o.enabled ? 'danger' : 'primary');
                hb.appendChild(actions([ p ]));

                var ssid = input('mu-hs-ssid', { maxlength: '32', value: o.ssid || '' });
                var psk = input('mu-hs-psk', { type: 'password', maxlength: '63', autocomplete: 'new-password', placeholder: o.password_set ? _('(unchanged)') : '' });
                var band = select('mu-hs-band', [ [ '5', '5 GHz' ], [ '2.4', '2.4 GHz' ] ], o.band);
                var chan = el('select', { id: 'mu-hs-chan' });
                var fill = function () {
                    clear(chan);
                    var list = (o.channels || {})[band.value] || [];
                    [ 'auto' ].concat(list).forEach(function (c) {
                        chan.appendChild(el('option', { value: String(c), text: c === 'auto' ? _('Automatic') : String(c) }));
                    });
                    chan.value = band.value === o.band && o.channel ? String(o.channel) : 'auto';
                };
                band.addEventListener('change', fill);
                fill();
                var hid = check('mu-hs-hidden', _('Hidden network (the name is not broadcast)'), !!o.hidden);
                [ field(_('Name (SSID)'), ssid), field(_('New password (empty: unchanged)'), psk), field(_('Band'), band),
                    field(_('Channel'), chan), hid.node ].forEach(function (n) { hb.appendChild(n); });
                var save = button(_('Save and restart the hotspot'), function () {
                    var req = {};
                    if (ssid.value !== (o.ssid || '')) {
                        if (!ssid.value || new TextEncoder().encode(ssid.value).length > 32 || /^ | $/.test(ssid.value)) { toast(_('The name must be 1 to 32 bytes, without a space first or last.'), 'error'); return; }
                        req.ssid = ssid.value;
                    }
                    if (psk.value) {
                        if (!/^[\x20-\x7e]{8,63}$/.test(psk.value)) { toast(_('The password must be 8 to 63 printable ASCII characters.'), 'error'); return; }
                        req.password = psk.value;
                    }
                    if (band.value !== o.band) req.band = band.value;
                    if (chan.value !== String(o.channel) || req.band) req.channel = chan.value;
                    if (hid.box.checked !== !!o.hidden) req.hidden = hid.box.checked;
                    if (!Object.keys(req).length) { toast(_('Nothing changed.'), 'ok'); return; }
                    act(save, 'wifi.set', req, { confirm: _('Restart the hotspot? Devices on it are disconnected and must join again (with the new password, if it changed).'),
                        done: function () { psk.value = ''; setTimeout(refresh, 5000); } });
                }, 'primary');
                hb.appendChild(actions([ save ]));
                hb.appendChild(el('h3', { text: _('Connected devices') }));
                if (!(o.clients || []).length) hb.appendChild(note(_('None.')));
                else hb.appendChild(table([ _('Name'), _('Address'), _('MAC') ], o.clients.map(function (c) { return [ c.name, c.ip, c.mac ]; })));
            });
        }
        refresh();

        cl.appendChild(note(_('The radio does one thing at a time: joining a network turns the hotspot off while it is joined. If you reach this page over the hotspot, you lose it.'), 'warn'));
        var cs = cl.appendChild(el('div'));
        var scan = pre('');
        var cssid = input('mu-wc-ssid', { maxlength: '32' });
        var cpsk = input('mu-wc-psk', { type: 'password', maxlength: '64', autocomplete: 'off' });
        var jb = button(_('Join'), function () {
            var s = cssid.value;
            if (!s || new TextEncoder().encode(s).length > 32) { toast(_('The name must be 1 to 32 bytes.'), 'error'); return; }
            var req = cpsk.value ? { ssid: s, password: cpsk.value } : { ssid: s, open: true };
            act(jb, 'wificlient.join', req, { confirm: _('Join this network? The hotspot is off while the device is a client of it.'),
                done: status });
            cpsk.value = '';
        }, 'primary');
        var lb = button(_('Leave and turn the hotspot back on'), function () {
            act(lb, 'wificlient.leave', {}, { done: status });
        });
        var sbtn = button(_('Look for networks'), function () {
            busy(sbtn, true);
            scan.textContent = _('Looking...');
            api('wificlient.scan', {}).then(function (r) { busy(sbtn, false); scan.textContent = r.text || ''; },
                function (ex) { busy(sbtn, false); scan.textContent = errText(ex); });
        });
        cl.appendChild(field(_('Network name'), cssid));
        cl.appendChild(field(_('Password (empty for an open network)'), cpsk));
        cl.appendChild(actions([ jb, lb, sbtn ]));
        cl.appendChild(scan);
        function status() {
            return load(cs, 'wificlient.status', {}, function (r) {
                cs.appendChild(row(_('Joined a network'), yes(r.active)));
                if (r.text) cs.appendChild(pre(r.text));
            });
        }
        status();
    };

    /* VPN: the module's state and switches, profiles, subscriptions, engines, settings, logs (mu300-vpn through
     * mu300-api; the module itself: mu300-extra install vpn) */
    pages.vpn = function (root) {
        var st = root.appendChild(card(_('VPN'), 'mu-vpn'));
        var sb = st.appendChild(el('div'));
        var rest = root.appendChild(el('div'));
        function refresh() {
            return load(sb, 'vpn.status', {}, function (r) {
                clear(rest);
                if (!r.installed) {
                    sb.appendChild(note(_('The VPN module is not installed. It brings the VPN (VLESS, VMess, Trojan, Shadowsocks, Hysteria2, TUIC, WireGuard and more) with a kill switch, profiles and subscriptions; it is downloaded from GitHub (the device must be online).')));
                    if (r.extra) {
                        var ib = button(_('Install the VPN module'), function () { act(ib, 'vpn.install', {}, { done: refresh }); }, 'primary');
                        sb.appendChild(actions([ ib ]));
                    }
                    return;
                }
                if (r.legacy) { sb.appendChild(note(_('This VPN module is too old for this page: update it (Updates tab).'), 'warn')); return; }
                status(r.status || {});
                profiles();
                subs();
                engines();
                settings();
                logs();
            });
        }
        function status(s) {
            sb.appendChild(row(_('Switch'), on(s.enabled)));
            sb.appendChild(row(_('Running'), yes(s.running)));
            sb.appendChild(row(_('Profile'), s.profile ? (s.profile.name || s.profile.id) + (s.profile.type ? ' (' + s.profile.type + ')' : '') : null));
            sb.appendChild(row(_('Engine'), s.engine && typeof s.engine === 'object' ? s.engine.name : s.engine));
            sb.appendChild(row(_('Tunnel'), s.tunnel && s.tunnel.up ? (s.tunnel.iface || '') + ' ' + _('up') : _('down')));
            var ks = s.kill_switch || {};
            sb.appendChild(row(_('Kill switch'), (ks.setting ? _('On') : _('Off')) + ', ' + (ks.active ? _('active') : _('inactive'))));
            var ip6 = s.ipv6 || {};
            sb.appendChild(row(_('IPv6'), ip6.setting));
            var exit = el('span', { 'class': 'mu-v', text: s.exit_ip ? String(s.exit_ip) : '-' });
            var xb = button(_('Find out'), function () {
                busy(xb, true);
                api('vpn.status', { exit_ip: true }).then(function (r) {
                    busy(xb, false);
                    exit.textContent = r.status && r.status.exit_ip ? String(r.status.exit_ip) : _('unknown');
                }, function (ex) { busy(xb, false); toast(errText(ex), 'error'); });
            });
            sb.appendChild(el('div', { 'class': 'mu-row' }, [ el('span', { 'class': 'mu-k', text: _('Exit address') }), exit, xb ]));
            if (s.missing_engine) sb.appendChild(note(cockpit.format(_('The profile needs the engine $0: install it below (Engines).'), s.missing_engine), 'warn'));
            var bar = sb.appendChild(actions([]));
            var tb = button(s.enabled ? _('Turn the VPN off') : _('Turn the VPN on'), function () {
                act(tb, 'vpn.service', { op: s.enabled ? 'off' : 'on' }, { done: refresh,
                    confirm: s.enabled && ks.setting ? _('Turn the VPN off? The kill switch goes with it: traffic leaves without the tunnel.') : null });
            }, s.enabled ? 'danger' : 'primary');
            bar.appendChild(tb);
            if (s.enabled) bar.appendChild(button(_('Restart'), function (e) { act(e.target, 'vpn.service', { op: 'restart' }, { done: refresh }); }));
            var kb = button(ks.setting ? _('Kill switch off') : _('Kill switch on'), function () {
                act(kb, 'vpn.killswitch', { value: ks.setting ? 'off' : 'on' }, { done: refresh,
                    confirm: ks.setting ? null : _('Turn the kill switch on? While the VPN is on and its tunnel is down, nothing leaves the device (its own traffic too) until the tunnel is back.') });
            });
            bar.appendChild(kb);
        }
        function profiles() {
            var c = rest.appendChild(card(_('Profiles'), 'mu-vpn-profiles'));
            var b = c.appendChild(el('div'));
            load(b, 'vpn.profiles', {}, function (r) {
                var list = Array.isArray(r.profiles) ? r.profiles : [];
                if (!list.length) b.appendChild(note(_('No profiles yet: add one below.')));
                list.forEach(function (p) {
                    var id = String(p.id || '');
                    if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(id)) return;
                    var what = [ p.type, p.protocol, p.transport, p.security ].filter(function (x) { return x; }).join(' / ');
                    var line = el('div', { 'class': 'mu-row' }, [ el('span', { 'class': 'mu-k', text: show(p.name) + (what ? '  (' + what + ')' : '') }) ]);
                    if (p.active) line.appendChild(el('span', { 'class': 'mu-v', text: _('in use') }));
                    else {
                        var u = button(_('Use'), function () { act(u, 'vpn.use', { id: id }, { done: refresh }); });
                        line.appendChild(u);
                    }
                    var rn = button(_('Rename'), function () {
                        var n = window.prompt(_('New name:'), String(p.name || ''));
                        if (n === null) return;
                        act(rn, 'vpn.rename', { id: id, name: n.trim() }, { done: refresh });
                    });
                    var rm = button(_('Remove'), function () {
                        act(rm, 'vpn.remove', { id: id }, { confirm: cockpit.format(_('Remove the profile $0?'), show(p.name)), done: refresh });
                    }, 'danger');
                    line.appendChild(rn);
                    line.appendChild(rm);
                    if (p.engine && p.engine_installed === false) line.appendChild(el('span', { 'class': 'mu-note mu-warn', text: cockpit.format(_('needs $0'), p.engine) }));
                    b.appendChild(line);
                });
            });
            c.appendChild(el('h3', { text: _('Add a profile') }));
            var data = el('textarea', { id: 'mu-vpn-data', rows: '4', spellcheck: 'false', autocomplete: 'off',
                placeholder: 'vless://... | vmess://... | trojan://... | ss://... | hysteria2://... | tuic://... | wireguard://... | [Interface] ...' });
            var name = input('mu-vpn-name', { maxlength: '64' });
            c.appendChild(field(_('A share link, or a configuration pasted whole'), data));
            c.appendChild(field(_('Name (optional)'), name));
            var ab = button(_('Add'), function () {
                var d = data.value.trim();
                if (!d) { toast(_('Paste a link or a configuration first.'), 'error'); return; }
                var link = /^(vless|vmess|trojan|ss|hysteria2|hy2|tuic|wireguard|wg):\/\/[!-~]+$/.test(d);
                var req = { source: link ? 'link' : 'text', data: link ? d : data.value };
                if (name.value.trim()) req.name = name.value.trim();
                act(ab, 'vpn.add', req, { ok: _('Profile added'), done: function () { data.value = ''; name.value = ''; refresh(); } });
            }, 'primary');
            var url = input('mu-vpn-url', { type: 'url', maxlength: '2048', placeholder: 'https://...' });
            var ub = button(_('Import from the address'), function () {
                if (!/^https:\/\/[A-Za-z0-9.-]{1,253}(:\d{1,5})?(\/[!-~]*)?$/.test(url.value)) { toast(_('An https:// address, please.'), 'error'); return; }
                var req = { url: url.value };
                if (name.value.trim()) req.name = name.value.trim();
                act(ub, 'vpn.add_url', req, { done: function () { url.value = ''; refresh(); } });
            });
            c.appendChild(actions([ ab ]));
            c.appendChild(field(_('Or a configuration file at an https:// address'), url));
            c.appendChild(actions([ ub ]));
        }
        function subs() {
            var c = rest.appendChild(card(_('Subscriptions'), 'mu-vpn-subs'));
            var b = c.appendChild(el('div'));
            load(b, 'vpn.subs', {}, function (r) {
                var list = Array.isArray(r.subscriptions) ? r.subscriptions : [];
                if (!list.length) { b.appendChild(note(_('No subscriptions.'))); return; }
                b.appendChild(table([ _('Name'), _('Server'), _('Profiles'), _('Updated'), _('Every'), '' ], list.filter(function (s) {
                    return /^[a-z0-9][a-z0-9-]{0,31}$/.test(String(s.id || ''));
                }).map(function (s) {
                    var rf = button(_('Refresh'), function () { act(rf, 'vpn.sub_refresh', { id: String(s.id) }, { done: refresh }); });
                    var rm = button(_('Remove'), function () {
                        act(rm, 'vpn.sub_remove', { id: String(s.id) }, { done: refresh,
                            confirm: cockpit.format(_('Remove the subscription $0 and its profiles?'), show(s.name)) });
                    }, 'danger');
                    return [ s.name, s.host, s.count, s.updated ? new Date(Number(s.updated) * 1000).toLocaleString() : null,
                        s.interval ? cockpit.format(_('$0 h'), s.interval) : _('by hand'), actions([ rf, rm ]) ];
                })));
                var all = button(_('Refresh all'), function () { act(all, 'vpn.sub_refresh', { id: 'all' }, { done: refresh }); });
                b.appendChild(actions([ all ]));
            });
            c.appendChild(el('h3', { text: _('Add a subscription') }));
            var sn = input('mu-vpn-sub-name', { maxlength: '64' });
            var su = input('mu-vpn-sub-url', { type: 'url', maxlength: '2048', placeholder: 'https://...' });
            var si = input('mu-vpn-sub-int', { type: 'number', min: '0', max: '720', value: '24' });
            c.appendChild(field(_('Name'), sn));
            c.appendChild(field(_('Address (https://)'), su));
            c.appendChild(field(_('Refresh every (hours, 0: by hand)'), si));
            var ab = button(_('Add'), function () {
                if (!sn.value.trim()) { toast(_('Give it a name.'), 'error'); return; }
                if (!/^https:\/\/[A-Za-z0-9.-]{1,253}(:\d{1,5})?(\/[!-~]*)?$/.test(su.value)) { toast(_('An https:// address, please.'), 'error'); return; }
                act(ab, 'vpn.sub_add', { name: sn.value.trim(), url: su.value, interval: Number(si.value) || 0 },
                    { done: function () { su.value = ''; refresh(); } });
            }, 'primary');
            c.appendChild(actions([ ab ]));
        }
        function engines() {
            var c = rest.appendChild(card(_('Engines'), 'mu-vpn-engines'));
            var b = c.appendChild(el('div'));
            if (!has('vpn.engines')) { b.appendChild(note(_('Update the device to see the engines here.'))); return; }
            load(b, 'vpn.engines', {}, function (r) {
                var list = Array.isArray(r.engines) ? r.engines : [];
                b.appendChild(table([ _('Engine'), _('Installed'), _('Version'), _('Size'), '' ], list.map(function (e) {
                    var name = String(e.name || '');
                    var btn = null;
                    if (!e.installed && /^(xray|sing-box|mihomo|dae)$/.test(name)) {
                        btn = button(_('Install'), function () { act(btn, 'vpn.engine_install', { name: name }, { done: refresh }); });
                    }
                    return [ name, yes(e.installed) + (e.installed && e.usable === false && e.reason ? ' (' + e.reason + ')' : ''),
                        e.version, e.size ? fmtBytes(e.size) : null, btn ];
                })));
            });
        }
        function settings() {
            var c = rest.appendChild(card(_('Settings'), 'mu-vpn-settings'));
            var b = c.appendChild(el('div'));
            load(b, 'vpn.settings', {}, function (r) {
                var s = r.settings || {};
                var v6 = select('mu-vpn-ipv6', [ [ 'auto', _('Automatic: through the tunnel when it carries IPv6, else blocked') ],
                    [ '1', _('Through the tunnel') ], [ '0', _('Off (blocked)') ] ], s.IPV6 || 'auto');
                b.appendChild(field(_('IPv6'), v6));
                var sv = button(_('Save'), function () { act(sv, 'vpn.setting', { key: 'IPV6', value: v6.value }, { done: refresh }); }, 'primary');
                b.appendChild(actions([ sv ]));
            });
        }
        function logs() {
            var c = rest.appendChild(card(_('Log'), 'mu-vpn-log'));
            var out = pre('');
            var lb = button(_('Show the log'), function () {
                busy(lb, true);
                api('vpn.logs', { lines: 200 }).then(function (r) { busy(lb, false); out.textContent = r.log || ''; out.scrollTop = out.scrollHeight; },
                    function (ex) { busy(lb, false); out.textContent = errText(ex); });
            });
            c.appendChild(actions([ lb ]));
            c.appendChild(out);
        }
        refresh();
    };

    /* Power: mu300-power's state and profiles, the LEDs, zram, the CPU profile and (EXPERIMENTAL) its voltage */
    pages.power = function (root) {
        var grid = root.appendChild(el('div'));
        if (has('power.get')) powerCards(grid);
        if (has('led.get')) ledCard(grid);
        if (has('zram.get')) zramCard(grid);
        if (has('cpu.get')) cpuCards(grid);
        if (!has('power.get')) grid.appendChild(note(_('Update the device to see its power settings here.')));
    };
    function powerCards(root) {
        var c = root.appendChild(card(_('Power'), 'mu-power'));
        var b = c.appendChild(el('div'));
        var pc = root.appendChild(card(_('Power profiles'), 'mu-power-profiles'));
        var pb = pc.appendChild(el('div'));
        var NAMES = { plugged: _('Plugged in'), battery: _('On battery'), saver: _('Saver') };
        function set(btn, key, value) { return act(btn, 'power.set', { key: key, value: value }, { done: refresh }); }
        function refresh() {
            return load(b, 'power.get', {}, function (o) {
                b.appendChild(row(_('State'), o.state === 'idle' ? _('Asleep: hotspot off') : o.state === 'charging-boot' ? _('Charging boot') : _('Awake')));
                b.appendChild(row(_('Profile in use'), (NAMES[o.profile] || o.profile) + (o.why ? ' (' + o.why + ')' : '')));
                b.appendChild(row(_('Power source'), o.supply === 'plugged' ? NAMES.plugged : o.supply === 'battery' ? NAMES.battery : o.supply));
                var bt = o.battery;
                if (bt) b.appendChild(row(_('Battery'), show(bt.capacity) + ' %, ' + show(bt.status) + ', ' + show(bt.mv) + ' mV, ' + show(bt.ma) + ' mA' +
                    (typeof bt.temp === 'number' ? ', ' + (bt.temp / 10).toFixed(1) + ' °C' : '')));
                else b.appendChild(row(_('Battery'), _('none (powered by USB)')));
                if (o.charge_off === 'temp') b.appendChild(note(_('Charging paused: battery temperature'), 'warn'));
                else if (o.charge_off === 'limit') b.appendChild(note(_('Charging paused: charge limit')));
                var conf = o.conf || {};
                clear(pb);
                var prof = select('mu-pw-profile', [ [ 'auto', _('Automatic: plugged in / on battery') ], [ 'plugged', NAMES.plugged ],
                    [ 'battery', NAMES.battery ], [ 'saver', NAMES.saver ] ], conf.PROFILE || 'auto');
                pb.appendChild(field(_('Profile'), prof));
                var ps = button(_('Use this profile'), function () { set(ps, 'PROFILE', prof.value); }, 'primary');
                pb.appendChild(actions([ ps ]));
                if (bt) {
                    var saver = input('mu-pw-saver', { type: 'number', min: '0', max: '100', value: show(conf.SAVER_BELOW) });
                    var cto = input('mu-pw-chargeto', { type: 'number', min: '0', max: '100', value: show(conf.CHARGE_TO) });
                    pb.appendChild(field(_('Switch to Saver under (%)'), saver));
                    pb.appendChild(field(_('Stop charging at (%)'), cto));
                    pb.appendChild(note(_('0 % never charges the battery (the device runs on the charger), 100 % charges it full; in between, charging stops at that level and starts again 5 points lower. Under 5 % the battery always charges. 80 % keeps the battery healthier on a device that stays plugged in.')));
                    var bs = button(_('Save'), function () {
                        busy(bs, true);
                        api('power.set', { key: 'SAVER_BELOW', value: Number(saver.value) }).then(function () {
                            return api('power.set', { key: 'CHARGE_TO', value: Number(cto.value) });
                        }).then(function () { busy(bs, false); toast(_('Saved'), 'ok'); refresh(); },
                            function (ex) { busy(bs, false); toast(errText(ex), 'error'); });
                    });
                    pb.appendChild(actions([ bs ]));
                    var pon = select('mu-pw-poweron', [ [ '0', _('Charge only, until a key starts it') ], [ '1', _('Start normally') ] ],
                        conf.POWER_ON_CHARGER === 1 ? '1' : '0');
                    pon.addEventListener('change', function () { set(null, 'POWER_ON_CHARGER', Number(pon.value)); });
                    pb.appendChild(field(_('When the charger switches the device on'), pon));
                }
                pb.appendChild(note(_('What each profile does while nobody is connected (no Wi-Fi client, no computer on USB): the Wi-Fi key, a computer on USB or the charger wake the device.')));
                var rows = [ 'plugged', 'battery', 'saver' ].map(function (p) {
                    var k = conf[p] || {};
                    var wi = input('mu-pw-' + p + '-wifi', { type: 'number', min: '0', max: '1440', value: show(k.WIFI_IDLE) });
                    wi.addEventListener('change', function () { set(null, p + '.WIFI_IDLE', Number(wi.value)); });
                    var ri = select('mu-pw-' + p + '-radio', [ [ 'keep', _('Keep as is') ], [ 'lte', _('LTE only (5G off)') ], [ 'off', _('Off') ] ], k.RADIO_IDLE);
                    ri.addEventListener('change', function () { set(null, p + '.RADIO_IDLE', ri.value); });
                    var li = select('mu-pw-' + p + '-leds', [ [ 'on', _('On') ], [ 'off', _('Off') ] ], k.LEDS_IDLE);
                    li.addEventListener('change', function () { set(null, p + '.LEDS_IDLE', li.value); });
                    var ci = select('mu-pw-' + p + '-cpu', [ [ 'full', _('Full') ], [ 'eco', _('Eco: big core off') ] ], k.CPU);
                    ci.addEventListener('change', function () { set(null, p + '.CPU', ci.value); });
                    return [ NAMES[p], wi, ri, li, ci ];
                });
                pb.appendChild(table([ _('Profile'), _('Hotspot off after (min, 0 = never)'), _('Modem when idle'), _('LEDs when idle'), _('CPU') ], rows));
                if (o.text) pb.appendChild(el('details', {}, [ el('summary', { text: _('mu300-power status') }), pre(o.text) ]));
            });
        }
        refresh();
    }
    function ledCard(root) {
        var c = root.appendChild(card(_('LEDs'), 'mu-led'));
        var b = c.appendChild(el('div'));
        function refresh() {
            return load(b, 'led.get', {}, function (o) {
                b.appendChild(note(_('How long the LEDs stay lit after a key press or a change; 0 keeps them on.')));
                var t = input('mu-led-timeout', { type: 'number', min: '0', max: '86400', value: o.timeout === null ? '' : String(o.timeout),
                    placeholder: o.device === 'u30air' ? '60' : '0' });
                b.appendChild(field(_('Seconds (empty: the device\'s default)'), t));
                var s = button(_('Save'), function () {
                    var n = Number(t.value);
                    if (t.value === '' || !(n >= 0 && n <= 86400 && Math.floor(n) === n)) { toast(_('A whole number of seconds, 0 or more.'), 'error'); return; }
                    act(s, 'led.set', { timeout: n }, { done: refresh });
                }, 'primary');
                var tb = button(_('Light each LED in turn'), function () { act(tb, 'led.set', { test: true }, { ok: _('Each LED lights for 2 seconds, one after the other.') }); });
                b.appendChild(actions([ s, tb ]));
            });
        }
        refresh();
    }
    function zramCard(root) {
        var c = root.appendChild(card(_('Compressed swap (zram)'), 'mu-zram'));
        var b = c.appendChild(el('div'));
        function refresh() {
            return load(b, 'zram.get', {}, function (o) {
                b.appendChild(note(_('Swap in RAM, compressed: room for about twice as many programs before the kernel has to end one.')));
                b.appendChild(row(_('State'), o.active ? _('On') : o.enabled ? _('Off now (on at the next boot)') : _('Off')));
                b.appendChild(pre(o.text || ''));
                var t = button(o.enabled ? _('Turn zram off') : _('Turn zram on'), function () {
                    act(t, 'zram.set', { state: o.enabled ? 'off' : 'on' }, { done: refresh });
                });
                b.appendChild(actions([ t ]));
            });
        }
        refresh();
    }
    function profileName(p) {
        return { saving: _('Power saving'), balanced: _('Balanced'), performance: _('Performance') }[p] || p;
    }
    function cpuCards(root) {
        var cpu = root.appendChild(card(_('CPU profile'), 'mu-cpu'));
        var volt = root.appendChild(card(_('CPU voltage offset (EXPERIMENTAL)'), 'mu-cpuv'));
        var cb = cpu.appendChild(el('div')), vb = volt.appendChild(el('div'));
        var WORD = 'EXPERIMENTAL';
        function arr(a) { return Array.isArray(a) && a.length === 3 ? a.map(String).join(', ') : '-'; }
        function refresh() {
            clear(vb);
            return load(cb, 'cpu.get', {}, function (o) {
                paintCpu(o.cpu || {});
                paintVolt(o.voltage);
            });
        }
        function paintCpu(s) {
            cb.appendChild(note(_('A profile sets the CPU governor and the per-cluster frequency limits, inside the hardware frequency table; the kernel keeps throttling at its thermal limit in every profile.')));
            var bar = el('div', { 'class': 'mu-actions' });
            (s.profiles || [ 'saving', 'balanced', 'performance' ]).forEach(function (p) {
                if (!/^[a-z]{1,20}$/.test(p)) return;
                var b = button(profileName(p), function () { act(b, 'cpu.profile', { name: p }, { done: refresh }); }, p === s.profile ? 'primary' : '');
                bar.appendChild(b);
            });
            cb.appendChild(bar);
            (s.policies || []).forEach(function (pol) {
                var mhz = function (v) { return typeof v === 'number' ? v + ' MHz' : '-'; };
                cb.appendChild(row('CPU ' + pol.cpus, pol.governor + ', ' + mhz(pol.cur) + ' (' + mhz(pol.min) + ' - ' + mhz(pol.max) + ')'));
            });
            if (s.throttling) cb.appendChild(note(_('Thermal throttling is active right now.'), 'warn'));
            // the cores and CPU0's low latency (FINDINGS 200), both saved and applied again at boot
            if (Array.isArray(s.cores) && s.cores.length) {
                cb.appendChild(note(_('Highlighted cores are online. A core taken offline stays offline, also after a reboot, until it is brought back. CPU0 always stays online.')));
                var cbar = el('div', { 'class': 'mu-actions' });
                s.cores.forEach(function (c) {
                    if (typeof c.cpu !== 'number') return;
                    var on = c.online === 1;
                    var b = button('CPU' + c.cpu, function () { act(b, 'cpu.core', { cpu: c.cpu, state: on ? 'off' : 'on' }, { done: refresh }); }, on ? 'primary' : '');
                    if (c.switchable !== 1) b.disabled = true;
                    cbar.appendChild(b);
                });
                cb.appendChild(cbar);
            }
            var ll = s.lowlatency || {};
            if (ll.supported === 1) {
                var lon = ll.saved === 1;
                cb.appendChild(note(_('Low latency keeps CPU0 out of its idle states, as ZTE\'s U30 Air firmware does: Wi-Fi delays are much shorter, and the device draws more power. Off by default.')));
                var lb = button(lon ? _('Low latency: on') : _('Low latency: off'), function () { act(lb, 'cpu.lowlatency', { state: lon ? 'off' : 'on' }, { done: refresh }); }, lon ? 'primary' : '');
                cb.appendChild(el('div', { 'class': 'mu-actions' }, [ lb ]));
            }
        }
        function paintVolt(v) {
            clear(vb);
            vb.appendChild(note(_('Experimental: changing the CPU voltage can make the device unstable, corrupt your data and permanently damage your device. Use it entirely at your own risk.'), 'danger-box'));
            if (!v || !v.kernel || v.phase === 'unsupported') {
                vb.appendChild(note(_('Not available with this kernel (a mainline kernel of this release or later has it).')));
                return;
            }
            var lim = v.limits || {};
            var lo = typeof lim.min === 'number' ? lim.min : -25000, hi = typeof lim.max === 'number' ? lim.max : 12500;
            var step = typeof lim.step === 'number' && lim.step > 0 ? lim.step : 3125;
            var phases = { off: _('Off: the stock voltages'), pending: _('Changes at the next reboot'),
                trial: _('Trial boot: kept once this boot has been up 10 minutes'), good: _('Kept: this offset passed its trial boot') };
            var reasons = { 'unclean-end': _('a boot with it ended without a clean shutdown'),
                'no-good-mark': _('its trial boot ended before 10 minutes'), 'invalid': _('invalid value'),
                'unknown-firmware': _('unknown SoC firmware'), 'kernel-refused': _('refused by the kernel'),
                'kernel-failed': _('the kernel could not start CPU scaling with it') };
            vb.appendChild(note(_('Offsets in µV for the three CPU domains (cores 0-3, 4-6 and 7), in whole steps. A new offset takes effect at the next reboot, as a trial: it is kept once that boot has been up 10 minutes, and dropped (back to 0) if a boot with it ends in a crash, a watchdog reset or a power cut.')));
            vb.appendChild(row(_('Running now (µV)'), arr(v.applied)));
            vb.appendChild(row(_('Saved for the next boot (µV)'), arr(v.requested)));
            vb.appendChild(row(_('State'), phases[v.phase] || String(v.phase || '-')));
            if (v.rejected && typeof v.rejected === 'object')
                vb.appendChild(note(cockpit.format(_('Taken back automatically: $0 µV ($1)'), arr(v.rejected.offsets),
                    reasons[v.rejected.reason] || String(v.rejected.reason || '?')), 'error'));
            var fwOk = v.firmware === 'known';
            if (!fwOk) vb.appendChild(note(_('The SoC firmware of this device is not one whose voltage encoding is known: only the stock voltages are possible.'), 'warn'));
            var cur = Array.isArray(v.requested) && v.requested.length === 3 ? v.requested : [ 0, 0, 0 ];
            var inputs = [];
            (Array.isArray(v.domains) ? v.domains : []).forEach(function (d) {
                var i = typeof d.domain === 'number' ? d.domain : -1;
                if (i < 0 || i > 2) return;
                var tbl = Array.isArray(d.table) ? d.table : [], top = tbl.length ? tbl[tbl.length - 1] : null;
                inputs[i] = input('mu-cpuv-in' + i, { type: 'number', min: String(lo), max: String(hi), step: String(step),
                    value: String(typeof cur[i] === 'number' ? cur[i] : 0) });
                vb.appendChild(field(cockpit.format(_('Domain $0 (cores $1), now $2 µV, highest step $3'), String(i), String(d.cpus || ''),
                    String(typeof d.offset === 'number' ? d.offset : '-'),
                    Array.isArray(top) ? (top[0] / 1000).toFixed(0) + ' MHz / ' + (top[1] / 1000).toFixed(2) + ' mV' : '-'), inputs[i]));
            });
            vb.appendChild(note(cockpit.format(_('Limits: $0 to $1 µV, in steps of $2 µV.'), String(lo), String(hi), String(step))));
            var word = input('mu-cpuv-word', { autocomplete: 'off', spellcheck: 'false', placeholder: WORD });
            vb.appendChild(field(cockpit.format(_('Type $0 to confirm:'), WORD), word));
            var save = button(_('Save for the next boot'), function () {
                var vals = [];
                for (var i = 0; i < 3; i++) {
                    var n = inputs[i] ? Number(inputs[i].value) : NaN;
                    if (!isFinite(n) || Math.floor(n) !== n || n < lo || n > hi || n % step !== 0) {
                        toast(cockpit.format(_('Each offset must be a multiple of $0 µV between $1 and $2.'), String(step), String(lo), String(hi)), 'error');
                        return;
                    }
                    vals.push(String(n));
                }
                var offs = vals.join(',');
                if (offs === '0,0,0') { act(save, 'cpu.voltage', { op: 'off' }, { done: refresh }); return; }
                if (word.value !== WORD) { toast(cockpit.format(_('Type $0 to confirm.'), WORD), 'error'); return; }
                act(save, 'cpu.voltage', { op: 'set', offsets: offs, confirm: WORD }, { done: refresh });
            }, 'danger');
            if (!fwOk || inputs.length !== 3) save.disabled = true;
            var off = button(_('Back to the stock voltages'), function () { act(off, 'cpu.voltage', { op: 'off' }, { done: refresh }); });
            var stress = v.stress || {};
            var sb = button(_('Run a 10-minute CPU stress test'), function () {
                act(sb, 'cpu.voltage', { op: 'stress', minutes: 10 }, { done: refresh });
            });
            if (stress.running) sb.disabled = true;
            vb.appendChild(actions([ save, off, sb ]));
            vb.appendChild(note(_('Test a new offset under load during its trial boot. The device may crash if the voltage is too low; the next boot then runs at the stock voltages.')));
            if (stress.running) vb.appendChild(note(_('A stress test is running.'), 'warn'));
            if (stress.last) vb.appendChild(pre(String(stress.last)));
        }
        refresh();
    }

    /* the USB network to a computer (usb.net): NCM, ECM or RNDIS from the next boot, for good or for that boot only */
    function usbNet(box) {
        var b = box.appendChild(el('div', { id: 'mu-usbnet' }));
        var NAMES = { ncm: 'NCM', ecm: 'ECM', rndis: 'RNDIS', 'default': _('NCM (the default)') };
        function paint(o) {
            b.appendChild(row(_('USB network now'), o.now ? NAMES[o.now] || o.now : NAMES['default']));
            b.appendChild(row(_('USB network from the next boot'), (NAMES[o.mode] || o.mode) + (o.once ? ' (' + _('that boot only') + ')' : '')));
            var mode = select('mu-usbnet-mode', [ [ 'default', NAMES['default'] ], [ 'ecm', 'ECM' ], [ 'rndis', 'RNDIS' ], [ 'ncm', 'NCM' ] ], o.mode || 'default');
            var scope = select('mu-usbnet-scope', [ [ 'permanent', _('From the next boot on') ], [ 'once', _('The next boot only, then NCM again') ] ], o.once ? 'once' : 'permanent');
            b.appendChild(field(_('USB network protocol'), mode));
            b.appendChild(field(_('How long'), scope));
            var sv = button(_('Save (used at the next boot)'), function () {
                var req = { mode: mode.value };
                if (mode.value !== 'default') req.scope = scope.value;
                act(sv, 'usb.net', req, { confirm: mode.value === 'rndis' ? _('RNDIS is for older Windows: macOS has no driver for it, and a Mac on USB loses the connection to the device until NCM is back. Save?') : null,
                    ok: _('Saved: the USB connection to the computer drops for a moment at the next boot and comes back with it.'),
                    done: function () { clear(b); load(b, 'usb.net', {}, paint); } });
            });
            b.appendChild(actions([ sv ]));
            b.appendChild(note(_('NCM: Windows 10 2004 and newer, macOS, Linux. ECM: macOS, Linux (Windows has no driver). RNDIS: older Windows (macOS has no driver).')));
        }
        load(b, 'usb.net', {}, paint);
    }

    /* System: the host name, time zone, login password, SSH, the LAN address, USB and adb, how the device boots,
     * Docker, the logs */
    pages.system = function (root) {
        var sc = root.appendChild(card(_('System'), 'mu-sys'));
        var sb = sc.appendChild(el('div'));
        var bc = root.appendChild(card(_('Boot'), 'mu-boot'));
        var bb = bc.appendChild(el('div'));
        var lc = root.appendChild(card(_('LAN, USB and adb'), 'mu-lan'));
        var lb = lc.appendChild(el('div'));
        function refresh() {
            load(sb, 'system.get', {}, function (o) {
                sb.appendChild(row(_('Release'), o.release));
                sb.appendChild(row(_('Kernel'), o.kernel));
                sb.appendChild(row(_('Time on the device'), o.time ? new Date(o.time * 1000).toLocaleString() : null));
                var hn = input('mu-sys-host', { value: o.hostname || '', maxlength: '63' });
                var hb = button(_('Save'), function () {
                    if (!/^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(hn.value)) { toast(_('A host name holds letters, digits and hyphens (not first or last).'), 'error'); return; }
                    act(hb, 'system.hostname', { name: hn.value }, { done: refresh });
                });
                sb.appendChild(field(_('Host name'), hn));
                sb.appendChild(actions([ hb ]));
                var tz = input('mu-sys-tz', { value: o.timezone || '', maxlength: '64', placeholder: 'Europe/Istanbul' });
                var tb = button(_('Save'), function () { act(tb, 'system.timezone', { zone: tz.value.trim() }, { done: refresh }); });
                sb.appendChild(field(_('Time zone (Area/City, or UTC)'), tz));
                sb.appendChild(actions([ tb ]));
                var p1 = input('mu-sys-pw1', { type: 'password', autocomplete: 'new-password', maxlength: '128' });
                var p2 = input('mu-sys-pw2', { type: 'password', autocomplete: 'new-password', maxlength: '128' });
                var pb = button(_('Change the password'), function () {
                    if (!p1.value) { toast(_('Enter the new password.'), 'error'); return; }
                    if (p1.value !== p2.value) { toast(_('The two passwords differ.'), 'error'); return; }
                    act(pb, 'system.password', { password: p1.value }, { confirm: cockpit.format(_('Change the password of $0? It is the one for SSH, the console and this panel.'), o.user || 'ubuntu'),
                        done: function () { p1.value = ''; p2.value = ''; } });
                }, 'primary');
                sb.appendChild(field(cockpit.format(_('New password of $0'), o.user || 'ubuntu'), p1));
                sb.appendChild(field(_('The new password again'), p2));
                sb.appendChild(actions([ pb ]));
                sb.appendChild(row(_('SSH'), on(o.ssh)));
                var ss = button(o.ssh ? _('Turn SSH off') : _('Turn SSH on'), function () {
                    act(ss, 'system.ssh', { state: o.ssh ? 'off' : 'on' }, { done: refresh,
                        confirm: o.ssh ? _('Turn SSH off? You can still log in here and on the USB serial console.') : null });
                });
                sb.appendChild(actions([ ss ]));
                boot(o);
            });
            load(lb, 'lan.get', {}, lan);
        }
        function boot(o) {
            clear(bb);
            var b = o.boot || {};
            bb.appendChild(row(_('Starts by default'), b.default === 'linux' ? 'Linux' : 'Android'));
            bb.appendChild(row(_('Linux system'), b.os));
            bb.appendChild(row(_('Kernel in use'), o.kernel_choice));
            bb.appendChild(row(_('Boot locked to Linux'), yes(b.locked)));
            var bar = bb.appendChild(actions([]));
            var an = button(_('Start Android next time'), function () {
                act(an, 'system.boot', { target: 'android' }, { done: refresh,
                    confirm: _('Start Android at the next boot? Linux comes back with mu300-linux from Android (or the installer).') });
            });
            var li = button(_('Start Linux by default'), function () { act(li, 'system.boot', { target: 'linux' }, { done: refresh }); });
            var lk = button(b.locked ? _('Unlock the boot') : _('Lock the boot to Linux'), function () {
                act(lk, 'system.boot', { target: b.locked ? 'unlock' : 'lock' }, { done: refresh });
            });
            [ an, li, lk ].forEach(function (x) { bar.appendChild(x); });
            var sys = Array.isArray(b.systems) ? b.systems : [];
            if (sys.length > 1) {
                var os = select('mu-boot-os', sys.map(function (s) { return [ s, s ]; }), b.os);
                var ob = button(_('Start this system next'), function () {
                    act(ob, 'system.bootos', { os: os.value }, { done: refresh, confirm: _('Start the chosen Linux system at the next boot?') });
                });
                bb.appendChild(field(_('Linux system at the next boot'), os));
                bb.appendChild(actions([ ob ]));
            }
            var rb = button(_('Restart the device'), function () {
                act(rb, 'system.reboot', {}, { confirm: _('Restart the device? Every connection is interrupted, this page too, for a minute or two.'),
                    ok: _('The device is restarting.') });
            }, 'danger');
            bb.appendChild(actions([ rb ]));
        }
        function lan(o) {
            lb.appendChild(row(_('LAN address'), o.ip));
            var ip = input('mu-lan-ip', { value: o.ip || '', maxlength: '15' });
            var ib = button(_('Save'), function () {
                act(ib, 'lan.set', { ip: ip.value.trim() }, { confirm: cockpit.format(_('Change the LAN address to $0? This page is then at https://$0:9090, and computers on USB and the hotspot get new addresses.'), ip.value.trim()) });
            });
            lb.appendChild(field(cockpit.format(_('LAN address (x.x.x.1 of a private network; the default is $0)'), show(o.default_ip)), ip));
            lb.appendChild(actions([ ib ]));
            if (o.usb_role_switch) {
                lb.appendChild(row(_('USB-C role'), o.usb_role));
                var h = button(_('Host (flash drives, keyboards)'), function () {
                    act(h, 'usb.role', { role: 'host' }, { confirm: _('Make the USB-C port a host? The USB network to a computer is gone until it is back to device.') });
                });
                var d = button(_('Device (network to a computer)'), function () { act(d, 'usb.role', { role: 'device' }); });
                lb.appendChild(actions([ h, d ]));
            }
            if (has('usb.net') && S.features.usb_net) usbNet(lb);
            var a = o.adb || {};
            lb.appendChild(row(_('adb'), a.state === 'off' ? _('Off') : a.auth === 'keys' ? cockpit.format(_('On, only for authorised computers ($0 keys)'), show(a.keys)) : _('On, for every computer')));
            if (a.auth !== 'keys') lb.appendChild(note(_('With adb open, anyone who plugs a cable into the device gets a root shell without a password.'), 'warn'));
            var key = el('textarea', { id: 'mu-adb-key', rows: '2', spellcheck: 'false', placeholder: 'QAAAA... user@host' });
            lb.appendChild(field(_('Your computer\'s adb key (~/.android/adbkey.pub), to allow keys only'), key));
            var kb = button(_('Allow authorised computers only'), function () {
                act(kb, 'adb.auth', { mode: 'keys', key: key.value.trim() }, { confirm: _('Let only the computers with an authorised key use adb? The key above goes in first.') });
            });
            var ob = button(_('Allow every computer'), function () {
                act(ob, 'adb.auth', { mode: 'open' }, { confirm: _('Let every computer use adb, without a key?') });
            });
            lb.appendChild(actions([ kb, ob ]));
        }
        refresh();
        if (has('docker.status')) {
            var dc = root.appendChild(card(_('Docker'), 'mu-docker'));
            var db = dc.appendChild(el('div'));
            load(db, 'docker.status', {}, function (o) {
                if (!o.installed) { dc.parentNode.removeChild(dc); return; }
                db.appendChild(row(_('Service'), o.active));
                var list = o.containers || [];
                if (!list.length) db.appendChild(note(_('No containers.')));
                else db.appendChild(table([ _('Name'), _('Image'), _('State'), _('Status') ], list.map(function (c) { return [ c.name, c.image, c.state, c.status ]; })));
            });
        }
        var gc = root.appendChild(card(_('Logs'), 'mu-logs'));
        var out = pre('');
        var gb = button(_('Show the logs'), function () {
            busy(gb, true);
            api('logs', { lines: 300 }).then(function (r) { busy(gb, false); out.textContent = r.text || ''; out.scrollTop = out.scrollHeight; },
                function (ex) { busy(gb, false); out.textContent = errText(ex); });
        });
        gc.appendChild(note(_('The release, the kernel\'s messages and the system log of this boot (Cockpit\'s Logs page has all of them).')));
        gc.appendChild(actions([ gb ]));
        gc.appendChild(out);
    };

    /* Updates: what is installed, a check and an update, the kernel, where the release files come from, the extras,
     * the packages put back after an update */
    pages.updates = function (root) {
        var uc = root.appendChild(card(_('Updates'), 'mu-updates'));
        var ub = uc.appendChild(el('div'));
        var kc = root.appendChild(card(_('Kernel'), 'mu-kernel'));
        var kb = kc.appendChild(el('div'));
        var mc = root.appendChild(card(_('Mirror (release files)'), 'mu-mirror'));
        var mb = mc.appendChild(el('div'));
        var ec = root.appendChild(card(_('Extras'), 'mu-extras'));
        var eb = ec.appendChild(el('div'));
        function refresh() {
            var st = has('update.status') ? load(ub, 'update.status', {}, function (o) {
                ub.appendChild(pre(o.version || ''));
                mirror(o.source || '');
            }) : Promise.resolve();
            st.then(function () {
                var cb = button(_('Check for updates'), function () { act(cb, 'update.check', {}); });
                var ab = button(_('Install the update'), function () {
                    act(ab, 'update.apply', {}, { confirm: _('Install the newest release now? The systems are replaced (your settings are kept) and the new one runs after the next reboot. Keep the device powered until it is done.'),
                        done: function (j) { if (j && j.rc === 0) toast(_('The update is installed: restart the device to run it.'), 'ok'); } });
                }, 'danger');
                ub.appendChild(actions([ cb, ab ]));
            });
            load(kb, 'system.get', {}, function (o) {
                kb.appendChild(row(_('Kernel running'), o.kernel));
                kb.appendChild(row(_('Kernel chosen'), o.kernel_choice));
                kb.appendChild(note(_('5.4 is the vendor kernel (Android\'s drivers); 6.18 is the mainline long-term kernel; 7.2 the stable mainline one; 7.3 the newest mainline one. A change downloads the kernel and takes effect at the next reboot.')));
                var k = select('mu-kernel', [ [ '7.3', '7.3' ], [ '7.2', '7.2' ], [ '6.18', '6.18 LTS' ], [ '5.4', '5.4 (vendor)' ] ], o.kernel_choice || '7.2');
                var kbtn = button(_('Change the kernel'), function () {
                    act(kbtn, 'kernel.set', { version: k.value }, { confirm: cockpit.format(_('Install kernel $0? It runs after the next reboot.'), k.value), done: refresh });
                });
                kb.appendChild(field(_('Kernel'), k));
                kb.appendChild(actions([ kbtn ]));
                extras(o.extras || []);
            });
            if (has('packages.get')) packages();
        }
        function mirror(src) {
            clear(mb);
            mb.appendChild(note(_('Where the release files are downloaded from: GitHub, or a mirror of them (a server with the same files, e.g. on your network). A mirror that cannot be reached is said once, and the rest comes from GitHub.')));
            mb.appendChild(pre(src));
            var u = input('mu-mirror-url', { type: 'url', maxlength: '512', placeholder: 'https://mirror.example.com/mu300' });
            var sb = button(_('Use this mirror'), function () {
                if (!/^https?:\/\/[A-Za-z0-9.-]{1,253}(:\d{1,5})?(\/[!-~]*)?$/.test(u.value)) { toast(_('An http:// or https:// address, please.'), 'error'); return; }
                act(sb, 'update.source', { url: u.value }, { done: refresh });
            }, 'primary');
            var gb = button(_('Back to GitHub'), function () { act(gb, 'update.source', { url: 'off' }, { done: refresh }); });
            mb.appendChild(field(_('Mirror address'), u));
            mb.appendChild(actions([ sb, gb ]));
        }
        function extras(list) {
            clear(eb);
            var NAMES = { vpn: _('VPN module'), lang: _('Languages of the OpenWrt panel'), panel: _('This web panel (Cockpit)'),
                'vpn-xray': _('VPN engine Xray'), 'vpn-singbox': _('VPN engine sing-box'), 'vpn-mihomo': _('VPN engine mihomo'), 'vpn-dae': _('VPN engine dae') };
            eb.appendChild(table([ _('Extra'), _('Installed'), _('Release'), '' ], list.filter(function (x) {
                return /^[a-z-]{1,20}$/.test(String(x.name || ''));
            }).map(function (x) {
                var b;
                if (x.installed) {
                    b = button(_('Remove'), function () {
                        act(b, 'extra.remove', { name: x.name }, { confirm: x.name === 'panel'
                            ? _('Remove the web panel? This page goes with it.') : cockpit.format(_('Remove $0?'), NAMES[x.name] || x.name), done: refresh });
                    }, 'danger');
                } else b = button(_('Install'), function () { act(b, 'extra.install', { name: x.name }, { done: refresh }); });
                return [ NAMES[x.name] || x.name, yes(x.installed), x.release, b ];
            })));
        }
        function packages() {
            var pc = root.appendChild(card(_('Your packages after an update'), 'mu-packages'));
            var pb = pc.appendChild(el('div'));
            load(pb, 'packages.get', {}, function (o) {
                pb.appendChild(note(_('The packages you installed are installed again after an update replaced the system, at its first boot (it needs the internet).')));
                pb.appendChild(pre(o.text || ''));
                var bar = pb.appendChild(actions([]));
                [ [ 'retry', _('Try again now') ], [ 'skip', _('Do not install them again') ], [ 'dismiss', _('Drop the note at login') ] ].forEach(function (a) {
                    var b = button(a[1], function () { act(b, 'packages.act', { op: a[0] }, { done: refresh }); });
                    bar.appendChild(b);
                });
            });
        }
        refresh();
    };

    /* Backup: the settings as one file (mu300-backup: Wi-Fi, mobile data, the SIM, VPN, the LAN, the system, keys),
     * and back */
    pages.backup = function (root) {
        var ec = root.appendChild(card(_('Save the settings'), 'mu-backup-export'));
        ec.appendChild(note(_('One file with the device\'s settings: Wi-Fi, mobile data, the SIM, the VPN and its profiles, the LAN, the system, SSH keys. It holds passwords and VPN keys: give it a passphrase to seal it, and keep it safe.')));
        var pass = input('mu-bk-pass', { type: 'password', autocomplete: 'new-password', maxlength: '256' });
        var wp = check('mu-bk-withpw', _('Also the login password (its hash)'), false);
        ec.appendChild(field(_('Passphrase (8 characters or more; empty: not sealed)'), pass));
        ec.appendChild(wp.node);
        var eb = button(_('Download the backup'), function () {
            if (pass.value && pass.value.length < 8) { toast(_('The passphrase must be 8 characters or more.'), 'error'); return; }
            var req = {};
            if (pass.value) req.passphrase = pass.value;
            if (wp.box.checked) req.with_password = true;
            busy(eb, true);
            api('backup.export', req).then(function (o) {
                busy(eb, false);
                var blob = new Blob([ String(o.file || '') ], { type: o.encrypted ? 'application/octet-stream' : 'application/json' });
                var a = el('a', { href: URL.createObjectURL(blob), download: String(o.name || 'mu300-backup.json') });
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                toast(o.encrypted ? _('Saved, sealed with the passphrase.') : _('Saved (not sealed).'), 'ok');
            }, function (ex) { busy(eb, false); toast(errText(ex), 'error'); });
        }, 'primary');
        ec.appendChild(actions([ eb ]));

        var ic = root.appendChild(card(_('Restore the settings'), 'mu-backup-import'));
        var file = el('input', { type: 'file', id: 'mu-bk-file', accept: '.json,.enc,application/json' });
        var ipass = input('mu-bk-ipass', { type: 'password', autocomplete: 'off', maxlength: '256' });
        var force = check('mu-bk-force', _('A backup of another model too'), false);
        var out = pre('');
        ic.appendChild(field(_('Backup file'), file));
        ic.appendChild(field(_('Passphrase (when it is sealed)'), ipass));
        ic.appendChild(force.node);
        function request(dry) {
            return new Promise(function (resolve, reject) {
                var f = file.files && file.files[0];
                if (!f) { reject({ code: 'missing', detail: _('Backup file') }); return; }
                if (f.size > 4194304) { reject({ code: 'bad-value', detail: _('Backup file') }); return; }
                var r = new FileReader();
                r.onload = function () {
                    var req = { file: String(r.result || '') };
                    if (ipass.value) req.passphrase = ipass.value;
                    if (force.box.checked) req.force = true;
                    if (dry) req.dry_run = true;
                    resolve(req);
                };
                r.onerror = function () { reject({ code: 'failed', detail: _('The file could not be read.') }); };
                r.readAsText(f);
            });
        }
        var cb = button(_('Check the file'), function () {
            busy(cb, true);
            request(true).then(function (req) { return api('backup.import', req); }).then(function (o) {
                busy(cb, false);
                out.textContent = (o.valid ? _('The file can be restored. What would change:') : _('The file cannot be restored:')) + '\n\n' + (o.text || '');
                rb.disabled = !o.valid;
            }, function (ex) { busy(cb, false); out.textContent = errText(ex); });
        });
        var rb = button(_('Restore'), function () {
            if (!confirmed(_('Restore these settings? They replace the device\'s, and the services restart with them (Wi-Fi and mobile data drop for a moment).'))) return;
            busy(rb, true);
            request(false).then(function (req) { return api('backup.import', req); }).then(function () {
                busy(rb, false);
                job.follow();
            }, function (ex) { busy(rb, false); toast(errText(ex), 'error'); });
        }, 'danger');
        rb.disabled = true;
        file.addEventListener('change', function () { rb.disabled = true; out.textContent = ''; });
        ic.appendChild(actions([ cb, rb ]));
        ic.appendChild(out);
    };

    /* ---- the frame ---------------------------------------------------------------------------------------- */
    /* every tab, and the method it needs (an older mu300-api without it: no tab) */
    var TABS = [ [ 'overview', _('Overview'), 'overview' ], [ 'mobile', _('Mobile network'), 'data.get' ], [ 'sms', _('SMS'), 'sms.list' ],
        [ 'at', _('AT terminal'), 'at.send' ], [ 'usage', _('Data usage'), 'traffic.get' ], [ 'wifi', _('Wi-Fi'), 'wifi.get' ],
        [ 'vpn', _('VPN'), 'vpn.status' ], [ 'power', _('Power and CPU'), 'cpu.get' ], [ 'system', _('System'), 'system.get' ],
        [ 'updates', _('Updates'), 'extra.list' ], [ 'backup', _('Backup'), 'backup.export' ] ];
    function tabOk(t) { return !S.hello || has(t[2]); }
    function showPage(name) {
        var t = TABS.filter(function (x) { return x[0] === name; })[0];
        if (!t || !tabOk(t)) name = 'overview';
        stopTimers();
        current = name;
        Array.prototype.forEach.call(document.querySelectorAll('.mu-tab'), function (b) {
            b.classList.toggle('mu-active', b.getAttribute('data-tab') === name);
        });
        var main = clear(document.getElementById('mu-main'));
        if (S.admin === false) {
            main.appendChild(note(_('Administrative access is off: the MU300 page needs it for nearly everything (the modem, the settings). Turn it on with the "Limited access" button at the top of Cockpit; your password is asked once.'), 'danger-box'));
            if (name === 'overview') rateCard(main.appendChild(el('div', { 'class': 'mu-grid' })));
            return;
        }
        if (S.error) main.appendChild(note(S.error, 'error'));
        pages[name](main);
        if (cockpit.location.path[0] !== name) cockpit.location.go([ name ]);
    }
    function paintTabs() {
        var nav = clear(document.getElementById('mu-tabs'));
        TABS.forEach(function (t) {
            if (!tabOk(t)) return;
            nav.appendChild(el('button', { 'class': 'mu-tab', type: 'button', 'data-tab': t[0], text: t[1],
                onclick: function () { showPage(t[0]); } }));
        });
    }
    /* hello: the methods this device's mu300-api has, its features, a job that runs */
    function hello() {
        return api('hello').then(function (o) {
            S.hello = o;
            S.error = null;
            S.methods = {};
            (o.methods || []).forEach(function (m) { S.methods[m] = true; });
            S.features = o.features || {};
            if (o.job && o.job.state && o.job.state !== 'none') job.follow();
        }, function (ex) {
            S.hello = null;
            if (ex && ex.code === 'not-root') S.admin = false;
            else S.error = errText(ex);
        });
    }
    function start() {
        var app = document.getElementById('app');
        app.appendChild(el('h1', { text: 'MU300' }));
        app.appendChild(el('nav', { 'class': 'mu-tabs', id: 'mu-tabs' }));
        app.appendChild(el('div', { id: 'mu-toast' }));
        app.appendChild(el('div', { id: 'mu-job' }));
        app.appendChild(el('main', { id: 'mu-main' }));
        cockpit.translate();
        /* administrative access (Cockpit's "Limited access" switch): without it the page says so; with it again, the
         * page loads anew */
        var perm = typeof cockpit.permission === 'function' ? cockpit.permission({ admin: true }) : null;
        function begin() {
            if (perm && perm.allowed === false) S.admin = false;
            else S.admin = null;
            return (S.admin === false ? Promise.resolve() : hello()).then(function () {
                paintTabs();
                showPage(cockpit.location.path[0] || current || 'overview');
            });
        }
        if (perm && perm.addEventListener) perm.addEventListener('changed', function () { begin(); });
        begin();
        cockpit.addEventListener('locationchanged', function () {
            if (cockpit.location.path[0] && cockpit.location.path[0] !== current) showPage(cockpit.location.path[0]);
        });
    }
    /* for the tests: the helpers, without a page */
    window.MU300 = { netDev: netDev, fmtBytes: fmtBytes, fmtRate: fmtRate, errText: errText, mask: mask, pages: pages,
        show: showPage, api: api, state: S };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
}());
