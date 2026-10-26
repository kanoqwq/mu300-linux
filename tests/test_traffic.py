"""mu300-traffic: the mobile data count (the panel's Data usage page). A fake / with sipa_eth0's counters, the boot
id and the uptime; the clock and the UTC offset are fixed per call (MU300_TRAFFIC_NOW, MU300_TRAFFIC_TZOFF). Every
byte counted once: the first reading is a base, a reboot or a re-created interface counts its new counters, a
counter that went down starts over; days split at local midnight by time, measured with the uptime (a clock jump
does not stretch an interval); bytes wait for NTP and are dated by their uptime; the billing cycle, the cap, its
warning and the cut; the service's checkpoints and its last save at a stop."""
import json
import os
import signal
import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone

from helpers import BIN, ShellTest

TOOL = BIN / 'mu300-traffic'
TZ3 = timezone(timedelta(hours=3))


def at(y, m, d, hh=0, mm=0, ss=0, off=3):
    """the epoch of a local time at UTC+off"""
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone(timedelta(hours=off))).timestamp())


class TrafficBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'mnt/mu300-disk/.mu300').mkdir(parents=True)
        (self.root / 'run/mu300').mkdir(parents=True)
        self.store = self.root / 'mnt/mu300-disk/.mu300/traffic'
        self.stub('logger', 'shift 2; echo "$*" >> "$STUBLOG/log"')
        for c in ('ifdown', 'ifup', 'systemctl'):
            self.stub(c, f'echo "{c} $*" >> "$STUBLOG/calls"')
        self.synced(True)
        self.now = at(2026, 10, 10, 12)
        self.off = 10800

    def fresh(self):
        """a new device: nothing counted, no live ledger, no settings"""
        for p in (self.store, self.root / 'run/mu300/traffic'):
            subprocess.run(['rm', '-rf', str(p)])
        for f in ('log', 'calls'):
            (self.tmp / f).unlink(missing_ok=True)

    def synced(self, yes):
        f = self.root / 'run/mu300/clock-synced'
        if yes:
            f.write_text('')
        else:
            f.unlink(missing_ok=True)

    def counters(self, rx, tx, up, boot='aaaa-1', idx=7, dev='sipa_eth0'):
        (self.root / 'proc/sys/kernel/random/boot_id').write_text(boot + '\n')
        (self.root / 'proc/uptime').write_text(f'{up}.42 100.00\n')
        d = self.root / 'sys/class/net' / dev
        (d / 'statistics').mkdir(parents=True, exist_ok=True)
        (d / 'ifindex').write_text(f'{idx}\n')
        (d / 'statistics/rx_bytes').write_text(f'{rx}\n')
        (d / 'statistics/tx_bytes').write_text(f'{tx}\n')

    def no_interface(self, dev='sipa_eth0'):
        subprocess.run(['rm', '-rf', str(self.root / 'sys/class/net' / dev)])

    def run_tool(self, shell, *args, now=None, off=None, ok=True, **env):
        r = self.script(shell, TOOL, *args, MU300_SYSROOT=self.root, MU300_TRAFFIC_NOW=now or self.now,
                        MU300_TRAFFIC_TZOFF=self.off if off is None else off, **env)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def sample(self, shell, rx, tx, up, now, **kw):
        """the counters are RX/TX at UP (uptime) and NOW; one reading taken and saved"""
        off = kw.pop('off', None)
        self.counters(rx, tx, up, **kw)
        self.run_tool(shell, 'save', now=now, off=off)

    def view(self, shell, now=None, off=None):
        return json.loads(self.run_tool(shell, 'json', now=now, off=off).stdout)

    def days(self, shell, now=None, off=None):
        return {d['date']: (d['rx'], d['tx']) for d in self.view(shell, now, off)['days']}

    def ledger(self):
        return (self.store / 'ledger').read_text()

    def set(self, shell, key, value):
        self.run_tool(shell, 'set', key, value)


