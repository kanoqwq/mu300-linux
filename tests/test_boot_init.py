"""boot/init: where the Linux filesystem is looked for. The card functions are cut out of init between their
markers and run against files that stand in for block devices."""
import re
import shutil
import unittest

from helpers import TOP, ShellTest

INIT = (TOP / 'boot' / 'init').read_text()


def fake_ext4(path, label, magic=b'\x53\xef'):
    data = bytearray(4096)
    data[1080:1082] = magic
    data[1144:1144 + len(label)] = label.encode()
    path.write_bytes(bytes(data))


class SdRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- sd-root begin\n(.*?)# --- sd-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no sd-root block')
        e = re.search(r'# --- emmc begin\n(.*?)# --- emmc end', INIT, re.S)
        self.assertIsNotNone(e, 'boot/init has no emmc block')
        return e.group(1) + m.group(1)

    def run_fn(self, shell, call, glob):
        code = 'log() { :; }; mdev() { :; }\n' + self.functions() + f'\nMU300_SD_GLOB="{glob}"\n{call}\necho "rc=$?"'
        return self.sh(shell, code).stdout

    def test_label(self):
        fake_ext4(self.tmp / 'a', 'mu300sd')
        fake_ext4(self.tmp / 'b', 'mu300sd', magic=b'\x00\x00')
        for shell in self.each_shell():
            self.assertIn('mu300sd\nrc=0', self.run_fn(shell, f'ext4_label {self.tmp}/a', ''))
            self.assertEqual('rc=1', self.run_fn(shell, f'ext4_label {self.tmp}/b', '').strip())

    def test_partition_before_whole_card(self):
        fake_ext4(self.tmp / 'mmcblk1', 'mu300sd')
        fake_ext4(self.tmp / 'mmcblk1p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn(f'{self.tmp}/mmcblk1p1\nrc=0', out)

    def test_finds_card_under_another_number(self):
        fake_ext4(self.tmp / 'mmcblk2p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn('mmcblk2p1\nrc=0', out)

    def test_foreign_and_missing(self):
        fake_ext4(self.tmp / 'mmcblk1p1', 'photos')
        for shell in self.each_shell():
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1').strip())
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/none[1-9]').strip())

    def test_wait_gives_up(self):
        for shell in self.each_shell():
            out = self.run_fn(shell, 'sleep() { :; }; wait_sd_root 3', f'{self.tmp}/none[1-9]')
            self.assertEqual('rc=1', out.strip())

    # The wait beyond the first seconds: a fake /sys with the hosts and card devices of a scenario; sleep counts.
    def fake_sys(self, hosts=(), card=None):
        s = self.tmp / 'sys'
        for h in hosts:
            (s / 'class' / 'mmc_host' / h).mkdir(parents=True, exist_ok=True)
        if 'mmc0' in hosts:  # the eMMC, set up long before root selection
            e = s / 'class' / 'mmc_host' / 'mmc0' / 'mmc0:0001'
            e.mkdir()
            (e / 'type').write_text('MMC\n')
        (s / 'bus' / 'mmc' / 'devices').mkdir(parents=True, exist_ok=True)
        if card:  # card = (type, bound)
            c = s / 'bus' / 'mmc' / 'devices' / 'mmc1:aaaa'
            c.mkdir(parents=True, exist_ok=True)
            (c / 'type').write_text(card[0] + '\n')
            if card[1]:
                (c / 'driver').mkdir(exist_ok=True)
        return s

    def waited(self, shell, kernel, sys, extra=''):
        call = (f'n_sleep=0; sleep() {{ n_sleep=$((n_sleep + 1)); {extra} }}; uname() {{ echo {kernel}; }}; '
                f'MU300_SYS={sys}; wait_sd_root 3 6; r=$?; echo "slept=$n_sleep"; (exit $r)')
        return self.run_fn(shell, call, f'{self.tmp}/mmcblk[1-9]p1')

    def test_mainline_empty_slot_keeps_the_short_wait(self):
        # 6.18/7.2 poll the slot and find a card in about 2.5 s: a host without a card is no reason to wait longer
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '6.18.55', sys).strip())

    def test_card_found_but_not_set_up_yet_extends_the_wait(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'), card=('SD', False))
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '6.18.55', sys).strip())

    def test_card_that_is_set_up_and_foreign_does_not_extend(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'), card=('SD', True))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_vendor_kernel_with_a_card_host_extends_the_wait(self):
        # 5.4 has a card-detect line and no poll, and the card was once seen only at 23.75 s (FINDINGS 31j)
        sys = self.fake_sys(hosts=('mmc0', 'mmc1', 'mmc2'))
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_vendor_kernel_without_a_card_host_keeps_the_short_wait(self):
        # the U30 Air: the eMMC is its only host
        sys = self.fake_sys(hosts=('mmc0',))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_late_card_found_during_the_long_wait(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1', 'mmc2'))
        src = self.tmp / 'card'
        fake_ext4(src, 'mu300sd')
        appear = f'[ $n_sleep = 5 ] && cp {src} {self.tmp}/mmcblk1p1;'
        for shell in self.each_shell():
            (self.tmp / 'mmcblk1p1').unlink(missing_ok=True)
            out = self.waited(shell, '5.4.254-gb50db5b6224c', sys, appear)
            self.assertEqual(f'{self.tmp}/mmcblk1p1\nslept=5\nrc=0', out.strip())


    # The eMMC and the card by type, never by number (FINDINGS 31l: the card slot's host once took mmc0).
    def fake_disks(self, layout):
        """layout: {'mmcblk0': 'SD', 'mmcblk1': 'MMC', ...}; /sys/block/<n>/device/type in self.tmp/sys,
        the block devices as files in self.tmp/dev"""
        s, d = self.tmp / 'sys', self.tmp / 'dev'
        d.mkdir(exist_ok=True)
        for n, typ in layout.items():
            (s / 'block' / n / 'device').mkdir(parents=True, exist_ok=True)
            (s / 'block' / n / 'device' / 'type').write_text(typ + '\n')
        return s, d

    def by_type(self, shell, call, layout):
        s, d = self.fake_disks(layout)
        return self.run_fn(shell, f'MU300_SYS={s}; MU300_DEV={d}; {call}', '')

    def test_emmc_by_type(self):
        for shell in self.each_shell():
            for layout, want in (({'mmcblk0': 'MMC', 'mmcblk0boot0': 'MMC', 'mmcblk1': 'SD'}, 'mmcblk0'),
                                 ({'mmcblk0': 'SD', 'mmcblk1': 'MMC', 'mmcblk1boot0': 'MMC'}, 'mmcblk1')):
                shutil.rmtree(self.tmp / 'sys', ignore_errors=True)
                self.assertIn(f'{self.tmp}/dev/{want}\nrc=0', self.by_type(shell, 'emmc_dev', layout))
            # nothing in sysfs: mmcblk0, as before
            shutil.rmtree(self.tmp / 'sys', ignore_errors=True)
            self.assertIn(f'{self.tmp}/dev/mmcblk0\nrc=0', self.by_type(shell, 'emmc_dev', {}))

    def test_card_on_mmcblk0_is_found(self):
        # the card slot took mmc0: the card is mmcblk0, the eMMC mmcblk1
        layout = {'mmcblk0': 'SD', 'mmcblk1': 'MMC', 'mmcblk1boot0': 'MMC'}
        self.fake_disks(layout)
        fake_ext4(self.tmp / 'dev' / 'mmcblk0p1', 'mu300sd')
        fake_ext4(self.tmp / 'dev' / 'mmcblk1p1', 'mu300sd')   # never looked at: the eMMC
        for shell in self.each_shell():
            self.assertIn(f'{self.tmp}/dev/mmcblk0p1\nrc=0', self.by_type(shell, 'find_sd_root', layout))

    def test_the_emmc_is_never_taken_for_the_card(self):
        layout = {'mmcblk0': 'SD', 'mmcblk1': 'MMC'}
        self.fake_disks(layout)
        fake_ext4(self.tmp / 'dev' / 'mmcblk0p1', 'photos')
        fake_ext4(self.tmp / 'dev' / 'mmcblk1p1', 'mu300sd')
        fake_ext4(self.tmp / 'dev' / 'mmcblk1', 'mu300sd')
        for shell in self.each_shell():
            self.assertEqual('rc=1', self.by_type(shell, 'find_sd_root', layout).strip())

    def test_card_on_mmcblk1_still_found(self):
        layout = {'mmcblk0': 'MMC', 'mmcblk1': 'SD'}
        self.fake_disks(layout)
        fake_ext4(self.tmp / 'dev' / 'mmcblk1', 'mu300sd')   # the whole card, no partition table
        for shell in self.each_shell():
            self.assertIn(f'{self.tmp}/dev/mmcblk1\nrc=0', self.by_type(shell, 'find_sd_root', layout))

    def test_card_coming_under_any_host_number(self):
        # a card on mmc0 whose block device is still being set up extends the wait as one on mmc1 did
        s = self.tmp / 'sys'
        c = s / 'bus' / 'mmc' / 'devices' / 'mmc0:aaaa'
        c.mkdir(parents=True)
        (c / 'type').write_text('SD\n')
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '6.18.55', s).strip())

    def test_vendor_kernel_card_host_under_any_number(self):
        # 5.4 with the eMMC on mmc1 and the card host on mmc0: still a card host, still the long wait
        s = self.tmp / 'sys'
        (s / 'class' / 'mmc_host' / 'mmc0').mkdir(parents=True)
        e = s / 'class' / 'mmc_host' / 'mmc1' / 'mmc1:0001'
        e.mkdir(parents=True)
        (e / 'type').write_text('MMC\n')
        (s / 'bus' / 'mmc' / 'devices').mkdir(parents=True)
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', s).strip())


