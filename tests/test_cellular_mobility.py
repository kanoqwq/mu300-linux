"""Mobility address renewal and watchdog decisions, with isolated AT/IP/netifd."""
from helpers import ShellTest, TOP, BIN
import shlex
PDP_LIB = TOP / "rootfs/overlay/opt/mu300/lib/pdp-address.sh"
PDP_SOURCE = ". " + shlex.quote(str(PDP_LIB)) + "\n"


MONITOR = TOP / 'openwrt/overlay/lib/netifd/proto/mu300cell-v6.sh'
SOURCE = MONITOR.read_text()
MOBILE = (BIN / 'mobile-data').read_text()


class Mobility(ShellTest):
    def setUp(self):
        super().setUp()
        self.functions = PDP_SOURCE + SOURCE[SOURCE.index('current_v6() {'):SOURCE.index('\nlast_v6=$(v6_snapshot)')]
        self.functions = self.functions.replace('/run/mu300-at', str(self.tmp / 'at'))
        for name in ('at', 'at4', 'at6', 'at7'):
            directory = self.tmp / name
            directory.mkdir()
            import os
            os.mkfifo(directory / 'cmd')
        self.calls = self.tmp / 'calls'
        self.stub('logger', 'echo "log $*" >>"$STUBLOG/calls"')
        self.stub('sleep', ':')  # exercise grace decisions without waiting six seconds
        self.stub('mu300-at', '''
echo "at $MU300_AT_DIR $*" >>"$STUBLOG/calls"
[ "${AT_FAIL:-0}" = 0 ] || exit 1
case "$*" in
  *CGACT*) printf '%s\n' "${CGACT_REPLY:-+CGACT:1,1}" ;;
  *CGCONTRDP*) cat "$STUBLOG/reply" ;;
esac
''')
        self.stub('ip', '''
echo "ip $*" >>"$STUBLOG/calls"
case "$*" in
  '-4 -o addr show'*) [ "${KERNEL_MISSING:-0}" = 1 ] || echo '1: sipa_eth0 inet 10.0.0.1/8 scope global sipa_eth0' ;;
  '-6 addr show'*) printf '%s\n' 'inet6 2408:1::2/64 scope global' 'valid_lft 60sec preferred_lft 30sec' ;;
  '-6 route show'*) echo 'default via fe80::1 dev sipa_eth0 proto ra expires 100sec' ;;
  '-4 addr add'*|'-4 addr replace'*) [ "${ADD_FAIL:-0}" = 0 ] || exit 1 ;;
esac
''')
        self.reply = self.tmp / 'reply'
        self.reply.write_text('+CGCONTRDP: 1,6,internet,10.0.0.2.255.0.0.0,,1.1.1.1,8.8.8.8\n')
        self.proto = '''
ip() { "$STUBLOG/stubs/ip" "$@"; }
logger() { "$STUBLOG/stubs/logger" "$@"; }
sleep() { "$STUBLOG/stubs/sleep" "$@"; }
proto_init_update() { echo "proto init $*" >>"$STUBLOG/calls"; }
proto_set_keep() { echo "proto keep $*" >>"$STUBLOG/calls"; }
proto_add_ipv4_address() { echo "proto address $*" >>"$STUBLOG/calls"; }
proto_add_ipv4_route() { echo "proto route $*" >>"$STUBLOG/calls"; }
proto_add_ipv6_address() { echo "proto v6address $*" >>"$STUBLOG/calls"; }
proto_add_ipv6_route() { echo "proto v6route $*" >>"$STUBLOG/calls"; }
proto_add_dns_server() { echo "proto dns $*" >>"$STUBLOG/calls"; }
proto_send_update() { echo "proto update $*" >>"$STUBLOG/calls"; [ "${REPORT_FAIL:-0}" = 0 ]; }
config=wan; ifname=sipa_eth0; sim_slot=0; logtag=test
ip4=10.0.0.1; prefix4=8; dns1=9.9.9.9; dns2=149.112.112.112; peerdns=1
'''

    def refresh(self, shell, extra='', **env):
        self.calls.unlink(missing_ok=True)
        r = self.sh(shell, self.functions + self.proto + extra + '''
rc=0; refresh_bearer cgev || rc=$?
printf 'rc=%s ip=%s/%s dns=%s,%s\n' "$rc" "$ip4" "$prefix4" "$dns1" "$dns2"
''', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, self.calls.read_text()

    def test_replace_first_preserves_ipv6_lifetimes(self):
        for shell in self.each_shell():
            output, calls = self.refresh(shell)
            self.assertIn('rc=0 ip=10.0.0.2/8 dns=1.1.1.1,8.8.8.8', output)
            self.assertLess(calls.index('ip -4 addr add 10.0.0.2/8'), calls.index('proto update wan'))
            self.assertLess(calls.index('proto update wan'), calls.index('ip -4 addr del 10.0.0.1/8'))
            self.assertIn('proto init sipa_eth0 1 1', calls)
            self.assertIn('proto keep 0', calls)
            self.assertIn('proto v6address 2408:1::2 64 30 60', calls)
            self.assertIn('proto v6route :: 0 fe80::1 4096 100', calls)
            self.assertNotIn('CGACT=0', calls)

    def test_empty_or_failed_at_keeps_old_snapshot(self):
        for shell in self.each_shell():
            for env in ({'AT_FAIL': '1'}, {'CGACT_REPLY': 'OK'}, {'CGACT_REPLY': '+CGACT:11,1'}):
                output, calls = self.refresh(shell, **env)
                self.assertIn('rc=1 ip=10.0.0.1/8 dns=9.9.9.9,149.112.112.112', output)
                self.assertNotIn('proto update', calls)
                self.assertNotIn('ip -4 addr del', calls)

    def test_inactive_context_does_not_trust_stale_address(self):
        for shell in self.each_shell():
            output, calls = self.refresh(shell, CGACT_REPLY='+CGACT: 1,0')
            self.assertIn('rc=2 ip=10.0.0.1/8', output)
            self.assertEqual(calls.count(';+CGACT?'), 4)
            self.assertNotIn('proto update', calls)

    def test_failed_update_can_be_retried(self):
        for shell in self.each_shell():
            for failure in ('ADD_FAIL', 'REPORT_FAIL'):
                output, calls = self.refresh(shell, **{failure: '1'})
                self.assertIn('rc=1 ip=10.0.0.1/8', output)
                self.assertNotIn('ip -4 addr del', calls)
            self.calls.unlink(missing_ok=True)
            code = self.functions + self.proto + '''
REPORT_FAIL=1
refresh_bearer cgev || true
REPORT_FAIL=0
refresh_bearer cgev
echo "$ip4/$prefix4"
'''
            r = self.sh(shell, code)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('10.0.0.2/8', r.stdout)
            self.assertIn('ip -4 addr del 10.0.0.1/8', self.calls.read_text())

    def test_invalid_address_and_noncontiguous_mask_rejected(self):
        for address in ('999.0.0.2.255.0.0.0', '10.0.0.2.255.0.255.0'):
            self.reply.write_text(f'+CGCONTRDP: 1,6,internet,{address},,1.1.1.1,8.8.8.8\n')
            for shell in self.each_shell():
                output, calls = self.refresh(shell)
                self.assertIn('rc=1 ip=10.0.0.1/8', output)
                self.assertNotIn('proto update', calls)

    def test_same_ip_missing_from_kernel_is_restored(self):
        self.reply.write_text('+CGCONTRDP: 1,6,internet,10.0.0.1.255.0.0.0,,9.9.9.9,149.112.112.112\n')
        for shell in self.each_shell():
            _, calls = self.refresh(shell, KERNEL_MISSING='1')
            self.assertIn('ip -4 addr add 10.0.0.1/8', calls)
            self.assertIn('proto update wan', calls)
            self.assertNotIn('ip -4 addr del', calls)

    def test_sim_two_only_uses_its_owner(self):
        for shell in self.each_shell():
            _, calls = self.refresh(shell, extra='sim_slot=1\n')
            self.assertIn(f'at {self.tmp}/at4', calls)
            self.assertIn('AT+SPACTCARD=1;+CGCONTRDP=1', calls)
            self.assertNotIn(f'at {self.tmp}/at6', calls)

    def test_zero_dns_is_absent_and_ipv6_reply_does_not_hide_ipv4(self):
        self.reply.write_text('+CGCONTRDP: 1,6,internet,' + '.'.join(['0'] * 32) + ',,,\n'
                              '+CGCONTRDP: 1,6,internet,"10.0.0.2.255.0.0.0",,"1.1.1.1","0.0.0.0"\r\n')
        for shell in self.each_shell():
            output, calls = self.refresh(shell)
            self.assertIn('dns=1.1.1.1,', output)
            self.assertNotIn('proto dns 0.0.0.0', calls)

    def test_cgev_burst_coalesces_but_hint_during_refresh_is_retained(self):
        start = SOURCE.index('\t\tcase $line in')
        case = SOURCE[start:SOURCE.index('\n\t\tesac', start) + len('\n\t\tesac')]
        for shell in self.each_shell():
            code = f'pdp_pending="{self.tmp}/pending"\n'
            for line in ('+CGEV: NW ACT 11,17', '+CGEV: NW MODIFY 1,8', '+SPPCODATA: 1'):
                code += f"line='{line}'\n" + case + '\n'
            code += 'rm -f "$pdp_pending"\n'  # consumer begins its fresh AT read
            code += "line='+CGEV: NW DETACH'\n" + case + '\nrm -f "$pdp_pending"\n'
            r = self.sh(shell, code)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines(), ['pdp', 'pdp'])

    def test_native_event_source_observes_either_bank_without_waiting_for_nr3_log(self):
        start=SOURCE.index('( # NR0 is the shared unsolicited source.')
        producer=SOURCE[start:SOURCE.index('\nrenew_pending=0',start)]
        self.stub('ubus', '''
echo "listen $*" >>"$STUBLOG/calls"
printf '%s\\n' '{"mu300.modem.urc":{"channel":"stty_nr0","line":"+CGEV: NW PDN DEACT 1"}}' '{"mu300.modem.urc":{"channel":"stty_nr3","line":"+CGEV: ME PDN ACT 11"}}'
''')
        self.stub('tail','echo tail >>"$STUBLOG/calls"; exit 1')
        for shell in self.each_shell():
            self.calls.unlink(missing_ok=True)
            r=self.sh(shell,'native_events=1; config=wan; events="$STUBLOG/events"; pdp_pending="$STUBLOG/pending"\n'+producer+'\nwait "$urcmon"; cat "$events"; rm -f "$pdp_pending"')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.splitlines(),['pdp'])
            calls=self.calls.read_text()
            self.assertIn('listen -S listen mu300.modem.urc mu300cell-wan',calls)
            self.assertNotIn('tail',calls)

    def test_setup_starts_one_monitor_for_each_pdp_family(self):
        proto = (MONITOR.parent / 'mu300cell.sh').read_text()
        setup = proto[proto.index('proto_mu300cell_setup() {'):proto.index('\nproto_mu300cell_renew()')]
        setup = setup.replace('/run/', str(self.tmp) + '/')
        setup = setup.replace('/proc/sys/', str(self.tmp / 'sys') + '/')
        setup = setup.replace('/tmp/mu300cell.err', str(self.tmp / 'error'))
        setup = setup.replace('/opt/mu300/bin/mobile-data', str(self.stubs / 'mobile-data'))
        setup = setup.replace('/opt/mu300/bin/mu300-sim-env', str(self.tmp / 'sim-env'))
        (self.tmp / 'sim-env').write_text('mu300_sim_env() { MU300_SIM_SLOT=0; MU300_SIM_DEVICE=mob_sipa; }\n')
        self.stub('mobile-data', "printf '%s\\n' 'IP=10.0.0.2' 'IFACE=mob_sipa' 'PREFIX=8' 'DNS1=1.1.1.1' 'DNS2=8.8.8.8'")
        for shell in self.each_shell():
            for family in ('IP', 'IPV4V6'):
                self.calls.unlink(missing_ok=True)
                code = self.proto + setup + '''
json_get_vars() { apn=internet; pdptype=$TEST_PDP_TYPE; peerdns=1; }
proto_run_command() { echo "run $*" >>"$STUBLOG/calls"; }
proto_mu300cell_setup wan
'''
                r = self.sh(shell, code, TEST_PDP_TYPE=family)
                self.assertEqual(r.returncode, 0, r.stderr)
                commands = [line for line in self.calls.read_text().splitlines() if line.startswith('run ')]
                self.assertEqual(len(commands), 1)
                self.assertIn('mu300cell-v6.sh wan mob_sipa 10.0.0.2 8 1.1.1.1 8.8.8.8 1  0', commands[0])


    def test_ipv6_only_grant_removes_only_old_ipv4_after_reporting(self):
        self.reply.write_text('+CGCONTRDP: 1,6,internet,2001:db8::42,,2001:4860:4860::8888,::\n')
        for shell in self.each_shell():
            output,calls=self.refresh(shell)
            self.assertIn('rc=0 ip=/ dns=2001:4860:4860::8888,',output)
            self.assertNotIn('ip -4 addr add',calls)
            self.assertNotIn('ip -4 route replace',calls)
            self.assertIn('proto v6route :: 0 fe80::1 4096 100',calls)
            self.assertIn('proto dns 2001:4860:4860::8888',calls)
            self.assertLess(calls.index('proto update wan'),calls.index('ip -4 addr del 10.0.0.1/8'))
            self.assertNotIn('proto address',calls)

    def test_steady_ipv6_bearer_has_no_spurious_update_or_retry(self):
        self.reply.write_text('+CGCONTRDP: 1,6,internet,::8000:0:0:0,,2001:4860:4860::8888,::\n')
        for shell in self.each_shell():
            output,calls=self.refresh(shell,extra='ip4=; prefix4=; dns1=2001:4860:4860::8888; dns2=\n',KERNEL_MISSING='1')
            self.assertIn('rc=0',output)
            self.assertNotIn('proto update',calls)
            self.assertNotIn('ip -4 addr add',calls)
            self.assertNotIn('ip -4 route del',calls)

    def test_setup_accepts_ipv6_only_but_never_a_failed_attach(self):
        proto=(MONITOR.parent/'mu300cell.sh').read_text()
        setup=proto[proto.index('proto_mu300cell_setup() {'):proto.index('\nproto_mu300cell_renew()')]
        setup=setup.replace('/run/',str(self.tmp)+'/').replace('/proc/sys/',str(self.tmp/'sys')+'/')
        setup=setup.replace('/tmp/mu300cell.err',str(self.tmp/'error'))
        setup=setup.replace('/opt/mu300/bin/mobile-data',str(self.stubs/'mobile-data'))
        setup=setup.replace('/opt/mu300/bin/mu300-sim-env',str(self.tmp/'sim-env'))
        (self.tmp/'sim-env').write_text('mu300_sim_env() { MU300_SIM_SLOT=0; MU300_SIM_DEVICE=mob_sipa; }\n')
        self.stub('mobile-data', "printf '%s\\n' 'IP=' 'IFACE=mob_sipa' 'PDP_TYPE=IPV6' 'DNS1=2001:db8::53'; exit \"${DIAL_FAIL:-0}\"")
        for shell in self.each_shell():
            for fail in ('0','1'):
                self.calls.unlink(missing_ok=True)
                code=self.proto+setup+'''
json_get_vars() { apn=internet; pdptype=IPV4V6; peerdns=1; }
proto_run_command() { echo "run $*" >>"$STUBLOG/calls"; }
proto_notify_error() { echo "error $*" >>"$STUBLOG/calls"; }
proto_setup_failed() { :; }
proto_mu300cell_setup wan
'''
                r=self.sh(shell,code,DIAL_FAIL=fail)
                calls=self.calls.read_text()
                self.assertNotIn('proto address',calls)
                self.assertNotIn('ip -4 addr add',calls)
                if fail=='0':
                    self.assertEqual(r.returncode,0,r.stderr)
                    self.assertIn('run wan',calls)
                    self.assertIn('proto dns 2001:db8::53',calls)
                else:
                    self.assertNotEqual(r.returncode,0)
                    self.assertIn('error wan ATTACH_FAILED',calls)
                    self.assertNotIn('run wan',calls)


