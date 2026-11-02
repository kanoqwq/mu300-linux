/* Host test of the CPU control backend against a fake /sys tree (U30PRO_CPU_ROOT); see tests/run_cpu_offline.sh */
import * as cpu from '/usr/share/u30pro/cpu.uc';
import { readfile, unlink, mkdir } from 'fs';

let failed = 0, total = 0;
function check(name, ok, info) {
	total++;
	if (!ok) failed++;
	printf('%s %s%s\n', ok ? 'PASS' : 'FAIL', name, ok ? '' : ' -- ' + sprintf('%J', info));
}
const ROOT = getenv('U30PRO_CPU_ROOT');
function online(c) { return trim(readfile(ROOT + '/sys/devices/system/cpu/cpu' + c + '/online') || '1'); }
function gov(c) { return trim(readfile(ROOT + '/sys/devices/system/cpu/cpu' + c + '/cpufreq/scaling_governor')); }
function maxf(c) { return trim(readfile(ROOT + '/sys/devices/system/cpu/cpu' + c + '/cpufreq/scaling_max_freq')); }

let st = cpu.state();
check('eight CPUs are found', st.total == 8 && st.online == 8, st);
check('two clusters with a policy', length(st.clusters) == 2 && st.clusters[0].policy.active && st.clusters[1].policy.active, st.clusters);
check('little cluster is CPU0-5 and big is CPU6-7', length(st.clusters[0].cpus) == 6 && st.clusters[1].cpus[0] == 6, st.clusters);
check('every CPU with an online file can be offlined', st.cpus[0].can_offline && st.cpus[1].can_offline && st.cpus[7].can_offline, st.cpus);
check('thermal: SoC temperature and trips are reported', st.thermal.soc_c == 44.5 && st.thermal.soc_zone && length(st.thermal.trips) == 2, st.thermal);

let all = [0, 1, 2, 3, 4, 5, 6, 7];
check('guard: at least one core', cpu.check_online([], all) != null);
check('one little core alone is accepted', cpu.check_online([0], all) == null);
check('the two big cores alone are accepted', cpu.check_online([6, 7], all) == null);
check('a single big core alone is accepted', cpu.check_online([7], all) == null);
check('guard: unknown CPU', cpu.check_online([0, 6, 9], all) != null);
check('guard: not a list', cpu.check_online(null, all) != null);
check('one little + one big is accepted', cpu.check_online([0, 6], all) == null);
check('all cores are accepted', cpu.check_online(all, all) == null);

let r = cpu.set({ online: [0, 1, 2, 6, 7] });
check('switching off CPU3-5 works', r.ok && online(3) == '0' && online(4) == '0' && online(5) == '0' && online(6) == '1', r);
r = cpu.set({ online: [0, 1, 2, 3, 4, 5, 6, 7] });
check('switching them back on works', r.ok && online(3) == '1' && online(5) == '1', r);
r = cpu.set({ online: [0, 1, 2, 3, 4, 5] });
check('little cores only', r.ok && online(6) == '0' && online(7) == '0' && online(0) == '1', r);
r = cpu.set({ online: [6, 7] });
check('big cores only (CPU0 off)', r.ok && online(0) == '0' && online(5) == '0' && online(6) == '1' && online(7) == '1', r);
r = cpu.set({ online: [0] });
check('one little core alone', r.ok && online(0) == '1' && online(6) == '0' && online(7) == '0', r);
r = cpu.set({ online: [] });
check('refuses to switch every core off', !r.ok && online(0) == '1', r);
r = cpu.set({ online: [0, 1, 2, 3, 4, 5, 6, 7] });
check('everything back on', r.ok && online(0) == '1' && online(7) == '1', r);

r = cpu.set({ clusters: { big: { governor: 'performance' } } });
check('big governor change', r.ok && gov(6) == 'performance' && gov(0) == 'schedutil', r);
r = cpu.set({ clusters: { big: { governor: 'nonsense' } } });
check('unknown governor is refused', !r.ok && gov(6) == 'performance', r);
r = cpu.set({ clusters: { little: { max_khz: 1534000 } } });
check('maximum frequency of the little cluster', r.ok && maxf(0) == '1534000', r);
r = cpu.set({ clusters: { little: { min_khz: 1950000 } } });
check('minimum above the maximum is refused', !r.ok, r);
r = cpu.set({ clusters: { little: { max_khz: 1234567 } } });
check('a frequency that is not an operating point is refused', !r.ok && maxf(0) == '1534000', r);
r = cpu.set({ clusters: { little: { min_khz: '689000', max_khz: '2041000' } } });
check('frequencies given as strings are accepted', r.ok && maxf(0) == '2041000', r);

/* a CPU that cannot be switched off (its online file is a directory in the fake tree): error and rollback */
unlink(ROOT + '/sys/devices/system/cpu/cpu5/online');
mkdir(ROOT + '/sys/devices/system/cpu/cpu5/online');
r = cpu.set({ online: [0, 1, 2, 3, 4, 6, 7] });
check('failing offline step is reported and rolled back', !r.ok && r.rolled_back && online(3) == '1', r);

let d = cpu.defaults_request();
check('defaults: all cores, schedutil, full range', length(d.online) == 8 && d.clusters.little.governor == 'schedutil' && d.clusters.big.max_khz == 2210000, d);

printf('\n%d checks, %d failed\n', total, failed);
exit(failed ? 1 : 0);