class Region(ShellTest):
    """The internal region is looked for on the eMMC ($EMMC), whatever its number, and never past its end."""

    def block(self):
        m = re.search(r'# --- region begin\n(.*?)# --- region end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no region block')
        return m.group(1)

    def setup_disk(self, name, parts_end, size, region_at=None):
        b = self.tmp / 'sys' / 'block' / name
        p = b / f'{name}p1'
        p.mkdir(parents=True)
        (p / 'start').write_text('34\n')
        (p / 'size').write_text(f'{parts_end - 34}\n')
        (b / 'size').write_text(f'{size // 512}\n')
        dev = self.tmp / name
        with open(dev, 'wb') as f:
            f.truncate(size)
            if region_at is not None:
                f.seek(region_at + 1080); f.write(b'\x53\xef')
                f.seek(region_at + 1144); f.write(b'mu300root')
        return dev

    def find(self, shell, emmc, root_offset):
        code = self.block() + f'\nMU300_SYS={self.tmp}/sys; EMMC={emmc}; ROOT_OFFSET={root_offset}; find_root_offset; echo "rc=$?"'
        return self.sh(shell, code).stdout

    def test_region_on_an_emmc_that_is_mmcblk1(self):
        # partitions end at sector 100: the region is at the first 2 MiB boundary after them
        emmc = self.setup_disk('mmcblk1', 100, 4 << 20, region_at=2 << 20)
        for shell in self.each_shell():
            self.assertEqual(f'{2 << 20}\nrc=0', self.find(shell, emmc, 3 << 20).strip())

    def test_gpt_partition_named_mu300root(self):
        # kanoqwq's desktop installer: the system in a GPT partition of that name, no region (#290). Found on the
        # eMMC only, and only with the mu300root filesystem in it
        e = re.search(r'# --- emmc begin\n(.*?)# --- emmc end', INIT, re.S).group(1)
        sysd, dev = self.tmp / 'sys', self.tmp / 'dev'
        dev.mkdir()
        for disk, typ, parts in (('mmcblk0', 'MMC', {'p5': 'cache', 'p60': 'mu300root'}),
                                 ('mmcblk1', 'SD', {'p1': 'mu300root'})):
            (sysd / 'block' / disk / 'device').mkdir(parents=True)
            (sysd / 'block' / disk / 'device' / 'type').write_text(typ + '\n')
            for suffix, name in parts.items():
                (sysd / 'block' / disk / (disk + suffix)).mkdir()
                (sysd / 'block' / disk / (disk + suffix) / 'uevent').write_text(f'DEVTYPE=partition\nPARTNAME={name}\n')
        fake_ext4(dev / 'mmcblk1p1', 'mu300root')
        code = e + self.block() + f'\nMU300_SYS={sysd}; MU300_DEV={dev}; gpt_root; echo "rc=$?"'
        for shell in self.each_shell():
            # the whole label: one that only starts with mu300root is not it (kanoqwq's 5161859d)
            for label, want in (('mu300root', f'{dev}/mmcblk0p60\nrc=0'), ('mu300sd', 'rc=1'), (None, 'rc=1'),
                                ('mu300rootfs', 'rc=1')):
                (dev / 'mmcblk0p60').unlink(missing_ok=True)
                if label:
                    fake_ext4(dev / 'mmcblk0p60', label)
                self.assertEqual(want, self.sh(shell, code).stdout.strip(), label)

    def test_never_reads_past_the_end(self):
        # the default offset lies beyond this disk: skipped, not read up to (busybox dd would read the whole disk)
        emmc = self.setup_disk('mmcblk1', 100, 4 << 20)
        for shell in self.each_shell():
            self.assertEqual('rc=1', self.find(shell, emmc, 27762098176).strip())


# Stand-ins for what root selection touches on the device. One card (mmcblk1p1) and one internal region; the
# scenario says which of them are there, when the card shows up and what is on it. mount and umount keep track
# of what is on /disk in $ON, and pick_root answers from that.
STUBS = r'''
log() { echo "$*" >> "$T/log"; }
find_sd_root() { [ "$CARD_NOW" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
wait_sd_root() { echo "wait $*" >> "$T/log"; [ "$CARD_LATE" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
find_root_offset() { [ "$INTERNAL" = 1 ] && { echo 4096; return 0; }; return 1; }
gpt_root() { [ "$PART" = 1 ] && { echo /dev/mmcblk0p60; return 0; }; return 1; }
root_fsck() { echo "fsck $1" >> "$T/log"; }
losetup() { case $1 in -f) echo /dev/loop0 ;; -d) echo "detach $2" >> "$T/log" ;; -o) echo "attach $4" >> "$T/log" ;; esac; }
cat() { case $1 in /sys/*) echo "$ROOT_OFFSET" ;; *) command cat "$@" ;; esac; }
dd() { [ "$INTERNAL" = 1 ] && printf '\123\357'; }
mount() {
    case $5 in
        /dev/loop*) [ "$INTERNAL" = 1 ] || return 1; ON=internal ;;
        /dev/mmcblk0p*) [ "$PART_MOUNTS" = 0 ] && return 1; ON=part ;;
        *) [ "$CARD_MOUNTS" = 0 ] && return 1; ON=card ;;
    esac
}
umount() { ON=; }
pick_root() { case $ON in internal) echo /disk/ubuntu ;; part) echo /disk/openwrt ;; card) [ "$CARD_EMPTY" = 1 ] || echo /disk/openwrt ;; esac; }
ON=
ROOT_OFFSET=27762098176
EMMC=${EMMC:-/dev/mmcblk0}
'''


