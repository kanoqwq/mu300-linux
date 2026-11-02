/* CPU cores and CPU frequency control of the U30 Pro (UMS9632: 6 x Cortex-A55 "little" = CPU0-5, 2 x Cortex-A78 "big" = CPU6-7).
 *
 * Reads the state from /sys/devices/system/cpu and applies core on/off, governor and frequency limits with guards:
 *  - at least one core stays online; any set is accepted, including one little core alone or the two big cores alone (both were tested on
 *    the device under load, CPU0 included: the kernel can hot-unplug the boot CPU);
 *  - governor and frequencies must come from what the cpufreq driver reports, minimum <= maximum;
 *  - one change at a time (lock), read-back verification, and an automatic rollback to the previous state when a step fails.
 * Settings can be stored as the boot default in UCI (u30pro_native_control.cpu); u30pro-cpu-apply applies them at boot.
 * U30PRO_CPU_ROOT prefixes /sys and the lock directory for the host tests. */
import { readfile, writefile, lsdir, stat, mkdir, rmdir } from 'fs';
import * as uci from 'uci';

const ROOT = getenv('U30PRO_CPU_ROOT') || '';
const SYS = ROOT + '/sys/devices/system/cpu';
const THERMAL = ROOT + '/sys/class/thermal';
const LOCK = ROOT + '/run/u30pro-cpu.lock';
const CLUSTERS = [
	{ id: 'little', name: 'Little cores (Cortex-A55)', cpus: [0, 1, 2, 3, 4, 5] },
	{ id: 'big', name: 'Big cores (Cortex-A78)', cpus: [6, 7] }
];
const MIN_ONLINE_TOTAL = 1;

function text(path) { let s = readfile(path); return s == null ? null : trim(s); }
function num(path) { let s = text(path); return (s != null && match(s, /^-?[0-9]+$/)) ? int(s) : null; }
function words(path) { let s = text(path); return s ? split(s, / +/) : []; }
function has(list, v) { for (let x in list) if (x == v) return true; return false; }
function nums(path) { let r = []; for (let w in words(path)) if (match(w, /^[0-9]+$/)) push(r, int(w)); return r; }

function cpu_exists(c) { return stat(SYS + '/cpu' + c) != null; }
function cpu_online(c) {
	let v = num(SYS + '/cpu' + c + '/online');
	return v == null ? cpu_exists(c) : v == 1;       /* cpu0 has no "online" file when it cannot be offlined */
}
function cpufreq_dir(c) { return SYS + '/cpu' + c + '/cpufreq'; }

function cluster_of(c) {
	for (let cl in CLUSTERS) if (has(cl.cpus, c)) return cl;
	return null;
}

function policy_info(cl) {
	/* the policy of a cluster is owned by its first online CPU */
	let owner = null;
	for (let c in cl.cpus) if (cpu_online(c) && stat(cpufreq_dir(c)) != null) { owner = c; break; }
	if (owner == null) return { active: false };
	let d = cpufreq_dir(owner);
	return {
		active: true, owner: owner,
		governor: text(d + '/scaling_governor'), governors: words(d + '/scaling_available_governors'),
		min_khz: num(d + '/scaling_min_freq'), max_khz: num(d + '/scaling_max_freq'), cur_khz: num(d + '/scaling_cur_freq'),
		hw_min_khz: num(d + '/cpuinfo_min_freq'), hw_max_khz: num(d + '/cpuinfo_max_freq'), freqs: nums(d + '/scaling_available_frequencies'),
		driver: text(d + '/scaling_driver')
	};
}

