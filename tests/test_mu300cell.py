"""openwrt/overlay/lib/netifd/proto/mu300cell.sh: the netifd protocol handler, sourced with the netifd functions stubbed.

The script is sourced with INCLUDE_ONLY=1 (it then defines only the proto_mu300cell_* functions). The netifd
functions, `ip`, `mobile-data`, `mu300-led`, `sleep`, `logger` and `fw4` record their arguments in $STUBLOG/calls.
The script's absolute paths (/opt/mu300/bin, /tmp, /proc/sys) are pointed into the scratch directory in a copy;
sipa_eth0's disable_ipv6 and accept_ra are files there, so what the script writes into them is checked.

Relay mode (K35-K37, K30, D9; openwrt-luci only) has its files in openwrt/luci-overlay: the event monitor
mu300cell-v6.sh, ndp-learn, init.d/mu300-ndp and the first-boot settings in 91-mu300-luci (class Relay).
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

PROTO = TOP / 'openwrt' / 'overlay' / 'lib' / 'netifd' / 'proto' / 'mu300cell.sh'
LUCI = TOP / 'openwrt' / 'luci-overlay'
MONITOR = LUCI / 'lib' / 'netifd' / 'proto' / 'mu300cell-v6.sh'
NDP_LEARN = LUCI / 'opt' / 'mu300' / 'bin' / 'ndp-learn'
NDP_INIT = LUCI / 'etc' / 'init.d' / 'mu300-ndp'
LUCI_DEFAULTS = LUCI / 'etc' / 'uci-defaults' / '91-mu300-luci'

HARNESS = r'''
rec() { printf '%s\n' "$*" >> "$STUBLOG/calls"; }
proto_init_update() { rec proto_init_update "$@"; }
proto_add_ipv4_address() { rec proto_add_ipv4_address "$@"; }
proto_add_ipv4_route() { rec proto_add_ipv4_route "$@"; }
proto_add_dns_server() { rec proto_add_dns_server "$@"; }
proto_send_update() { rec proto_send_update "$@"; }
proto_kill_command() { rec proto_kill_command "$@"; }
proto_run_command() { rec proto_run_command "$@"; }
proto_config_add_string() { rec proto_config_add_string "$@"; }
proto_config_add_boolean() { rec proto_config_add_boolean "$@"; }
proto_config_add_array() { rec proto_config_add_array "$@"; }
proto_notify_error() { rec proto_notify_error "$@"; }
proto_block_restart() { rec proto_block_restart "$@"; }
proto_set_available() { rec proto_set_available "$@"; }
proto_setup_failed() { rec proto_setup_failed "$@"; }
proto_add_dynamic_defaults() { rec proto_add_dynamic_defaults; }
json_get_vars() { apn=$T_APN; pdptype=$T_PDPTYPE; peerdns=$T_PEERDNS; ipv6=$T_IPV6; apn_internal=${T_APN_INTERNAL:-}; pdptype_internal=${T_PDPTYPE_INTERNAL:-}
    auth=${T_AUTH:-}; username=${T_USERNAME:-}; password=${T_PASSWORD:-}; }
json_init() { rec json_init; }
json_add_string() { rec json_add_string "$@"; }
json_add_boolean() { rec json_add_boolean "$@"; }
json_close_object() { rec json_close_object; }
json_dump() { echo '{}'; }
ubus() { rec ubus "$@"; }
INCLUDE_ONLY=1
. "$SCRIPT"
'''

# What mobile-data's netifd path prints (MU300_NETIFD=1 mobile-data up): six KEY=VALUE lines, in this order.
UP_V4 = 'IFACE=sipa_eth0\\nIP=10.20.30.40\\nPREFIX=30\\nDNS1=1.1.1.1\\nDNS2=8.8.8.8\\nIID6=\\n'
UP_V6 = 'IFACE=sipa_eth0\\nIP=10.20.30.40\\nPREFIX=30\\nDNS1=1.1.1.1\\nDNS2=\\nIID6=0000:0000:1234:5678\\n'


class Mu300cell(ShellTest):
    def setUp(self):
        super().setUp()
        bindir = self.tmp / 'bin'
        bindir.mkdir()
        self.conf = self.tmp / 'proc' / 'net' / 'ipv6' / 'conf' / 'sipa_eth0'
        self.conf.mkdir(parents=True)
        text = PROTO.read_text()
        text = text.replace('/opt/mu300/bin', str(bindir)).replace('/tmp/mu300cell.err', str(self.tmp / 'err')) \
                   .replace('/proc/sys', str(self.tmp / 'proc'))
        self.script_copy = self.tmp / 'mu300cell.sh'
        self.script_copy.write_text(text)
        self.stub('sleep', 'echo "sleep $*" >> "$STUBLOG/calls"')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/calls"')
        self.stub('fw4', 'exit 1')
        # ip: records; `-4 -o addr show` lists the global v4 addresses the carrier "left behind" ($T_LEFT);
        # `-4 addr replace` fails when $T_NO_REPLACE is set (an ip without it).
        self.stub('ip', '''echo "ip $*" >> "$STUBLOG/calls"
case "$*" in
  "-4 -o addr show dev "*) for a in $T_LEFT; do echo "2: sipa_eth0    inet $a scope global sipa_eth0"; done ;;
  "-4 addr replace "*) [ -z "$T_NO_REPLACE" ] || exit 2 ;;
esac
exit 0''')
        self.mobile = bindir / 'mobile-data'
        self.set_mobile('up', 0)
        for n, body in (('mu300-led', 'echo "mu300-led $*" >> "$STUBLOG/calls"'),):
            p = bindir / n
            p.write_text('#!/bin/sh\n' + body + '\n')
            p.chmod(0o755)

    def set_mobile(self, out, rc, err=''):
        self.mobile.write_text('#!/bin/sh\necho "mobile-data $* env:$MU300_NETIFD:$MU300_PDP_TYPE:$MU300_IPV6" >> "$STUBLOG/calls"\n'
                               'echo "$1 $#:${MU300_APN_AUTH-unset}:${MU300_APN_USER-unset}:${MU300_APN_PASSWORD-unset}" >> "$STUBLOG/creds"\n'
                               f'[ "$1" = up ] || exit 0\nprintf \'{out}\'\necho \'{err}\' >&2\nexit {rc}\n')
        self.mobile.chmod(0o755)

    def run_proto(self, shell, func, out=UP_V4, rc=0, err='', ok=0, **env):
        self.set_mobile(out, rc, err)
        (self.tmp / 'calls').write_text('')
        for k in ('disable_ipv6', 'accept_ra'):
            (self.conf / k).write_text('-\n')
        e = dict(T_APN='internet', T_PDPTYPE='IP', T_PEERDNS='1', T_IPV6='', T_LEFT='', T_NO_REPLACE='',
                 SCRIPT=self.script_copy)
        e.update(env)
        # sleep as a function too: busybox ash runs its own sleep applet without looking at PATH
        r = self.sh(shell, 'sleep() { echo "sleep $*" >> "$STUBLOG/calls"; }\n' + HARNESS + f'{func} wan\n', **e)
        self.assertEqual(r.returncode, ok, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines()

    def test_setup_update_is_external(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup')
            self.assertIn('proto_init_update sipa_eth0 1 1', calls)
            self.assertIn('proto_add_ipv4_address 10.20.30.40 30', calls)
            self.assertIn('proto_send_update wan', calls)
            self.assertIn('mobile-data up internet env:1:IP:', calls)

    def test_internal_profile_and_teardown_use_boot_slot(self):
        helper = self.mobile.parent / 'mu300-sim'
        helper.write_text('#!/bin/sh\necho 1\n')
        helper.chmod(0o755)
        self.conf = self.conf.parent / 'sipa_eth8'
        self.conf.mkdir()
        for shell in self.each_shell():
            (self.tmp / 'creds').unlink(missing_ok=True)
            calls = self.run_proto(shell, 'proto_mu300cell_setup',
                                   out=UP_V4.replace('sipa_eth0', 'sipa_eth8'),
                                   T_APN='ctnet', T_APN_INTERNAL='cmnet', T_AUTH='pap', T_USERNAME='user', T_PASSWORD='secret')
            self.assertIn('mobile-data up cmnet env:1:IPV4V6:', calls)
            # the internal card dials its own APN: the external card's credentials are not offered to its carrier
            self.assertEqual((self.tmp / 'creds').read_text().splitlines()[0], 'up 2:::')
            self.assertIn('proto_init_update sipa_eth8 1 1', calls)
            self.assertIn('ip -4 route replace default dev sipa_eth8', calls)
            calls = self.run_proto(shell, 'proto_mu300cell_teardown')
            self.assertIn('ip -4 addr flush dev sipa_eth8 scope global', calls)
            self.assertIn('ip -6 route flush dev sipa_eth8', calls)
            self.assertFalse(any('sipa_eth0' in c for c in calls))

    def test_the_apn_credentials_reach_mobile_data_in_its_environment_only(self):
        # FINDINGS 65: username, password and auth of the wan interface; never an argument (ps), never in the log
        for shell in self.each_shell():
            (self.tmp / 'creds').unlink(missing_ok=True)
            calls = self.run_proto(shell, 'proto_mu300cell_setup', T_AUTH='chap', T_USERNAME='user@apn', T_PASSWORD='s3 cret')
            self.assertEqual((self.tmp / 'creds').read_text().splitlines()[0], 'up 2:chap:user@apn:s3 cret')
            self.assertNotIn('s3 cret', '\n'.join(calls))
            self.assertNotIn('user@apn', '\n'.join(calls))
            # a dial that fails: what is logged is mobile-data's own words, never the credentials
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=1, err='the modem refused the APN credentials (AT+CGPCO)', ok=1,
                                   T_AUTH='chap', T_USERNAME='user@apn', T_PASSWORD='s3 cret')
            self.assertIn('proto_notify_error wan ATTACH_FAILED', calls)
            self.assertNotIn('s3 cret', '\n'.join(calls))
            self.assertNotIn('user@apn', '\n'.join(calls))
            # none set: empty, so nothing of an environment the service was started with gets through
            (self.tmp / 'creds').unlink()
            self.run_proto(shell, 'proto_mu300cell_setup')
            self.assertEqual((self.tmp / 'creds').read_text().splitlines()[0], 'up 2:::')
            calls = self.run_proto(shell, 'proto_mu300cell_init_config')
            for name in ('auth', 'username', 'password'):
                self.assertIn(f'proto_config_add_string {name}', calls)

    def test_setup_installs_v4_address_and_route_itself(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', T_LEFT='9.9.9.9/32 10.20.30.40/30 10.1.1.1/24')
            self.assertIn('ip -4 addr replace 10.20.30.40/30 dev sipa_eth0', calls)
            self.assertIn('ip -4 addr del 9.9.9.9/32 dev sipa_eth0', calls)
            self.assertIn('ip -4 addr del 10.1.1.1/24 dev sipa_eth0', calls)
            self.assertNotIn('ip -4 addr del 10.20.30.40/30 dev sipa_eth0', calls)
            self.assertIn('ip -4 route replace default dev sipa_eth0', calls)
            # installed before netifd is told about it
            self.assertLess(calls.index('ip -4 addr replace 10.20.30.40/30 dev sipa_eth0'),
                            calls.index('proto_send_update wan'))
            # R34: and once more after the leftovers are gone: a leftover primary in the new address's subnet takes
            # its secondary (the new address) with it when it is deleted
            last_del = max(i for i, c in enumerate(calls) if c.startswith('ip -4 addr del '))
            again = [i for i, c in enumerate(calls) if c == 'ip -4 addr replace 10.20.30.40/30 dev sipa_eth0']
            self.assertGreater(again[-1], last_del, calls)
            self.assertLess(again[-1], calls.index('proto_send_update wan'))

    def test_setup_adds_when_replace_fails(self):
        """R34: an `ip` that refuses `addr replace` still gets the address, by `addr add`."""
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', T_NO_REPLACE='1')
            self.assertIn('ip -4 addr add 10.20.30.40/30 dev sipa_eth0', calls)
            self.assertLess(calls.index('ip -4 addr add 10.20.30.40/30 dev sipa_eth0'),
                            calls.index('proto_send_update wan'))

    def test_v4_bearer_switches_ipv6_off_after_the_update(self):
        """R34: an IPv4-only bearer gets disable_ipv6 1 (FINDINGS 13f), written after proto_send_update."""
        for shell in self.each_shell():
            self.run_proto(shell, 'proto_mu300cell_setup')
            self.assertEqual((self.conf / 'disable_ipv6').read_text().strip(), '1', shell)
            self.assertEqual((self.conf / 'accept_ra').read_text().strip(), '-', shell)

    def test_setup_flushes_v6_first(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup')
            a = calls.index('ip -6 addr flush dev sipa_eth0 scope global')
            r = calls.index('ip -6 route flush dev sipa_eth0')
            self.assertLess(max(a, r), calls.index('proto_send_update wan'))

    def test_teardown(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_teardown')
            self.assertEqual(calls[0], 'ubus call network.interface.wan status')
            self.assertEqual(calls[1], 'proto_kill_command wan')
            self.assertEqual(calls[2].split(' env:')[0], 'mobile-data down')
            for c in ('ip -4 addr flush dev sipa_eth0 scope global', 'ip -4 route del default dev sipa_eth0',
                      'ip -6 addr flush dev sipa_eth0 scope global', 'ip -6 route flush dev sipa_eth0'):
                self.assertIn(c, calls[3:])

    def test_teardown_uses_active_bearer_and_rejects_other_interfaces(self):
        self.stub('jsonfilter', 'echo "$T_BEARER"')
        for shell in self.each_shell():
            for bearer, expected in [('sipa_eth8', 'sipa_eth8'), ('br-lan', 'sipa_eth0'),
                                     ('sipa_eth8;touch injected', 'sipa_eth0')]:
                calls = self.run_proto(shell, 'proto_mu300cell_teardown', T_BEARER=bearer)
                flushes = [c for c in calls if c.startswith('ip ')]
                self.assertEqual(flushes, [f'ip -4 addr flush dev {expected} scope global',
                    f'ip -4 route del default dev {expected}', f'ip -6 addr flush dev {expected} scope global',
                    f'ip -6 route flush dev {expected}'])

    def test_attach_failure_still_sleeps_20(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=1, err='no carrier', ok=1)
            self.assertIn('proto_notify_error wan ATTACH_FAILED', calls)
            self.assertIn('sleep 20', calls)
            self.assertLess(calls.index('sleep 20'), calls.index('proto_setup_failed wan'))
            self.assertFalse(any(c.startswith('proto_send_update') for c in calls))

    def test_no_modem_blocks_restart(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=3, err='no modem', ok=1)
            self.assertIn('proto_notify_error wan NO_MODEM', calls)
            self.assertIn('proto_block_restart wan', calls)
            self.assertNotIn('sleep 20', calls)

    def test_held_for_roaming_is_retried_a_minute_later_without_a_log_line(self):
        """FINDINGS 120: exit 4 is data held back for roaming. DATA_ROAMING, netifd tries again after a minute (the
        way data comes back at home), no restart block, no address, and no log line of its own (mobile-data said it
        once)."""
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=4, err='', ok=1)
            self.assertIn('proto_notify_error wan DATA_ROAMING', calls)
            self.assertNotIn('proto_block_restart wan', calls)
            self.assertIn('sleep 60', calls)
            self.assertLess(calls.index('sleep 60'), calls.index('proto_setup_failed wan'))
            self.assertFalse([c for c in calls if c.startswith(('proto_send_update', 'logger', 'proto_init_update'))], calls)

    def test_an_empty_slot_is_not_redialled(self):
        """FINDINGS 186: exit 6 is no card in the slot in use: SIM_MISSING, no retry every 20 s (unavailable; the card's event brings
        the WAN up when one answers)."""
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=6, err='', ok=1)
            self.assertIn('proto_notify_error wan SIM_MISSING', calls)
            self.assertIn('proto_set_available wan 0', calls)
            self.assertNotIn('proto_block_restart wan', calls)
            self.assertNotIn('sleep 20', calls)

    def test_a_sim_radio_switched_off_is_not_retried(self):
        """FINDINGS 185: exit 7 is the SIM's radio switched off in its settings: SIM_RADIO_DISABLED, no retry (the
        interface is unavailable until mu300-sim-radio switches it on and brings the WAN up), no address."""
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=7, err='', ok=1)
            self.assertIn('proto_notify_error wan SIM_RADIO_DISABLED', calls)
            self.assertIn('proto_set_available wan 0', calls)
            self.assertNotIn('proto_block_restart wan', calls)
            self.assertNotIn('sleep 20', calls)
            self.assertFalse([c for c in calls if c.startswith(('proto_send_update', 'proto_init_update'))], calls)

    def test_ipv6_bearer_still_adds_extendprefix_interface(self):
        """Without ipv6 'relay' (plain OpenWrt, or the option unset or 'extend'): today's RFC 7278 design, no monitor."""
        for ipv6 in ('', 'extend'):
            for shell in self.each_shell():
                calls = self.run_proto(shell, 'proto_mu300cell_setup', out=UP_V6, T_PDPTYPE='IPV4V6', T_IPV6=ipv6)
                self.assertIn('ip -6 addr add fe80::0000:0000:1234:5678/64 dev sipa_eth0', calls)
                self.assertIn('json_add_string name wan_6', calls)
                self.assertIn('json_add_boolean extendprefix 1', calls)
                self.assertIn('proto_add_dynamic_defaults', calls)
                self.assertTrue(any(c.startswith('ubus call network add_dynamic') for c in calls))
                # the link-local is added after the flush
                self.assertLess(calls.index('ip -6 addr flush dev sipa_eth0 scope global'),
                                calls.index('ip -6 addr add fe80::0000:0000:1234:5678/64 dev sipa_eth0'))
                self.assertFalse([c for c in calls if c.startswith('proto_run_command')], calls)
                self.assertEqual((self.conf / 'accept_ra').read_text().strip(), '-', shell)

    def test_ipv6_bearer_turns_ipv6_back_on_after_a_teardown(self):
        """The teardown's mobile-data down leaves disable_ipv6 1, and netifd does not reset it for a device that
        stayed: the IPv6 bearer's setup turns it on itself (a WAN restart lost IPv6 until a reboot on an F50)."""
        for shell in self.each_shell():
            self.run_proto(shell, 'proto_mu300cell_setup', out=UP_V6, T_PDPTYPE='IPV4V6')
            self.assertEqual((self.conf / 'disable_ipv6').read_text().strip(), '0', shell)
        body = PROTO.read_text()
        branch = body[body.index('elif [ -n "$iid" ]; then'):]
        self.assertLess(branch.index('echo 0 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"'),
                        branch.index('ip -6 addr add "fe80::$iid/64"'))

    def test_config_has_the_ipv6_option_and_renew(self):
        """K35: `option ipv6` (relay / extend) is a protocol option, and netifd may ask for a renew."""
        for shell in self.each_shell():
            (self.tmp / 'calls').write_text('')
            r = self.sh(shell, HARNESS + 'proto_mu300cell_init_config; echo "renew=$renew_handler"',
                        T_APN='', T_PDPTYPE='', T_PEERDNS='', T_IPV6='', SCRIPT=self.script_copy)
            self.assertIn('renew=1', r.stdout, r.stderr)
            self.assertIn('proto_config_add_string ipv6', (self.tmp / 'calls').read_text().splitlines())

    def test_relay_starts_the_monitor(self):
        """K35, K36, K64: ipv6 'relay' with a dual-stack context. mobile-data is told (MU300_IPV6=relay), sipa_eth0
        takes RAs (disable_ipv6 0, accept_ra 2, after the 1->0 and 0->2 cycles), and after the first update the event
        monitor runs as netifd's protocol task with the fork's arguments. No extendprefix interface: odhcpd relays."""
        for out in (UP_V4, UP_V6):          # the carrier filled +CGCONTRDP's v6 address, or did not
            for shell in self.each_shell():
                calls = self.run_proto(shell, 'proto_mu300cell_setup', out=out, T_PDPTYPE='IPV4V6', T_IPV6='relay')
                self.assertIn('mobile-data up internet env:1:IPV4V6:relay', calls)
                dns2 = '8.8.8.8' if out == UP_V4 else ''
                run = ('proto_run_command wan /lib/netifd/proto/mu300cell-v6.sh wan sipa_eth0 10.20.30.40 30 1.1.1.1 '
                       f'{dns2} 1')
                self.assertIn(run, calls)
                self.assertLess(calls.index('proto_send_update wan'), calls.index(run))
                self.assertEqual((self.conf / 'disable_ipv6').read_text().strip(), '0', shell)
                self.assertEqual((self.conf / 'accept_ra').read_text().strip(), '2', shell)
                self.assertFalse([c for c in calls if 'extendprefix' in c or c.startswith('ubus')], calls)
                self.assertTrue([c for c in calls if c.startswith('proto_init_update sipa_eth0 1 1')], calls)
        # the 1->0 and 0->2 cycles, in that order, after the v6 flush and before the first update
        text = PROTO.read_text()
        body = text[text.index('proto_mu300cell_setup() {'):text.index('proto_mu300cell_renew() {')]
        cycle = [body.index(s) for s in ('ip -6 route flush dev "$ifname"', 'echo 1 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"',
                                         'echo 0 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"',
                                         'echo 0 > "/proc/sys/net/ipv6/conf/$ifname/accept_ra"',
                                         'echo 2 > "/proc/sys/net/ipv6/conf/$ifname/accept_ra"', 'proto_send_update')]
        self.assertEqual(cycle, sorted(cycle))

    def test_relay_on_an_ipv4_context_is_ipv4(self):
        """ipv6 'relay' with pdptype IP: no monitor, no RAs, IPv6 off on the bearer as before."""
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', T_PDPTYPE='IP', T_IPV6='relay')
            self.assertFalse([c for c in calls if c.startswith('proto_run_command')], calls)
            self.assertEqual((self.conf / 'disable_ipv6').read_text().strip(), '1', shell)
            self.assertEqual((self.conf / 'accept_ra').read_text().strip(), '-', shell)

    def test_renew_signals_the_monitor(self):
        """K35: renew sends SIGUSR1 to the protocol task (the monitor re-reads the bearer); nothing else. A shell
        that cannot name the signal (dash) sends nothing; netifd runs busybox ash, which can."""
        for shell in self.each_shell():
            num = subprocess.run(shell + ['-c', 'kill -l SIGUSR1'], capture_output=True, text=True).stdout.strip()
            calls = self.run_proto(shell, 'proto_mu300cell_renew')
            self.assertEqual(calls, [f'proto_kill_command wan {num}'] if num else [], shell)


