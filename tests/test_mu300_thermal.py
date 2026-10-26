"""mu300_thermal's trend for step_wise (upstream/modules/mu300_thermal/mu300_trend.h, FINDINGS 166), compiled on the
host: the slope over about a second instead of reading against reading, a dead band, the cooling up fast and down at
most a step a second in the throttling band. Plus a small plant: step_wise as 6.18/7.2 run it (one step per 100 ms
poll), a die that follows the CPU power within a poll and creeps up slowly, and sensor noise of +-1.5 C - with the
core's own trend the cooling never gets past a few steps and the die passes 95 C, with this one it stays under 90 C on average and under 95 C at its peak."""
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

HEADER = TOP / 'upstream/modules/mu300_thermal/mu300_trend.h'
HZ = 250

# commands on stdin, one per line: "r WHEN TEMP" records a reading; "t TRIP HYST WHEN DROP_EVERY" prints the trend
# (stable/raising/dropping, or nodata without a slope)
HARNESS = r'''
#include <stdio.h>
#include "mu300_trend.h"
int main(void)
{
	static struct mu300_history h;
	char c;
	unsigned long when, every;
	int temp, trip, hyst, now;
	long slope;

	while (scanf(" %c", &c) == 1) {
		if (c == 'r' && scanf("%lu %d", &when, &temp) == 2) {
			mu300_record(&h, when, temp);
		} else if (c == 't' && scanf("%d %d %lu %lu", &trip, &hyst, &when, &every) == 4) {
			if (mu300_slope(&h, HZ, HZ, HZ / 2, &now, &slope))
				puts("nodata");
			else
				switch (mu300_trend(&h, now, slope, trip, hyst, when, every)) {
				case MU300_RAISING: puts("raising"); break;
				case MU300_DROPPING: puts("dropping"); break;
				default: puts("stable");
				}
			fflush(stdout);
		}
	}
	return 0;
}
'''


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
class Trend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        (cls.dir / 'h.c').write_text(HARNESS)
        cls.exe = cls.dir / 'h'
        subprocess.run(['cc', '-std=c99', '-Wall', '-Werror', f'-DHZ={HZ}', f'-I{HEADER.parent}', '-o', str(cls.exe),
                        str(cls.dir / 'h.c')], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_cmds(self, cmds):
        r = subprocess.run([str(self.exe)], input='\n'.join(cmds) + '\n', capture_output=True, text=True, check=True)
        return r.stdout.split()

    def readings(self, temps, step=HZ // 10, start=1000):
        """one reading per 100 ms poll; returns the commands and the time of the last one"""
        cmds = [f'r {start + i * step} {t}' for i, t in enumerate(temps)]
        return cmds, start + (len(temps) - 1) * step

    def trend(self, temps, trip=85000, hyst=1000, every=HZ):
        cmds, when = self.readings(temps)
        return self.run_cmds(cmds + [f't {trip} {hyst} {when} {every}'])[-1]

    def test_no_slope_without_half_a_second_of_history(self):
        self.assertEqual(self.trend([90000]), 'nodata')
        self.assertEqual(self.trend([90000, 91000, 92000]), 'nodata')                    # 200 ms
        self.assertEqual(self.trend([84000 + i * 1000 for i in range(8)]), 'raising')    # 700 ms

    def test_the_noise_of_one_reading_against_the_last_is_no_trend(self):
        # 86 C flat with +-1 C between polls: the core's own trend said "dropping" on every second poll
        flat = [86000 + (1000 if i % 2 else -1000) for i in range(16)]
        flat[-1] = 85000                                                      # the last poll lower than the one before
        self.assertEqual(self.trend(flat), 'stable')

    def test_above_trip_plus_margin_the_cooling_only_goes_up(self):
        self.assertEqual(self.trend([88000] * 16), 'raising')                # flat at 88 C: one step more per poll
        self.assertEqual(self.trend([90000 - i * 20 for i in range(16)]), 'raising')     # a slow fall: 0.2 C/s
        self.assertEqual(self.trend([95000 - i * 200 for i in range(16)]), 'stable')     # 2 C/s down: hold

    def test_in_the_band_the_slope_decides_and_steps_back_are_rate_limited(self):
        self.assertEqual(self.trend([85000 + i * 150 for i in range(16)]), 'raising')    # +1.5 C/s at 87 C
        self.assertEqual(self.trend([86000] * 16), 'stable')
        falling = [89000 - i * 150 for i in range(26)]                                   # -1.5 C/s, 89 to 85.25 C
        cmds = [f'r {1000 + i * 25} {t}' for i, t in enumerate(falling)]
        # asked after the readings of 86.75, 86.6 (a poll later) and 85.25 C (a second after the first)
        out =self.run_cmds(cmds[:16] + [f't 85000 1000 {1000 + 15 * 25} {HZ}'] + cmds[16:17] +
                            [f't 85000 1000 {1000 + 16 * 25} {HZ}'] + cmds[17:] +
                            [f't 85000 1000 {1000 + 25 * 25} {HZ}'])
        self.assertEqual(out, ['dropping', 'stable', 'dropping'])           # not again before a second has passed
        # under the trip but still throttling (trip less hysteresis): a step back a second, without a falling slope
        self.assertEqual(self.trend([84500] * 16), 'dropping')

    def test_below_the_band_the_cooling_is_held_until_well_under_the_trip(self):
        # step_wise lifts every step at once on "dropping" here: under a full load the die is 10-20 C hotter a
        # second later
        self.assertEqual(self.trend([82000] * 16), 'stable')
        self.assertEqual(self.trend([83500 - i * 150 for i in range(16)]), 'stable')
        self.assertEqual(self.trend([79000] * 16), 'dropping')

    def test_the_lower_trip_does_not_use_up_the_rate_limit_of_the_upper_one(self):
        # step_wise asks for every passive trip in turn (70 C, then 85 C): at 86 C the 70 C trip is far below
        cmds, when = self.readings([89000 - i * 150 for i in range(16)])
        out = self.run_cmds(cmds + [f't 70000 1000 {when} {HZ}', f't 85000 1000 {when} {HZ}'])
        self.assertEqual(out, ['stable', 'dropping'])


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
class Plant(unittest.TestCase):
    """step_wise against a crude F50 under 8 busy loops (6.18.56: 38 -> 98 C in three minutes with the core's trend)"""
    STATES = 9

    @classmethod
    def setUpClass(cls):
        Trend.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def simulate(self, own_trend, seconds=600, seed=1):
        rng = random.Random(seed)
        proc = subprocess.Popen([str(self.exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        state, case, die, last, peak, tail = 0, 45.0, 73.0, None, 0.0, []
        trip, hyst, crossed = 85000, 1000, False
        for poll in range(seconds * 10):
            when = 1000 + poll * HZ // 10
            power = 1.0 - 0.07 * state                                       # each step about 7 % less power
            case += (power * 0.12 - (case - 45.0) * 0.002) / 10              # the package creeps up, slowly
            die += (case + 28.0 * power - die) * 0.2                         # the die follows the power in ~0.5 s
            temp = int((die + rng.uniform(-1.5, 1.5)) * 1000)
            proc.stdin.write(f'r {when} {temp}\nt {trip} {hyst} {when} {HZ}\n')
            proc.stdin.flush()
            mine = proc.stdout.readline().strip()
            if own_trend or mine == 'nodata':
                trend = 'stable' if last is None or temp == last else ('raising' if temp > last else 'dropping')
            else:
                trend = mine
            last = temp
            threshold = trip - hyst if crossed else trip
            throttle = temp >= threshold
            crossed = throttle
            # gov_step_wise.c get_target_state (6.18.56 and 7.2.10 alike)
            if throttle:
                if trend == 'raising':
                    state = min(state + 1, self.STATES)
                elif trend == 'dropping':
                    state = max(state - 1, 1)
            elif trend == 'dropping':
                state = 0
            peak = max(peak, temp / 1000)
            if poll >= (seconds - 300) * 10:
                tail.append(temp / 1000)
        proc.stdin.close()
        proc.wait()
        return peak, sum(tail) / len(tail)

    def test_the_cores_trend_lets_it_run_away_and_this_one_holds_it(self):
        peak, _ = self.simulate(own_trend=True)
        self.assertGreater(peak, 95)
        for seed in (1, 2, 3):
            peak, avg = self.simulate(own_trend=False, seed=seed)
            self.assertLess(peak, 95, seed)
            self.assertLess(avg, 90, seed)


if __name__ == '__main__':
    unittest.main()