class Counting(TrafficBase):
    def test_first_reading_is_a_base_and_later_ones_count(self):
        for shell in self.each_shell():
            self.fresh()
            # 5 GB already through the interface before counting started: not put on any day
            self.sample(shell, 5_000_000_000, 1_000_000, 100, self.now)
            self.assertEqual(self.days(shell), {})
            self.sample(shell, 5_000_300_000, 1_000_020, 160, self.now + 60)
            self.assertEqual(self.days(shell, self.now + 60), {'2026-10-10': (300_000, 20)})
            v = self.view(shell, self.now + 60)
            self.assertEqual((v['today']['rx'], v['today']['tx'], v['cycle']['used']), (300_000, 20, 300_020))

    def test_a_view_adds_what_came_since_the_last_reading_without_writing(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 0, 0, 100, self.now)
            before = self.ledger()
            self.counters(4096, 1024, 130)
            self.assertEqual(self.days(shell, self.now + 30), {'2026-10-10': (4096, 1024)})
            self.assertEqual(self.ledger(), before)

    def test_a_reboot_counts_the_new_counters_once(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 1000, 100, 100, self.now)
            self.sample(shell, 9000, 900, 160, self.now + 60)      # 8000/800 in the first boot
            # a reboot: the live ledger is gone with /run, the disk copy is that of the last save
            subprocess.run(['rm', '-rf', str(self.root / 'run/mu300/traffic')])
            self.sample(shell, 500, 50, 40, self.now + 600, boot='bbbb-2', idx=9)
            self.sample(shell, 700, 70, 100, self.now + 660, boot='bbbb-2', idx=9)
            self.assertEqual(self.days(shell, self.now + 660), {'2026-10-10': (8000 + 700, 800 + 70)})

    def test_a_recreated_interface_or_a_lower_counter_starts_over(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 10_000, 1000, 100, self.now)
            self.sample(shell, 300, 30, 160, self.now + 60, idx=12)    # re-created: a new ifindex, new counters
            self.sample(shell, 50, 40, 220, self.now + 120, idx=12)    # lower rx: reset; tx went on (+10)
            self.assertEqual(self.days(shell, self.now + 120), {'2026-10-10': (350, 40)})

    def test_another_interface_is_only_a_base(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 100, 10, 100, self.now)
            self.sample(shell, 200, 20, 160, self.now + 60)
            self.set(shell, 'device', 'wwan0')
            self.sample(shell, 7_000_000, 7_000, 220, self.now + 120, dev='wwan0')    # its history: not counted
            self.sample(shell, 7_000_100, 7_010, 280, self.now + 180, dev='wwan0')
            self.assertEqual(self.days(shell, self.now + 180), {'2026-10-10': (200, 20)})

    def test_no_interface_counts_nothing_and_keeps_the_base(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 100, 10, 100, self.now)
            self.no_interface()
            self.run_tool(shell, 'save', now=self.now + 60)
            self.assertIn('base aaaa-1 7 sipa_eth0 100 10 100', self.ledger())
            v = self.view(shell, self.now + 60)
            self.assertEqual(v['available'], 0)
            self.sample(shell, 160, 20, 160, self.now + 120)
            self.assertEqual(self.days(shell, self.now + 120), {'2026-10-10': (60, 10)})


