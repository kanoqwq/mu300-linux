/* U30 Pro baseband management library (QModem-style features on the Unisoc modem).
 * All AT traffic goes through the AT broker, ubus object u30pro.at (/usr/sbin/u30pro-atd), which serialises access (no command filter).
 * The AT reference with the response formats is docs/MODEM_AT.md. */
import { popen, readfile, writefile, mkdir, stat, unlink } from 'fs';
import { connect } from 'ubus';

export const RUN = '/run/u30pro-modem';
/* the command-line client (for jobs that run in the background) */
export const ATC = getenv('U30PRO_ATC') || '/usr/sbin/u30pro-atc';

/* ---------------------------------------------------------------- AT access */

function shq(s) { return "'" + replace(s, "'", "'\\''") + "'"; }

/* the offline tests set U30PRO_ATC to a test double with the client's command-line interface (tests/fake_modem/fake-atc.sh) */
function broker_cli(method, msg) {
	let a = method == 'command' ? '-t ' + int(msg.timeout) + ' ' + shq(msg.cmd)
		: method == 'sms' ? shq(sprintf('!sms T%d %d %s', msg.timeout, msg.len, msg.pdu)) : shq('!' + method);
	let p = popen(ATC + ' ' + a + ' 2>&1', 'r');
	let out = p ? (p.read('all') || '') : '';
	if (p) p.close();
	let j;
	try { j = json(trim(out)); } catch (e) { j = null; }
	return type(j) == 'object' ? j : { ok: false, status: 'no-broker', error: trim(out) || 'no reply from ' + ATC, lines: [] };
}

/* a call to the broker; the connection waits as long as the command may run, plus the time it may wait for commands before it.
 * A ubus connection stays open until disconnect(), also when nothing refers to it any more (only a gc() run would close it), so
 * every call closes its own: the long-running screen feed calls this every few seconds and would otherwise use up ubusd's file
 * descriptors. */
function broker(method, msg, timeout_ms) {
	if (getenv('U30PRO_ATC'))
		return broker_cli(method, msg ?? {});
	let bus = connect(null, int(((timeout_ms || 5000) + 999) / 1000) + 30);
	if (!bus)
		return { ok: false, status: 'no-broker', error: 'cannot connect to ubus', lines: [] };
	let r = bus.call('u30pro.at', method, msg ?? {}), err = bus.error();
	bus.disconnect();
	if (type(r) != 'object')
		return { ok: false, status: 'no-broker', error: err || 'u30pro-atd is not running', lines: [] };
	return r;
}

function at(cmd, timeout) {
	/* any command (no filter): it starts with AT, is one line and fits the broker's request */
	if (type(cmd) != 'string' || !match(cmd, /^[Aa][Tt]/) || match(cmd, /[\r\n]/) || length(cmd) > 900)
		return { ok: false, status: 'invalid', error: 'invalid AT command', lines: [] };
	let j = broker('command', { cmd: cmd, timeout: int(timeout || 5000) }, timeout);
	if (!j.lines) j.lines = [];
	return j;
}

/* one SMS-SUBMIT PDU through the broker (AT+CMGS prompt handling lives in u30pro-atd) */
function sms_send(len, pdu, timeout) {
	if (type(len) != 'int' || type(pdu) != 'string' || !match(pdu, /^[0-9A-Fa-f]+$/) || length(pdu) < 14 || length(pdu) > 400)
		return { ok: false, status: 'invalid', error: 'invalid PDU', lines: [] };
	let j = broker('sms', { len: len, pdu: pdu, timeout: int(timeout || 60000) }, timeout || 60000);
	if (!j.lines) j.lines = [];
	return j;
}

function broker_status() {
	return broker('status');
}

function urc_log() {
	let j = broker('urc');
	if (!j.urc) j.urc = [];
	return j;
}

/* first line of a response that starts with prefix, without the prefix */
function field(r, prefix) {
	for (let l in r.lines)
		if (index(l, prefix) == 0) return trim(substr(l, length(prefix)));
	return null;
}

