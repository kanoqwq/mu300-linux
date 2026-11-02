"""Reproduce empty/not-ready SIM after the SMS safety deadline expires.

Run the complete daemon with a synthetic monotonic clock/event source, then
insert a ready SIM. No wall-clock waits, modem, message data or device access.
"""
import os
from helpers import ShellTest, BIN


class SmsIdleDeadline(ShellTest):
    def test_absent_and_not_ready_never_reenter_an_expired_wait(self):
        atdir=self.tmp/'at';atdir.mkdir();os.mkfifo(atdir/'cmd')
        events=self.tmp/'events.sh'
        events.write_text('''
NOW=0; WAITS=0
sms_clock() { SMS_NOW=$NOW; }
sms_events_start() { :; }
sms_events_stop() { :; }
sms_events_wait() {
    printf '%s %s\n' "$NOW" "$1" >> "$STUBLOG/waits"
    [ "$1" -gt "$NOW" ] || exit 93
    WAITS=$((WAITS+1))
    case $WAITS in
        1) NOW=301; echo absent > "$STUBLOG/sim-state" ;;
        2) NOW=901; echo unknown > "$STUBLOG/sim-state" ;;
        3) NOW=906; echo ready > "$STUBLOG/sim-state" ;;
        *) exit 0 ;;
    esac
    sms_clock
}
''')
        registration=self.tmp/'registration.sh'
        registration.write_text('''
mu300_sim_absent() { [ "$(cat "$STUBLOG/sim-state")" = absent ]; }
mu300_sim_is() { [ "$(cat "$STUBLOG/sim-state")" = ready ]; }
''')
        self.stub('mu300-at','''
printf '%s %s\n' "$(cat "$STUBLOG/sim-state")" "$*" >> "$STUBLOG/at-calls"
printf '+CPIN: READY\nOK\n'
''')
        self.stub('sms','printf "%s\\n" "$*" >> "$STUBLOG/sms-calls"')
        sms=self.stubs/'sms'
        # The result runs under every available shell used by production.
        for shell in self.each_shell():
            for name in ['waits','at-calls','sms-calls']:
                (self.tmp/name).unlink(missing_ok=True)
            (self.tmp/'sim-state').write_text('absent')
            result=self.script(shell,BIN/'mu300-smsd',
                MU300_SMS_POOL=self.tmp/'pool',MU300_AT_DIR=atdir,
                MU300_SMS_LOCK=self.tmp/'lock',MU300_SMS_TRANSPORT='ubus',
                MU300_SMS_BIN=sms,MU300_SMS_DIRECT_FLAG=self.tmp/'direct',
                MU300_SMS_EVENTS_LIB=events,MU300_REGISTRATION_LIB=registration,
                MU300_SMS_POLL='5',MU300_SMS_SYNC_INTERVAL='300',MU300_SIM_SLOT='0')
            self.assertEqual(result.returncode,0,result.stderr)
            waits=[list(map(int,line.split())) for line in (self.tmp/'waits').read_text().splitlines()]
            self.assertEqual(len(waits),4)
            self.assertTrue(all(due>now for now,due in waits),waits)
            self.assertTrue(all(line.startswith('ready ') for line in (self.tmp/'at-calls').read_text().splitlines()))
            self.assertIn('archive-sim',(self.tmp/'sms-calls').read_text())
            self.assertFalse((self.tmp/'lock').exists())