class Days(TrafficBase):
    def test_an_interval_over_midnight_is_split_by_time(self):
        for shell in self.each_shell():
            self.fresh()
            mid = at(2026, 10, 11)
            self.sample(shell, 0, 0, 1000, mid - 30)
            self.sample(shell, 6001, 3001, 1090, mid + 60)      # 90 s: 30 before midnight, 60 after
            d = self.days(shell, mid + 60)
            self.assertEqual(d, {'2026-10-10': (2000, 1000), '2026-10-11': (4001, 2001)})

    def test_midnight_is_local(self):
        for shell in self.each_shell():
            self.fresh()
            t = at(2026, 10, 11, 1, 0, 0)      # 01:00 at +3 is 22:00 UTC the day before
            self.sample(shell, 0, 0, 100, t - 60)
            self.sample(shell, 100, 0, 160, t)
            self.assertEqual(self.days(shell, t), {'2026-10-11': (100, 0)})
            self.fresh()
            self.sample(shell, 0, 0, 100, t - 60, off=0)
            self.sample(shell, 100, 0, 160, t, off=0)
            self.assertEqual(self.days(shell, t, off=0), {'2026-10-10': (100, 0)})

    def test_a_clock_jump_does_not_stretch_the_interval(self):
        for shell in self.each_shell():
            self.fresh()
            # NTP steps the clock forward two days between two readings 60 s of uptime apart: the 60 s end now
            t = at(2026, 10, 10, 23, 0)
            self.sample(shell, 0, 0, 1000, t)
            self.sample(shell, 6000, 0, 1060, t + 2 * 86400)
            self.assertEqual(self.days(shell, t + 2 * 86400), {'2026-10-12': (6000, 0)})
            # and back by a day: still the last 60 s, on the day the clock says now
            self.sample(shell, 6600, 0, 1120, t + 86400 + 60)
            self.assertEqual(self.days(shell, t + 2 * 86400), {'2026-10-11': (600, 0), '2026-10-12': (6000, 0)})

    def test_a_dst_change_keeps_every_byte_on_its_local_day(self):
        for shell in self.each_shell():
            self.fresh()
            # 2026-10-25 03:00 +03 back to 02:00 +02 (a European-style change, offsets given as the clock has them)
            t = at(2026, 10, 25, 2, 59, 0, off=3)
            self.sample(shell, 0, 0, 1000, t, off=10800)
            self.sample(shell, 1000, 0, 1120, t + 120, off=7200)
            mid = at(2026, 10, 26, 0, 0, 0, off=2)
            self.sample(shell, 1500, 0, 1120 + (mid - t - 120) + 60, mid + 60, off=7200)
            d = self.days(shell, mid + 60, off=7200)
            self.assertEqual(sum(rx for rx, _ in d.values()), 1500)
            self.assertEqual(sorted(d), ['2026-10-25', '2026-10-26'])

    def test_pending_bytes_wait_for_ntp_and_are_dated_by_uptime(self):
        for shell in self.each_shell():
            self.fresh()
            self.synced(False)
            # the clock says 2025 after the boot (sysfixtime); real time is 23:50 on 2026-10-10
            wrong = at(2025, 1, 1)
            self.sample(shell, 0, 0, 30, wrong)
            self.sample(shell, 4000, 400, 90, wrong + 60)      # uptime 30..90: 23:50:30..23:51:30 real
            v = self.view(shell, wrong + 60)
            self.assertEqual((v['clock'], v['days'], v['pending']), (0, [], {'rx': 4000, 'tx': 400}))
            self.assertEqual(v['cycle']['used'], 4400)        # counted at once towards the cycle and the cap
            # NTP at uptime 1290 (20 min later): 00:10:30 real
            self.synced(True)
            real = at(2026, 10, 10, 23, 50) + 1260
            self.sample(shell, 4500, 450, 1290, real)
            d = self.days(shell, real)
            self.assertEqual(sum(rx for rx, _ in d.values()), 4500)
            self.assertEqual(d['2026-10-10'][0] + d.get('2026-10-11', (0, 0))[0], 4500)
            self.assertNotIn('2025-01-01', d)
            self.assertEqual(self.view(shell, real)['pending'], {'rx': 0, 'tx': 0})
            self.assertNotIn('\np ', self.ledger())

    def test_pending_bytes_of_an_earlier_boot_go_to_the_day_ntp_comes(self):
        for shell in self.each_shell():
            self.fresh()
            self.synced(False)
            self.sample(shell, 0, 0, 30, at(2025, 1, 1))
            self.sample(shell, 900, 0, 90, at(2025, 1, 1) + 60)
            subprocess.run(['rm', '-rf', str(self.root / 'run/mu300/traffic')])
            self.synced(True)
            self.sample(shell, 100, 0, 50, self.now, boot='bbbb-2', idx=8)
            self.assertEqual(self.days(shell), {'2026-10-10': (1000, 0)})

    def test_six_hours_without_ntp_trusts_the_clock(self):
        for shell in self.each_shell():
            self.fresh()
            self.synced(False)
            self.sample(shell, 0, 0, 21000, self.now)
            self.sample(shell, 10, 0, 21700, self.now + 700)
            self.assertEqual(self.days(shell, self.now + 700), {'2026-10-10': (10, 0)})

    def test_days_are_kept_for_800_days(self):
        for shell in self.each_shell():
            self.fresh()
            old = ''.join(f'd 2024-0{m}-01 {m} 0\n' for m in range(1, 8))
            self.store.mkdir(parents=True)
            (self.store / 'ledger').write_text('mu300-traffic 1\nbase aaaa-1 7 sipa_eth0 0 0 100\n' + old)
            self.sample(shell, 1, 0, 160, self.now)     # 2026-10-10: 800 days back is 2024-08-01
            self.assertNotIn('2024-07-01', self.ledger())
            self.assertNotIn('d 2024-01-01', self.ledger())

    def test_a_damaged_ledger_line_is_dropped(self):
        for shell in self.each_shell():
            self.fresh()
            self.store.mkdir(parents=True)
            (self.store / 'ledger').write_text('mu300-traffic 1\nbase aaaa-1 7 sipa_eth0 0 0 100\n'
                                               'd 2026-10-09 12 x\nd 2026-10-08 5 5\ngarbage\n')
            self.sample(shell, 1, 0, 160, self.now)
            self.assertEqual(self.days(shell), {'2026-10-08': (5, 5), '2026-10-10': (1, 0)})