/* CSV split that keeps quoted commas and strips the quotes */
function csv(s) {
	let out = [], cur = '', q = false;
	for (let i = 0; i < length(s); i++) {
		let c = substr(s, i, 1);
		if (c == '"') { q = !q; continue; }
		if (c == ',' && !q) { push(out, trim(cur)); cur = ''; continue; }
		cur += c;
	}
	push(out, trim(cur));
	return out;
}

function num(s) { return (s != null && match(s, /^-?[0-9]+$/)) ? int(s) : null; }

/* ------------------------------------------------------------- band / freq maths */

/* LTE: band -> [downlink low MHz*10, NOffs-DL, count] */
const LTE_BANDS = {
	'1': [21100, 0, 600], '2': [19300, 600, 600], '3': [18050, 1200, 750], '4': [21100, 1950, 450],
	'5': [8690, 2400, 250], '7': [26200, 2750, 700], '8': [9250, 3450, 350], '12': [7290, 5010, 170],
	'13': [7460, 5180, 100], '14': [7580, 5280, 100], '17': [7340, 5730, 120], '18': [8600, 5850, 150],
	'19': [8750, 6000, 150], '20': [7910, 6150, 300], '25': [19300, 8040, 650], '26': [8590, 8690, 250],
	'28': [7580, 9210, 450], '29': [7170, 9660, 80], '30': [23500, 9770, 100], '32': [14520, 9920, 400],
	'34': [20100, 36200, 250], '38': [25700, 37750, 500], '39': [18800, 38250, 400], '40': [23000, 38650, 1000],
	'41': [24960, 39650, 2000], '42': [34000, 41590, 2000], '43': [36000, 43590, 2000], '46': [51500, 46790, 3150],
	'48': [35500, 55240, 1400], '66': [21100, 66436, 900], '71': [6170, 68586, 400]
};

function lte_earfcn(n) {
	if (n == null) return null;
	for (let b in LTE_BANDS) {
		let t = LTE_BANDS[b];
		if (n >= t[1] && n < t[1] + t[2])
			return { band: int(b), mhz: (t[0] + (n - t[1])) / 10.0 };
	}
	return null;
}

/* LTE bandwidth index of SPPHYCHINFO -> MHz */
const LTE_BW_MHZ = [ 1.4, 3, 5, 10, 15, 20 ];

const NR_RANGES = [
	[1, 2110, 2170], [3, 1805, 1880], [5, 869, 894], [7, 2620, 2690], [8, 925, 960], [20, 791, 821], [25, 1930, 1995],
	[28, 758, 803], [38, 2570, 2620], [40, 2300, 2400], [41, 2496, 2690], [66, 2110, 2200], [71, 617, 652],
	[77, 3300, 4200], [78, 3300, 3800], [79, 4400, 5000]
];

function nr_arfcn(n) {
	if (n == null) return null;
	let f;
	if (n < 600000) f = n * 0.005;
	else if (n < 2016667) f = 3000 + (n - 600000) * 0.015;
	else f = 24250.08 + (n - 2016667) * 0.060;
	let band = null;
	/* prefer the narrower/commercial bands in the overlaps (n78 before n77, n41 before n7/n38) */
	for (let r in NR_RANGES)
		if (f >= r[1] && f <= r[2] && (band == null || r[0] == 78 || r[0] == 41)) band = r[0];
	return { mhz: f, band: band };
}

export const NR_FDD = [1, 2, 3, 5, 7, 8, 12, 20, 25, 28, 66, 70, 71, 74];
export const NR_TDD = [34, 38, 39, 40, 41, 50, 51, 77, 78, 79];
export const NR_SUPER = [75, 76, 80, 81, 82, 83, 84, 86];
/* the radio bands this hardware advertises (also learned from AT+SPLBAND on first read) */
export const LTE_HW = [1, 3, 5, 8, 34, 39, 40, 41];
export const NR_HW = [1, 3, 5, 8, 28, 41, 78];

function bits_to_list(mask, offset) {
	let out = [];
	for (let i = 0; i < 32; i++)
		if (mask & (1 << i)) push(out, offset + i);
	return out;
}