class Relay(ShellTest):
    """The luci-overlay half of relay mode: mu300cell-v6.sh, ndp-learn, init.d/mu300-ndp, 91-mu300-luci."""

    def test_reap_kills_the_old_monitor_without_a_file(self):
        """Final review minor 6: reap() kills the processes a previous monitor left (matched in a ps snapshot) and
        writes no file: there was a fixed /tmp/mu300cell-reap.ps written by root."""
        text = MONITOR.read_text()
        m = re.search(r'\nreap\(\) \{\n.*?\n\}\n', text, re.S)
        self.assertTrue(m)
        self.assertNotIn('/tmp/', m.group(0))
        for shell in self.each_shell():
            old = subprocess.Popen(['sleep', '30'])
            other = subprocess.Popen(['sleep', '30'])
            try:
                self.stub('ps', f'''printf '%s\\n' '  PID USER       VSZ STAT COMMAND' \\
  ' {old.pid} root      1234 S    /bin/sh /lib/netifd/proto/mu300cell-v6.sh wan sipa_eth0 10.0.0.1' \\
  ' {other.pid} root      1234 S    /bin/sh /lib/netifd/proto/mu300cell-v6.sh wwan sipa_eth1 10.0.0.1' ''')
                before = sorted(self.tmp.rglob('*'))
                r = self.sh(shell, m.group(0) + 'cd "$STUBLOG" && reap "mu300cell-v6.sh wan sipa_eth0"')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(sorted(self.tmp.rglob('*')), before)
                self.assertIsNotNone(old.wait(timeout=5))
                self.assertIsNone(other.poll())
            finally:
                for p in (old, other):
                    if p.poll() is None:
                        p.kill()
                    p.wait()

    def test_monitor_probe_exits_at_once(self):
        """netifd probes every *.sh in its proto directory with '<script> "" dump'; the monitor is not a protocol and
        must exit at once (it would otherwise block the scan, or wait on a fifo for ever). /lib/functions.sh does not
        exist here, so getting past the probe check would fail."""
        for shell in self.each_shell():
            for args in (['', 'dump'], ['dump']):
                r = subprocess.run(shell + [str(MONITOR)] + args, capture_output=True, text=True, timeout=5,
                                   env=self.env())
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), (shell, args))

    def test_monitor_once_reports_external(self):
        """K36: one report (--once): the v4 address and route, the RA's address with its lifetimes, the RA default
        route, the DNS servers - all address-external, as setup sent them."""
        text = MONITOR.read_text()
        harness = self.tmp / 'netifd.sh'
        harness.write_text('''rec() { printf '%s\\n' "$*" >> "$STUBLOG/calls"; }
proto_init_update() { rec proto_init_update "$@"; }
proto_add_ipv4_address() { rec proto_add_ipv4_address "$@"; }
proto_add_ipv4_route() { rec proto_add_ipv4_route "$@"; }
proto_add_ipv6_address() { rec proto_add_ipv6_address "$@"; }
proto_add_ipv6_route() { rec proto_add_ipv6_route "$@"; }
proto_add_dns_server() { rec proto_add_dns_server "$@"; }
proto_send_update() { rec proto_send_update "$@"; }
''')
        text = text.replace('. /lib/functions.sh\n', '').replace('. /lib/netifd/netifd-proto.sh', f'. "{harness}"')
        copy = self.tmp / 'mu300cell-v6.sh'
        copy.write_text(text)
        self.stub('ip', '''case "$*" in
  "-6 addr show dev sipa_eth0 scope global") printf '%s\\n' \\
    '5: sipa_eth0: <POINTOPOINT,UP,LOWER_UP> mtu 1500 state UNKNOWN qlen 1000' \\
    '    inet6 2001:db8:1:2:a:b:c:d/64 scope global dynamic mngtmpaddr' \\
    '       valid_lft 7100sec preferred_lft 3500sec' ;;
  "-6 route show dev sipa_eth0 default proto ra") echo 'default via fe80::1 proto ra metric 1024 expires 1700sec hoplimit 64 pref medium' ;;
esac''')
        for shell in self.each_shell():
            (self.tmp / 'calls').write_text('')
            r = subprocess.run(shell + [str(copy), 'wan', 'sipa_eth0', '10.20.30.40', '30', '1.1.1.1', '8.8.8.8', '1',
                                        '--once'], capture_output=True, text=True, timeout=10, env=self.env())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'calls').read_text().splitlines(), [
                'proto_init_update sipa_eth0 1 1', 'proto_add_ipv4_address 10.20.30.40 30',
                'proto_add_ipv4_route 0.0.0.0 0', 'proto_add_ipv6_address 2001:db8:1:2:a:b:c:d 64 3500 7100',
                'proto_add_ipv6_route :: 0 fe80::1 4096 1700', 'proto_add_dns_server 1.1.1.1',
                'proto_add_dns_server 8.8.8.8', 'proto_send_update wan'], shell)

    def test_ndp_learn_prefix(self):
        """K37: the bearer's /64 from /proc/net/if_inet6 (fixed-width hex, so compressed hextets cannot confuse it):
        only sipa_eth0's global (scope 00) address counts, never link-local or another interface."""
        fake = self.tmp / 'if_inet6'
        fake.write_text('fe800000000000000000000000000001 05 40 20 80 sipa_eth0\n'
                        '2a0200000db800010000000000000001 03 40 00 80    br-lan\n'
                        '240e0388000a12340000000000000abc 05 40 00 00 sipa_eth0\n')
        for shell in self.each_shell():
            r = self.script(shell, NDP_LEARN, '--prefix', MU300_IF_INET6=fake)
            self.assertEqual((r.returncode, r.stdout), (0, '240e:388:a:1234::/64\n'), (shell, r.stderr))
        fake.write_text('fe800000000000000000000000000001 05 40 20 80 sipa_eth0\n')
        for shell in self.each_shell():
            r = self.script(shell, NDP_LEARN, '--prefix', MU300_IF_INET6=fake)
            self.assertEqual((r.returncode, r.stdout), (0, ''), (shell, r.stderr))

    # Security review: ndp-learn takes nothing from the LAN. ip is a stub that logs every call to $STUBLOG/ipcalls;
    # the neighbour table on br-lan and the host routes on sipa_eth0 are hostile ($T_NEIGH, $T_WANROUTES).
    HOSTILE_NEIGH = ('2001:4860:4860::8888 lladdr 02:00:00:00:00:01 REACHABLE\n'      # off-prefix (a DNS server)
                     '240e:388:a:1234::5 lladdr 02:00:00:00:00:02 REACHABLE\n'        # inside the prefix
                     '240e:388:a:1234::1 lladdr 02:00:00:00:00:03 REACHABLE\n'        # the router's own address
                     '240e:388:a:1234:: lladdr 02:00:00:00:00:04 STALE\n'             # subnet-router anycast
                     'fe80::1 lladdr 02:00:00:00:00:05 REACHABLE\n'
                     'ff02::1 lladdr 33:33:00:00:00:01 NOARP\n'
                     '-6 lladdr 02:00:00:00:00:06 REACHABLE\n'
                     '2a00::1;reboot lladdr 02:00:00:00:00:07 REACHABLE\n')
    HOSTILE_WAN = ('240e:388:a:1234::5 proto static metric 1024\n'                   # odhcpd's stray host route
                   '2001:4860:4860::8888 proto static metric 1024\n'                 # off-prefix: not ours to delete
                   '240e:388:a:1234::6;reboot proto static\n'
                   '240e:388:a:1234:-x proto static\n'
                   '240e:388:a:1234::/64 proto ra metric 256\n'
                   'default via fe80::1 proto ra metric 1024\n')

    def ndp_once(self, shell, neigh, wan):
        fake = self.tmp / 'if_inet6'
        fake.write_text('240e0388000a12340000000000000001 05 40 00 00 sipa_eth0\n')
        (self.tmp / 'neigh').write_text(neigh)
        (self.tmp / 'wan').write_text(wan)
        (self.tmp / 'ipcalls').write_text('')
        self.stub('ip', '''echo "ip $*" >> "$STUBLOG/ipcalls"
case "$*" in
  "-6 neigh show"*) cat "$STUBLOG/neigh" ;;
  "-6 route show dev sipa_eth0") cat "$STUBLOG/wan" ;;
esac
exit 0''')
        self.stub('logger', 'exit 0')
        r = self.script(shell, NDP_LEARN, '--once', MU300_IF_INET6=fake)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'ipcalls').read_text().splitlines()

    def test_ndp_learn_takes_nothing_from_the_lan(self):
        """Security review: a LAN client's spoofed neighbour advertisements must not make the router route any
        address (off-prefix, its own, link-local, multicast, anycast, malformed) to br-lan: ndp-learn does not read
        the neighbour table at all, adds no host route and no proxy entry. The only route it installs is the
        bearer's /64 on br-lan, derived from the kernel's own address."""
        for shell in self.each_shell():
            calls = self.ndp_once(shell, self.HOSTILE_NEIGH, '')
            self.assertIn('ip -6 route replace 240e:388:a:1234::/64 dev br-lan metric 128', calls)
            self.assertFalse([c for c in calls if 'neigh' in c], calls)
            adds = [c for c in calls if ' add ' in f'{c} ' or c.startswith('ip -6 route replace')]
            self.assertEqual(set(adds), {'ip -6 route replace 240e:388:a:1234::/64 dev br-lan metric 128'}, calls)

    def test_ndp_learn_lan_route_wins_over_the_bearer(self):
        """Review I2: the carrier's RA puts the same /64 on sipa_eth0 at metric 256; the br-lan /64 must have a
        lower metric, or NAT66 replies to clients leave by the bearer. A re-run replaces it in place (one route per
        round), and the metric-1024 route an earlier ndp-learn installed is removed, never added."""
        for shell in self.each_shell():
            for _ in range(2):
                calls = self.ndp_once(shell, '', self.HOSTILE_WAN)
                lan = [c for c in calls if 'br-lan' in c and not c.startswith('ip -6 route show')]
                self.assertEqual(lan, ['ip -6 route replace 240e:388:a:1234::/64 dev br-lan metric 128',
                                       'ip -6 route del 240e:388:a:1234::/64 dev br-lan metric 1024'], (shell, calls))
            m = re.search(r'LAN_METRIC=(\d+)', NDP_LEARN.read_text())
            self.assertTrue(m and int(m.group(1)) < 256, 'the br-lan /64 must beat the RA route (metric 256)')

    def test_ndp_learn_removes_only_valid_in_prefix_wan_host_routes(self):
        """Host routes inside the /64 on sipa_eth0 (odhcpd's, which override the LAN /64) are removed; an
        off-prefix one, the /64, the default and anything that is not strictly an IPv6 address are left alone and
        never reach ip's arguments."""
        for shell in self.each_shell():
            calls = self.ndp_once(shell, '', self.HOSTILE_WAN)
            dels = [c for c in calls if ' del ' in c and 'sipa_eth0' in c]
            self.assertEqual(dels, ['ip -6 route del 240e:388:a:1234::5/128 dev sipa_eth0'], calls)
            self.assertFalse([c for c in calls if 'reboot' in c or ' -x' in c], calls)

    # #87: the old SLAAC address stays on sipa_eth0 beside the new one after a PDP re-activation without ifdown.
    TWO_PREFIXES = ('240e0388000a12340000000000000001 05 40 00 00 sipa_eth0\n'
                    '240e0388000a99990000000000000002 05 40 00 00 sipa_eth0\n'
                    '240e0388000a12340000000000000abc 05 40 00 00 sipa_eth0\n')    # same /64 again: listed once

    def test_ndp_learn_prefix_lists_every_bearer_prefix(self):
        """#87: --prefix prints each /64 the bearer holds, once and sorted, not only the first if_inet6 lists."""
        fake = self.tmp / 'if_inet6'
        fake.write_text('2a0200000db800010000000000000001 03 40 00 80    br-lan\n' + self.TWO_PREFIXES)
        for shell in self.each_shell():
            r = self.script(shell, NDP_LEARN, '--prefix', MU300_IF_INET6=fake)
            self.assertEqual((r.returncode, r.stdout), (0, '240e:388:a:1234::/64\n240e:388:a:9999::/64\n'),
                             (shell, r.stderr))

    def test_ndp_learn_routes_every_bearer_prefix(self):
        """#87: with two /64s on sipa_eth0, both go to br-lan (LAN clients may hold addresses in either: odhcpd
        relayed the RA of each), and stray host routes inside either are removed from sipa_eth0."""
        fake = self.tmp / 'if_inet6'
        fake.write_text(self.TWO_PREFIXES)
        (self.tmp / 'wan').write_text('240e:388:a:1234::5 proto static metric 1024\n'
                                      '240e:388:a:9999::6 proto static metric 1024\n'
                                      '2001:4860:4860::8888 proto static metric 1024\n')
        self.stub('ip', '''echo "ip $*" >> "$STUBLOG/ipcalls"
case "$*" in "-6 route show dev sipa_eth0") cat "$STUBLOG/wan" ;; esac
exit 0''')
        self.stub('logger', 'exit 0')
        for shell in self.each_shell():
            (self.tmp / 'ipcalls').write_text('')
            r = self.script(shell, NDP_LEARN, '--once', MU300_IF_INET6=fake)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = (self.tmp / 'ipcalls').read_text().splitlines()
            self.assertEqual([c for c in calls if 'replace' in c], [
                'ip -6 route replace 240e:388:a:1234::/64 dev br-lan metric 128',
                'ip -6 route replace 240e:388:a:9999::/64 dev br-lan metric 128'], (shell, calls))
            self.assertEqual([c for c in calls if ' del ' in c and 'sipa_eth0' in c], [
                'ip -6 route del 240e:388:a:1234::5/128 dev sipa_eth0',
                'ip -6 route del 240e:388:a:9999::6/128 dev sipa_eth0'], (shell, calls))

    def test_ndp_learn_drops_only_the_prefix_that_left(self):
        """#87: across rounds of one ndp-learn, a /64 that left the bearer loses its br-lan route (both metrics),
        the one that stays keeps it, and a new one is logged once; with none left, the last one is dropped too."""
        text = NDP_LEARN.read_text()
        funcs = text[:text.index('\ncase ${1:-} in')]
        fake = self.tmp / 'if_inet6'
        one = '240e0388000a99990000000000000002 05 40 00 00 sipa_eth0\n'
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/ipcalls"; exit 0')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/ipcalls"; exit 0')
        code = (funcs + '\nround() { cp "$1" "$IF_INET6"; echo "-- round" >> "$STUBLOG/ipcalls"; learn_once; }\n'
                f'round "$STUBLOG/two"; round "$STUBLOG/one"; round "$STUBLOG/one"; round "$STUBLOG/none"\n')
        (self.tmp / 'two').write_text(self.TWO_PREFIXES)
        (self.tmp / 'one').write_text(one)
        (self.tmp / 'none').write_text('')
        for shell in self.each_shell():
            (self.tmp / 'ipcalls').write_text('')
            r = self.sh(shell, code, MU300_IF_INET6=fake, MU300_RA_DEPRECATE='true')
            self.assertEqual(r.returncode, 0, r.stderr)
            rounds = (self.tmp / 'ipcalls').read_text().split('-- round\n')[1:]
            # (the withdrawal from the LAN has tests of its own)
            changes = [[c for c in rnd.splitlines() if (c.startswith('logger') and 'withdraw' not in c)
                        or (' del ' in c and 'br-lan' in c
                        and 'metric 1024' not in c)] for rnd in rounds]
            self.assertEqual(changes, [
                ['logger -t ndp-learn LAN prefix route: 240e:388:a:1234::/64 dev br-lan metric 128',
                 'logger -t ndp-learn LAN prefix route: 240e:388:a:9999::/64 dev br-lan metric 128'],
                ['ip -6 route del 240e:388:a:1234::/64 dev br-lan metric 128'],
                [],
                ['ip -6 route del 240e:388:a:9999::/64 dev br-lan metric 128']], (shell, rounds))
            self.assertIn('ip -6 route del 240e:388:a:1234::/64 dev br-lan metric 1024', rounds[1], shell)
            self.assertIn('ip -6 route replace 240e:388:a:9999::/64 dev br-lan metric 128', rounds[2], shell)
            self.assertNotIn('replace', rounds[3], shell)

    def withdraw_rounds(self, shell, *states):
        """Rounds of one ndp-learn over bearer STATES ('a', 'b', 'ab', ''), returning per round the ra-deprecate
        calls (a stub) and the ndp-learn log lines."""
        text = NDP_LEARN.read_text()
        funcs = text[:text.index('\ncase ${1:-} in')]
        lines = {'a': '240e0388000a12340000000000000001 05 40 00 00 sipa_eth0\n',
                 'b': '240e0388000a99990000000000000002 05 40 00 00 sipa_eth0\n'}
        for st in set(states):
            (self.tmp / f'st-{st or "none"}').write_text(''.join(lines[c] for c in st))
        self.stub('ip', 'case "$*" in "-6 route show default dev sipa_eth0") cat "$STUBLOG/defroute" ;; esac\nexit 0')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/calls"; exit 0')
        self.stub('ra-deprecate', 'echo "ra-deprecate $*" >> "$STUBLOG/calls"; exit 0')
        (self.tmp / 'defroute').write_text('default via fe80::1 proto ra metric 1024 expires 1734sec pref medium\n')
        code = (funcs + '\nround() { cp "$STUBLOG/st-$1" "$IF_INET6"; echo "-- round" >> "$STUBLOG/calls"; '
                'learn_once; }\n')
        code += ''.join(f'round {st or "none"}\n' for st in states)
        (self.tmp / 'calls').write_text('')
        r = self.sh(shell, code, MU300_IF_INET6=self.tmp / 'if_inet6', MU300_RA_DEPRECATE=self.stubs / 'ra-deprecate')
        self.assertEqual(r.returncode, 0, r.stderr)
        rounds = (self.tmp / 'calls').read_text().split('-- round\n')[1:]
        return [[c for c in rnd.splitlines() if 'ra-deprecate' in c or 'withdraw' in c] for rnd in rounds]

    def test_ndp_learn_withdraws_a_renumbered_prefix_from_the_lan(self):
        """#87: when the carrier moves the bearer to another /64, the old one is withdrawn from the LAN (preferred
        lifetime 0) in three rounds, with the carrier router's remaining lifetime; then nothing more."""
        ra = 'ra-deprecate'
        for shell in self.each_shell():
            # a PDP re-activation without ifdown: the new /64 next to the old, then the old one gone
            got = self.withdraw_rounds(shell, 'a', 'ab', 'b', 'b', 'b', 'b')
            send = f'{ra} br-lan 1734 240e:388:a:1234::/64'
            self.assertEqual(got, [[], [], ['logger -t ndp-learn withdrawing from the LAN: 240e:388:a:1234::/64', send],
                                   [send], [send], []], shell)

    def test_ndp_learn_withdraws_after_a_wan_restart_only_on_a_new_prefix(self):
        """#87: a WAN restart flushes the bearer (no /64 for a while). The same /64 coming back is never withdrawn
        (that would take the live prefix from every client); a different one makes the old one withdrawn."""
        ra = 'ra-deprecate'
        for shell in self.each_shell():
            with self.subTest(back='same'):
                self.assertEqual(self.withdraw_rounds(shell, 'a', '', '', 'a', 'a', 'a'), [[]] * 6)
            with self.subTest(back='another'):
                got = self.withdraw_rounds(shell, 'a', '', 'b', 'b', 'b', 'b')
                self.assertEqual(got[:2], [[], []])
                self.assertEqual(got[2][1:] + got[3] + got[4], [f'{ra} br-lan 1734 240e:388:a:1234::/64'] * 3)
                self.assertEqual(got[5], [])

    def test_ndp_learn_router_lifetime(self):
        """The withdrawing RA refreshes the clients' default router: never 0, at most 9000, 1800 without an expiry."""
        text = NDP_LEARN.read_text()
        funcs = text[:text.index('\ncase ${1:-} in')]
        for route, want in (('default via fe80::1 proto ra metric 1024 expires 1734sec pref medium', '1734'),
                            ('default via fe80::1 proto ra metric 1024 pref medium', '1800'),
                            ('', '1800'),
                            ('default via fe80::1 proto ra expires 0sec', '1800'),
                            ('default via fe80::1 proto ra expires 65535sec', '9000')):
            (self.tmp / 'defroute').write_text(route + '\n')
            self.stub('ip', 'cat "$STUBLOG/defroute"')
            for shell in self.each_shell():
                r = self.sh(shell, funcs + '\nrouter_lifetime\n')
                self.assertEqual((r.returncode, r.stdout), (0, want + '\n'), (shell, route, r.stderr))

    def test_ra_deprecate_is_in_the_panel_image(self):
        """ra-deprecate needs ucode's socket module, which the panel system's build installs."""
        build = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('[ -d /in/luci-plugin ] && apk add ucode-mod-socket', build)
        ra = (LUCI / 'opt' / 'mu300' / 'bin' / 'ra-deprecate').read_text()
        self.assertTrue(ra.startswith('#!/usr/bin/ucode\n'))
        self.assertIn("import * as socket from 'socket';", ra)

    def test_ndp_learn_never_routes_a_bad_prefix(self):
        """A bearer address that is not global unicast (multicast, link-local) gives no prefix, so no route."""
        fake = self.tmp / 'if_inet6'
        for line in ('ff020000000000000000000000000001 05 40 00 00 sipa_eth0\n',
                     'fe800000000000000000000000000001 05 40 00 00 sipa_eth0\n',
                     'fd661126795600000000000000000001 05 40 00 00 sipa_eth0\n',     # ULA
                     '00000000000000000000ffff0a000001 05 40 00 00 sipa_eth0\n',     # v4-mapped
                     '240e0388000a12340000000000000001 05 40 00 00 sipa_eth0x\n',    # another interface
                     '240e0388000a1234000000000000001 05 40 00 00 sipa_eth0\n'):     # 31 digits
            fake.write_text(line)
            for shell in self.each_shell():
                r = self.script(shell, NDP_LEARN, '--prefix', MU300_IF_INET6=fake)
                self.assertEqual((r.returncode, r.stdout), (0, ''), (shell, line))

    def init_start(self, shell, ipv6):
        self.stub('uci', f'[ "$*" = "-q get network.wan.ipv6" ] && {{ [ -n "{ipv6}" ] && echo "{ipv6}"; exit 0; }}\n'
                         'exit 1')
        harness = ('rec() { printf \'%s\\n\' "$*" >> "$STUBLOG/calls"; }\n'
                   'procd_open_instance() { rec procd_open_instance "$@"; }\n'
                   'procd_set_param() { rec procd_set_param "$@"; }\n'
                   'procd_close_instance() { rec procd_close_instance "$@"; }\n'
                   f'. "{NDP_INIT}"\nstart_service\n')
        (self.tmp / 'calls').write_text('')
        r = self.sh(shell, harness)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines()

    def test_ndp_init_only_in_relay_mode(self):
        """K37: init.d/mu300-ndp starts ndp-learn only when network.wan.ipv6 is relay."""
        for shell in self.each_shell():
            for ipv6 in ('', 'extend'):
                self.assertEqual(self.init_start(shell, ipv6), [], (shell, ipv6))
            calls = self.init_start(shell, 'relay')
            self.assertIn('procd_open_instance', calls)
            self.assertIn('procd_set_param command /bin/sh /opt/mu300/bin/ndp-learn', calls)
        text = NDP_INIT.read_text()
        self.assertTrue(text.startswith('#!/bin/sh /etc/rc.common\n'))
        self.assertRegex(text, r'(?m)^START=\d+$')
        self.assertIn('USE_PROCD=1', text)

    def uci_db(self):
        """A uci stand-in over the flat file uci.db (lines k='v'): the file."""
        db = self.tmp / 'uci.db'
        db.write_text("firewall.@zone[0].name='lan'\nfirewall.@zone[1].name='wan'\n")
        # uci stand-in: "set k=v", "get k", "show [cfg]", "commit", "batch" (stdin, lines of set/delete)
        self.stub('uci', r'''db=$STUBLOG/uci.db
[ "$1" = -q ] && shift
setkv() { k=${1%%=*}; v=${1#*=}; v=${v#\'}; v=${v%\'}; awk -v k="$k=" 'index($0, k) != 1' "$db" > "$db.n"; echo "$k='$v'" >> "$db.n"; mv "$db.n" "$db"; }
case $1 in
  set) setkv "$2" ;;
  get) sed -n "s/^$2='\(.*\)'$/\1/p" "$db" | grep . ;;
  show) grep "^$2" "$db" ;;
  delete) grep -v "^$2[=.]" "$db" > "$db.n"; mv "$db.n" "$db" ;;
  commit) echo "commit $2" >> "$STUBLOG/commits" ;;
  batch) while read -r op kv; do case $op in set) setkv "$kv" ;; delete) grep -v "^$kv[=.]" "$db" > "$db.n"; mv "$db.n" "$db" ;; esac; done ;;
esac
exit 0''')
        return db

    def test_first_boot_selects_relay(self):
        """K30, D9: 91-mu300-luci sets ipv6 relay, pdptype IPV4V6 and the fork's RA/DHCPv6 relay and masq6; run twice
        (uci is a stand-in over a flat file), the result is the same. Review I1: no NDP relay on lan or wan (odhcpd's
        relay pins any LAN-spoofed neighbour, even off-prefix, as a /128 to br-lan), and an earlier ndp or
        ndproxy_routing value is removed. Review R38: the ULA OpenWrt generated is kept, never a fixed one."""
        db = self.uci_db()
        want = {
            "network.wan.ipv6": 'relay', "network.wan.pdptype": 'IPV4V6', "network.lan.ip6assign": '60',
            "network.globals.ula_prefix": 'fd12:3456:789a::/48', "dhcp.wan": 'dhcp', "dhcp.wan.interface": 'wan',
            "dhcp.wan.master": '1', "dhcp.wan.ra": 'relay', "dhcp.wan.dhcpv6": 'relay',
            "dhcp.lan.ra": 'relay', "dhcp.lan.dhcpv6": 'relay',
            "firewall.@zone[1].masq6": '1', "luci.main.mediaurlbase": '/luci-static/aurora', "luci.main.lang": 'en'}
        before = ("firewall.@zone[0].name='lan'\nfirewall.@zone[1].name='wan'\n"
                  "network.globals.ula_prefix='fd12:3456:789a::/48'\n"     # OpenWrt's own random ULA
                  "dhcp.lan.ndp='relay'\ndhcp.wan.ndp='hybrid'\n"           # an earlier run's or a user's value
                  "dhcp.lan.ndproxy_routing='1'\ndhcp.wan.ndproxy_routing='1'\n")
        for shell in self.each_shell():
            db.write_text(before)
            states = []
            for _ in range(2):
                r = self.script(shell, LUCI_DEFAULTS)
                self.assertEqual(r.returncode, 0, r.stderr)
                states.append(sorted(db.read_text().splitlines()))   # the stand-in appends what it sets
            self.assertEqual(states[0], states[1], shell)
            got = dict(l.split('=', 1) for l in states[0])
            for k, v in want.items():
                self.assertEqual(got.get(k), f"'{v}'", (shell, k))
            self.assertNotIn('firewall.@zone[0].masq6', got)
            for k in got:
                self.assertNotRegex(k, r'^dhcp\.(lan|wan)\.(ndp|ndproxy_routing)$', (shell, 'NDP relay left on'))
            self.assertEqual(got.get('luci.mu300.defaults'), "'1'", shell)
            commits = set((self.tmp / 'commits').read_text().split())
            self.assertTrue({'network', 'dhcp', 'firewall', 'luci'} <= commits, commits)

    def test_an_update_keeps_the_users_choices_and_still_removes_ndp_relay(self):
        """Final review I3: 91-mu300-luci runs again on the first boot after every update (a fresh rootfs, the kept
        /etc/config). The defaults are a first install's: the PDP type, IPv6 mode, relay settings, masq6, theme and
        language the user set since stay; the NDP relay options are deleted on every run, whoever set them."""
        db = self.uci_db()
        user = {'network.wan.pdptype': 'IP', 'network.wan.ipv6': 'extend', 'network.lan.ip6assign': '64',
                'dhcp.lan.ra': 'server', 'dhcp.lan.dhcpv6': 'server', 'dhcp.wan.master': '0',
                'firewall.@zone[1].masq6': '0', 'luci.main.mediaurlbase': '/luci-static/bootstrap',
                'luci.main.lang': 'tr'}
        for shell in self.each_shell():
            db.write_text("firewall.@zone[0].name='lan'\nfirewall.@zone[1].name='wan'\n")
            r = self.script(shell, LUCI_DEFAULTS)                      # the first install
            self.assertEqual(r.returncode, 0, r.stderr)
            text = db.read_text()
            for k, v in user.items():                                   # the user's choices since
                text = ''.join(l + '\n' for l in text.splitlines() if not l.startswith(k + '=')) + f"{k}='{v}'\n"
            text += "dhcp.lan.ndp='relay'\ndhcp.wan.ndproxy_routing='1'\n"  # NDP relay turned back on
            db.write_text(text)
            r = self.script(shell, LUCI_DEFAULTS)                      # the first boot after an update
            self.assertEqual(r.returncode, 0, r.stderr)
            got = dict(l.split('=', 1) for l in db.read_text().splitlines())
            for k, v in user.items():
                self.assertEqual(got.get(k), f"'{v}'", (shell, k))
            for k in got:
                self.assertNotRegex(k, r'^dhcp\.(lan|wan)\.(ndp|ndproxy_routing)$', (shell, 'NDP relay left on'))



