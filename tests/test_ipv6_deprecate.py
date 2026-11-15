"""Exercise the deployed ndp-learn withdrawal state machine without sending an RA."""
import unittest

from helpers import TOP, ShellTest

NDP = TOP / 'openwrt/overlay/opt/mu300/bin/ndp-learn'


class PrefixWithdrawal(ShellTest):
    def setUp(self):
        super().setUp()
        source=NDP.read_text()
        self.lib = 'IFACE=sipa_eth0\nIF_INET6=${MU300_IF_INET6:-/proc/net/if_inet6}\n' + source[source.index('bearer_prefixes() {'):source.index('\ncase ${1:-} in')]
        # Keep standalone BusyBox ash from bypassing the isolated ip fixture.
        self.lib = 'ip() { "$STUBLOG/stubs/ip" "$@"; }\n' + self.lib
        self.stub('logger', ':')
        self.stub('uci', 'printf "%s\\n" "${RA_MODE:-relay}"')
        self.stub('ip', 'printf "%s\\n" "${DEFAULT_ROUTE:-default via fe80::1 dev sipa_eth0 expires 1234sec}"')
        self.stub('ra-deprecate', 'echo "$*" >> "$STUBLOG/ras"\nexit "${RA_FAIL:-0}"')

    def run_sequence(self, shell, prefixes, **env):
        (self.tmp / 'ras').unlink(missing_ok=True)
        code = self.lib + '\nlast_prefixes=\n'
        for p in prefixes:
            code += f"prefixes='{p}'; withdraw_old; last_prefixes=$prefixes\n"
        r = self.sh(shell, code, MU300_RA_DEPRECATE=self.stubs / 'ra-deprecate', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'ras').read_text().splitlines() if (self.tmp / 'ras').exists() else []

    def test_same_prefix_after_disappearance_is_not_deprecated(self):
        for shell in self.each_shell():
            self.assertEqual(self.run_sequence(shell, ['2408:1:2:3::/64', '', '', '2408:1:2:3::/64']), [])

    def test_new_prefix_withdraws_old_three_times_and_retains_gateway(self):
        a, b = '2408:1:2:3::/64', '2408:1:2:4::/64'
        for shell in self.each_shell():
            for gap in ([], ['', '']):
                got = self.run_sequence(shell, [a] + gap + [b] * 6)
                self.assertEqual(got, [f'br-lan 1234 {a}'] * 3)

    def test_returning_prefix_cancels_its_withdrawal(self):
        a, b = '2408:1:2:3::/64', '2408:1:2:4::/64'
        for shell in self.each_shell():
            got = self.run_sequence(shell, [a, b, a, a, a, a])
            self.assertEqual(got, [f'br-lan 1234 {a}'] + [f'br-lan 1234 {b}'] * 3)

    def test_coexisting_prefixes_only_withdraw_the_one_that_left(self):
        a, b = '2408:1:2:3::/64', '2408:1:2:4::/64'
        for shell in self.each_shell():
            self.assertEqual(self.run_sequence(shell, [a, f'{a}\n{b}', f'{b}\n{a}']), [])
            got = self.run_sequence(shell, [a, f'{a}\n{b}', b, b, b, b])
            self.assertEqual(got, [f'br-lan 1234 {a}'] * 3)

    def test_multiple_prefixes_return_after_wan_restart(self):
        a, b = '2408:1:2:3::/64', '2408:1:2:4::/64'
        for shell in self.each_shell():
            self.assertEqual(self.run_sequence(shell, [f'{a}\n{b}', '', '', f'{b}\n{a}']), [])
            got = self.run_sequence(shell, [f'{a}\n{b}', '', b, b, b, b])
            self.assertEqual(got, [f'br-lan 1234 {a}'] * 3)

    def test_no_ra_in_server_mode_and_failed_sends_are_bounded(self):
        a, b = '2408:1:2:3::/64', '2408:1:2:4::/64'
        for shell in self.each_shell():
            self.assertEqual(self.run_sequence(shell, [a, b, b, b], RA_MODE='server'), [])
            self.assertEqual(len(self.run_sequence(shell, [a] + [b]*100, RA_FAIL='1')), 3)

    def test_router_lifetime_fallback_and_clamp(self):
        for shell in self.each_shell():
            for expiry, expected in [('0', '1800'), ('1', '1'), ('2147483647', '9000'), ('forever', '1800')]:
                r = self.sh(shell, self.lib + '\nrouter_lifetime', DEFAULT_ROUTE=f'default expires {expiry}sec')
                self.assertEqual(r.stdout.strip(), expected)

    def test_our_routing_and_build_dependency_remain(self):
        text = NDP.read_text()
        for keep in ('LAN_PREFIX_METRIC=128', 'route del "$r/128" dev "$IFACE"', 'route add "$a/128" dev br-lan', 'accept_ra'):
            self.assertIn(keep, text)
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('ucode-mod-socket', build)
        self.assertIn('chmod 0755 $R/opt/mu300/bin/ra-deprecate', build)
        helper = (TOP / 'openwrt/overlay/opt/mu300/bin/ra-deprecate').read_text()
        for keep in ('IPV6_MULTICAST_HOPS, 255', 'u32(VALID) + u32(0)', 'rlt < 1', 'rlt > 9000'):
            self.assertIn(keep, helper)


if __name__ == '__main__':
    unittest.main()
