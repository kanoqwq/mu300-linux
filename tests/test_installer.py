"""tools/linux-mode.sh: which adb device install.sh and uninstall.sh work on. With a phone or tablet attached next to
the device the installer must ask, never pick one by itself."""
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

from helpers import TOP, ShellTest

F50 = '324950664950           device usb:1 product:MU300 model:F50 device:MU300 transport_id:1'
U30 = '323960377386           device usb:2 product:U30Air model:U30_Air device:U30Air transport_id:2'
V50 = '9a8b7c6d5e4f           device usb:6 product:MU3351 model:V50 device:MU3351 transport_id:6'
TABLET = 'd16d93c5               device usb:3 product:nabu_global model:21051182G device:nabu transport_id:3'
OFFLINE = '192.168.31.1:55555     offline product:MU5358 model:MU5358 device:MU5358 transport_id:4'
# a device running MU300 Linux: adb is on there by default, and its USB serial number says which system answers
LINUX = 'MU300LINUX-324950664950-203d8329 device usb:1-1 transport_id:5'
LINUX_KEYS = 'MU300LINUX-324950664950-203d8329 unauthorized usb:1-1 transport_id:5'
LINUX_SERIAL = 'MU300LINUX-324950664950-203d8329'


class SelectDevice(ShellTest):
    def run_select(self, shell, devices, answer='', mode=''):
        (self.tmp / 'adb.out').write_text('List of devices attached\n' + ''.join(d + '\n' for d in devices) + '\n')
        self.stub('adb', '[ "$1 $2" = "devices -l" ] && cat "$STUBLOG/adb.out"; exit 0')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'. "$TOP/tools/linux-mode.sh"; unset ANDROID_SERIAL; select_device {mode}; '
                'echo "rc=$? serial=${ANDROID_SERIAL:-none}"')
        return self.sh(shell, code, stdin=answer + '\n').stdout

    def test_one_device_that_is_the_target(self):
        for shell in self.each_shell():
            for dev, serial in ((F50, '324950664950'), (U30, '323960377386')):
                out = self.run_select(shell, [dev])
                self.assertNotIn('ASKED', out)
                self.assertIn(f'serial={serial}', out)

    def test_target_and_tablet_asks_with_the_target_as_default(self):
        for shell in self.each_shell():
            out = self.run_select(shell, [TABLET, F50, OFFLINE])
            self.assertIn('ASKED[2]', out)                 # the F50 is the second line, and the default
            self.assertIn('serial=324950664950', out)
            out = self.run_select(shell, [TABLET, F50], answer='1')
            self.assertIn('serial=d16d93c5', out)          # the user's choice counts
            out = self.run_select(shell, [F50, U30])       # two targets: ask, first is the default
            self.assertIn('ASKED[1]', out)

    def test_v50_is_a_target_too(self):
        # the ZTE V50 (FINDINGS 95) is picked like the other two; install.sh then checks it is exactly one
        for shell in self.each_shell():
            out = self.run_select(shell, [V50])
            self.assertNotIn('ASKED', out)
            self.assertIn('serial=9a8b7c6d5e4f', out)
            out = self.run_select(shell, [TABLET, V50])
            self.assertIn('ASKED[2]', out)

    def test_only_a_tablet_asks(self):
        # the F50 was in Linux and a tablet was the only adb device: it must not be taken silently
        for shell in self.each_shell():
            out = self.run_select(shell, [TABLET])
            self.assertIn('ASKED[1]', out)

    def test_invalid_choice(self):
        for shell in self.each_shell():
            self.assertIn('DIE', self.run_select(shell, [TABLET, F50], answer='7'))

    def test_quiet_never_asks(self):
        for shell in self.each_shell():
            out = self.run_select(shell, [TABLET, F50], mode='quiet')
            self.assertNotIn('ASKED', out)
            self.assertIn('rc=0 serial=324950664950', out)
            out = self.run_select(shell, [TABLET], mode='quiet')
            self.assertNotIn('ASKED', out)
            self.assertIn('rc=1 serial=none', out)


class LinuxOverAdb(ShellTest):
    """A device running MU300 Linux shows up in `adb devices` (adb is on by default there), and its shell is root:
    `su -c id -u` says 0 there too. It must never be worked on as a rooted Android."""

    def setUp(self):
        super().setUp()
        # adb: the device list is a file; the reboot asked of the Linux turns it into the F50 in Android
        self.stub('adb', r'''
echo "adb $*" >> "$STUBLOG/adb.log"
case "$1 $2" in
    "devices -l") cat "$STUBLOG/adb.out"; exit 0 ;;
    "get-state ") n=$(awk -v s="${ANDROID_SERIAL:-}" 'NR > 1 && $2 == "device" && (s == "" || $1 == s)' "$STUBLOG/adb.out" | grep -c .)
        [ "$n" = 1 ] && echo device || echo "error: more than one or no device" >&2; exit 0 ;;
esac
[ "$1" = -s ] && { serial=$2; shift 2; } || serial=${ANDROID_SERIAL:-}
case "$serial $*" in
    MU300LINUX*"shell test -x /opt/mu300/bin/mu300-next-boot"*) [ -e "$STUBLOG/not-ours" ] || echo mu300 ;;
    MU300LINUX*"mu300-next-boot android && sync && reboot")
        [ -e "$STUBLOG/reboot-fails" ] || cp "$STUBLOG/adb.after" "$STUBLOG/adb.out" ;;
    *"shell ls -d /opt/mu300/bin"*) [ -e "$STUBLOG/linux-in-disguise" ] && echo /opt/mu300/bin ;;
    *"shell getprop sys.boot_completed") echo 1 ;;
    *"shell su -c 'id -u'") echo 0 ;;
esac
exit 0''')
        self.stub('nc', '[ -e "$STUBLOG/ssh-up" ]')        # the SSH port of the USB network
        self.stub('sleep', ':')
        self.stub('ssh', 'echo "ssh $*" >> "$STUBLOG/adb.log"; rm -f "$STUBLOG/ssh-up"; cp "$STUBLOG/adb.after" "$STUBLOG/adb.out"')

    def devices(self, name, lines):
        (self.tmp / name).write_text('List of devices attached\n' + ''.join(d + '\n' for d in lines) + '\n')

    def run_sh(self, shell, call, answer='', serial=None):
        (self.tmp / 'adb.log').unlink(missing_ok=True)
        # sleep as a function: busybox ash may run its own sleep applet before the stub on PATH (CI: 60 waits of 5 s)
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; '
                'say() { echo "SAY $*"; }; sleep() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'. "$TOP/tools/linux-mode.sh"; {"ANDROID_SERIAL=" + serial + "; export ANDROID_SERIAL" if serial else "unset ANDROID_SERIAL"}; '
                f'{call}; echo "rc=$? serial=${{ANDROID_SERIAL:-none}}"')
        r = self.sh(shell, code, stdin=answer + '\n')
        log = self.tmp / 'adb.log'
        return r.stdout, (log.read_text() if log.exists() else '')

    def test_it_is_not_an_android_device(self):
        for shell in self.each_shell():
            # alone: nothing to select, nothing to ask, and no state to work on
            self.devices('adb.out', [LINUX])
            out, _ = self.run_sh(shell, 'select_device')
            self.assertEqual('rc=0 serial=none', out.strip())
            out, log = self.run_sh(shell, 'echo "state=[$(android_state)]"')
            self.assertIn('state=[]', out)
            self.assertNotIn('get-state', log)             # adb would have said "device": the Linux one
            out, _ = self.run_sh(shell, 'target_attached')
            self.assertIn('rc=1', out)
            # next to the F50 in Android (two boards): the F50, without a question
            self.devices('adb.out', [LINUX, F50])
            out, _ = self.run_sh(shell, 'select_device')
            self.assertEqual('rc=0 serial=324950664950', out.strip())
            # next to a tablet: the tablet is asked about, the Linux device is not on the list
            self.devices('adb.out', [LINUX, TABLET])
            out, _ = self.run_sh(shell, 'select_device')
            self.assertIn('ASKED[1]', out)
            self.assertNotIn('MU300LINUX', out)
            self.assertIn('serial=d16d93c5', out)
            # named by ANDROID_SERIAL
            out, _ = self.run_sh(shell, 'echo "state=[$(android_state)]"', serial=LINUX_SERIAL)
            self.assertIn('state=[linux]', out)

    def test_which_devices_are_in_linux(self):
        for shell in self.each_shell():
            self.devices('adb.out', [F50, LINUX])
            out, _ = self.run_sh(shell, 'linux_adb_listed && echo "open=$(linux_adb)"')
            self.assertIn(f'open={LINUX_SERIAL}', out)
            self.devices('adb.out', [F50, LINUX_KEYS])     # adb auth keys there, and this computer has no key
            out, _ = self.run_sh(shell, 'linux_adb_listed && echo listed; linux_adb')
            self.assertIn('listed', out)
            self.assertIn('rc=1', out)
            self.devices('adb.out', [F50, TABLET])
            out, _ = self.run_sh(shell, 'linux_adb_listed')
            self.assertIn('rc=1', out)
            self.devices('adb.out', [LINUX, LINUX.replace('324950664950', '323960377386')])
            out, _ = self.run_sh(shell, 'linux_adb')         # two of them: none is picked
            self.assertIn('rc=1', out)

    def test_sent_back_to_android_over_adb(self):
        # no SSH (RNDIS on a Mac, or no nc): adb is the way in, and no password is asked
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX])
            self.devices('adb.after', [F50])
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('SAY The device is running MU300 Linux, not Android', out)
            self.assertIn('ASKED[yes]', out)
            self.assertIn(f'asking it over adb ({LINUX_SERIAL})', out)
            self.assertIn(f"adb -s {LINUX_SERIAL} shell /opt/mu300/bin/mu300-next-boot android && sync && reboot", log)
            self.assertIn('Android is up', out)
            self.assertIn('rc=0 serial=324950664950', out)
            self.assertNotIn('ssh', log)
            # never su, getprop or anything else of the installer in the Linux shell
            for line in log.splitlines():
                if 'MU300LINUX' in line:
                    self.assertRegex(line, r'shell (test -x /opt/mu300/bin/mu300-next-boot|/opt/mu300/bin/mu300-next-boot android)')

    def test_named_by_android_serial_is_sent_back_too(self):
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX])
            self.devices('adb.after', [F50])
            out, log = self.run_sh(shell, 'require_android', serial=LINUX_SERIAL)
            self.assertIn('rc=0 serial=324950664950', out)
            self.assertNotIn("su -c", log)

    def test_two_boards_in_linux_the_named_one_is_rebooted_never_the_other(self):
        # measured on the Mac with F50-B and a U30 Air both in Linux: linux_adb picked none of the two, and the SSH
        # fallback went to 192.168.77.1/78.1 - where the U30 Air, not the F50 named by ANDROID_SERIAL, answered
        other = LINUX.replace('324950664950', '323960377386')
        for shell in self.each_shell():
            self.devices('adb.out', [other, LINUX])
            self.devices('adb.after', [other, F50])
            (self.tmp / 'ssh-up').write_text('')
            out, log = self.run_sh(shell, 'require_android', serial=LINUX_SERIAL)
            self.assertIn(f'asking it over adb ({LINUX_SERIAL})', out)
            self.assertIn('rc=0 serial=324950664950', out)
            self.assertNotIn('ssh', log)
            self.assertNotIn('323960377386', log)
            # adb cannot reboot it: no SSH to an address that may be the other board's
            self.devices('adb.out', [other, LINUX])
            (self.tmp / 'reboot-fails').write_text('')
            out, log = self.run_sh(shell, 'require_android', serial=LINUX_SERIAL)
            self.assertIn('DIE more than one device runs MU300 Linux', out)
            self.assertNotIn('ssh', log)
            # the named one is not on adb at all, the other one is: no SSH to an address that may be the other's
            self.devices('adb.out', [other])
            out, log = self.run_sh(shell, 'require_android', serial=LINUX_SERIAL)
            self.assertIn('DIE more than one device runs MU300 Linux', out)
            self.assertNotIn('ssh', log)
            self.assertNotIn('reboot', log)
            self.devices('adb.out', [other, LINUX])
            # none named: neither is picked over adb, and SSH is not guessed either
            (self.tmp / 'reboot-fails').unlink()
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('DIE more than one device runs MU300 Linux', out)
            self.assertNotIn('reboot', log)
            self.assertNotIn('ssh', log)
            (self.tmp / 'ssh-up').unlink()

    def test_waiting_for_android_never_takes_the_phone_next_to_it(self):
        # measured: while the F50 rebooted, a Pixel was the only adb device; "adb get-state" said "device" and the
        # check went on with the Pixel. Now only the board that was rebooted (its serial is in MU300LINUX-<serial>-...)
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX, TABLET])
            self.devices('adb.after', [TABLET])          # the F50 never comes back
            out, log = self.run_sh(shell, 'require_android', serial=LINUX_SERIAL)
            self.assertNotIn('Android is up', out)
            self.assertIn('DIE the device did not come back as Android', out)
            self.assertNotIn('d16d93c5 shell', log)
            self.devices('adb.out', [LINUX, TABLET])
            self.devices('adb.after', [TABLET, F50])
            out, _ = self.run_sh(shell, 'require_android')
            self.assertIn('rc=0 serial=324950664950', out)

    def test_the_answer_no_touches_nothing(self):
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX])
            out, log = self.run_sh(shell, 'require_android', answer='no')
            self.assertIn('DIE boot Android yourself', out)
            self.assertNotIn('shell', log)

    def test_keys_only_and_no_ssh_says_what_to_do(self):
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX_KEYS])
            self.devices('adb.after', [F50])
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('SAY The device is running MU300 Linux, not Android', out)
            self.assertIn('DIE boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)', out)
            self.assertNotIn('shell', log)
            self.assertNotIn('waiting for Android', out)

    def test_keys_only_goes_over_ssh_as_before(self):
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX_KEYS])
            self.devices('adb.after', [F50])
            (self.tmp / 'ssh-up').write_text('')
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('ssh -t', log)
            self.assertIn('rc=0 serial=324950664950', out)

    def test_a_reboot_that_does_not_happen_falls_back_to_ssh_or_says_so(self):
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX])
            self.devices('adb.after', [F50])
            (self.tmp / 'reboot-fails').write_text('')
            out, _ = self.run_sh(shell, 'require_android')
            self.assertIn('DIE boot Android yourself', out)
            (self.tmp / 'ssh-up').write_text('')
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('ssh -t', log)
            self.assertIn('rc=0 serial=324950664950', out)
            (self.tmp / 'reboot-fails').unlink()

    def test_only_our_linux_is_told_to_reboot(self):
        # something else that calls itself MU300LINUX: no mu300-next-boot there, so no reboot command
        (self.tmp / 'not-ours').write_text('')
        for shell in self.each_shell():
            self.devices('adb.out', [LINUX])
            out, log = self.run_sh(shell, 'require_android')
            self.assertNotIn('&& sync && reboot', log)
            self.assertIn('DIE boot Android yourself', out)

    def test_a_linux_shell_is_never_taken_for_android(self):
        # whatever the serial number says: the marker of our Linux on the device that was selected stops it
        (self.tmp / 'linux-in-disguise').write_text('')
        for shell in self.each_shell():
            self.devices('adb.out', [F50])
            out, log = self.run_sh(shell, 'require_android')
            self.assertIn('DIE The device is running MU300 Linux, not Android', out)
            self.assertNotIn('getprop', log)
        (self.tmp / 'linux-in-disguise').unlink()
        for shell in self.each_shell():
            self.devices('adb.out', [F50])
            out, _ = self.run_sh(shell, 'require_android')
            self.assertIn('rc=0 serial=324950664950', out)


