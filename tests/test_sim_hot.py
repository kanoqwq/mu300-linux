"""Broker-only SIM transactions (mu300-sim-u30, the U30 Air's live switch): the card in use off, then the other one on;
a switch is saved as the boot default only once the target has a bearer; a failed switch (or one without data)
preserves boot policy; a radio left in an unknown state restarts the device. Runs on macOS too: flock(1) and
/proc/uptime are not needed."""
import json
import os
import subprocess
import time
import signal
from helpers import BIN, TOP, ShellTest

# flock(1) for a host without it (macOS): the lock is taken on the inherited descriptor, and BSD/Linux flock locks
# belong to the open file description, so it holds for the shell that opened it after this exits.
FLOCK = r'''
nb=0; while [ "$#" -gt 1 ]; do case $1 in -n) nb=1;; esac; shift; done
exec python3 -c 'import fcntl, sys
try: fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | (fcntl.LOCK_NB if sys.argv[2] == "1" else 0))
except OSError: sys.exit(1)' "$1" "$nb"
'''
# netifd's wan: up with an address on the pinned card's bearer, unless the test says T_WAN=0 (never) or
# T_NODATA_SLOT=<slot> (not while that card is the pinned one). T_HELD=1: somebody took it down (the panel's button,
# ifdown, the data cap) and it stays down until an ifup: netifd's autostart is false.
UBUS = r'''
case "$*" in *network.interface.wan*)
    slot=$(cat "$STUBLOG/run/mu300/sim-slot")
    if [ "${T_HELD:-0}" = 1 ] && ! grep -qx up "$STUBLOG/net" 2>/dev/null; then
        printf '{ "up": false, "pending": false, "autostart": false, "ipv4-address": [ ] }\n'
    elif [ "${T_WAN:-1}" = 1 ] && [ "${T_NODATA_SLOT:-none}" != "$slot" ]; then printf '{ "up": true, "l3_device": "%s", "ipv4-address": [ { "address": "10.0.0.2" } ] }\n' "${T_BEARER:-sipa_eth$(( slot * 8 ))}"
    else printf '{ "up": false, "ipv4-address": [ ] }\n'; fi ;;
esac
'''
# The modem, as our U30 Air answered (docs/FINDINGS.md 74): a card whose SIM power is off says "+CME ERROR: 10"
# whatever is in its position; AT+SPACTCARD=<n>;+SFUN=2/3 is its SIM power, ;+SFUN=5 its radio off, AT+SFUN=4 on its
# data channel its radio on; AT+SPSWDATA gives the packet-data route to the channel's card and AT+SPSWDATA? reads it.
#   T_NOCARD=<slot>   no card in that position        T_PIN (+T_PINSLOT, default 0)  that card answers T_PIN
#   T_FAIL_ONCE=1     the first AT+SPSWDATA is refused T_ROUTE_STUCK=1  AT+SPSWDATA answers OK and moves nothing
#   T_NOROUTE=1       AT+SPSWDATA? is not answered     T_SILENT=1  nothing answers once a radio was switched on
#   T_DELAY=<s>       AT+SFUN=4 takes that long        T_BUSY_FIRST=1  the first radio-off finds the channel busy
MODEM = r'''
shift 2
case "$MU300_AT_DIR" in *at4|*at5) slot=1;; *) slot=0;; esac
echo "$slot $1" >> "$STUBLOG/at"
[ "${MU300_SIM_CONTROL:-}" = 1 ] || echo "$slot $1" >> "$STUBLOG/at-ungated"
if [ "${T_SILENT:-}" = 1 ] && [ -e "$STUBLOG/mutated" ]; then exit 1; fi
case "$1" in
 'AT+CPIN?')
   if [ ! -e "$STUBLOG/power$slot" ] || [ "${T_NOCARD:-}" = "$slot" ]; then echo '+CME ERROR: 10'; exit 0; fi
   if [ -n "${T_PIN:-}" ] && [ "${T_PINSLOT:-0}" = "$slot" ]; then echo "+CPIN: $T_PIN"; else echo '+CPIN: READY'; fi;;
 "AT+SPACTCARD=$slot;+SFUN=2") touch "$STUBLOG/power$slot";;
 "AT+SPACTCARD=$slot;+SFUN=3") rm -f "$STUBLOG/power$slot";;
 "AT+SPACTCARD=$slot;+SFUN=5")
   if [ "${T_BUSY_FIRST:-}" = 1 ] && [ ! -e "$STUBLOG/was-busy" ]; then touch "$STUBLOG/was-busy"; echo 'mu300-at: busy' >&2; exit 1; fi
   echo 0 > "$STUBLOG/radio$slot";;
 'AT+SFUN=4') [ -z "${T_DELAY:-}" ] || sleep "$T_DELAY"; touch "$STUBLOG/mutated"; echo 1 > "$STUBLOG/radio$slot";;
 'AT+CFUN?') echo "+CFUN: $(cat "$STUBLOG/radio$slot")";;
 'AT+SPSWDATA')
   if [ "${T_FAIL_ONCE:-}" = 1 ] && [ ! -e "$STUBLOG/failed" ]; then touch "$STUBLOG/failed"; echo ERROR; exit 0; fi
   [ "${T_ROUTE_STUCK:-}" = 1 ] || echo "$slot" > "$STUBLOG/route";;
 'AT+SPSWDATA?') if [ "${T_NOROUTE:-}" = 1 ]; then echo ERROR; exit 0; fi; echo "+SPSWDATA: $(cat "$STUBLOG/route")";;
 AT+SPACTCARD=*) echo ERROR; exit 0;;   # a command for the other card on this channel: never sent
esac
echo OK
'''

