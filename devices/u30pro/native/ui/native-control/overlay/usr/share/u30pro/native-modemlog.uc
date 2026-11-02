import { readfile, writefile, popen, mkdir, unlink, stat, lsdir } from 'fs';

/* Native modem log (see docs/research/modem-log.md): raises the log level of the modem's protocol stack with the one AT syntax that is known to work
 * (AT+SPLOGLEVEL=1,<level>,"<module mask>","<SAP mask>"), drains /dev/slog_nr into /tmp/modemlog and shows the readable messages.
 *   start {level 1..5, minutes 1..60}  stop  status  view {lines, filter, match}  clear */
const D = '/tmp/modemlog';
const MASK_M = 'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF', MASK_S = 'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF';
function out(v) { printf('%J\n', v); exit(0); }
function fail(s) { out({ ok: false, error: s }); }
function run(s) { let p = popen(s, 'r'); if (!p) return ''; let v = p.read('all') || ''; p.close(); return trim(v); }
function input() { try { return json(trim(readfile('/dev/stdin') || '') || '{}') || {}; } catch (e) { return {}; } }
function running() { let pid = trim(readfile(D + '/reader.pid') || ''); return !!(match(pid, /^[0-9]+$/) && stat('/proc/' + pid) != null); }
function at(cmd) { let r = run('u30pro-atc \'' + cmd + '\''); try { return json(r) || { ok: false }; } catch (e) { return { ok: false }; } }
function cur_level() {
	let r = at('AT+SPLOGLEVEL?');
	if (r.ok) for (let l in r.lines) { let m = match(l, /LEVEL: (-?[0-9]+)/); if (m) return int(m[1]); }
	return null;
}
function set_level(n) { return at(sprintf('AT+SPLOGLEVEL=1,%d,"%s","%s"', n, MASK_M, MASK_S)); }
function files() {
	let list = split(run('ls -tr ' + D + '/nr.*.bin 2>/dev/null'), '\n'), r = [];
	for (let f in list) if (f != '') push(r, f);
	return r;
}
function sizes() {
	let n = 0;
	for (let f in files()) { let s = stat(f); if (s) n += s.size; }
	return n;
}
function status() {
	let st = { running: running(), level: cur_level(), bytes: sizes(), files: length(files()) };
	let meta = trim(readfile(D + '/meta') || '');
	let m = match(meta, /^([0-9]+) ([0-9]+) (-?[0-9]+)$/);
	if (m) { st.started = int(m[1]); st.minutes = int(m[2]); st.set_level = int(m[3]); st.seconds_left = max(0, int(m[1]) + int(m[2]) * 60 - time()); }
	return st;
}
/* kill a process and everything below it (the busybox of the router has no pkill) */
function kill_tree(pid) {
	let kids = [];
	for (let e in lsdir('/proc')) {
		if (!match(e, /^[0-9]+$/)) continue;
		let st = readfile('/proc/' + e + '/stat') || '', m = match(st, /^[0-9]+ \(.*\) [A-Za-z] ([0-9]+) /);
		if (m && m[1] == '' + pid) push(kids, e);
	}
	for (let k in kids) kill_tree(k);
	run('kill ' + pid + ' 2>/dev/null');
}
function stop(auto) {
	let was = running();
	if (cur_level() != null && cur_level() != -1) set_level(-1);
	let pid = trim(readfile(D + '/reader.pid') || '');
	if (match(pid, /^[0-9]+$/)) kill_tree(pid);
	let ap = trim(readfile(D + '/autostop.pid') || '');
	if (!auto && match(ap, /^[0-9]+$/)) kill_tree(ap);
	unlink(D + '/reader.pid'); unlink(D + '/autostop.pid'); unlink(D + '/meta');
	return was;
}

const NOISE = /NVM DELTA|NVParam|OSAL_tmr|DsmSio|rohc:|EAP_|DELTA NV|chr function|mcdt_|NData_control|sipa seq|sipa_dele|PROD_|NRL2 Normal/;
const SIGNALLING = /RRC|NAS|EMM|ESM|5GMM|5GSM|PDP|PDN|CMCP|ATTACH|DETACH|REGIST|SIB|MEAS|HANDOVER|RECONF|CELL|RACH|RANDOM|PAGING|SERVICE_REQ|MSG_ID|APP_MN|NRCON|NRINA|NRIDL|NRCRH|NRSRH|LRRC|CSFB|IMS|ISIP|SIGNAL|PLMN|SEL_CELL|AUTH|SECURITY|TAU|TRACKING/;
const AT_DIALOGUE = /^\+|^AT|ATC_|\^CEND/;

let method = ARGV[0];
if (method == 'status') out({ ok: true, status: status() });
else if (method == 'start') {
	let a = input(), level = a.level == null ? 5 : int(a.level), minutes = a.minutes == null ? 10 : int(a.minutes);
	if (level < 1 || level > 5) fail('Level must be 1 to 5');
	if (minutes < 1 || minutes > 60) fail('Duration must be 1 to 60 minutes');
	if (running()) fail('The modem log is already being captured');
	mkdir(D, 0700);
	run('rm -f ' + D + '/nr.*.bin');
	run('setsid /usr/sbin/u30pro-modemlog-reader </dev/null >/dev/null 2>&1 &');
	for (let i = 0; i < 20 && !running(); i++) run('sleep 0.2 2>/dev/null || sleep 1');
	if (!running()) fail('The reader did not start');
	let r = set_level(level);
	if (!r.ok) { stop(); fail('The modem refused the log level'); }
	writefile(D + '/meta', sprintf('%d %d %d\n', time(), minutes, level));
	run(sprintf('(setsid sh -c "sleep %d; ucode /usr/share/u30pro/native-modemlog.uc stop auto </dev/null >/dev/null" </dev/null >/dev/null 2>&1 & echo $! >%s/autostop.pid)', minutes * 60, D));
	out({ ok: true, status: status() });
}
else if (method == 'stop') { stop(ARGV[1] == 'auto'); out({ ok: true, status: status() }); }
else if (method == 'clear') { run('rm -f ' + D + '/nr.*.bin'); out({ ok: true, status: status() }); }
else if (method == 'view') {
	let a = input(), n = a.lines == null ? 200 : int(a.lines), filter = a.filter || 'signalling', rx = null;
	if (n < 1 || n > 2000) n = 200;
	let fl = files();
	if (!length(fl)) out({ ok: true, lines: [], status: status() });
	if (type(a.match) == 'string' && length(a.match) > 0 && length(a.match) < 60 && !match(a.match, /[^A-Za-z0-9_ .:+*^$|()\[\]-]/)) rx = regexp(a.match, 'i');
	let raw = run('strings -n 8 ' + join(' ', fl) + ' 2>/dev/null | tail -n 60000'), lines = [], last = null;
	for (let l in split(raw, '\n')) {
		if (!match(l, /[A-Za-z]{4}/) || match(l, NOISE)) continue;
		if (filter == 'signalling' && !match(l, SIGNALLING)) continue;
		if (filter == 'at' && !match(l, AT_DIALOGUE)) continue;
		if (rx && !match(l, rx)) continue;
		if (l == last) continue;
		last = l;
		push(lines, length(l) > 200 ? substr(l, 0, 200) : l);
	}
	if (length(lines) > n) lines = slice(lines, length(lines) - n);
	out({ ok: true, lines: lines, status: status() });
}
else fail('Unknown method');
