/* Host test of the battery / temperature protection (usr/share/u30pro/safety.uc) against a fake /sys tree (U30PRO_SAFETY_ROOT);
 * see tests/run_safety_offline.sh */
import * as safety from '/usr/share/u30pro/safety.uc';
import { readfile, writefile } from 'fs';

let failed = 0, total = 0;
function check(name, ok, info) {
	total++;
	if (!ok) failed++;
	printf('%s %s%s\n', ok ? 'PASS' : 'FAIL', name, ok ? '' : ' -- ' + sprintf('%J', info));
}
const ROOT = getenv('U30PRO_SAFETY_ROOT');
const CFG = { enabled: true, low_warn_percent: 10, low_shutdown_percent: 3, low_shutdown_mv: 3350, low_ticks: 6,
	pause_batt_c: 55, pause_chg_c: 65, pause_board_c: 75, pause_rf_c: 85, emergency_c: 95, emergency_batt_c: 65,
	hot_ticks: 3, cool_ticks: 6, resume_margin_c: 8 };

function m(over) {
	let r = { capacity: 80, voltage_mv: 3900, batt_c: 30, online: true, temps: { charger: 35, board: 35, ambient: 33, rf_234g: 36, rf_5g: 36 } };
	for (let k, v in (over || {})) {
		if (k == 'temps') for (let a, b in v) r.temps[a] = b;
		else r[k] = v;
	}
	return r;
}
/* run n evaluations of the same measurement, return the last result and the actions of all of them */
function run(meas, n, mem) {
	let all = [], r = null;
	for (let i = 0; i < n; i++) {
		r = safety.evaluate(meas, CFG, mem);
		mem = r.memory;
		for (let a in r.actions) push(all, a);
	}
	return { r: r, actions: all, mem: mem };
}
function has(list, v) { for (let x in list) if (x == v) return true; return false; }

/* normal operation */
let x = run(m(), 20);
check('normal: no action, level ok', length(x.actions) == 0 && x.r.verdict.level == 'ok', x.r);

/* low battery */
x = run(m({ online: false, capacity: 8 }), 3);
check('8% on battery: warning only', length(x.actions) == 0 && x.r.verdict.level == 'warning' && has(x.r.verdict.reasons, 'battery_low'), x.r);
x = run(m({ online: false, capacity: 3 }), 5);
check('3% on battery: critical but not yet (debounce 6)', length(x.actions) == 0 && x.r.verdict.level == 'critical', x);
x = run(m({ online: false, capacity: 3 }), 6);
check('3% on battery for 6 evaluations: orderly power off', has(x.actions, 'poweroff:battery'), x);
x = run(m({ online: true, capacity: 2 }), 12);
check('2% while a charger is connected: nothing', length(x.actions) == 0, x);
x = run(m({ online: false, capacity: 50, voltage_mv: 3300 }), 6);
check('3.3 V on battery: power off', has(x.actions, 'poweroff:battery'), x);
x = run(m({ online: false, capacity: 3 }), 4);
let y = run(m({ online: false, capacity: 20 }), 1, x.mem);
x = run(m({ online: false, capacity: 3 }), 4, y.mem);
check('the low-battery counter restarts after a good reading', length(x.actions) == 0, x);
x = run(m({ online: null, capacity: 2 }), 12);
check('unknown charger state is never "on battery"', length(x.actions) == 0, x);
x = run(m({ capacity: null, voltage_mv: null, online: false }), 12);
check('unreadable battery is ignored', length(x.actions) == 0, x);