class PendingEvent(ShellTest):
    def setUp(self):
        super().setUp()
        self.code=SOURCE[SOURCE.index('retry_due=0 retry_step=0'):SOURCE.index('\nlast_v6=$(v6_snapshot)')] + '\nexec 3</dev/null\n'

    def test_busy_event_retries_then_success_clears_deadline(self):
        for shell in self.each_shell():
            r=self.sh(shell,self.code+'''
monitor_clock() { MONITOR_NOW=100; }
refresh_bearer() { return "$RESULT"; }
RESULT=1
for i in 1 2 3 4 5; do refresh_event cgev || :; echo "$retry_due"; done
RESULT=0; refresh_event cgev; echo "$retry_due:$retry_step"
RESULT=2; rc=0; refresh_event cgev || rc=$?; echo "$rc:$retry_due"
''')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.splitlines(),['101','103','105','107','101','0:0','2:0'])

    def test_other_events_do_not_postpone_pending_retry(self):
        for shell in self.each_shell():
            r=self.sh(shell,self.code+'''
retry_due=103; ticks=0; renew_pending=0
monitor_clock() { ticks=$((ticks+1)); MONITOR_NOW=$((99+ticks)); }
read() { event=v6; return 0; }
for i in 1 2 3 4; do read_event; echo "$event:$retry_due"; done
''')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.splitlines(),['v6:103','v6:103','v6:103','pdp-retry:103'])

    def test_renew_interrupt_retains_pending_event_and_idle_read_has_no_deadline(self):
        for shell in self.each_shell():
            r=self.sh(shell,self.code+'''
retry_due=103; renew_pending=0
monitor_clock() { MONITOR_NOW=100; }
read() { renew_pending=1; return 1; }
rc=0; read_event || rc=$?; echo "$rc:$retry_due"
retry_due=0
monitor_clock() { echo unexpected-clock; return 1; }
read() { event=v6; return 0; }
read_event; echo "$event:$retry_due"
''')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.splitlines(),['1:103','v6:0'])


