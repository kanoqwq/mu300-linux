"""The ZTE V50's cards (FINDINGS 204): ZTE's board variants, the card each SIM interface reaches, the boot-time switch
before the CP starts, and nothing of it on another board. No hardware: a fake sysfs and a uci stub."""
import json
import re
import unittest

from helpers import BIN, TOP, ShellTest

LIB = TOP / 'rootfs/overlay/opt/mu300/lib/v50-card.sh'
DRIVER = TOP / 'upstream/port/drivers/misc/zte-v50-sim.c'

UCI = r"""
[ "$1" != -q ] || shift
verb=$1; key=${2:-}
case $key in
 network.wan.sim_slot|network.wan.sim_slot=*) file="$STUBLOG/choice" ;;
 network.wan.v50_card|network.wan.v50_card=*) file="$STUBLOG/card" ;;
 *) file= ;;
esac
case $verb in
 get) [ -n "$file" ] && [ -f "$file" ] && cat "$file" ;;
 set) [ -n "$file" ] && printf '%s\n' "${key#*=}" > "$file" ;;
 delete) [ -n "$file" ] && rm -f "$file" ;;
 commit) true ;;
 *) exit 1;;
esac
"""


class CardLibrary(ShellTest):
    """v50-card.sh, kanoqwq's table of ZTE's B15 firmware."""

    def lib(self, code, **env):
        return self.sh(['sh'], f'. "{LIB}"; {code}', **env)

    def test_board_variants(self):
        want = {'0': '0 1 2', '1': '0 1 2 3', '2': '0 1 3', '3': '0 1'}
        for board, cards in want.items():
            self.assertEqual(self.lib(f'v50_cards {board}').stdout.strip(), cards)
        self.assertNotEqual(self.lib('v50_cards 4').returncode, 0)

    def test_card_map_shares_the_two_sim_interfaces(self):
        # the third built-in card shares SIM2, the external one SIM1: never a third or fourth modem slot
        want = {'0': '0 psim_select 0', '1': '1 chip_select 0', '2': '1 chip_select 1', '3': '0 psim_select 1'}
        for card, mapping in want.items():
            self.assertEqual(self.lib(f'v50_card_map 1 {card}').stdout.strip(), mapping)
        self.assertNotEqual(self.lib('v50_card_map 3 2').returncode, 0)   # two built-in cards only
        self.assertNotEqual(self.lib('v50_card_map 2 2').returncode, 0)   # no third built-in card
        self.assertNotEqual(self.lib('v50_card_map 9 0').returncode, 0)   # unknown board: nothing guessed

    def test_active_card(self):
        for args, card in (('0 1 0', '0'), ('0 0 1', '3'), ('1 0 1', '1'), ('1 1 0', '2')):
            self.assertEqual(self.lib(f'v50_active_card {args}').stdout.strip(), card)
        self.assertNotEqual(self.lib('v50_active_card 0 0 x').returncode, 0)

    def test_board_id_sources(self):
        root = self.tmp / 'root'
        (root / 'proc/device-tree/chosen').mkdir(parents=True)
        (root / 'proc/cmdline').write_text('console=ttyS1 root=/dev/mmcblk0p1\n')
        (root / 'proc/device-tree/chosen/bootargs').write_bytes(b'androidboot.slot_suffix=_a androidboot.zte_boardid=2\0')
        self.assertEqual(self.lib('v50_board_id', MU300_SYSROOT=root).stdout.strip(), '2')
        (root / 'proc/driver').mkdir()
        (root / 'proc/driver/board_id').write_text('3\n')
        self.assertEqual(self.lib('v50_board_id', MU300_SYSROOT=root).stdout.strip(), '3')
        (root / 'proc/driver/board_id').write_text('7\n')
        self.assertNotEqual(self.lib('v50_board_id', MU300_SYSROOT=root).returncode, 0)


