"""Exercise the real passive parser; no serial device, radio command or modem reset."""
import json
import shlex
from helpers import ShellTest, TOP


class RegistrationRecovery(ShellTest):
    def test_passive_cache_query_urc_and_duplicates(self):
        library = TOP / 'rootfs/overlay/opt/mu300/lib/registration.sh'
        self.stub('ubus', 'printf "%s\\n" "$*" >> "$STUBLOG/events"')
        for shell in self.each_shell():
            cache = self.tmp / 'registration'
            events = self.tmp / 'events'
            if events.exists():
                events.unlink()
            code = '. ' + shlex.quote(str(library)) + '''
mu300_registration_save 0 '+CEREG: 1,"1234","12345678",7'
mu300_registration_save 0 '+CEREG: 1,"5678","87654321",7'
mu300_registration_reply 1 '+CEREG: 2,5,"1234","87654321",11'
wait
'''
            r = self.sh(shell, code, MU300_REGISTRATION_DIR=cache)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads((cache/'slot0.json').read_text()),
                             dict(slot=0, stat=1, rat=7))
            self.assertEqual(json.loads((cache/'slot1.json').read_text()),
                             dict(slot=1, stat=5, rat=11))
            self.assertEqual(len(events.read_text().splitlines()), 2)
            self.assertNotIn('1234', events.read_text())
            for file in cache.iterdir():
                file.unlink()

    def test_registration_rechecks_stale_absence_once_without_polling(self):
        library = TOP/'rootfs/overlay/opt/mu300/lib/registration.sh'
        self.stub('ubus', 'printf "%s\\n" "$*" >> "$STUBLOG/events"')
        for i,shell in enumerate(self.each_shell()):
            cache=self.tmp/f'hotplug-{i}'
            (self.tmp/'events').unlink(missing_ok=True)
            code='. '+shlex.quote(str(library))+"""
mu300_sim_save 1 absent
mu300_registration_save 1 '+CEREG: 1,"a","b",11'
mu300_registration_save 1 '+CEREG: 1,"a","b",11'
wait
"""
            result=self.sh(shell,code,MU300_REGISTRATION_DIR=cache)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((cache/'sim1.json').exists())
            self.assertFalse((cache/'sim0.json').exists())
            events=(self.tmp/'events').read_text().splitlines()
            self.assertEqual(sum('mu300.sim-change' in e for e in events),1)

    def test_all_states_and_unknown_rat_are_preserved_for_policy(self):
        library = TOP / 'rootfs/overlay/opt/mu300/lib/registration.sh'
        self.stub('ubus', 'exit 1')  # an unavailable bus must not fail AT owner
        for shell in self.each_shell():
            cache = self.tmp / 'states'
            for state in range(11):
                code = '. ' + shlex.quote(str(library)) + (
                    f"\nmu300_registration_save 1 '+CEREG: {state}'\nwait\n")
                r = self.sh(shell, code, MU300_REGISTRATION_DIR=cache)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(json.loads((cache/'slot1.json').read_text()),
                                 dict(slot=1, stat=state, rat=None))

    def test_registration_timeout_remains_distinct_from_fatal_or_address_failure(self):
        source = (TOP/'openwrt/overlay/lib/netifd/proto/mu300cell.sh').read_text()
        branch = source[source.index('\tif [ "$rc" = 5 ]'):source.index('\tip=$(echo')]
        for shell in self.each_shell():
            code = '''proto_notify_error() { echo "error:$*"; }
proto_set_available() { echo "available:$*"; }
test_setup() {
    local rc=5 config=wan slot=1
''' + branch.replace('/run/', str(self.tmp)+'/') + '\n}\ntest_setup\n'
            r = self.sh(shell, code)
            self.assertEqual(r.returncode, 1)
            self.assertIn('error:wan REGISTRATION_WAIT', r.stdout)
            self.assertIn('available:wan 0', r.stdout)
            self.assertEqual((self.tmp/'mu300cell-wan.registration-wait').read_text(), '1\n')