class MobileDataRelay(ShellTest):
    """K64: mobile-data's side of relay mode, sourced (MU300_LIB=1) with bash, /proc/sys/net/ipv6/conf in the scratch
    directory. On the netifd path with MU300_IPV6=relay and a dual-stack context, sipa_eth0 takes the carrier's RA
    (disable_ipv6 0, accept_ra 2) even without an interface identifier; everywhere else nothing changes."""

    def run_relay(self, **env):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        conf = self.tmp / 'conf' / 'sipa_eth0'
        conf.mkdir(parents=True, exist_ok=True)
        for k in ('disable_ipv6', 'accept_ra'):
            (conf / k).write_text('-\n')
        lib = self.tmp / 'mobile-data.lib'
        lib.write_text((BIN / 'mobile-data').read_text().replace('/proc/sys/net/ipv6/conf/', f'{self.tmp}/conf/'))
        e = dict(MU300_NETIFD='', MU300_IPV6='', MU300_PDP_TYPE='')
        e.update(env)
        r = self.sh(['bash'], f'MU300_LIB=1; . "{lib}"\nrc=0; v6_relay_ra || rc=$?; echo "rc=$rc"', **e)
        return r.stdout.strip(), (conf / 'disable_ipv6').read_text().strip(), (conf / 'accept_ra').read_text().strip()

    def test_relay_takes_the_ra(self):
        for pdp in ('IPV4V6', 'IPV6'):
            self.assertEqual(self.run_relay(MU300_NETIFD='1', MU300_IPV6='relay', MU300_PDP_TYPE=pdp),
                             ('rc=0', '0', '2'), pdp)

    def test_not_relay_changes_nothing(self):
        for env in (dict(MU300_NETIFD='1', MU300_IPV6='', MU300_PDP_TYPE='IPV4V6'),        # plain OpenWrt (extend)
                    dict(MU300_NETIFD='1', MU300_IPV6='extend', MU300_PDP_TYPE='IPV4V6'),
                    dict(MU300_NETIFD='1', MU300_IPV6='relay', MU300_PDP_TYPE='IP'),       # an IPv4 context
                    dict(MU300_NETIFD='1', MU300_IPV6='relay', MU300_PDP_TYPE=''),
                    dict(MU300_NETIFD='', MU300_IPV6='relay', MU300_PDP_TYPE='IPV4V6')):   # the systemd path
            self.assertEqual(self.run_relay(**env), ('rc=1', '-', '-'), env)

    def test_relay_replaces_v6_off_on_the_netifd_path_only(self):
        """Relay keeps IPv6 on where the bearer has no interface identifier; otherwise v6_off as before, and the
        systemd path still runs v6_up (test_device_scripts' test_systemd_path_keeps_v6_up)."""
        text = (BIN / 'mobile-data').read_text()
        start = text.index('\nup_locked() {')
        body = text[start:text.index('\ndown() {', start)]
        netifd = body.index('if [ "${MU300_NETIFD:-0}" = 1 ]; then')
        self.assertIn('v6_relay_ra || { [ -n "$iid" ] || v6_off; }', body[:netifd])