class V50Selection(ShellTest):
    def setUp(self):
        super().setUp()
        self.device = 'v50'
        self.stub('mu300-device', 'echo "$(cat "$STUBLOG/device")"')
        (self.tmp / 'device').write_text('v50\n')
        self.stub('uci', UCI)
        self.stub('logger', 'shift 2; echo "$*" >> "$STUBLOG/syslog"')
        self.choice = self.tmp / 'choice'
        self.card = self.tmp / 'card'
        self.mux = self.tmp / 'sys/bus/platform/drivers/zte-v50-sim/sim_gpio_ctl'
        self.mux.mkdir(parents=True)
        for name, value in (('chip_select', '0'), ('psim_select', '0'), ('psim_present', '1')):
            (self.mux / name).write_text(value + '\n')
        self.board(3)

    def board(self, n):
        (self.tmp / 'proc/driver').mkdir(parents=True, exist_ok=True)
        (self.tmp / 'proc/driver/board_id').write_text(f'{n}\n')

    def sim(self, *args, shell=('sh',), **env):
        env.setdefault('MU300_SIM_ROOT', self.tmp)
        env.setdefault('MU300_SYSROOT', self.tmp)
        env.setdefault('MU300_DEVICE_CMD', self.stubs / 'mu300-device')
        env.setdefault('MU300_SIM_CONFIG_LIB', TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh')
        env.setdefault('MU300_V50_CARD_LIB', LIB)
        return self.script(list(shell), BIN / 'mu300-sim', *args, **env)

    def status(self):
        r = self.sim('status', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_status_reports_cards_without_an_at_command(self):
        self.stub('mu300-at', 'echo called >> "$STUBLOG/at"; exit 1')
        self.board(1)
        (self.mux / 'psim_select').write_text('1\n')
        st = self.status()
        self.assertEqual(st['slot1_supported'], 1)
        self.assertTrue(st['capabilities']['boot_selection'])
        v = st['v50']
        self.assertEqual((v['board_id'], v['cards'], v['card'], v['saved_card'], v['switches']), (1, [0, 1, 2, 3], 3, None, True))
        self.assertFalse((self.tmp / 'at').exists())

    def test_status_text(self):
        self.board(1)
        self.card.write_text('2\n')
        (self.mux / 'psim_select').write_text('1\n')
        r = self.sim('status')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('card in use: 3\nnext boot card: 2\n', r.stdout)

    def test_card_saves_card_and_its_slot(self):
        self.board(0)
        for shell in self.each_shell():
            r = self.sim('card', '2', shell=shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.choice.read_text(), self.card.read_text()), ('1\n', '2\n'))
            self.assertIn('after a reboot', r.stdout)
            # the levels are not touched now: the modem is running; the switch is for the next boot
            self.assertEqual((self.mux / 'chip_select').read_text(), '0\n')
            st = self.status()
            self.assertEqual((st['default'], st['v50']['saved_card']), (2, 2))

    def test_card_refuses_what_the_board_has_not(self):
        r = self.sim('card', '3')   # board 3: two built-in cards, no tray
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('has no card 3', r.stderr)
        self.assertFalse(self.card.exists())
        self.assertFalse(self.choice.exists())
        (self.tmp / 'proc/driver/board_id').write_text('5\n')
        r = self.sim('card', '0')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.card.exists())

    def test_card_needs_the_driver(self):
        for f in self.mux.iterdir():
            f.unlink()
        self.mux.rmdir()
        r = self.sim('card', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('zte-v50-sim', r.stderr)
        self.assertFalse(self.card.exists())

    def test_default_keeps_a_card_on_the_same_slot_only(self):
        self.board(1)
        self.assertEqual(self.sim('card', '3').returncode, 0)   # external: SIM1
        self.assertEqual(self.sim('default', '1').returncode, 0)
        self.assertEqual(self.card.read_text(), '3\n')
        self.assertEqual(self.sim('default', '2').returncode, 0)
        self.assertFalse(self.card.exists())
        self.assertEqual(self.choice.read_text(), '1\n')

    def test_card_none_forgets_the_card(self):
        self.board(1)
        self.assertEqual(self.sim('card', '2').returncode, 0)
        self.assertEqual(self.sim('card', 'none').returncode, 0)
        self.assertFalse(self.card.exists())
        self.assertEqual(self.choice.read_text(), '1\n')

    def boot(self):
        r = self.sim('v50-boot')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_boot_sets_the_switch_before_the_cp_and_drops_the_old_identity(self):
        self.board(1)
        ident = self.tmp / 'etc/unisoc-modem'
        ident.mkdir(parents=True)
        (ident / 'sim.json').write_text('{}')
        (ident / 'sim-slot1.json').write_text('{}')
        self.card.write_text('3\n')
        self.choice.write_text('0\n')
        self.boot()
        self.assertEqual((self.mux / 'psim_select').read_text(), '1\n')
        self.assertEqual((self.mux / 'chip_select').read_text(), '0\n')
        self.assertFalse((ident / 'sim.json').exists())          # the previous card's identity on SIM1
        self.assertTrue((ident / 'sim-slot1.json').exists())     # SIM2's card did not change
        # once a boot: a respawned android-vendor-start does not switch a powered card
        (self.mux / 'psim_select').write_text('0\n')
        self.boot()
        self.assertEqual((self.mux / 'psim_select').read_text(), '0\n')

    def test_boot_on_the_right_level_writes_nothing(self):
        self.card.write_text('1\n')
        self.choice.write_text('1\n')
        ident = self.tmp / 'etc/unisoc-modem'
        ident.mkdir(parents=True)
        (ident / 'sim-slot1.json').write_text('{}')
        (self.mux / 'chip_select').chmod(0o444)
        self.boot()
        self.assertTrue((ident / 'sim-slot1.json').exists())

    def test_boot_leaves_the_switches_without_a_choice_or_a_valid_one(self):
        cases = [
            ('', '0', 3),          # no card chosen: the bootloader's routing, as before this existed
            ('2', '1', 3),         # board 3 has no third card
            ('3', '1', 1),         # the card is on SIM1, SIM2 chosen for the boot (WAN page)
            ('3', '0', 7),         # a board ID ZTE's firmware does not have
        ]
        for card, slot, board in cases:
            with self.subTest(card=card, slot=slot, board=board):
                (self.tmp / 'run/mu300/v50-card-applied').unlink(missing_ok=True)
                self.board(board)
                if card:
                    self.card.write_text(card + '\n')
                else:
                    self.card.unlink(missing_ok=True)
                self.choice.write_text(slot + '\n')
                self.boot()
                self.assertEqual((self.mux / 'psim_select').read_text(), '0\n')
                self.assertEqual((self.mux / 'chip_select').read_text(), '0\n')

    def live_stubs(self, fail_cpin=False):
        # mobile-data: radio-locked runs the command (the lock link names it), at answers OK and logs
        self.stub('mobile-data', r"""
case $1 in
 radio-locked) shift; ln -sfn $$ "$STUBLOG/run/mu300-radio-on.owner"; "$@"; rc=$?; rm -f "$STUBLOG/run/mu300-radio-on.owner"; exit $rc ;;
 at) echo "$2" >> "$STUBLOG/at.log"
     case $2 in 'AT+CPIN?') [ -e "$STUBLOG/nocpin" ] && { echo OK; exit 0; }; printf '+CPIN: READY\nOK\n' ;; *) echo OK ;; esac ;;
esac
""")
        self.stub('mu300-sim-radio', 'exit 0')
        if fail_cpin:
            (self.tmp / 'nocpin').touch()

    def live(self, card):
        return self.sim('card', card, '--now', shell=('bash',), MU300_MOBILE_DATA=self.stubs / 'mobile-data',
                        MU300_SIM_RADIO=self.stubs / 'mu300-sim-radio', MU300_V50_SWITCH_WAIT=0)

    def test_live_switch_in_zte_order(self):
        """kanoqwq's live switch (opt-in): SFUN=5, SFUN=3, the switch, SFUN=2, the card's answer, SFUN=4."""
        self.board(1)
        self.choice.write_text('0\n')
        self.live_stubs()
        r = self.live('3')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.mux / 'psim_select').read_text(), '1\n')
        self.assertEqual((self.tmp / 'at.log').read_text().split(),
                         ['AT+SFUN=5', 'AT+SFUN=3', 'AT+SFUN=2', 'AT+CPIN?', 'AT+SFUN=4'])
        self.assertEqual(self.card.read_text(), '3\n')

    def test_live_switch_failure_switches_back(self):
        self.board(1)
        self.choice.write_text('0\n')
        self.live_stubs(fail_cpin=True)
        r = self.live('3')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.mux / 'psim_select').read_text(), '0\n')
        self.assertFalse(self.card.exists())

    def test_live_switch_only_on_the_slot_in_use(self):
        self.board(1)
        self.choice.write_text('0\n')
        self.live_stubs()
        r = self.live('2')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('next boot', r.stderr)
        self.assertFalse((self.tmp / 'at.log').exists())

    def test_other_boards_unchanged(self):
        for device in ('f50', 'u30air'):
            with self.subTest(device=device):
                (self.tmp / 'device').write_text(device + '\n')
                (self.tmp / 'run/mu300/v50-card-applied').unlink(missing_ok=True)
                self.card.write_text('3\n')
                self.choice.write_text('0\n')
                self.board(1)
                self.boot()
                self.assertEqual((self.mux / 'psim_select').read_text(), '0\n')
                self.assertFalse((self.tmp / 'run/mu300/v50-card-applied').exists())
                self.assertNotEqual(self.sim('card', '0').returncode, 0)
                if device == 'f50':
                    self.assertNotIn('v50', self.status())