class Storage(ShellTest):
    """tools/storage.sh: internal region or SD card."""
    GIB = 1024 ** 3

    def run_choose(self, shell, card, internal=30 * GIB, answer='', forced='', label=None, tail=''):
        # card: None, or (device, sectors, has_partition, type); label: None (no ext4 on it) or its ext4 label
        if label is not None:
            (self.tmp / 'label').write_text(label)
        (self.tmp / 'sd').write_text('' if card is None else '%s %s %s\n' % (
            card[0] + ('p1' if card[2] else ''), card[1], card[3]))
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'gib() { echo "$1"; }; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'su_do() {{ case $1 in *skip=1080*) [ -e "{self.tmp}/label" ] && echo " 53 ef" ;; '
                f'*skip=1144*) cat "{self.tmp}/label" 2>/dev/null ;; *) cat "{self.tmp}/sd" ;; esac; }}; SIZE={internal}; '
                f'{"MU300_STORAGE=" + forced + "; " if forced else "unset MU300_STORAGE; "}'
                '. "$TOP/tools/storage.sh"; sd_probe; ' + (tail or 'choose_storage; ') +
                'echo "mode=$SD_MODE dev=${SD_DEV:-none}"')
        return self.sh(shell, code, stdin=answer + '\n').stdout

    def test_no_card_never_asks(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, None)
            self.assertNotIn('ASKED', out)
            self.assertIn('mode=0 dev=none', out)

    def test_card_asks_with_internal_as_default(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card)
            self.assertIn('ASKED[internal]', out)
            self.assertIn('mode=0', out)
            out = self.run_choose(shell, card, answer='sd')
            self.assertIn('mode=1 dev=/dev/block/mmcblk1p1', out)

    def test_small_internal_space_makes_the_card_the_default(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, False, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card, internal=100 * 1024 ** 2)
            self.assertIn('ASKED[sd]', out)
            self.assertIn('mode=1 dev=/dev/block/mmcblk1', out)

    def test_forced(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            self.assertIn('mode=1', self.run_choose(shell, card, forced='sd'))
            out = self.run_choose(shell, card, forced='internal')
            self.assertNotIn('ASKED', out); self.assertIn('mode=0', out)

    def test_forced_sd_without_card_dies(self):
        for shell in self.each_shell():
            self.assertIn('DIE', self.run_choose(shell, None, forced='sd'))

    def test_small_first_partition_is_too_small(self):
        # 64 MiB first partition in front of a big card: too small, not a reason to take the whole device
        card = ('/dev/block/mmcblk1', 64 * 2048, True, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card)
            self.assertNotIn('ASKED', out)
            self.assertIn('mode=0', out)

    def test_not_an_sd_card(self):
        for shell in self.each_shell():
            self.assertIn('mode=0 dev=none', self.run_choose(shell, ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'MMC')))

    def test_invalid_answer(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            self.assertIn('DIE', self.run_choose(shell, card, answer='usb'))

    def test_sd_existing(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            for label, want in ((None, 'no'), ('mu300sd', 'yes'), ('data', 'foreign'), ('', 'foreign')):
                (self.tmp / 'label').unlink(missing_ok=True)
                out = self.run_choose(shell, card, label=label, tail='echo "existing=$(sd_existing)"; SD_MODE=; ')
                self.assertIn('existing=%s\n' % want, out)

    def test_sd_existing_is_quiet_about_a_fat_card(self):
        # a FAT card has arbitrary bytes where ext4 keeps its label; macOS's tr in a UTF-8 locale printed
        # "tr: Illegal byte sequence" for them in the middle of the installer's report
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        (self.tmp / 'label').write_bytes(b'\xff\xfe\x80MSDOS\xc3\x00\x00')
        code = (f'su_do() {{ case $1 in *skip=1080*) echo " 00 00" ;; *skip=1144*) cat "{self.tmp}/label" ;; '
                f'*) echo /dev/block/mmcblk1p1 {card[1]} SD ;; esac; }}; t() {{ printf %s "$1"; }}; '
                f'. "{TOP}/tools/storage.sh"; sd_probe; echo "existing=$(sd_existing)"')
        for shell in self.each_shell():
            r = self.sh(shell, code, LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
            self.assertIn('existing=no\n', r.stdout)
            self.assertEqual('', r.stderr)

    def test_foreign_ext4_card_is_refused_before_anything_else(self):
        # the device refuses to format it; saying so only after the download and the build costs the user an hour
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            for kw in ({'answer': 'sd'}, {'forced': 'sd'}):
                out = self.run_choose(shell, card, label='data', **kw)
                self.assertIn('DIE', out)
                self.assertIn('MU300_STORAGE=internal', out)
            self.assertIn('mode=0', self.run_choose(shell, card, label='data'))        # internal stays possible
            self.assertIn('mode=1', self.run_choose(shell, card, label='mu300sd', answer='sd'))
            self.assertIn('DIE', self.run_choose(shell, card, label='', answer='sd'))   # unlabelled ext4 too


    def test_card_with_an_installation_is_the_default(self):
        # init starts a mu300sd card before anything internal: internal storage is not the safe answer then
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card, label='mu300sd')
            self.assertIn('ASKED[sd]', out)
            self.assertIn('mode=1 dev=/dev/block/mmcblk1p1', out)
            self.assertNotIn('starts that one first', out)

    def test_internal_with_an_installed_card_warns_and_needs_a_confirmation(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            for kw in ({'answer': 'internal\ninternal'}, {'forced': 'internal', 'answer': 'internal'}):
                out = self.run_choose(shell, card, label='mu300sd', **kw)
                self.assertIn('starts that one first', out, kw)
                self.assertIn('uninstall', out, kw)
                self.assertIn('ASKED[no]', out, kw)
                self.assertIn('mode=0', out, kw)
            for kw in ({'answer': 'internal\n'}, {'forced': 'internal', 'answer': 'yes'}):
                out = self.run_choose(shell, card, label='mu300sd', **kw)
                self.assertIn('starts that one first', out, kw)
                self.assertIn('DIE', out, kw)
                self.assertNotIn('mode=', out, kw)

    def test_internal_with_a_card_without_installation_asks_nothing_more(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            for label in (None, 'data'):
                (self.tmp / 'label').unlink(missing_ok=True)
                for kw in ({'answer': 'internal'}, {'forced': 'internal'}):
                    out = self.run_choose(shell, card, label=label, **kw)
                    self.assertNotIn('starts that one first', out)
                    self.assertNotIn('ASKED[no]', out)
                    self.assertIn('mode=0', out)

    def test_sd_kernel_ok(self):
        # a mainline bundle without ./features: sdcard has the SD host gated off, and a card install with it would
        # never find its card (it lands in Android)
        k = self.tmp / 'kmain'
        k.mkdir()
        for shell in self.each_shell():
            for features, sd_mode, want in ((None, 1, 1), ('sdcard\n', 1, 0), ('other\n', 1, 1),
                                            (None, 0, 0), ('sdcard\n', 0, 0)):
                (k / 'features').unlink(missing_ok=True)
                if features is not None:
                    (k / 'features').write_text(features)
                r = self.sh(shell, f'. "{TOP}/tools/storage.sh"; SD_MODE={sd_mode}; sd_kernel_ok "{k}"; echo "rc=$?"')
                self.assertEqual(f'rc={want}', r.stdout.strip(), (features, sd_mode))

    def test_installer_refuses_a_card_install_with_a_kernel_that_cannot_read_it(self):
        src = (TOP / 'install.sh').read_text()
        unpack = src.index('tar -xzf "$ASSETS/mu300-kernel-$KERNEL.tar.gz" -C "$KMAIN"')
        check = src.index('sd_kernel_ok "$KMAIN" || die')
        self.assertLess(unpack, check)
        self.assertLess(check, src.index("say \"$(t 'Adding the vendor files from your device to the images')\""))


class InstallEnv(ShellTest):
    """mu300-install.env, what android-install.sh is told: tools/storage.sh's write_install_env (install.sh) and
    install.ps1's InstallEnvText write the same text (tests/installer.Tests.ps1 compares the two)."""
    COMMON = dict(OFF=27762098176, FORMAT=1, OSES='ubuntu openwrt', WIPE_LEGACY=0, UPDATE=0, BOOT_OS='openwrt',
                  DEFAULT_LINUX=1, BOOT_ATTEMPTS=5, IMPORT_HOTSPOT=1, KERNEL='6.18', SYNC_BOOTCHAIN=1,
                  PWHASH='$6$salt$hash/x.y')

    def write_env(self, shell, **v):
        code = ''.join(f"{k}='{val}'; " for k, val in v.items())
        r = self.sh(shell, code + f'. "{TOP}/tools/storage.sh"; write_install_env')
        self.assertEqual('', r.stderr)
        return r.stdout

    @staticmethod
    def parse(text):
        return dict(line.split('=', 1) for line in text.splitlines())

    def test_sd_mode_carries_the_card_and_the_internal_region(self):
        # SIZE in SD mode is the card's; the file still gets the internal region (the marker goes there)
        for shell in self.each_shell():
            out = self.write_env(shell, **self.COMMON, SIZE=31914967040, INT_SIZE=34776023040, SD_MODE=1,
                           SD_DEV='/dev/block/mmcblk1p1', INTERNAL_EXISTS=1)
            e = self.parse(out)
            self.assertEqual(e['SD_MODE'], '1')
            self.assertEqual(e['SD_DEV'], '/dev/block/mmcblk1p1')
            self.assertEqual(e['INTERNAL_EXISTS'], '1')
            self.assertEqual((e['OFF'], e['SIZE']), ('27762098176', '34776023040'))
            self.assertEqual((e['OFF_S'], e['SIZE_S']), ('54222848', '67921920'))
            self.assertEqual((e['FORMAT'], e['UPDATE']), ('1', '0'))
            self.assertEqual(e['OSES'], '"ubuntu openwrt"')
            self.assertEqual(e['PWHASH'], "'$6$salt$hash/x.y'")
            self.assertEqual(list(e), ['OFF', 'SIZE', 'OFF_S', 'SIZE_S', 'FORMAT', 'OSES', 'WIPE_LEGACY', 'UPDATE',
                                       'BOOT_OS', 'DEFAULT_LINUX', 'BOOT_ATTEMPTS', 'IMPORT_HOTSPOT', 'KERNEL',
                                       'SD_MODE', 'SD_DEV', 'INTERNAL_EXISTS', 'ADB', 'ADB_KEY', 'SYNC_BOOTCHAIN',
                                       'PWHASH', 'KEEP_PW_OSES'])
            self.assertEqual(e['SYNC_BOOTCHAIN'], '1')
            self.assertEqual(e['KEEP_PW_OSES'], '""')
            # an update that keeps the passwords of the systems the user confirmed
            out = self.write_env(shell, **dict(self.COMMON, FORMAT=0, UPDATE=1), SIZE=34776023040, INT_SIZE='', SD_MODE=0,
                                 SD_DEV='', INTERNAL_EXISTS=1, KEEP_PW_OSES='ubuntu openwrt')
            self.assertEqual(self.parse(out)['KEEP_PW_OSES'], '"ubuntu openwrt"')

    def test_internal_mode(self):
        for shell in self.each_shell():
            common = dict(self.COMMON, FORMAT=0, UPDATE=1)
            out = self.write_env(shell, **common, SIZE=34776023040, INT_SIZE='', SD_MODE=0, SD_DEV='', INTERNAL_EXISTS=1)
            e = self.parse(out)
            self.assertEqual((e['SD_MODE'], e['SD_DEV'], e['INTERNAL_EXISTS']), ('0', '', '1'))
            self.assertEqual((e['OFF'], e['SIZE'], e['OFF_S'], e['SIZE_S']),
                             ('27762098176', '34776023040', '54222848', '67921920'))
            self.assertEqual((e['FORMAT'], e['UPDATE']), ('0', '1'))

    def test_bootchain_sync_is_off_unless_set(self):
        for shell in self.each_shell():
            common = {k: v for k, v in self.COMMON.items() if k != 'SYNC_BOOTCHAIN'}
            e = self.parse(self.write_env(shell, **common, SIZE=1, INT_SIZE='', SD_MODE=0, SD_DEV='', INTERNAL_EXISTS=0))
            self.assertEqual(e['SYNC_BOOTCHAIN'], '0')

    def test_adb(self):
        key = 'QAAAA' + 'A' * 694 + '='
        for shell in self.each_shell():
            e = self.parse(self.write_env(shell, **self.COMMON, SIZE=1, INT_SIZE='', SD_MODE=0, SD_DEV='',
                                          INTERNAL_EXISTS=0))
            self.assertEqual((e['ADB'], e['ADB_KEY']), ('0', "''"))      # off when nobody asked
            e = self.parse(self.write_env(shell, **self.COMMON, SIZE=1, INT_SIZE='', SD_MODE=0, SD_DEV='',
                                          INTERNAL_EXISTS=0, ADB=1, ADB_KEY=f'{key} me@pc'))
            self.assertEqual((e['ADB'], e['ADB_KEY']), ('1', f"'{key} me@pc'"))

    def test_adb_pubkey(self):
        key = 'QAAAA' + 'A' * 694 + '='
        f = self.tmp / 'adbkey.pub'
        cases = [(f'{key} me@my-mac.local', f'{key} me@my-mac.local'),     # adbkey.pub: no newline at the end
                 (f'{key} evil\'$(reboot)`x`\n', f'{key} evilrebootx'),     # a name that could leave the quotes
                 (key, f'{key} installer'),
                 ('ssh-rsa AAAAB3NzaC1yc2E me@pc\n', None), (key[1:] + ' x', None), (key[:-1] + 'A', None),
                 ('QAAAA' + 'A' * 693 + "'=", None), ('', None)]
        for shell in self.each_shell():
            for text, want in cases:
                f.write_text(text)
                r = self.sh(shell, f'. "{TOP}/tools/storage.sh"; adb_pubkey "{f}"')
                if want is None:
                    self.assertEqual(1, r.returncode, text)
                    self.assertEqual('', r.stdout)
                else:
                    self.assertEqual(0, r.returncode, r.stderr)
                    self.assertEqual(want + '\n', r.stdout)
            r = self.sh(shell, f'. "{TOP}/tools/storage.sh"; adb_pubkey "{self.tmp}/none"')
            self.assertEqual(1, r.returncode)

    def test_installer_writes_the_env_file_with_it(self):
        src = (TOP / 'install.sh').read_text()
        self.assertIn('write_install_env > "$env"', src)
        self.assertNotIn("printf 'OFF=", src)


class SdErase(ShellTest):
    """tools/storage.sh's sd_erase (uninstall.sh): the card is erased only when it holds mu300sd, never the eMMC.

    su_do runs the device commands here, with dd, sm and umount as stubs: dd reads the superblock from the files
    label/magic and logs every write, sm lists the volumes in vols, umount drops the line from the mounts file."""

    def setUp(self):
        super().setUp()
        t = self.tmp
        self.stub('dd', 'case "$*" in\n'
                  '  *of=*) echo "dd $*" >> "$STUBLOG/log"; [ -e "$STUBLOG/stuck" ] || rm -f "$STUBLOG/label" ;;\n'
                  '  *skip=1080*) [ -e "$STUBLOG/label" ] && [ ! -e "$STUBLOG/nomagic" ] && printf "\\123\\357" ;;\n'
                  '  *skip=1144*) cat "$STUBLOG/label" 2>/dev/null ;;\n'
                  'esac; true')
        self.stub('sm', 'case $1 in list-volumes) cat "$STUBLOG/vols" ;; *) echo "sm $*" >> "$STUBLOG/log"\n'
                  '  [ -e "$STUBLOG/stuck-sm" ] && exit 1\n'
                  '  grep -v "/vold/$2 " "$STUBLOG/mounts" > "$STUBLOG/m.new"; mv "$STUBLOG/m.new" "$STUBLOG/mounts" ;; esac')
        self.stub('umount', 'echo "umount $*" >> "$STUBLOG/log"; [ -e "$STUBLOG/stuck-mount" ] && exit 1\n'
                  'grep -v " $1 " "$STUBLOG/mounts" > "$STUBLOG/m.new"; mv "$STUBLOG/m.new" "$STUBLOG/mounts"')
        self.stub('sync', 'true')
        (t / 'vols').write_text('private mounted null\npublic:179,1 mounted 1234-ABCD\npublic:8,1 mounted 55AA-1\n')
        (t / 'mounts').write_text('/dev/block/mmcblk0p40 /data f2fs rw 0 0\n'
                                  '/dev/block/mmcblk1p1 /data/local/tmp/mu300root ext4 rw 0 0\n')
        (t / 'log').write_text('')
        for disk in ('mmcblk0', 'mmcblk1'):
            (t / 'sys' / 'block' / disk / 'device').mkdir(parents=True)
        (t / 'sys/block/mmcblk0/device/type').write_text('MMC\n')
        (t / 'sys/block/mmcblk1/device/type').write_text('SD\n')

    def run_erase(self, shell, label='mu300sd', dev='/dev/block/mmcblk1', tail='sd_erase; echo SURVIVED'):
        if label is not None:
            (self.tmp / 'label').write_bytes(label.encode() + b'\0' * (16 - len(label)))
        (self.tmp / 'sd').write_text(f'{dev} {62 * 2 ** 21} SD\n')
        code = (f'TOP="{TOP}"; say() {{ :; }}; die() {{ echo "DIE $*"; exit 1; }}; '
                'gib() { echo "$1"; }; t() { printf "%s" "$1"; }; '
                'su_do() { case $1 in "for b in /sys/block/mmcblk"*) cat "$STUBLOG/sd" ;; *) sh -c "$1" ;; esac; }; '
                f'MU300_SYSFS="{self.tmp}/sys"; MU300_MOUNTS="{self.tmp}/mounts"; '
                '. "$TOP/tools/storage.sh"; sd_probe; ' + tail)
        return self.sh(shell, code).stdout, (self.tmp / 'log').read_text()

    def test_erases_the_mu300sd_card_after_releasing_it(self):
        for shell in self.each_shell():
            (self.tmp / 'mounts').write_text('/dev/block/mmcblk0p40 /data f2fs rw 0 0\n'
                                             '/dev/block/mmcblk1p1 /data/local/tmp/mu300root ext4 rw 0 0\n')
            (self.tmp / 'log').write_text('')
            out, log = self.run_erase(shell, dev='/dev/block/mmcblk1p1')
            self.assertIn('SURVIVED', out)
            self.assertNotIn('DIE', out)
            self.assertIn('sm unmount public:179,1\n', log)
            self.assertNotIn('public:8,1', log)                     # a USB stick is not the card
            self.assertIn('umount /data/local/tmp/mu300root\n', log)
            self.assertNotIn('umount /data\n', log)                 # the eMMC's own mounts stay
            self.assertIn('dd if=/dev/zero of=/dev/block/mmcblk1p1 bs=1048576 count=64 conv=notrunc', log)
            self.assertEqual(log.count('dd '), 1)

    def test_whole_card_without_a_partition_table(self):
        for shell in self.each_shell():
            out, log = self.run_erase(shell, dev='/dev/block/mmcblk1')
            self.assertIn('SURVIVED', out)
            self.assertIn('of=/dev/block/mmcblk1 ', log)

    def test_never_a_card_without_mu300sd(self):
        for shell in self.each_shell():
            for label in (None, 'data', '', 'mu300root'):
                (self.tmp / 'label').unlink(missing_ok=True)
                (self.tmp / 'log').write_text('')
                out, log = self.run_erase(shell, label=label)
                self.assertIn('DIE', out, label)
                self.assertNotIn('dd ', log, label)

    def test_no_card(self):
        for shell in self.each_shell():
            (self.tmp / 'sd').write_text('')
            out, log = self.run_erase(shell, tail='SD_DEV=; sd_erase; echo SURVIVED')
            self.assertIn('DIE', out)
            self.assertNotIn('dd ', log)

    def test_never_the_emmc(self):
        # mmcblk0 is the eMMC Android runs from, whatever its superblock says: refused on the host and, should a
        # command for it ever be built, on the device too
        for shell in self.each_shell():
            for dev in ('/dev/block/mmcblk0p1', '/dev/block/mmcblk0', '/dev/block/sda1', '/dev/block/mmcblk1p1 x'):
                (self.tmp / 'log').write_text('')
                out, log = self.run_erase(shell, tail=f'SD_DEV="{dev}"; sd_erase; echo SURVIVED')
                self.assertIn('DIE', out, dev)
                self.assertNotIn('dd ', log, dev)
            for dev in ('/dev/block/mmcblk0p1', '/dev/block/mmcblk0'):
                out, log = self.run_erase(shell, tail=f'su_do "$(sd_erase_cmd {dev})"')
                self.assertIn('REFUSED', out, dev)
                self.assertNotIn('dd ', log, dev)

    def test_device_side_checks(self):
        for shell in self.each_shell():
            # not an SD card in sysfs (a second eMMC, an SDIO function)
            (self.tmp / 'sys/block/mmcblk1/device/type').write_text('MMC\n')
            out, log = self.run_erase(shell, tail='su_do "$(sd_erase_cmd /dev/block/mmcblk1p1)"')
            self.assertIn('REFUSED', out); self.assertNotIn('dd ', log)
            (self.tmp / 'sys/block/mmcblk1/device/type').write_text('SD\n')
            # the ext4 magic is gone (the label bytes alone are no filesystem)
            (self.tmp / 'nomagic').write_text('')
            out, log = self.run_erase(shell, tail='su_do "$(sd_erase_cmd /dev/block/mmcblk1p1)"')
            self.assertIn('REFUSED', out); self.assertNotIn('dd ', log)
            (self.tmp / 'nomagic').unlink()
            # the label changed between the host check and the erase
            out, log = self.run_erase(shell, label='data', tail='su_do "$(sd_erase_cmd /dev/block/mmcblk1p1)"')
            self.assertIn('REFUSED', out); self.assertNotIn('dd ', log)

    def test_vold_mount_that_stays_is_not_erased(self):
        # vold mounts the card as /dev/block/vold/public:179,N: a failed sm unmount must stop the write too
        vold = '/dev/block/vold/public:179,1 /mnt/media_rw/1234-ABCD vfat rw 0 0\n'
        for shell in self.each_shell():
            (self.tmp / 'mounts').write_text(vold)
            (self.tmp / 'log').write_text('')
            out, log = self.run_erase(shell, dev='/dev/block/mmcblk1p1')
            self.assertIn('SURVIVED', out)                          # sm let go of it: erased
            self.assertIn('dd if=/dev/zero', log)
            (self.tmp / 'stuck-sm').write_text('')
            (self.tmp / 'mounts').write_text(vold)
            (self.tmp / 'log').write_text('')
            out, log = self.run_erase(shell, dev='/dev/block/mmcblk1p1')
            self.assertIn('DIE', out)
            self.assertNotIn('dd ', log)
            (self.tmp / 'stuck-sm').unlink()

    def test_still_mounted_is_not_erased(self):
        for shell in self.each_shell():
            (self.tmp / 'stuck-mount').write_text('')
            (self.tmp / 'mounts').write_text('/dev/block/mmcblk1p1 /data/local/tmp/mu300root ext4 rw 0 0\n')
            out, log = self.run_erase(shell, dev='/dev/block/mmcblk1p1')
            self.assertIn('DIE', out)
            self.assertNotIn('dd ', log)

    def test_a_filesystem_that_survives_the_erase_is_reported(self):
        for shell in self.each_shell():
            (self.tmp / 'stuck').write_text('')
            out, _ = self.run_erase(shell)
            self.assertIn('DIE', out)
            self.assertIn('still', out)

    def test_device_command_has_no_quotes(self):
        # it travels inside su -c '...', and uninstall.ps1 sends the same text, where double quotes are lost
        for shell in self.each_shell():
            out, _ = self.run_erase(shell, tail='sd_erase_cmd /dev/block/mmcblk1p1')
            self.assertTrue(out)
            self.assertNotIn("'", out); self.assertNotIn('"', out)


class Region(ShellTest):
    """storage.sh's region functions, with su_do running the device commands against a fake sysfs and eMMC file."""

    def fake(self, ext4_at=None, full=False):
        sysfs = self.tmp / 'sys/block/mmcblk0'
        (sysfs / 'mmcblk0p1').mkdir(parents=True, exist_ok=True)
        (sysfs / 'mmcblk0p1/start').write_text('2048\n')
        # ends at 8 GiB + 1 MiB; full: 2 MiB before the end of the disk, the layout of issue #65
        (sysfs / 'mmcblk0p1/size').write_text(f'{((16 << 21) - 4096) if full else (4 << 21)}\n')
        (sysfs / 'size').write_text(f'{16 << 21}\n')                   # a 16 GiB eMMC
        emmc = self.tmp / 'mmcblk0'
        with open(emmc, 'wb') as f:
            f.truncate(16 << 30)
            if ext4_at is not None:
                f.seek(ext4_at + 1024 + 4); f.write(struct.pack('<I', 1000))
                f.seek(ext4_at + 1080); f.write(b'\x53\xef')
                f.seek(ext4_at + 1144); f.write(b'mu300root')
        return emmc

    def run_region(self, shell, call):
        su = (f'su_do() {{ printf "%s\\n" "$1" >> "{self.tmp}/su.log"; '
              f'sh -c "$(printf "%s" "$1" | sed -e "s|/sys/block|{self.tmp}/sys/block|g" '
              f'-e "s|/data/mu300linux|{self.tmp}/data/mu300linux|g" '
              f'-e "s|/dev/block/mmcblk0|{self.tmp}/mmcblk0|g")"; }}\n'
              # the installers' t: the message with its arguments in place
              't() { _m=$1; shift; _i=1; for _a; do _m=$(printf "%s" "$_m" | sed "s|{$_i}|$_a|g"); _i=$((_i + 1)); done; '
              'printf "%s" "$_m"; }\n')
        return self.sh(shell, su + f'. "{TOP}/tools/storage.sh"\n{call}\n'
                       'echo "rc=$? OFF=$OFF SIZE=$SIZE existing=${existing:-} DIRTY=${DIRTY:-}"')

    def part(self, n, name, start, size):
        """partition n of the fake eMMC, with its GPT name in uevent as the kernel gives it"""
        d = self.tmp / f'sys/block/mmcblk0/mmcblk0p{n}'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'start').write_text(f'{start}\n'); (d / 'size').write_text(f'{size}\n')
        (d / 'uevent').write_text(f'DEVTYPE=partition\nPARTN={n}\nPARTNAME={name}\n')

    @staticmethod
    def ext4(emmc, at, label=b'mu300root', blocks=1000):
        with open(emmc, 'r+b') as f:
            f.seek(at + 1024 + 4); f.write(struct.pack('<I', blocks))
            f.seek(at + 1080); f.write(b'\x53\xef')
            f.seek(at + 1144); f.write(label.ljust(16, b'\0'))

    # a GPT partition behind p1 (which ends at 8 GiB + 1 MiB): 3 GiB, 1 MiB aligned
    PSTART, PSIZE = 2048 + (4 << 21) + 2048, 3 << 21

    def test_a_gpt_partition_named_mu300root_is_where_linux_goes(self):
        """kanoqwq's desktop installer puts the system in a GPT partition named mu300root behind userdata (FINDINGS
        178/179). The region behind the last partition is then (nearly) nothing, and a U30 user was told there was
        no room: the partition is the place - updated or wiped there - and boot/init boots it (stage=root-part)."""
        emmc = self.fake()
        ps, pz = self.PSTART, self.PSIZE
        self.part(2, 'mu300root', ps, pz)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; echo "IN_PART=$IN_PART"').stdout
            self.assertIn(f'rc=0 OFF={ps * 512} SIZE={pz * 512}', out)
            self.assertIn('IN_PART=1', out)
            # empty: no installation, the whole partition to format (after the data check)
            out = self.run_region(shell, 'region_probe; region_find_existing; echo "IN_PART=$IN_PART PART_FS=$PART_FS"; '
                                  'region_part_refusal || echo no-refusal').stdout
            self.assertIn(f'OFF={ps * 512} SIZE={pz * 512} existing=no', out)
            self.assertIn('IN_PART=1 PART_FS=none', out)
            self.assertIn('no-refusal', out)
        self.ext4(emmc, ps * 512)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing; echo "IN_PART=$IN_PART PART_FS=$PART_FS"').stdout
            self.assertIn(f'rc=0 OFF={ps * 512} SIZE={1000 * 4096} existing=yes', out)
            self.assertIn('IN_PART=1 PART_FS=mu300root', out)
        # never larger than the partition, whatever the superblock says
        self.ext4(emmc, ps * 512, blocks=(pz * 512 // 4096) + 10)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing').stdout
            self.assertIn(f'OFF={ps * 512} SIZE={pz * 512} existing=yes', out)
            # the bounds the device checks again (android-install.sh): inside the partition, or in the free space
            out = self.run_region(shell, f'region_probe; for a in "{ps * 512} {pz * 512}" "{ps * 512} {pz * 512 + 512}" '
                                  f'"{(ps - 8) * 512} 4096"; do region_in_bounds $a && echo in || echo out; done').stdout
            self.assertIn('in\nout\nout\n', out)

    def test_a_region_installation_wins_over_the_partition(self):
        # boot/init looks at the region first (find_root_offset before gpt_root): so do the installers
        emmc = self.fake()
        ps, pz = self.PSTART, self.PSIZE
        self.part(2, 'mu300root', ps, pz)
        rstart = ((ps + pz) // 4096 + 1) * 4096 * 512
        self.ext4(emmc, rstart, blocks=500)
        self.ext4(emmc, ps * 512)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing; echo "IN_PART=$IN_PART"').stdout
            self.assertIn(f'rc=0 OFF={rstart} SIZE={500 * 4096} existing=yes', out)
            self.assertIn('IN_PART=0', out)

    def test_a_partition_this_installer_cannot_use_says_why(self):
        emmc = self.fake()
        ps, pz = self.PSTART, self.PSIZE
        self.part(2, 'mu300root', ps, pz)
        self.ext4(emmc, ps * 512, label=b'rootfs')
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing; echo "PART_FS=$PART_FS"; '
                                  'region_part_refusal; echo " refused=$?"').stdout
            self.assertIn('existing=no', out)
            self.assertIn('PART_FS=ext4', out)
            self.assertIn('another Linux (ext4) filesystem', out)
            self.assertIn(' refused=0', out)
        # two of that name: neither is used, and the reason says so
        self.part(3, 'mu300root', ps + pz, 4096)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; echo "IN_PART=$IN_PART"; region_find_existing; region_part_refusal').stdout
            self.assertIn('IN_PART=0', out)
            self.assertIn('2 partitions named mu300root', out)
        # its image file in Android's data: said, not refused
        shutil.rmtree(self.tmp / 'sys/block/mmcblk0/mmcblk0p3')
        (self.tmp / 'data/mu300linux').mkdir(parents=True)
        (self.tmp / 'data/mu300linux/openwrt.ext4').write_bytes(b'')
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_image_note; echo " note=$?"').stdout
            self.assertIn('/data/mu300linux/openwrt.ext4', out)
            self.assertIn(' note=0', out)

    def test_the_probe_command_is_install_ps1s_and_has_no_double_quotes(self):
        # install.ps1 and uninstall.ps1 send the same command, and Windows PowerShell 5.1 drops double quotes on the
        # way to adb
        self.fake()
        for shell in self.each_shell():
            self.run_region(shell, 'region_probe')
            cmd = (self.tmp / 'su.log').read_text().splitlines()[0]
            self.assertNotIn('"', cmd)
            self.assertIn("(SuDo '" + cmd + "')", (TOP / 'install.ps1').read_text())
            self.assertIn("(SuDo '" + cmd + "')", (TOP / 'uninstall.ps1').read_text())
            (self.tmp / 'su.log').unlink()

    def test_probe(self):
        self.fake()
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe').stdout
            start = ((2048 + (4 << 21)) // 4096 + 1) * 4096
            end = (((16 << 21) - 34) // 4096 - 1) * 4096
            self.assertIn(f'rc=0 OFF={start * 512} SIZE={(end - start) * 512}', out)

    def test_existing_and_dirty(self):
        start = ((2048 + (4 << 21)) // 4096 + 1) * 4096 * 512
        emmc = self.fake(ext4_at=start)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing').stdout
            self.assertIn(f'OFF={start} SIZE={1000 * 4096} existing=yes', out)
        self.fake()
        with open(emmc, 'r+b') as f:
            f.seek(start + (512 << 10)); f.write(b'data' * 1024)    # inside the first of the 16 sampled MiB
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing; region_dirty').stdout
            self.assertIn('existing=no DIRTY=1', out)

    def test_no_answer_is_never_an_empty_region(self):
        """2026-10-16, F50-C: adb's server went away in the middle of --check; every read came back empty, and the
        region holding an installation was "existing: no", "free and empty" (DIRTY empty, compared as 0). In a real
        run that skips the update-or-wipe question and the overwrite one. No answer is status 2 now."""
        start = ((2048 + (4 << 21)) // 4096 + 1) * 4096 * 512
        self.fake(ext4_at=start)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; su_do() { :; }; region_find_existing').stdout
            self.assertIn('rc=2', out)
            self.assertIn('existing= ', out)
            out = self.run_region(shell, 'region_probe; su_do() { :; }; region_dirty').stdout
            self.assertIn('rc=2', out)
            self.assertIn('DIRTY=\n', out + '\n')
            # and an answer is still read as before
            out = self.run_region(shell, 'region_probe; region_find_existing').stdout
            self.assertIn(f'rc=0 OFF={start} SIZE={1000 * 4096} existing=yes', out)

    def test_a_table_that_ends_at_the_end_of_the_disk_is_an_empty_region_and_is_never_read(self):
        """Issue #65 (and #43, #52): the last partition ends 2 MiB before the end of the eMMC. The first boundary
        behind it is the end of the disk and the last one before the backup GPT lies before it: a negative size,
        and a probe of the region's superblock 2 bytes past the last sector - a dd that never returns on the
        device. The region is empty, and nothing past the disk's last sector is read; the fixed offset of the
        first releases (27762098176) lies beyond this 16 GiB disk and is not read either."""
        self.fake(full=True)
        disk = 16 << 21
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing').stdout
            self.assertIn(f'rc=0 OFF={disk * 512} SIZE=0 existing=no', out)
            for cmd in (self.tmp / 'su.log').read_text().splitlines():
                m = re.search(r'dd if=/dev/block/mmcblk0 bs=1 skip=(\d+) count=(\d+)', cmd)
                if m:
                    self.assertLessEqual(int(m.group(1)) + int(m.group(2)), disk * 512, cmd)
            (self.tmp / 'su.log').unlink()

    def test_the_legacy_offset_is_read_only_on_a_disk_that_holds_it(self):
        start = ((2048 + (4 << 21)) // 4096 + 1) * 4096 * 512
        self.fake(ext4_at=start)
        for shell in self.each_shell():
            self.run_region(shell, 'region_probe; region_find_existing')
            log = (self.tmp / 'su.log').read_text()
            self.assertIn(f'skip={start + 1080}', log)
            self.assertNotIn('skip=27762099256', log, 'the legacy offset lies beyond a 16 GiB disk')
            (self.tmp / 'su.log').unlink()


def bc_block(suffix, a, b):
    """A bootloader_control block of misc (AOSP layout) with these slot_info bytes for a and b."""
    x = bytearray(bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146'))
    x[0:4] = suffix
    x[12] = a
    x[14] = b
    x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
    return bytes(x)


class Model(ShellTest):
    """install.sh's model block: the F50 and the U30 Air as before, the ZTE V50 (FINDINGS 95) exactly V50 / MU3351 on
    ums9620 and said to be experimental, any other V50 refused without a question, an unknown model asked about."""
    SRC = (TOP / 'install.sh').read_text()

    def decide(self, shell, model, platform='ums9620', mode='prebuilt', answer='no'):
        m = re.search(r'# --- model begin\n(.*?)# --- model end', self.SRC, re.S)
        self.assertIsNotNone(m, 'install.sh has no model block')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; MODE={mode}; '
                'die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED "; read -r _a; eval "$1=\\$_a"; }; '
                'info() { :; }; '
                f'su_do() {{ [ "$1" = "getprop ro.board.platform" ] && echo "{platform}"; }}; '
                f'model="{model}"\n' + m.group(1) + 'echo "DEVICE=$DEVICE"')
        return self.sh(shell, code, stdin=answer + '\n').stdout

    def test_three_devices(self):
        for shell in self.each_shell():
            for model, want in (('F50 / MU300', 'f50'), ('ZTE MU300 / mu300', 'f50'), ('U30_Air / U30Air', 'u30air'),
                                ('V50 / MU3351', 'v50')):
                out = self.decide(shell, model)
                self.assertIn(f'DEVICE={want}', out, model)
                self.assertNotIn('ASKED', out)
                self.assertEqual('EXPERIMENTAL' in out, want == 'v50', model)

    def test_no_other_v50(self):
        for shell in self.each_shell():
            for model, platform in (('V50 / MU3351', 'mt6789'), ('V50 / MU3351', ''), ('V50 / P671F60', 'ums9620'),
                                    ('ZTE MU3351 / MU3351', 'ums9620')):
                out = self.decide(shell, model, platform)
                self.assertIn('DIE This looks like a ZTE V50', out, (model, platform))
                self.assertNotIn('ASKED', out)          # never offered as an F50
            # a V50 runs a mainline kernel: no local build of 5.4 for it
            self.assertIn('DIE The ZTE V50 runs a mainline kernel only', self.decide(shell, 'V50 / MU3351', mode='build'))
            # a Blade V50 phone is no V50: an unknown model, asked about as ever
            out = self.decide(shell, 'ZTE Blade V50 Design / P671F60')
            self.assertIn('ASKED', out)
            self.assertNotIn('DEVICE=', out)
            self.assertIn('DEVICE=f50', self.decide(shell, 'Pixel 7 / panther', answer='yes'))


class InstallSlot(ShellTest):
    """install.sh's slot block, cut out between its markers (issue #86): Linux goes on the slot Android is not on,
    boot_<that slot> is written with the image built for it and misc gets that slot's trial block - and the boot
    partition Android runs from is never written. su_do runs the device commands against files: by-name/boot_a,
    boot_b and misc, and /data/local/tmp; getprop answers the next line of the file slots on every call."""
    SRC = (TOP / 'install.sh').read_text()
    PS1 = (TOP / 'install.ps1').read_text()

    def setUp(self):
        super().setUp()
        self.byname = self.tmp / 'byname'
        self.byname.mkdir()
        (self.tmp / 't').mkdir()
        # adb push SRC DST: DST in the fake /data/local/tmp
        self.stub('adb', '[ "$1" = push ] && cp "$2" "$STUBLOG/t/$(basename "$3")"; exit 0')

    def device(self, *slots, same=False):
        for f in self.byname.iterdir():
            f.unlink()
        for f in (self.tmp / 't').iterdir():
            f.unlink()
        (self.byname / 'boot_a').write_bytes(b'A' * 65536)
        if same:
            (self.byname / 'boot_b').symlink_to(self.byname / 'boot_a')
        else:
            (self.byname / 'boot_b').write_bytes(b'B' * 65536)
        self.misc0 = bytes(2048) + bc_block(b'_a\0\0', 0x9f, 0x1e) + bytes(4096 - 2080)
        (self.byname / 'misc').write_bytes(self.misc0)
        (self.tmp / 'slots').write_text(''.join(s + '\n' for s in slots))
        (self.tmp / 'n').unlink(missing_ok=True)
        (self.tmp / 'su.log').write_text('')

    def image(self, built_for, block):
        """A boot image as build-boot-image.py leaves it: IMG, its .json and the trial block of its slot."""
        img = self.tmp / f'boot-linux-slot{built_for}.img'
        data = bytes(range(256)) * 128
        img.write_bytes(data)
        img.with_suffix('.json').write_text(json.dumps({'sha256': hashlib.sha256(data).hexdigest(),
                                                        'linux_slot': built_for}))
        img.with_suffix(f'.misc-slot-{built_for}-trial.bin').write_bytes(block)
        return img, data

    def run_slot(self, shell, tail):
        m = re.search(r'# --- slot begin\n(.*?)# --- slot end', self.SRC, re.S)
        self.assertIsNotNone(m, 'install.sh has no slot block')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; . "$TOP/tools/releases.sh"; MU300_LANG=en; T=/data/local/tmp; '
                'say() { echo "SAY $*"; }; die() { echo "DIE $*"; exit 1; }; '
                'su_do() { printf "%s\\n" "$1" >> "$STUBLOG/su.log"; case $1 in '
                '"getprop ro.boot.slot_suffix") n=$(( $(cat "$STUBLOG/n" 2>/dev/null || echo 0) + 1 )); '
                'echo $n > "$STUBLOG/n"; l=$(sed -n "${n}p" "$STUBLOG/slots"); '
                '[ $n -le $(wc -l < "$STUBLOG/slots") ] || l=$(tail -n 1 "$STUBLOG/slots"); printf "%s\\n" "$l" ;; '
                '*) sh -c "$(printf "%s" "$1" | sed -e "s|/dev/block/by-name|$STUBLOG/byname|g" '
                '-e "s|/data/local/tmp|$STUBLOG/t|g")" ;; esac; }\n'
                + m.group(1) + tail)
        return self.sh(shell, code).stdout

    def boots(self):
        return (self.byname / 'boot_a').read_bytes(), (self.byname / 'boot_b').read_bytes()

    def written(self):
        """The partitions su_do wrote with dd (of=...)."""
        return re.findall(r'of=/dev/block/by-name/(\w+)', (self.tmp / 'su.log').read_text())

    def test_android_on_a_puts_linux_on_b(self):
        block = bc_block(b'_b\0\0', 0x9e, 0x2f)
        for shell in self.each_shell():
            self.device('_a')
            img, data = self.image('b', block)
            out = self.run_slot(shell, f'slot_setup; echo "A=$ANDROID_SLOT L=$LINUX_SLOT"; write_linux_boot "{img}"; echo DONE')
            self.assertIn('A=a L=b', out)
            self.assertIn('Linux goes to slot b (boot_b)', out)
            self.assertIn('DONE', out, out)
            self.assertEqual(self.boots(), (b'A' * 65536, data))
            self.assertEqual((self.byname / 'misc').read_bytes()[2048:2080], block)
            self.assertEqual(self.written(), ['boot_b', 'misc'])

    def test_android_on_b_puts_linux_on_a_and_never_touches_boot_b(self):
        block = bc_block(b'_a\0\0', 0x2f, 0x9e)
        for shell in self.each_shell():
            self.device('_b')
            img, data = self.image('a', block)
            out = self.run_slot(shell, f'slot_setup; echo "A=$ANDROID_SLOT L=$LINUX_SLOT"; write_linux_boot "{img}"; echo DONE')
            self.assertIn('A=b L=a', out)
            self.assertIn('Linux goes to slot a (boot_a)', out)
            self.assertIn('DONE', out, out)
            self.assertEqual(self.boots(), (data, b'B' * 65536))           # Android's boot_b as it was
            self.assertEqual((self.byname / 'misc').read_bytes()[2048:2080], block)
            self.assertEqual(self.written(), ['boot_a', 'misc'])
            self.assertNotIn('boot_b', ' '.join(l for l in (self.tmp / 'su.log').read_text().splitlines()
                                                 if 'readlink' not in l))

    def test_unknown_slot_is_refused(self):
        for shell in self.each_shell():
            for suffix in ('', '_c', 'a', '_a_b'):
                self.device(suffix)
                out = self.run_slot(shell, 'slot_setup; echo SURVIVED')
                self.assertIn('DIE cannot tell which slot Android runs from', out, suffix)
                self.assertNotIn('SURVIVED', out)
                self.assertEqual(self.written(), [])

    def test_slot_read_again_before_writing(self):
        # Android ran from b when the image was built for a, and from a when it is written: a would be Android's
        for shell in self.each_shell():
            self.device('_b', '_a')
            img, _ = self.image('a', bc_block(b'_a\0\0', 0x2f, 0x9e))
            out = self.run_slot(shell, f'slot_setup; write_linux_boot "{img}"; echo SURVIVED')
            self.assertIn('DIE Android no longer runs from slot b; boot_a and misc were not changed', out)
            self.assertNotIn('SURVIVED', out)
            self.assertEqual(self.boots(), (b'A' * 65536, b'B' * 65536))
            self.assertEqual((self.byname / 'misc').read_bytes(), self.misc0)

    def test_image_for_the_other_slot_is_refused(self):
        # an image (and trial block) built for b next to an Android on b would arm Android's own slot
        for shell in self.each_shell():
            self.device('_b')
            img, _ = self.image('b', bc_block(b'_b\0\0', 0x9e, 0x2f))
            out = self.run_slot(shell, f'slot_setup; write_linux_boot "{img}"; echo SURVIVED')
            self.assertIn('DIE the boot image was not built for slot a', out)
            self.assertEqual(self.written(), [])
            self.assertEqual(self.boots(), (b'A' * 65536, b'B' * 65536))

    def test_boot_a_and_boot_b_the_same_partition(self):
        for shell in self.each_shell():
            self.device('_b', same=True)
            out = self.run_slot(shell, 'slot_setup; echo SURVIVED')
            self.assertIn('DIE boot_a and boot_b are the same partition', out)
            self.assertNotIn('SURVIVED', out)

    def test_release_check_only_for_linux_on_a(self):
        for shell in self.each_shell():
            for slot, rel, ok in (('a', 'v2026.10.09', False), ('a', 'v2026.09.28', False), ('a', 'v2026.10.10', True),
                                  ('a', 'v2026.10.14', True), ('a', 'v2027.01.01', True), ('a', 'custom', True),
                                  ('b', 'v2026.09.28', True),
                                  # the new names (FINDINGS 125): newer than every three-field tag, whatever the date
                                  ('a', 'v2026.10.16.1', True), ('a', 'v2026.10.01.1', True), ('a', 'v2026.10.16.0', True)):
                out = self.run_slot(shell, f'LINUX_SLOT={slot}; slot_release_ok {rel}; echo SURVIVED')
                self.assertEqual('SURVIVED' in out, ok, (slot, rel, out))
                if not ok:
                    self.assertIn('use v2026.10.10 or newer', out)

    def test_installer_builds_for_the_linux_slot_from_androids_boot(self):
        src = self.SRC
        build = src[src.index('python3 "$TOP/boot/build-boot-image.py"'):]
        build = build[:build.index('>/dev/null')]
        self.assertIn('--stock-boot "$WORK/dumps/boot_$ANDROID_SLOT.img"', build)
        self.assertIn('--linux-slot "$LINUX_SLOT"', build)
        self.assertIn('--out "$WORK/boot-linux-slot$LINUX_SLOT.img"', build)
        self.assertIn('dev_pull /dev/block/by-name/boot_$ANDROID_SLOT "$WORK/dumps/boot_$ANDROID_SLOT.img"', src)
        self.assertIn('write_linux_boot "$WORK/boot-linux-slot$LINUX_SLOT.img"', src)
        # (the pull is a function now, shared with --backup: both of its calls come after the slot is known)
        self.assertLess(src.index('\nslot_setup\n'), src.index('\npull_boot_and_misc\n'))
        self.assertLess(src.index('\nslot_setup\n'), src.index('[ $BACKUP_ONLY = 0 ] || backup_only'))
        self.assertEqual(src.count('pull_boot_and_misc\n'), 2)
        self.assertLess(src.index('slot_release_ok "$RELEASE"'), src.index("say \"$(t 'Downloading release {1}'"))
        # no slot is named by hand outside the slot block any more
        rest = src.replace(re.search(r'# --- slot begin\n.*?# --- slot end', src, re.S).group(0), '')
        for fixed in ('by-name/boot_a', 'by-name/boot_b', 'misc-slot-b-trial', 'boot-linux-slotb', "= _a ]"):
            self.assertNotIn(fixed, rest)

    def test_windows_installer_does_the_same(self):
        ps = self.PS1
        self.assertIn("'_b' { $ANDROID_SLOT = 'b'; $LINUX_SLOT = 'a' }", ps)
        self.assertIn("'_a' { $ANDROID_SLOT = 'a'; $LINUX_SLOT = 'b' }", ps)
        self.assertIn("'--linux-slot', $LINUX_SLOT", ps)
        self.assertIn("'--stock-boot', \"$Work\\dumps\\boot_$ANDROID_SLOT.img\"", ps)
        self.assertIn('of=/dev/block/by-name/boot_$LINUX_SLOT', ps)
        self.assertIn('misc-slot-$LINUX_SLOT-trial.bin', ps)
        for fixed in ('by-name/boot_b bs', 'of=/dev/block/by-name/boot_b', 'misc-slot-b-trial', 'boot-linux-slotb',
                      "-ne '_a'"):
            self.assertNotIn(fixed, ps)
        # the slot is read again right before boot_<Linux slot> is written
        self.assertLess(ps.index('Android no longer runs from slot {1}'), ps.index('of=/dev/block/by-name/boot_$LINUX_SLOT'))


class UninstallSlot(ShellTest):
    """uninstall.sh puts misc back to the slot Android runs from and copies Android's boot over the Linux slot's."""
    SRC = (TOP / 'uninstall.sh').read_text()

    def test_the_block_for_androids_slot(self):
        py = re.search(r"<<'PY'\n(.*?)\nPY\n", self.SRC, re.S).group(1)
        misc = self.tmp / 'misc'
        # misc as Linux left it: armed for its trial on the other slot
        for slot, live, want in (('a', bc_block(b'_b\0\0', 0x9e, 0x2f), bc_block(b'_a\0\0', 0x9f, 0x1e)),
                                 ('b', bc_block(b'_a\0\0', 0x2f, 0x9e), bc_block(b'_b\0\0', 0x1e, 0x9f))):
            misc.write_bytes(bytes(2048) + live + bytes(4096 - 2080))
            r = subprocess.run([sys.executable, '-', str(misc), slot], input=py, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), want.hex(), slot)

    def test_slot_follows_android(self):
        src = self.SRC
        self.assertIn('_b) ANDROID_SLOT=b; LINUX_SLOT=a ;;', src)
        self.assertIn('of=/dev/block/by-name/boot_$LINUX_SLOT', src)
        self.assertIn('if=/dev/block/by-name/boot_$ANDROID_SLOT', src)
        self.assertNotIn('of=/dev/block/by-name/boot_b', src)
        self.assertNotIn("= _a ]", src)
        ps = (TOP / 'uninstall.ps1').read_text()
        self.assertIn('of=/dev/block/by-name/boot_$LINUX_SLOT', ps)
        self.assertIn('Python $pyFile $miscTmp $ANDROID_SLOT', ps)
        self.assertNotIn("-ne '_a'", ps)


class ChooseSystems(ShellTest):
    """install.sh's choose_systems, cut out between its markers: which systems, which OpenWrt, which one boots."""
    SRC = (TOP / 'install.sh').read_text()

    def run_choose(self, shell, choice, answers='', preset='', size=100):
        m = re.search(r'# --- choose-systems begin\n(.*?)# --- choose-systems end', self.SRC, re.S)
        self.assertIsNotNone(m, 'install.sh has no choose-systems block')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; OWRT_VER=25.12.6; '
                f'NEED_OPENWRT=1; NEED_UBUNTU=2; NEED_BOTH=3; SIZE={size}; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'choice={choice}; ' + (f'MU300_OPENWRT={preset}; ' if preset else 'unset MU300_OPENWRT; ') +
                m.group(1) + 'choose_systems; echo "OSES=$OSES BOOT=$BOOT_OS"')
        return self.sh(shell, code, stdin=answers).stdout

    def test_both_with_the_control_panel(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 3, '2\nopenwrt-luci\n')
            self.assertIn('OSES=ubuntu openwrt-luci BOOT=openwrt-luci', out, shell)

    def test_plain_openwrt_asks_too(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 2, '1\n')
            self.assertIn('OSES=openwrt BOOT=openwrt', out, shell)
            self.assertIn('1) OpenWrt 25.12.6: the standard LuCI', out)
            self.assertNotIn('Which one should boot', out)

    def test_default_is_the_standard_openwrt(self):
        for shell in self.each_shell():
            self.assertIn('OSES=openwrt BOOT=openwrt', self.run_choose(shell, 2, '\n'), shell)

    def test_ubuntu_alone_never_asks_which_openwrt(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 1)
            self.assertNotIn('ASKED', out)
            self.assertIn('OSES=ubuntu BOOT=ubuntu', out)

    def test_preset_answers_without_the_question(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 2, preset='luci')
            self.assertNotIn('ASKED', out)
            self.assertIn('OSES=openwrt-luci BOOT=openwrt-luci', out, shell)
            out = self.run_choose(shell, 3, 'ubuntu\n', preset='plain')
            self.assertEqual(out.count('ASKED'), 1, out)
            self.assertIn('OSES=ubuntu openwrt BOOT=ubuntu', out)

    def test_bad_preset(self):
        for shell in self.each_shell():
            self.assertIn('DIE MU300_OPENWRT must be plain or luci', self.run_choose(shell, 2, preset='x'), shell)

    def test_boot_question_offers_the_chosen_names(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 3, '2\nubuntu\n')
            self.assertIn('OSES=ubuntu openwrt-luci BOOT=ubuntu', out, shell)

    def test_boot_answer_must_be_in_oses(self):
        for shell in self.each_shell():
            self.assertIn('DIE invalid system', self.run_choose(shell, 3, '2\nopenwrt\n'), shell)
            self.assertIn('DIE invalid system', self.run_choose(shell, 3, '1\nopenwrt-luci\n'), shell)

    def test_invalid_choice_and_small_device(self):
        for shell in self.each_shell():
            self.assertIn('DIE invalid choice', self.run_choose(shell, 9), shell)
            self.assertIn('DIE invalid choice', self.run_choose(shell, 2, '7\n'), shell)
            self.assertIn('DIE that choice needs', self.run_choose(shell, 3, '2\n', size=2), shell)
            self.assertIn('OSES=openwrt-luci', self.run_choose(shell, 2, '2\n', size=1), shell)

    def run_default(self, shell, size):
        m = re.search(r'# --- choose-systems begin\n(.*?)# --- choose-systems end', self.SRC, re.S)
        code = (f'NEED_OPENWRT=1; NEED_UBUNTU=2; NEED_BOTH=3; SIZE={size}; ' + m.group(1) + 'systems_default')
        return self.sh(shell, code).stdout.strip()

    def test_enter_chooses_what_fits(self):
        # FINDINGS 153: on the 804 MiB a make-room run leaves, Enter (3) stopped the reinstall with "needs 2400 MiB"
        for shell in self.each_shell():
            self.assertEqual([self.run_default(shell, s) for s in (100, 3, 2, 1)], ['3', '3', '1', '2'], shell)
        self.assertIn('ask choice "$(t \'Choice\')" "$(systems_default)" MU300_ANS_SYSTEMS', self.SRC)
        self.assertNotIn('ask choice "$(t \'Choice\')" 3 MU300_ANS_SYSTEMS', self.SRC)

    def test_a_choice_that_does_not_fit_names_the_one_that_does(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, 3, '1\n', size=1)
            self.assertIn('DIE that choice needs', out, shell)
            self.assertIn('Choose 2 (OpenWrt) instead: it fits here.', out, shell)
            # it stops before the question of which OpenWrt
            self.assertNotIn('ASKED', out, shell)
            out = self.run_choose(shell, 3, '1\n', size=2)
            self.assertIn('Choose 1 (Ubuntu) or 2 (OpenWrt) instead: one system fits here.', out, shell)
            self.assertNotIn('instead', self.run_choose(shell, 2, '1\n', size=0), shell)


if __name__ == '__main__':
    unittest.main()


class VpnModule(ShellTest):
    """install.sh's fetch_vpn_module: the VPN module's latest release (dikeckaan/mu300-linux-vpn), checked against its
    SHA256SUMS, or a copy on the computer (MU300_VPN_MODULE)"""

    def setUp(self):
        super().setUp()
        src = (TOP / 'install.sh').read_text()
        m = re.search(r'\nfetch_vpn_module\(\) \{\n.*?\n\}\n', src, re.S)
        self.assertIsNotNone(m, 'install.sh has no fetch_vpn_module')
        self.fn = m.group(0)
        self.server = self.tmp / 'server'
        self.server.mkdir()
        (self.server / 'mu300-linux-vpn.tar.gz').write_bytes(b'module v1')
        self.sums()
        # curl [-fsSL] -o OUT URL: from the server directory, by the URL's last part
        self.stub('curl', 'out=; url=; while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift ;; -*) ;; *) url=$1 ;; esac; '
                          'shift; done; echo "$url" >> "$STUBLOG/urls"; '
                          f'src="{self.server}/${{url##*/}}"; [ -f "$src" ] || exit 22; cat "$src" > "$out"')

    def sums(self, data=None):
        import hashlib
        h = hashlib.sha256(data if data is not None else (self.server / 'mu300-linux-vpn.tar.gz').read_bytes()).hexdigest()
        (self.server / 'SHA256SUMS').write_text(f'{h}  mu300-linux-vpn.tar.gz\n')

    def run_fetch(self, shell, **env):
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; WORK="{self.tmp}/work"; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'fetch() { curl -fL -o "$2" "$1"; }\n' + self.fn + '\nfetch_vpn_module; echo "rc=$? EXTRA_VPN=$EXTRA_VPN"')
        return self.sh(shell, code, **env)

    def test_downloads_and_verifies_the_latest_release(self):
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            out = self.run_fetch(shell).stdout
            self.assertIn(f'EXTRA_VPN={self.tmp}/work/vpn-module/mu300-linux-vpn.tar.gz', out)
            self.assertEqual((self.tmp / 'work/vpn-module/mu300-linux-vpn.tar.gz').read_bytes(), b'module v1')
            self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn/releases/latest/download/SHA256SUMS',
                          (self.tmp / 'urls').read_text())
            out = self.run_fetch(shell, MU300_VPN_URL='http://10.0.0.2/m').stdout
            self.assertIn('rc=0', out)
            self.assertIn('http://10.0.0.2/m/SHA256SUMS', (self.tmp / 'urls').read_text())

    def test_a_bad_checksum_dies(self):
        self.sums(b'something else')
        for shell in self.each_shell():
            out = self.run_fetch(shell).stdout
            self.assertIn('DIE', out)
            self.assertIn('checksum mismatch', out)

    def test_a_copy_on_the_computer(self):
        copy = self.tmp / 'copy'
        copy.mkdir()
        (copy / 'mu300-linux-vpn.tar.gz').write_bytes(b'module v1')
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            out = self.run_fetch(shell, MU300_VPN_MODULE=copy / 'mu300-linux-vpn.tar.gz').stdout
            self.assertIn(f'EXTRA_VPN={copy}/mu300-linux-vpn.tar.gz', out)
            self.assertFalse((self.tmp / 'urls').exists())
            # checked against a SHA256SUMS next to it
            (copy / 'SHA256SUMS').write_text('0' * 64 + '  mu300-linux-vpn.tar.gz\n')
            self.assertIn('DIE', self.run_fetch(shell, MU300_VPN_MODULE=copy / 'mu300-linux-vpn.tar.gz').stdout)
            (copy / 'SHA256SUMS').unlink()
            self.assertIn('DIE', self.run_fetch(shell, MU300_VPN_MODULE=copy / 'nosuch.tar.gz').stdout)


class VpnOnInTheReplacedSystem(ShellTest):
    """install.sh over an installation whose VPN is on: tools/storage.sh's vpn_on_systems finds it through a read-only
    mount (":ks" when its kill switch is on too), the VPN question's default becomes yes (no otherwise), and "no" with
    the kill switch on is refused before anything is written"""

    def run_probe(self, shell, existing='yes', on=('ubuntu',), ks=(), sd_mode=0, oses='ubuntu openwrt'):
        # su_do: the device side - its command line is recorded, and run against a fake mounted filesystem with the
        # images' lib/vpn-orphan.sh (pushed to /data/local/tmp/ on the device)
        root = self.tmp / 'probe'
        for o in ('ubuntu', 'openwrt'):
            (root / o / 'etc/mu300').mkdir(parents=True, exist_ok=True)
            (root / o / 'etc/mu300/vpn.conf').write_text(f'ENABLE={1 if o in on else 0}\nKILL_SWITCH={1 if o in ks else 0}\n')
        lib = TOP / 'rootfs/overlay/opt/mu300/lib/vpn-orphan.sh'
        self.stub('adb', 'echo "adb $*" >> "$STUBLOG/calls"')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; . "$TOP/tools/releases.sh"; MU300_LANG=en; T=/data/local/tmp; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                f'su_do() {{ echo "$1" > "{self.tmp}/su"; '
                f'eval "$(printf "%s" "$1" | sed "s|^.*&& {{|{{|; s|/data/local/tmp/vpn-orphan.sh|{lib}|; '
                f's|/data/local/tmp/mu300probe|{root}|g; s|sh /data/local/tmp/android-mount-mu300root.sh -u [^;]*;||")"; }}; '
                f'existing={existing}; OFF=123; SIZE=456; SD_MODE={sd_mode}; SD_DEV=/dev/block/mmcblk1p1; OSES="{oses}"; '
                '. "$TOP/tools/storage.sh"; echo "probe=[$(vpn_on_systems "$OSES" | tr "\\n" " ")]"')
        return self.sh(shell, code)

    def test_the_probe(self):
        for shell in self.each_shell():
            for on, ks, want in ((('ubuntu',), (), 'probe=[ubuntu ]'), ((), (), 'probe=[]'),
                                 (('ubuntu', 'openwrt'), ('openwrt',), 'probe=[ubuntu openwrt:ks ]')):
                r = self.run_probe(shell, on=on, ks=ks)
                self.assertIn(want, r.stdout, (on, ks, r.stderr))
                su = (self.tmp / 'su').read_text()
                self.assertIn('MU300_OFF=123 MU300_SIZE=456 MU300_RO=1 sh /data/local/tmp/android-mount-mu300root.sh', su)
                self.assertIn('. /data/local/tmp/vpn-orphan.sh', su)
                self.assertIn('android-mount-mu300root.sh -u /data/local/tmp/mu300probe', su)
                self.assertNotIn("'", su)                       # su_do wraps the command in single quotes
            self.assertIn('vpn-orphan.sh', (self.tmp / 'calls').read_text())     # pushed along
            # only the systems being installed count
            self.assertIn('probe=[]', self.run_probe(shell, on=('openwrt',), oses='ubuntu').stdout)

    def test_the_card_and_no_installation(self):
        for shell in self.each_shell():
            self.run_probe(shell, sd_mode=1)
            self.assertIn('MU300_SD_DEV=/dev/block/mmcblk1p1 MU300_RO=1', (self.tmp / 'su').read_text())
            (self.tmp / 'su').unlink()
            self.assertIn('probe=[]', self.run_probe(shell, existing='no').stdout)
            self.assertFalse((self.tmp / 'su').exists())          # nothing to look at: nothing mounted

    def question(self, shell, probe, answer):
        src = (TOP / 'install.sh').read_text()
        block = src[src.index('vx_default=no\n'):src.index('\n[ -n "$PKG" ] || KERNEL=5.4\n')]
        block = block[:block.index('\nEXTRA_VPN=')]
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; OSES="ubuntu openwrt"; '
                'die() { echo "DIE $*"; exit 1; }; '
                f'vpn_on_systems() {{ printf "%s" "{probe}" | tr " " "\\n"; }}; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }\n'
                + block + '\necho "vx=$vx"')
        return self.sh(shell, code, stdin=answer + '\n').stdout

    def test_both_defaults(self):
        for shell in self.each_shell():
            out = self.question(shell, '', '')
            self.assertIn('ASKED[no]', out)
            self.assertIn('vx=no', out)
            self.assertNotIn('The VPN is on', out)
            out = self.question(shell, 'ubuntu', '')
            self.assertIn('The VPN is on in the system being replaced (ubuntu)', out)
            self.assertIn('ASKED[yes]', out)
            self.assertIn('vx=yes', out)
            # kill switch off: "no" stays possible
            self.assertIn('vx=no', self.question(shell, 'ubuntu', 'no'))

    def test_no_with_the_kill_switch_on_is_refused(self):
        for shell in self.each_shell():
            out = self.question(shell, 'ubuntu openwrt:ks', 'no')
            self.assertIn('The VPN is on in the system being replaced (ubuntu openwrt)', out)
            self.assertIn('DIE The VPN is on with its kill switch in openwrt', out)
            self.assertNotIn('vx=', out)
            self.assertIn('vx=yes', self.question(shell, 'openwrt:ks', ''))
