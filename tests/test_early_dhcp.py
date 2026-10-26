"""The USB host's early DHCP lease, from boot/init into the system (K5, K9, K11, K13, K25-K27): OpenWrt's preinit
server on the system's own subnet, the LAN hook that ends it, the first-boot defaults, and Ubuntu's lan-start. The
scripts run against a scratch directory and stub commands, under each shell (busybox ash on OpenWrt)."""
import json
import shutil
import subprocess
import unittest

from helpers import BIN, TOP, ShellTest


OPENWRT = TOP / 'openwrt' / 'overlay'
LUCI_DEFAULTS = TOP / 'openwrt' / 'luci-overlay' / 'etc' / 'uci-defaults' / '91-mu300-luci'

# A uci stand-in that keeps the configuration (JSON in $STUBLOG/uci.json) and logs every call to $STUBLOG/uci.log
# ("uci ARGS", a batch's lines as "batch: LINE", commits to $STUBLOG/commits). It knows what the defaults scripts use:
# get, set, delete, add_list, add, show, commit and batch, with named sections, @type[N] and the ids `add` gives
# (cfg0e1, cfg0e2, ...), and shows anonymous sections as @type[N] as uci does. UCI_NOLOG=1: a test's own edit.
UCI_STANDIN = r"""
import json, os, re, shlex, sys
log = os.environ['STUBLOG']
dbf = os.path.join(log, 'uci.json')
db = json.load(open(dbf)) if os.path.exists(dbf) else {}
def note(line):
    if not os.environ.get('UCI_NOLOG'):
        open(os.path.join(log, 'uci.log'), 'a').write(line + '\n')
args = sys.argv[1:]
note('uci ' + ' '.join(args))
if args[:1] == ['-q']:
    args = args[1:]
def find(cfg, sec):
    secs = db.get(cfg, [])
    m = re.fullmatch(r'@(\w+)\[(-?\d+)\]', sec)
    if m:
        of = [s for s in secs if s['type'] == m.group(1)]
        try:
            return of[int(m.group(2))]
        except IndexError:
            return None
    return next((s for s in secs if s['name'] == sec), None)
def split(key):
    parts = key.split('.', 2)
    return parts + [None] * (3 - len(parts))
def label(cfg, s):
    if not s['anon']:
        return s['name']
    of = [x for x in db[cfg] if x['type'] == s['type']]
    return '@%s[%d]' % (s['type'], of.index(s))
def q(v):
    return ' '.join("'%s'" % x for x in v) if isinstance(v, list) else "'%s'" % v
def run(op, rest):
    if op == 'get':
        cfg, sec, opt = split(rest[0])
        s = find(cfg, sec)
        if s is None or (opt and opt not in s['opts']):
            return 1
        v = s['opts'][opt] if opt else s['type']
        print(' '.join(v) if isinstance(v, list) else v)
    elif op in ('set', 'add_list'):
        key, _, val = rest[0].partition('=')
        cfg, sec, opt = split(key)
        s = find(cfg, sec)
        if opt is None:
            if s is None:
                db.setdefault(cfg, []).append({'name': sec, 'type': val, 'anon': False, 'opts': {}})
            else:
                s['type'] = val
            return 0
        if s is None:
            return 1
        if op == 'set':
            s['opts'][opt] = val
        else:
            cur = s['opts'].get(opt, [])
            s['opts'][opt] = (cur if isinstance(cur, list) else [cur]) + [val]
    elif op == 'delete':
        cfg, sec, opt = split(rest[0])
        s = find(cfg, sec)
        if s is None or (opt and opt not in s['opts']):
            return 1
        if opt:
            del s['opts'][opt]
        else:
            db[cfg].remove(s)
    elif op == 'add':
        n = db.setdefault('_ids', 0) + 1
        db['_ids'] = n
        sid = 'cfg0e%x' % n
        db.setdefault(rest[0], []).append({'name': sid, 'type': rest[1], 'anon': True, 'opts': {}})
        print(sid)
    elif op == 'show':
        cfgs = [rest[0]] if rest else [c for c in db if c != '_ids']
        for cfg in cfgs:
            for s in db.get(cfg, []):
                print('%s.%s=%s' % (cfg, label(cfg, s), s['type']))
                for k, v in s['opts'].items():
                    print('%s.%s.%s=%s' % (cfg, label(cfg, s), k, q(v)))
    elif op == 'commit':
        open(os.path.join(log, 'commits'), 'a').write((rest[0] if rest else 'all') + '\n')
    return 0
rc = 0
if args[:1] == ['batch']:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        note('batch: ' + line)
        w = shlex.split(line)
        rc = run(w[0], w[1:]) or rc
else:
    rc = run(args[0], args[1:])
json.dump(db, open(dbf, 'w'))
sys.exit(rc)
"""