class Cycle(TrafficBase):
    def cyc(self, shell, now, reset):
        self.set(shell, 'reset-day', str(reset))
        c = self.view(shell, now)['cycle']
        return c['start'], c['last'], c['days_left']

    def test_reset_day(self):
        for shell in self.each_shell():
            self.fresh()
            self.counters(0, 0, 100)
            self.assertEqual(self.cyc(shell, at(2026, 10, 10, 12), 1), ('2026-10-01', '2026-10-31', 22))
            self.assertEqual(self.cyc(shell, at(2026, 10, 10, 12), 15), ('2026-09-15', '2026-10-14', 5))
            self.assertEqual(self.cyc(shell, at(2026, 10, 15, 0, 0, 1), 15), ('2026-10-15', '2026-11-14', 31))
            # 31: the month's last day
            self.assertEqual(self.cyc(shell, at(2027, 2, 28, 9), 31), ('2027-02-28', '2027-03-30', 31))
            self.assertEqual(self.cyc(shell, at(2027, 2, 27, 9), 31), ('2027-01-31', '2027-02-27', 1))
            self.assertEqual(self.cyc(shell, at(2028, 2, 29, 9), 30), ('2028-02-29', '2028-03-29', 30))
            self.assertEqual(self.cyc(shell, at(2026, 12, 31, 23, 59), 1), ('2026-12-01', '2026-12-31', 1))

    def test_cycles_sum_their_days(self):
        for shell in self.each_shell():
            self.fresh()
            self.store.mkdir(parents=True)
            (self.store / 'ledger').write_text('mu300-traffic 1\nbase aaaa-1 7 sipa_eth0 0 0 100\n'
                                               'd 2026-08-20 1 0\nd 2026-09-14 2 0\nd 2026-09-15 4 0\n'
                                               'd 2026-10-09 8 0\n')
            self.counters(0, 0, 100)
            self.set(shell, 'reset-day', '15')
            cyc = self.view(shell)['cycles']
            self.assertEqual([(c['start'], c['last'], c['rx']) for c in cyc],
                             [('2026-09-15', '2026-10-14', 12), ('2026-08-15', '2026-09-14', 3)])
            text = self.run_tool(shell, 'cycles').stdout
            self.assertIn('2026-09-15 - 2026-10-14', text)

    def test_used_corrects_this_cycle_only(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 0, 0, 100, self.now)
            self.sample(shell, 1 << 30, 0, 160, self.now + 60)
            self.run_tool(shell, 'used', '10G', now=self.now + 60)
            v = self.view(shell, self.now + 60)
            self.assertEqual(v['cycle']['used'], 10 << 30)
            self.assertEqual(v['cycle']['adj'], 9 << 30)
            self.sample(shell, (1 << 30) + 100, 0, 220, self.now + 120)
            self.assertEqual(self.view(shell, self.now + 120)['cycle']['used'], (10 << 30) + 100)
            self.assertEqual(self.view(shell, at(2026, 11, 1, 12))['cycle']['used'], 0)    # next cycle

    def test_clear_forgets_and_keeps_counting(self):
        for shell in self.each_shell():
            self.fresh()
            self.sample(shell, 0, 0, 100, self.now)
            self.sample(shell, 500, 0, 160, self.now + 60)
            self.assertNotEqual(self.run_tool(shell, 'clear', ok=False).returncode, 0)    # needs --yes
            self.counters(800, 0, 220)
            self.run_tool(shell, 'clear', '--yes', now=self.now + 120)
            self.assertEqual(self.days(shell, self.now + 120), {})
            self.sample(shell, 850, 0, 280, self.now + 180)
            self.assertEqual(self.days(shell, self.now + 180), {'2026-10-10': (50, 0)})

    def test_settings_are_checked(self):
        for shell in self.each_shell():
            self.fresh()
            for key, value in (('reset-day', '0'), ('reset-day', '32'), ('cap', '-1'), ('cap', '5X'),
                               ('warn', '101'), ('cut', 'yes'), ('device', '../x'), ('nope', '1')):
                self.assertNotEqual(self.run_tool(shell, 'set', key, value, ok=False).returncode, 0, (key, value))
            self.set(shell, 'cap', '1.5G')
            self.assertIn('CAP=1610612736\n', (self.store / 'conf').read_text())
            # a file edited by hand with nonsense: the defaults
            (self.store / 'conf').write_text('RESET_DAY=$(reboot)\nCAP=12abc\nWARN=0\n')
            self.counters(0, 0, 100)
            v = self.view(shell)
            self.assertEqual((v['reset_day'], v['cap']['bytes'], v['cap']['warn']), (1, 0, 90))


