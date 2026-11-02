/* rpcd methods of the CPU page.  usage: ucode cpu-rpc.uc <get|set|save|reset>   (JSON arguments on stdin) */
import { readfile, writefile, rename } from 'fs';
import * as cpu from '/usr/share/u30pro/cpu.uc';

let method = ARGV[0];
let args;
try { args = json(trim(readfile('/dev/stdin') || '') || '{}'); } catch (e) { args = {}; }
if (type(args) != 'object') args = {};
/* exec-plugin methods that carry structured data pass it as a JSON string in args.config */
if (type(args.config) == 'string') {
	try { let c = json(args.config); if (type(c) == 'object') for (let k in c) args[k] = c[k]; } catch (e) {}
}

function out(v) { printf('%J\n', v); exit(0); }

/* CPU load from /proc/stat between this call and the previous one (of any caller), in tenths of a percent, overall and per online core;
 * null on the very first call.  Within 0.2 s of the previous sample the previous result is repeated (too short a window to measure). */
function usage() {
	const PATH = '/run/u30pro-cpu-usage.json';
	let cur = {}, ncpu = 0;
	for (let l in split(readfile('/proc/stat') || '', '\n')) {
		let f = match(l, /^(cpu[0-9]*) +(.*)$/);
		if (!f) continue;
		let v = map(split(trim(f[2]), / +/), (x) => int(x)), total = 0;
		/* user nice system idle iowait irq softirq steal */
		for (let i = 0; i < length(v) && i < 8; i++) total += v[i];
		cur[f[1]] = [ total, v[3] + (v[4] ?? 0) ];
		if (f[1] != 'cpu') ncpu++;
	}
	let prev = null;
	try { prev = json(readfile(PATH) || 'null'); } catch (e) {}
	if (!cur.cpu) return null;
	if (prev && prev.stat && prev.stat.cpu && cur.cpu[0] - prev.stat.cpu[0] < 20 * (ncpu || 1)) return prev.result;
	let pct = (a, b) => {
		let dt = a[0] - b[0], di = a[1] - b[1];
		return dt > 0 ? int((1000 * (dt - di) + dt / 2) / dt) : null;
	};
	let result = null;
	if (prev && prev.stat && prev.stat.cpu) {
		result = { total_tenths: pct(cur.cpu, prev.stat.cpu), cores: {} };
		for (let k in cur) if (k != 'cpu' && prev.stat[k]) result.cores[k] = pct(cur[k], prev.stat[k]);
	}
	writefile(PATH + '.tmp', sprintf('%J', { stat: cur, result: result }));
	rename(PATH + '.tmp', PATH);
	return result;
}

if (method == 'get') {
	let st = cpu.state();
	st.ok = true;
	st.saved = cpu.load_saved();
	st.usage = usage();
	out(st);
}
else if (method == 'set') {
	let r = cpu.set(args);
	if (r.ok && args.save == true) { let s = cpu.save_current(); r.saved = s.ok; if (!s.ok) r.save_error = s.error; }
	out(r);
}
else if (method == 'save') {
	out(cpu.save_current());
}
else if (method == 'reset') {
	let r = cpu.set(cpu.defaults_request());
	if (r.ok) { let c = cpu.clear_saved(); r.cleared = c.ok; }
	out(r);
}
else {
	out({ ok: false, error: 'unknown method ' + method });
}