/* AT+SPLBAND=0 -> { lte: [bands...] }; reply order: <49-64>,<33-48>,<17-32>,<1-16>,<65-80> */
function parse_lte_bands(line) {
	let v = csv(line);
	if (length(v) < 5) return null;
	let m = map(v, (x) => num(x) ?? 0);
	let out = [];
	for (let b in bits_to_list(m[3], 1)) if (b <= 16) push(out, b);
	for (let b in bits_to_list(m[2], 17)) if (b <= 32) push(out, b);
	for (let b in bits_to_list(m[1], 33)) if (b <= 48) push(out, b);
	for (let b in bits_to_list(m[0], 49)) if (b <= 64) push(out, b);
	for (let b in bits_to_list(m[4], 65)) if (b <= 80) push(out, b);
	return sort(out, (a, b) => a - b);
}

function lte_band_command(bands) {
	let m1 = 0, m2 = 0, m3 = 0, m4 = 0, m5 = 0;
	for (let b in bands) {
		if (b >= 1 && b <= 16) m1 |= 1 << (b - 1);
		else if (b <= 32) m2 |= 1 << (b - 17);
		else if (b <= 48) m3 |= 1 << (b - 33);
		else if (b <= 64) m4 |= 1 << (b - 49);
		else if (b <= 80) m5 |= 1 << (b - 65);
	}
	return sprintf('AT+SPLBAND=1,%d,%d,%d,%d,%d', m4, m3, m2, m1, m5);
}

/* AT+SPLBAND=3 -> FDD mask,0,TDD mask (super band mask is appended when the modem reports it) */
function parse_nr_bands(line) {
	let v = map(csv(line), (x) => num(x) ?? 0);
	if (length(v) < 3) return null;
	let out = [];
	for (let i = 0; i < length(NR_FDD); i++) if (v[0] & (1 << i)) push(out, NR_FDD[i]);
	for (let i = 0; i < length(NR_TDD); i++) if (v[2] & (1 << i)) push(out, NR_TDD[i]);
	if (length(v) > 3)
		for (let i = 0; i < length(NR_SUPER); i++) if (v[3] & (1 << i)) push(out, NR_SUPER[i]);
	return sort(out, (a, b) => a - b);
}

function nr_band_command(bands) {
	let f = 0, t = 0, s = 0;
	for (let b in bands) {
		let i;
		if ((i = index(NR_FDD, b)) >= 0) f |= 1 << i;
		else if ((i = index(NR_TDD, b)) >= 0) t |= 1 << i;
		else if ((i = index(NR_SUPER, b)) >= 0) s |= 1 << i;
	}
	return sprintf('AT+SPLBAND=2,%d,0,%d,%d', f, t, s);
}

/* ---------------------------------------------------------------- queries */

const ACT = { '0': 'GSM', '2': 'UTRAN', '3': 'GSM/EGPRS', '4': 'HSDPA', '5': 'HSUPA', '6': 'HSPA', '7': 'LTE',
	'10': 'LTE (5GC)', '11': 'NR (5GC)', '12': 'NG-RAN', '13': 'LTE+NR (EN-DC)' };
const REG = { '0': 'Not registered', '1': 'Registered (home network)', '2': 'Searching', '3': 'Registration denied', '4': 'Unknown', '5': 'Registered (roaming)' };