function thermal_info() {
	let zones = {}, soc = null, hottest = null, trips = [], throttle = false, soc_zone = null;
	for (let z in (lsdir(THERMAL) || [])) {
		if (!match(z, /^thermal_zone[0-9]+$/)) continue;
		let type = text(THERMAL + '/' + z + '/type'), t = num(THERMAL + '/' + z + '/temp');
		if (type == null || t == null) continue;
		zones[type] = t / 1000.0;
		if (type == 'soc-thmzone') { soc = t / 1000.0; soc_zone = z; }
		else if (match(type, /^(core[0-9]+|cluster|gpu)-thmzone$/) && (hottest == null || t / 1000.0 > hottest)) hottest = t / 1000.0;
	}
	if (soc_zone != null) {
		for (let i = 0; i < 8; i++) {
			let tt = num(THERMAL + '/' + soc_zone + '/trip_point_' + i + '_temp');
			if (tt == null) break;
			push(trips, { temp_c: tt / 1000.0, type: text(THERMAL + '/' + soc_zone + '/trip_point_' + i + '_type') });
		}
	}
	for (let c in (lsdir(THERMAL) || [])) {
		if (!match(c, /^cooling_device[0-9]+$/)) continue;
		let type = text(THERMAL + '/' + c + '/type'), cur = num(THERMAL + '/' + c + '/cur_state');
		if (type != null && match(type, /^cpufreq/) && cur != null && cur > 0) throttle = true;
	}
	return { soc_c: soc != null ? soc : hottest, soc_zone: soc_zone != null, hottest_c: hottest, trips: trips, throttling: throttle,
		governor: soc_zone != null ? text(THERMAL + '/' + soc_zone + '/policy') : null };
}

function state() {
	let cpus = [], clusters = [], total = 0, online_n = 0;
	let possible = text(SYS + '/possible');
	for (let cl in CLUSTERS) {
		let members = [];
		for (let c in cl.cpus) {
			if (!cpu_exists(c)) continue;
			let on = cpu_online(c);
			let item = { id: c, cluster: cl.id, online: on, can_offline: stat(SYS + '/cpu' + c + '/online') != null,
				cur_khz: on ? num(cpufreq_dir(c) + '/scaling_cur_freq') : null };
			push(cpus, item);
			push(members, c);
			total++;
			if (on) online_n++;
		}
		let p = policy_info(cl);
		push(clusters, { id: cl.id, name: cl.name, cpus: members, policy: p });
	}
	return { possible: possible, total: total, online: online_n, cpus: cpus, clusters: clusters, thermal: thermal_info(),
		min_online_total: MIN_ONLINE_TOTAL, always_online: [] };
}

/* ---- validation ---- */
function intlist(v) {
	if (type(v) != 'array') return null;
	let r = [];
	for (let x in v) { if (type(x) == 'string' && match(x, /^[0-9]+$/)) x = int(x); if (type(x) != 'int' || x < 0) return null; if (!has(r, x)) push(r, x); }
	return r;
}

/* returns an error string, or null when the requested core set is acceptable */
function check_online(want, present) {
	if (want == null) return 'The list of online CPUs is invalid';
	for (let c in want) if (!has(present, c)) return sprintf('CPU%s does not exist', c);
	if (length(want) < MIN_ONLINE_TOTAL) return 'At least one core must stay online';
	return null;
}

function check_policy(cl, req, info) {
	if (!info.active) return sprintf('The %s cluster has no cpufreq policy', cl.id);
	let gov = req.governor != null ? req.governor : info.governor;
	if (!has(info.governors, gov)) return sprintf('Governor %s is not available', gov);
	let mn = req.min_khz != null ? req.min_khz : info.min_khz, mx = req.max_khz != null ? req.max_khz : info.max_khz;
	if (type(mn) != 'int' || type(mx) != 'int') return 'Frequencies must be whole numbers (kHz)';
	if (!has(info.freqs, mn) || !has(info.freqs, mx)) return 'The frequency is not one of the cluster operating points';
	if (mn > mx) return 'The minimum frequency is higher than the maximum';
	return null;
}

/* ---- applying ---- */
function put(path, value) {
	let r = writefile(path, '' + value);
	return r != null && r >= 0;
}