class MobileDataContext(ShellTest):
    """mobile-data's define_context (FINDINGS 65), sourced (MU300_LIB=1) with bash: the context of an APN, and the
    credentials of one that asks for them. mu300-at is a stub that records its arguments, its stdin and what it finds
    of the credentials in its own environment, and answers from a file; the nr2 command fifo is one in the scratch
    directory. SECRET never appears anywhere but on the stub's stdin: every run is checked for that."""

    SECRET = 's3 cr,et-Pw'
    AT_STUB = r"""
printf 'argv:' >> "$STUBLOG/at.log"; for a in "$@"; do printf ' [%s]' "$a" >> "$STUBLOG/at.log"; done; echo >> "$STUBLOG/at.log"
printf 'env: %s|%s\n' "${MU300_APN_USER-unset}" "${MU300_APN_PASSWORD-unset}" >> "$STUBLOG/at.log"
cmd=
for a in "$@"; do cmd=$a; done
if [ "$cmd" = - ]; then IFS= read -r cmd; printf 'stdin: %s\n' "$cmd" >> "$STUBLOG/at.log"; fi
case $cmd in
    AT+CGPCO=0,\"\",\"\",*) cat "$STUBLOG/cgpco-clear" 2>/dev/null || echo OK ;;
    AT+CGPCO*) cat "$STUBLOG/cgpco" 2>/dev/null || echo OK ;;
    *) echo OK ;;
esac
"""

    def setUp(self):
        super().setUp()
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.stub('mu300-at', self.AT_STUB)
        os.mkfifo(self.tmp / 'cmd')
        self.mark = self.tmp / 'etc' / 'apn-auth-set.sim0'      # the external card's; the internal one's: .sim1
        self.mark.parent.mkdir()
        self.conf = self.tmp / 'mobile-data.conf'
        self.netconf = self.tmp / 'network'
        self.lib = self.tmp / 'mobile-data.lib'
        self.lib.write_text((BIN / 'mobile-data').read_text()
                            .replace('DATA_DIR=/run/mu300-at2', f'DATA_DIR={self.tmp}')   # its command fifo
                            .replace('/opt/mu300/bin/mu300-at ', f'{self.stubs}/mu300-at '))

    def define(self, apn, slot=0, trace=False, drop=(), **env):
        """define_context APN with the settings in the environment (DROP: names left out of it altogether, so that
        the settings file says them). -> (CompletedProcess, the stub's log lines)."""
        (self.tmp / 'at.log').unlink(missing_ok=True)
        e = dict(MU300_APN_USER='', MU300_APN_PASSWORD='', MU300_APN_AUTH='', MU300_PDP_TYPE='', MU300_NETIFD='',
                 MU300_APN_AUTH_MARK=self.mark.with_suffix(''), MU300_CONF=self.conf, MU300_NETWORK_CONF=self.netconf, APN=apn)
        e.update(env)
        full = self.env(**e)
        for name in drop:
            full.pop(name, None)
        code = (f'MU300_LIB=1; . "{self.lib}"\nSIM_SLOT={slot}\nrc=0; '
                'define_context "${APN:-${MU300_APN:-}}" || rc=$?; echo "rc=$rc"')
        r = subprocess.run(['bash'] + (['-x'] if trace else []) + ['-c', code], capture_output=True, text=True,
                           env=full, timeout=60)
        log = (self.tmp / 'at.log').read_text().splitlines() if (self.tmp / 'at.log').exists() else []
        # the password: on the stub's stdin and nowhere else - not in an argument, not in the environment of
        # anything started, not in what the script prints, not in a trace
        for line in log:
            if not line.startswith('stdin: '):
                self.assertNotIn(self.SECRET, line)
            if line.startswith('env: '):
                self.assertIn(line, ('env: unset|unset',), 'a child of mobile-data inherited the credentials')
        self.assertNotIn(self.SECRET, r.stdout + r.stderr)
        return r, log

    def sent(self, log):
        """The commands the modem got, in order: (how, command) with how 'argv' or 'stdin'."""
        out = []
        for line in log:
            if line.startswith('stdin: '):
                out[-1] = ('stdin', line[len('stdin: '):])
            elif line.startswith('argv:'):
                out.append(('argv', re.findall(r'\[(.*?)\]', line)[-1]))
        return out

    def test_no_apn_defines_nothing(self):
        r, log = self.define('')
        self.assertEqual((r.stdout.strip(), log, r.stderr), ('rc=0', [], ''))

    def test_credentials_without_an_apn_are_no_dial_at_all(self):
        # never a dial that quietly leaves them out
        for env in (dict(MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET), dict(MU300_APN_PASSWORD=self.SECRET),
                    dict(MU300_APN_USER='u'), dict(MU300_APN_USER='u', MU300_APN_AUTH='chap')):
            r, log = self.define('', **env)
            self.assertEqual((r.stdout.strip(), log), ('rc=1', []), env)
            self.assertIn('without an APN', r.stderr)

    def test_an_apn_without_credentials_is_defined_as_before(self):
        r, log = self.define('internet')
        self.assertEqual((r.stdout.strip(), self.sent(log)), ('rc=0', [('argv', 'AT+CGDCONT=1,"IP","internet"')]), r.stderr)
        r, log = self.define('internet', MU300_PDP_TYPE='IPV4V6', MU300_APN_AUTH='none', MU300_APN_USER='u',
                             MU300_APN_PASSWORD=self.SECRET)
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IPV4V6","internet"')])
        self.assertFalse(self.mark.exists())
        # the internal card's dial, byte for byte what it has always sent; an ERROR to its empty form is not the
        # end of it, as it never was
        for answer in ('OK\n', 'ERROR\n'):
            (self.tmp / 'cgpco-clear').write_text(answer)
            r, log = self.define('cmnet', slot=1)
            self.assertEqual(r.stdout.strip(), 'rc=0')
            self.assertEqual(self.sent(log), [('argv', 'AT+CDEFMP=1,"0"'),
                                              ('argv', 'AT+CGDCONT=1,"IPV4V6","cmnet","",0,0,0,0,1,,,,,,,,,,,,,0'),
                                              ('argv', 'AT+CGPCO=0,"","",1,0'),
                                              ('argv', 'AT+CGEQREQ=1,2,0,0,0,0,2,0,"1e4","0e0",3,0,0')])

    def test_credentials_follow_the_context_and_never_touch_a_command_line(self):
        for auth, n in (('', 3), ('both', 3), ('pap', 1), ('chap', 2)):
            self.mark.unlink(missing_ok=True)
            r, log = self.define('private.apn', MU300_APN_USER='user@corp', MU300_APN_PASSWORD=self.SECRET, MU300_APN_AUTH=auth)
            self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr)
            self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IP","private.apn"'),
                                              ('stdin', f'AT+CGPCO=0,"user@corp","{self.SECRET}",1,{n}')], auth)
            self.assertNotIn('user@corp', '\n'.join(l for l in log if l.startswith('argv:')))
            self.assertEqual(self.mark.read_text(), '')             # remembers that the modem has some; holds nothing
            self.assertEqual(self.mark.stat().st_mode & 0o777, 0o600)
        # the internal card: in the place of its empty form, and with a mark of its own - its dial without
        # credentials must not make the external card's forgotten, whose context still holds them
        r, log = self.define('cmnet', slot=1, MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
        self.assertEqual(self.sent(log)[2], ('stdin', f'AT+CGPCO=0,"u","{self.SECRET}",1,3'))
        sim1 = self.mark.with_suffix('.sim1')
        self.assertTrue(sim1.exists() and self.mark.exists())
        r, log = self.define('cmnet', slot=1)
        self.assertEqual((sim1.exists(), self.mark.exists()), (False, True))
        # a user name alone, and a password alone, are credentials too
        r, log = self.define('a', MU300_APN_USER='u')
        self.assertEqual(self.sent(log)[1], ('stdin', 'AT+CGPCO=0,"u","",1,3'))
        r, log = self.define('a', MU300_APN_PASSWORD=self.SECRET)
        self.assertEqual(self.sent(log)[1], ('stdin', f'AT+CGPCO=0,"","{self.SECRET}",1,3'))

    def test_a_trace_does_not_hold_the_password(self):
        # bash -x mobile-data, as somebody looking for a fault would run it
        r, log = self.define('private.apn', trace=True, MU300_APN_USER='tracedUser', MU300_APN_PASSWORD=self.SECRET)
        self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr[-400:])
        self.assertEqual(self.sent(log)[1][0], 'stdin')
        self.assertIn('+ ', r.stderr)                               # it is a trace
        self.assertNotIn('tracedUser', r.stderr)
        # and from the settings file
        self.conf.write_text(f'MU300_APN=private.apn\nMU300_APN_USER=tracedUser\nMU300_APN_PASSWORD={self.SECRET}\n')
        self.conf.chmod(0o600)
        r, log = self.define('', trace=True, drop=('MU300_APN_USER', 'MU300_APN_PASSWORD', 'MU300_APN_AUTH'))
        self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr[-400:])
        self.assertNotIn('tracedUser', r.stderr)

    def test_a_modem_that_refuses_the_credentials_ends_the_dial_and_says_nothing_secret(self):
        for answer in ('ERROR\n', '+CME ERROR: 50\n', ''):
            (self.tmp / 'cgpco').write_text(answer)
            r, log = self.define('private.apn', MU300_APN_USER='user', MU300_APN_PASSWORD=self.SECRET)
            self.assertEqual(r.stdout.strip(), 'rc=1', answer)
            self.assertIn('refused the APN credentials', r.stderr)
            # what the modem did with them is not known: the mark stays, so the next dial without them clears first
            self.assertTrue(self.mark.exists())

    def test_nothing_that_could_end_the_at_argument_reaches_the_modem(self):
        bad = ['pa"ss', 'pa\\ss', 'pa\rAT+CFUN=0', 'pa\nss', 'päss', 'x' * 65, 'tab\there', '\x7f']
        for value in bad:
            for name in ('MU300_APN_USER', 'MU300_APN_PASSWORD'):
                env = {'MU300_APN_USER': 'u', 'MU300_APN_PASSWORD': 'p', name: value}
                r, log = self.define('internet', **env)
                self.assertEqual(r.stdout.strip(), 'rc=1', (name, value))
                self.assertEqual(log, [], (name, value))            # refused before the context is touched
                self.assertNotIn(value, r.stderr)
        r, log = self.define('internet', MU300_APN_USER='u', MU300_APN_AUTH='3;AT+CFUN=0')
        self.assertEqual((r.stdout.strip(), log), ('rc=1', []))
        self.assertIn('none, pap, chap or both', r.stderr)
        # the APN itself, as before
        for apn in ('a"b', 'a\\b', 'a\rb', 'a\nb'):
            r, log = self.define(apn)
            self.assertEqual((r.stdout.strip(), log), ('rc=1', []), apn)

    def test_credentials_taken_out_of_the_settings_are_taken_out_of_the_modem_before_the_next_dial(self):
        clear = ('argv', 'AT+CGPCO=0,"","",1,0')
        self.define('private.apn', MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
        self.assertTrue(self.mark.exists())
        r, log = self.define('private.apn')
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IP","private.apn"'), clear])
        self.assertFalse(self.mark.exists())
        r, log = self.define('private.apn')
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IP","private.apn"')])
        # the APN taken out with them: nothing is defined, but the credentials still go
        self.define('private.apn', MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
        r, log = self.define('')
        self.assertEqual((r.stdout.strip(), self.sent(log), self.mark.exists()), ('rc=0', [clear], False))
        # A modem that will not take them back: no dial. It would present the old credentials to whatever network
        # the new settings name. The mark stays, and the next try asks again.
        for apn in ('other.apn', ''):
            self.define('private.apn', MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
            for answer in ('ERROR\n', ''):
                (self.tmp / 'cgpco-clear').write_text(answer)
                r, log = self.define(apn)
                self.assertEqual((r.stdout.strip(), self.mark.exists()), ('rc=1', True), (apn, answer))
                self.assertIn('did not take back', r.stderr)
            (self.tmp / 'cgpco-clear').unlink()
            r, log = self.define(apn)
            self.assertEqual((r.stdout.strip(), self.mark.exists()), ('rc=0', False), apn)

    def test_no_credentials_reach_the_modem_unless_the_mark_can_be_written(self):
        # without the mark nothing would remember to take them back
        (self.tmp / 'conf-file-not-dir').write_text('')
        r, log = self.define('private.apn', MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET,
                             MU300_APN_AUTH_MARK=self.tmp / 'conf-file-not-dir' / 'mark')
        self.assertEqual(r.stdout.strip(), 'rc=1')
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IP","private.apn"')])
        self.assertIn('are not given to the modem', r.stderr)
        # a link where the mark goes is replaced by a file of ours, never written through
        target = self.tmp / 'elsewhere'
        target.write_text('keep\n')
        self.mark.symlink_to(target)
        r, log = self.define('private.apn', MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
        self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr)
        self.assertEqual((target.read_text(), self.mark.is_symlink(), self.mark.read_text()), ('keep\n', False, ''))
        self.assertEqual([p.name for p in self.mark.parent.iterdir()], ['apn-auth-set.sim0'])     # no temporary file left

    def test_the_settings_file_is_roots_alone_or_there_is_no_dial(self):
        # one gate for both systems: Ubuntu's mobile-data.conf, and /etc/config/network under netifd
        creds = dict(MU300_APN_USER='u', MU300_APN_PASSWORD=self.SECRET)
        for netifd, path in (('', self.conf), ('1', self.netconf)):
            path.write_text('x\n')
            path.chmod(0o644)
            r, log = self.define('private.apn', MU300_NETIFD=netifd, **creds)
            self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, netifd)
            # without credentials the file is nobody's business
            path.chmod(0o644)
            self.define('internet', MU300_NETIFD=netifd)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            # a link is not followed to change anything: what it leads to must be private already
            target = self.tmp / f'real{netifd}'
            target.write_text('x\n')
            path.unlink()
            path.symlink_to(target)
            for mode, rc in ((0o644, 'rc=1'), (0o640, 'rc=1'), (0o604, 'rc=1'), (0o600, 'rc=0'), (0o400, 'rc=0')):
                target.chmod(mode)
                r, log = self.define('private.apn', MU300_NETIFD=netifd, **creds)
                self.assertEqual((r.stdout.strip(), target.stat().st_mode & 0o777), (rc, mode), (netifd, oct(mode)))
                if rc == 'rc=1':
                    self.assertEqual(log, [])                       # refused before anything reaches the modem
                    self.assertIn('others can read it', r.stderr)
            # something that is no file at all
            path.unlink()
            path.mkdir()
            r, log = self.define('private.apn', MU300_NETIFD=netifd, **creds)
            self.assertEqual((r.stdout.strip(), log), ('rc=1', []))
            path.rmdir()
            # no file: the credentials came another way (a command run by hand)
            r, log = self.define('private.apn', MU300_NETIFD=netifd, **creds)
            self.assertEqual(r.stdout.strip(), 'rc=0')

    def test_every_dial_reads_the_settings_file_the_same_way(self):
        # mu300-power's "mobile-data resume" and a command run by hand have no environment from systemd: what the
        # environment does not say comes from the file, so they dial the APN, with its credentials, as the service does
        names = ('MU300_APN', 'MU300_PDP_TYPE', 'MU300_APN_USER', 'MU300_APN_PASSWORD', 'MU300_APN_AUTH')
        self.conf.write_text('# a comment\n#MU300_APN=commented\nMU300_APN=first\nMU300_APN="private.apn"\n'
                             f"MU300_PDP_TYPE=IPV4V6\nMU300_APN_USER='user name'\nMU300_APN_PASSWORD={self.SECRET}\n"
                             'MU300_APN_AUTH=chap\r\nOTHER=$(touch "$STUBLOG/executed")\n')
        self.conf.chmod(0o644)
        r, log = self.define('', drop=names)
        self.assertEqual(r.stdout.strip(), 'rc=0', r.stderr)
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IPV4V6","private.apn"'),
                                          ('stdin', f'AT+CGPCO=0,"user name","{self.SECRET}",1,2')])
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.tmp / 'executed').exists())          # read, never run
        # what the environment says wins (systemd read the same file; a hand-set variable)
        r, log = self.define('', drop=[n for n in names if n != 'MU300_APN_AUTH'], MU300_APN_AUTH='pap')
        self.assertEqual(self.sent(log)[1], ('stdin', f'AT+CGPCO=0,"user name","{self.SECRET}",1,1'))
        # under netifd the file is not looked at: mu300cell.sh said everything
        self.mark.unlink()
        r, log = self.define('internet', drop=names, MU300_NETIFD='1')
        self.assertEqual(self.sent(log), [('argv', 'AT+CGDCONT=1,"IP","internet"')])

    def test_the_interface_page_offers_them(self):
        js = (TOP / 'openwrt/overlay/www/luci-static/resources/protocol/mu300cell.js').read_text()
        for kind, name in (('Value', 'username'), ('Value', 'password'), ('ListValue', 'auth')):
            self.assertIn(f"form.{kind}, '{name}'", js)
        self.assertIn('o.password = true', js)
        for v in ("o.value('pap'", "o.value('chap'", "o.value('both'", "o.value('none'"):
            self.assertIn(v, js)
        example = (TOP / 'rootfs/overlay/etc/mu300/mobile-data.conf.example').read_text()
        for name in ('MU300_APN_USER', 'MU300_APN_PASSWORD', 'MU300_APN_AUTH', 'install -m 600'):
            self.assertIn(name, example)
        # the settings file never goes into a release, and OpenWrt's network settings are private from the first boot
        self.assertIn('(hotspot|vpn|toolkit|mobile-data)', (TOP / 'tools/make-release.sh').read_text())
        self.assertIn('chmod 600 /etc/config/network', (TOP / 'openwrt/overlay/etc/uci-defaults/90-mu300').read_text())
        # the protocol handler has no gate of its own to differ from mobile-data's
        self.assertNotIn('chmod', (TOP / 'openwrt/overlay/lib/netifd/proto/mu300cell.sh').read_text())