/* one-shot snapshot of everything the overview page shows */
function serving() {
	let r = { ok: true };
	let cereg = at('AT+CEREG?'), c5g = at('AT+C5GREG?'), cops = at('AT+COPS?'), cesq = at('AT+CESQ'), csq = at('AT+CSQ');
	let phy = at('AT+SPPHYCHINFO?'), endc = at('AT+SPENDC?'), nw = at('AT+SPNWINFO?'), ca = at('AT+SPCAINFO?');
	if (!cereg.ok && cereg.status == 'no-broker')
		return { ok: false, error: cereg.error };

	let e = field(cereg, '+CEREG:');
	if (e) {
		let v = csv(e);
		r.eps = { stat: num(v[1]), stat_text: REG[v[1]] ?? '', tac: v[2], ci: v[3], act: num(v[4]), act_text: ACT[v[4]] ?? '' };
		if (v[2] && match(v[2], /^[0-9A-Fa-f]+$/)) r.eps.tac_dec = hex(v[2]);
		if (v[3] && match(v[3], /^[0-9A-Fa-f]+$/)) {
			let ci = hex(v[3]);
			r.eps.eci = ci; r.eps.enb = ci >> 8; r.eps.cell = ci & 255;
		}
	}
	let g = field(c5g, '+C5GREG:');
	if (g) { let v = csv(g); r.nr_reg = { n: num(v[0]), stat: num(v[1]), stat_text: REG[v[1]] ?? '' }; }
	let o = field(cops, '+COPS:');
	if (o) {
		let v = csv(o);
		r.operator = { mode: num(v[0]), format: num(v[1]), id: v[2], act: num(v[3]) };
	}
	let n = field(nw, '+SPNWINFO:');
	if (n) { let v = csv(n); r.network = { mcc: v[0], mnc: v[1], name: v[2], short_name: v[3] }; }
	let q = field(cesq, '+CESQ:');
	if (q) {
		let v = map(csv(q), (x) => num(x));
		r.cesq = { rxlev: v[0], ber: v[1], rscp: v[2], ecno: v[3], rsrq_idx: v[4], rsrp_idx: v[5] };
		if (v[5] != null && v[5] >= 0 && v[5] <= 97) r.cesq.rsrp_dbm = v[5] - 141;
		if (v[4] != null && v[4] >= 0 && v[4] <= 34) r.cesq.rsrq_db = -20 + v[4] * 0.5;
		/* the NR cell (27.007: ss_rsrq, ss_rsrp, ss_sinr; 255 while there is none, as with EN-DC before the NR leg is added); the lower
		 * bound of each step, as for LTE.  On this modem they match AT+SPENGMD=0,14,1 (-92.08 dBm, -5.81 dB for 65 and 75). */
		if (v[6] != null && v[6] >= 0 && v[6] <= 127) { r.cesq.ss_rsrq_idx = v[6]; r.cesq.ss_rsrq_db = -43.5 + v[6] * 0.5; }
		if (v[7] != null && v[7] >= 0 && v[7] <= 126) { r.cesq.ss_rsrp_idx = v[7]; r.cesq.ss_rsrp_dbm = v[7] - 157; }
		if (v[8] != null && v[8] >= 0 && v[8] <= 127) { r.cesq.ss_sinr_idx = v[8]; r.cesq.ss_sinr_db = -23.5 + v[8] * 0.5; }
	}
	let s = field(csq, '+CSQ:');
	if (s) { let v = csv(s); r.csq = num(v[0]); }
	/* one entry per serving cell, "|" between them: the LTE cell and, with EN-DC once the NR leg is added (only while data flows), the NR cell
	 * (technology 11), e.g. 13,1,1850,19850,3,3,"1",196,3|11,2,628704,0,80,80,"1",307,78.  The NR entry gives its bandwidths in MHz. */
	/* cells: every entry in order (with carrier aggregation the LTE secondary cells follow the primary), bandwidths in MHz */
	let p = field(phy, '+SPPHYCHINFO:');
	r.cells = [];
	for (let e in (p ? split(p, '|') : [])) {
		let v = csv(e), act = num(v[0]);
		if (act == 11 || act == 12) {
			let c = { act: act, dl_arfcn: num(v[2]), ul_arfcn: num(v[3]), dl_bw_mhz: num(v[4]), ul_bw_mhz: num(v[5]), pci: num(v[7]), band: num(v[8]) };
			let f = nr_arfcn(c.dl_arfcn);
			if (f) { c.dl_mhz = f.mhz; if (c.band == null) c.band = f.band; }
			push(r.cells, { rat: 'nr', band: c.band, arfcn: c.dl_arfcn, ul_arfcn: c.ul_arfcn, mhz: c.dl_mhz, bw_mhz: c.dl_bw_mhz, pci: c.pci });
			if (!r.nr_phy) r.nr_phy = c;
		} else {
			let c = { act: act, dl_earfcn: num(v[2]), ul_earfcn: num(v[3]), dl_bw_idx: num(v[4]), ul_bw_idx: num(v[5]), pci: num(v[7]), band: num(v[8]) };
			let f = lte_earfcn(c.dl_earfcn);
			if (f) { c.dl_mhz = f.mhz; if (c.band == null) c.band = f.band; }
			push(r.cells, { rat: 'lte', band: c.band, arfcn: c.dl_earfcn, ul_arfcn: c.ul_earfcn, mhz: c.dl_mhz, bw_mhz: LTE_BW_MHZ[c.dl_bw_idx], pci: c.pci });
			if (!r.phy) r.phy = c;
		}
	}
	let d = field(endc, '+ENDC:');
	if (d) r.endc = (num(trim(d)) == 1);
	let a = field(ca, '+SPCAINFO:');
	if (a) { let v = map(csv(a), (x) => num(x)); r.ca = { raw: a }; }
	/* the NR leg of EN-DC comes and goes with traffic (the network releases it on an idle link, and then the modem reports nothing of
	 * NR).  The last one seen is kept in RUN/nr-last.json and, while the anchor still says EN-DC and no NR leg is up, reported as nr_last
	 * (its cell and measurements, age_s) for up to 30 min, so that the pages show the NR cell of the connection when it is idle too. */
	if (r.nr_phy) {
		mkdir(RUN, 0700);
		writefile(RUN + '/nr-last.json', sprintf('%J', { t: time(), phy: r.nr_phy, rsrp: r.cesq?.ss_rsrp_dbm, rsrq: r.cesq?.ss_rsrq_db, sinr: r.cesq?.ss_sinr_db }));
	} else if (r.endc) {
		let l = null;
		try { l = json(readfile(RUN + '/nr-last.json') || 'null'); } catch (e) {}
		if (type(l) == 'object' && type(l.phy) == 'object' && time() - l.t >= 0 && time() - l.t < 1800)
			r.nr_last = { phy: l.phy, rsrp: l.rsrp, rsrq: l.rsrq, sinr: l.sinr, age_s: time() - l.t };
	}
	return r;
}