function take_lock() {
	if (mkdir(LOCK, 0700)) return true;
	let st = stat(LOCK);
	if (st != null && time() - st.mtime > 30) {      /* a stale lock of a crashed change */
		rmdir(LOCK);
		return mkdir(LOCK, 0700) ? true : false;
	}
	return false;
}
function drop_lock() { rmdir(LOCK); }

function snapshot() {
	let st = state(), online = [], clusters = {};
	for (let c in st.cpus) if (c.online) push(online, c.id);
	for (let cl in st.clusters) if (cl.policy.active)
		clusters[cl.id] = { governor: cl.policy.governor, min_khz: cl.policy.min_khz, max_khz: cl.policy.max_khz };
	return { online: online, clusters: clusters };
}

/* apply a (validated) target; returns null or an error string.  Order: cores up, policy, cores down. */
function apply_target(target) {
	let st = state(), present = [];
	for (let c in st.cpus) push(present, c.id);
	for (let c in target.online) {
		if (!cpu_online(c) && !put(SYS + '/cpu' + c + '/online', 1)) return sprintf('Cannot bring CPU%s online', c);
	}
	for (let cl in CLUSTERS) {
		let req = target.clusters != null ? target.clusters[cl.id] : null;
		if (req == null) continue;
		let info = policy_info(cl), d = cpufreq_dir(info.owner);
		if (!info.active) continue;
		if (req.governor != null && req.governor != info.governor && !put(d + '/scaling_governor', req.governor)) return sprintf('Cannot set the %s governor', cl.id);
		let mn = req.min_khz != null ? req.min_khz : info.min_khz, mx = req.max_khz != null ? req.max_khz : info.max_khz;
		/* never cross the limits on the way: raise the maximum first, lower the minimum first */
		if (mx >= info.max_khz) { if (!put(d + '/scaling_max_freq', mx) || !put(d + '/scaling_min_freq', mn)) return sprintf('Cannot set the %s frequency limits', cl.id); }
		else { if (!put(d + '/scaling_min_freq', mn) || !put(d + '/scaling_max_freq', mx)) return sprintf('Cannot set the %s frequency limits', cl.id); }
	}
	for (let c in present) {
		if (!has(target.online, c) && cpu_online(c) && !put(SYS + '/cpu' + c + '/online', 0)) return sprintf('CPU%s refused to go offline (it is busy or reserved)', c);
	}
	return null;
}

function verify(target) {
	let st = state();
	for (let c in st.cpus) if (c.online != has(target.online, c.id)) return sprintf('CPU%s did not reach the requested state', c.id);
	for (let cl in st.clusters) {
		let req = target.clusters != null ? target.clusters[cl.id] : null;
		if (req == null || !cl.policy.active) continue;
		if (req.governor != null && cl.policy.governor != req.governor) return sprintf('The %s governor was not accepted', cl.id);
		if (req.min_khz != null && cl.policy.min_khz != req.min_khz) return sprintf('The %s minimum frequency was not accepted', cl.id);
		if (req.max_khz != null && cl.policy.max_khz != req.max_khz) return sprintf('The %s maximum frequency was not accepted', cl.id);
	}
	return null;
}