class Watchdog(ShellTest):
    def setUp(self):
        super().setUp()
        self.context = PDP_SOURCE + MOBILE[MOBILE.index('watch_context_state() {'):MOBILE.index('\nwatch() {')]
        self.watch = MOBILE[MOBILE.index('watch() {'):MOBILE.index('\n# Radio quality')]
        self.watch = self.watch.replace('/run/', str(self.tmp) + '/')
        self.event_transport=self.tmp/'event-transport.sh'
        self.event_transport.write_text('watch_events_start() { :; }\nwatch_events_stop() { :; }\nwatch_next() { sleep "${MU300_WATCH_INTERVAL:-30}"; }\n')
        self.stub('sleep', '''
n=$(cat "$STUBLOG/round" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" >"$STUBLOG/round"
[ "$n" -le "${ROUNDS:-1}" ] || exit 99
''')
        self.stub('ip', "echo '1: sipa_eth0 inet 10.0.0.1/8 scope global sipa_eth0'")
        self.stub('jsonfilter', 'echo false')
        self.stub('ubus', 'echo "ubus $*" >>"$STUBLOG/calls"')
        self.stub('ifup', 'echo "ifup $*" >>"$STUBLOG/calls"')
        self.stub('led-status', ':')

    def test_context_forms_and_timeouts_are_distinct(self):
        for shell in self.each_shell():
            for reply, expected in [('+CGACT: 1,1\r\n+CGACT:11,1', 0),
                                    ('+CGACT:1,0', 1), ('+CGACT:11,1', 2), ('OK', 2)]:
                code = self.context + '\nCID=1\nat() { printf "%s\\n" "$REPLY"; }\nwatch_context_state'
                r = self.sh(shell, code, REPLY=reply)
                self.assertEqual(r.returncode, expected, r.stderr)

    def run_watch(self, **env):
        for name in ('calls', 'round'):
            (self.tmp / name).unlink(missing_ok=True)
        code = 'set -euo pipefail\n' + self.context + self.watch + '''
CID=1; DEV=/dev/null; IFACE=sipa_eth0; DEVICE=f50
MU300_NETIFD=1
modem_stack() { return 0; }
at_owner_alive() { return 1; }
mu300_sim_env() { MU300_SIM_DEVICE=sipa_eth0; }
tty_setup() { :; }
led_up() { :; }
radio_on() { echo radio_on >>"$STUBLOG/calls"; }
at() {
    echo "at $*" >>"$STUBLOG/calls"
    case "$1" in
        *CGACT*) [ "${AT_FAIL:-0}" = 0 ] || return 1; echo "${CGACT_REPLY:-+CGACT:1,1}" ;;
        *CGCONTRDP*) echo "${RDP_REPLY:-+CGCONTRDP: 1,6,internet,10.0.0.2.255.0.0.0,,1.1.1.1,8.8.8.8}" ;;
        *CFUN*) [ "${CFUN_FAIL:-0}" = 0 ] || return 1; echo "${CFUN_REPLY:-+CFUN: 1}" ;;
    esac
}
watch
'''
        r = self.sh(['bash'], code, **dict(dict(MU300_DATA_EVENTS_LIB=self.event_transport),**env))
        self.assertEqual(r.returncode, 99, r.stderr)
        return (self.tmp / 'calls').read_text()

    def test_address_change_requests_renew_not_redial(self):
        calls = self.run_watch()
        self.assertIn('ubus call network.interface.wan renew', calls)
        self.assertNotIn('ifup', calls)
        self.assertNotIn('CFUN', calls)
        self.assertNotIn('radio_on', calls)

    def test_at_failure_does_not_reconnect_or_turn_on_radio(self):
        calls = self.run_watch(AT_FAIL='1', ROUNDS='2')
        self.assertNotIn('CFUN', calls)
        self.assertNotIn('ifup', calls)
        self.assertNotIn('radio_on', calls)

    def test_confirmed_loss_recovers_but_failed_cfun_is_not_radio_off(self):
        calls = self.run_watch(CGACT_REPLY='+CGACT:1,0', CFUN_FAIL='1', ROUNDS='2')
        self.assertIn('ifup wan', calls)
        self.assertNotIn('radio_on', calls)

    def test_erased_contexts_still_repair_explicit_radio_off(self):
        calls = self.run_watch(CGACT_REPLY='OK', CFUN_REPLY='+CFUN: 0')
        self.assertIn('radio_on', calls)
        calls = self.run_watch(CGACT_REPLY='OK', CFUN_FAIL='1')
        self.assertNotIn('radio_on', calls)

    def test_ipv6_only_active_context_never_redials_or_renews_periodically(self):
        calls=self.run_watch(RDP_REPLY='+CGCONTRDP: 1,5,internet,::8000:0:0:0,,2001:db8::53,::',ROUNDS='2')
        self.assertNotIn('renew',calls)
        self.assertNotIn('ifup',calls)
        self.assertNotIn('CFUN',calls)
        self.assertNotIn('CGPADDR',calls)
