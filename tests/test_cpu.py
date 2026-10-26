"""mu300-cpu: CPU performance profiles against a fake /sys (MU300_SYSROOT), a fake /run and a config file.

A profile is a governor and a per-cluster scaling_min/max inside the hardware frequency table; the tool never
changes voltage and never touches the kernel thermal trips. These tests check the bounds (nothing is set outside
cpuinfo_min_freq..cpuinfo_max_freq), the governor fallback, the ceiling file thermal-guard reads, and the JSON."""
import json
import unittest

from helpers import ShellTest, BIN

CPU = BIN / 'mu300-cpu'


class CpuTest(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.run_dir = self.tmp / 'run'
        (self.run_dir / 'mu300').mkdir(parents=True)
        self.conf = self.tmp / 'cpu.conf'
        self.stub('logger', ':')
        self.cpufreq = self.root / 'sys/devices/system/cpu/cpufreq'

    # a cpufreq policy with a frequency table, governors, and the current min/max/governor
    def policy(self, name, cpus, freqs, govs='ondemand userspace powersave performance schedutil',
               gov='schedutil', cur=None):
        d = self.cpufreq / name
        d.mkdir(parents=True, exist_ok=True)
        fl = sorted(freqs)
        (d / 'cpuinfo_min_freq').write_text(f'{fl[0]}\n')
        (d / 'cpuinfo_max_freq').write_text(f'{fl[-1]}\n')
        (d / 'scaling_available_frequencies').write_text(' '.join(str(f) for f in fl) + ' \n')
        (d / 'scaling_available_governors').write_text(govs + ' \n')
        (d / 'scaling_min_freq').write_text(f'{fl[0]}\n')
        (d / 'scaling_max_freq').write_text(f'{fl[-1]}\n')
        (d / 'scaling_governor').write_text(gov + '\n')
        (d / 'scaling_cur_freq').write_text(f'{cur or fl[-1]}\n')
        (d / 'related_cpus').write_text(cpus + '\n')
        return d

    # the UMS9620's three clusters, as the F50-B board reports them
    def three_clusters(self):
        self.policy('policy0', '0 1 2 3', [614400, 768000, 936000, 1105000, 1228800, 1404000, 1560000, 1703000, 1846000, 2002000])
        self.policy('policy4', '4 5 6', [768000, 1157000, 1228800, 1326000, 1536000, 1703000, 1755000, 2054000, 2171000, 2301000])
        self.policy('policy7', '7', [614400, 768000, 936000, 1105000, 1228800, 1378000, 1536000, 1703000, 1872000, 2041000, 2210000])

    def cpu(self, shell, args, **env):
        return self.sh(shell, f'"{CPU}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_CPU_CONF=self.conf, **env)

    def read(self, policy, leaf):
        return (self.cpufreq / policy / leaf).read_text().strip()


class Profiles(CpuTest):
    def test_default_is_balanced(self):
        self.three_clusters()
        for shell in self.each_shell():
            self.assertEqual(self.cpu(shell, 'profile').stdout.strip(), 'balanced')

    def test_performance_pins_the_hardware_maximum_and_performance_governor(self):
        self.three_clusters()
        for shell in self.each_shell():
            r = self.cpu(shell, 'profile performance')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.read('policy0', 'scaling_max_freq'), '2002000')
            self.assertEqual(self.read('policy4', 'scaling_max_freq'), '2301000')
            self.assertEqual(self.read('policy0', 'scaling_governor'), 'performance')
            self.assertEqual(self.read('policy0', 'scaling_min_freq'), '614400')

    def test_saving_caps_near_sixty_percent_but_never_below_the_minimum(self):
        self.three_clusters()
        for shell in self.each_shell():
            self.cpu(shell, 'profile saving')
            # highest table step at or below 60 % of 2002000 (=1201200) is 1105000; of 2301000 (=1380600) is 1326000
            self.assertEqual(self.read('policy0', 'scaling_max_freq'), '1105000')
            self.assertEqual(self.read('policy4', 'scaling_max_freq'), '1326000')
            self.assertEqual(self.read('policy0', 'scaling_min_freq'), '614400')
            self.assertEqual(self.read('policy0', 'scaling_governor'), 'schedutil')

    def test_every_frequency_set_is_inside_the_hardware_table(self):
        self.three_clusters()
        for shell in self.each_shell():
            for prof in ('saving', 'balanced', 'performance'):
                self.cpu(shell, f'profile {prof}')
                for pol in ('policy0', 'policy4', 'policy7'):
                    lo = int(self.read(pol, 'cpuinfo_min_freq'))
                    hi = int(self.read(pol, 'cpuinfo_max_freq'))
                    mn = int(self.read(pol, 'scaling_min_freq'))
                    mx = int(self.read(pol, 'scaling_max_freq'))
                    self.assertTrue(lo <= mn <= mx <= hi, f'{prof} {pol}: {lo} {mn} {mx} {hi}')

    def test_unknown_profile_is_refused_and_nothing_changes(self):
        self.three_clusters()
        for shell in self.each_shell():
            r = self.cpu(shell, 'profile turbo')
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.read('policy0', 'scaling_max_freq'), '2002000')
            self.assertFalse(self.conf.exists())

    def test_governor_falls_back_when_the_wanted_one_is_absent(self):
        # a 5.4-style policy without schedutil: balanced must still set a real governor, not an empty string
        self.policy('policy0', '0 1 2 3', [614400, 1200000, 2002000], govs='ondemand userspace performance', gov='ondemand')
        for shell in self.each_shell():
            self.cpu(shell, 'profile balanced')
            self.assertIn(self.read('policy0', 'scaling_governor'), ('ondemand', 'userspace', 'performance'))
            self.assertEqual(self.read('policy0', 'scaling_max_freq'), '2002000')

    def test_apply_uses_the_saved_profile(self):
        self.three_clusters()
        self.conf.write_text('PROFILE=performance\n')
        for shell in self.each_shell():
            # reset to stock first
            for pol in ('policy0', 'policy4', 'policy7'):
                (self.cpufreq / pol / 'scaling_governor').write_text('schedutil\n')
            self.cpu(shell, 'apply')
            self.assertEqual(self.read('policy0', 'scaling_governor'), 'performance')

    def test_bad_saved_value_is_treated_as_balanced(self):
        self.three_clusters()
        self.conf.write_text('PROFILE=turbo\n')
        for shell in self.each_shell():
            self.assertEqual(self.cpu(shell, 'profile').stdout.strip(), 'balanced')
            r = self.cpu(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.read('policy0', 'scaling_max_freq'), '2002000')