class Driver(unittest.TestCase):
    def test_probe_drives_nothing(self):
        src = DRIVER.read_text()
        self.assertEqual(src.count('devm_gpiod_get('), 3)
        self.assertEqual(len(re.findall(r'devm_gpiod_get\([^;]*GPIOD_ASIS\)', src)), 3)
        self.assertFalse('GPIOD_OUT' in src)
        self.assertFalse('/chosen' in src, 'no level from the bootloader\'s arguments')
        # bound only on the board with the V50's AW9523B
        self.assertIn('"awinic,aw9523b"', src)
        # the detect line is never written
        self.assertFalse('psim_present_store' in src)

    def test_built_in_and_hooked_up(self):
        self.assertIn('CONFIG_ZTE_V50_SIM=y', (TOP / 'upstream/mu300-mainline.config').read_text().splitlines())
        self.assertIn("'drivers/misc/Makefile', 'zte-v50-sim.o'", (TOP / 'upstream/port/install.py').read_text())

    def test_boot_hook_before_the_cp(self):
        s = (BIN / 'android-vendor-start').read_text()
        hook = s.index('v50-boot')
        self.assertLess(hook, s.index('exec /opt/mu300/bin/android-run modem_control'))
        self.assertIn('v50-boot </dev/null || true', s)


if __name__ == '__main__':
    unittest.main()
