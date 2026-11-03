/* Shared battery history: readonly sysfs, one minute records, ten-day retention. */
import { readfile, writefile, open, rename, unlink, mkdir, lsdir } from 'fs';

const ROOT = getenv('UNISOC_BATTERY_ROOT') || '';
const PROFILE = json(readfile(ROOT + '/usr/share/unisoc-battery-history/profile.json') || '{}');
if (!(PROFILE.family in ['e5', 'mu300'])) die('battery-history: invalid family profile\n');
const FAMILY = PROFILE.family, PS = ROOT + '/sys/class/power_supply';
const DIR = ROOT + (FAMILY == 'e5' ? '/etc/e5/battery' : '/etc/mu300/battery');
const FILE = DIR + '/battery.csv', RUN = ROOT + '/run/unisoc-battery-history', PENDING = RUN + '/pending.csv';
let BAT = '';
const KEEP = 10 * 86400, STEP = 60;
const RANGES = [ 'full', 'charge', '24h', '7d' ], FULL_PCT = 95;

/* the records, as parallel arrays (a few MB less than one array per record) */
let T = [], P = [], MA = [], MW = [], S = [];
let acc = null, trimmed = 0;

function text(p) { let s = readfile(p); return s == null ? null : trim(s); }
function int_of(p) { let s = text(p); return s != null && match(s, /^-?[0-9]+$/) ? int(s) : null; }
function mounted() { return ROOT ? !!readfile(ROOT + '/mounted') : true; }
function present() {
    let board = getenv('UNISOC_BATTERY_BOARD') || text(ROOT + '/run/mu300/device') || '';
    // Stock battery-less F50/V50 expose an FGU with synthetic values. Keep the
    // existing MU300 hardware identity rule; never show those as real batteries.
    if (FAMILY == 'mu300' && board in ['f50', 'v50']) { BAT = ''; return false; }
    let names = ['battery', 'sc27xx-fgu', ...(lsdir(PS) || [])], seen = {};
    for (let name in names) {
        if (seen[name]) continue; seen[name] = true;
        let path = PS + '/' + name;
        if (text(path + '/type') == 'Battery' && text(path + '/present') != '0') { BAT = path; return true; }
    }
    BAT = ''; return false;
}
function power_state() {
    let online = false, status = BAT ? text(BAT + '/status') : null;
    // Paired charger status is a family profile input; aggregate E5 battery status stays authoritative.
    for (let name in PROFILE.status_supplies || []) {
        let value = text(PS + '/' + name + '/status');
        if (value != null) { status = value; break; }
    }
    for (let name in lsdir(PS) || []) {
        let path = PS + '/' + name, kind = text(path + '/type');
        if (!match(kind || '', /^(USB|Mains)/) && !(name in ['usb', 'ac', 'bq256xx-charger'])) continue;
        if (int_of(path + '/online') == 1) {
            online = true;
            if (status == null) status = text(path + '/status');
        }
    }
    if (status in ['Charging', 'Full']) online = true;
    return { online, status };
}
function state(pct) {
    let p = power_state();
    if (!p.online) return 'D';
    if (pct != null && (pct >= 100 || (p.status == 'Full' && pct >= FULL_PCT))) return 'F';
    return p.status == 'Charging' ? 'C' : 'P';
}

function add(t, pct, ma, mw, st) {
	push(T, t); push(P, pct); push(MA, ma); push(MW, mw); push(S, st);
}

function parse(data) {
	for (let l in split(data ?? '', '\n')) {
		let f = split(l, ',');
		if (length(f) < 6 || !match(f[0], /^[0-9]+$/)) continue;
		let t = int(f[0]);
		if (length(T) && t <= T[-1]) continue;
		add(t, int(f[1]), int(f[2]), int(f[3]), substr(f[5], 0, 1));
	}
}

function load() {
	T = []; P = []; MA = []; MW = []; S = [];
	parse(readfile(FILE));
	parse(readfile(PENDING));
}