def fresh_openwrt_config():
    """The configuration an armsr first boot has when uci-defaults run (config_generate's, abridged)."""
    def sec(_name, _type, _anon=False, **opts):
        return {'name': _name, 'type': _type, 'anon': _anon, 'opts': opts}
    return {
        'system': [sec('cfg01e48a', 'system', True, hostname='OpenWrt', timezone='UTC', ttylogin='0')],
        'network': [sec('loopback', 'interface', proto='static', ipaddr='127.0.0.1', netmask='255.0.0.0'),
                    sec('globals', 'globals', ula_prefix='fd12:3456:789a::/48'),
                    sec('cfg030f15', 'device', True, name='br-lan', type='bridge', ports=['eth0']),
                    sec('lan', 'interface', device='br-lan', proto='static', ipaddr='192.168.1.1',
                        netmask='255.255.255.0', ip6assign='60'),
                    sec('wan', 'interface', device='eth1', proto='dhcp'),
                    sec('wan6', 'interface', device='eth1', proto='dhcpv6')],
        'firewall': [sec('cfg01e63d', 'defaults', True, input='REJECT', output='ACCEPT', forward='REJECT'),
                     sec('cfg02dc81', 'zone', True, name='lan', network=['lan']),
                     sec('cfg03dc81', 'zone', True, name='wan', network=['wan', 'wan6'], masq='1')],
        'dhcp': [sec('lan', 'dhcp', interface='lan', start='100', limit='150', leasetime='12h'),
                 sec('wan', 'dhcp', interface='wan', ignore='1')],
        # luci-base's own, with the languages the image's catalogs registered
        'luci': [sec('main', 'core', lang='auto', mediaurlbase='/luci-static/bootstrap'),
                 sec('languages', 'internal', tr='T\u00fcrk\u00e7e (Turkish)', zh_cn='Chinese')],
    }