/* ---------------------------------------------------------------- QoS ("subscribed"): QCI / 5QI and the aggregate maximum bit rates */
const QCI_TEXT = { '1': 'Conversational voice (GBR)', '2': 'Conversational video (GBR)', '3': 'Real-time gaming (GBR)', '4': 'Non-conversational video (GBR)', '5': 'IMS signalling',
	'6': 'Buffered video/TCP', '7': 'Voice/video/gaming', '8': 'Buffered video/TCP', '9': 'Default bearer (best effort)', '65': 'MC PTT voice (GBR)',
	'66': 'Non-MC PTT voice (GBR)', '69': 'MC signalling', '70': 'MC data', '79': 'V2X', '80': 'Low-latency eMBB', '82': 'Low-latency (GBR)', '83': 'Automated driving (GBR)' };

/* kbit/s as text; 0 = unspecified */
function kbps_text(v) {
	if (v == null) return null;
	if (v == 0) return 'Not limited or unspecified';
	/* the subscribed AMBR of this SIM is 1660 Mbit/s down / 4294 Mbit/s up (QCI 9): whole Mbit/s above 1 Gbit/s, as the operator quotes it */
	if (v >= 1000000) return sprintf('%d Mbit/s', v / 1000);
	if (v >= 1000) return sprintf('%.1f Mbit/s', v / 1000.0);
	return v + ' kbit/s';
}

/* +CGEQOSRDP: <cid>,<QCI>,[<DL_GBR>,<UL_GBR>],[<DL_MBR>,<UL_MBR>][,<DL_AMBR>,<UL_AMBR>]
 * +C5GQOSRDP: <cid>,<5QI>,[<DL_GFBR>,<UL_GFBR>],[<DL_MFBR>,<UL_MFBR>][,<DL_SAMBR>,<UL_SAMBR>[,<window>]] */
function qos_entry(line, nr) {
	let v = map(csv(line), (x) => num(x));
	if (v[0] == null || v[1] == null) return null;
	let e = { cid: v[0], qi: v[1], qi_text: QCI_TEXT[v[1] + ''] ?? '', dl_gbr: v[2], ul_gbr: v[3], dl_mbr: v[4], ul_mbr: v[5],
		dl_ambr: v[6], ul_ambr: v[7] };
	if (nr && v[8] != null) e.window_ms = v[8];
	for (let k in ['dl_gbr', 'ul_gbr', 'dl_mbr', 'ul_mbr', 'dl_ambr', 'ul_ambr'])
		if (e[k] != null) e[k + '_text'] = kbps_text(e[k]);
	return e;
}