function append(path, line) {
	let f = open(path, 'a');
	if (!f) return false;
	f.write(line);
	f.close();
	return true;
}

/* one record: into memory and onto the store (the lines kept in /run go there first once it is mounted) */
function store(t, pct, ma, mw, mv, st, temp) {
	if (length(T) && t <= T[-1]) return;	/* the clock went back */
	add(t, pct, ma, mw, st);
	let line = sprintf('%d,%d,%d,%d,%d,%s,%d\n', t, pct, ma, mw, mv, st, temp ?? 0);
	if (mounted()) {
		mkdir(ROOT + (FAMILY == 'e5' ? '/etc/e5' : '/etc/mu300'), 0755);
		mkdir(DIR, 0700);
		let p = readfile(PENDING);
		if (p != null && append(FILE, p)) unlink(PENDING);
		if (append(FILE, line)) return;
	}
	mkdir(RUN, 0755);
	append(PENDING, line);
}

/* records older than 10 days: dropped once an hour, the file written anew */
function trim_old(now) {
	if (now - trimmed < 3600 || !length(T) || T[0] >= now - KEEP) return;
	trimmed = now;
	let i = 0;
	while (i < length(T) && T[i] < now - KEEP) i++;
	T = slice(T, i); P = slice(P, i); MA = slice(MA, i); MW = slice(MW, i); S = slice(S, i);
	if (!mounted()) return;
	let keep = [];
	for (let l in split(readfile(FILE) ?? '', '\n')) {
		let t = int(split(l, ',')[0]);
		if (t >= now - KEEP) push(keep, l);
	}
	writefile(FILE + '.tmp', length(keep) ? join('\n', keep) + '\n' : '');
	rename(FILE + '.tmp', FILE);
}

/* the start of a range: { t0, found }; found is false for "full" and "charge" when the records hold no such event (t0 is then their start) */
function range_start(key, now) {
	let n = length(T);
	if (key == '24h') return { t0: now - 86400, found: true };
	if (key == '7d') return { t0: now - 7 * 86400, found: true };
	if (!n) return { t0: now, found: false };
	let i = n - 1;
	if (key == 'charge') {
		/* the start of the newest run of records with the charger plugged in */
		while (i >= 0 && S[i] == 'D') i--;
		if (i < 0) return { t0: T[0], found: false };
		while (i > 0 && S[i - 1] != 'D') i--;
		return { t0: T[i], found: true };
	}
	/* full: the last full record that later records left (a full run that lasts until now is passed over) */
	let full = (k) => S[k] == 'F' && P[k] >= FULL_PCT;
	while (i >= 0 && full(i)) i--;
	while (i >= 0 && !full(i)) i--;
	return i < 0 ? { t0: T[0], found: false } : { t0: T[i], found: true };
}

/* the records of [t0, now] in N steps: the charge, the power drawn from the battery (mW, the average of the minutes on battery) and the state
 * (C before F before P before D when a step holds several) of each step, null for a step without records; and the totals of the range */
const RANK = { C: 4, F: 3, P: 2, D: 1 };
function series(t0, now, points) {
	let span = now - t0, step = span > points * STEP ? span / points : STEP, n = span > 0 ? int((span + step - 1) / step) : 0;
	let pct = [], mw = [], st = [], sum = [], cnt = [];
	for (let k = 0; k < n; k++) { push(pct, null); push(mw, null); push(st, null); push(sum, 0); push(cnt, 0); }
	let used = 0, charged = 0, onbat = 0, energy = 0, first = null, prev = null, i = length(T);
	while (i > 0 && T[i - 1] >= t0) i--;
	for (; i < length(T); i++) {
		let t = T[i];
		if (t > now) break;
		let k = int((t - t0) / step);
		if (k >= n) k = n - 1;
		if (k < 0) continue;
		pct[k] = P[i];
		if (st[k] == null || RANK[S[i]] > RANK[st[k]]) st[k] = S[i];
		if (S[i] == 'D') {
			sum[k] += -MW[i]; cnt[k]++;
			onbat += STEP; energy += -MW[i];
		}
		if (first == null) first = P[i];
		/* gaps (the device was off) count neither way */
		if (prev != null && t - T[prev] <= 3 * STEP) {
			let d = P[i] - P[prev];
			if (d < 0) used -= d; else charged += d;
		}
		prev = i;
	}
	for (let k = 0; k < n; k++) if (cnt[k]) mw[k] = int(sum[k] / cnt[k]);
	energy = int(energy / 60);	/* mW minutes -> mWh */
	return { t0, t1: now, step, pct, mw, st,
		stats: { pct_start: first, pct_now: prev != null ? P[prev] : null, used, charged, on_battery_s: onbat, energy_mwh: energy,
			avg_mw: onbat ? int(energy * 3600 / onbat) : null, per_hour: onbat >= 600 ? used * 3600.0 / onbat : null } };
}