class EarlyUsbOpenWrt(ShellTest):
    """The early DHCP lease of boot/init carried into OpenWrt (K9, K11, K13, K25-K27): preinit serves the system's
    own LAN on the gadget's netdev until netifd brings the LAN up, the LAN hook ends it, and the first-boot defaults
    give the host its pinned, broadcast lease and the early path its firewall zone."""

    def setUp(self):
        super().setUp()
        (self.tmp / 'run').mkdir()
        (self.tmp / 'net').mkdir()

    def netdevs(self, *names):
        for n in names:
            (self.tmp / 'net' / n).mkdir(parents=True, exist_ok=True)

    def text(self, rel, **paths):
        body = (OPENWRT / rel).read_text()
        for old, new in paths.items():
            body = body.replace(old, new)
        return body

    # --- preinit 06_mu300_early_usb (K11)
    def preinit(self, shell, lan, netmask=None):
        self.stub('uci', f'echo "uci $*" >> "$STUBLOG/uci.log"\n'
                         f'[ "$*" = "-q get network.lan.ipaddr" ] && {"echo " + repr(lan) if lan else "exit 1"}\n'
                         f'[ "$*" = "-q get network.lan.netmask" ] && {"echo " + repr(netmask) if netmask else "exit 1"}\n'
                         'exit 1')
        self.stub('mu300-lan-ip', 'echo 192.168.78.1')
        # (not named busybox: that would be the shell under test, first on PATH)
        self.stub('bb', 'echo "busybox $*" >> "$STUBLOG/bb.log"\n'
                             'case $1 in udhcpd) cp "$3" "$STUBLOG/udhcpd.conf" ;; esac')
        body = self.text('lib/preinit/06_mu300_early_usb', **{'/lib/mu300/usb-host.sh': str(OPENWRT / 'lib' / 'mu300' / 'usb-host.sh'), 
            '/opt/mu300/bin/busybox': f'{self.stubs}/bb', '/opt/mu300/bin/': f'{self.stubs}/', '/run/': f'{self.tmp}/run/',
            '/sys/class/net/': f'{self.tmp}/net/'})
        r = self.sh(shell, 'boot_hook_add() { :; }\n' + body + '\nmu300_early_usb\nwait\n')
        self.assertEqual(0, r.returncode, r.stderr)
        conf = self.tmp / 'udhcpd.conf'
        return conf.read_text() if conf.exists() else None

    def test_preinit_uses_the_configured_lan(self):
        # a custom LAN address: the host is on the system's subnet from its first lease in the system on
        for shell in self.each_shell():
            for lan in ('10.1.2.1', '10.1.2.1/24'):
                self.netdevs('usb0')
                conf = self.preinit(shell, lan)
                for line in ('start 10.1.2.200', 'end 10.1.2.200', 'interface usb0', 'option router 10.1.2.1',
                             'option dns 10.1.2.1', 'option subnet 255.255.255.0', 'option lease 3600'):
                    self.assertIn(line + '\n', conf, lan)
                self.assertNotIn('192.168.77', conf)
                bb = (self.tmp / 'bb.log').read_text()
                self.assertIn('busybox ifconfig usb0 10.1.2.1 netmask 255.255.255.0 up', bb)
                self.assertTrue((self.tmp / 'run' / 'mu300-early-udhcpd.pid').read_text().strip().isdigit())
                self.tearDown(); self.setUp()

    def test_preinit_without_a_lan_address_takes_the_device_default(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            conf = self.preinit(shell, None)
            self.assertIn('start 192.168.78.200\n', conf)
            self.assertIn('option router 192.168.78.1\n', conf)
            self.tearDown(); self.setUp()

    def test_preinit_other_masks(self):
        # the pool and the mask inside the configured subnet (R37): a /16 from netmask or CIDR, a /25 without .200
        cases = (('10.1.0.1', '255.255.0.0', '10.1.0.200', '255.255.0.0'),
                 ('10.1.0.1/16', None, '10.1.0.200', '255.255.0.0'),
                 ('10.1.2.1/25', None, '10.1.2.126', '255.255.255.128'),
                 ('10.1.2.129/25', None, '10.1.2.200', '255.255.255.128'))
        for shell in self.each_shell():
            for lan, mask, host, subnet in cases:
                self.netdevs('usb0')
                conf = self.preinit(shell, lan, mask)
                self.assertIn(f'start {host}\nend {host}\n', conf, lan)
                self.assertIn(f'option subnet {subnet}\n', conf, lan)
                self.assertIn(f'netmask {subnet} up', (self.tmp / 'bb.log').read_text(), lan)
                self.tearDown(); self.setUp()

    def test_preinit_never_offers_the_router(self):
        for shell in self.each_shell():
            for lan, host in (('10.1.2.200', '10.1.2.199'), ('10.1.2.254/25', '10.1.2.200'),
                              ('10.1.2.126/25', '10.1.2.125')):
                self.netdevs('usb0')
                conf = self.preinit(shell, lan)
                self.assertIn(f'start {host}\n', conf, lan)
                self.tearDown(); self.setUp()

    def test_preinit_serves_rndis0(self):
        for shell in self.each_shell():
            self.netdevs('rndis0')
            conf = self.preinit(shell, '10.1.2.1')
            self.assertIn('interface rndis0\n', conf)
            self.assertIn('busybox ifconfig rndis0 10.1.2.1', (self.tmp / 'bb.log').read_text())
            self.tearDown(); self.setUp()

    def test_preinit_without_a_gadget_does_nothing(self):
        for shell in self.each_shell():
            self.assertIsNone(self.preinit(shell, '10.1.2.1'))
            self.assertFalse((self.tmp / 'run' / 'mu300-early-udhcpd.pid').exists())
            self.tearDown(); self.setUp()

    # --- hotplug iface/10-mu300-usb (K9; K10 rejected: the re-enumeration stays)
    def hotplug(self, shell, stamp_age=None, bridge='10.1.2.1/24', port='10.1.2.1/24', rndis_port='yes',
                rndis_addr='', usb0_port='yes', skip_rebind=False):
        (self.tmp / 'run' / 'mu300-early-udhcpd.pid').write_text('4242\n')
        (self.tmp / 'uptime').write_text('100.50 90.00\n')
        (self.tmp / 'mounts').write_text(f'configfs {self.tmp}/cfg configfs rw 0 0\n')
        g = self.tmp / 'cfg' / 'usb_gadget' / 'linux'
        g.mkdir(parents=True, exist_ok=True)
        (g / 'UDC').write_text('25100000.dwc3\n')
        if stamp_age is not None:
            (self.tmp / 'stamp').write_text(f'{100 - stamp_age}\n')
        # usb0 kept the bridge's address; rndis0 is in the bridge without it - unless the test says otherwise:
        # RNDIS_PORT=no is a rndis0 that is not a bridge port until the hook makes it one (the stub remembers the
        # enslavement), RNDIS_ADDR an address preinit left on it (issue #45)
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/ip.log"\n'
                        f'rp={rndis_port}; grep -q "^ip link set rndis0 master br-lan$" "$STUBLOG/ip.log" && rp=yes\n'
                        'case "$*" in\n'
                        f'"-4 -o addr show dev br-lan") echo "9: br-lan    inet {bridge} scope global br-lan" ;;\n'
                        f'"-o link show dev usb0") [ "{usb0_port}" = yes ] && echo "7: usb0: <UP> mtu 1500 master br-lan state UP" ||'
                        ' echo "7: usb0: <UP> mtu 1500 state UP" ;;\n'
                        '"-o link show dev rndis0") [ "$rp" = yes ] && echo "8: rndis0: <UP> mtu 1500 master br-lan state UP" ||'
                        ' echo "8: rndis0: <UP> mtu 1500 state UP" ;;\n'
                        f'"-4 -o addr show dev usb0") echo "7: usb0    inet {port} scope global usb0" ;;\n'
                        f'"-4 -o addr show dev rndis0") [ -n "{rndis_addr}" ] && echo "8: rndis0    inet {rndis_addr} scope global rndis0" ;;\n'
                        'esac\nexit 0')
        body = self.text('etc/hotplug.d/iface/10-mu300-usb', **{
            '/run/': f'{self.tmp}/run/', '/sys/class/net/': f'{self.tmp}/net/', '/tmp/mu300-usb-rebound': f'{self.tmp}/stamp',
            '/sys/kernel/config': f'{self.tmp}/cfg', '/proc/uptime': f'{self.tmp}/uptime', '/proc/mounts': f'{self.tmp}/mounts'})
        code = ('kill() { echo "kill $*" >> "$STUBLOG/kill.log"; }; sleep() { :; }; logger() { :; }; mount() { :; }\n'
                f'ACTION=ifup INTERFACE=lan MU300_USB_SKIP_REBIND={int(skip_rebind)}\n' + body)
        r = self.sh(shell, code)
        self.assertEqual('', r.stderr)
        return g

    def test_lan_up_ends_the_early_server(self):
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            g = self.hotplug(shell)
            self.assertEqual('kill 4242\n', (self.tmp / 'kill.log').read_text())
            self.assertFalse((self.tmp / 'run' / 'mu300-early-udhcpd.pid').exists())
            ip = (self.tmp / 'ip.log').read_text()
            self.assertIn('ip addr del 10.1.2.1/24 dev usb0\n', ip)
            self.assertNotIn('dev rndis0\nip addr del', ip)
            self.assertEqual(1, ip.count('ip addr del'))
            self.assertIn('ip link set rndis0 master br-lan\n', ip)
            # the macOS re-enumeration still runs (K10 rejected until its gate)
            self.assertEqual('100', (self.tmp / 'stamp').read_text().strip())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.tearDown(); self.setUp()

    def test_rebind_recovery_hands_off_lan_without_scheduling_another_rebind(self):
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            g = self.hotplug(shell, skip_rebind=True, rndis_port='no', rndis_addr='10.1.2.1/24')
            self.assertEqual('kill 4242\n', (self.tmp / 'kill.log').read_text())
            self.assertFalse((self.tmp / 'run' / 'mu300-early-udhcpd.pid').exists())
            calls = (self.tmp / 'ip.log').read_text()
            self.assertIn('ip addr del 10.1.2.1/24 dev usb0\n', calls)
            self.assertIn('ip addr del 10.1.2.1/24 dev rndis0\n', calls)
            self.assertEqual(1, calls.count('ip link set rndis0 master br-lan\n'))
            self.assertFalse((self.tmp / 'stamp').exists(), 'recovery queued another gadget rebind')
            self.assertEqual('25100000.dwc3\n', (g / 'UDC').read_text())
            self.tearDown(); self.setUp()

    def test_rndis_joins_the_bridge_before_its_address_comes_off(self):
        """Issue #45: a device whose bridge section predates the switch to RNDIS. Preinit's early server (K11)
        left the LAN address on rndis0, and rndis0 is not a bridge port when this hook runs. It used to be enslaved
        after the removal loop, so the address stayed while br-lan held the same one, and neither the Wi-Fi clients
        nor the USB host could use the LAN. Join first, then take the address off - and once only."""
        for shell in self.each_shell():
            self.netdevs('rndis0')                     # a RNDIS boot has no usb0
            self.hotplug(shell, port='', rndis_port='no', rndis_addr='10.1.2.1/24')   # no usb0 address
            ip = (self.tmp / 'ip.log').read_text()
            self.assertIn('ip link set rndis0 master br-lan\n', ip)
            self.assertIn('ip addr del 10.1.2.1/24 dev rndis0\n', ip)
            self.assertLess(ip.index('ip link set rndis0 master br-lan\n'), ip.index('ip addr del 10.1.2.1/24 dev rndis0\n'))
            self.assertEqual(1, ip.count('ip addr del'))
            # after the re-enumeration the new netdev is a port again (the second half of the issue)
            self.assertEqual(2, ip.count('ip link set rndis0 master br-lan\n'))
            self.tearDown(); self.setUp()

    def test_a_netdev_that_is_not_a_port_stays_out_after_the_rebind(self):
        """Only a gadget netdev that was a bridge port before the re-enumeration is made one again afterwards: a
        user who took usb0 out of br-lan for an interface of its own keeps it that way, on every LAN ifup."""
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            self.hotplug(shell, port='', usb0_port='no', rndis_port='no')
            ip = (self.tmp / 'ip.log').read_text()
            self.assertNotIn('ip link set usb0 master br-lan\n', ip)
            # rndis0 joined before the removal loop, so it is a port and comes back after the rebind
            self.assertEqual(2, ip.count('ip link set rndis0 master br-lan\n'))
            self.tearDown(); self.setUp()

    def test_port_prefix_other_than_the_bridge(self):
        # I1: br-lan has the LAN as /16, preinit left a /24 on usb0 - its route would beat the bridge's
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell, bridge='10.1.0.1/16', port='10.1.0.1/24')
            ip = (self.tmp / 'ip.log').read_text()
            self.assertIn('ip addr del 10.1.0.1/24 dev usb0\n', ip)
            self.assertEqual(1, ip.count('ip addr del'))
            self.tearDown(); self.setUp()

    def test_another_address_on_the_port_stays(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell, bridge='10.1.0.1/16', port='10.1.0.10/24')
            self.assertNotIn('ip addr del', (self.tmp / 'ip.log').read_text())
            self.tearDown(); self.setUp()

    def test_without_rndis0_nothing_is_reattached(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell)
            self.assertNotIn('link set rndis0', (self.tmp / 'ip.log').read_text())
            self.tearDown(); self.setUp()

    def test_handoff_even_when_the_re_enumeration_waits(self):
        # a second LAN ifup within 20 s skips the re-enumeration, never the handoff
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell, stamp_age=5)
            self.assertEqual('kill 4242\n', (self.tmp / 'kill.log').read_text())
            self.assertIn('ip addr del 10.1.2.1/24 dev usb0', (self.tmp / 'ip.log').read_text())
            self.assertEqual('95', (self.tmp / 'stamp').read_text().strip())
            self.tearDown(); self.setUp()

    def test_other_events_do_nothing(self):
        body = self.text('etc/hotplug.d/iface/10-mu300-usb', **{'/run/': f'{self.tmp}/run/'})
        (self.tmp / 'run' / 'mu300-early-udhcpd.pid').write_text('4242\n')
        for shell in self.each_shell():
            for env in ('ACTION=ifdown INTERFACE=lan', 'ACTION=ifup INTERFACE=wan'):
                r = self.sh(shell, 'kill() { echo "kill $*" >> "$STUBLOG/kill.log"; }\n' + env + '\n' + body)
                self.assertEqual(0, r.returncode)
                self.assertFalse((self.tmp / 'kill.log').exists(), env)

    # --- uci-defaults 90-mu300 (K25, K26, K27)
    def defaults(self, shell, runs=1, mac='02:50:aa:bb:77:02', lan='192.168.77.1', config=None):
        """Run 90-mu300 RUNS times against the uci stand-in, on CONFIG (a fresh first boot's when None and there is
        none yet): uci.log."""
        (self.tmp / 'inittab').write_text('')
        if mac:
            (self.tmp / 'run' / 'mu300-usb-host-mac').write_text(mac + '\n')
        self.stub('mu300-lan-ip', f'echo {lan}')
        self.uci_standin(config)
        body = self.text('etc/uci-defaults/90-mu300', **{'/lib/mu300/usb-host.sh': str(OPENWRT / 'lib' / 'mu300' / 'usb-host.sh'), 
            '/opt/mu300/bin/': f'{self.stubs}/', '/run/': f'{self.tmp}/run/', '/sys/': f'{self.tmp}/sys/',
            '/etc/inittab': f'{self.tmp}/inittab', '/usr/lib/lua/luci/i18n/': f'{self.tmp}/i18n/'})
        for _ in range(runs):
            r = self.sh(shell, body)
            self.assertEqual(0, r.returncode, r.stderr)
        return (self.tmp / 'uci.log').read_text()

    def uci_standin(self, config=None):
        (self.stubs / 'uci.py').write_text(UCI_STANDIN)
        self.stub('uci', f'exec python3 "{self.stubs}/uci.py" "$@"')
        db = self.tmp / 'uci.json'
        if config is not None or not db.exists():
            db.write_text(json.dumps(fresh_openwrt_config() if config is None else config))

    def uci(self, *args):
        """The stand-in, as the test's own edit (not logged): its stdout."""
        r = subprocess.run(['python3', str(self.stubs / 'uci.py')] + list(args), capture_output=True, text=True,
                           env=self.env(UCI_NOLOG=1))
        return r.stdout.strip() if r.returncode == 0 else None

    def test_defaults_pin_the_usb_host_with_broadcast(self):
        for shell in self.each_shell():
            log = self.defaults(shell)
            for line in ("batch: set dhcp.mu300_usb=host", "batch: set dhcp.mu300_usb.mac='02:50:aa:bb:77:02'",
                         "batch: set dhcp.mu300_usb.ip='192.168.77.200'", "batch: set dhcp.mu300_usb.broadcast='1'",
                         'uci commit dhcp'):
                self.assertIn(line + '\n', log)
            # Task 30's offloading stays
            self.assertIn("uci -q set firewall.@defaults[0].flow_offloading=1\n", log)
            self.tearDown(); self.setUp()

    def test_defaults_never_pin_the_router(self):
        for shell in self.each_shell():
            log = self.defaults(shell, lan='192.168.77.200')
            self.assertIn("batch: set dhcp.mu300_usb.ip='192.168.77.199'\n", log)
            self.tearDown(); self.setUp()

    def test_defaults_without_a_known_host_mac_pin_nothing(self):
        for shell in self.each_shell():
            log = self.defaults(shell, mac=None)
            self.assertNotIn('mu300_usb', log)
            self.tearDown(); self.setUp()

    def test_defaults_add_the_earlyusb_zone_once(self):
        for shell in self.each_shell():
            log = self.defaults(shell, runs=2)
            self.assertEqual(1, log.count('uci add firewall zone\n'))
            for line in ('uci set firewall.cfg0e1.name=earlyusb', 'uci add_list firewall.cfg0e1.device=usb0',
                         'uci add_list firewall.cfg0e1.device=rndis0', 'uci set firewall.cfg0e1.input=ACCEPT',
                         'uci set firewall.cfg0e1.forward=REJECT', 'uci commit firewall'):
                self.assertIn(line + '\n', log)
            self.tearDown(); self.setUp()

    def test_defaults_name_the_lan_and_wan_zone_devices_once(self):
        """Issue #67: the zones carry their devices, so the S19 ruleset has the LAN's input rules and the wan's
        masquerade before netifd has made br-lan and sipa_eth0; added once, and not to a zone that has it."""
        for shell in self.each_shell():
            log = self.defaults(shell, runs=2)
            self.assertEqual(1, log.count('uci add_list firewall.@zone[0].device=br-lan\n'))
            self.assertEqual(1, log.count('uci add_list firewall.@zone[1].device=sipa_eth0\n'))
            self.assertEqual(self.uci('get', 'firewall.@zone[0].device'), 'br-lan')
            self.assertEqual(self.uci('get', 'firewall.@zone[1].device'), 'sipa_eth0 sipa_eth8')   # #67 + #72: both bearers
            self.tearDown(); self.setUp()

    # --- hotplug.d/iface/19-mu300-fw4 (issue #67): the 5.4 kernel's stale flowtable before 20-firewall's reload
    def fw4_hook(self, shell, kernel='5.4.254', action='ifup', iface='lan', flowtable=True, in_zone=True):
        self.stub('uname', f'echo {kernel}')
        self.stub('fw4', 'echo "fw4 $*" >> "$STUBLOG/fw4.log"; ' + ('exit 0' if in_zone else 'exit 1'))
        self.stub('nft', 'echo "nft $*" >> "$STUBLOG/nft.log"\n'
                         'case "$*" in "list flowtables inet fw4") '
                         + ('printf "table inet fw4 {\\n\\tflowtable ft {\\n\\t}\\n}\\n"' if flowtable else ':')
                         + ' ;; esac')
        self.stub('logger', ':')
        body = self.text('etc/hotplug.d/iface/19-mu300-fw4', **{'/etc/init.d/firewall enabled': 'true'})
        r = self.sh(shell, f'ACTION={action} INTERFACE={iface}\n' + body)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual('', r.stderr)
        nft = self.tmp / 'nft.log'
        return nft.read_text() if nft.exists() else ''

    def test_fw4_hook_deletes_the_flowtable_on_5_4_before_the_reload(self):
        for shell in self.each_shell():
            self.assertIn('nft delete flowtable inet fw4 ft\n', self.fw4_hook(shell))
            self.tearDown(); self.setUp()

    def test_fw4_hook_leaves_the_flowtable_alone_when_no_reload_follows_or_the_kernel_updates_it(self):
        for shell in self.each_shell():
            for kw in ({'kernel': '6.18.56'}, {'kernel': '7.2.10'}, {'action': 'ifdown'}, {'in_zone': False},
                       {'flowtable': False}):
                with self.subTest(shell=' '.join(shell), **kw):
                    self.assertNotIn('delete', self.fw4_hook(shell, **kw))
                    self.tearDown(); self.setUp()

    def test_defaults_bridge_rndis0_only_when_it_exists(self):
        for shell in self.each_shell():
            for present in (False, True):
                if present:
                    (self.tmp / 'sys' / 'class' / 'net' / 'rndis0').mkdir(parents=True)
                log = self.defaults(shell)
                self.assertIn("uci add_list network.@device[0].ports=usb0\n", log)
                self.assertEqual(present, "uci add_list network.@device[0].ports=rndis0\n" in log)
                self.assertIn("uci set network.@device[0].bridge_empty=1\n", log)
                self.tearDown(); self.setUp()

    # --- 90-mu300 again after an update (a fresh rootfs, the kept /etc/config)
    USER = {'network.lan.ipaddr': '10.9.8.1', 'network.lan.netmask': '255.255.0.0', 'network.lan.ip6assign': '48',
            'network.wan.apn': 'internet.example', 'system.@system[0].hostname': 'myrouter',
            'system.@system[0].zonename': 'Europe/Berlin', 'system.@system[0].timezone': 'CET-1CEST,M3.5.0,M10.5.0/3',
            'firewall.@defaults[0].flow_offloading': '0', 'dhcp.mu300_usb.ip': '10.9.8.50'}

    def user_changes(self):
        """What a user changes after the first install: USER, br-lan's members, and a wan6 of their own."""
        for k, v in self.USER.items():
            self.uci('set', f'{k}={v}')
        self.uci('delete', 'network.@device[0].ports')
        self.uci('add_list', 'network.@device[0].ports=usb0')
        self.uci('add_list', 'network.@device[0].ports=eth9')
        self.uci('set', 'network.wan6=interface')
        self.uci('set', 'network.wan6.proto=dhcpv6')

    def test_first_install_sets_the_defaults_and_the_marker_last(self):
        for shell in self.each_shell():
            log = self.defaults(shell)
            want = {'network.lan.ipaddr': '192.168.77.1', 'network.lan.netmask': '255.255.255.0',
                    'network.lan.ip6assign': '64', 'network.wan.proto': 'mu300cell', 'network.wan.apn': '',
                    'system.@system[0].hostname': 'mu300', 'system.@system[0].zonename': 'Europe/Istanbul',
                    'system.@system[0].timezone': '<+03>-3', 'firewall.@defaults[0].flow_offloading': '1',
                    'firewall.@defaults[0].flow_offloading_hw': '0', 'network.@device[0].ports': 'usb0',
                    'network.@device[0].bridge_empty': '1', 'dhcp.mu300_usb.ip': '192.168.77.200',
                    'system.mu300.defaults': '1'}
            for k, v in want.items():
                self.assertEqual(self.uci('get', k), v, (shell, k))
            self.assertIsNone(self.uci('get', 'network.wan6'))
            # the marker is set and committed after every other commit
            lines = log.splitlines()
            self.assertEqual(lines[-2:], ["batch: set system.mu300.defaults='1'", 'uci commit system'])
            self.assertEqual((self.tmp / 'commits').read_text().split()[-2:], ['network', 'system'])
            self.tearDown(); self.setUp()

    def test_an_update_keeps_the_users_settings(self):
        # the first boot after an update runs 90-mu300 again on the kept configuration: what the user set stays,
        # what the system needs is still there
        for shell in self.each_shell():
            self.defaults(shell)                                    # the first install
            self.user_changes()
            self.uci('set', 'network.wan.proto=dhcp')               # something set wan aside
            self.defaults(shell, mac='02:50:aa:bb:77:09')           # the first boot after an update
            for k, v in self.USER.items():
                self.assertEqual(self.uci('get', k), v, (shell, k))
            self.assertEqual(self.uci('get', 'network.@device[0].ports'), 'usb0 eth9', shell)
            self.assertEqual(self.uci('get', 'network.wan6.proto'), 'dhcpv6', shell)
            self.assertEqual(self.uci('get', 'dhcp.mu300_usb.mac'), '02:50:aa:bb:77:02', shell)
            self.assertEqual(self.uci('get', 'network.wan.proto'), 'mu300cell', shell)
            self.assertEqual(self.uci('get', 'network.@device[0].bridge_empty'), '1', shell)
            self.assertEqual(self.uci('show', 'firewall').count("name='earlyusb'"), 1, shell)
            self.assertEqual((self.tmp / 'inittab').read_text().count('ttyGS0:'), 1, shell)
            self.tearDown(); self.setUp()

    def test_an_update_of_an_install_from_before_the_marker(self):
        # set up by an earlier 90-mu300 (wan proto mu300cell, no marker, no K25 host entry, no earlyusb zone, no
        # flowtable, no bridge_empty): the user's settings stay; it gets what it lacked, and the marker
        old = fresh_openwrt_config()
        for s in old['network']:
            if s['name'] == 'lan':
                s['opts'].update(ipaddr='10.9.8.1', netmask='255.255.255.0', ip6assign='64')
            if s['name'] == 'wan':
                s['opts'] = {'proto': 'mu300cell', 'apn': 'internet.example'}
            if s['type'] == 'device':
                s['opts']['ports'] = ['usb0']
        old['network'] = [s for s in old['network'] if s['name'] != 'wan6']
        old['system'][0]['opts'].update(hostname='myrouter', zonename='Europe/Berlin')
        for shell in self.each_shell():
            self.defaults(shell, config=old)
            for k, v in {'network.lan.ipaddr': '10.9.8.1', 'network.lan.ip6assign': '64',
                         'network.wan.apn': 'internet.example', 'system.@system[0].hostname': 'myrouter',
                         'system.@system[0].zonename': 'Europe/Berlin', 'network.wan.proto': 'mu300cell',
                         'firewall.@defaults[0].flow_offloading': '1', 'network.@device[0].bridge_empty': '1',
                         # the host entry on the LAN as configured, as preinit's early server gives it
                         'dhcp.mu300_usb.ip': '10.9.8.200', 'system.mu300.defaults': '1'}.items():
                self.assertEqual(self.uci('get', k), v, (shell, k))
            self.assertEqual(self.uci('show', 'firewall').count("name='earlyusb'"), 1, shell)
            # and from now on it is an update like any other: the flowtable the user turns off stays off
            self.uci('set', 'firewall.@defaults[0].flow_offloading=0')
            self.defaults(shell)
            self.assertEqual(self.uci('get', 'firewall.@defaults[0].flow_offloading'), '0', shell)
            self.tearDown(); self.setUp()

    def test_openwrt_luci_first_install_ends_with_ip6assign_60(self):
        # 90-mu300 then 91-mu300-luci: the panel system's first install asks for a /60 from the ULA (K30); an update
        # keeps what the user chose
        for shell in self.each_shell():
            self.defaults(shell)
            r = self.script(shell, LUCI_DEFAULTS)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.uci('get', 'network.lan.ip6assign'), '60', shell)
            self.uci('set', 'network.lan.ip6assign=64')
            self.defaults(shell)
            self.assertEqual(self.script(shell, LUCI_DEFAULTS).returncode, 0)
            self.assertEqual(self.uci('get', 'network.lan.ip6assign'), '64', shell)
            self.tearDown(); self.setUp()

    def test_the_images_languages_are_offered_after_an_update(self):
        # the kept /etc/config/luci of a plain OpenWrt from before had no languages: the image's catalogs (Turkish,
        # Chinese) are registered on the first boot after the update; a name the user's configuration has stays
        for shell in self.each_shell():
            (self.tmp / 'i18n').mkdir(exist_ok=True)
            for n in ('base.tr.lmo', 'base.zh-cn.lmo'):
                (self.tmp / 'i18n' / n).write_text('x')
            old = fresh_openwrt_config()
            old['luci'][1]['opts'] = {'zh_cn': 'Mine'}
            self.defaults(shell, config=old)
            self.assertEqual(self.uci('get', 'luci.languages.tr'), 'T\u00fcrk\u00e7e (Turkish)', shell)
            self.assertEqual(self.uci('get', 'luci.languages.zh_cn'), 'Mine', shell)
            # without the catalogs (no LuCI translations in the image) nothing is offered
            for n in ('base.tr.lmo', 'base.zh-cn.lmo'):
                (self.tmp / 'i18n' / n).unlink()
            old['luci'][1]['opts'] = {}
            self.defaults(shell, config=old)
            self.assertIsNone(self.uci('get', 'luci.languages.tr'), shell)
            self.tearDown(); self.setUp()

    def test_luci_starts_in_english_and_keeps_a_chosen_language(self):
        # a first install is English, not auto; an update keeps a language the user chose; a system from before
        # the marker that still says auto (nobody's choice) gets English once; no LuCI, nothing touched
        for shell in self.each_shell():
            self.defaults(shell)
            self.assertEqual(self.uci('get', 'luci.main.lang'), 'en', shell)
            self.assertEqual(self.uci('get', 'luci.mu300.lang'), '1', shell)
            self.assertEqual(self.script(shell, LUCI_DEFAULTS).returncode, 0)
            self.assertEqual(self.uci('get', 'luci.main.lang'), 'en', shell)
            for chosen in ('tr', 'auto', 'zh_cn'):
                self.uci('set', f'luci.main.lang={chosen}')
                self.defaults(shell)
                self.assertEqual(self.script(shell, LUCI_DEFAULTS).returncode, 0)
                self.assertEqual(self.uci('get', 'luci.main.lang'), chosen, (shell, chosen))
            for before in ('auto', None, 'tr'):
                old = fresh_openwrt_config()
                old['luci'][0]['opts'].pop('lang')
                if before:
                    old['luci'][0]['opts']['lang'] = before
                self.defaults(shell, config=old)
                self.assertEqual(self.uci('get', 'luci.main.lang'), before if before == 'tr' else 'en', (shell, before))
            old = fresh_openwrt_config()
            del old['luci']
            (self.tmp / 'uci.log').write_text('')
            log = self.defaults(shell, config=old)
            self.assertNotIn('set luci', log, shell)
            self.assertNotIn('commit luci', log, shell)
            self.tearDown(); self.setUp()

    # --- init.d/mu300-post (K13)
    def test_post_bridges_usb1(self):
        text = (OPENWRT / 'etc' / 'init.d' / 'mu300-post').read_text()
        self.assertIn('[ -e /sys/class/net/usb1 ] && ip link set usb1 master br-lan', text)
        # K12's gate was shown on macOS only (Windows and Linux hosts not measured): one behaviour for every host,
        # the lease check and the re-enumeration; the fork's one-shot rebind at boot is not started
        self.assertIn('mu300-usb-reset --if-no-lease 25', text)
        self.assertNotIn('usb-ready', text)
        self.assertNotIn('--fast-run', text)

    def test_overlay_scripts_are_executable(self):
        # the fork's mu300-post and mu300-usb-reset (Task 26) run the LAN hook directly
        for rel in ('lib/mu300/usb-host.sh', 'lib/preinit/06_mu300_early_usb', 'etc/hotplug.d/iface/10-mu300-usb', 'etc/uci-defaults/90-mu300'):
            self.assertTrue((OPENWRT / rel).stat().st_mode & 0o111, rel)