function qos() {
	let r = { ok: true, eps: [], nr: [], raw: [] };
	let cids = [1];
	let dc = at('AT+CGDCONT?');
	if (dc.ok) {
		cids = [];
		for (let l in dc.lines) { let mm = match(l, /^\+CGDCONT: *([0-9]+),/); if (mm && length(cids) < 4) push(cids, int(mm[1])); }
		if (!length(cids)) cids = [1];
	}
	let any = false;
	for (let cid in cids) {
		let a = at('AT+CGEQOSRDP=' + cid);
		if (a.ok) for (let l in a.lines) if (index(l, '+CGEQOSRDP:') == 0) {
			push(r.raw, l); let e = qos_entry(trim(substr(l, 11)), false); if (e) { push(r.eps, e); any = true; }
		}
		let b = at('AT+C5GQOSRDP=' + cid);
		if (b.ok) for (let l in b.lines) if (index(l, '+C5GQOSRDP:') == 0) {
			push(r.raw, l); let e = qos_entry(trim(substr(l, 11)), true); if (e) { push(r.nr, e); any = true; }
		}
	}
	if (!any) { r.ok = false; r.error = 'The modem returned no QoS information (data connection not established, or this firmware does not support AT+CGEQOSRDP / AT+C5GQOSRDP)'; }
	return r;
}

/* serving cell + neighbours with real radio measurements (AT+SPQ4GNCELLEX), values are x100 */
function neighbors() {
	let r = at('AT+SPQ4GNCELLEX=4,4');
	let out = { ok: r.ok, lte: [], nr: [], raw: r.lines };
	let l = field(r, '+SPQ4GNCELL:');
	if (l) {
		let v = map(csv(l), (x) => num(x));
		if (length(v) >= 5) {
			out.serving = { earfcn: v[0], pci: v[1], rsrp: v[2] / 100.0, rsrq: v[3] / 100.0 };
			let fq = lte_earfcn(v[0]);
			if (fq) { out.serving.band = fq.band; out.serving.mhz = fq.mhz; }
			let cnt = v[4];
			for (let i = 0; i < cnt && 5 + 4 * i + 3 < length(v); i++) {
				let k = 5 + 4 * i;
				let c = { earfcn: v[k], pci: v[k + 1], rsrp: v[k + 2] / 100.0, rsrq: v[k + 3] / 100.0 };
				let f = lte_earfcn(v[k]);
				if (f) { c.band = f.band; c.mhz = f.mhz; }
				push(out.lte, c);
			}
		}
	}
	let n = at('AT+SPQ5GNCELLEX');
	out.nr_status = n.status;
	let nl = field(n, '+SPQ5GNCELL');
	if (nl) out.nr_raw = nl;
	return out;
}

/* engineering dumps: split on '-' where an empty token marks a minus sign (the firmware prints negatives as "--123") */
function eng_tokens(line) {
	let out = [], parts = split(line, '-'), neg = false;
	for (let p in parts) {
		if (p == '') { neg = true; continue; }
		push(out, neg ? '-' + p : p);
		neg = false;
	}
	return out;
}

function engineering(kind) {
	const cmds = { lte_serving: 'AT+SPENGMD=0,6,0', lte_neighbors: 'AT+SPENGMD=0,6,6', lte_main: 'AT+SPENGMD=0,0,6',
		nr_serving: 'AT+SPENGMD=0,14,1', nr_neighbors: 'AT+SPENGMD=0,14,2' };
	if (!cmds[kind]) return { ok: false, error: 'unknown engineering view' };
	let r = at(cmds[kind]);
	let out = { ok: r.ok, status: r.status, command: cmds[kind], lines: r.lines, tokens: [] };
	if (r.ok && length(r.lines))
		for (let l in r.lines)
			push(out.tokens, eng_tokens(l));
	if (kind == 'lte_serving' && out.tokens[0] && length(out.tokens[0]) > 12) {
		let t = out.tokens[0];
		out.decoded = { band: num(t[0]), earfcn: num(t[1]), pci: num(t[2]), rsrp: num(t[3]) / 100.0, rsrq: num(t[4]) / 100.0 };
	}
	return out;
}

