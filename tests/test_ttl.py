"""mu300-ttl and fw4's software flowtable: while a TTL is set, OpenWrt's flow offloading is off (an offloaded flow
skips postrouting, where the TTL rule is); without fw4 (Ubuntu) nothing about the firewall is touched."""
import unittest

from helpers import BIN, ShellTest

KEY = 'firewall.@defaults[0].flow_offloading'


class TtlBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')
        # the 5.4 vendor kernel: no act_pedit, so the nft backend (TtlTc below has the tc one)
        self.stub('modprobe', 'exit 1')
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = -f ] && cat >> "$STUBLOG/nft.in"; '
                         '[ "$1" = list ] && exit 1; exit 0')

    def openwrt(self, offloading='1'):
        """Fake uci (flow_offloading in a file) and fw4 that log their calls."""
        self.store = self.tmp / 'offloading'
        self.store.write_text(offloading + '\n')
        self.stub('uci', f'''echo "$*" >> "$STUBLOG/uci.log"
[ "$1" = -q ] && shift
case $1 in
    get) [ "$2" = '{KEY}' ] || exit 1; cat "$STUBLOG/offloading" ;;
    set) case $2 in '{KEY}='*) echo "${{2#*=}}" > "$STUBLOG/offloading" ;; *) exit 1 ;; esac ;;
    commit) [ "$2" = firewall ] || exit 1 ;;
    *) exit 1 ;;
esac''')
        self.stub('fw4', 'echo "$*" >> "$STUBLOG/fw4.log"')

    def ttl(self, shell, *args):
        return self.script(shell, BIN / 'mu300-ttl', *args, MU300_TTL_CONF=self.conf,
                           MU300_TTL_NET=self.tmp / 'net')

    def reset(self, offloading='1'):
        for f in ('nft.args', 'nft.in', 'uci.log', 'fw4.log', 'tc.log', 'modprobe.log'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)
        (self.conf.parent / 'ttl.offload-off').unlink(missing_ok=True)
        if hasattr(self, 'store'):
            self.store.write_text(offloading + '\n')

    def log(self, name):
        p = self.tmp / name
        return p.read_text() if p.exists() else ''

    def offloading(self):
        return self.store.read_text().strip()


class TtlOffload(TtlBase):
    def test_set_turns_offloading_off_and_off_turns_it_back_on(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertEqual(self.offloading(), '0')
            self.assertIn('commit firewall', self.log('uci.log'))
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            # a new value with offloading already off: no second reload
            (self.tmp / 'fw4.log').unlink()
            self.assertEqual(self.ttl(shell, 'set', '128').returncode, 0)
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            self.assertEqual(r.stderr, '')

    def test_boot_apply_keeps_offloading_off_for_a_saved_ttl(self):
        # after an update uci-defaults (90-mu300) sets flow_offloading=1; mu300-toolkit apply (S96) runs this
        self.openwrt()
        for shell in self.each_shell():
            self.reset('1')
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=64\n')
            r = self.ttl(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            # the next boot: already off, the firewall is not reloaded again
            self.reset('0')
            self.conf.write_text('TTL=64\n')
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')

    def test_no_ttl_leaves_offloading_as_it_is(self):
        # the default (no TTL): offloading stays on; a choice made in LuCI (off) is not undone at boot
        self.openwrt()
        for shell in self.each_shell():
            for value in ('1', '0'):
                self.reset(value)
                r = self.ttl(shell, 'apply')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.offloading(), value)
                self.assertNotIn('set', self.log('uci.log'))
                self.assertEqual(self.log('fw4.log'), '')
                self.assertEqual(self.ttl(shell).returncode, 0)  # status touches nothing either
                self.assertEqual(self.log('uci.log').count('set'), 0)

    def test_invalid_value_touches_no_firewall(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '0').returncode, 2)
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('uci.log'), '')
            self.assertEqual(self.log('fw4.log'), '')

    def test_failed_rule_leaves_offloading_on(self):
        self.openwrt()
        self.stub('nft', '[ "$1" = -f ] && { cat > /dev/null; exit 1; }; exit 0')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertIn('could not be set', r.stderr)
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('fw4.log'), '')

    def test_failed_uci_is_reported_and_the_rule_stays(self):
        self.openwrt()
        self.stub('uci', 'exit 1')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertIn('flow offloading could not be turned off', r.stderr)
            self.assertEqual(self.log('fw4.log'), '')

    def test_ubuntu_without_fw4_is_unchanged(self):
        # no fw4 on PATH: no uci call, whatever uci there is
        self.stub('uci', 'echo "$*" >> "$STUBLOG/uci.log"')
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '64').returncode, 0)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, '')
            self.assertEqual(self.log('uci.log'), '')


