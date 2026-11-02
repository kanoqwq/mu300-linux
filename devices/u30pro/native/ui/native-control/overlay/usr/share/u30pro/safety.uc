/* Battery and temperature protection of the U30 Pro (user space, one evaluation every 10 s by the service /usr/sbin/u30pro-safety, which
 * serves the result as the ubus object u30pro.safety, method status).
 *
 * What the kernel already does: the SoC zone throttles the CPUs (passive trips 70/85 C) and powers off at the critical trip (115 C); the charger
 * driver applies the stock JEITA table by battery temperature (stops below 0 C and above 50 C).  What it does not do and this module adds:
 *  - low battery: on battery (no charger online) with the charge at or below `low_shutdown_percent`, or the voltage at or below
 *    `low_shutdown_mv`, for `low_ticks` evaluations in a row -> orderly power off (sync, then poweroff), so the flash is not cut in the
 *    middle of a write when the PMIC browns out;
 *  - charge pause: while charging, the battery (>= pause_batt_c), the charger NTC (>= pause_chg_c), the board NTC (>= pause_board_c) or an RF
 *    NTC (>= pause_rf_c) is too hot for `hot_ticks` evaluations -> "inhibit-charge" until everything is `resume_margin_c` below its limit for
 *    `cool_ticks` evaluations.  A user's own inhibit-charge (bypass mode) is never touched: only a pause started here is ended here;
 *  - emergency: the board, charger, ambient or RF NTC >= emergency_c, or the battery >= emergency_batt_c, for `hot_ticks` evaluations
 *    -> charging paused and orderly power off.
 * The wcn/usb channels and the case estimates (frame, front, back, bkchg, skin) are not used: on this board the first two read a
 * constant ~60 C (the stock kernel too) and the estimates are built from them.
 *
 * Sensor reads that fail are ignored (never a reason to act).  State and verdict (counters + `level`, `reasons`, `charge_paused`): status(),
 * renewed at every evaluation.  U30PRO_SAFETY_ROOT prefixes /sys and /run, makes the power off a log line and writes the state to
 * <root>/run/u30pro-safety.json, for the host tests. */
import { readfile, writefile, popen } from 'fs';
import * as uci from 'uci';

const ROOT = getenv('U30PRO_SAFETY_ROOT') || '';
const DRY = ROOT != '';
const STATE = ROOT + '/run/u30pro-safety.json';
const ACTIONS = ROOT + '/run/u30pro-safety-actions.log';
const PS = ROOT + '/sys/class/power_supply';
const THERMAL = ROOT + '/sys/class/thermal';

const DEFAULTS = {
	enabled: true,
	low_warn_percent: 10, low_shutdown_percent: 3, low_shutdown_mv: 3350, low_ticks: 6,
	pause_batt_c: 55, pause_chg_c: 65, pause_board_c: 75, pause_rf_c: 85,
	emergency_c: 95, emergency_batt_c: 65, hot_ticks: 3, cool_ticks: 6, resume_margin_c: 8
};
const ZONES = [ [ 'chg-thmzone', 'charger' ], [ 'board-thmzone', 'board' ], [ 'ambient-thmzone', 'ambient' ], [ 'pa-thmzone', 'rf_234g' ], [ 'pa5g-thmzone', 'rf_5g' ] ];

function text(path) { let s = readfile(path); return s == null ? null : trim(s); }
function int_of(path) { let s = text(path); return (s != null && match(s, /^-?[0-9]+$/)) ? int(s) : null; }

function settings() {
	let cfg = {};
	for (let k, v in DEFAULTS) cfg[k] = v;
	if (!DRY) {
		let c = uci.cursor();
		for (let k, v in DEFAULTS) {
			let s = c.get('u30pro_native_control', 'safety', k);
			if (s == null || s == '') continue;
			if (type(v) == 'bool') cfg[k] = (s != '0');
			else if (match(s, /^[0-9]+$/)) cfg[k] = int(s);
		}
	}
	return cfg;
}

/* the measurements; a missing one is null */
function measure() {
	let m = { capacity: null, voltage_mv: null, batt_c: null, online: null, temps: {} };
	m.capacity = int_of(PS + '/u30pro-battery/capacity');
	let uv = int_of(PS + '/u30pro-battery/voltage_now');
	if (uv != null) m.voltage_mv = uv / 1000.0;
	let t = int_of(PS + '/u30pro-battery/temp');
	if (t != null) m.batt_c = t / 10.0;
	let on = int_of(PS + '/u30pro-charger-master/online');
	let pd = int_of(PS + '/sprd-tcpm-source-psy-sc27xx-pd/online');
	if (on != null || pd != null) m.online = (on == 1 || pd == 1);
	for (let z = 0; z < 64; z++) {
		let zone = THERMAL + '/thermal_zone' + z, kind = text(zone + '/type');
		if (kind == null) continue;
		for (let zz in ZONES) {
			if (zz[0] != kind) continue;
			let v = int_of(zone + '/temp');
			if (v != null && v > -40000 && v < 200000) m.temps[zz[1]] = v / 1000.0;
		}
	}
	return m;
}

function hottest_over(m, cfg, limits) {
	/* list of [name, temp, limit] that reached its limit; limits maps sensor -> limit in C */
	let r = [];
	if (m.batt_c != null && limits.battery != null && m.batt_c >= limits.battery) push(r, [ 'battery', m.batt_c, limits.battery ]);
	for (let name, t in m.temps)
		if (limits[name] != null && t >= limits[name]) push(r, [ name, t, limits[name] ]);
	return r;
}