/* Live sysfs readings are independent of the one-minute history bucket. No AT,
 * charger write or UI request can change the fuel gauge or charge policy. */
function snapshot() {
	present();
	let pct = int_of(BAT + '/capacity'), ua = int_of(BAT + '/current_now'), uv = int_of(BAT + '/voltage_now');
	return { t: time(), pct: pct != null && pct >= 0 && pct <= 100 ? pct : null,
		ma: ua != null ? ua / 1000.0 : null, mv: uv != null ? uv / 1000.0 : null,
		mw: ua != null && uv != null ? ua * uv / 1000000000.0 : null,
		st: state(pct), online: power_state().online, temp: int_of(BAT + '/temp') };
}

/* Estimate only from a recent, continuous discharge. Charging, an off-device
 * gap, a rising/stuck gauge and insufficient history produce no estimate. */
function estimate(now) {
	let n = length(T), live = snapshot();
	if (!n || live.online || live.pct == null || S[-1] != 'D' || now - T[-1] > 3 * STEP) return null;
	let i = n - 1;
	while (i > 0 && T[i] - T[i - 1] <= 3 * STEP && S[i - 1] == 'D' && P[i - 1] >= P[i] && T[i - 1] >= now - 3600) i--;
	let seconds = T[-1] - T[i], drop = P[i] - live.pct;
	return seconds >= 600 && drop > 0 ? int(live.pct * seconds / drop) : null;
}

function history(key, points) {
	if (index(RANGES, key) < 0) return { error: 'Unknown range' };
	points = type(points) == 'int' && points >= 10 && points <= 1000 ? points : 240;
	let now = time(), r = range_start(key, now), s = series(r.t0, now, points);
	s.range = key; s.found = r.found; s.first = length(T) ? T[0] : null; s.now = now;
	s.current = snapshot();
	s.present = present();
	s.estimate_s = estimate(now);
	return s;
}

/* every 10 s: one reading into the minute's sums; a new minute writes the last one */
function tick() {
	let now = time(), minute = now - now % STEP;
	if (acc && acc.t != minute) {
		if (acc.n) {
			let n = acc.n;
			store(acc.t, acc.pct, int(acc.ua / n / 1000), int(acc.mw / n), int(acc.uv / n / 1000), acc.st, acc.temp);
		}
		acc = null;
		trim_old(now);
	}
	if (!acc) acc = { t: minute, n: 0, ua: 0, uv: 0, mw: 0 };
	let pct = int_of(BAT + '/capacity'), ua = int_of(BAT + '/current_now'), uv = int_of(BAT + '/voltage_now');
	if (pct == null || ua == null || uv == null || pct < 0 || pct > 100) return;
	acc.n++;
	acc.ua += ua; acc.uv += uv; acc.mw += ua * uv / 1000000000.0;
	acc.pct = pct; acc.st = state(pct); acc.temp = int_of(BAT + '/temp');
}

function init() {
	load();
	trim_old(time());
}

export { init, tick, history, range_start, series, load, store, present, snapshot, estimate, RANGES };
