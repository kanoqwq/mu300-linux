/* Host test of the battery history (usr/share/u30pro/battery-log.uc) against a fake root (U30PRO_BATTLOG_ROOT); see
 * tests/run_battlog_offline.sh */
import * as blog from '/usr/share/u30pro/battery-log.uc';
import { readfile, writefile, unlink } from 'fs';

let failed = 0, total = 0;
function check(name, ok, info) {
	total++;
	if (!ok) failed++;
	printf('%s %s%s\n', ok ? 'PASS' : 'FAIL', name, ok ? '' : ' -- ' + sprintf('%J', info));
}
const ROOT = getenv('U30PRO_BATTLOG_ROOT');
const B = ROOT + '/sys/class/power_supply/u30pro-battery', C = ROOT + '/sys/class/power_supply/u30pro-charger-master';
const FILE = ROOT + '/mnt/data/log/battery.csv', PENDING = ROOT + '/run/u30pro-battery/pending.csv';

/* a day of records ending a minute ago: 600 min on battery 100 -> 70 % at 1.5 W, then charging 70 -> 100 % in 90 min, 30 min full, then on
 * battery again 100 -> 85 % at 2 W until now (with a 30 min gap: the device was off) */
let now = time(), minute = now - now % 60, t = minute - (600 + 90 + 30 + 300) * 60;
const start = t;
let rec = [];
for (let i = 0; i < 600; i++) push(rec, [ t + i * 60, 100 - int(i / 20), -400, -1500, 'D' ]);
t += 600 * 60;
const charge_at = t;
for (let i = 0; i < 90; i++) push(rec, [ t + i * 60, 70 + int(i / 3), 1500, 6000, 'C' ]);
t += 90 * 60;
for (let i = 0; i < 30; i++) push(rec, [ t + i * 60, 100, 0, 0, 'F' ]);
const full_end = t + 29 * 60;
t += 30 * 60;
for (let i = 0; i < 300; i++) {
	if (i >= 100 && i < 130) continue;
	push(rec, [ t + i * 60, 100 - int(i / 20), -530, -2000, 'D' ]);
}
writefile(ROOT + '/mounted', '1');
for (let r in rec) blog.store(r[0], r[1], r[2], r[3], 3900, r[4], 300);
let lines = split(trim(readfile(FILE) ?? ''), '\n');
check('records on the store', length(lines) == length(rec), length(lines));
check('record format', lines[0] == sprintf('%d,100,-400,-1500,3900,D,300', start), lines[0]);
blog.store(start, 50, 0, 0, 3900, 'D', 300);
check('a record from the past is ignored', length(split(trim(readfile(FILE)), '\n')) == length(rec));

/* the ranges */
let r = blog.range_start('charge', minute);
check('charge: start of the last charge', r.found && r.t0 == charge_at, r);
r = blog.range_start('full', minute);
check('full: the device left full', r.found && r.t0 == full_end, r);
r = blog.range_start('24h', minute);
check('24h', r.found && r.t0 == minute - 86400, r);

/* the series since the charge: the charge and the drain after it */
let s = blog.series(charge_at, minute, 60);
check('series has 60 steps or fewer', length(s.pct) <= 60 && length(s.pct) >= 59, length(s.pct));
check('first step charging', s.st[0] == 'C' && s.mw[0] == null, [ s.st[0], s.mw[0] ]);
check('last step on battery at 2 W', s.st[-1] == 'D' && s.mw[-1] == 2000, [ s.st[-1], s.mw[-1] ]);
check('charged 30 points', s.stats.charged == 30, s.stats);
check("used 12 points: the 2 across the gap do not count", s.stats.used == 12, s.stats);
check('on battery 270 min', s.stats.on_battery_s == 270 * 60, s.stats);
check('average 2 W', s.stats.avg_mw == 2000, s.stats);
check('energy 9 Wh', s.stats.energy_mwh == 9000, s.stats);
let gap = 0;
for (let v in s.pct) if (v == null) gap++;
check('the gap shows as empty steps', gap >= 1, s.pct);

/* the charger's "Full" for a moment at 70 % (it starts after a boot) is no full charge */
blog.store(minute - 30, 70, 0, 90, 4060, 'F', 300);
r = blog.range_start('full', minute);
check('a short Full below 95 % is ignored', r.found && r.t0 == full_end, r);

/* sitting full now: the full charge before that */
blog.store(minute, 100, 0, 0, 4300, 'F', 300);
r = blog.range_start('full', minute);
check('full now: the previous full', r.found && r.t0 == full_end, r);
r = blog.range_start('charge', minute);
check('plugged now: this charge (from the short Full on)', r.found && r.t0 == minute - 30, r);

/* history over ubus and the screen file */
let h = blog.history('7d', 100);
check('history 7d', h.range == '7d' && h.found && length(h.pct) <= 100 && h.current.st == 'F', [ h.range, length(h.pct), h.current ]);
check('history refuses an unknown range', blog.history('1y').error != null);
blog.screen_file(minute);
let sf = split(trim(readfile(ROOT + '/run/u30pro-battery/screen')), '\n');
check('screen file: 4 ranges of 4 lines', length(sf) == 16, length(sf));
check('screen file head', match(sf[0], /^R full 1 [0-9]+ [0-9]+ 100 [0-9]+ [0-9]+ [0-9-]+$/) != null, sf[0]);
check('screen file levels', length(split(sf[1], ' ')) == 61 && length(sf[3]) == 62, [ sf[1], sf[3] ]);

/* reload from the file */
blog.load();
r = blog.range_start('charge', minute);
check('reloaded', r.t0 == minute - 30, r);

/* without the store: kept in /run, moved over once it is mounted */
unlink(ROOT + '/mounted');
blog.store(minute + 60, 100, 0, 0, 4300, 'F', 300);
check('pending while not mounted', index(readfile(PENDING) ?? '', sprintf('%d,', minute + 60)) == 0);
writefile(ROOT + '/mounted', '1');
blog.store(minute + 120, 100, 0, 0, 4300, 'F', 300);
let tail = slice(split(trim(readfile(FILE)), '\n'), -2);
check('pending moved to the store', readfile(PENDING) == null && index(tail[0], sprintf('%d,', minute + 60)) == 0 && index(tail[1], sprintf('%d,', minute + 120)) == 0, tail);

/* a reading */
writefile(B + '/capacity', '65\n'); writefile(B + '/current_now', '-700000\n'); writefile(B + '/voltage_now', '3900000\n'); writefile(B + '/temp', '400\n');
writefile(C + '/online', '0\n'); writefile(C + '/status', 'Not charging\n');
blog.tick();
check('tick reads the gauge', true);

printf('%d/%d passed\n', total - failed, total);
exit(failed ? 1 : 0);
