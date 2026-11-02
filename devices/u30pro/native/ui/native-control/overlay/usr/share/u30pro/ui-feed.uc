/* Feeder of the on-device screen UI (u30pro-panel): collect(req, bus) returns the keys and values the screen shows (feed_set() of
 * tools/ui/data.h); the service /usr/sbin/u30pro-ui-feed hands them over (ubus call u30pro.panel feed).  req: screen_on, page (from
 * u30pro.panel status), sms_refresh, sms_ack (requests of the screen), sms_new (a new-message notification of the AT broker).
 * bus is the service's own ubus connection (null: none): a connection opened here on every run would stay open.
 * Heavier probes are cached in /run/u30pro-ui so that one run costs only a few file reads.
 * Nothing here reads subscriber identifiers; SMS bodies are only shown on the device's own screen. */
import { readfile, writefile, popen, stat, unlink } from 'fs';
import * as m from '/usr/share/u30pro/modem.uc';
import * as sms from '/usr/share/u30pro/sms.uc';
import * as wl from '/usr/share/u30pro/wireless.uc';

const RUN = '/run/u30pro-ui';
let now, out;

function clean(v) {
	let s = '' + (v ?? '');
	for (let ch in [chr(9), chr(10), chr(13)]) s = join(' ', split(s, ch));
	return s;
}
function put(k, v) { out[k] = clean(v); }
/* list entries: fields are cleaned one by one and joined with a TAB */
function putf(k, fields) { out[k] = join(chr(9), map(fields, (f) => clean(f))); }
function slurp(path) { return trim(readfile(path) || ''); }
function json_file(path) {
	try { return json(readfile(path) || 'null'); } catch (e) { return null; }
}
function run(cmd) {
	let p = popen(cmd, 'r');
	if (!p) return '';
	let s = p.read('all') || '';
	p.close();
	return s;
}
/* result of fn() cached for maxage seconds in RUN/<name>.json */
function cached(name, maxage, fn) {
	let path = RUN + '/' + name + '.json', st = stat(path);
	if (st && now - st.mtime < maxage) {
		let v = json_file(path);
		if (v != null) return v;
	}
	let v = fn();
	writefile(path, sprintf('%J', v));
	return v;
}

