"""Offline tests for ui/native-control/overlay/usr/sbin/u30pro-charger (what a charger power-on does).
No device is used: /proc, the data store and the PMIC reset are replaced under a temporary root.
Run under Linux (WSL):  python3 tests/test_charger.py
The device runs BusyBox ash; to use it:  U30_CHARGER_SHELL="qemu-aarch64 -L /opt/u30root /opt/u30root/bin/busybox sh" python3 tests/test_charger.py
"""
import os, shlex, struct, subprocess, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHARGER = REPO / 'ui' / 'native-control' / 'overlay' / 'usr' / 'sbin' / 'u30pro-charger'
SHELL = shlex.split(os.environ.get('U30_CHARGER_SHELL', 'sh'))
# the device's /proc/cmdline has embedded newlines; the boot mode comes from the bootloader part
NORMAL = 'earlycon=sprd_serial,0x20210000,921600n8 console=ttyS1,921600n8\n      modem=shutdown sprdboot.mode=normal\n u30pro.modem=auto\n'
CHARGER_BOOT = 'earlycon=sprd_serial,0x20210000,921600n8 console=ttyS1,921600n8\n      modem=shutdown sprdboot.mode=charger\n u30pro.modem=auto\n'
INPUT_DEVICES = ('I: Bus=0018 Vendor=0000 Product=0000 Version=0000\nN: Name="tlsc6x_touch"\nH: Handlers=event0 \nB: KEY=400 0 0 0 0 0\n\n'
                 'I: Bus=0019 Vendor=0001 Product=0001 Version=0100\nN: Name="gpio-keys"\nH: Handlers=kbd event1 \nB: KEY=1c004000000000 0\n\n')
EV_SYN, EV_KEY, KEY_HOME, KEY_POWER = 0, 1, 102, 116


def event(t, etype, code, value):
    """struct input_event on arm64: u64 seconds, u64 microseconds, u16 type, u16 code, s32 value"""
    sec = int(t)
    return struct.pack('<QQHHi', sec, round((t - sec) * 1e6), etype, code, value)


def key(t, code, value):
    return event(t, EV_KEY, code, value) + event(t, EV_SYN, 0, 0)