class RootSelect(ShellTest):
    """Which filesystem ends up on /disk: the root-select block of init run against stubs."""

    def region(self):
        m = re.search(r'# --- root-select begin\n(.*?)# --- root-select end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no root-select block')
        code = m.group(1)
        # the device paths are only redirected here, never changed in init
        for path, repl in (('/run/mu300-root-dev', '$T/root-dev'), ('/run/rootmount.err', '$T/err'),
                           ('/disk/.mu300/root-on-sd', '$T/root-on-sd'),
                           ('/etc/mu300-desktop-root-mode', '$T/desktop-mode')):
            self.assertIn(path, code)
            code = code.replace(path, repl)
        return code

    def select(self, shell, marker=False, desktop_mode=None, **scenario):
        for f in ('log', 'root-dev', 'root-on-sd', 'desktop-mode'):
            (self.tmp / f).unlink(missing_ok=True)
        (self.tmp / 'log').touch()
        if marker:
            (self.tmp / 'root-on-sd').touch()
        if desktop_mode is not None:
            (self.tmp / 'desktop-mode').write_text(desktop_mode + '\n')
        code = STUBS + self.region() + '\necho "mounted=$root_mounted on=$ON disk-dev=$diskdev"'
        r = self.sh(shell, code, T=self.tmp, **scenario)
        self.assertEqual('', r.stderr)
        dev = self.tmp / 'root-dev'
        return r.stdout.strip(), dev.read_text().strip() if dev.exists() else '', (self.tmp / 'log').read_text()

    def test_card_only(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, INTERNAL=0)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('stage=sd-root dev=/dev/mmcblk1p1', log)
            self.assertNotIn('wait', log)

    def test_card_wins_over_internal(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, INTERNAL=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertNotIn('root-offset', log)

    def test_internal_without_marker_never_waits(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertNotIn('wait', log)

    def test_marker_and_late_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1, CARD_LATE=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('wait 8 30\ndetach /dev/loop0\nfsck /dev/mmcblk1p1\nstage=sd-root dev=/dev/mmcblk1p1', log)

    def test_marker_and_no_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('wait 8 30\nstage=sd-root-missing', log)

    def test_marker_and_late_card_that_is_empty(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1, CARD_LATE=1, CARD_EMPTY=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('stage=sd-root-empty dev=/dev/mmcblk1p1\nstage=sd-root-failed', log)

    def test_empty_card_falls_back_to_internal(self):
        # a card with the label but no system inside must not end in standalone mode while an internal system exists
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, CARD_EMPTY=1, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('stage=sd-root-empty dev=/dev/mmcblk1p1', log)
            self.assertNotIn('wait', log)

    def test_card_that_does_not_mount_falls_back_to_internal(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, CARD_MOUNTS=0, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertNotIn('wait', log)

    def test_nothing_at_all_waits_then_standalone(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0)
            self.assertEqual('mounted=0 on= disk-dev=', out)
            self.assertEqual('', dev)
            self.assertIn('stage=sd-wait (no internal system)\nwait 8 30', log)

    def test_internal_on_an_emmc_that_is_mmcblk1(self):
        # the card slot took mmc0: the region is attached from the eMMC, and root-dev names it
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=1, EMMC='/dev/mmcblk1')
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk1@4096', dev)
            self.assertIn('attach /dev/mmcblk1', log)

    def test_gpt_partition_when_there_is_no_region(self):
        # #290: another installer's partition named mu300root, mounted as it is; root-dev says internal ("@")
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0, PART=1)
            self.assertEqual('mounted=1 on=part disk-dev=/dev/mmcblk0p60', out)
            self.assertEqual('mmcblk0@mu300root', dev)
            self.assertIn('stage=root-part dev=/dev/mmcblk0p60', log)
            self.assertNotIn('attach', log)
            self.assertNotIn('wait', log)

    def test_region_wins_over_a_gpt_partition(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=1, PART=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertNotIn('root-part', log)

    def test_card_wins_over_a_gpt_partition(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, INTERNAL=0, PART=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)

    def test_desktop_partition_choice_comes_first(self):
        # kanoqwq's desktop installer: the owner chose the partition (part in the device segment, kept by updates).
        # It boots before a card and a region (from his 5161859d)
        for shell in self.each_shell():
            out, dev, log = self.select(shell, desktop_mode='part', CARD_NOW=1, INTERNAL=1, PART=1)
            self.assertEqual('mounted=1 on=part disk-dev=/dev/mmcblk0p60', out)
            self.assertEqual('mmcblk0@mu300root', dev)
            self.assertIn('stage=root-part dev=/dev/mmcblk0p60\nstage=root-part-chosen', log)
            self.assertNotIn('sd-root', log)
            # any other mode, or none: the usual order
            for mode in ('sd', 'legacy', ''):
                out, _, _ = self.select(shell, desktop_mode=mode, CARD_NOW=1, INTERNAL=1, PART=1)
                self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out, mode)

    def test_desktop_partition_choice_is_not_a_gate(self):
        # bootability first: a chosen partition that is missing or does not mount leaves the usual order, and a
        # partition that failed is not mounted a second time
        for shell in self.each_shell():
            out, _, log = self.select(shell, desktop_mode='part', CARD_NOW=1, INTERNAL=1, PART=0)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            out, dev, log = self.select(shell, desktop_mode='part', INTERNAL=1, PART=1, PART_MOUNTS=0)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertEqual(1, log.count('root-part-failed'))
            out, dev, log = self.select(shell, desktop_mode='part', INTERNAL=0, PART=1, PART_MOUNTS=0)
            self.assertEqual('mounted=0 on= disk-dev=', out)
            self.assertEqual(1, log.count('root-part-failed'))

    def test_gpt_partition_that_does_not_mount(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0, PART=1, PART_MOUNTS=0)
            self.assertEqual('mounted=0 on= disk-dev=', out)
            self.assertEqual('', dev)
            self.assertIn('stage=root-part-failed dev=/dev/mmcblk0p60', log)

    def test_fsck_before_every_mount(self):
        # the unmounted device is what e2fsck gets: the loop, the GPT partition, the card
        for shell in self.each_shell():
            _, _, log = self.select(shell, INTERNAL=1)
            self.assertIn('attach /dev/mmcblk0\nfsck /dev/loop0', log)
            _, _, log = self.select(shell, INTERNAL=0, PART=1)
            self.assertIn('fsck /dev/mmcblk0p60\nstage=root-part dev=/dev/mmcblk0p60', log)
            _, _, log = self.select(shell, CARD_NOW=1, INTERNAL=0)
            self.assertIn('fsck /dev/mmcblk1p1\nstage=sd-root dev=/dev/mmcblk1p1', log)

    def test_no_internal_and_late_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0, CARD_LATE=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('wait 8 30', log)


class Slot(ShellTest):
    """The slot block of init: which slot Linux booted from, which block restores Android, what userspace gets."""

    def block(self):
        m = re.search(r'# --- slot begin\n(.*?)# --- slot end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no slot block')
        return m.group(1)

    def run_slot(self, shell, cmdline=None, bootargs=None, image=None, call='linux_slot_detect; publish_slot',
                 blocks=('slot-a', 'slot-b-trial', 'slot-b', 'slot-a-trial'), power_conf=None):
        etc, run = self.tmp / 'etc', self.tmp / 'run'
        shutil.rmtree(run, ignore_errors=True)
        shutil.rmtree(etc, ignore_errors=True)
        etc.mkdir()
        for n in blocks:
            (etc / f'misc-bc-{n}.bin').write_text(n)
        if image:
            (etc / 'mu300-linux-slot').write_text(image + '\n')
        srcs = []
        for name, text in (('cmdline', cmdline), ('bootargs', bootargs)):
            if text is not None:
                (self.tmp / name).write_bytes(text.encode() + b'\0')
                srcs.append(str(self.tmp / name))
        code = (f'log() {{ echo "$*" >> "{self.tmp}/log"; }}\n'
                f'write_misc_bc() {{ echo "write $1" >> "{self.tmp}/log"; }}\n' + self.block() + f'\n{call}\n'
                'echo "linux=$LINUX_SLOT android=$ANDROID_SLOT"')
        (self.tmp / 'log').write_text('')
        newroot = self.tmp / 'newroot'
        shutil.rmtree(newroot, ignore_errors=True)
        if power_conf is not None:
            (newroot / 'etc/mu300').mkdir(parents=True)
            (newroot / 'etc/mu300/power.conf').write_text(power_conf)
        r = self.sh(shell, code, MU300_CMDLINE_SRC=' '.join(srcs) or f'{self.tmp}/none', MU300_ETC=etc, MU300_RUN=run,
                    MU300_NEWROOT=newroot)
        self.assertEqual(r.stderr, '')
        return r.stdout.strip(), (self.tmp / 'log').read_text(), run

    def test_cmdline_names_the_slot(self):
        for shell in self.each_shell():
            out, log, run = self.run_slot(shell, cmdline='console=x androidboot.slot_suffix=_a loglevel=5')
            self.assertEqual(out, 'linux=a android=b')
            self.assertIn('stage=linux-slot slot=a source=', log)
            self.assertEqual((run / 'mu300' / 'linux-slot').read_text(), 'a\n')
            self.assertEqual((run / 'mu300' / 'misc-bc-android.bin').read_text(), 'slot-b')
            self.assertEqual((run / 'mu300' / 'misc-bc-linux-trial.bin').read_text(), 'slot-a-trial')

    def test_bootargs_when_the_kernel_replaced_the_cmdline(self):
        for shell in self.each_shell():
            out, _, _ = self.run_slot(shell, cmdline='loglevel=5', bootargs='androidboot.slot_suffix=_b')
            self.assertEqual(out, 'linux=b android=a')

    def test_boot_mode_from_lk_bootargs(self):
        # LK's "charger" boot (a flat battery plugged in) opens our slot too: init says so for mu300-power
        for shell in self.each_shell():
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0 loglevel=3',
                                          bootargs='androidboot.slot_suffix=_b androidboot.mode=charger')
            self.assertEqual((run / 'mu300' / 'boot-mode').read_text().strip(), 'charger')
            self.assertTrue((run / 'mu300' / 'charging-boot').exists(), 'charging-boot should exist when mode is charger')
            self.assertIn('stage=boot-mode mode=charger', log)
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0',
                                          bootargs='androidboot.slot_suffix=_b androidboot.mode=normal')
            self.assertEqual((run / 'mu300' / 'boot-mode').read_text().strip(), 'normal')
            self.assertFalse((run / 'mu300' / 'charging-boot').exists(), 'charging-boot should not exist when mode is normal')
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0', bootargs=None)
            self.assertEqual((run / 'mu300' / 'boot-mode').read_text().strip(), 'normal')
            self.assertFalse((run / 'mu300' / 'charging-boot').exists(), 'charging-boot should not exist when mode defaults to normal')
            self.assertIn('stage=boot-mode mode=normal source=default', log)

    def test_power_on_charger_makes_no_charging_boot_marker(self):
        # mu300-power's POWER_ON_CHARGER=1 in the system that boots (FINDINGS 196): the mode is still charger, but the
        # Ubuntu units must not be held back
        for shell in self.each_shell():
            args = 'androidboot.slot_suffix=_b androidboot.mode=charger'
            _, log, run = self.run_slot(shell, cmdline='root=/dev/ram0', bootargs=args,
                                        power_conf='CHARGE_TO=80\nPOWER_ON_CHARGER=1\n')
            self.assertEqual((run / 'mu300' / 'boot-mode').read_text().strip(), 'charger')
            self.assertFalse((run / 'mu300' / 'charging-boot').exists())
            self.assertIn('mode=charger source=', log); self.assertIn(' power-on=1', log)
            _, log, run = self.run_slot(shell, cmdline='root=/dev/ram0', bootargs=args, power_conf='POWER_ON_CHARGER=0\n')
            self.assertTrue((run / 'mu300' / 'charging-boot').exists())
            self.assertNotIn('power-on', log)

    def test_image_then_default(self):
        for shell in self.each_shell():
            self.assertEqual(self.run_slot(shell, image='a')[0], 'linux=a android=b')
            out, log, _ = self.run_slot(shell)
            self.assertEqual(out, 'linux=b android=a')
            self.assertIn('source=default', log)

    def test_mismatch_is_logged_and_the_booted_slot_wins(self):
        for shell in self.each_shell():
            out, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', image='a')
            self.assertEqual(out, 'linux=b android=a')
            self.assertIn('stage=linux-slot-MISMATCH booted=b image=a', log)

    def test_restore_writes_androids_block(self):
        for shell in self.each_shell():
            _, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_a', call='linux_slot_detect; restore_android')
            self.assertIn(f'write {self.tmp}/etc/misc-bc-slot-b.bin', log)
            _, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', call='linux_slot_detect; restore_android')
            self.assertIn(f'write {self.tmp}/etc/misc-bc-slot-a.bin', log)

    def test_legacy_names_only_for_slot_b(self):
        # an older mu300-next-boot writes misc-bc-slot-a.bin for "android": with Linux on a that block would boot
        # Linux, marked successful, for ever - so with Linux on a the old names must not exist at all
        for shell in self.each_shell():
            _, _, run = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b')
            self.assertEqual((run / 'mu300' / 'misc-bc-slot-a.bin').read_text(), 'slot-a')
            self.assertEqual((run / 'mu300' / 'misc-bc-slot-b-trial.bin').read_text(), 'slot-b-trial')
            _, _, run = self.run_slot(shell, cmdline='androidboot.slot_suffix=_a')
            self.assertFalse((run / 'mu300' / 'misc-bc-slot-a.bin').exists())
            self.assertFalse((run / 'mu300' / 'misc-bc-slot-b-trial.bin').exists())

    def test_old_device_segment(self):
        # the update every existing installation takes: a new init in the generic segment, the device segment of
        # an older installer (only the slot-b pair, no etc/mu300-linux-slot), booted from b
        for shell in self.each_shell():
            out, _, run = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', blocks=('slot-a', 'slot-b-trial'))
            self.assertEqual(out, 'linux=b android=a')
            self.assertEqual((run / 'mu300' / 'linux-slot').read_text(), 'b\n')
            for n, want in (('misc-bc-android.bin', 'slot-a'), ('misc-bc-linux-trial.bin', 'slot-b-trial'),
                            ('misc-bc-slot-a.bin', 'slot-a'), ('misc-bc-slot-b-trial.bin', 'slot-b-trial')):
                self.assertEqual((run / 'mu300' / n).read_text(), want, n)
            _, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', blocks=('slot-a', 'slot-b-trial'),
                                      call='linux_slot_detect; restore_android')
            self.assertIn(f'write {self.tmp}/etc/misc-bc-slot-a.bin', log)


