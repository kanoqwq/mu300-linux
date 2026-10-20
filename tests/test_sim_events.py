"""Run SIM event/cache logic without serial devices or a running system ubus."""
import json
import shlex
from helpers import ShellTest, TOP


class SimEvents(ShellTest):
    def setUp(self):
        super().setUp()
        self.library = TOP / 'rootfs/overlay/opt/mu300/lib/registration.sh'
        self.stub('ubus', 'printf "%s\\n" "$*" >> "$STUBLOG/events"')

    def test_stock_events_only_are_hints_not_absence(self):
        for shell in self.each_shell():
            events = self.tmp / 'events'
            events.unlink(missing_ok=True)
            source = '. ' + shlex.quote(str(self.library)) + '''
mu300_sim_changed 0 '+ECIND: 3,1,2'
mu300_sim_changed 1 '+ECIND: 3,2'
mu300_sim_changed 1 '+ECIND: 3,4'
mu300_sim_changed 0 '+ECIND: 3,100'
mu300_sim_changed 0 '+ECIND: 3,0,0,1'
mu300_sim_changed 1 '+ECIND: 3,1,2,0,1'
mu300_sim_changed 0 '+ECIND: 2,1,2'
mu300_sim_changed 0 '+ECIND: 3,1,99'
mu300_sim_changed 0 '+ECIND: 3,2junk'
mu300_sim_changed 0 '+ECIND: 3,1,2,7junk'
mu300_sim_changed 0 '+ECIND: 3,0,0,-1'
mu300_sim_changed 0 '+ECIND: 3,0,0,4294967296'
mu300_sim_changed 0 '+ECIND: 3,0,0,'
mu300_sim_changed 0 '+ECIND: 3,0,,1'
mu300_sim_changed 2 '+ECIND: 3,2'
wait
'''
            cache = self.tmp / 'sim'
            r = self.sh(shell, source, MU300_REGISTRATION_DIR=cache)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(events.read_text().splitlines()), 6)
            self.assertFalse(cache.exists())

    def test_confirmed_cpin_scopes_and_unknown_reply(self):
        for shell in self.each_shell():
            cache = self.tmp / 'sim'
            source = '. ' + shlex.quote(str(self.library)) + '''
mu300_sim_reply 'AT+SPACTCARD=1;+CFUN?' '+CFUN: 1
OK'
mu300_sim_reply 'AT+SPACTCARD=1;+CPIN?' '+CME ERROR: 10'
mu300_sim_absent 1 || exit 91
mu300_sim_absent 0 && exit 92
mu300_sim_reply 'AT+SPACTCARD=1;+CPIN?' '+CME ERROR: 14'
mu300_sim_absent 1 || exit 93
mu300_sim_reply 'AT+SPACTCARD=1;+CPIN?' '+CPIN: READY'
mu300_sim_absent 1 || exit 94
mu300_sim_reply 'AT+SPACTCARD=0;+CPIN?' '+CPIN: SIM PIN
OK'
mu300_sim_reply 'AT+SPACTCARD=1;+CPIN?' '+CPIN: READY
OK'
mu300_sim_absent 1 && exit 95
mu300_sim_reply 'AT+CEREG?' '+CME ERROR: 10'
mu300_sim_reply 'AT+CPIN?' '+CME ERROR: 10'
wait
'''
            r = self.sh(shell, source, MU300_REGISTRATION_DIR=cache)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads((cache/'sim0.json').read_text()), dict(slot=0, state='locked'))
            self.assertEqual(json.loads((cache/'sim1.json').read_text()), dict(slot=1, state='ready'))
            for file in cache.iterdir(): file.unlink()

    def test_sim_power_off_is_not_physical_removal(self):
        for shell in self.each_shell():
            cache = self.tmp / 'power'
            source = '. ' + shlex.quote(str(self.library)) + '''
mu300_sim_reply 'AT+SPACTCARD=0;+CPIN?' '+CME ERROR: 10'
mu300_sim_absent 0 && exit 90
mu300_sim_reply 'AT+SPACTCARD=0;+SFUN=2' 'OK'
mu300_sim_reply 'AT+SPACTCARD=0;+CPIN?' '+CME ERROR: 10'
mu300_sim_absent 0 || exit 91
mu300_sim_reply 'AT+SPACTCARD=0;+SFUN=3' 'OK'
mu300_sim_absent 0 && exit 92
mu300_sim_reply 'AT+SPACTCARD=0;+CPIN?' '+CME ERROR: 10'
mu300_sim_absent 0 && exit 93
mu300_sim_reply 'AT+SPACTCARD=0;+CPIN?' '+CPIN: READY
OK'
mu300_sim_is 0 ready || exit 94
wait
'''
            r = self.sh(shell, source, MU300_REGISTRATION_DIR=cache)
            self.assertEqual(r.returncode, 0, r.stderr)
            for file in cache.iterdir(): file.unlink()

    def test_known_absence_ends_registration_without_queries_or_sleep(self):
        text = (TOP/'rootfs/overlay/opt/mu300/bin/mobile-data').read_text()
        function = text[text.index('wait_registered() {'):text.index('\n# The modem needs')]
        for shell in self.each_shell():
            # mobile-data is bash; use bash for its SECONDS contract.
            if 'bash' not in shell: continue
            source = '''at() { exit 90; }
sleep() { exit 91; }
mu300_sim_absent() { return 0; }
''' + function + '\nwait_registered\n'
            r = self.sh(shell, source, MU300_SIM_SLOT=0, MU300_SIM_URC=self.tmp/'unused')
            self.assertEqual(r.returncode, 6, r.stderr)

    def test_sms_known_absence_does_not_probe_or_initialize(self):
        text = (TOP/'rootfs/overlay/opt/mu300/bin/mu300-smsd').read_text()
        readiness = text[text.index('# A starting/busy SIM'):text.index('at_ok() {')]
        gating = text[text.index('    if sim_absent; then', text.index('while :; do\n    sleep')):
                      text.index('    if process_spool; then')]
        for shell in self.each_shell():
            code = '''sim_absent() { return 0; }
sms_at() { echo unexpected-query; exit 90; }
sleep() { echo unexpected-sleep; exit 91; }
''' + readiness + '''
setup_sms() { echo unexpected-setup; }
sim_ready() { return 1; }
sim_configured=1
for turn in 1 2 3; do
''' + gating + '\necho unexpected-poll\ndone\n[ "$sim_configured" = 0 ]\n'
            result = self.sh(shell, code, DIRECT_FLAG=self.tmp/'flag')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