class HotSim(ShellTest):
    def setUp(self):
        super().setUp()
        self.run = self.tmp / 'run'
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        (self.etc / 'openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        (self.run / 'mu300').mkdir(parents=True)
        (self.run / 'mu300/sim-slot').write_text('1\n')
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.run / 'mu300-radio-freeze').touch()
        for n in ('mu300-at','mu300-at2','mu300-at4','mu300-at5'):
            d=self.run/n; (d/'owner').mkdir(parents=True)
            (d/'owner/pid').write_text(str(os.getpid())); os.mkfifo(d/'cmd')
        # SIM2 is the card in use: its radio on, its SIM power on, the data route its own; SIM1 is off
        (self.tmp/'radio0').write_text('0'); (self.tmp/'radio1').write_text('1')
        (self.tmp/'power1').touch(); (self.tmp/'route').write_text('1\n')
        self.stub('uci', r"""case "$*" in
 '-q get network.wan.sim_slot') cat "$STUBLOG/etc/uci-slot" ;;
 '-q get network.wan.sim_hot') cat "$STUBLOG/etc/uci-hot" ;;
 '-q get network.wan.sim_restart') cat "$STUBLOG/etc/uci-restart" 2>/dev/null ;;
 'set network.wan.sim_slot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-slot" ;;
 'set network.wan.sim_hot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-hot" ;;
 'commit network') : ;;
 *) exit 1;;
esac""")
        self.stub('ifdown' , 'echo down >> "$STUBLOG/net"')
        self.stub('ifup', 'echo up >> \"$STUBLOG/net\"; if [ \"${T_IFUP_FAIL_ONCE:-0}\" = 1 ] && [ ! -e \"$STUBLOG/ifup-failed\" ]; then touch \"$STUBLOG/ifup-failed\"; exit 1; fi')
        self.stub('mu300-device', 'echo "${T_DEVICE:-u30air}"')
        self.stub('setsid', "exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \"$@\"")   # macOS has none; the process group is only for the interrupt
        self.stub('flock', FLOCK)
        self.stub('ubus', UBUS)
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger"')
        # the restart of a device whose radio state is unknown: recorded, never the host's own reboot
        self.stub('reboot', 'echo reboot >> "$STUBLOG/reboot"')
        self.stub('sync', ':')
        self.stub('mu300-at', MODEM)
        # radio-locked: the lock is this stub's pid while the command runs; T_RADIO_BUSY=1: somebody else holds it
        self.stub('mobile-data', '[ "${T_RADIO_BUSY:-}" != 1 ] || exit 75; shift; ln -s "$$" "$T_RUN/mu300-radio-on.owner"; "$@"; r=$?; rm -f "$T_RUN/mu300-radio-on.owner"; exit "$r"')
        self.controller=self.tmp/'mu300-sim'
        self.controller.write_text((BIN/'mu300-sim-u30').read_text().replace('/run/',str(self.run)+'/').replace('/etc/',str(self.etc)+'/')
            .replace('/opt/mu300/bin/mu300-at','mu300-at').replace('/opt/mu300/bin/mobile-data','mobile-data')
            .replace('/opt/mu300/bin/mu300-device','mu300-device'))
        self.controller.chmod(0o755)

    def env(self, **extra):
        extra.setdefault('MU300_SIM_CONFIG_LIB', TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh')
        extra.setdefault('MU300_SIM_ROOT', self.tmp)   # the library's own files (sim-data-seen, the route mark)
        extra.setdefault('MU300_REBOOT_CMD', self.stubs / 'reboot')
        extra.setdefault('MU300_SIM_RESTART_DELAY', '0')
        extra.setdefault('T_RUN', self.run)
        return super().env(**extra)

    def ctl(self, *args, **env):
        env.setdefault('MU300_SIM_WAN_WAIT', '2')
        return self.script(['bash'],self.controller,*args,**env)

    def status(self, **env):
        return json.loads(self.ctl('status', **env).stdout)

    def calls(self):
        p = self.tmp / 'at'
        return p.read_text().splitlines() if p.exists() else []

    def rebooted(self, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if (self.tmp / 'reboot').exists(): return True
            time.sleep(.05)
        return False

    def test_bidirectional_switch_persists_default(self):
        for public,slot in [('1','0'),('2','1')]:
            r=self.ctl('switch',public)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertEqual((self.etc/'uci-slot').read_text(),slot+'\n')
            self.assertEqual((self.run/'mu300/sim-slot').read_text(),slot+'\n')
            s=self.status()
            self.assertEqual(s['active'],int(public)); self.assertEqual(s['result'],'switched')
        calls=self.calls()
        self.assertIn('0 AT+SPACTCARD=0;+SFUN=2',calls)
        self.assertIn('1 AT+SPACTCARD=1;+SFUN=5',calls)
        self.assertNotIn('SPTESTMODEM',' '.join(calls))
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\ndown\nup\n')
        # both cards have had data: remembered for the boot check, in a file of root's alone
        seen = self.etc / 'mu300/sim-data-seen'
        self.assertEqual(sorted(seen.read_text().split()), ['0', '1'])
        self.assertEqual(seen.stat().st_mode & 0o077, 0)
        self.assertFalse((self.run/'mu300/sim-sequence').exists())

    def test_the_card_in_use_goes_off_before_the_other_one_comes_on(self):
        # measured on our U30 Air: with the target's radio switched on first, its AT+SFUN=4 never answered and the
        # card in use lost its radio; this order answered within a second, both ways (FINDINGS 74)
        self.assertEqual(self.ctl('switch','1').returncode,0)
        calls=self.calls()
        off, on = calls.index('1 AT+SPACTCARD=1;+SFUN=5'), calls.index('0 AT+SFUN=4')
        self.assertLess(off, on)
        self.assertLess(calls.index('1 AT+SPACTCARD=1;+SFUN=3'), on)
        self.assertLess(calls.index('0 AT+SPSWDATA'), on, 'the data route before the radio, as the cold start')
        # every selecting command names its own card: a bare AT+SPACTCARD=0 changes nothing on this firmware
        self.assertFalse([c for c in calls if c.endswith('AT+SPACTCARD=0') or c.endswith('AT+SPACTCARD=1')])
        self.assertEqual((self.tmp/'radio0').read_text().strip(),'1'); self.assertEqual((self.tmp/'radio1').read_text().strip(),'0')
        self.assertTrue((self.tmp/'power0').exists()); self.assertFalse((self.tmp/'power1').exists())
        # and every one of them went through mu300-at's gate as the switch's own (MU300_SIM_CONTROL)
        self.assertFalse((self.tmp/'at-ungated').exists())

    def test_wrong_bearer_is_not_saved_as_success(self):
        result = self.ctl('switch', '1', T_BEARER='sipa_eth8')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['result'], 'no_data')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '1\n')

    def test_the_network_led_blinks_the_target_while_switching(self):
        # as ZTE's firmware (FINDINGS 154): the target card's colour blinks once the old connection is taken down;
        # the dial that follows shows the card in use. A missing or failing mu300-led changes nothing.
        self.stub('mu300-led', 'echo "led $* $(cat "$T_RUN/mu300/sim-switching" >/dev/null 2>&1 && echo marked)" >> "$STUBLOG/net"')
        r = self.ctl('switch', '1', MU300_LED_CMD=self.stubs / 'mu300-led')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / 'net').read_text(), 'down\nled data switching 0 marked\nup\n')
        self.stub('mu300-led', 'exit 1')
        r = self.ctl('switch', '2', MU300_LED_CMD=self.stubs / 'mu300-led')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.ctl('switch', '1', MU300_LED_CMD=self.tmp / 'no-such-led')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')

    def test_failed_ifup_rolls_back_and_keeps_preference(self):
        result = self.ctl('switch', '1', T_IFUP_FAIL_ONCE=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['result'], 'no_data')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '1\n')

    def test_same_active_card_without_data_does_not_replace_manual_default(self):
        (self.etc / 'uci-slot').write_text('0\n')
        result = self.ctl('switch', '2', T_WAN=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.etc / 'uci-slot').read_text(), '0\n')
        self.assertFalse((self.tmp / 'at').exists())

    def test_no_bearer_on_the_target_rolls_back_without_saving(self):
        # the card in use had data, the target gets none (another carrier's APN, no plan): back to the one that had
        r=self.ctl('switch','1',T_NODATA_SLOT='0')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('no data',r.stdout)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual((self.run/'mu300/sim-slot').read_text(),'1\n')
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],2); self.assertEqual(s['result'],'no_data')
        # dialled the target, found nothing, went back and dialled the old card again
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\ndown\nup\n')
        calls=self.calls()
        self.assertEqual(calls.count('0 AT+SFUN=4'),1); self.assertEqual(calls.count('1 AT+SFUN=4'),1)
        self.assertFalse((self.run/'mu300/sim-switching').exists())
        self.assertFalse((self.etc/'mu300/sim-data-seen').exists(), 'a card without data is not remembered as one with')
        self.assertFalse((self.tmp/'reboot').exists())

    def test_no_data_anywhere_keeps_the_users_choice(self):
        # issue #194's unit the other way round: the card in use has no data (no plan), the user switches to the
        # other one, and that one has none yet either. Going back restores nothing: the target stays, netifd keeps
        # dialling it, and the boot default is not saved on a card that showed no data.
        r=self.ctl('switch','1',T_WAN='0')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('staying on the target',r.stdout)
        self.assertEqual((self.run/'mu300/sim-slot').read_text(),'0\n')
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        s=self.status(T_WAN='0')
        self.assertEqual(s['active'],1); self.assertEqual(s['default'],2); self.assertEqual(s['result'],'no_data_kept')
        self.assertEqual(s['phase'],'idle')
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n', 'no second teardown: the target keeps dialling')
        self.assertEqual(self.calls().count('1 AT+SFUN=4'),0, 'no way back')
        self.assertFalse((self.run/'mu300/sim-switching').exists())
        self.assertFalse((self.tmp/'reboot').exists())
        # once the target has data, the same command saves it
        r=self.ctl('switch','1')
        self.assertEqual(r.returncode,0,r.stdout)
        self.assertEqual((self.etc/'uci-slot').read_text(),'0\n')
        self.assertEqual(self.status()['result'],'switched')

    def test_mobile_data_switched_off_stays_off(self):
        # (after kanoqwq's clean-tf-7.2 2d3b6f3f; FINDINGS 104) the user turned mobile data off, then switches cards:
        # the switch is done and saved, and nothing brings the data back - no ifup, and mobile-data's marker that
        # keeps its watchdog from dialling stays where its `down` put it
        marker = self.run / 'mu300-mobile-data-down'
        marker.touch()
        r = self.ctl('switch', '1', T_HELD='1')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / 'net').read_text(), 'down\n', 'never brought up')
        self.assertTrue(marker.exists(), "the watchdog's marker stays")
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '0\n', "the user's choice is the boot default")
        s = self.status(T_HELD='1')
        self.assertEqual(s['active'], 1); self.assertEqual(s['default'], 1)
        self.assertEqual(s['result'], 'switched'); self.assertEqual(s['phase'], 'idle')
        self.assertFalse((self.etc / 'mu300/sim-data-seen').exists(), 'no data was seen on it')
        self.assertEqual(self.calls().count('0 AT+SFUN=4'), 1)
        # and back: the same, and still no ifup
        self.assertEqual(self.ctl('switch', '2', T_HELD='1').returncode, 0)
        self.assertEqual((self.tmp / 'net').read_text(), 'down\ndown\n')
        self.assertTrue(marker.exists())
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')

    def test_mobile_data_off_and_a_failed_switch_leave_it_off(self):
        marker = self.run / 'mu300-mobile-data-down'
        marker.touch()
        r = self.ctl('switch', '1', T_HELD='1', T_FAIL_ONCE='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.status(T_HELD='1')['result'], 'rolled_back')
        self.assertEqual((self.tmp / 'net').read_text(), 'down\n')
        self.assertTrue(marker.exists())
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        r = self.ctl('switch', '1', T_HELD='1', T_BUSY_FIRST='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.tmp / 'net').read_text(), 'down\ndown\n')
        self.assertTrue(marker.exists())

    def test_mobile_data_off_the_card_in_use_is_saved_without_data(self):
        (self.etc / 'uci-slot').write_text('0\n')
        r = self.ctl('switch', '2', T_HELD='1')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        self.assertFalse((self.tmp / 'at').exists())
        self.assertFalse((self.tmp / 'net').exists())

    def test_mobile_data_on_a_switch_still_removes_the_marker_and_dials(self):
        # the teardown of the switch's own ifdown leaves the marker (mobile-data down): an ifup goes with removing it
        marker = self.run / 'mu300-mobile-data-down'
        marker.touch()
        self.assertEqual(self.ctl('switch', '1').returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual((self.tmp / 'net').read_text(), 'down\nup\n')

    def test_a_target_whose_radio_is_off_is_refused(self):
        """FINDINGS 185: the target's radio is switched off in its settings: refused before anything is sent."""
        self.stub('sim-radio', '[ "$1" = allowed ] || exit 2; case " ${T_RF_OFF:-} " in *" $2 "*) exit 1 ;; esac; exit 0')
        r = self.ctl('switch', '1', MU300_SIM_RADIO_BIN=self.stubs / 'sim-radio', T_RF_OFF='0')
        self.assertEqual(r.returncode, 1)
        self.assertIn('switched off in its settings', r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.tmp / 'net').exists())
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')

    def test_the_card_in_use_in_flight_mode_by_its_setting_still_switches(self):
        """FINDINGS 185: the card in use is in flight mode (CFUN 4) because its radio is off in its settings: a known
        state, switched off the same way; a CFUN 4 without that setting is still an unexpected state."""
        self.stub('sim-radio', '[ "$1" = allowed ] || exit 2; case " ${T_RF_OFF:-} " in *" $2 "*) exit 1 ;; esac; exit 0')
        (self.tmp / 'radio1').write_text('4')
        r = self.ctl('switch', '1', MU300_SIM_RADIO_BIN=self.stubs / 'sim-radio')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Unexpected radio state', r.stdout)
        r = self.ctl('switch', '1', MU300_SIM_RADIO_BIN=self.stubs / 'sim-radio', T_RF_OFF='1')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')

    def test_the_card_in_use_off_by_sfun5_still_switches(self):
        """FINDINGS 202: off by its setting is AT+SFUN=5 now, which reads back as CFUN 0 or 2: known states too."""
        self.stub('sim-radio', '[ "$1" = allowed ] || exit 2; case " ${T_RF_OFF:-} " in *" $2 "*) exit 1 ;; esac; exit 0')
        for state in ('0', '2'):
            with self.subTest(cfun=state):
                self.setUp()
                self.stub('sim-radio', '[ "$1" = allowed ] || exit 2; case " ${T_RF_OFF:-} " in *" $2 "*) exit 1 ;; esac; exit 0')
                (self.tmp / 'radio1').write_text(state)
                r = self.ctl('switch', '1', MU300_SIM_RADIO_BIN=self.stubs / 'sim-radio', T_RF_OFF='1')
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')

    def test_same_card_sends_no_commands(self):
        self.assertEqual(self.ctl('switch','2').returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_locked_target_is_refused_before_the_connection_is_touched(self):
        r=self.ctl('switch','1',T_PIN='SIM PIN')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('PIN',r.stdout); self.assertIn('nothing was changed',r.stdout)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertFalse((self.tmp/'net').exists(), 'the WAN never went down')
        self.assertEqual((self.tmp/'radio1').read_text().strip(),'1')
        calls=self.calls()
        self.assertNotIn('1 AT+SPACTCARD=1;+SFUN=5',calls)
        # the SIM power that was switched on for the question is off again
        self.assertEqual(calls[-1],'0 AT+SPACTCARD=0;+SFUN=3'); self.assertFalse((self.tmp/'power0').exists())
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['result'],'failed')

    def test_empty_target_position_is_refused_before_the_connection_is_touched(self):
        r=self.ctl('switch','1',T_NOCARD='0')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('No card answers',r.stdout)
        self.assertFalse((self.tmp/'net').exists())
        self.assertEqual((self.tmp/'radio1').read_text().strip(),'1')
        self.assertFalse((self.tmp/'power0').exists())
        self.assertEqual(self.status()['active'],2)

    def test_a_target_whose_sim_power_is_off_is_not_taken_for_an_empty_position(self):
        # our U30 Air's second position: "+CME ERROR: 10" at every boot, READY a second after its SIM power is on
        calls_before = self.calls()
        self.assertFalse((self.tmp/'power0').exists())
        self.assertEqual(self.ctl('switch','1').returncode,0)
        calls=self.calls()
        self.assertLess(calls.index('0 AT+SPACTCARD=0;+SFUN=2'), calls.index('0 AT+CPIN?'))
        self.assertEqual(calls_before, [])

    def test_rolls_back_once_without_changing_boot_default(self):
        r=self.ctl('switch','1',T_FAIL_ONCE='1')
        self.assertNotEqual(r.returncode,0)
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],2)
        self.assertEqual(s['result'],'rolled_back')
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n')
        self.assertEqual((self.tmp/'radio0').read_text().strip(),'0'); self.assertEqual((self.tmp/'radio1').read_text().strip(),'1')
        self.assertFalse((self.tmp/'reboot').exists())

    def test_data_route_on_the_other_card_is_a_failed_switch(self):
        # AT+SPSWDATA said OK and AT+SPSWDATA? still names the old card (after kanoqwq's read-back): not a switch
        r=self.ctl('switch','1',T_ROUTE_STUCK='1')
        self.assertNotEqual(r.returncode,0)
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['result'],'rolled_back')
        self.assertEqual(self.calls().count('0 AT+SFUN=4'),0, 'no radio on a card that does not have the data route')
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')

    def test_firmware_without_the_route_question_still_switches(self):
        r=self.ctl('switch','1',T_NOROUTE='1')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.status()['active'],1)

    def test_route_mark_follows_the_second_card(self):
        mark = self.etc/'mu300/sim-data-route-2'
        self.assertEqual(self.ctl('switch','1').returncode,0)
        self.assertFalse(mark.exists(), 'the modem said the route is SIM1\'s')
        self.assertEqual(self.ctl('switch','2').returncode,0)
        self.assertTrue(mark.exists(), 'a later plain SIM1 dial asks the modem and takes the route back')
        self.assertEqual(mark.stat().st_size,0)

    def test_busy_channel_changes_nothing(self):
        # an SMS transaction held nr4's client lock for the whole wait: the first command was never sent
        r=self.ctl('switch','1',T_BUSY_FIRST='1')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('nothing was changed',r.stdout)
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['result'],'failed')
        self.assertEqual((self.tmp/'radio1').read_text().strip(),'1')
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n')
        self.assertNotIn('0 AT+SFUN=4',self.calls()); self.assertEqual(self.calls().count('1 AT+SFUN=4'),0)
        self.assertFalse((self.tmp/'reboot').exists())

    def test_busy_radio_lock_changes_nothing(self):
        # a dial's radio-on or the lock helper's SFUN restart holds mobile-data's radio lock (exit 75)
        r=self.ctl('switch','1',T_RADIO_BUSY='1',MU300_SIM_RADIO_LOCK_WAIT='0')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('nothing was changed',r.stdout)
        self.assertEqual(self.status()['active'],2)
        self.assertNotIn('1 AT+SPACTCARD=1;+SFUN=5',self.calls())
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n')

    def test_silent_cp_stops_and_the_device_restarts(self):
        self.assertNotEqual(self.ctl('switch','1',T_SILENT='1').returncode,0)
        s=self.status()
        self.assertIsNone(s['active']); self.assertEqual(s['default'],2)
        self.assertEqual(s['result'],'restarting')
        self.assertEqual((self.tmp/'net').read_text(),'down\n')
        self.assertTrue(self.rebooted(), 'nothing else brings the data back: the saved default does, after a restart')
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n', 'a failed switch never changes it')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)

    def test_silent_cp_without_automatic_restart_says_reboot_required(self):
        (self.etc/'uci-restart').write_text('0\n')   # network.wan.sim_restart '0'
        self.assertNotEqual(self.ctl('switch','1',T_SILENT='1').returncode,0)
        s=self.status()
        self.assertIsNone(s['active']); self.assertEqual(s['result'],'reboot_required')
        self.assertFalse(self.rebooted(1))
        self.assertNotEqual(self.ctl('switch','1').returncode,0)

    def test_next_boot_selection_does_not_change_active(self):
        r=self.ctl('default','1')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        s=json.loads(r.stdout)
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],1)
        self.assertEqual(s['busy'],0, 'the holder of the lock is not busy to itself')
        self.assertFalse((self.tmp/'at').exists())
        # and the next boot's choice needs neither hot switching nor the brokers
        (self.etc/'uci-hot').write_text('0\n')
        (self.run/'mu300-at4/owner/pid').unlink()
        self.assertEqual(self.ctl('default','2').returncode,0)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')

    def test_legacy_slot_capability_agrees_with_native_u30_capabilities(self):
        s = self.status()
        self.assertEqual(s['slot1_supported'], 1)
        self.assertTrue(s['capabilities']['boot_selection'])
        self.assertTrue(s['capabilities']['live_switch'])
        (self.run / 'mu300/sim-active-unknown').touch()
        s = self.status()
        self.assertEqual(s['slot1_supported'], 1)
        self.assertIsNone(s['active'])

    def test_fallback_of_the_last_boot_is_reported_until_the_user_chooses(self):
        (self.etc/'mu300-sim-fallback').write_text('cold-start-failed\n')
        self.assertEqual(self.status()['fallback'],'cold-start-failed')
        (self.etc/'mu300-sim-fallback').write_text('+CCID: 8990; rm -rf /\n')   # never modem output, never shell
        self.assertEqual(self.status()['fallback'],'8990rm-rf')
        self.assertEqual(self.ctl('default','2').returncode,0)
        self.assertFalse((self.etc/'mu300-sim-fallback').exists())
        self.assertIsNone(self.status()['fallback'])

    def test_status_reports_the_boot_check_of_a_second_card(self):
        check = self.run/'mu300/sim-boot-check'
        s = self.status()
        self.assertIsNone(s['boot_check']); self.assertIsNone(s['boot_check_left'])
        check.write_text('waiting 99999999\n')
        s = self.status()
        self.assertEqual(s['boot_check'],'waiting'); self.assertIsInstance(s['boot_check_left'],int)
        for word in ('confirmed','kept','off','failed'):
            check.write_text(word+'\n')
            self.assertEqual(self.status()['boot_check'],word)
        check.write_text('"; reboot #\n')   # only the known words are ever printed
        s = self.status()
        self.assertIsNone(s['boot_check'])
        check.write_text('waiting $(reboot)\n')
        s = self.status()
        self.assertEqual(s['boot_check'],'waiting'); self.assertIsNone(s['boot_check_left'])
        self.assertFalse((self.tmp/'reboot').exists())

    def test_other_devices_and_systems_refuse_everything(self):
        for env in ({'T_DEVICE': 'f50'}, {'T_DEVICE': 'u30pro'}):
            for args in (('status',), ('default','2'), ('hot','1'), ('switch','1')):
                r=self.ctl(*args, **env)
                self.assertNotEqual(r.returncode,0,args)
                self.assertEqual(json.loads(r.stdout)['supported'],0)
        (self.etc/'openwrt_release').unlink()   # Ubuntu
        r=self.ctl('default','2')
        self.assertNotEqual(r.returncode,0); self.assertEqual(json.loads(r.stdout)['ok'],0)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual((self.etc/'uci-hot').read_text(),'1\n')
        self.assertFalse((self.tmp/'at').exists())

    def test_disabled_or_missing_owner_sends_nothing(self):
        (self.etc/'uci-hot').write_text('0')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        (self.etc/'uci-hot').write_text('1')
        (self.run/'mu300-at4/owner/pid').unlink()
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_injection_and_internal_sequence_rejected(self):
        for verb,value in [('switch','1;reboot'),('hot','yes'),('default','0'),('--sequence','0'),
                           ('switch','1\nAT+CFUN=0'),('switch','$(reboot)'),('default','2 ;+SPSWDATA'),('switch','')]:
            self.assertNotEqual(self.ctl(verb,value).returncode,0)
        self.assertFalse((self.tmp/'at').exists())
        self.assertFalse((self.tmp/'reboot').exists())
        # the only things that ever reach the modem are built from the two slot numbers
        self.assertEqual(self.ctl('switch','1').returncode,0)
        allowed = {'AT+CFUN?','AT+CPIN?','AT+SPSWDATA','AT+SPSWDATA?','AT+SFUN=4'} | {
            f'AT+SPACTCARD={n};+SFUN={f}' for n in (0,1) for f in (2,3,5)}
        self.assertLessEqual({c.split(' ',1)[1] for c in self.calls()}, allowed)

    def test_concurrent_request_cannot_change_default(self):
        lock=self.run/'mu300/sim-control.lock'
        with lock.open('w') as f:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
            self.assertNotEqual(self.ctl('default','1').returncode,0)
            self.assertNotEqual(self.ctl('switch','1').returncode,0)
            self.assertEqual(self.status()['busy'],1)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual(self.status()['busy'],0)
        self.assertFalse((self.tmp/'at').exists())

    def test_interrupted_switch_keeps_default_and_restarts(self):
        env=self.env(T_DELAY='10')
        p=subprocess.Popen(['bash',str(self.controller),'switch','1'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+15
            while not (self.run/'mu300/sim-sequence').exists():
                self.assertLess(time.monotonic(),deadline); time.sleep(.02)
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=5)
            s=self.status()
            self.assertIsNone(s['active']); self.assertEqual(s['default'],2)
            self.assertEqual(s['result'],'restarting')
            self.assertTrue(self.rebooted())
            self.assertFalse((self.run/'mu300/sim-sequence').exists(), 'the AT gate is not left closed')
        finally:
            if p.poll() is None: p.kill(); p.communicate()

    def test_killed_controller_is_recovered_by_its_guard(self):
        # SIGKILL (or the kernel's out-of-memory killer): no trap runs. The lock is the kernel's and goes with the
        # process; the guard sees the unfinished switch and declares the radio unknown - measured on the U30 Air:
        # "restarting" within 5 s, the device back on its saved card after the restart
        env=self.env(T_DELAY='6')
        # (no pipes: the sequence outlives the controller here and would keep them open)
        p=subprocess.Popen(['bash',str(self.controller),'switch','1'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+15
            while not (self.run/'mu300/sim-sequence').exists():
                self.assertLess(time.monotonic(),deadline); time.sleep(.02)
            p.kill(); p.communicate(timeout=5)
            self.assertTrue(self.rebooted(10))
            s=self.status()
            self.assertIsNone(s['active']); self.assertEqual(s['result'],'restarting')
            # The lock is the kernel's: it is gone with its last holder. That is the orphaned sequence, which still
            # sends its commands - the AT gate stays closed for exactly that long, and no longer.
            deadline=time.monotonic()+12
            while self.status()['busy'] != 0:
                self.assertLess(time.monotonic(),deadline, 'a stale lock'); time.sleep(.3)
            self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
            self.assertFalse((self.run/'mu300/sim-sequence').exists())
            self.assertFalse((self.run/'mu300/sim-switching').exists())
        finally:
            if p.poll() is None: p.kill(); p.communicate()

    def test_interrupted_marker_never_allows_another_switch(self):
        (self.run/'mu300/sim-switching').touch()
        s=self.status()
        self.assertIsNone(s['active']); self.assertEqual(s['result'],'reboot_required')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_state_files_are_roots_alone_and_hold_no_reply(self):
        self.assertEqual(self.ctl('switch','1').returncode,0)
        for name in ('sim-switch.log','sim-result','sim-phase','sim-slot','sim-control.lock'):
            self.assertEqual((self.run/'mu300'/name).stat().st_mode & 0o077, 0, name)
        log=(self.run/'mu300/sim-switch.log').read_text()
        self.assertIn('AT+SPSWDATA',log)
        for reply in ('+CPIN:','+CFUN:','+SPSWDATA:','READY','OK'):
            self.assertNotIn(reply,log)


class AtGate(ShellTest):
    """mu300-at while a live switch runs: every client but the switch itself is told "busy", and a marker left by a
    killed switch closes nothing (it counts only while the switch holds its lock)."""
    def setUp(self):
        super().setUp()
        self.run = self.tmp / 'run'
        (self.run / 'mu300').mkdir(parents=True)
        self.stub('flock', FLOCK)
        self.at = self.tmp / 'mu300-at'
        self.at.write_text((BIN / 'mu300-at').read_text().replace('/run/', str(self.run) + '/')
                           .replace('/opt/mu300/bin/mu300-sim', str(self.tmp / 'no-selector')))

    def ask(self, **env):
        # no daemon in the scratch /run: past the gate the client ends with its own "no device" words
        return self.script(['bash'], self.at, 'AT', MU300_AT_DEV=self.tmp / 'no-device', **env)

    def test_gate(self):
        import fcntl
        past = self.ask()
        self.assertNotIn('busy', past.stderr)
        (self.run / 'mu300/sim-sequence').touch()
        r = self.ask()
        self.assertEqual(r.stderr, past.stderr, 'a marker nobody holds the lock for is a killed switch: not believed')
        with (self.run / 'mu300/sim-control.lock').open('w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.ask()
            self.assertEqual((r.returncode, r.stderr.strip()), (1, 'mu300-at: busy'))
            r = self.ask(MU300_SIM_CONTROL='1')
            self.assertEqual(r.stderr, past.stderr, 'the switch\'s own commands pass')
        (self.run / 'mu300/sim-sequence').unlink()
        self.assertEqual(self.ask().stderr, past.stderr)


class PanelRadioActions(ShellTest):
    """The panel's radio on/off and modem reset send their own CFUN/SFUN commands to "the" card: in a dual-SIM boot
    (the four owners, with or without live switching) they are refused before anything is sent."""
    def setUp(self):
        super().setUp()
        self.run = self.tmp / 'run'
        (self.run / 'mu300').mkdir(parents=True)
        app = TOP / 'openwrt/luci-app-mu300/root/usr'
        (self.tmp / 'usr/libexec/unisoc-modem').mkdir(parents=True)
        (self.tmp / 'usr/share/unisoc-modem').mkdir(parents=True)
        self.action = self.tmp / 'usr/libexec/unisoc-modem/action'
        self.action.write_text((app / 'libexec/unisoc-modem/action').read_text().replace('/run/', str(self.run) + '/'))
        (self.tmp / 'usr/share/unisoc-modem/lib.sh').write_text((app / 'share/unisoc-modem/lib.sh').read_text())
        self.stub('uci', 'exit 1')
        self.stub('at', 'echo "$*" >> "$STUBLOG/at"; echo OK')
        for name in ('ifup', 'ifdown'):
            self.stub(name, f'echo {name} >> "$STUBLOG/net"')

    def act(self, shell, *args):
        return self.script(shell, self.action, *args, MU300_AT=self.stubs / 'at', MOBILE_DATA=self.tmp / 'none')

    def test_refused_in_a_dual_sim_boot(self):
        for marker in ('mu300-radio-freeze', 'mu300/sim-managed', 'mu300/sim-switching'):
            (self.run / marker).touch()
            for shell in self.each_shell():
                for args in (('radio', 'off'), ('radio', 'on'), ('modem-reset',)):
                    with self.subTest(marker=marker, args=args):
                        r = self.act(shell, *args)
                        self.assertEqual(json.loads(r.stdout)['ok'], 0, r.stdout + r.stderr)
                        self.assertFalse((self.tmp / 'at').exists())
                        self.assertFalse((self.tmp / 'net').exists())
            (self.run / marker).unlink()


if __name__ == '__main__':
    import unittest
    unittest.main()