class Device(ShellTest):
    """Which device init takes (the device block) and the USB subnet that goes with it: the F50 and the U30 Air as
    before, labelled or not; the V50 (FINDINGS 95) by its AW9523B node, also under an older installer's f50 label,
    on 192.168.79.x; nothing ever takes one board for another."""
    def decide(self, shell, label=None, u30=False, v50=None, dt=True):
        d = self.tmp / 'dt'
        shutil.rmtree(d, ignore_errors=True)
        if dt:
            (d / 'soc' / 'ap-ahb' / 'i2c@2270000').mkdir(parents=True)
        if u30:
            (d / 'charger_policy_service').mkdir(parents=True)
        if v50:
            (d / 'soc' / v50 / 'i2c@2270000' / 'aw9523b_led@5b').mkdir(parents=True)
        f = self.tmp / 'mu300-device'
        f.unlink(missing_ok=True)
        if label is not None:
            f.write_text(label + '\n')
        m = re.search(r'# --- device begin\n(.*?)# --- device end\n', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no device block')
        net = re.search(r'^case \$DEVICE in u30air\) NET=.*$', INIT, re.M)
        self.assertIsNotNone(net)
        code = m.group(1) + net.group(0) + '\necho "$DEVICE $NET"'
        return self.sh(shell, code, MU300_DT=str(d), MU300_DEVICE_FILE=str(f)).stdout.strip()

    def test_three_boards(self):
        cases = [
            (dict(), 'f50 192.168.77'), (dict(label='f50'), 'f50 192.168.77'), (dict(label='v50'), 'f50 192.168.77'),
            (dict(u30=True), 'u30air 192.168.78'), (dict(label='u30air', u30=True), 'u30air 192.168.78'),
            (dict(label='u30air'), 'u30air 192.168.78'), (dict(label='f50', u30=True), 'f50 192.168.77'),
            (dict(label='v50', u30=True), 'u30air 192.168.78'), (dict(u30=True, v50='ap-ahb'), 'u30air 192.168.78'),
            (dict(v50='ap-ahb'), 'v50 192.168.79'), (dict(v50='soc:ap-ahb'), 'v50 192.168.79'),
            (dict(label='v50', v50='ap-ahb'), 'v50 192.168.79'), (dict(label='f50', v50='ap-ahb'), 'v50 192.168.79'),
            (dict(label='v50', dt=False), 'v50 192.168.79'), (dict(dt=False), 'f50 192.168.77'),
        ]
        for shell in self.each_shell():
            for kw, want in cases:
                self.assertEqual(self.decide(shell, **kw), want, kw)


class UsbId(ShellTest):
    """The gadget's MACs and USB serial: the device's serial number and its eMMC's, so that two devices restored from
    one backup (the same androidboot.serialno) still differ on one computer."""
    def functions(self):
        m = re.search(r'# --- usb-id begin\n(.*?)# --- usb-id end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no usb-id block')
        return m.group(1)

    def ident(self, shell, bootargs, cids=(), net='192.168.77'):
        b = self.tmp / 'bootargs'
        b.write_bytes(bootargs.encode() + b'\0')
        s = self.tmp / 'sys'
        (s / 'bus' / 'mmc' / 'devices').mkdir(parents=True, exist_ok=True)
        for i, (kind, cid) in enumerate(cids):
            d = s / 'bus' / 'mmc' / 'devices' / f'mmc{i}:000{i}'
            d.mkdir(exist_ok=True)
            (d / 'type').write_text(kind + '\n')
            (d / 'cid').write_text(cid + '\n')
        code = f'NET={net}; MU300_SYS={s}\n' + self.functions() + f'\nusb_id {self.tmp}/none {b}\necho "$USBID $MAC"'
        return self.sh(shell, code).stdout.strip()

    @staticmethod
    def mac(text, last='77'):
        import hashlib
        h = hashlib.md5((text + '\n').encode()).hexdigest()
        return f'02:50:{h[0:2]}:{h[2:4]}:{last}'

    def test_serial_and_emmc(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'console=ttyS1 androidboot.serialno=324950664950 androidboot.emmcid=2128e853 x=1')
            self.assertEqual(out, '324950664950-2128e853 ' + self.mac('324950664950-2128e853'))

    def test_clones_differ(self):
        for shell in self.each_shell():
            a = self.ident(shell, 'androidboot.serialno=324950664950 androidboot.emmcid=2128e853')
            b = self.ident(shell, 'androidboot.serialno=324950664950 androidboot.emmcid=7f0011aa')
            self.assertNotEqual(a.split()[1], b.split()[1])

    def test_no_emmcid_is_the_serial_alone(self):
        # not the eMMC's CID from /sys: init runs before the modules that find the eMMC (and on mainline it may or
        # may not be there yet), so the identity would change from boot to boot
        cids = (('MMC', 'ea010e325931384347102128e8539c00'),)
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.serialno=324950664950', cids)
            self.assertEqual(out, '324950664950 ' + self.mac('324950664950'))

    def test_emmcid_without_serial(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.emmcid=2128e853')
            self.assertEqual(out, '2128e853 ' + self.mac('2128e853'))

    def test_u30air_subnet_byte(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.serialno=323960377386 androidboot.emmcid=203edc81', net='192.168.78')
            self.assertEqual(out.split()[1], self.mac('323960377386-203edc81', '78'))

    def test_nothing_known_is_the_old_identity(self):
        for shell in self.each_shell():
            self.assertEqual(self.ident(shell, 'console=ttyS1'), self.mac(''))
            self.assertEqual(self.ident(shell, 'androidboot.serialno=1234'), '1234 ' + self.mac('1234'))

    def test_serial_string_uses_it(self):
        self.assertIn('echo "MU300LINUX${USBID:+-$USBID}" > "$g/strings/0x409/serialnumber"', INIT)
        self.assertIn('usb_id /proc/cmdline /proc/device-tree/chosen/bootargs', INIT)


class PickRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- pick-root begin\n(.*?)# --- pick-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no pick-root block')
        return m.group(1).replace('/disk', str(self.tmp / 'disk'))

    def system(self, name, init='sbin/init'):
        p = self.tmp / 'disk' / name / init
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\n'); p.chmod(0o755)

    def pick(self, shell):
        return self.sh(shell, self.functions() + '\npick_root').stdout.strip()

    def test_only_the_third_system(self):
        self.system('openwrt-luci')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_boot_os_chooses_it(self):
        self.system('ubuntu', 'lib/systemd/systemd'); self.system('openwrt'); self.system('openwrt-luci')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_never_boots_a_kept_copy(self):
        self.system('openwrt-luci.old')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci.old\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), '')


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_wait_is_written_once(self):
        # the 8 seconds are one constant, behind the two conditions the RootSelect tests exercise
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertEqual(body.count('wait_sd_root 8 30'), 1)


class GadgetHarness(ShellTest):
    """setup_usb_gadget, run against a directory that stands in for /config."""

    @staticmethod
    def block(name):
        m = re.search(rf'# --- {name} begin\n(.*?)# --- {name} end', INIT, re.S)
        assert m, f'boot/init has no {name} block'
        return m.group(1)

    def build(self, shell, usbnet, then='', no_rndis=False):
        """setup_usb_gadget with MU300_USBNET=USBNET (None: unset), then the shell code THEN; NO_RNDIS: a kernel
        without f_rndis, whose configfs refuses to make rndis.rn0."""
        body = self.block('usb-gadget') + self.block('usb-net')
        for path, repl in (('/config/usb_gadget', f'{self.tmp}/cfg/usb_gadget'), ('/sys/class/udc', f'{self.tmp}/udc'),
                           ('/sys/class/net/', f'{self.tmp}/net/'), ('/run/', f'{self.tmp}/run/')):
            body = body.replace(path, repl)
        (self.tmp / 'run').mkdir(exist_ok=True)
        g = self.tmp / 'cfg' / 'usb_gadget' / 'linux'
        code = ('log() { echo "$*" >> "$T/log"; }; mount() { :; }; sleep() { :; }; persist() { :; }\n'
                # the early DHCP server: recorded, never run
                'udhcpd() { echo "udhcpd $*" >> "$T/log"; }; kill() { echo "kill $*" >> "$T/log"; }\n'
                'killall() { echo "killall $*" >> "$T/log"; }\n'
                # configfs makes these itself when their parent is made, and takes them away with it
                'mkdir() { for d; do case $d in */functions/rndis.rn0) [ -z "$NO_RNDIS" ] || return 1 ;; esac; done\n'
                '    command mkdir "$@" || return; for d; do case $d in */functions/rndis.rn0) command mkdir -p "$d/os_desc/interface.rndis" ;; esac; done; }\n'
                'rmdir() { for d; do case $d in */functions/*) command rm -rf "$d" ;; *) command rmdir "$d" ;; esac; done; }\n'
                'mkdir -p "%s/cfg/usb_gadget/linux/os_desc"\n' % self.tmp +
                'ifconfig() { echo "ifconfig $*" >> "$T/log"; }; ip() { echo "ip $*" >> "$T/log"; }\n'
                f'MAC=02:00:00:00:00 NET=192.168.77 T={self.tmp} NO_RNDIS={"1" if no_rndis else ""}\n' + (f'MU300_USBNET="{usbnet}"\n' if usbnet is not None else '')
                + body + '\nsetup_usb_gadget\n' + then)
        r = self.sh(shell, code)
        self.assertEqual(0, r.returncode, r.stderr)
        return g

    def configs(self, g):
        return sorted(p.name for p in (g / 'configs').iterdir())

    def links(self, g, c):
        return {p.name: p.resolve().name for p in (g / 'configs' / c).iterdir() if p.is_symlink()}


class UsbGadget(GadgetHarness):
    def test_rndis_one_configuration_with_console(self):
        for shell in self.each_shell():
            g = self.build(shell, 'rndis')
            self.assertEqual(['c.1'], self.configs(g))
            self.assertEqual({'f1': 'rndis.rn0', 'f2': 'GS0'}, {k: v.replace('acm.', '') for k, v in self.links(g, 'c.1').items()})
            self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
            self.assertEqual('1', (g / 'os_desc' / 'use').read_text().strip())
            self.assertEqual('c.1', (g / 'os_desc' / 'c.1').resolve().name)
            self.assertFalse((g / 'functions' / 'ncm.usb0').exists())
            self.assertIn('RNDIS', (g / 'configs' / 'c.1' / 'strings' / '0x409' / 'configuration').read_text())
            self.assertIn('ifconfig rndis0 up', (self.tmp / 'log').read_text())
            self.tearDown(); self.setUp()

    def test_rndis_wins_whatever_the_order(self):
        for shell in self.each_shell():
            for usbnet in ('rndis ncm', 'ncm rndis'):
                g = self.build(shell, usbnet)
                self.assertEqual(['c.1'], self.configs(g), usbnet)
                self.assertEqual(['acm.GS0', 'rndis.rn0'], sorted(p.name for p in (g / 'functions').iterdir()), usbnet)
                self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
                self.tearDown(); self.setUp()

    def test_ncm_ecm_unchanged(self):
        for shell in self.each_shell():
            for usbnet in ('ncm ecm', None):
                g = self.build(shell, usbnet)
                self.assertEqual(['c.1'], self.configs(g))
                self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
                self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
                self.assertFalse((g / 'os_desc' / 'use').exists())
                self.assertFalse((g / 'functions' / 'rndis.rn0').exists())
                self.tearDown(); self.setUp()


class UsbNetPolicy(ShellTest):
    """usb_net_policy ROOTDIR: the gadget functions etc/mu300/usb-net of the chosen system asks for (D12, K7)."""

    def policy(self, shell, content, usbnet=None):
        f = self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net'
        f.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(content)
        env = {} if usbnet is None else {'MU300_USBNET': usbnet}
        r = self.sh(shell, GadgetHarness.block('usb-net') + f'\nusb_net_policy "{self.tmp}/root"; echo "rc=$?"', **env)
        self.assertEqual('', r.stderr)
        return r.stdout

    def test_each_value(self):
        for shell in self.each_shell():
            for value, functions in (('ncm', 'ncm ecm'), ('ecm', 'ecm ncm'), ('rndis', 'rndis ncm ecm'),
                                     ('ecm\n', 'ecm ncm')):
                self.assertEqual(f'{functions}\nrc=0\n', self.policy(shell, value), value)

    def test_garbage_and_missing_say_nothing(self):
        for shell in self.each_shell():
            for value in ('', 'RNDIS', 'ncm ecm', 'mode=ecm', 'ecm; reboot', '../x', None):
                self.assertEqual('rc=0\n', self.policy(shell, value), value)

    def test_command_line_wins(self):
        for shell in self.each_shell():
            for value in ('ncm', 'ecm', 'rndis'):
                self.assertEqual('rc=0\n', self.policy(shell, value, usbnet='ncm ecm'), value)
                self.assertEqual('rc=0\n', self.policy(shell, value, usbnet='rndis'), value)


class UsbNetRebind(GadgetHarness):
    """apply_usb_net ROOTDIR after pick_root: the gadget built at boot, rebuilt once when the system asks for other
    functions (D12, K7), and the applied marker (K8)."""

    def apply(self, shell, value, usbnet=None, no_rndis=False):
        (self.tmp / 'udc' / '25100000.dwc3').mkdir(parents=True, exist_ok=True)
        f = self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net'
        f.parent.mkdir(parents=True, exist_ok=True)
        if value is not None:
            f.write_text(value + '\n')
        return self.build(shell, usbnet, f'apply_usb_net "{self.tmp}/root"', no_rndis=no_rndis)

    def binds(self):
        return (self.tmp / 'log').read_text().count('stage=usb-bind-start')

    def marker(self):
        m = self.tmp / 'run' / 'mu300' / 'usb-net-applied'
        return m.read_text().strip() if m.exists() else None

    def test_rndis_rebuilds_the_single_configuration_gadget(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis')
            self.assertEqual(2, self.binds())
            self.assertEqual(['c.1'], self.configs(g))
            self.assertEqual({'f1': 'rndis.rn0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'rndis.rn0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
            self.assertEqual('1', (g / 'os_desc' / 'use').read_text().strip())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.assertIn('ifconfig rndis0 up', (self.tmp / 'log').read_text())
            self.assertEqual('rndis', self.marker())
            self.tearDown(); self.setUp()

    def test_rndis_on_a_kernel_without_it_falls_back_to_ncm(self):
        # the panel saved rndis, permanently: a kernel without f_rndis must still get a network function, every boot
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis', no_rndis=True)
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'ncm.usb0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
            self.assertFalse((g / 'os_desc' / 'use').exists())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.assertNotIn('usb-no-net-function', (self.tmp / 'log').read_text())
            self.assertIn('ifconfig usb0', (self.tmp / 'log').read_text())
            self.assertEqual('ncm', self.marker())   # what was made, not what was asked for
            self.tearDown(); self.setUp()

    def test_ecm_rebuilds_with_ecm(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'ecm')
            self.assertEqual(2, self.binds())
            self.assertEqual({'f1': 'ecm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'ecm.usb0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
            self.assertIn('stage=usb-net-policy ecm', (self.tmp / 'log').read_text())
            self.assertEqual('ecm', self.marker())
            self.tearDown(); self.setUp()

    def test_ncm_is_what_was_bound_no_rebind(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'ncm')
            self.assertEqual(1, self.binds())
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            # the setting is in effect: a one-shot choice is consumed all the same
            self.assertEqual('ncm', self.marker())
            self.tearDown(); self.setUp()

    def test_garbage_or_no_file_leaves_the_gadget(self):
        for shell in self.each_shell():
            for value in ('bogus', None):
                g = self.apply(shell, value)
                self.assertEqual(1, self.binds(), value)
                self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
                self.assertIsNone(self.marker())
                self.tearDown(); self.setUp()

    def test_no_controller_no_second_wait(self):
        for shell in self.each_shell():
            (self.tmp / 'root' / 'etc' / 'mu300').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net').write_text('ecm\n')
            g = self.build(shell, None, f'apply_usb_net "{self.tmp}/root"')
            log = (self.tmp / 'log').read_text()
            self.assertEqual(1, log.count('stage=usb-wait-udc'))
            self.assertIn('skipped (no udc)', log)
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertIsNone(self.marker())
            self.tearDown(); self.setUp()

    def test_command_line_wins(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis', usbnet='ncm ecm')
            self.assertEqual(1, self.binds())
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertIsNone(self.marker())
            self.tearDown(); self.setUp()


class EarlyDhcp(GadgetHarness):
    """K5: the host gets a lease the moment the gadget is bound, from a server on the gadget's netdev, on init's own
    subnet ($NET) and for 120 s only - the system may use another subnet, and its server takes over at the renewal."""

    def netdevs(self, *names):
        for n in names:
            (self.tmp / 'net' / n).mkdir(parents=True, exist_ok=True)

    def conf(self):
        return (self.tmp / 'run' / 'udhcpd-usb0.conf').read_text()

    def starts(self):
        return [l for l in (self.tmp / 'log').read_text().splitlines() if l.startswith('udhcpd ')]

    def test_ncm_serves_usb0(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.build(shell, None)
            conf = self.conf()
            for line in ('start 192.168.77.200', 'end 192.168.77.200', 'interface usb0', 'option router 192.168.77.1',
                         'option subnet 255.255.255.0', 'option lease 120'):
                self.assertIn(line + '\n', conf)
            self.assertNotIn('3600', conf)
            self.assertIn('ifconfig usb0 192.168.77.1 netmask 255.255.255.0 up', (self.tmp / 'log').read_text())
            self.assertEqual(['udhcpd -f ' + str(self.tmp / 'run' / 'udhcpd-usb0.conf')], self.starts())
            self.assertEqual('02:00:00:00:00:02', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.assertTrue((self.tmp / 'run' / 'udhcpd-usb0.pid').read_text().strip().isdigit())
            self.tearDown(); self.setUp()

    def test_rndis_serves_rndis0(self):
        for shell in self.each_shell():
            self.netdevs('rndis0')
            self.build(shell, 'rndis')
            self.assertIn('interface rndis0\n', self.conf())
            self.assertIn('ifconfig rndis0 192.168.77.1 netmask 255.255.255.0 up', (self.tmp / 'log').read_text())
            self.assertEqual('02:00:00:00:00:04', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.tearDown(); self.setUp()

    def test_no_netdev_no_server(self):
        for shell in self.each_shell():
            self.build(shell, None)
            self.assertEqual([], self.starts())
            self.assertFalse((self.tmp / 'run' / 'udhcpd-usb0.conf').exists())
            self.tearDown(); self.setUp()

    def test_a_rebuilt_gadget_replaces_the_server(self):
        # apply_usb_net builds the gadget a second time (D12): one server at a time, on the new netdev
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            (self.tmp / 'udc' / '25100000.dwc3').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net').write_text('rndis\n')
            self.build(shell, None, f'apply_usb_net "{self.tmp}/root"')
            log = (self.tmp / 'log').read_text().splitlines()
            starts = [i for i, l in enumerate(log) if l.startswith('udhcpd ')]
            kills = [i for i, l in enumerate(log) if l.startswith('kill ')]
            self.assertEqual(2, len(starts))
            self.assertEqual(1, len(kills), log)
            self.assertLess(starts[0], kills[0])
            self.assertLess(kills[0], starts[1])
            self.assertIn('interface rndis0\n', self.conf())
            self.assertEqual('02:00:00:00:00:04', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.tearDown(); self.setUp()

    def test_place_in_init(self):
        block = self.block('usb-gadget')
        self.assertIn('start $NET.200', block)
        self.assertIn('option lease 120', block)
        # gone before the system starts: its own server answers (Ubuntu's dnsmasq, OpenWrt's preinit then dnsmasq)
        self.assertEqual(1, INIT.count('killall udhcpd 2>/dev/null\n    mkdir -p /newroot/dev'))
        self.assertLess(INIT.index('killall udhcpd'), INIT.index('exec switch_root'))
        # the standalone fallback starts its own server on usb0 in place of the early one
        tail = INIT[INIT.index('stage=rootfs-unavailable'):]
        self.assertLess(tail.index('killall udhcpd'), tail.index('udhcpd /etc/udhcpd.conf'))
        self.assertIn('(sleep 300\n', INIT)


class UsbNetPlace(unittest.TestCase):
    def test_after_pick_root_before_switch_root(self):
        call = 'apply_usb_net "$rootdir"'
        self.assertEqual(1, INIT.count(call))
        self.assertLess(INIT.index('rootdir=$(pick_root)'), INIT.index(call))
        self.assertLess(INIT.index(call), INIT.index('exec switch_root'))
        # the gadget is still built before the root is looked for (D12: not the fork's order)
        self.assertLess(INIT.index('\nsetup_usb_gadget\n'), INIT.index('# --- root-select begin'))

    def test_one_gadget_builder(self):
        self.assertEqual(1, INIT.count('echo 0x0302 > "$g/bcdDevice"'))
        self.assertNotIn('cdc=', INIT)



class BootTries(ShellTest):
    """The backstop count of boots that never reached mu300-boot-ok (.mu300/boot-tries), against a misc file."""
    def functions(self):
        m = re.search(r'# --- boot-tries begin\n(.*?)# --- boot-tries end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no boot-tries block')
        return 'log() { echo "$*" >&2; }\n' + m.group(1)

    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        (self.disk / '.mu300').mkdir(parents=True)
        (self.disk / 'ubuntu/etc/mu300').mkdir(parents=True)
        (self.disk / 'ubuntu/etc/mu300/default-boot').write_text('linux\n')
        self.misc = self.tmp / 'misc'

    def lk(self, slot, tries, successful=False, magic=b'BCAB'):
        """misc with LK's bootloader_control: the Linux slot (SLOT) not successful (unless SUCCESSFUL), prio 15,
        TRIES left"""
        data = bytearray(4096)
        data[2048:2052] = b'_a\0\0'
        data[2052:2056] = magic
        mine = 15 | tries << 4 | (0x80 if successful else 0)
        data[2060] = 0x9e if slot == 'b' else mine
        data[2062] = mine if slot == 'b' else 0x9e
        self.misc.write_bytes(bytes(data))

    def boot(self, shell, slot='b', misc=True):
        code = (self.functions() + f'\nmisc={self.misc if misc else ""}; LINUX_SLOT={slot}\n'
                f'if boot_tries_exceeded "{self.disk}/ubuntu" "{self.disk}"; then echo android; else echo linux; fi')
        r = self.sh(shell, code)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip(), r.stderr

    def count(self):
        return (self.disk / '.mu300/boot-tries').read_text().strip()

    def test_counts_and_sends_the_boot_after_the_fifth_to_android(self):
        # LK that does not count (its tries stay where they were): the backstop is what brings Android back
        for shell in self.each_shell():
            for slot in ('a', 'b'):
                (self.disk / '.mu300/boot-tries').write_text('0\n')
                self.lk(slot, 6)
                for n in range(1, 6):
                    self.assertEqual(self.boot(shell, slot)[0], 'linux')
                    self.assertEqual(self.count(), str(n))
                self.assertEqual(self.boot(shell, slot)[0], 'android')
                self.assertEqual(self.count(), '0')

    def test_a_locked_slot_is_never_sent_to_android(self):
        # mu300-next-boot lock: the Linux slot is successful in LK's block and the wish is on the disk - no count, no
        # backstop, any number of boots (the bit alone keeps the backstop: test_a_successful_slot_keeps_the_backstop)
        (self.disk / '.mu300/boot-lock').write_text('')
        for shell in self.each_shell():
            for slot in ('a', 'b'):
                (self.disk / '.mu300/boot-tries').write_text('0\n')
                self.lk(slot, 6, successful=True)
                for _ in range(8):
                    out, err = self.boot(shell, slot)
                    self.assertEqual(out, 'linux')
                self.assertIn('stage=boot-locked', err)
                self.assertEqual(self.count(), '0')

    def test_the_standalone_fallback_stays_when_locked(self):
        tail = INIT[INIT.index('log "stage=rootfs-unavailable'):]
        tail = tail[:tail.index('ifconfig usb0')]
        self.assertLess(tail.index('if lk_locked; then'), tail.index('restore_android'))
        locked = tail[tail.index('if lk_locked; then'):tail.index('else')]
        self.assertNotIn('restore_android', locked)
        self.assertIn('touch /run/stay', locked)
        self.assertIn('/run/to-android', locked)

    def test_a_count_lk_already_acted_on_starts_again(self):
        # five boots that never reached mu300-boot-ok: LK went to Android by itself, the count stayed at 5. The
        # slot armed again from Android (su -c mu300-linux: tries 2, LK leaves 1) must boot Linux, not bounce
        # straight back to Android (seen on F50 #1)
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.lk('b', 1)
            out, err = self.boot(shell)
            self.assertEqual(out, 'linux')
            self.assertEqual(self.count(), '1')
            self.assertIn('stage=boot-tries-restart', err)

    def test_attempts_setting(self):
        (self.disk / '.mu300/boot-attempts').write_text('2\n')
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('2\n')
            self.lk('b', 3)
            self.assertEqual(self.boot(shell)[0], 'android')

    def test_unknown_misc_keeps_the_backstop(self):
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.assertEqual(self.boot(shell, misc=False)[0], 'android')

    def test_one_shot_mode_never_counts(self):
        (self.disk / 'ubuntu/etc/mu300/default-boot').unlink()
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('9\n')
            self.lk('b', 6)
            self.assertEqual(self.boot(shell)[0], 'linux')
            self.assertEqual(self.count(), '9')

    def test_lk_counting_down_from_n_plus_1(self):
        # LK armed with N+1 tries counts one down before each boot: it leaves 1 at boot N (a count of N, no restart)
        # and 0 at boot N+1, where the backstop goes to Android on the same boot LK would have
        for shell in self.each_shell():
            for slot in ('a', 'b'):
                (self.disk / '.mu300/boot-tries').write_text('0\n')
                for n in range(1, 6):
                    self.lk(slot, 6 - n)
                    out, err = self.boot(shell, slot)
                    self.assertEqual(out, 'linux', n)
                    self.assertNotIn('boot-tries-restart', err)
                    self.assertEqual(self.count(), str(n))
                self.lk(slot, 0)
                self.assertEqual(self.boot(shell, slot)[0], 'android')

    def test_a_count_lk_already_acted_on_starts_again_on_slot_a(self):
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.lk('a', 1)
            out, err = self.boot(shell, 'a')
            self.assertEqual(out, 'linux')
            self.assertEqual(self.count(), '1')
            self.assertIn('stage=boot-tries-restart', err)

    def test_lk_leaving_0_keeps_the_backstop(self):
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.lk('b', 0)
            self.assertEqual(self.boot(shell)[0], 'android')

    def test_no_bootloader_control_keeps_the_backstop(self):
        # a misc without the BCAB magic: its slot bytes are not LK's tries
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.lk('b', 1, magic=bytes(4))
            out, err = self.boot(shell)
            self.assertEqual(out, 'android')
            self.assertNotIn('boot-tries-restart', err)

    def test_a_successful_slot_keeps_the_backstop(self):
        # 0x9f: tries 1 but marked successful - LK does not count it down, so it says nothing about a fallback
        for shell in self.each_shell():
            (self.disk / '.mu300/boot-tries').write_text('5\n')
            self.lk('b', 1, successful=True)
            out, err = self.boot(shell)
            self.assertEqual(out, 'android')
            self.assertNotIn('boot-tries-restart', err)


if __name__ == '__main__':
    unittest.main()


class Fsck(ShellTest):
    """root_fsck: e2fsck only when the superblock records an error, and never a reason not to boot."""

    def code(self):
        m = re.search(r'# --- fsck begin\n(.*?)# --- fsck end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no fsck block')
        code = m.group(1)
        for path in ('/run/', '/proc/uptime'):
            self.assertIn(path, code)
        (self.tmp / 'run').mkdir(exist_ok=True)
        (self.tmp / 'uptime').write_text('100.00 50.00\n')
        return code.replace('/run/', '$T/run/').replace('/proc/uptime', '$T/uptime')

    def dev(self, state=1, errors=0):
        d = bytearray(4096)
        d[1080:1082] = b'\x53\xef'
        d[1082:1084] = state.to_bytes(2, 'little')
        d[1428:1432] = errors.to_bytes(4, 'little')
        (self.tmp / 'disk').write_bytes(bytes(d))
        return self.tmp / 'disk'

    def fsck_stub(self, body):
        f = self.tmp / 'e2fsck'
        f.write_text('#!/bin/sh\necho "$*" >> "$T/args"\n' + body + '\n')
        f.chmod(0o755)
        return f

    def run_fsck(self, shell, timeout=20, **env):
        for f in ('log', 'args'):
            (self.tmp / f).unlink(missing_ok=True)
        code = 'log() { echo "$*" >> "$T/log"; }\n' + self.code() + '\nroot_fsck "$DEV"; echo "rc=$?"'
        env.setdefault('MU300_FSCK_BIN', str(self.tmp / 'e2fsck'))
        r = self.sh(shell, code, T=self.tmp, DEV=self.tmp / 'disk', MU300_FSCK_TIMEOUT=str(timeout), **env)
        log = self.tmp / 'log'
        args = self.tmp / 'args'
        return (r.stdout.strip(), log.read_text() if log.exists() else '',
                args.read_text() if args.exists() else '')

    def test_clean_filesystem_runs_nothing(self):
        self.dev(state=1)
        self.fsck_stub('exit 0')
        for shell in self.each_shell():
            self.assertEqual(('rc=0', '', ''), self.run_fsck(shell))

    def test_error_bit_runs_e2fsck(self):
        self.dev(state=3, errors=258)
        self.fsck_stub('echo "mu300root: 12/16384 files (0.0% non-contiguous), 9545/65536 blocks"; exit 1')
        for shell in self.each_shell():
            out, log, args = self.run_fsck(shell)
            self.assertEqual('rc=0', out)
            self.assertEqual(f'-fy {self.tmp}/disk\n', args)
            self.assertIn(f'stage=fsck-start dev={self.tmp}/disk state=3 errors=258 timeout=20s', log)
            self.assertIn('stage=fsck-done', log)
            self.assertIn('rc=1 (errors corrected)', log)
            self.assertIn('9545/65536 blocks', log)
            self.assertIn('broken_system_clock = 1', (self.tmp / 'run' / 'e2fsck.conf').read_text())

    def test_failure_still_boots(self):
        self.dev(state=2)
        for rc, why in ((4, 'errors left uncorrected'), (8, 'operational error'), (12, 'operational error'),
                        (32, 'cancelled'), (16, 'usage or library error')):
            self.fsck_stub(f'exit {rc}')
            for shell in self.each_shell():
                out, log, _ = self.run_fsck(shell)
                self.assertEqual('rc=0', out)
                self.assertIn(f'stage=fsck-FAILED dev={self.tmp}/disk rc={rc} ({why}; booting anyway)', log)

    def test_timeout_stops_it_and_boots(self):
        self.dev(state=2)
        self.fsck_stub('exec sleep 30')
        for shell in self.each_shell():
            out, log, _ = self.run_fsck(shell, timeout=1)
            self.assertEqual('rc=0', out)
            self.assertIn('stage=fsck-TIMEOUT', log)
            self.assertIn('rc=143 (killed by signal 15; booting anyway)', log)

    def test_no_e2fsck_in_the_ramdisk(self):
        self.dev(state=2)
        for shell in self.each_shell():
            out, log, _ = self.run_fsck(shell, MU300_FSCK_BIN=str(self.tmp / 'none'))
            self.assertEqual('rc=0', out)
            self.assertIn('stage=fsck-unavailable', log)

    def test_force_and_off(self):
        self.fsck_stub('exit 0')
        for shell in self.each_shell():
            self.dev(state=1)
            out, log, args = self.run_fsck(shell, MU300_FSCK='force')
            self.assertIn('rc=0 (no errors)', log)
            self.dev(state=2)
            self.assertEqual(('rc=0', '', ''), self.run_fsck(shell, MU300_FSCK='off'))

    def test_not_an_ext4_read_error(self):
        (self.tmp / 'disk').unlink(missing_ok=True)
        self.fsck_stub('exit 0')
        for shell in self.each_shell():
            self.assertEqual(('rc=0', '', ''), self.run_fsck(shell))

    @unittest.skipUnless(shutil.which('mkfs.ext4') and shutil.which('debugfs') and shutil.which('e2fsck'),
                         'e2fsprogs is not installed')
    def test_real_e2fsck_clears_the_error(self):
        import subprocess
        img = self.tmp / 'disk'
        img.unlink(missing_ok=True)
        with open(img, 'wb') as f:
            f.truncate(16 << 20)
        subprocess.run(['mkfs.ext4', '-q', '-F', '-L', 'mu300root', str(img)], check=True)
        subprocess.run(['debugfs', '-w', '-R', 'ssv state 3', str(img)], check=True, capture_output=True)
        subprocess.run(['debugfs', '-w', '-R', 'ssv error_count 5', str(img)], check=True, capture_output=True)
        shell = self.shells[0]
        out, log, _ = self.run_fsck(shell, MU300_FSCK_BIN=shutil.which('e2fsck'))
        self.assertEqual('rc=0', out)
        self.assertIn('state=3 errors=5', log)
        self.assertIn('stage=fsck-done', log)
        data = img.read_bytes()[:4096]
        self.assertEqual(0, data[1082] & 2)
        self.assertEqual(b'\0\0\0\0', data[1428:1432])
        # the next boot reads one byte and runs nothing
        out, log, _ = self.run_fsck(shell, MU300_FSCK_BIN=shutil.which('e2fsck'))
        self.assertEqual(('rc=0', ''), (out, log))