class LanStartHandoff(ShellTest):
    """Ubuntu: the early lease of boot/init ends at lan-start, which takes the early address off whichever gadget
    netdev init put it on (usb0, or rndis0 with RNDIS), so br-lan alone holds the subnet and dnsmasq answers."""

    def test_flushes_both_gadget_netdevs(self):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/ip.log"; exit 0')
        self.stub('dnsmasq', 'echo "dnsmasq $*" >> "$STUBLOG/dnsmasq.log"')
        r = subprocess.run(['bash', str(BIN / 'lan-start')], capture_output=True, text=True,
                           env=self.env(MU300_LAN_IP='10.1.2.1'), timeout=30)
        self.assertEqual(0, r.returncode, r.stderr)
        ip = (self.tmp / 'ip.log').read_text()
        self.assertIn('ip addr flush dev usb0\n', ip)
        self.assertIn('ip addr flush dev rndis0\n', ip)
        self.assertLess(ip.index('ip addr flush dev rndis0'), ip.index('ip addr replace 10.1.2.1/24 dev br-lan'))
        # the early host address (.200) is in the pool: a renewal is answered (NAK, then a fresh lease)
        self.assertIn('--dhcp-range=10.1.2.2,10.1.2.200,', (self.tmp / 'dnsmasq.log').read_text())


if __name__ == '__main__':
    unittest.main()
