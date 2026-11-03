"""Do not interpret query reporting mode as registration or use another SIM URC."""
from helpers import ShellTest, BIN


class RegistrationScope(ShellTest):
    def test_query_and_unsolicited_forms_do_not_overlap(self):
        source = (BIN / 'mobile-data').read_text()
        function = source[source.index('is_registered() {'):source.index('\nwait_registered()')]
        for shell in self.each_shell():
            for kind, reply, expected in [
                ('query', '+CEREG: 1,8', False),
                ('query', '+CEREG: 5,2', False),
                ('query', '+CEREG: 2,1,"F2F5","0140589A",7', True),
                ('query', '+CEREG: 2,5', True),
                ('query', '+CEREG: 2,1\n+CEREG: 2,8', False),
                ('urc', '+CEREG: 1,"F2F5","0140589A",7', True),
                ('urc', '+CEREG: 5', True),
                ('urc', '+CEREG: 8', False),
                ('urc', '+CEREG: 1\n+CEREG: 2', False),
            ]:
                r = self.sh(shell, function + '\nis_registered ' + kind, stdin=reply+'\n')
                self.assertEqual(r.returncode == 0, expected, (kind, reply, r.stderr))

    def test_wait_uses_selected_stream_and_consumes_each_batch(self):
        source = (BIN / 'mobile-data').read_text()
        function = source[source.index('wait_registered() {'):source.index('\n# The modem needs')]
        self.assertIn('urclog=${MU300_SIM_URC:-', function)
        self.assertIn('is_registered urc', function)
        self.assertIn('start=$((size + 1))\n        if', function)
        self.assertIn('[ "$((size + 1))" -lt "$start" ]', function)

    def test_procd_env_is_a_single_table_per_instance(self):
        # procd_set_param env creates an object, it does not append to one.
        from helpers import TOP
        # Native modemd owns all brokers now; the removed shell-daemon
        # per-channel environment is no longer the production interface.
        source = (BIN.parent / 'lib/modemd-service.sh').read_text().replace(
            '/opt/mu300/bin/mu300-device', 'mu300-device')
        code = source + '''
procd_open_instance() { instance=$1; env_count=0; }
procd_set_param() {
    [ "$1" != env ] || { env_count=$((env_count+1)); [ "$env_count" = 1 ] || exit 12; }
    if [ "$instance" = modemd ] && [ "$1" = env ]; then printf '%s\n' "$@"; fi
}
procd_append_param() { printf '%s\n' "$@"; }
procd_close_instance() { :; }
mu300_sms_pool() { echo '/custom sms/pool'; }
modemd_single_layout
'''
        self.stub('uci', 'case "$*" in *sim_slots*) echo 2 ;; *) exit 1 ;; esac')
        self.stub('mu300-device', 'echo u30air')
        for shell in self.each_shell():
            r = self.sh(shell, code)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('dev=/dev/stty_nr4,dir=/run/mu300-at4,urc=3,', r.stdout)
            self.assertEqual(r.stdout.count('MU300_SMS_POOL=/custom sms/pool'), 1)

    def test_cancel_pending_registration_does_not_send_more_at(self):
        source=(BIN/'mobile-data').read_text()
        function=source[source.index('wait_registered() {'):source.index('\n# The modem needs')]
        flag=self.tmp/'cancelled';flag.touch()
        log=self.tmp/'urc.log';log.write_text('')
        function=function.replace('/run/mu300-mobile-data-down',str(flag))
        r=self.sh(['bash'],function+'''\nat() { echo unexpected; }
wait_registered
''', MU300_SIM_URC=log)
        self.assertEqual(r.returncode,1,r.stderr)
        self.assertEqual(r.stdout,'')

    def test_cp_rejecting_combined_read_still_registers(self):
        source=(BIN/'mobile-data').read_text()
        functions=source[source.index('is_registered() {'):source.index('\n# The modem needs')]
        log=self.tmp/'urc';log.write_text('')
        code=functions+'''
at() {
    printf '%s\\n' "$1" >> "$STUBLOG/queries"
    case "$1" in
      'AT+CEREG?;+C5GREG?') echo ERROR;;
      'AT+CEREG?') printf '+CEREG: 2,1\\nOK\\n';;
      'AT+C5GREG?') echo ERROR;;
    esac
}
wait_registered
'''
        result=self.sh(['bash'],code,MU300_SIM_URC=log,MU300_SIM_SLOT='0',MU300_REGISTER_WAIT='2')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.tmp/'queries').read_text().splitlines(),['AT+CEREG?;+C5GREG?','AT+CEREG?','AT+C5GREG?'])