if __name__ == '__main__':
    unittest.main()


class RelayMonitorBearer(ShellTest):
    """FINDINGS 91: the relay monitor's bearer half - which card's channels and URC log it uses, what it believes of
    +CGCONTRDP, and how an address change is applied (after kanoqwq's clean-tf-7.2 3cbf37fc). The monitor is
    sourced with MU300CELL_V6_LIB=1 (functions only); mu300-sim, mu300-at, ip and logger are stubs."""

    def setUp(self):
        super().setUp()
        harness = self.tmp / 'netifd.sh'
        harness.write_text('rec() { printf \'%s\\n\' "$*" >> "$STUBLOG/calls"; }\n'
                           + ''.join(f'{f}() {{ rec {f} "$@"; }}\n' for f in (
                               'proto_init_update', 'proto_add_ipv4_address', 'proto_add_ipv4_route',
                               'proto_add_ipv6_address', 'proto_add_ipv6_route', 'proto_add_dns_server',
                               'proto_send_update')))
        text = MONITOR.read_text()
        text = text.replace('. /lib/functions.sh\n', '').replace('. /lib/netifd/netifd-proto.sh', f'. "{harness}"')
        text = text.replace('/opt/mu300/bin/mu300-sim', str(self.stubs / 'mu300-sim'))
        self.copy = self.tmp / 'mu300cell-v6.sh'
        self.copy.write_text(text)
        self.stub('mu300-sim', '[ "$1" = active ] && echo "${SLOT:-0}"')
        self.stub('logger', 'shift 2; echo "log $*" >> "$STUBLOG/calls"')
        # mu300-at: records the channel and the command, answers $RDP (printf %b)
        self.stub('mu300-at', 'echo "at ${MU300_AT_DIR##*/} $3" >> "$STUBLOG/calls"\n'
                  '[ "${RDP:-}" != none ] || exit 1\nprintf "%b\\n" "$RDP"')
        # ip: logs every change; "addr show" lists $HAVE; a change matching $IPFAIL fails
        self.stub('ip', 'case "$*" in *"addr show"*) [ -z "$HAVE" ] || echo "2: sipa_eth0    inet $HAVE scope global";'
                  ' exit 0 ;; esac\necho "ip $*" >> "$STUBLOG/calls"\n'
                  'case "$*" in ${IPFAIL:-nothing-fails}) exit 2 ;; esac')
        self.chan = self.tmp / 'at6'
        self.chan.mkdir()
        os.mkfifo(self.chan / 'cmd')

    def run_lib(self, shell, code, **env):
        (self.tmp / 'calls').write_text('')
        env.setdefault('MU300_CELL_AT_DIRS', str(self.chan))
        env.setdefault('MU300_RADIO_FREEZE', str(self.tmp / 'no-freeze'))
        r = self.sh(shell, f'set -- wan sipa_eth0 10.0.0.2 24 1.1.1.1 8.8.8.8 1\nMU300CELL_V6_LIB=1\n. "{self.copy}"\n'
                    + code, **env)
        return r, (self.tmp / 'calls').read_text().splitlines()

    def test_the_active_cards_channels_and_urc_log(self):
        freeze = self.tmp / 'freeze'
        cases = [('0', False, '/run/mu300-at6 /run/mu300-at7 /run/mu300-at', '/run/mu300-at/urc/stty_nr0.log'),
                 ('0', True, '/run/mu300-at6 /run/mu300-at7 /run/mu300-at', '/run/mu300-at/urc/stty_nr0.log'),
                 ('1', False, '/run/mu300-at', '/run/mu300-at/urc/stty_nr3.log'),
                 # the dual-SIM boot's SIM2: its own owner, never SIM1's nr1 (the defect)
                 ('1', True, '/run/mu300-at4', '/run/mu300-at4/urc/stty_nr3.log')]
        for shell in self.each_shell():
            for slot, native, dirs, urc in cases:
                freeze.unlink(missing_ok=True)
                if native:
                    freeze.touch()
                r, _ = self.run_lib(shell, 'echo "$AT_DIRS|$urclog"', SLOT=slot, MU300_CELL_AT_DIRS='',
                                    MU300_RADIO_FREEZE=str(freeze))
                self.assertEqual(r.stdout.strip(), f'{dirs}|{urc}', (slot, native, r.stderr))
            # a slot mu300-sim cannot name: no monitor at all
            r, _ = self.run_lib(shell, 'echo reached', SLOT='2')
            self.assertNotIn('reached', r.stdout)

    def test_mask2prefix_takes_only_contiguous_masks(self):
        good = {'255.255.255.0': '24', '255.255.255.255': '32', '255.255.254.0': '23', '0.0.0.0': '0',
                '255.128.0.0': '9'}
        bad = ['255.0.255.0', '255.255.255.1', '255.255.255', '255.255.255.0.0', '255.255.256.0', 'x.0.0.0', '']
        for shell in self.each_shell():
            for mask, want in good.items():
                r, _ = self.run_lib(shell, f'mask2prefix "{mask}"')
                self.assertEqual((r.returncode, r.stdout.strip()), (0, want), mask)
            for mask in bad:
                r, _ = self.run_lib(shell, f'mask2prefix "{mask}"')
                self.assertNotEqual(r.returncode, 0, mask)

    def test_valid_ipv4(self):
        good = ['10.1.2.3', '255.255.255.255', '1.0.0.0', '100.64.0.1']
        bad = ['0.0.0.0', '10.1.2', '10.1.2.3.4', '256.1.1.1', '01.2.3.4', '1..2.3', '-1.2.3.4', '1.2.3.4;id', '',
               '1.2.3.4 ', '$(id).1.1.1', '1.2.3.']
        for shell in self.each_shell():
            for a in good:
                self.assertEqual(self.run_lib(shell, f'valid_ipv4 "{a}"')[0].returncode, 0, a)
            for a in bad:
                self.assertNotEqual(self.run_lib(shell, f"valid_ipv4 '{a}'")[0].returncode, 0, a)

    RDP = '+CGCONTRDP: 1,5,"apn","{addr}","","{dns1}","{dns2}"\\r\\nOK'

    def test_read_bearer_believes_only_a_well_formed_answer(self):
        show = 'read_bearer && echo "got $BEARER_IP4/$BEARER_PREFIX4 [$BEARER_DNS1] [$BEARER_DNS2]"'
        cases = [(('10.9.8.7.255.255.255.0', '1.1.1.1', '8.8.8.8'), 'got 10.9.8.7/24 [1.1.1.1] [8.8.8.8]'),
                 (('10.9.8.7.255.255.255.0', '1.1.1.1', '0.0.0.0'), 'got 10.9.8.7/24 [1.1.1.1] []'),
                 (('10.9.8.7.255.255.255.0', '999.1.1.1', '8.8.8.8'), 'got 10.9.8.7/24 [] [8.8.8.8]'),
                 (('10.9.8.7.255.0.255.0', '1.1.1.1', '8.8.8.8'), ''),       # not a mask
                 (('10.9.8.777.255.255.255.0', '1.1.1.1', '8.8.8.8'), ''),   # not an address
                 (('0.0.0.0.0.0.0.0', '0.0.0.0', '0.0.0.0'), '')]            # no bearer
        for shell in self.each_shell():
            for (addr, d1, d2), want in cases:
                r, calls = self.run_lib(shell, show, RDP=self.RDP.format(addr=addr, dns1=d1, dns2=d2))
                self.assertEqual(r.stdout.strip(), want, (addr, d1, d2, r.stderr))
                self.assertIn(f'at at6 AT+CGCONTRDP=1', calls)
            r, _ = self.run_lib(shell, show, RDP='none')
            self.assertEqual(r.stdout.strip(), '')

    def test_a_new_address_is_added_before_the_old_one_goes(self):
        rdp = self.RDP.format(addr='10.9.8.7.255.255.255.0', dns1='1.1.1.1', dns2='8.8.8.8')
        for shell in self.each_shell():
            r, calls = self.run_lib(shell, 'refresh_bearer cgev; echo "now $ip4/$prefix4"', RDP=rdp, HAVE='10.0.0.2/24')
            self.assertIn('now 10.9.8.7/24', r.stdout, r.stderr)
            ip = [c for c in calls if c.startswith('ip ')]
            self.assertEqual(ip[:3], ['ip -4 addr add 10.9.8.7/24 dev sipa_eth0',
                                      'ip -4 route replace default dev sipa_eth0',
                                      'ip -4 addr del 10.0.0.2/24 dev sipa_eth0'], calls)
            self.assertIn('proto_add_ipv4_address 10.9.8.7 24', calls)
            self.assertIn('proto_send_update wan', calls)

    def test_a_failed_install_keeps_the_previous_state_for_the_next_try(self):
        rdp = self.RDP.format(addr='10.9.8.7.255.255.255.0', dns1='1.1.1.1', dns2='8.8.8.8')
        for shell in self.each_shell():
            code = ('rc=0; refresh_bearer cgev || rc=$?; echo "first rc=$rc $ip4/$prefix4 $dns1"\n'
                    'IPFAIL=nothing-fails; refresh_bearer renew; echo "second $ip4/$prefix4"')
            r, calls = self.run_lib(shell, code, RDP=rdp, HAVE='10.0.0.2/24', IPFAIL='*route*')
            self.assertIn('first rc=1 10.0.0.2/24 1.1.1.1', r.stdout, r.stderr)
            self.assertIn('second 10.9.8.7/24', r.stdout, r.stderr)
            # nothing was reported while it failed, and the old address was not taken away before the new one held
            first = calls[:calls.index('log renew: bearer updated to 10.9.8.7/24')] if \
                'log renew: bearer updated to 10.9.8.7/24' in calls else calls
            fail_part = first[:next(i for i, c in enumerate(first) if 'keeping the previous state' in c)]
            self.assertFalse([c for c in fail_part if c.startswith('proto_') or 'addr del' in c], calls)

    def test_an_unchanged_bearer_whose_address_the_kernel_lost_is_put_back(self):
        rdp = self.RDP.format(addr='10.0.0.2.255.255.255.0', dns1='1.1.1.1', dns2='8.8.8.8')
        for shell in self.each_shell():
            r, calls = self.run_lib(shell, 'refresh_bearer cgev', RDP=rdp, HAVE='10.0.0.2/24')
            self.assertIn('log cgev: bearer unchanged', calls)
            self.assertFalse([c for c in calls if c.startswith('ip ')], calls)
            r, calls = self.run_lib(shell, 'refresh_bearer cgev', RDP=rdp, HAVE='')
            self.assertIn('ip -4 addr add 10.0.0.2/24 dev sipa_eth0', calls)
            self.assertFalse([c for c in calls if 'addr del' in c], calls)   # never deletes what it just put back
            self.assertIn('proto_send_update wan', calls)

    def test_the_urc_hints_and_their_coalescing(self):
        text = MONITOR.read_text()
        for hint in ('*+CGEV:*PDN*', '*+CGEV:*ACT*', '*+CGEV:*DETACH*', '*+CGEV:*MODIFY*', '*+SPPCODATA:*'):
            self.assertIn(hint, text)
        # one queued read per burst, flagged by a directory (no file written through a planted symlink)
        self.assertIn('mkdir -m 700 "$pending" 2>/dev/null && printf \'pdp\\n\'', text)
        self.assertIn('rmdir "$pending"', text)
        # a hint in the cool-down waits instead of being dropped
        self.assertNotIn('-lt 2 ] && continue', text)
