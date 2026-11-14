"""Real PDP parser and dial fetch, including IPv6-only and dual-record replies."""
import unittest
import shlex
from helpers import ShellTest, TOP, BIN

LIB = TOP/'rootfs/overlay/opt/mu300/lib/pdp-address.sh'
V6 = '0.0.0.0.0.0.0.0.128.0.0.0.0.0.0.0'
MASK = '.'.join(['0']*16)
DNS = '32.1.72.96.72.96.0.0.0.0.0.0.0.0.136.136'


class PdpAddresses(ShellTest):
    def parse(self, reply, cid=1):
        for shell in self.each_shell():
            result=self.sh(shell, '. '+shlex.quote(str(LIB))+'''
pdp_address_parse "$REPLY" "$CID" || exit $?
printf '%s|%s|%s|%s|%s\n' "$PDP_IP4" "$PDP_PREFIX4" "$PDP_IP6" "$PDP_DNS1" "$PDP_DNS2"
''', REPLY=reply, CID=str(cid))
            yield result

    def test_ipv6_iid_and_dotted_dns_are_not_ipv4(self):
        reply=f'+CGCONTRDP: 1,5,internet,{V6}.{MASK},,{DNS},::\r\nOK'
        for r in self.parse(reply):
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.strip(),'||0:0:0:0:8000:0:0:0|2001:4860:4860:0:0:0:0:8888|')

    def test_dual_record_order_and_cid_isolation(self):
        v4='+CGCONTRDP: 1,5,internet,10.2.3.4.255.255.255.0,,1.1.1.1,8.8.8.8'
        v6=f'+CGCONTRDP: 1,5,internet,{V6}.{MASK},,{DNS},'
        for reply in (v4+'\n'+v6,v6+'\n'+v4):
            for r in self.parse('+CGCONTRDP: 11,5,other,9.9.9.9.255.0.0.0,,4.4.4.4,\n'+reply):
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertTrue(r.stdout.startswith('10.2.3.4|24|0:0:0:0:8000:0:0:0|'))

    def test_ipv4_and_colon_ipv6(self):
        for address,expected in [('10.1.2.3.255.0.0.0','10.1.2.3|8||9.9.9.9|'),
                                 ('2001:db8::42','||2001:db8::42|9.9.9.9|')]:
            for r in self.parse(f'+CGCONTRDP: 1,5,internet,{address},,9.9.9.9,0.0.0.0'):
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),expected)

    def test_unknown_and_malformed_replies_do_not_publish(self):
        for value in ('','0.0.0.0.0.0.0.0','.'.join(['0']*32),'1.2.3.999.255.0.0.0',
                      '10.1.2.3.255.0.255.0','1:::2','1:2:3:4:5:6:7:8:9','::',
                      '10.1.2.3.255.0.0.0,,bad-dns'):
            for r in self.parse(f'+CGCONTRDP: 1,5,internet,{value},,,'):
                self.assertNotEqual(r.returncode,0,(value,r.stdout))

    def test_fetch_wait_accepts_full_reply_not_just_first_record(self):
        source=(BIN/'mobile-data').read_text()
        fn=source[source.index('fetch_addr() {'):source.index('\nis_registered() {')]
        fn=fn.replace('/run/mu300-mobile-data-down',str(self.tmp/'down'))
        r=self.sh(['bash'], '. '+shlex.quote(str(LIB))+'\n'+fn+'''
CID=1
at() { printf '%s\n' "$REPLY"; }
fetch_addr 2
printf '%s|%s\n' "$PDP_IP4" "$PDP_IP6"
''', REPLY=f'+CGCONTRDP: 11,5,wrong,10.0.0.2.255.0.0.0,,,\n+CGCONTRDP: 1,5,internet,{V6}.{MASK},,{DNS},')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout.strip(),'|0:0:0:0:8000:0:0:0')

    def test_real_dial_path_reports_granted_family_and_keeps_at_order(self):
        source=(BIN/'mobile-data').read_text()
        functions=source[source.index('fetch_addr() {'):source.index('\nis_registered() {')]
        functions+=source[source.index('v6_iid() {'):source.index('\nLAN6=')]
        functions+=source[source.index('configure_pdp_context() {'):source.index('\nresolve_apn_selection() {')]
        functions+=source[source.index('configure_initial_apn() {'):source.index('\ndown() {')]
        functions=functions.replace('/usr/libexec/unisoc-modem/roaming','/bin/true')
        functions=functions.replace('/run/mu300-mobile-data-down',str(self.tmp/'down'))
        functions=functions.replace('/run/mu300-mobile-data-manual-down',str(self.tmp/'manual-down'))
        functions=functions.replace('/proc/sys/',str(self.tmp/'sys')+'/')
        # Prevent the host's module directory from being used as a fixture.
        functions=functions.replace('_m=/lib/modules/$(uname -r)','_m="$STUBLOG/no-modules"')
        self.stub('uci','exit 1');self.stub('led-status',':')
        for kind,address,expected in [('IP','10.1.2.3.255.0.0.0','IP'),
                                      ('IPV4V6',V6+'.'+MASK,'IPV6'),('IPV6',V6+'.'+MASK,'IPV6')]:
            code='. '+shlex.quote(str(LIB))+'\n'+functions+'''
set -euo pipefail
CID=1; IFACE=sipa_eth8; DEVICE=f50; MU300_SIM_SLOT=1; MU300_NETIFD=1
modem_stack() { :; }; tty_setup() { :; }; radio_on() { :; }; mark() { :; }
radio_say() { :; }
wait_registered() { :; }; resolve_apn_selection() { APN_RESOLVED=0; }
at() {
    echo "$1" >> "$STUBLOG/at-order"
    case "$1" in
        *CGCONTRDP*) printf '%s\n' "$RDP" ;;
        *CGDATA*) echo CONNECT ;;
        *CGPADDR*) echo '+CGPADDR: 1,0.0.0.0.0.0.0.0.128.0.0.0.0.0.0.0' ;;
        *CEREG*) echo '+CEREG: 2,1,"01","01",7' ;;
        *) echo OK ;;
    esac
}
up_locked internet
'''
            dns=DNS if expected=='IPV6' else '1.1.1.1'
            (self.tmp/'at-order').unlink(missing_ok=True)
            r=self.sh(['bash'],code,MU300_PDP_TYPE=kind,RDP=f'+CGCONTRDP: 1,5,internet,{address},,{dns},')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('PDP_TYPE='+expected,r.stdout)
            self.assertIn('IFACE=sipa_eth8',r.stdout)
            self.assertIn('IP=\n' if expected=='IPV6' else 'IP=10.1.2.3\n',r.stdout)
            order=(self.tmp/'at-order').read_text()
            self.assertLess(order.index('AT+CGDCONT'),order.index('AT+CGACT=1'))
            self.assertLess(order.index('AT+CGACT=1'),order.index('AT+CGCONTRDP'))
            self.assertLess(order.index('AT+CGCONTRDP'),order.index('AT+CGDATA'))


if __name__=='__main__': unittest.main()