class Cap(TrafficBase):
    def setUp(self):
        super().setUp()
        (self.root / 'etc').mkdir()
        (self.root / 'etc/openwrt_release').write_text("DISTRIB_ID='OpenWrt'\n")

    def calls(self):
        p = self.tmp / 'calls'
        time.sleep(0.2)     # the cut runs in the background
        return p.read_text().splitlines() if p.exists() else []

    def log(self):
        p = self.tmp / 'log'
        return p.read_text() if p.exists() else ''

    def round(self, shell, rx, up, now=None, **kw):
        """one round of the service (run's round, through `save`, which acts the same way)"""
        self.counters(rx, 0, up, **kw)
        return self.run_tool(shell, 'save', now=now or self.now + up)

    def test_warning_and_cut_once(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-mobile-data-down').unlink(missing_ok=True)
            self.set(shell, 'cap', '1000')
            self.set(shell, 'warn', '80')
            self.set(shell, 'cut', 'on')
            self.round(shell, 0, 100)
            self.round(shell, 850, 160)
            self.assertEqual(self.view(shell, self.now + 160)['cap']['level'], 'warn')
            self.assertIn('80% of the monthly cap', self.log())
            self.assertEqual(self.calls(), [])
            self.round(shell, 1000, 220)
            v = self.view(shell, self.now + 220)
            self.assertEqual((v['cap']['level'], v['cap']['cut_active']), ('over', 1))
            self.assertEqual(self.calls(), ['ifdown wan'])
            self.assertIn('monthly cap is reached', self.log())
            # mobile-data down marks it; the next rounds do nothing more
            (self.root / 'run/mu300-mobile-data-down').write_text('')
            self.round(shell, 1010, 280)
            self.assertEqual(self.calls(), ['ifdown wan'])
            self.assertEqual(self.log().count('the monthly cap is reached ('), 1)

    def test_turned_back_on_by_hand_it_stays_on_until_a_reboot(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-mobile-data-down').unlink(missing_ok=True)
            self.set(shell, 'cap', '1000')
            self.set(shell, 'cut', 'on')
            self.round(shell, 0, 100)
            self.round(shell, 2000, 160)
            self.assertEqual(self.calls(), ['ifdown wan'])
            (self.root / 'run/mu300-mobile-data-down').write_text('')
            self.round(shell, 2000, 220)
            (self.root / 'run/mu300-mobile-data-down').unlink()     # the user: data up
            self.round(shell, 2100, 280)
            self.round(shell, 2200, 340)
            self.assertEqual(self.calls(), ['ifdown wan'])
            self.assertEqual(self.view(shell, self.now + 340)['cap']['cut_active'], 0)

    def test_a_reboot_cuts_again_and_the_next_cycle_restores(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-mobile-data-down').unlink(missing_ok=True)
            self.set(shell, 'cap', '1000')
            self.set(shell, 'cut', 'on')
            self.round(shell, 0, 100)
            self.round(shell, 2000, 160)
            subprocess.run(['rm', '-rf', str(self.root / 'run/mu300/traffic')])
            self.round(shell, 10, 30, now=self.now + 600, boot='bbbb-2', idx=9)    # netifd dialled at boot
            self.assertEqual(self.calls(), ['ifdown wan', 'ifdown wan'])
            (self.root / 'run/mu300-mobile-data-down').write_text('')
            self.round(shell, 10, 90, now=at(2026, 11, 1, 0, 1), boot='bbbb-2', idx=9)
            self.assertEqual(self.calls(), ['ifdown wan', 'ifdown wan', 'ifup wan'])
            self.assertIn('back on', self.log())

    def test_raising_the_cap_or_cut_off_restores(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-mobile-data-down').unlink(missing_ok=True)
            self.set(shell, 'cap', '1000')
            self.set(shell, 'cut', 'on')
            self.round(shell, 0, 100)
            self.round(shell, 2000, 160)
            (self.root / 'run/mu300-mobile-data-down').write_text('')
            self.set(shell, 'cap', '1G')
            self.round(shell, 2000, 220)
            self.assertEqual(self.calls(), ['ifdown wan', 'ifup wan'])

    def test_without_cut_nothing_is_turned_off(self):
        for shell in self.each_shell():
            self.fresh()
            self.set(shell, 'cap', '1000')
            self.round(shell, 0, 100)
            self.round(shell, 5000, 160)
            self.assertEqual(self.view(shell, self.now + 160)['cap']['level'], 'over')
            self.assertEqual(self.calls(), [])

    def test_ubuntu_stops_the_service(self):
        (self.root / 'etc/openwrt_release').unlink()
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-mobile-data-down').unlink(missing_ok=True)
            self.set(shell, 'cap', '1000')
            self.set(shell, 'cut', 'on')
            self.round(shell, 0, 100)
            self.round(shell, 2000, 160)
            self.assertEqual(self.calls(), ['systemctl stop mu300-mobile-data.service'])

    def test_text_view(self):
        for shell in self.each_shell():
            self.fresh()
            self.set(shell, 'cap', '50G')
            self.round(shell, 0, 100)
            self.round(shell, 3 << 30, 160)
            out = self.run_tool(shell, now=self.now + 160).stdout
            self.assertIn('today         2026-10-10  down 3.0 GB, up 0 B', out)
            self.assertIn('this cycle    2026-10-01 - 2026-10-31  3.0 GB used of 50.0 GB (6%), 22 days left', out)
            self.assertIn('2026-10-10  down    3.0 GB', self.run_tool(shell, 'days', now=self.now + 160).stdout)


class Service(TrafficBase):
    def start(self, shell, **env):
        e = self.env(MU300_SYSROOT=self.root, MU300_TRAFFIC_NOW=self.now, MU300_TRAFFIC_TZOFF=self.off, **env)
        return subprocess.Popen(shell + [str(TOOL), 'run'], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def wait_for(self, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def test_a_stop_saves_at_once_and_the_disk_is_written_rarely(self):
        for shell in self.each_shell():
            self.fresh()
            self.counters(1000, 100, 100)
            self.store.mkdir(parents=True)
            (self.store / 'conf').write_text('INTERVAL=10\nCHECKPOINT=1800\n')
            p = self.start(shell)
            try:
                live = self.root / 'run/mu300/traffic/ledger'
                self.assertTrue(self.wait_for(lambda: (self.store / 'ledger').exists()))
                first = (self.store / 'ledger').read_text()
                self.counters(5000, 500, 200)
                time.sleep(0.5)
                p.send_signal(signal.SIGTERM)
                _, err = p.communicate(timeout=5)
            finally:
                if p.poll() is None:
                    p.kill()
                    p.communicate()
            self.assertEqual(p.returncode, 0, err)
            self.assertIn('base aaaa-1 7 sipa_eth0 1000 100 100', first)
            self.assertIn('d 2026-10-10 4000 400', (self.store / 'ledger').read_text())
            self.assertEqual(live.read_text(), (self.store / 'ledger').read_text())
            self.assertFalse((self.root / 'run/mu300/traffic/pid').exists())

    def test_one_service_at_a_time(self):
        for shell in self.each_shell():
            self.fresh()
            self.counters(0, 0, 100)
            (self.root / 'run/mu300/traffic').mkdir(parents=True)
            (self.root / 'run/mu300/traffic/pid').write_text(f'{os.getpid()}\n')
            r = self.run_tool(shell, 'run', ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('already running', r.stderr)

    def test_a_stale_lock_is_taken_over(self):
        for shell in self.each_shell():
            self.fresh()
            self.counters(0, 0, 100)
            lock = self.root / 'run/mu300/traffic/lock'
            lock.mkdir(parents=True)
            (lock / 'pid').write_text('999999\n')
            self.run_tool(shell, 'save')
            self.assertFalse(lock.exists())

    def test_the_summary_is_for_the_dashboard(self):
        for shell in self.each_shell():
            self.fresh()
            self.set(shell, 'cap', '1000')
            self.sample(shell, 0, 0, 100, self.now)
            self.sample(shell, 950, 0, 160, self.now + 60)
            s = (self.root / 'run/mu300/traffic/summary').read_text()
            for line in ('level=warn', 'used=950', 'cap=1000', 'today_rx=950', 'clock=1', 'cycle_start=2026-10-01'):
                self.assertIn(line + '\n', s)


class Json(TrafficBase):
    def test_json_is_valid_with_nothing_counted(self):
        for shell in self.each_shell():
            self.fresh()
            self.no_interface()
            (self.root / 'proc/sys/kernel/random/boot_id').write_text('aaaa-1\n')
            (self.root / 'proc/uptime').write_text('5.00 1.00\n')
            v = self.view(shell)
            self.assertEqual((v['available'], v['days'], v['cap']['level'], v['since']), (0, [], 'none', None))
            self.assertEqual(len(v['cycles']), 1)

    def test_store_falls_back_to_etc_without_the_linux_disk(self):
        for shell in self.each_shell():
            self.fresh()
            subprocess.run(['rm', '-rf', str(self.root / 'mnt')])
            self.sample(shell, 0, 0, 100, self.now)
            self.assertTrue((self.root / 'etc/mu300/traffic/ledger').exists())
            (self.root / 'mnt/mu300-disk/.mu300').mkdir(parents=True)


if __name__ == '__main__':
    unittest.main()