/* one evaluation: (measurements, settings, memory) -> { actions, memory, verdict }; no I/O, the host tests call it directly */
function evaluate(m, cfg, mem) {
	mem = mem || {};
	let n = { low: mem.low || 0, hot: mem.hot || 0, emerg: mem.emerg || 0, cool: mem.cool || 0 };
	let paused = !!mem.paused;
	let actions = [], reasons = [], level = 'ok';

	let on_battery = (m.online === false);
	let low = on_battery && ((m.capacity != null && m.capacity <= cfg.low_shutdown_percent) ||
		(m.voltage_mv != null && m.voltage_mv <= cfg.low_shutdown_mv));
	n.low = low ? n.low + 1 : 0;
	if (low) { level = 'critical'; push(reasons, 'battery_critical'); }
	else if (on_battery && m.capacity != null && m.capacity <= cfg.low_warn_percent) { level = 'warning'; push(reasons, 'battery_low'); }
	if (n.low >= cfg.low_ticks) push(actions, 'poweroff:battery');

	let pause_limits = { battery: cfg.pause_batt_c, charger: cfg.pause_chg_c, board: cfg.pause_board_c, rf_234g: cfg.pause_rf_c, rf_5g: cfg.pause_rf_c };
	let emerg_limits = { battery: cfg.emergency_batt_c, charger: cfg.emergency_c, board: cfg.emergency_c, ambient: cfg.emergency_c,
		rf_234g: cfg.emergency_c, rf_5g: cfg.emergency_c };
	let hot = hottest_over(m, cfg, pause_limits), emerg = hottest_over(m, cfg, emerg_limits);

	n.emerg = length(emerg) ? n.emerg + 1 : 0;
	if (length(emerg)) { level = 'critical'; for (let h in emerg) push(reasons, 'emergency:' + h[0]); }
	if (n.emerg >= cfg.hot_ticks) {
		if (!paused) push(actions, 'pause-charge');
		push(actions, 'poweroff:temperature');
		paused = true;
	}

	/* charge pause only matters while a charger is there; the hot counter keeps counting so that unplugging does not hide a hot battery */
	n.hot = length(hot) ? n.hot + 1 : 0;
	if (length(hot)) {
		if (level == 'ok') level = 'warning';
		for (let h in hot) push(reasons, 'hot:' + h[0]);
	}
	if (!paused && m.online !== false && n.hot >= cfg.hot_ticks) {
		push(actions, 'pause-charge');
		paused = true;
		n.cool = 0;
	}

	if (paused) {
		let cool_limits = {};
		for (let k, v in pause_limits) cool_limits[k] = v - cfg.resume_margin_c;
		let still = hottest_over(m, cfg, cool_limits);
		n.cool = length(still) ? 0 : n.cool + 1;
		if (length(emerg) == 0 && n.cool >= cfg.cool_ticks) {
			push(actions, 'resume-charge');
			paused = false;
			n.cool = 0;
		}
		else if (level == 'ok')
			level = 'warning';
		if (paused) push(reasons, 'charge_paused');
	}

	return { actions: actions, memory: { low: n.low, hot: n.hot, emerg: n.emerg, cool: n.cool, paused: paused },
		verdict: { level: level, reasons: reasons, charge_paused: paused, on_battery: on_battery, capacity: m.capacity,
			battery_c: m.batt_c, board_c: m.temps } };
}

function log(msg) {
	if (DRY) {
		let f = readfile(ACTIONS) || '';
		writefile(ACTIONS, f + msg + '\n');
	}
	else {
		let p = popen('logger -t u30pro-safety ' + "'" + replace(msg, "'", '') + "'", 'r');
		if (p) p.close();
	}
}

function sh(command) {
	if (DRY) { log('run ' + command); return true; }
	let p = popen(command + ' 2>&1', 'r');
	if (!p) return false;
	p.read('all');
	return p.close() == 0;
}

function do_action(a) {
	if (a == 'pause-charge') {
		log('pausing charging: temperature');
		return sh('echo inhibit-charge > /sys/class/power_supply/u30pro-charger-master/charge_behaviour');
	}
	if (a == 'resume-charge') {
		log('resuming charging: temperatures are back to normal');
		/* only when the charger is still inhibited (not when something else already put it back) */
		let cur = text(PS + '/u30pro-charger-master/charge_behaviour') || '';
		if (!DRY && index(cur, '[inhibit-charge]') < 0)
			return true;
		return sh('echo auto > /sys/class/power_supply/u30pro-charger-master/charge_behaviour');
	}
	if (substr(a, 0, 9) == 'poweroff:') {
		log('orderly power off: ' + substr(a, 9));
		sh('sync');
		return sh('poweroff');
	}
	return false;
}

/* the user's own "inhibit-charge" (bypass) is left alone: a pause started here is only ended here, and a resume is skipped when the
 * charger is no longer in the state this module set */
let last = null;

function tick() {
	let cfg = settings();
	if (!cfg.enabled) {
		last = { actions: [], memory: {}, verdict: { level: 'ok', reasons: [ 'disabled' ] } };
		if (DRY) writefile(STATE, sprintf('%J', last));
		return last;
	}
	let mem = last?.memory ?? {};
	if (DRY && last == null) { let prev = readfile(STATE); if (prev != null) { let j = json(prev); if (j && j.memory) mem = j.memory; } }
	last = evaluate(measure(), cfg, mem);
	for (let a in last.actions) do_action(a);
	if (DRY) writefile(STATE, sprintf('%J', last));
	return last;
}

/* the result of the last evaluation */
function status() { return last; }

export { measure, evaluate, tick, status };