/* req: { online: [cpu...], clusters: { little: {governor,min_khz,max_khz}, big: {...} } } ; returns { ok, error?, state } */
function set(req) {
	let st = state(), present = [];
	for (let c in st.cpus) push(present, c.id);
	let target = { online: req.online != null ? intlist(req.online) : null, clusters: {} };
	if (target.online == null) {
		target.online = [];
		for (let c in st.cpus) if (c.online) push(target.online, c.id);
	}
	let e = check_online(target.online, present);
	if (e) return { ok: false, error: e };
	if (req.clusters != null && type(req.clusters) != 'object') return { ok: false, error: 'The cluster settings are invalid' };
	for (let cl in CLUSTERS) {
		let r = req.clusters != null ? req.clusters[cl.id] : null;
		if (r == null) continue;
		if (type(r) != 'object') return { ok: false, error: 'The cluster settings are invalid' };
		for (let k in ['min_khz', 'max_khz']) if (type(r[k]) == 'string' && match(r[k], /^[0-9]+$/)) r[k] = int(r[k]);
		e = check_policy(cl, r, st.clusters[cl.id == 'little' ? 0 : 1].policy);
		if (e) return { ok: false, error: e };
		target.clusters[cl.id] = r;
	}
	if (!take_lock()) return { ok: false, error: 'Another CPU change is in progress, try again in a moment' };
	let before = snapshot();
	e = apply_target(target);
	if (!e) e = verify(target);
	/* the cpufreq core settles asynchronously after a CPU came online (observed on the device: a limit written right after a core
	 * was switched on read back unchanged); write again and look again a few times before giving up */
	for (let i = 0; e && i < 3; i++) {
		sleep(300);
		let again = apply_target(target);
		e = again ? again : verify(target);
	}
	if (e) {
		apply_target(before);                                   /* best effort rollback */
		drop_lock();
		return { ok: false, error: e, rolled_back: true, state: state() };
	}
	drop_lock();
	return { ok: true, state: state() };
}

/* ---- boot defaults (UCI) ---- */
function load_saved() {
	let c = uci.cursor(), s = c.get_all('u30pro_native_control', 'cpu');
	if (s == null) return null;
	let r = { online: null, clusters: {} };
	if (type(s.online) == 'string') r.online = intlist(map(split(trim(s.online), / +/), (x) => match(x, /^[0-9]+$/) ? int(x) : -1));
	for (let cl in CLUSTERS) {
		let g = s['gov_' + cl.id], mn = s['min_' + cl.id], mx = s['max_' + cl.id];
		if (g == null && mn == null && mx == null) continue;
		r.clusters[cl.id] = {};
		if (type(g) == 'string') r.clusters[cl.id].governor = g;
		if (type(mn) == 'string' && match(mn, /^[0-9]+$/)) r.clusters[cl.id].min_khz = int(mn);
		if (type(mx) == 'string' && match(mx, /^[0-9]+$/)) r.clusters[cl.id].max_khz = int(mx);
	}
	return r;
}

function save_current() {
	let snap = snapshot(), c = uci.cursor();
	c.set('u30pro_native_control', 'cpu', 'cpu');
	c.set('u30pro_native_control', 'cpu', 'online', join(' ', snap.online));
	for (let cl in CLUSTERS) {
		let s = snap.clusters[cl.id];
		if (s == null) continue;
		c.set('u30pro_native_control', 'cpu', 'gov_' + cl.id, s.governor);
		c.set('u30pro_native_control', 'cpu', 'min_' + cl.id, '' + s.min_khz);
		c.set('u30pro_native_control', 'cpu', 'max_' + cl.id, '' + s.max_khz);
	}
	return c.commit('u30pro_native_control') ? { ok: true } : { ok: false, error: 'Cannot save the CPU settings' };
}

function clear_saved() {
	let c = uci.cursor();
	c.delete('u30pro_native_control', 'cpu');
	return c.commit('u30pro_native_control') ? { ok: true } : { ok: false, error: 'Cannot clear the CPU settings' };
}

/* defaults: every core online, schedutil, full frequency range */
function defaults_request() {
	let st = state(), online = [], clusters = {};
	for (let c in st.cpus) push(online, c.id);
	for (let cl in st.clusters) if (cl.policy.active) {
		clusters[cl.id] = { governor: has(cl.policy.governors, 'schedutil') ? 'schedutil' : cl.policy.governor,
			min_khz: cl.policy.hw_min_khz, max_khz: cl.policy.hw_max_khz };
	}
	return { online: online, clusters: clusters };
}

export { cluster_of, state, check_online, set, load_saved, save_current, clear_saved, defaults_request };