/* charge pause and resume */
x = run(m({ batt_c: 56 }), 2);
check('hot battery: not yet (debounce 3)', length(x.actions) == 0, x);
x = run(m({ batt_c: 56 }), 3);
check('hot battery for 3 evaluations: pause charging', has(x.actions, 'pause-charge') && x.r.verdict.charge_paused, x);
let paused = x.mem;
x = run(m({ batt_c: 50 }), 6, paused);
check('48 C is not 8 C below 55: still paused', !has(x.actions, 'resume-charge') && x.r.verdict.charge_paused, x);
x = run(m({ batt_c: 45 }), 5, paused);
check('cool for 5 evaluations: still paused', !has(x.actions, 'resume-charge'), x);
x = run(m({ batt_c: 45 }), 6, paused);
check('cool for 6 evaluations: resume charging', has(x.actions, 'resume-charge') && !x.r.verdict.charge_paused, x);
x = run(m({ temps: { charger: 66 } }), 3);
check('hot charger NTC pauses charging', has(x.actions, 'pause-charge'), x);
x = run(m({ temps: { rf_5g: 86 } }), 3);
check('hot RF NTC pauses charging', has(x.actions, 'pause-charge'), x);
x = run(m({ temps: { board: 76 } }), 3);
check('hot board NTC pauses charging', has(x.actions, 'pause-charge'), x);
x = run(m({ temps: { board: 70 } }), 10);
check('board 70 C is fine', length(x.actions) == 0, x);
x = run(m({ batt_c: 60, online: false }), 10);
check('hot battery with no charger: nothing to pause, no power off', length(x.actions) == 0 && x.r.verdict.level == 'warning', x);

/* emergency */
x = run(m({ temps: { board: 96 } }), 2);
check('96 C board: not yet', !has(x.actions, 'poweroff:temperature'), x);
x = run(m({ temps: { board: 96 } }), 3);
check('96 C board for 3 evaluations: pause charging and power off', has(x.actions, 'pause-charge') && has(x.actions, 'poweroff:temperature'), x);
x = run(m({ batt_c: 66 }), 3);
check('66 C battery: power off', has(x.actions, 'poweroff:temperature'), x);
x = run(m({ temps: { board: 96 } }), 1);
y = run(m(), 1, x.mem);
x = run(m({ temps: { board: 96 } }), 2, y.mem);
check('one normal reading resets the emergency counter', !has(x.actions, 'poweroff:temperature'), x);
x = run(m({ temps: { ambient: null } }), 6);
check('a missing sensor is not a reason to act', length(x.actions) == 0, x);

/* real I/O: fake sysfs */
function put(path, v) { writefile(ROOT + path, v + '\n'); }
put('/sys/class/power_supply/u30pro-battery/capacity', 77);
put('/sys/class/power_supply/u30pro-battery/voltage_now', 3980000);
put('/sys/class/power_supply/u30pro-battery/temp', 312);
put('/sys/class/power_supply/u30pro-charger-master/online', 1);
put('/sys/class/power_supply/sprd-tcpm-source-psy-sc27xx-pd/online', 1);
let meas = safety.measure();
check('measure: battery values and units', meas.capacity == 77 && meas.voltage_mv == 3980 && meas.batt_c == 31.2 && meas.online === true, meas);
check('measure: the thermal zones are found by name', meas.temps.board == 36.5 && meas.temps.charger == 41.75 && meas.temps.rf_5g == 36.7 && meas.temps.ambient == 35, meas.temps);
check('measure: wcn/usb/skin zones are not used', meas.temps.wcn == null && meas.temps.skin == null && meas.temps.usb == null, meas.temps);

/* tick: hot charger for three runs pauses charging, then it is logged and the verdict file written */
put('/sys/class/thermal/thermal_zone3/temp', 67000);
let last;
for (let i = 0; i < 3; i++) last = safety.tick();
check('tick: three hot runs pause charging', has(last.actions, 'pause-charge'), last);
let act = readfile(ROOT + '/run/u30pro-safety-actions.log') || '';
check('tick: the pause is logged and the charger is told', index(act, 'pausing charging') >= 0 && index(act, 'inhibit-charge') >= 0, act);
let st = json(readfile(ROOT + '/run/u30pro-safety.json'));
check('tick: the verdict file is written', st && st.verdict.charge_paused === true && st.verdict.level == 'warning', st);

printf('\n%d checks, %d failed\n', total, failed);
exit(failed ? 1 : 0);