/* the carriers of the connection for the web page, one row each (cells of serving(): LTE primary, LTE secondary cells with carrier
 * aggregation, the NR cell of EN-DC or SA) with what the modem measures per carrier.  The primary cells get the finer engineering values
 * (0.01 dB) when the dump is of the same cell: LTE RSRP/RSRQ from AT+SPENGMD=0,6,0 and its SINR from AT+SPENGMD=0,0,6, NR RSRP/RSRQ from
 * AT+SPENGMD=0,14,1 (band, NR-ARFCN, PCI, RSRP, RSRQ first); otherwise AT+CESQ (LTE fields, the SS fields for NR, 0.5 to 1 dB steps).
 * The NR SINR is the one of CESQ.  Secondary LTE cells have no measurement here. */
function carriers() {
	let s = serving();
	if (!s.ok) return s;
	let cq = s.cesq ?? {}, out = [], lte_first = length(s.cells) && s.cells[0].rat == 'lte';
	let le = s.phy ? engineering('lte_serving') : null, lm = s.phy ? engineering('lte_main') : null, ne = s.nr_phy ? engineering('nr_serving') : null;
	let lte_done = false, nr_done = false;
	for (let c in s.cells) {
		let row = { rat: c.rat, band: c.band, arfcn: c.arfcn, ul_arfcn: c.ul_arfcn, mhz: c.mhz, bw_mhz: c.bw_mhz, pci: c.pci };
		if (c.rat == 'lte' && !lte_done) {
			lte_done = true;
			row.role = 'pcc';
			let d = le?.decoded;
			if (d && d.earfcn == c.arfcn && d.pci == c.pci) { row.rsrp = d.rsrp; row.rsrq = d.rsrq; }
			else { row.rsrp = cq.rsrp_dbm; row.rsrq = cq.rsrq_db; }
			let t = lm?.ok && length(lm.tokens) ? lm.tokens[0] : null, v = t && length(t) > 2 ? num(t[2]) : null;
			if (v != null && v > -3000 && v < 5000) row.sinr = v / 100.0;
		} else if (c.rat == 'lte') {
			row.role = 'scc';
		} else if (!nr_done) {
			nr_done = true;
			row.role = lte_first ? 'endc' : 'pcc';
			let t = ne?.ok && length(ne.tokens) ? ne.tokens[0] : null;
			if (t && length(t) > 4 && num(t[1]) == c.arfcn && num(t[2]) == c.pci && num(t[3]) != null && num(t[4]) != null) {
				row.rsrp = num(t[3]) / 100.0; row.rsrq = num(t[4]) / 100.0;
			} else { row.rsrp = cq.ss_rsrp_dbm; row.rsrq = cq.ss_rsrq_db; }
			row.sinr = cq.ss_sinr_db;
		} else row.role = 'scc';
		push(out, row);
	}
	if (!nr_done && s.nr_last) {
		let p = s.nr_last.phy, f = nr_arfcn(p.dl_arfcn);
		push(out, { rat: 'nr', role: 'endc', idle: true, age_s: s.nr_last.age_s, band: p.band, arfcn: p.dl_arfcn, ul_arfcn: p.ul_arfcn,
			mhz: f ? f.mhz : null, bw_mhz: p.dl_bw_mhz, pci: p.pci, rsrp: s.nr_last.rsrp, rsrq: s.nr_last.rsrq, sinr: s.nr_last.sinr });
	}
	return { ok: true, serving: s, carriers: out };
}

/* ------------------------------------------------------------ band & cell lock */

function bands_get() {
	let l = at('AT+SPLBAND=0'), n = at('AT+SPLBAND=3');
	let out = { ok: l.ok || n.ok, lte_hw: LTE_HW, nr_hw: NR_HW };
	let lv = field(l, '+SPLBAND:');
	if (lv) out.lte = parse_lte_bands(lv);
	let nv = field(n, '+SPLBAND:');
	if (nv) out.nr = parse_nr_bands(nv);
	out.lte_status = l.status; out.nr_status = n.status;
	return out;
}