class Ceiling(CpuTest):
    def test_saving_writes_a_ceiling_line_per_cluster(self):
        self.three_clusters()
        for shell in self.each_shell():
            self.cpu(shell, 'profile saving')
            lines = (self.run_dir / 'mu300/cpu-ceiling').read_text().splitlines()
            got = dict(l.split('=') for l in lines if '=' in l)
            self.assertEqual(got['policy0'], '1105000')
            self.assertEqual(got['policy4'], '1326000')
            # performance then raises the ceiling back to the hardware maximum
            self.cpu(shell, 'profile performance')
            lines = (self.run_dir / 'mu300/cpu-ceiling').read_text().splitlines()
            got = dict(l.split('=') for l in lines if '=' in l)
            self.assertEqual(got['policy0'], '2002000')


class NoCpufreq(CpuTest):
    def test_no_policies_is_not_an_error(self):
        # a kernel with no CPU scaling at all: apply must succeed quietly, change nothing
        for shell in self.each_shell():
            r = self.cpu(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.cpu(shell, 'profile performance')
            self.assertEqual(r.returncode, 0, r.stderr)


class Json(CpuTest):
    def test_status_json_is_valid_and_lists_the_clusters(self):
        self.three_clusters()
        for shell in self.each_shell():
            self.cpu(shell, 'profile balanced')
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(st['ok'], 1)
            self.assertEqual(st['profile'], 'balanced')
            self.assertEqual(len(st['policies']), 3)
            p0 = [p for p in st['policies'] if p['name'] == 'policy0'][0]
            self.assertEqual(p0['hwmax'], 2002)   # kHz -> MHz
            self.assertEqual(p0['cpus'], '0-3')
            self.assertIn(st['throttling'], (0, 1))

    def thermal(self):
        """the kernel's zones as mainline with mu300_thermal shows them: two physical zones without trips, the stock
        soc-thmzone with its three trips and the cpufreq cooling devices bound (cdevK -> ../cooling_deviceM)"""
        th = self.root / 'sys/class/thermal'
        for i, (kind, temp) in enumerate((('lit0-thmzone', 51200), ('big7-thmzone', 56800))):
            z = th / f'thermal_zone{i}'
            z.mkdir(parents=True)
            (z / 'type').write_text(kind + '\n')
            (z / 'temp').write_text(f'{temp}\n')
            (z / 'mode').write_text('enabled\n')
        for i, cpu in enumerate((0, 4, 7)):
            c = th / f'cooling_device{i}'
            c.mkdir()
            (c / 'type').write_text(f'cpufreq-cpu{cpu}\n')
            (c / 'cur_state').write_text(f'{2 if cpu == 7 else 0}\n')
            (c / 'max_state').write_text('9\n')
        z = th / 'thermal_zone2'
        z.mkdir()
        (z / 'type').write_text('soc-thmzone\n')
        (z / 'temp').write_text('56800\n')
        (z / 'mode').write_text('enabled\n')
        for i, (tt, tv) in enumerate((('passive', 70000), ('passive', 85000), ('critical', 110000))):
            (z / f'trip_point_{i}_type').write_text(tt + '\n')
            (z / f'trip_point_{i}_temp').write_text(f'{tv}\n')
        for i in range(3):
            (z / f'cdev{i}').symlink_to(f'../cooling_device{i}')

    def test_status_json_lists_the_thermal_zones_read_only(self):
        # the panel's read-only Thermal section: every zone with its temperature (mC), the trips and the cooling
        # devices bound to it; the symlinks and trip files are read, nothing is written
        self.three_clusters()
        self.thermal()
        for shell in self.each_shell():
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            zones = {z['zone']: z for z in st['thermal']}
            self.assertEqual(sorted(zones), ['big7-thmzone', 'lit0-thmzone', 'soc-thmzone'])
            self.assertEqual(zones['lit0-thmzone'], {'zone': 'lit0-thmzone', 'temp': 51200, 'mode': 'enabled',
                                                     'trips': [], 'cooling': []})
            soc = zones['soc-thmzone']
            self.assertEqual(soc['trips'], [{'type': 'passive', 'temp': 70000}, {'type': 'passive', 'temp': 85000},
                                            {'type': 'critical', 'temp': 110000}])
            self.assertEqual(soc['cooling'], [{'type': 'cpufreq-cpu0', 'state': 0, 'max': 9},
                                              {'type': 'cpufreq-cpu4', 'state': 0, 'max': 9},
                                              {'type': 'cpufreq-cpu7', 'state': 2, 'max': 9}])
            self.assertEqual(st['throttling'], 1)     # a cooling device above state 0

    def test_status_json_without_thermal_zones(self):
        self.three_clusters()
        for shell in self.each_shell():
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(st['thermal'], [])

    def test_throttling_reflects_the_alarm_flag(self):
        self.three_clusters()
        (self.run_dir / 'mu300/thermal-alarm').write_text('')
        for shell in self.each_shell():
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(st['throttling'], 1)



class CoresAndLatency(CpuTest):
    """FINDINGS 200 (after kanoqwq's cpu-cores and mu300-lowlatency): a core taken offline is saved and taken offline
    again by apply; CPU0 never; CPU0's idle states off only when asked (low latency is off by default)."""

    def cores(self, n=8):
        base = self.root / 'sys/devices/system/cpu'
        for c in range(n):
            d = base / f'cpu{c}'
            d.mkdir(parents=True, exist_ok=True)
            if c:
                (d / 'online').write_text('1\n')
        for st in range(3):
            d = base / f'cpu0/cpuidle/state{st}'
            d.mkdir(parents=True, exist_ok=True)
            (d / 'disable').write_text('0\n')
        return base

    def test_a_core_offline_is_saved_and_replayed_and_cpu0_is_refused(self):
        for shell in self.each_shell():
            self.conf.unlink(missing_ok=True)
            base = self.cores()
            self.three_clusters()
            r = self.cpu(shell, 'core 7 off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((base / 'cpu7/online').read_text().strip(), '0')
            self.assertIn('CORES_OFF=7', self.conf.read_text())
            self.cpu(shell, 'core 5 off')
            self.assertIn('CORES_OFF=7,5', self.conf.read_text())
            self.assertNotEqual(self.cpu(shell, 'core 0 off').returncode, 0)
            self.assertNotEqual(self.cpu(shell, 'core 9 off').returncode, 0)     # no such CPU
            self.assertIn('CORES_OFF=7,5', self.conf.read_text())
            # a boot: both online again, apply takes them offline
            (base / 'cpu7/online').write_text('1\n')
            (base / 'cpu5/online').write_text('1\n')
            self.assertEqual(self.cpu(shell, 'apply').returncode, 0)
            self.assertEqual(((base / 'cpu7/online').read_text().strip(), (base / 'cpu5/online').read_text().strip()),
                             ('0', '0'))
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual([c['online'] for c in st['cores']], [1, 1, 1, 1, 1, 0, 1, 0])
            self.assertEqual([c['switchable'] for c in st['cores']], [0, 1, 1, 1, 1, 1, 1, 1])
            self.assertEqual(st['cores_off'], '7,5')
            self.cpu(shell, 'core 7 on')
            self.cpu(shell, 'core 5 on')
            self.assertEqual((base / 'cpu7/online').read_text().strip(), '1')
            self.assertNotIn('CORES_OFF', self.conf.read_text())

    def test_a_broken_saved_list_takes_nothing_offline(self):
        for shell in self.each_shell():
            base = self.cores()
            self.conf.write_text('CORES_OFF=0,7\n')
            self.cpu(shell, 'apply')
            self.assertEqual((base / 'cpu7/online').read_text().strip(), '1')

    def test_low_latency_is_off_by_default_and_only_on_when_asked(self):
        for shell in self.each_shell():
            self.conf.unlink(missing_ok=True)
            base = self.cores()
            nodes = [base / f'cpu0/cpuidle/state{s}/disable' for s in range(3)]
            self.assertEqual(self.cpu(shell, 'lowlatency').stdout.strip(), 'off')
            self.cpu(shell, 'apply')
            self.assertEqual({n.read_text().strip() for n in nodes}, {'0'})
            self.assertEqual(self.cpu(shell, 'lowlatency on').returncode, 0)
            self.assertEqual({n.read_text().strip() for n in nodes}, {'1'})
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(st['lowlatency'], {'saved': 1, 'active': 1, 'supported': 1})
            for n in nodes:
                n.write_text('0\n')                     # a boot
            self.cpu(shell, 'apply')
            self.assertEqual({n.read_text().strip() for n in nodes}, {'1'})
            self.cpu(shell, 'lowlatency off')
            self.assertEqual({n.read_text().strip() for n in nodes}, {'0'})
            self.assertEqual(self.cpu(shell, 'lowlatency').stdout.strip(), 'off')
            self.assertNotEqual(self.cpu(shell, 'lowlatency maybe').returncode, 0)

    def test_low_latency_without_cpuidle_is_refused(self):
        for shell in self.each_shell():
            (self.root / 'sys/devices/system/cpu').mkdir(parents=True, exist_ok=True)
            self.assertNotEqual(self.cpu(shell, 'lowlatency on').returncode, 0)
            st = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(st['lowlatency']['supported'], 0)

if __name__ == '__main__':
    unittest.main()