function collect(req, bus) {
	now = time();
	out = {};
	let screen_on = req.screen_on !== false;

	/* ---- cellular (u30pro-celld, ubus object u30pro.cell) */
	let cell = bus?.call('u30pro.cell', 'status', {}) ?? {};
	put('cell_state', cell.state ?? 'stopped');
	put('cell_connected', cell.connected === true ? 1 : 0);
	put('op', cell.operator ?? '');
	put('ip', cell.address ?? '');
	put('iface', cell.device ?? 'sipa_eth0');
	put('csq', cell.signal_csq ?? 0);

	/* ---- radio details, every 10 s (120 s while the screen is off).  A query that fails or comes back without a measurement (the modem is busy or
	 * restarting) keeps the last complete answer for up to 90 s instead of dropping the screen to "no signal"; the retry waits the same 10 s. */
	function complete(s) { return s && s.ok && s.cesq && s.cesq.rsrp_dbm != null; }
	let sig = null;
	if (cell.state && cell.state != 'stopped') {
		let path = RUN + '/serving.json', st = stat(path), old = st ? json_file(path) : null, maxage = screen_on ? 10 : 120;
		let retry = stat(RUN + '/serving.retry');
		if (st && now - st.mtime < maxage) sig = old;
		else if (retry && now - retry.mtime < maxage) sig = complete(old) ? old : null;
		else {
			let r = m.serving();
			if (complete(r)) { sig = r; writefile(path, sprintf('%J', r)); }
			else if (complete(old) && st && now - st.mtime < 90) { sig = old; writefile(RUN + '/serving.retry', '' + now); }
			else writefile(path, sprintf('%J', r && r.ok ? r : { ok: false }));
			if (sig == null && r && r.ok) sig = r;
		}
	}
	let have = complete(sig);
	put('have_sig', have ? 1 : 0);
	put('nr', have && sig.nr_phy ? 1 : 0);
	put('nr_idle', have && !sig.nr_phy && sig.nr_last ? 1 : 0);
	if (have) {
		/* 5G: registered on NR (SA), an EPS access technology with NR (10..13), or LTE with EN-DC; when CEREG reports no technology at all
		 * (its unsolicited mode was reset by a modem restart), the last reported one is kept rather than guessed */
		let act = sig.eps ? sig.eps.act : null;
		let nr_sa = sig.nr_reg != null && (sig.nr_reg.stat == 1 || sig.nr_reg.stat == 5);
		let rat = (nr_sa || sig.endc === true || act == 13 || act == 10 || act == 11 || act == 12) ? '5G' : (act == 7 ? '4G' : ((act != null && act >= 2 && act <= 6) ? '3G' : ''));
		if (rat == '') rat = slurp(RUN + '/rat.txt');
		else writefile(RUN + '/rat.txt', rat);
		put('rat', rat);
		put('endc', sig.endc === true ? 1 : 0);
		put('rsrp', sig.cesq.rsrp_dbm);
		put('rsrq', sig.cesq.rsrq_db ?? 0);
		put('band', sig.phy ? (sig.phy.band ?? 0) : 0);
		put('pci', sig.phy ? (sig.phy.pci ?? 0) : 0);
		put('earfcn', sig.phy ? (sig.phy.dl_earfcn ?? 0) : 0);
		put('bw_idx', sig.phy ? (sig.phy.dl_bw_idx ?? -1) : -1);
		/* the NR leg of EN-DC: its cell from SPPHYCHINFO, RSRP and SINR from the SS fields of CESQ.  It is only there while the NR leg is
		 * added, which the network does when data flows; an idle link shows the LTE anchor alone. */
		if (sig.nr_phy) {
			put('nr_band', sig.nr_phy.band ?? 0);
			put('nr_pci', sig.nr_phy.pci ?? 0);
			put('nr_arfcn', sig.nr_phy.dl_arfcn ?? 0);
			put('nr_bw', sig.nr_phy.dl_bw_mhz ?? 0);
			if (sig.cesq.ss_rsrp_dbm != null) put('nr_rsrp', sig.cesq.ss_rsrp_dbm);
			if (sig.cesq.ss_sinr_db != null) put('nr_sinr', sig.cesq.ss_sinr_db);
		} else if (sig.nr_last) {
			/* idle: the NR cell of the connection as last seen (modem.uc serving()) */
			let l = sig.nr_last;
			put('nr_band', l.phy.band ?? 0);
			put('nr_pci', l.phy.pci ?? 0);
			put('nr_arfcn', l.phy.dl_arfcn ?? 0);
			put('nr_bw', l.phy.dl_bw_mhz ?? 0);
			if (l.rsrp != null) put('nr_rsrp', l.rsrp);
			if (l.sinr != null) put('nr_sinr', l.sinr);
		}
		/* SINR of the LTE serving cell: AT+SPENGMD=0,0,6, its third field in 0.01 dB (UL MCS, DL MCS, SINR, ... as the vendor's dialer reads it).
		 * An engineering query, so it is only asked while the screen shows the signal page, at most every 10 s. */
		if (screen_on && req.page == 1) {
			let e = cached('sinr', 10, () => {
				let r = m.engineering('lte_main'), t = r.ok && length(r.tokens) ? r.tokens[0] : null, v = t && length(t) > 2 ? int(t[2]) : null;
				return { db: v != null && v == v && v > -3000 && v < 5000 ? v / 100.0 : null };
			});
			if (e.db != null) put('sinr', e.db);
		} else {
			/* elsewhere the last reading (up to 2 min old) is passed on without asking, so the page opens with a value */
			let st = stat(RUN + '/sinr.json'), e = st && now - st.mtime < 120 ? json_file(RUN + '/sinr.json') : null;
			if (e && e.db != null) put('sinr', e.db);
		}
	}

	/* ---- Wi-Fi hotspot and relay: /etc/config/wireless and the state netifd, hostapd and iwinfo report over ubus */
	let wifi = cached('wifi', screen_on ? 5 : 30, () => {
		let h = wl.hotspot(true);
		return { active: h.active, ssid: h.ssid, psk: h.key, channel: h.channel, band: h.band, clients: h.clients };
	});
	let relay = cached('relay', screen_on ? 5 : 30, () => {
		/* enabled and cfg: the saved switch and network (the screen's switch needs a network set in the web interface first) */
		let r = wl.relay();
		return { state: r.state, ssid: r.ssid, enabled: r.enabled ? 1 : 0, cfg: r.cfg };
	});
	put('relay_state', relay.state);
	put('relay_ssid', relay.ssid);
	put('relay_on', relay.enabled ? 1 : 0);
	put('relay_cfg', relay.cfg ?? '');
	put('wifi_active', wifi.active ? 1 : 0);
	put('wifi_enabled', wifi.active ? 1 : 0);
	put('ssid', wifi.ssid);
	put('psk', wifi.psk);
	put('wifi_band', wifi.band);
	put('channel', wifi.channel);
	put('nclients', length(wifi.clients));
	for (let i = 0; i < length(wifi.clients) && i < 6; i++)
		putf('cl.' + i, [wifi.clients[i].name, wifi.clients[i].ip, wifi.clients[i].rssi]);
	let usb = slurp('/run/u30pro-usb-interface');
	put('admin_wifi', trim(run('/usr/sbin/u30pro-webwifi status')) == 'allowed' ? 1 : 0);
	put('usb_up', (usb != '' && slurp('/sys/class/net/' + usb + '/carrier') == '1') ? 1 : 0);

	/* ---- SMS: refreshed on request, on a new-message notification, and once at start */
	let seen_path = RUN + '/sms.seen';
	function sms_keys(list) {
		let r = [];
		for (let x in list) push(r, (x.address ?? '') + '|' + (x.ts ?? ''));
		return r;
	}
	let want = false, st_cache = json_file(RUN + '/sms.json');
	/* +CMTI / +CMT / +CDSI notifications arrive as events of the AT broker (u30pro.at.urc), passed in as req.sms_new */
	if (req.sms_refresh || req.sms_new) want = true;
	if (!st_cache) want = true;
	else if (st_cache.failed && now - st_cache.time > 45) want = true;
	if (want && cell.state && cell.state != 'stopped') {
		let r = sms.list(null);
		if (r.ok) {
			let rec = [];
			for (let x in r.messages) if (x.type == 'deliver') push(rec, { address: x.address, ts: x.ts, text: x.text, stat: x.stat });
			let first = !stat(seen_path), seen = {};
			for (let l in split(readfile(seen_path) || '', '\n')) if (l != '') seen[l] = 1;
			for (let x in rec) {
				let key = x.address + '|' + x.ts;
				x.unread = (first ? x.stat == 0 : !seen[key]) ? 1 : 0;
			}
			if (first) writefile(seen_path, join('\n', map(rec, (x) => x.unread ? '' : x.address + '|' + x.ts)) + '\n');
			writefile(RUN + '/sms.json', sprintf('%J', { time: now, list: rec }));
		} else {
			/* modem not ready or busy: keep what we had and try again in 45 s */
			writefile(RUN + '/sms.json', sprintf('%J', { time: now, failed: true, list: (st_cache && st_cache.list) ? st_cache.list : [] }));
		}
	}
	if (req.sms_ack) {
		let c = json_file(RUN + '/sms.json');
		if (c && c.list) {
			let add = '';
			for (let x in c.list) { add += x.address + '|' + x.ts + '\n'; x.unread = 0; }
			writefile(seen_path, (readfile(seen_path) || '') + add);
			writefile(RUN + '/sms.json', sprintf('%J', c));
		}
	}
	let sc = json_file(RUN + '/sms.json');
	let unread = 0;
	if (sc && sc.list) {
		let n = 0;
		for (let x in sc.list) {
			if (x.unread) unread++;
			if (n < 6) { putf('sms.' + n, [x.address, x.ts, x.unread ? 1 : 0, x.text]); n++; }
		}
		put('sms_total', length(sc.list));
	}
	put('sms_unread', unread);

	/* ---- system facts */
	let cmd = slurp('/proc/cmdline'), sl = match(cmd, /slot_suffix=_([ab])/);
	put('slot', sl ? uc(sl[1]) : '');
	let build = json_file('/etc/u30pro/native-build.json');
	put('version', build && build.stage ? build.stage : '');
	/* a charge pause of the battery and temperature protection (u30pro.safety) */
	let safety = bus?.call('u30pro.safety', 'status', {});
	put('chg_paused', safety?.verdict?.charge_paused === true ? 1 : 0);
	return out;
}

export { collect };