function cell_lock_get() {
	let out = { ok: true };
	for (let k in [[12, 'lte'], [16, 'nr']]) {
		let r = at('AT+SPFORCEFRQ=' + k[0] + ',3');
		let v = field(r, '+SPFORCEFRQ:');
		let st = { raw: v, status: r.status };
		if (v) {
			let a = csv(v);
			/* "12,3" = no lock; a locked cell lists its frequency and PCI after the first two items */
			if (length(a) >= 4) { st.locked = true; st.earfcn = num(a[2]); st.pci = num(a[3]); }
			else st.locked = false;
		}
		out[k[1]] = st;
	}
	return out;
}

function cell_lock_set(rat, earfcn, pci) {
	if (!(rat == 'lte' || rat == 'nr') || type(earfcn) != 'int' || type(pci) != 'int' || earfcn < 0 || earfcn > 3279165 || pci < 0 || pci > 1007)
		return { ok: false, error: 'invalid cell lock parameters' };
	return at('AT+SPFORCEFRQ=' + (rat == 'lte' ? 12 : 16) + ',6,' + earfcn + ',' + pci, 8000);
}

function cell_lock_clear() {
	let a = at('AT+SPFORCEFRQ=12,4'), b = at('AT+SPFORCEFRQ=16,4');
	return { ok: a.ok && b.ok, lte: a.status, nr: b.status };
}

function bands_reset() {
	/* an all-zero mask is not "no restriction" on every band type: on the real modem it left NR at 0,0,0 while the
	 * factory state is the full hardware list, so restore the explicit hardware masks */
	let a = at(lte_band_command(LTE_HW)), b = at(nr_band_command(NR_HW));
	return { ok: a.ok && b.ok, lte: a.status, nr: b.status };
}

function bands_apply(lte, nr) {
	let out = { ok: true };
	if (lte != null) {
		for (let b in lte) if (type(b) != 'int' || b < 1 || b > 80) return { ok: false, error: 'invalid LTE band' };
		let r = at(length(lte) ? lte_band_command(lte) : 'AT+SPLBAND=1,0,0,0,0,0', 8000);
		out.lte = r.status; out.ok = out.ok && r.ok;
	}
	if (nr != null) {
		for (let b in nr) if (type(b) != 'int' || (index(NR_FDD, b) < 0 && index(NR_TDD, b) < 0 && index(NR_SUPER, b) < 0)) return { ok: false, error: 'invalid NR band' };
		let r = at(length(nr) ? nr_band_command(nr) : 'AT+SPLBAND=2,0,0,0,0', 8000);
		out.nr = r.status; out.ok = out.ok && r.ok;
	}
	return out;
}

/* ---------------------------------------------------------- safety net for locks */

const PENDING = RUN + '/lock-pending.json';

function pending_get() {
	try { return json(readfile(PENDING)); } catch (e) { return null; }
}

/* A lock that is not confirmed within seconds is undone by a detached timer: nobody should be locked out of the network
 * by a wrong band choice. */
function pending_arm(seconds, what) {
	mkdir(RUN, 0700);
	let s = int(seconds);
	if (s < 30 || s > 900) s = 120;
	writefile(PENDING, sprintf('%J', { until: time() + s, seconds: s, what: what }));
	let p = popen("(sleep " + s + "; [ -f " + PENDING + " ] && ucode /usr/share/u30pro/modem-rpc.uc revert </dev/null >/dev/null 2>&1) </dev/null >/dev/null 2>&1 &", 'r');
	if (p) p.close();
	return { until: time() + s, seconds: s };
}

function pending_clear() { unlink(PENDING); }

export { at, qos, sms_send, broker_status, urc_log, csv, lte_earfcn, nr_arfcn, parse_lte_bands, lte_band_command, parse_nr_bands, nr_band_command, serving, carriers, neighbors, eng_tokens, engineering, bands_get, cell_lock_get, cell_lock_set, cell_lock_clear, bands_reset, bands_apply, pending_get, pending_arm, pending_clear };