@unittest.skipUnless(os.name == 'posix', 'needs a POSIX shell')
class TestCharger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name)
        for d in ('proc', 'run', 'overlay', 'bin'):
            (r / d).mkdir(parents=True)
        (r / 'proc/mounts').write_text('/dev/mmcblk0p83 /overlay ext4 rw,relatime 0 0\n')
        reset = r / 'bin/reset'
        reset.write_text('#!/bin/sh\necho "reset $*" >>"$U30PRO_CHARGER_ROOT/resets"\n')
        reset.chmod(0o755)
        self.env = dict(os.environ, U30PRO_CHARGER_ROOT=str(r), U30PRO_CHARGER_RESET=str(reset), U30PRO_CHARGER_WAIT='0',
                        U30PRO_CHARGER_SETTING='')

    def tearDown(self):
        self.tmp.cleanup()

    def cmdline(self, text):
        (self.root / 'proc/cmdline').write_text(text)

    def run_charger(self, *args):
        return subprocess.run(SHELL + [str(CHARGER)] + list(args), env=self.env, capture_output=True, text=True, timeout=30)

    def resets(self):
        f = self.root / 'resets'
        return f.read_text().splitlines() if f.exists() else []

    def streak(self):
        f = self.root / 'overlay/u30pro-charger/streak'
        return f.read_text().strip() if f.exists() else None

    def set_streak(self, n):
        d = self.root / 'overlay/u30pro-charger'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'streak').write_text('%d\n' % n)

    def events(self):
        f = self.root / 'overlay/u30pro-charger/events.log'
        return f.read_text() if f.exists() else ''

    def test_normal_boot_continues_and_clears_the_count(self):
        self.cmdline(NORMAL)
        self.set_streak(2)
        r = self.run_charger('early')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.resets(), [])
        self.assertEqual(self.streak(), '0')
        self.assertFalse((self.root / 'run/u30pro-charger-mode').exists())

    def test_charger_boot_restarts_into_the_normal_mode(self):
        self.cmdline(CHARGER_BOOT)
        r = self.run_charger('early')
        self.assertEqual(self.resets(), ['reset 5'])
        self.assertEqual(self.streak(), '1')
        self.assertIn('restarting into the normal mode', self.events())
        # if the reset does not come, the boot goes on in charger mode rather than as a normal boot
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.root / 'run/u30pro-charger-mode').exists())

    def test_third_charger_boot_in_a_row_still_restarts(self):
        self.cmdline(CHARGER_BOOT)
        self.set_streak(2)
        self.run_charger('early')
        self.assertEqual(self.resets(), ['reset 5'])
        self.assertEqual(self.streak(), '3')

    def test_fourth_charger_boot_in_a_row_stays(self):
        self.cmdline(CHARGER_BOOT)
        self.set_streak(3)
        r = self.run_charger('early')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.resets(), [])
        self.assertEqual(self.streak(), '4')
        self.assertIn('4 times in a row: staying in charger mode', self.events())

    def test_setting_stay_never_restarts(self):
        self.cmdline(CHARGER_BOOT)
        self.env['U30PRO_CHARGER_SETTING'] = 'stay'
        r = self.run_charger('early')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.resets(), [])
        self.assertEqual(self.streak(), '1')
        self.assertIn('setting stay', self.events())

    def test_unknown_setting_means_boot(self):
        self.cmdline(CHARGER_BOOT)
        self.env['U30PRO_CHARGER_SETTING'] = 'bogus'
        self.run_charger('early')
        self.assertEqual(self.resets(), ['reset 5'])

    def test_no_data_store_stays_without_restart(self):
        self.cmdline(CHARGER_BOOT)
        (self.root / 'proc/mounts').write_text('tmpfs /tmp tmpfs rw 0 0\n')
        r = self.run_charger('early')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.resets(), [])
        self.assertIn('not available for the restart count', (self.root / 'run/u30pro-charger.log').read_text())

    def test_android_boot_mode_is_recognised(self):
        self.cmdline('console=ttyS1 androidboot.mode=charger\n')
        self.run_charger('early')
        self.assertEqual(self.resets(), ['reset 5'])

    def test_similar_words_are_not_charger_mode(self):
        self.cmdline('console=ttyS1 sprdboot.mode=chargerx xsprdboot.mode=charger\n')
        r = self.run_charger('early')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.resets(), [])

    def test_restart_then_normal_boot_clears_the_count(self):
        self.cmdline(CHARGER_BOOT)
        self.run_charger('early')
        self.assertEqual(self.streak(), '1')
        self.cmdline(NORMAL)
        self.assertEqual(self.run_charger('early').returncode, 0)
        self.assertEqual(self.streak(), '0')

    def keys(self, data):
        (self.root / 'proc/bus/input').mkdir(parents=True, exist_ok=True)
        (self.root / 'proc/bus/input/devices').write_text(INPUT_DEVICES)
        (self.root / 'dev/input').mkdir(parents=True, exist_ok=True)
        (self.root / 'dev/input/event1').write_bytes(data)

    def charger_log(self):
        f = self.root / 'run/u30pro-charger.log'
        return f.read_text() if f.exists() else ''

    def test_power_key_held_restarts(self):
        self.cmdline(CHARGER_BOOT)
        self.keys(key(1792700000.25, KEY_POWER, 1) + key(1792700002.75, KEY_POWER, 0))
        r = self.run_charger('watch')
        self.assertEqual(self.resets(), ['reset 5'])
        self.assertIn('power key held 2500 ms', self.charger_log())
        # the end of the (test) device ends the loop
        self.assertEqual(r.returncode, 1)

    def test_power_key_held_exactly_two_seconds_restarts(self):
        self.cmdline(CHARGER_BOOT)
        self.keys(key(1792700000.5, KEY_POWER, 1) + key(1792700002.5, KEY_POWER, 0))
        self.run_charger('watch')
        self.assertEqual(self.resets(), ['reset 5'])

    def test_short_power_press_is_left_to_the_screen(self):
        self.cmdline(CHARGER_BOOT)
        self.keys(key(1792700000.0, KEY_POWER, 1) + key(1792700001.0, KEY_POWER, 0))
        self.run_charger('watch')
        self.assertEqual(self.resets(), [])

    def test_held_home_key_does_nothing(self):
        self.cmdline(CHARGER_BOOT)
        self.keys(key(1792700000.0, KEY_HOME, 1) + key(1792700005.0, KEY_HOME, 0))
        self.run_charger('watch')
        self.assertEqual(self.resets(), [])

    def test_release_without_press_is_ignored(self):
        self.cmdline(CHARGER_BOOT)
        self.keys(key(1792700009.0, KEY_POWER, 0) + key(1792700010.0, KEY_POWER, 1) + key(1792700010.5, KEY_POWER, 0))
        self.run_charger('watch')
        self.assertEqual(self.resets(), [])

    def test_watch_does_nothing_in_a_normal_boot(self):
        self.cmdline(NORMAL)
        self.keys(key(1792700000.0, KEY_POWER, 1) + key(1792700003.0, KEY_POWER, 0))
        r = self.run_charger('watch')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.resets(), [])

    def test_watch_without_power_key_device(self):
        self.cmdline(CHARGER_BOOT)
        (self.root / 'proc/bus/input').mkdir(parents=True, exist_ok=True)
        (self.root / 'proc/bus/input/devices').write_text(INPUT_DEVICES.replace('gpio-keys', 'other-keys'))
        r = self.run_charger('watch')
        self.assertEqual(r.returncode, 1)
        self.assertIn('no gpio-keys input device', self.charger_log())

    def test_status(self):
        self.cmdline(CHARGER_BOOT)
        self.set_streak(2)
        out = self.run_charger('status').stdout
        self.assertIn('this boot: charger mode', out)
        self.assertIn('setting: boot; charger power-ons in a row: 2', out)


if __name__ == '__main__':
    unittest.main()