class TtlTc(TtlBase):
    """Mainline kernels (act_pedit, act_csum, cls_matchall): the TTL is rewritten by tc on clsact egress of every
    sipa_eth*, which an offloaded flow still passes (the flowtable transmits with dev_queue_xmit), so flow offloading
    is left alone. The 5.4 vendor kernel has no act_pedit: modprobe fails and the nft backend above is used."""

    V4 = 'egress pref 10 protocol ip matchall action pedit ex munge ip ttl set {} pipe action csum ip'
    V6 = 'egress pref 11 protocol ipv6 matchall action pedit ex munge ip6 hoplimit set {}'

    def setUp(self):
        super().setUp()
        self.stub('modprobe', 'echo "$*" >> "$STUBLOG/modprobe.log"')
        # tc logs its arguments; "filter show" prints $STUBLOG/tc.show; an add fails when $STUBLOG/tc.fail exists
        self.stub('tc', 'echo "$*" >> "$STUBLOG/tc.log"\n'
                        'case "$*" in *"filter show"*) cat "$STUBLOG/tc.show" 2>/dev/null ;;\n'
                        '    *"filter add"*) [ -e "$STUBLOG/tc.fail" ] && exit 2 ;; esac\nexit 0')
        net = self.tmp / 'net'
        for i in ('sipa_eth0', 'sipa_eth1', 'lo', 'br-lan'):
            (net / i).mkdir(parents=True, exist_ok=True)

    def tc_log(self):
        return self.log('tc.log').splitlines()

    def test_set_uses_tc_on_every_sipa_and_leaves_offloading_alone(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.log('modprobe.log').split(), ['act_pedit', 'act_csum', 'cls_matchall'])
            tc = self.tc_log()
            for i in ('sipa_eth0', 'sipa_eth1'):
                self.assertIn(f'qdisc add dev {i} clsact', tc)
                self.assertIn(f'filter add dev {i} ' + self.V4.format(64), tc)
                self.assertIn(f'filter add dev {i} ' + self.V6.format(64), tc)
            self.assertFalse([c for c in tc if ' lo ' in c + ' ' or 'br-lan' in c], tc)
            self.assertEqual(self.log('nft.in'), '')                   # no nft rule
            self.assertIn('delete table inet mu300_ttl', self.log('nft.args'))  # an old one goes
            self.assertEqual(self.offloading(), '1')
            self.assertNotIn('set', self.log('uci.log'))
            self.assertEqual(self.log('fw4.log'), '')

    def test_apply_is_idempotent(self):
        # mobile-data calls apply on every bring-up: matchall has no replace (EEXIST, seen on the U30 Air), so each
        # filter is deleted before it is added again - never a second one, never a failed add
        for shell in self.each_shell():
            self.reset()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=65\n')
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            tc = self.tc_log()
            self.assertFalse([c for c in tc if 'replace' in c], tc)
            add = 'filter add dev sipa_eth0 ' + self.V4.format(65)
            self.assertEqual(tc.count(add), 2)
            # the second add comes after a delete of the first
            last_add = max(i for i, x in enumerate(tc) if x == add)
            self.assertIn('filter del dev sipa_eth0 egress pref 10', tc[tc.index(add) + 1:last_add])

    def test_tc_failure_falls_back_to_nft(self):
        self.openwrt()
        (self.tmp / 'tc.fail').write_text('')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertIn('filter del dev sipa_eth0 egress pref 10', self.tc_log())   # half-set filters removed
            self.assertEqual(self.offloading(), '0')

    def test_off_removes_both_and_leaves_offloading_alone(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset('0')                     # turned off in LuCI by the user: not ours to turn back on
            self.assertEqual(self.ttl(shell, 'set', '128').returncode, 0)
            (self.tmp / 'tc.log').unlink()
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            tc = self.tc_log()
            for i in ('sipa_eth0', 'sipa_eth1'):
                for pref in ('10', '11'):
                    self.assertIn(f'filter del dev {i} egress pref {pref}', tc)
            self.assertFalse([c for c in tc if 'filter add' in c], tc)
            self.assertIn('delete table inet mu300_ttl', self.log('nft.args'))
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')

    def test_offloading_turned_off_by_nft_comes_back_with_tc(self):
        # set on the 5.4 kernel (offloading off), then a mainline boot: tc does not need it off
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            self.stub('modprobe', 'exit 1')
            self.assertEqual(self.ttl(shell, 'set', '64').returncode, 0)
            self.assertEqual(self.offloading(), '0')
            self.stub('modprobe', 'exit 0')
            r = self.ttl(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.offloading(), '1')
            self.assertFalse((self.conf.parent / 'ttl.offload-off').exists())

    def test_off_after_an_nft_rule_without_the_marker(self):
        # set by an older mu300-ttl (no ttl.offload-off): its nft table still says offloading was turned off
        self.openwrt('0')
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = list ] && exit 0; exit 0')
        for shell in self.each_shell():
            self.reset('0')
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.offloading(), '1')
            self.assertFalse((self.conf.parent / 'ttl.offload-off').exists())

    def test_no_sipa_yet_sets_nothing_and_fails_nothing(self):
        # at boot before the modem: the rule comes with mobile data (mobile-data up calls apply)
        for shell in self.each_shell():
            self.reset()
            for i in ('sipa_eth0', 'sipa_eth1'):
                (self.tmp / 'net' / i).rmdir()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.log('nft.in'), '')
            for i in ('sipa_eth0', 'sipa_eth1'):
                (self.tmp / 'net' / i).mkdir()

    SHOW = """filter protocol ip pref 10 matchall chain 0 
filter protocol ip pref 10 matchall chain 0 handle 0x1 
  not_in_hw (rule hit 42)
\taction order 1:  pedit action pipe keys 1
\tAction statistics:
\tSent 4200 bytes 42 pkt (dropped 0, overlimits 0 requeues 0) 
\taction order 2: csum (iph) action pass
\tSent 4200 bytes 42 pkt (dropped 0, overlimits 0 requeues 0) 
filter protocol ipv6 pref 11 matchall chain 0 
filter protocol ipv6 pref 11 matchall chain 0 handle 0x1 
  not_in_hw (rule hit 7)
\tSent 700 bytes 7 pkt (dropped 0, overlimits 0 requeues 0) 
"""

    def test_status_names_the_backend(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=64\n')
            (self.tmp / 'tc.show').write_text(self.SHOW)
            r = self.ttl(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('tc on sipa_eth0', r.stdout)
            self.assertIn('flow offload untouched', r.stdout)
            self.assertIn('IPv4 TTL 64 (84 packets so far)', r.stdout)      # both interfaces summed
            self.assertIn('IPv6 hop limit 64 (14 packets so far)', r.stdout)
            r = self.ttl(shell, 'state')
            self.assertEqual(r.stdout, 'TTL=64\nBACKEND=tc\nIFACES=sipa_eth0 sipa_eth1\nOFFLOAD=1\n')
            (self.tmp / 'tc.show').unlink()
            # the nft table in force
            self.store.write_text('0\n')
            self.stub('nft', 'echo \'table inet mu300_ttl { chain postrouting {\n'
                             'oifname "sipa_eth*" ip ttl set 64 counter packets 5 bytes 9\n} }\'')
            r = self.ttl(shell)
            self.assertIn('nft, flow offload off', r.stdout)
            self.assertIn('IPv4 TTL 64 (5 packets so far)', r.stdout)
            r = self.ttl(shell, 'state')
            self.assertEqual(r.stdout, 'TTL=64\nBACKEND=nft\nIFACES=\nOFFLOAD=0\n')
            self.stub('nft', 'exit 1')
            self.conf.unlink()
            r = self.ttl(shell, 'state')
            self.assertEqual(r.stdout, 'TTL=\nBACKEND=none\nIFACES=\nOFFLOAD=0\n')

    def test_ubuntu_with_tc_touches_no_firewall(self):
        self.stub('uci', 'echo "$*" >> "$STUBLOG/uci.log"')
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '64').returncode, 0)
            self.assertIn('filter add dev sipa_eth0 ' + self.V4.format(64), self.tc_log())
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, '')
            self.assertEqual(self.log('uci.log'), '')
            r = self.ttl(shell, 'state')
            self.assertIn('OFFLOAD=\n', r.stdout)


if __name__ == '__main__':
    unittest.main()
