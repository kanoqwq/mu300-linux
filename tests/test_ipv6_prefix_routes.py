"""Run the real relay route loop against isolated kernel-table fixtures."""
from helpers import TOP, ShellTest

NDP = TOP / 'openwrt/overlay/opt/mu300/bin/ndp-learn'
A = '2001:db8:0:0::/64'
B = '2408:1:2:4::/64'
ROWS = {
    'a': '20010db8000000000000000000000001 05 40 00 00 sipa_eth0\n',
    'b': '24080001000200040000000000000001 05 40 00 00 sipa_eth0\n',
}


class PrefixRoutes(ShellTest):
    def setUp(self):
        super().setUp()
        source=NDP.read_text()
        self.lib = 'IFACE=sipa_eth0\nIF_INET6=${MU300_IF_INET6:-/proc/net/if_inet6}\n' + source[source.index('bearer_prefixes() {'):source.index('\ncase ${1:-} in')]
        # Standalone BusyBox ash can prefer its ip applet over PATH stubs.
        bind_ip = 'ip() { "$STUBLOG/stubs/ip" "$@"; }\n'
        self.lib = bind_ip + self.lib
        self.ndp = self.tmp/'ndp-learn'
        self.ndp.write_text(bind_ip + source)
        self.inet6 = self.tmp / 'if_inet6'
        self.stub('logger', ':')
        self.stub('uci', 'case "$*" in *main.data_sim) echo 0;; *main.sim_slots) echo 1;; *main.sim0_device) :;; *) echo relay;; esac')
        self.stub('ra-deprecate', 'echo "ra $*" >> "$STUBLOG/calls"')
        self.stub('ip', '''echo "ip $*" >> "$STUBLOG/calls"
case "$*" in
    "-6 route show default dev sipa_eth0") echo 'default via fe80::1 expires 1234sec' ;;
    "-6 route show dev sipa_eth0") cat "$STUBLOG/wan" ;;
    "-6 route show dev br-lan") cat "$STUBLOG/lan" ;;
    "-6 neigh show dev br-lan") cat "$STUBLOG/neigh" ;;
esac
exit 0''')
        self.extra_ndp=dict(MU300_NDP_INTERFACE_LIB=TOP/'rootfs/overlay/opt/mu300/lib/ndp-interface.sh',MU300_NDP_SIM_ENV=TOP/'rootfs/overlay/opt/mu300/bin/mu300-sim-env')
        for name in ('wan', 'lan', 'neigh', 'calls'):
            (self.tmp / name).write_text('')

    def sequence(self, shell, *states):
        (self.tmp / 'calls').write_text('')
        code = self.lib + '\n'
        for i, state in enumerate(states):
            src = self.tmp / f'state-{i}'
            src.write_text(''.join(ROWS[c] for c in state))
            code += f'cp "{src}" "$IF_INET6"; echo --round >> "$STUBLOG/calls"; learn_once\n'
        result = self.sh(shell, code, MU300_IF_INET6=self.inet6,
                         MU300_RA_DEPRECATE=self.stubs / 'ra-deprecate')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return [s.splitlines() for s in (self.tmp / 'calls').read_text().split('--round\n')[1:]]

    def test_prefix_list_is_complete_sorted_unique_and_bearer_only(self):
        self.inet6.write_text(ROWS['b'] + ROWS['a'] * 2 +
            'fe800000000000000000000000000001 05 40 20 00 sipa_eth0\n' +
            ROWS['b'].replace('sipa_eth0', 'br-lan') +
            '2oops 05 40 00 00 sipa_eth0\n')
        for shell in self.each_shell():
            got = self.script(shell, NDP, '--prefix', MU300_IF_INET6=self.inet6,**self.extra_ndp)
            self.assertEqual((got.returncode, got.stdout, got.stderr), (0, f'{A}\n{B}\n', ''))

    def test_coexisting_routes_are_both_repaired_each_round(self):
        for shell in self.each_shell():
            rounds = self.sequence(shell, 'ab', 'ba', 'ab')
            for calls in rounds:
                self.assertEqual([c for c in calls if ' route replace ' in c], [
                    f'ip -6 route replace {A} dev br-lan metric 128',
                    f'ip -6 route replace {B} dev br-lan metric 128'])
                self.assertFalse([c for c in calls if ' route del ' in c or c.startswith('ra ')])

    def test_remove_only_departed_prefix_and_do_not_repin_stale_neighbour(self):
        (self.tmp / 'lan').write_text('2001:db8::99 proto static\n2408:1:2:4::99 proto static\n')
        (self.tmp / 'neigh').write_text('2001:db8::99 lladdr aa:bb:cc:dd:ee:ff STALE\n'
                                       '2408:1:2:4::99 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n')
        for shell in self.each_shell():
            rounds = self.sequence(shell, 'ab', 'b', 'b', '')
            deletes = [c for c in rounds[1] if ' route del ' in c]
            self.assertEqual(deletes, [f'ip -6 route del {A} dev br-lan metric 128',
                                       'ip -6 route del 2001:db8::99/128 dev br-lan'])
            self.assertNotIn('ip -6 route add 2001:db8::99/128 dev br-lan', rounds[1])
            self.assertIn('ip -6 route add 2408:1:2:4::99/128 dev br-lan', rounds[1])
            self.assertFalse([c for c in rounds[2] if ' route del ' in c])
            self.assertIn(f'ip -6 route del {B} dev br-lan metric 128', rounds[3])
            self.assertFalse([c for c in rounds[3] if ' route add ' in c or ' route replace ' in c])

    def test_wrong_wan_host_routes_in_both_prefixes_are_removed(self):
        (self.tmp / 'wan').write_text('2001:db8::22 proto static\n'
            '2408:1:2:4::22/128 proto static\n2606:4700::1111 proto static\n'
            '2408:1:2:4::/64 proto kernel\nfe80::1 proto static\n')
        for shell in self.each_shell():
            calls = self.sequence(shell, 'ab')[0]
            self.assertEqual([c for c in calls if ' route del ' in c], [
                'ip -6 route del 2001:db8::22/128 dev sipa_eth0',
                'ip -6 route del 2408:1:2:4::22/128 dev sipa_eth0'])

    def test_full_loop_withdraws_only_after_new_prefix_arrives(self):
        for shell in self.each_shell():
            same = self.sequence(shell, 'ab', '', 'ba', 'ba')
            self.assertFalse([c for rnd in same for c in rnd if c.startswith('ra ')])
            changed = self.sequence(shell, 'a', 'ab', 'b', 'b', 'b', 'b')
            self.assertEqual([c for rnd in changed for c in rnd if c.startswith('ra ')],
                             [f'ra br-lan 1234 {A}'] * 3)

    def test_host_filter_normalizes_ipv6_and_rejects_foreign_routes(self):
        valid = ['2001:db8::1', '2001:0DB8:0:0:0:0:0:2', '2408:1:2:4::3/128']
        invalid = ['2001:db8:0:1::1', '2001:db8::/64', '2001:db8::1::2',
                   '2001:db8:::1', '2001:db8:0:0:0:0:0:12345', '::1', 'fd00::1',
                   'fe80::1', 'default', '2001:db8:1', '2001:db8::zz']
        data = '\n'.join(valid + invalid) + '\n'
        for shell in self.each_shell():
            got = self.sh(shell, self.lib + f"\nprefix_hosts '{A}\n{B}'", stdin=data)
            self.assertEqual((got.returncode, got.stderr), (0, ''))
            self.assertEqual(got.stdout.splitlines(), [s.removesuffix('/128') for s in valid])
            empty = self.sh(shell, self.lib + "\nprefix_hosts ''", stdin=data)
            self.assertEqual((empty.returncode, empty.stdout, empty.stderr), (0, '', ''))

    def test_once_exits_without_sleep_or_router_solicitation(self):
        self.inet6.write_text(ROWS['a'] + ROWS['b'])
        self.stub('sleep', 'echo unexpected-sleep >&2; exit 1')
        for shell in self.each_shell():
            got = self.script(shell, self.ndp, '--once', MU300_IF_INET6=self.inet6,
                              MU300_RA_DEPRECATE=self.stubs / 'ra-deprecate',**self.extra_ndp)
            self.assertEqual((got.returncode, got.stderr), (0, ''))
