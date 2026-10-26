"""wifi-client: the radio as a client of another network, against a fake / (MU300_SYSROOT) and stubs for iw,
wpa_supplicant, wpa_cli, udhcpc, nft and the rest. The scan parsing uses the real format of iw 6.7 and of
wpa_supplicant 2.10's scan_results (captured on an F50); the rest is what the script asks those tools to do."""
import os
import re

from helpers import BIN, TOP, ShellTest

WIFI = BIN / 'wifi-client'
PSKHEX = 'ab' * 32


def bss(mac, ssid_line, signal, extra='', capability='ESS Privacy SpectrumMgmt RadioMeasure (0x1111)'):
    """one BSS as iw 6.7 prints it"""
    s = (f'BSS {mac}(on wlan0)\n\tTSF: 2705885027 usec (0d, 00:45:05)\n\tfreq: 5560\n'
         f'\tbeacon interval: 100 TUs\n\tcapability: {capability}\n\tsignal: {signal}.00 dBm\n'
         '\tlast seen: 1312 ms ago\n')
    if ssid_line is not None:
        s += f'\tSSID: {ssid_line}\n'
    s += '\tSupported rates: 6.0* 9.0 12.0* 18.0 24.0* 36.0 48.0 54.0 \n'
    return s + extra


def rsn(suites, wpa1=False):
    kind = 'WPA' if wpa1 else 'RSN'
    return (f'\t{kind}:\t * Version: 1\n\t\t * Group cipher: CCMP\n\t\t * Pairwise ciphers: CCMP\n'
            f'\t\t * Authentication suites: {suites}\n'
            '\t\t * Capabilities: 16-PTKSA-RC 1-GTKSA-RC MFP-capable (0x00cc)\n'
            '\tBSS Load:\n\t\t * station count: 5\n')


# a reduced neighbor report after the name: iw prints a BSSID line in it, and "SSID: " is in "BSSID: "
RNR = ('\tReduced Neighbor Report:\n\t\t * TBTT Information Header: 0x0001\n'
       '\t\t\t * BSSID: 00:00:00:00:00:00\n\t\t\t * Short SSID: 0x12345678\n')

IWSCAN = (
    bss('2e:16:9d:c0:49:d0', 'KEDI 5G', -32, rsn('PSK'))
    + bss('2e:16:9d:c0:49:d6', 'KEDI MLO', -33, rsn('SAE') + RNR)
    + bss('2e:16:9d:c0:49:d2', 'KEDI MISAFIR 5G', -34, rsn('PSK SAE'))
    + bss('2a:16:9d:c0:49:d0', None, -35, rsn('PSK'))                     # hidden: no name at all
    + bss('2a:16:9d:c0:49:d9', '\\x00\\x00\\x00\\x00', -36, rsn('PSK'))      # hidden: a zeroed name
    + bss('5c:7d:ae:b4:0b:be', '\\xc3\\x87ay Evi \\xe2\\x98\\x95', -40, rsn('PSK'))
    + bss('5c:7d:ae:b4:0b:bf', 'bad\\x1b[2Jname', -41, rsn('PSK'))
    + bss('5c:7d:ae:b4:0b:c0', 'c1\\xc2\\x9b31mcsi', -42, rsn('PSK'))           # U+009B: CSI on some terminals
    + bss('5c:7d:ae:b4:0b:c1', 'raw\\x9b\\xff\\xc3end', -43, rsn('PSK'))        # not UTF-8 at all
    + bss('5c:7d:ae:b4:0b:c2', 'bidi\\xe2\\x80\\xaegpj.exe', -44, rsn('PSK'))   # U+202E
    + bss('5c:7d:ae:b4:0b:c3', 'bell\\x07\\x7f\\x0a', -45, rsn('PSK'))
    + bss('5c:7d:ae:b4:0b:c4', 'Ho\\xe2\\x80\\x8bme', -46, rsn('PSK'))               # zero-width space
    + bss('5c:7d:ae:b4:0b:c5', '\\xef\\xbb\\xbfbom\\xd8\\x9cALM', -47, rsn('PSK'))     # BOM, U+061C
    + bss('5c:7d:ae:b4:0b:c6', 'ls\\xe2\\x80\\xa8iso\\xe2\\x81\\xa6', -48, rsn('PSK'))  # U+2028, U+2066
    + bss('5c:7d:ae:b4:0b:c7', 'over\\xc0\\xaf\\xe0\\x80\\xaf\\xed\\xa0\\x80', -49, rsn('PSK'))  # overlong, surrogate
    + bss('5c:7d:ae:b4:0b:c8', 'tag\\xf3\\xa0\\x80\\x81\\xf0\\x9f\\x98\\x80', -50, rsn('PSK'))  # a tag, an emoji
    + bss('5c:7d:ae:b4:0b:c9', 'esc\\x5cx1b', -51, rsn('PSK'))                       # a real backslash
    + bss('6c:5a:b0:ea:60:74', 'old router', -70, rsn('PSK', wpa1=True))
    + bss('6c:5a:b0:ea:60:75', 'office', -72, rsn('IEEE 802.1X'))
    + bss('6c:5a:b0:ea:60:76', 'cafe', -75, '', capability='ESS ShortSlotTime (0x0401)')
    + bss('6c:5a:b0:ea:60:77', 'museum', -76, '', capability='ESS Privacy ShortSlotTime (0x0411)')
)

SCAN_RESULTS = '\n'.join([
    'bssid / frequency / signal level / flags / ssid',
    '2e:16:9d:c0:49:d0\t5560\t-32\t[WPA2-PSK-CCMP][ESS]\tKEDI 5G',
    '2e:16:9d:c0:49:d6\t5560\t-32\t[WPA2-SAE-CCMP][SAE-H2E][ESS]\tKEDI MLO',
    '2a:16:9d:c0:49:d5\t2462\t-34\t[WPA2-SAE-CCMP][SAE-H2E][ESS]\tKEDI MLO',
    '2e:16:9d:c0:49:d1\t5560\t-33\t[WPA2-PSK-CCMP][ESS]\t',
    '2e:16:9d:c0:49:d2\t5560\t-33\t[WPA2-PSK+SAE-CCMP][SAE-H2E][ESS]\tKEDI MISAFIR 5G',
    '5c:7d:ae:b4:0b:be\t5180\t-42\t[WPA2-PSK-CCMP][ESS][UTF-8]\t\\xc3\\x87ay Evi',
    '5c:7d:ae:b4:0b:bc\t5180\t-43\t[WPA2-PSK-CCMP][ESS]\tback\\\\slash \\"quoted\\"',
    '80:2b:f9:4f:4a:4f\t2412\t-71\t[WPA-PSK-CCMP+TKIP][WPA2-PSK-CCMP+TKIP][WPS][ESS]\tTURKSAT',
    '6c:5a:b0:ea:60:75\t2437\t-79\t[WPA2-EAP-CCMP][ESS]\toffice',
    '6c:5a:b0:ea:60:76\t2437\t-80\t[ESS]\tcafe',
    '6c:5a:b0:ea:60:77\t2437\t-81\t[ESS]\tevil\\x1b]0;title\\x07\\xc2\\x85x',
]) + '\n'


ORPHAN_LIB = BIN.parent / 'lib' / 'vpn-orphan.sh'


class WifiClient(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'sys/class/net/wlan0').mkdir(parents=True)
        (self.root / 'etc/mu300').mkdir(parents=True)
        (self.root / 'run').mkdir()
        self.conf = self.root / 'etc/mu300/wifi-client.conf'
        self.wpaconf = self.root / 'run/mu300-wifi-client.conf'
        # the LAN's subnet comes from its configuration (br-lan has no address yet at a boot join)
        (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.79.1\n')
        self.ev = self.tmp / 'events'
        (self.tmp / 'iwscan').write_text(IWSCAN)
        (self.tmp / 'scan_results').write_text(SCAN_RESULTS)
        (self.tmp / 'capa').write_text('NONE IEEE8021X WPA-EAP WPA-PSK WPA-EAP-SUITE-B OWE DPP FT-PSK FT-EAP\n')
        log = 'echo "$(basename "$0") $*" >> "$STUBLOG/events"'
        self.stub('id', 'echo 0')
        self.stub('sleep', 'echo sleep >> "$STUBLOG/sleeps"')
        self.stub('timeout', 'shift; exec "$@"')
        self.stub('rfkill', 'exit 0')
        self.stub('resolvectl', 'exit 0')
        self.stub('systemctl', log + '\n[ "$1" = is-active ] && echo inactive\nexit 0')
        self.stub('systemd-run', log)
        self.stub('sysctl', log)
        self.stub('vpn', log)
        self.stub('iw', 'case "$*" in\n'
                        '  "dev wlan0 info") printf "\\ttype %s\\n" "$(cat "$STUBLOG/iftype" 2>/dev/null || echo managed)" ;;\n'
                        '  "dev wlan0 scan") cat "$STUBLOG/iwscan" ;;\n'
                        '  "dev wlan0 link") if [ -e "$STUBLOG/supplicant" ] || [ -e "$STUBLOG/assoc" ]; then\n'
                        '                      printf "Connected to 2e:16:9d:c0:49:d0 (on wlan0)\\n\\tsignal: -40 dBm\\n"\n'
                        '                    else echo "Not connected."; fi ;;\n'
                        '  "dev wlan0 disconnect") echo "iw $*" >> "$STUBLOG/events" ;;\n'
                        'esac')
        self.stub('pgrep', 'case "$*" in *wpa_supplicant*) [ -e "$STUBLOG/supplicant" ] ;; *) exit 1 ;; esac')
        # a supplicant that will not go ($STUBLOG/stuck) stays whatever it is sent
        self.stub('pkill', log + '\ncase "$*" in *wpa_supplicant*) [ -e "$STUBLOG/stuck" ] || rm -f "$STUBLOG/supplicant" ;; esac\nexit 0')
        self.stub('wpa_supplicant', log + '\n: > "$STUBLOG/supplicant"')
        # status: COMPLETED once the configuration it was handed has a network in it, unless told otherwise
        self.stub('wpa_cli', 'case $3 in\n'
                             '  status) echo poll >> "$STUBLOG/polls"; [ -e "$STUBLOG/supplicant" ] || exit 1\n'
                             '          if [ -e "$STUBLOG/never" ]; then echo wpa_state=SCANNING; exit 0; fi\n'
                             '          grep -q "network=" "$MU300_SYSROOT/run/mu300-wifi-client.conf" 2>/dev/null || { echo wpa_state=DISCONNECTED; exit 0; }\n'
                             '          echo wpa_state=COMPLETED; echo "ssid=KEDI 5G" ;;\n'
                             '  scan_results) cat "$STUBLOG/scan_results" ;;\n'
                             '  scan) echo OK ;;\n'
                             '  get_capability) [ "$4" = key_mgmt ] && cat "$STUBLOG/capa" ;;\n'
                             '  terminate) echo "wpa_cli terminate" >> "$STUBLOG/events" ;;\n'
                             '  -a) echo "wpa_cli -a $4 $5 $6 $7" >> "$STUBLOG/events" ;;\n'
                             '  reconfigure) echo "wpa_cli reconfigure" >> "$STUBLOG/events"\n'
                             '               cp "$MU300_SYSROOT/run/mu300-wifi-client.conf" "$STUBLOG/wpaconf.used"; echo OK ;;\n'
                             'esac')
        # wpa_passphrase as the real one: the passphrase from the command line or, without one, a line of stdin
        self.stub('wpa_passphrase', log + '\n'
                                    'if [ $# -ge 2 ]; then p=$2; else echo "# reading passphrase from stdin"; IFS= read -r p; fi\n'
                                    'printf \'network={\\n\\tssid="%s"\\n\\t#psk="%s"\\n\\tpsk=' + PSKHEX + '\\n}\\n\' "$1" "$p"')
        self.stub('udhcpc', log + '\n: > "$STUBLOG/addr"')
        self.stub('ip', 'case "$*" in\n'
                        '  "-4 -br addr show wlan0") [ -e "$STUBLOG/addr" ] && echo "wlan0 UP 192.168.2.248/24" ;;\n'
                        '  "-4 route show default dev wlan0") echo "default via 192.168.2.1 metric 50" ;;\n'
                        '  "-4 route show dev wlan0 proto kernel scope link") [ -e "$STUBLOG/addr" ] && echo "$(cat "$STUBLOG/subnet" 2>/dev/null || echo 192.168.2.0/24) src 192.168.2.248" ;;\n'
                        '  "-4 addr flush dev wlan0") rm -f "$STUBLOG/addr" ;;\n'
                        '  "-4 addr add "*" dev wlan0") : > "$STUBLOG/addr" ;;\n'
                        '  "-4 -o addr show dev br-lan") echo "5: br-lan    inet 192.168.79.1/24 brd 192.168.79.255 scope global br-lan" ;;\n'
                        'esac\n'
                        'case "$1" in rule|route) echo "ip $*" >> "$STUBLOG/events" ;; esac\nexit 0')
        # nft as the kernel keeps it: the filter table of the last transaction is what "list table" shows
        # ($STUBLOG/filter), in nft 1.0.9's own listing format (tabs, counters, a blank line between chains; the rules
        # themselves were checked against a real nft 1.0.9). nft -f fails while $STUBLOG/nftfail exists, only a
        # sharing one while nftfailshare does, and with nftlie it says yes and changes nothing; with nftlisterr no
        # listing works. "list table ip mu300_nat" answers whether mobile data shares.
        self.stub('nft', 'case "$1" in\n'
                         '  -f) [ -e "$STUBLOG/nftfail" ] && exit 1; t=$(cat)\n'
                         '      case $t in *"ip daddr {"*) [ -e "$STUBLOG/nftfailshare" ] && exit 1 ;; esac\n'
                         '      { echo "nft -f"; printf "%s\\n" "$t" | sed "s/^/  | /"; } >> "$STUBLOG/events"\n'
                         '      [ -e "$STUBLOG/nftlie" ] && exit 0\n'
                         '      printf "%s\\n" "$t" | awk \'/^table inet mu300_wifi_filter [{]/ { p = 1 } p { gsub(/    /, "\\t");\n'
                         '          sub(/counter /, "counter packets 0 bytes 0 "); if ($0 ~ /^\\tchain forward/) print ""; print }\' > "$STUBLOG/filter"\n'
                         '      [ -s "$STUBLOG/filter" ] || rm -f "$STUBLOG/filter" ;;\n'
                         '  list) [ -e "$STUBLOG/nftlisterr" ] && exit 1\n'
                         '        case "$*" in\n'
                         '          *mu300_nat*) [ -e "$STUBLOG/mobile" ] ;;\n'
                         '          "list table inet mu300_wifi_filter") [ -e "$STUBLOG/filter" ] && cat "$STUBLOG/filter" ;;\n'
                         '          "list tables") [ -e "$STUBLOG/filter" ] && echo "table inet mu300_wifi_filter"\n'
                         '                         [ -e "$STUBLOG/mobile" ] && echo "table ip mu300_nat"; exit 0 ;;\n'
                         '          *) exit 1 ;; esac ;;\n'
                         '  delete) echo "nft $*" >> "$STUBLOG/events"; case "$*" in *mu300_wifi_filter*) rm -f "$STUBLOG/filter" ;; esac ;;\n'
                         '  *) echo "nft $*" >> "$STUBLOG/events" ;;\n'
                         'esac')

    def run_wc(self, shell, *args, stdin='', **env):
        e = dict(MU300_SYSROOT=self.root, MU300_VPN_CMD=self.stubs / 'vpn', MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)
        e.update(env)
        return self.script(shell, WIFI, *args, stdin=stdin, **e)

    def fresh(self):
        for n in ('events', 'sleeps', 'polls', 'nftfail', 'nftfailshare', 'nftlie', 'nftlisterr', 'filter', 'mobile', 'subnet', 'supplicant', 'addr', 'never', 'wpaconf.used', 'iftype',
                  'stuck', 'assoc'):
            (self.tmp / n).unlink(missing_ok=True)
        for p in (self.conf, self.wpaconf, self.root / 'run/mu300-wifi-client.active',
                  self.root / 'run/mu300-wifi-client-had-ap', self.root / 'etc/mu300/vpn.conf'):
            p.unlink(missing_ok=True)

    def events(self):
        return self.ev.read_text() if self.ev.exists() else ''

    def rulesets(self):
        out = []
        for chunk in self.events().split('nft -f\n')[1:]:
            out.append('\n'.join(l[4:] for l in chunk.splitlines() if l.startswith('  | ')))
        return out

    def polls(self):
        # busybox sh can run its own sleep applet rather than the stub: count the supplicant's status polls instead
        p = self.tmp / 'polls'
        return len(p.read_text().splitlines()) if p.exists() else 0

    def sleeps(self):
        p = self.tmp / 'sleeps'
        return len(p.read_text().splitlines()) if p.exists() else 0

    def saved(self):
        return dict(l.split('=', 1) for l in self.conf.read_text().splitlines() if '=' in l)

    # ---- D6 and D3: the scan --------------------------------------------------------------------------------
    def scan_lines(self, out):
        """[(signal, label, name)] from the scan's '%4s dBm  %-6s  %s' lines"""
        rows = []
        for l in out.splitlines():
            m = re.match(r'^ *(-?\d+) dBm  (\S+) *  (.*)$', l)
            self.assertTrue(m, repr(l))
            self.assertEqual(l[18:], m.group(3), 'the name starts at column 19 (mu300-toolkit cuts it there)')
            rows.append((int(m.group(1)), m.group(2), m.group(3)))
        return rows

    def test_iw_scan(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'scan')
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = self.scan_lines(r.stdout)
            names = {n: lab for _, lab, n in rows}
            self.assertEqual(names['KEDI 5G'], 'WPA2')
            self.assertEqual(names['KEDI MLO'], 'WPA3')            # SAE only
            self.assertEqual(names['KEDI MISAFIR 5G'], 'WPA2/3')   # PSK and SAE: transition mode
            self.assertEqual(names['old router'], 'WPA')
            self.assertEqual(names['office'], 'EAP')
            self.assertEqual(names['cafe'], 'open')
            self.assertEqual(names['museum'], 'WEP')
            # UTF-8 names as themselves; control characters stay escaped, they would act on the terminal
            self.assertIn('Çay Evi ☕', names)
            self.assertIn('bad\\x1b[2Jname', names)
            self.assertIn('c1\\xc2\\x9b31mcsi', names)
            self.assertIn('raw\\x9b\\xff\\xc3end', names)
            self.assertIn('bidi\\xe2\\x80\\xaegpj.exe', names)
            self.assertIn('bell\\x07\\x7f\\x0a', names)
            self.assertIn('Ho\\xe2\\x80\\x8bme', names)
            self.assertIn('\\xef\\xbb\\xbfbom\\xd8\\x9cALM', names)
            self.assertIn('ls\\xe2\\x80\\xa8iso\\xe2\\x81\\xa6', names)
            self.assertIn('over\\xc0\\xaf\\xe0\\x80\\xaf\\xed\\xa0\\x80', names)
            self.assertIn('tag\\xf3\\xa0\\x80\\x81😀', names)
            self.assertIn('esc\\x5cx1b', names)       # the text: its backslash shown as \\x5c, so not the byte
            self.assertHostileFree(r.stdout)
            # no neighbour report's BSSID taken for a name, no hidden network
            self.assertNotIn('00:00:00:00:00:00', r.stdout)
            self.assertNotIn('\\x00', r.stdout)
            self.assertEqual(len(rows), 19, r.stdout)
            self.assertEqual([s for s, _, _ in rows], sorted([s for s, _, _ in rows], reverse=True))

    def assertHostileFree(self, text):
        """only printable text: no C0/C1 controls, no ESC, no DEL, no bidi overrides, valid UTF-8"""
        raw = text.encode('utf-8', 'surrogateescape')
        raw.decode('utf-8')
        for ch in raw.decode('utf-8'):
            o = ord(ch)
            self.assertFalse(o < 32 and ch != '\n' or o in (0xad, 0xa0) or 0xfe00 <= o <= 0xfe0f or 0x7f <= o <= 0x9f or 0x200b <= o <= 0x200f or 0x2028 <= o <= 0x202e
                             or 0x2060 <= o <= 0x206f or o in (0x61c, 0x180e, 0xfeff) or 0xe0000 <= o <= 0xe007f, repr(ch))

    def test_scan_through_the_supplicant(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'supplicant').touch()
            r = self.run_wc(shell, 'scan')
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = self.scan_lines(r.stdout)
            names = {n: lab for _, lab, n in rows}
            self.assertEqual(names['KEDI 5G'], 'WPA2')
            self.assertEqual(names['KEDI MLO'], 'WPA3')
            self.assertEqual(names['KEDI MISAFIR 5G'], 'WPA2/3')
            self.assertEqual(names['TURKSAT'], 'WPA2')
            self.assertEqual(names['office'], 'EAP')
            self.assertEqual(names['cafe'], 'open')
            self.assertIn('Çay Evi', names)
            self.assertIn('back\\x5cslash "quoted"', names)
            self.assertIn('evil\\x1b]0;title\\x07\\xc2\\x85x', names)
            self.assertHostileFree(r.stdout)
            self.assertEqual(sum(1 for _, _, n in rows if n == 'KEDI MLO'), 1)   # one line per name
            self.assertEqual(len(rows), 9, r.stdout)                              # the hidden one is not listed

    # ---- D4: the passphrase never on a command line ---------------------------------------------------------
    def assertJoinedWith(self, r, passphrase):
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.saved()['PSK'], passphrase)
        conf = self.wpaconf.read_text()
        self.assertIn(f'psk={PSKHEX}', conf)
        self.assertNotIn(passphrase, conf)       # wpa_passphrase also prints it, as a comment
        self.assertNotIn(passphrase, self.events())  # no program was handed it as an argument

    def test_password_from_stdin(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='s3cret pass\n')
            self.assertJoinedWith(r, 's3cret pass')
            self.assertNotIn('journal', r.stderr)

    def test_password_from_a_file(self):
        f = self.tmp / 'pw'
        f.write_text('from a file 1\r\n')
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '--password-file', f)
            self.assertJoinedWith(r, 'from a file 1')
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '--password-file', self.tmp / 'missing')
            self.assertNotEqual(r.returncode, 0)

    def test_password_argument_still_works_and_warns(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', 'on the line')
            self.assertJoinedWith(r, 'on the line')
            self.assertIn('journal', r.stderr)
            self.assertIn('connect "KEDI 5G"', r.stderr)

    def test_no_password_and_no_terminal_is_refused(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'cafe')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('--open', r.stderr)
            self.assertNotIn('pkill', self.events())

    def test_open_network(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'cafe', '--open')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('key_mgmt=NONE', self.wpaconf.read_text())
            self.assertNotIn('PSK', self.saved())

    def test_backslashes_survive_the_saved_file(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'a\\tb\\c', '-', stdin='pa\\ss\\cword\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.saved()['SSID'], 'a\\tb\\c')
            self.assertEqual(self.saved()['PSK'], 'pa\\ss\\cword')

    def test_failures_put_the_hotspot_back(self):
        # the supplicant does not start, or the kill switch cannot go up: the hotspot that was on comes back
        for case in ('supplicant', 'guard'):
            for shell in self.each_shell():
                self.fresh()
                (self.root / 'run/mu300-wifi-client-had-ap').touch()
                if case == 'supplicant':
                    self.stub('wpa_supplicant', 'exit 1')
                else:
                    # fatal only with the VPN on and its kill switch: a broken mu300-vpn with the VPN off is not
                    self.stub('vpn', 'exit 1')
                    r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.fresh()
                    (self.root / 'run/mu300-wifi-client-had-ap').touch()
                    (self.root / 'etc/mu300/vpn.conf').write_text('ENABLE=1\n')
                r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
                self.assertEqual(r.returncode, 1, case)
                self.assertIn('systemctl start mu300-hotspot', self.events(), case)
                if case == 'guard':
                    self.assertIn('kill switch', r.stderr)
                    self.assertNotIn('\nwpa_supplicant', self.events())
            (self.root / 'etc/mu300/vpn.conf').unlink(missing_ok=True)
            log = 'echo "$(basename "$0") $*" >> "$STUBLOG/events"'
            self.stub('wpa_supplicant', log + '\n: > "$STUBLOG/supplicant"')
            self.stub('vpn', log)

    def test_a_short_passphrase_is_refused_before_anything_is_touched(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='short\n')
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn('pkill', self.events())

    def test_status_shows_a_hostile_name_escaped(self):
        for shell in self.each_shell():
            self.fresh()
            self.stub('wpa_cli', 'case $3 in status) printf "wpa_state=COMPLETED\\nssid=x\\\\x1b[31m\\\\xc2\\\\x9by\\n" ;; esac')
            r = self.run_wc(shell, 'status')
            self.assertIn('network     x\\x1b[31m\\xc2\\x9by', r.stdout)
            self.assertHostileFree(r.stdout)

    def test_utf8_name_reaches_the_supplicant_as_bytes(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'Çay "Evi"', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ssid=' + 'Çay "Evi"'.encode().hex(), self.wpaconf.read_text())

    # ---- D3: WPA3 / SAE --------------------------------------------------------------------------------------
    def test_sae_only_is_refused_at_once_without_sae(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'never').touch()
            r = self.run_wc(shell, 'connect', 'KEDI MLO', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 1)
            self.assertIn('WPA3', r.stderr)
            self.assertIn('cannot', r.stderr)
            self.assertLess(self.polls(), 8, 'not the 25 s wait')
            self.assertFalse((self.tmp / 'supplicant').exists())
            self.assertFalse(self.conf.exists())
            # a WPA2 network that does not answer still gets the whole wait, and the generic message
            self.fresh()
            (self.tmp / 'never').touch()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n', MU300_JOIN_WAIT=12)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn('WPA3', r.stderr)
            self.assertGreaterEqual(self.polls(), 12)

    def test_sae_when_the_supplicant_and_driver_can(self):
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'capa').write_text('NONE WPA-PSK SAE OWE\n')
            r = self.run_wc(shell, 'connect', 'KEDI MLO', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            conf = self.wpaconf.read_text()
            self.assertIn('key_mgmt=WPA-PSK SAE', conf)
            self.assertIn('ieee80211w=1', conf)
            self.assertIn('sae_password=' + b'password1'.hex(), conf)
            self.assertIn('sae_pwe=2', conf)
            self.assertNotIn('password1', conf)
            # and without it, plain WPA-PSK
            self.fresh()
            (self.tmp / 'capa').write_text('NONE WPA-PSK OWE\n')
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            conf = self.wpaconf.read_text()
            self.assertIn('key_mgmt=WPA-PSK', conf)
            self.assertNotIn('SAE', conf)
            self.assertNotIn('sae_password', conf)

    # ---- D2: sharing the Wi-Fi uplink ------------------------------------------------------------------------
    def sharing(self, shell, subnet=None):
        self.fresh()
        (self.tmp / 'subnet').unlink(missing_ok=True)
        if subnet:
            (self.tmp / 'subnet').write_text(subnet)
        r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        rs = [x for x in self.rulesets() if 'table inet mu300_wifi_filter {' in x]
        self.assertEqual(len(rs), 2, self.events())   # before the radio joins, and with the subnet after DHCP
        rs = [rs[-1]]
        def chain(table, name):
            t = rs[0].split(f'table {table} {{', 1)[1].split(f'chain {name} {{', 1)[1]
            body = []
            for l in t.splitlines():
                if l.strip() == '}':
                    break
                body.append(l)
            return '\n'.join(body)
        return rs[0], chain

    def test_connect_shares_the_uplink(self):
        for shell in self.each_shell():
            rs, chain = self.sharing(shell)
            ev = self.events()
            self.assertIn('sysctl -qw net.ipv4.ip_forward=1', ev)
            # LAN clients out to the internet, masqueraded, as mobile-data does on sipa_eth0
            self.assertIn('oifname "wlan0" masquerade', chain('ip mu300_wifi_nat', 'postrouting'))
            # the VPN's kill switch goes up (when the VPN is on) before the radio can carry anything
            self.assertLess(ev.index('vpn guard'), ev.index('\nwpa_supplicant '))
            # and its routing and kill switch are never touched: with the VPN on, clients still go into the tunnel
            self.assertNotIn('ip rule', ev)
            self.assertNotIn('mu300_vpn', ev)

    def rules(self, text):
        return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('type ')]

    def test_nothing_comes_in_from_the_other_network(self):
        for shell in self.each_shell():
            rs, chain = self.sharing(shell)
            fwd = self.rules(chain('inet mu300_wifi_filter', 'forward'))
            # answers only, then everything from wlan0 dropped: nothing reaches the LAN
            self.assertEqual(fwd[:2], ['iifname "wlan0" ct state established,related accept',
                                       'iifname "wlan0" counter drop'])
            # the device's own services closed to it (SSH, DNS), DHCP answers and ping let in, as for the modem
            inp = self.rules(chain('inet mu300_wifi_filter', 'input'))
            self.assertEqual(inp[0], 'iifname "wlan0" ct state established,related accept')
            self.assertIn('iifname "wlan0" udp sport 67 udp dport 68 accept', inp)
            self.assertEqual(inp[-1], 'iifname "wlan0" counter drop')
            for r in inp[:-1]:
                self.assertTrue('established' in r or 'dport 68' in r or 'echo-request' in r, r)
            # no accept of anything new from wlan0 anywhere, and no chain with a drop policy that the VPN relies on
            self.assertNotIn('policy drop', rs)
            self.assertIn('table inet mu300_wifi_filter', rs)   # inet: IPv6 on wlan0 is closed too

    def test_lan_clients_reach_only_the_internet(self):
        for shell in self.each_shell():
            for subnet in (None, '203.0.113.0/24'):
                rs, chain = self.sharing(shell, subnet)
                fwd = self.rules(chain('inet mu300_wifi_filter', 'forward'))
                out = [r for r in fwd if r.startswith('oifname "wlan0"')]
                # the other network's private, link-local and CGNAT addresses: its other hosts and its router's
                # admin page are not for LAN clients; the router is only their way out
                self.assertIn('oifname "wlan0" ip daddr { %s } counter drop' % self.PRIVATE, out)
                self.assertEqual(self.applied()[-1], self.expected(subnet or '192.168.2.0/24'))
                self.assertIn(f'oifname "wlan0" ip daddr {subnet or "192.168.2.0/24"} counter drop', out)
                self.assertIn('oifname "wlan0" meta nfproto ipv6 counter drop', out)
                # every drop before the only other rule, the MSS clamp; nothing accepted towards wlan0
                self.assertTrue(out[-1].startswith('oifname "wlan0" tcp flags syn'), out)
                self.assertFalse([r for r in out if r.endswith('accept')], out)

    def test_leaving_stops_sharing(self):
        for cmd in (['disconnect'], ['forget'], ['disconnect', '--keep']):
            for shell in self.each_shell():
                self.fresh()
                self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
                self.ev.unlink()
                r = self.run_wc(shell, *cmd)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertRemoved(cmd)

    REMOVED = ('table ip mu300_wifi_nat\ndelete table ip mu300_wifi_nat\n'
               'table inet mu300_wifi_filter\ndelete table inet mu300_wifi_filter')

    def assertRemoved(self, msg=None):
        """the last word on the sharing rules: both tables gone, in one transaction"""
        rs = self.rulesets()
        self.assertTrue(rs, msg)
        self.assertEqual(rs[-1], self.REMOVED, msg)

    PRIVATE = ('0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, '
               '198.18.0.0/15, 224.0.0.0/3')
    INPUT = [
        '    chain input {',
        '        type filter hook input priority filter; policy accept;',
        '        iifname "wlan0" ct state established,related accept',
        '        iifname "wlan0" udp sport 67 udp dport 68 accept',
        '        iifname "wlan0" icmp type echo-request limit rate 5/second burst 5 packets accept',
        '        iifname "wlan0" icmpv6 type { echo-request, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept',
        '        iifname "wlan0" counter drop',
        '    }']
    NATTABLE = [
        'table ip mu300_wifi_nat {',
        '    chain postrouting {',
        '        type nat hook postrouting priority srcnat; policy accept;',
        '        oifname "wlan0" masquerade',
        '    }',
        '}']

    def expected(self, subnet, nat=True):
        """the rules while shared, every line in order: NAT, input closed, forwarding to the internet only"""
        return '\n'.join([self.REMOVED] + (self.NATTABLE if nat else []) + ['table inet mu300_wifi_filter {'] + self.INPUT + [
            '    chain forward {',
            '        type filter hook forward priority filter; policy accept;',
            '        iifname "wlan0" ct state established,related accept',
            '        iifname "wlan0" counter drop',
            '        oifname "wlan0" ip daddr { %s } counter drop' % self.PRIVATE]
            + (['        oifname "wlan0" ip daddr %s counter drop' % subnet] if subnet else []) + [
            '        oifname "wlan0" meta nfproto ipv6 counter drop',
            '        oifname "wlan0" tcp flags syn tcp option maxseg size set rt mtu',
            '    }',
            '}'])

    def closed(self):
        """the rules while wlan0 may be on the network but is not shared: input closed, nothing forwarded, no NAT"""
        return '\n'.join([self.REMOVED, 'table inet mu300_wifi_filter {'] + self.INPUT + [
            '    chain forward {',
            '        type filter hook forward priority filter; policy accept;',
            '        iifname "wlan0" counter drop',
            '        oifname "wlan0" counter drop',
            '    }',
            '}'])

    def applied(self):
        return ['\n'.join(l for l in x.splitlines() if l.strip()) for x in self.rulesets()]

    def test_exact_rules_through_a_join(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            ev = self.events()
            # stop_client first (nothing left from before), the rules without the subnet before the radio joins,
            # then with it once DHCP has answered; forwarding on only then
            self.assertEqual(self.applied(), [self.REMOVED, self.closed(), self.expected('192.168.2.0/24')])
            first = ev.index('  | table inet mu300_wifi_filter {')
            self.assertLess(first, ev.index('\nwpa_supplicant '))
            self.assertLess(ev.index('\nudhcpc '), ev.rindex('  | table inet mu300_wifi_filter {'))
            self.assertLess(ev.rindex('  | table inet mu300_wifi_filter {'), ev.index('ip_forward=1'))
            # disconnect: both tables gone in one transaction, forwarding off (mobile data is not sharing)
            self.ev.unlink()
            self.run_wc(shell, 'disconnect')
            self.assertRemoved()
            self.assertIn('sysctl -qw net.ipv4.ip_forward=0', self.events())
            # with mobile data sharing its bearer, forwarding stays on
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            (self.tmp / 'mobile').touch()
            self.ev.unlink()
            self.run_wc(shell, 'disconnect')
            self.assertRemoved()
            self.assertNotIn('ip_forward=0', self.events())

    def test_a_failed_join_leaves_nothing(self):
        cases = {'no answer': 'never', 'no address': 'noaddr', 'overlaps the LAN': 'overlap', 'nft fails': 'nftfail'}
        for name, how in cases.items():
            for shell in self.each_shell():
                self.fresh()
                (self.root / 'run/mu300-wifi-client-had-ap').touch()
                self.stub('udhcpc', 'echo "udhcpc $*" >> "$STUBLOG/events"\n' +
                          ('' if how == 'noaddr' else ': > "$STUBLOG/addr"'))
                if how == 'never':
                    (self.tmp / 'never').touch()
                if how == 'overlap':
                    (self.tmp / 'subnet').write_text('192.168.79.0/24')
                if how == 'nftfail':
                    (self.tmp / 'nftfail').touch()
                r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n', MU300_JOIN_WAIT=2)
                self.assertEqual(r.returncode, 1, (name, r.stdout))
                ev = self.events()
                if how == 'nftfail':
                    self.assertNotIn('\nwpa_supplicant ', ev)      # not joining with the interface open
                else:
                    self.assertRemoved(name)
                self.assertNotIn('ip_forward=1', ev, name)
                self.assertFalse((self.tmp / 'supplicant').exists(), name)
                self.assertFalse((self.root / 'run/mu300-wifi-client.active').exists(), name)
                self.assertIn('systemctl start mu300-hotspot', ev, name)
                if how == 'overlap':
                    self.assertIn('overlaps', r.stderr)
        self.stub('udhcpc', 'echo "udhcpc $*" >> "$STUBLOG/events"\n: > "$STUBLOG/addr"')

    def test_a_signal_mid_join_leaves_nothing(self):
        import signal, subprocess, time
        for shell in self.each_shell():
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                self.fresh()
                (self.tmp / 'never').touch()
                env = self.env(MU300_SYSROOT=self.root, MU300_VPN_CMD=self.stubs / 'vpn', MU300_VPN_ORPHAN_LIB=ORPHAN_LIB,
                               MU300_JOIN_WAIT=60)
                self.stub('sleep', 'echo sleep >> "$STUBLOG/sleeps"; exec /bin/sleep 0.2')
                p = subprocess.Popen(shell + [str(WIFI), 'connect', 'KEDI 5G', '-'], env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                     preexec_fn=lambda: [signal.signal(x, signal.SIG_DFL) for x in (signal.SIGINT, signal.SIGHUP)])
                p.stdin.write(b'password1\n'); p.stdin.close()
                t = time.time()
                while not (self.tmp / 'polls').exists() and time.time() - t < 20:
                    time.sleep(0.1)
                time.sleep(0.5)
                p.send_signal(sig)
                p.wait(timeout=20)
                p.stdout.close(); p.stderr.close()
                self.assertNotEqual(p.returncode, 0)
                self.assertRemoved(sig.name)
                self.assertFalse((self.tmp / 'supplicant').exists(), sig.name)
            self.stub('sleep', 'echo sleep >> "$STUBLOG/sleeps"')

    def test_the_lan_subnet_comes_from_configuration(self):
        # no lan.conf: the device's default (mu300-lan-ip); neither: no join, since the overlap cannot be checked
        lanip = self.tmp / 'bin'
        lanip.mkdir()
        (lanip / 'mu300-lan-ip').write_text('#!/bin/sh\necho 192.168.2.1\n')
        (lanip / 'mu300-lan-ip').chmod(0o755)
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'etc/mu300/lan.conf').unlink()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n', MU300_BIN=lanip)
            self.assertEqual(r.returncode, 1)
            self.assertIn('overlaps', r.stderr)
            self.fresh()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n', MU300_BIN=self.tmp / 'nothing')
            self.assertEqual(r.returncode, 1)
            self.assertIn('LAN subnet', r.stderr)
            self.assertRemoved()
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.79.1\n')

    def test_leaving_takes_the_link_down_before_the_rules(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.ev.unlink()
            self.run_wc(shell, 'disconnect')
            ev = self.events()
            self.assertLess(ev.index('pkill -f ^wpa_supplicant'), ev.index('  | delete table inet mu300_wifi_filter'))

    def test_hostile_names_in_messages(self):
        for shell in self.each_shell():
            self.fresh()
            # valid as a name (no control bytes), still shown escaped: bidi, zero-width, soft hyphen, variation
            # selector, a C1 control encoded in UTF-8, a backslash
            bad = 'x‮Y​­️\u0085Z\\x1b'
            r = self.run_wc(shell, 'connect', bad, '-', stdin='password1\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('x\\xe2\\x80\\xaeY\\xe2\\x80\\x8b\\xc2\\xad\\xef\\xb8\\x8f\\xc2\\x85Z\\x5cx1b', r.stdout)
            self.assertHostileFree(r.stdout + r.stderr)
            self.assertHostileFree(self.run_wc(shell, 'status').stdout)
            self.run_wc(shell, 'disconnect')
            r = self.run_wc(shell, 'up')
            self.assertHostileFree(r.stdout + r.stderr)
            self.assertIn('\\xe2\\x80\\xae', r.stdout)

    # one validator for every way in: the same hostile values through argument, stdin, file, prompt-less stdin
    # and the saved file, in bytes whatever the shell
    HOSTILE_SSIDS = ['a\nPSK=injected', 'tab\there', 'esc\x1b[31m', 'cr\rx', 'del\x7f', 'x' * 33, 'é' * 17, '']
    HOSTILE_PSKS = ['abcdefgh\nENABLE=1', 'pass\tword1', 'esc\x1b[2Jxyz', 'short', 'ç' * 3 + 'a', 'x' * 64,
                    'é' * 32]
    GOOD_PSKS = ['çççç', 'é' * 31 + 'a', 'pass word with spaces', '\\backslash\\', '"quoted" \'too\'']

    def ways_in(self, shell, ssid, psk):
        f = self.tmp / 'pwfile'
        f.write_bytes(psk.encode() + b'\n')
        yield 'argument', self.run_wc(shell, 'connect', ssid, psk)
        if '\n' not in psk:
            yield 'stdin', self.run_wc(shell, 'connect', ssid, '-', stdin=psk + '\n')
            yield 'file', self.run_wc(shell, 'connect', ssid, '--password-file', f)

    def test_one_validator_for_every_way_in(self):
        for shell in self.each_shell():
            for ssid in self.HOSTILE_SSIDS:
                self.fresh()
                for how, r in self.ways_in(shell, ssid, 'password1'):
                    self.assertEqual(r.returncode, 1, (how, repr(ssid)))
                    self.assertIn('network name' if ssid else 'usage', r.stderr)
                    self.assertNotIn('pkill', self.events(), how)   # nothing touched
                    self.assertFalse(self.conf.exists(), (how, repr(ssid)))
            for psk in self.HOSTILE_PSKS:
                self.fresh()
                for how, r in self.ways_in(shell, 'KEDI 5G', psk):
                    if how != 'argument' and '\n' in psk:
                        continue
                    self.assertEqual(r.returncode, 1, (how, repr(psk)))
                    self.assertIn('passphrase', r.stderr)
                    self.assertFalse(self.conf.exists(), (how, repr(psk)))
            for psk in self.GOOD_PSKS:
                for how, r in self.ways_in(shell, 'Kafe Ç', psk):
                    self.assertEqual(r.returncode, 0, (how, repr(psk), r.stderr))
                    self.assertEqual(self.saved()['PSK'], psk, how)
                    self.assertEqual(self.saved()['SSID'], 'Kafe Ç', how)
            # a hand-edited saved file goes through the same check at boot
            for line in ('SSID=esc\x1b[31m\nPSK=password1\nENABLE=1\n', 'SSID=KEDI 5G\nPSK=sh\nENABLE=1\n'):
                self.fresh()
                self.conf.write_text(line)
                r = self.run_wc(shell, 'up')
                self.assertEqual(r.returncode, 1, repr(line))
                self.assertHostileFree(r.stdout + r.stderr)
                self.assertNotIn('\nwpa_supplicant ', self.events())

    def test_leaving_never_takes_the_interface_down(self):
        # "ip link set wlan0 down" powers the WCN chip off on the F50, and the radio is dead until a reboot
        for shell in self.each_shell():
            for iftype in ('managed', 'AP'):
                self.fresh()
                (self.tmp / 'iftype').write_text(iftype)
                self.stub('ip', 'echo "ip $*" >> "$STUBLOG/events"; exit 0')
                self.run_wc(shell, 'disconnect')
                self.run_wc(shell, 'forget')
                self.assertNotIn('ip link set wlan0 down', self.events())
            (self.tmp / 'iftype').unlink()
        self.setUp()

    def test_leaving_the_hotspot_alone(self):
        # disconnect while the radio is the hotspot: the interface is hostapd's and stays up
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'iftype').write_text('AP')
            self.stub('ip', 'echo "ip $*" >> "$STUBLOG/events"; exit 0')
            self.run_wc(shell, 'disconnect')
            self.assertNotIn('ip link set wlan0 down', self.events())
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertNotIn('ip link set wlan0 down', self.events())
            (self.tmp / 'iftype').unlink()
        self.setUp()

    def test_overlap_with_the_live_lan_too(self):
        # lan.conf already says another subnet, br-lan still has the old one that the network overlaps
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=10.9.9.1\n')
            (self.tmp / 'subnet').write_text('192.168.79.0/24')
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 1)
            self.assertIn('overlaps', r.stderr)
            self.assertRemoved()
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.79.1\n')

    def test_openwrt_puts_the_client_in_the_wan_zone(self):
        (self.root / 'etc/openwrt_release').write_text("DISTRIB_ID='OpenWrt'\n")
        (self.root / 'etc/init.d').mkdir()
        fw = self.root / 'etc/init.d/firewall'
        fw.write_text('#!/bin/sh\necho "firewall $*" >> "$STUBLOG/events"\n')
        fw.chmod(0o755)
        self.stub('uci', 'echo "uci $*" >> "$STUBLOG/events"\n'
                         'case "$*" in\n'
                         '  "-q show firewall") printf "firewall.@zone[0].name=\'lan\'\\nfirewall.@zone[1].name=\'wan\'\\n"\n'
                         '      [ -e "$STUBLOG/inzone" ] && echo "firewall.@rule[9].name=\'mu300-wifi-client-private\'" ;;\n'
                         '  "-q get firewall.@zone[1].device") [ -e "$STUBLOG/inzone" ] && echo "sbtun wlan0" || echo sbtun ;;\n'
                         '  "add firewall rule") echo cfg0a ;;\n'
                         '  "-q get network.lan.ipaddr") echo 192.168.1.1 ;;\n'
                         'esac\nexit 0')
        self.stub('wifi', 'exit 0')
        for shell in self.each_shell():
            for inzone in (False, True):
                self.fresh()
                (self.tmp / 'inzone').unlink(missing_ok=True)
                if inzone:
                    (self.tmp / 'inzone').touch()
                r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
                self.assertEqual(r.returncode, 0, r.stderr)
                ev = self.events()
                if inzone:
                    self.assertNotIn('add_list', ev)
                    self.assertNotIn('commit firewall', ev)
                else:
                    self.assertIn('uci add_list firewall.@zone[1].device=wlan0', ev)
                    # fw4 forwards lan to wan to any address: LAN clients kept off the other network's private ones
                    for want in ('uci set firewall.cfg0a.src=lan', 'uci set firewall.cfg0a.dest=wan',
                                 'uci set firewall.cfg0a.device=wlan0', 'uci set firewall.cfg0a.direction=out',
                                 'uci set firewall.cfg0a.target=REJECT',
                                 'uci add_list firewall.cfg0a.dest_ip=192.168.0.0/16',
                                 'uci add_list firewall.cfg0a.dest_ip=10.0.0.0/8'):
                        self.assertIn(want, ev)
                    self.assertLess(ev.index('uci commit firewall'), ev.index('firewall reload'))
                # fw4 does the NAT and the forwarding there; the filter is the same as on Ubuntu (IPv6, the subnet)
                self.assertNotIn('table ip mu300_wifi_nat {', ev)
                self.assertNotIn('sysctl', ev)
                self.assertEqual(self.applied(), [self.REMOVED, self.closed(), self.expected('192.168.2.0/24', nat=False)])
                self.ev.unlink()
                self.run_wc(shell, 'disconnect')
                self.assertRemoved()

    # ---- the link lost and back: the supplicant's events (wpa_cli -a) -----------------------------------------
    def joined(self, shell):
        self.fresh()
        r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.ev.unlink()
        # wpa_cli -a's pid, as it writes it: the handler runs as its child (here: as this process's)
        (self.root / 'run/mu300-wifi-client.events.pid').write_text(f'{os.getpid()}\n')

    def test_the_link_is_watched_once_joined(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            ev = self.events()
            # wpa_cli calls this script back, by its full path, only once the join is complete and shared
            self.assertIn(f'wpa_cli -a {WIFI} -B -P {self.root}/run/mu300-wifi-client.events.pid', ev)
            self.assertLess(ev.rindex('  | table inet mu300_wifi_filter {'), ev.index('wpa_cli -a'))
            # and it is the first thing to go when leaving: the supplicant's going is not a lost link to handle
            self.ev.unlink()
            self.run_wc(shell, 'disconnect')
            ev = self.events()
            self.assertLess(ev.index('pkill -f ^wpa_cli -i wlan0 -a '), ev.index('wpa_cli terminate'))

    def test_a_lost_link_closes_the_sharing(self):
        for shell in self.each_shell():
            self.joined(shell)
            r = self.run_wc(shell, 'wlan0', 'DISCONNECTED')
            self.assertEqual(r.returncode, 0, r.stderr)
            # closed in one transaction (no NAT, nothing forwarded, input as before), then the lease and its route
            # gone, forwarding off (mobile data is not sharing)
            self.assertEqual(self.applied(), [self.closed()])
            ev = self.events()
            self.assertLess(ev.index('nft -f'), ev.index('ip route flush dev wlan0'))
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertIn('sysctl -qw net.ipv4.ip_forward=0', ev)
            self.assertTrue((self.root / 'run/mu300-wifi-client.active').exists())   # still the client's radio

    def test_a_link_back_is_checked_and_shared_again(self):
        for shell in self.each_shell():
            # the same network, another subnet now (another access point of it, a router that was reset)
            self.joined(shell)
            self.run_wc(shell, 'wlan0', 'DISCONNECTED')
            self.ev.unlink()
            (self.tmp / 'subnet').write_text('203.0.113.0/24')
            r = self.run_wc(shell, 'wlan0', 'CONNECTED')
            self.assertEqual(r.returncode, 0, r.stderr)
            ev = self.events()
            # closed while DHCP runs, shared with the new subnet only after it answered, forwarding on only then
            self.assertEqual(self.applied(), [self.closed(), self.expected('203.0.113.0/24')])
            self.assertLess(ev.index('udhcpc '), ev.rindex('nft -f'))
            self.assertLess(ev.rindex('nft -f'), ev.index('ip_forward=1'))
            # a network that now overlaps the LAN: not shared, no address kept
            self.ev.unlink()
            (self.tmp / 'subnet').write_text('192.168.79.0/24')
            r = self.run_wc(shell, 'wlan0', 'CONNECTED')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.applied(), [self.closed()])
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertNotIn('ip_forward=1', self.events())
            # no address at all: the same
            self.ev.unlink()
            (self.tmp / 'subnet').unlink()
            self.stub('udhcpc', 'echo "udhcpc $*" >> "$STUBLOG/events"')
            r = self.run_wc(shell, 'wlan0', 'CONNECTED')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.applied(), [self.closed()])
            self.stub('udhcpc', 'echo "udhcpc $*" >> "$STUBLOG/events"\n: > "$STUBLOG/addr"')

    def test_events_after_leaving_change_nothing(self):
        # an event handler that was already on its way when the client left: no rules come back
        for shell in self.each_shell():
            self.joined(shell)
            self.run_wc(shell, 'disconnect')
            self.ev.unlink()
            for e in ('CONNECTED', 'DISCONNECTED', 'TERMINATING'):
                self.assertEqual(self.run_wc(shell, 'wlan0', e).returncode, 0)
            self.assertEqual(self.events(), '')

    def test_a_stale_event_handler_changes_nothing(self):
        # a handler of the session before (it waited for the lock while the client left and joined again): its
        # wpa_cli is not this session's, so it leaves the new session alone
        for shell in self.each_shell():
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.events.pid').write_text('1\n')
            for e in ('CONNECTED', 'DISCONNECTED'):
                self.assertEqual(self.run_wc(shell, 'wlan0', e).returncode, 0)
            self.assertEqual(self.events(), '')
            self.assertTrue((self.tmp / 'addr').exists())

    # ---- DHCP: the lease renewed, and a new address checked before it is shared ------------------------------
    def lease(self, shell, action, ip='192.168.2.248', **extra):
        env = dict(interface='wlan0', ip=ip, mask='24', router='192.168.2.1')
        env.update(extra)
        return self.run_wc(shell, action, **env)

    def test_dhcp_keeps_renewing(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            udhcpc = [l for l in self.events().splitlines() if l.startswith('udhcpc ')]
            self.assertEqual(len(udhcpc), 1)
            self.assertNotIn(' -q', udhcpc[0])     # it stays, to renew the lease
            self.assertIn(' -n ', udhcpc[0])

    def test_a_renewal_with_the_same_address_changes_nothing(self):
        for shell in self.each_shell():
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            r = self.lease(shell, 'renew')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.events(), '')

    def test_a_new_address_is_checked_before_it_is_shared(self):
        for shell in self.each_shell():
            # another public subnet: closed, the address applied, then shared with the new subnet
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            (self.tmp / 'subnet').write_text('203.0.113.0/24')
            r = self.lease(shell, 'bound', ip='203.0.113.7')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.applied(), [self.closed(), self.expected('203.0.113.0/24')])
            self.assertTrue((self.tmp / 'addr').exists())
            self.assertEqual((self.root / 'run/mu300-wifi-client.dhcp').read_text(), '203.0.113.7/24\n')
            # one on the LAN's subnet: closed, not shared, no address
            self.ev.unlink()
            (self.tmp / 'subnet').write_text('192.168.79.0/24')
            r = self.lease(shell, 'bound', ip='192.168.79.9')
            self.assertEqual(self.applied(), [self.closed()])
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertIn('ip_forward=0', self.events())
            # the lease lost: closed, no address, forwarding off
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            r = self.lease(shell, 'deconfig')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.applied(), [self.closed()])
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertIn('ip_forward=0', self.events())
            # the first lease of a join (dhcp_run marks it) is applied only: connect (or the event handler) checks it
            self.fresh()
            (self.root / 'run/mu300-wifi-client.dhcp.first').touch()
            r = self.lease(shell, 'bound')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.rulesets(), [])
            self.assertTrue((self.tmp / 'addr').exists())
            self.assertFalse((self.root / 'run/mu300-wifi-client.dhcp.first').exists())
            # and outside a session nothing at all happens to a renewal
            (self.tmp / 'addr').unlink()
            self.assertEqual(self.lease(shell, 'bound').returncode, 0)
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertEqual(self.lease(shell, 'deconfig').returncode, 0)
            self.assertEqual(self.rulesets(), [])

    # ---- daemons do not hold the caller's descriptors -----------------------------------------------------
    def test_daemons_get_no_descriptor_of_the_caller(self):
        # the toolkit reads wifi-client's output and status through pipes on fds 3 and 4: a daemon that kept one
        # would hold the menu until it exits
        probe = '\nfor fd in 3 4 5 6 7 8 9; do { true >&$fd; } 2>/dev/null && echo "$(basename "$0") has fd $fd" >> "$STUBLOG/events"; done'
        log = 'echo "$(basename "$0") $*" >> "$STUBLOG/events"'
        wrapper = self.tmp / 'withfds.sh'
        wrapper.write_text('exec 3>/dev/null 4>/dev/null 5>/dev/null 6>/dev/null 7>/dev/null 8>/dev/null\n"$@"\n')
        for shell in self.each_shell():
            self.fresh()
            self.stub('wpa_supplicant', log + probe + '\n: > "$STUBLOG/supplicant"')
            self.stub('udhcpc', log + probe + '\n: > "$STUBLOG/addr"')
            self.stub('wpa_cli', (self.stubs / 'wpa_cli').read_text().split('\n', 1)[1].replace(
                '  -a) echo', '  -a) ' + probe.strip().replace('\n', ' ') + '; echo'))
            r = self.script(shell, wrapper, *shell, WIFI, 'connect', 'KEDI 5G', '-', stdin='password1\n',
                            MU300_SYSROOT=self.root, MU300_VPN_CMD=self.stubs / 'vpn', MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)
            self.assertEqual(r.returncode, 0, r.stderr)
            ev = self.events()
            self.assertIn('wpa_cli -a', ev)
            self.assertNotIn('has fd', ev)
        self.setUp()

    def test_names_that_look_like_others(self):
        for shell in self.each_shell():
            self.fresh()
            for name, want in ((' Home', '\\x20Home'), ('Home ', 'Home\\x20'), ('Ho me', 'Ho me'),
                               ('Ogham space', 'Ogham\\xe1\\x9a\\x80space')):
                self.assertEqual(self.run_wc(shell, 'shown-name', name).stdout, want + '\n', repr(name))

    def test_a_supplicant_that_will_not_go_keeps_the_rules(self):
        # the supplicant stays whatever it is sent, or it goes and the radio stays associated all the same
        for cmd, how in ((['disconnect'], 'stuck'), (['forget'], 'stuck'), (['connect', 'cafe', '--open'], 'stuck'),
                         (['disconnect'], 'assoc'), (['connect', 'cafe', '--open'], 'assoc')):
            for shell in self.each_shell():
                self.joined(shell)
                (self.tmp / how).touch()
                r = self.run_wc(shell, *cmd)
                self.assertEqual(r.returncode, 1, (cmd, r.stdout))
                self.assertIn('still on the network', r.stderr)
                ev = self.events()
                # asked to go, then killed, then the radio told to leave: none of it worked, so the rules stay,
                # closed (not removed), and nothing else is started on the radio
                self.assertIn('wpa_cli terminate', ev)
                if how == 'stuck':
                    self.assertIn('pkill -9 -f ^wpa_supplicant -B -i wlan0 ', ev)
                else:
                    self.assertIn('iw dev wlan0 disconnect', ev)
                self.assertEqual(self.applied(), [self.closed()], cmd)
                self.assertNotIn('systemctl start mu300-hotspot', ev)
                self.assertNotIn('\nwpa_supplicant ', ev)

    def test_the_rules_go_only_once_the_radio_has_left(self):
        for shell in self.each_shell():
            self.joined(shell)
            self.run_wc(shell, 'disconnect')
            ev = self.events()
            self.assertLess(ev.index('wpa_cli terminate'), ev.index('pkill -f ^wpa_supplicant'))
            self.assertLess(ev.index('pkill -f ^wpa_supplicant'), ev.index('ip route flush dev wlan0'))
            self.assertLess(ev.index('ip route flush dev wlan0'), ev.index('  | delete table inet mu300_wifi_filter'))
            self.assertRemoved()

    # ---- the ruleset nft shows is the truth: any doubt ends closed ---------------------------------------------
    def filt(self):
        f = self.tmp / 'filter'
        return f.read_text() if f.exists() else None

    def test_nft_that_says_yes_but_changes_nothing(self):
        for shell in self.each_shell():
            # before the radio: no closed table to be seen, so no join at all
            self.fresh()
            (self.tmp / 'nftlie').touch()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 1)
            self.assertIn('firewall', r.stderr)
            self.assertNotIn('\nwpa_supplicant ', self.events())
            self.assertNotIn('ip_forward=1', self.events())
            # once joined, a lease change whose sharing nft does not take: closed stays (the table nft shows)
            self.joined(shell)
            before = self.filt()
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            (self.tmp / 'subnet').write_text('203.0.113.0/24')
            (self.tmp / 'nftfailshare').touch()
            r = self.lease(shell, 'bound', ip='203.0.113.7')
            self.assertEqual(r.returncode, 1)
            self.assertNotEqual(self.filt(), before)
            self.assertIn('oifname "wlan0" counter packets 0 bytes 0 drop', self.filt())   # closed: nothing out
            self.assertNotIn('ip daddr {', self.filt())
            self.assertNotIn('ip_forward=1', self.events())
            self.assertIn('closed (not shared)', self.run_wc(shell, 'status').stdout)
            # a lying nft on a lost link: off the network
            self.joined(shell)
            (self.tmp / 'nftlie').touch()
            (self.tmp / 'filter').unlink()
            self.run_wc(shell, 'wlan0', 'DISCONNECTED')
            self.assertFalse((self.tmp / 'supplicant').exists())

    def test_a_join_whose_sharing_fails_ends_with_nothing(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'run/mu300-wifi-client-had-ap').touch()
            (self.tmp / 'nftfailshare').touch()
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 1)
            self.assertIsNone(self.filt())
            self.assertRemoved()
            self.assertNotIn('ip_forward=1', self.events())
            self.assertFalse((self.tmp / 'supplicant').exists())
            self.assertFalse((self.tmp / 'addr').exists())

    def test_a_ruleset_changed_behind_its_back_is_not_taken_for_its_own(self):
        tamper = {
            'an accept before the drops': lambda t: t.replace('\t\tiifname "wlan0" counter packets 0 bytes 0 drop\n\t\toifname',
                                                              '\t\taccept\n\t\tiifname "wlan0" counter packets 0 bytes 0 drop\n\t\toifname'),
            'the IPv6 drop gone': lambda t: t.replace('\t\toifname "wlan0" meta nfproto ipv6 counter packets 0 bytes 0 drop\n', ''),
            'a private range gone': lambda t: t.replace(' 10.0.0.0/8,', ''),
            'the input drop gone': lambda t: t.replace('\t\tiifname "wlan0" counter packets 0 bytes 0 drop\n\t}\n\n', '\t}\n\n'),
        }
        for shell in self.each_shell():
            for name, f in tamper.items():
                self.joined(shell)
                p = self.tmp / 'filter'
                t = p.read_text()
                self.assertNotEqual(f(t), t, name)
                p.write_text(f(t))
                st = self.run_wc(shell, 'status').stdout
                self.assertIn('is not as wifi-client puts it', st, name)
                self.assertNotIn('shared with the LAN', st, name)
                # the next transaction replaces it whole, and nft's listing confirms the state again
                (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
                (self.tmp / 'subnet').write_text('203.0.113.0/24')
                r = self.lease(shell, 'bound', ip='203.0.113.7')
                self.assertEqual(r.returncode, 0, (name, r.stderr))
                self.assertIn('shared with the LAN', self.run_wc(shell, 'status').stdout, name)
                (self.tmp / 'subnet').unlink()
            # nft that cannot list at all: not "none", not "shared"
            self.joined(shell)
            (self.tmp / 'nftlisterr').touch()
            self.assertIn('is not as wifi-client puts it', self.run_wc(shell, 'status').stdout)
            # a table left by a client that is gone is reported
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.active').unlink()
            self.assertIn('left over from a client that is gone', self.run_wc(shell, 'status').stdout)

    def test_a_lease_after_a_lost_one_is_checked(self):
        # the lease lost and a new one later on the same link (no link event): checked, not just applied
        for shell in self.each_shell():
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            self.lease(shell, 'deconfig')
            self.ev.unlink()
            (self.tmp / 'subnet').write_text('192.168.79.0/24')
            r = self.lease(shell, 'bound', ip='192.168.79.9')
            self.assertEqual(self.applied(), [self.closed()])
            self.assertFalse((self.tmp / 'addr').exists())
            self.ev.unlink()
            (self.tmp / 'subnet').write_text('203.0.113.0/24')
            r = self.lease(shell, 'bound', ip='203.0.113.7')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.applied(), [self.closed(), self.expected('203.0.113.0/24')])

    def test_tables_flushed_under_a_running_client_show_as_open(self):
        for shell in self.each_shell():
            self.joined(shell)
            (self.tmp / 'filter').unlink()
            self.assertIn('firewall    OPEN', self.run_wc(shell, 'status').stdout)

    def test_nft_failing_on_a_lost_link_or_lease_still_takes_the_address(self):
        for shell in self.each_shell():
            self.joined(shell)
            (self.tmp / 'nftfail').touch()
            r = self.run_wc(shell, 'wlan0', 'DISCONNECTED')
            self.assertFalse((self.tmp / 'addr').exists())
            # and the radio leaves the network: unconfirmed means not on it
            self.assertFalse((self.tmp / 'supplicant').exists())
            self.assertIn('ip route flush dev wlan0', self.events())
            self.assertIn('ip_forward=0', self.events())
            self.joined(shell)
            (self.root / 'run/mu300-wifi-client.dhcp').write_text('192.168.2.248/24\n')
            (self.tmp / 'nftfail').touch()
            r = self.lease(shell, 'bound', ip='203.0.113.7')
            self.assertEqual(r.returncode, 1)
            self.assertFalse((self.tmp / 'addr').exists())
            self.assertFalse((self.tmp / 'supplicant').exists())
            self.assertFalse((self.root / 'run/mu300-wifi-client.dhcp').exists())
            self.assertIn('ip_forward=0', self.events())

    def test_service_stop_releases_everything_and_changes_no_setting(self):
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-wifi-client.service').read_text()
        self.assertIn('ExecStop=/opt/mu300/bin/wifi-client release', unit)
        for shell in self.each_shell():
            self.joined(shell)
            r = self.run_wc(shell, 'release')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRemoved()
            self.assertIsNone(self.filt())
            self.assertFalse((self.tmp / 'supplicant').exists())
            self.assertNotIn('mu300-hotspot', self.events())
            self.assertEqual(self.saved()['ENABLE'], '1')
            # no client at all (the hotspot): nothing is touched, forwarding included
            self.fresh()
            r = self.run_wc(shell, 'release')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.events(), '')

    def test_hostile_configuration_and_paths_are_shown_as_text(self):
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=10.1.2.3\x1b[31m‮\n')
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n', MU300_BIN=self.tmp / 'nothing')
            self.assertEqual(r.returncode, 1)
            self.assertIn('LAN subnet', r.stderr)
            self.assertHostileFree(r.stdout + r.stderr)
            self.assertNotIn('\x1b', self.events())
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.79.1\n')
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '--password-file', self.tmp / 'no\x1b]0;x\x07such‮')
            self.assertEqual(r.returncode, 1)
            self.assertHostileFree(r.stderr)
            self.assertIn('\\x1b', r.stderr)

    # ---- the VPN: its kill switch is never bypassed ----------------------------------------------------------
    def test_the_vpn_kill_switch_comes_first_and_is_never_overridden(self):
        # (the kill switch's own forward chain, filter - 5 against these at filter, is the VPN module's: its tests,
        # in dikeckaan/mu300-linux-vpn, hold it to that)
        for shell in self.each_shell():
            self.fresh()
            (self.root / 'etc/mu300/vpn.conf').write_text('ENABLE=1\nKILL_SWITCH=1\n')
            r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            ev = self.events()
            self.assertLess(ev.index('vpn guard'), ev.index('\nwpa_supplicant '))
            for rs in self.applied():
                # no chain of these before the kill switch, none that drops by policy, nothing new let through
                # towards wlan0, and the VPN's own tables and routing never touched
                self.assertNotIn('priority filter -', rs)
                self.assertNotRegex(rs, r'priority -?\d')
                self.assertNotIn('policy drop', rs)
                self.assertFalse([l for l in rs.splitlines() if 'oifname "wlan0"' in l and l.strip().endswith('accept')])
                self.assertNotIn('mu300_vpn', rs)
            self.assertNotIn('ip rule', ev)

    def test_without_the_vpn_module_the_kill_switch_fails_closed(self):
        # the VPN is a module of its own: without it there is no mu300-vpn to put the kill switch up. With the VPN
        # on and its kill switch on, nothing joins or is shared (fail closed); otherwise the join goes on
        for shell in self.each_shell():
            for conf, joins in (('ENABLE=0\nKILL_SWITCH=1\n', True), ('ENABLE=1\nKILL_SWITCH=0\n', True),
                                ('ENABLE=1\nKILL_SWITCH=1\n', False), ('ENABLE="1"\n', False)):
                self.fresh()
                (self.root / 'etc/mu300/vpn.conf').write_text(conf)
                r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n',
                                MU300_VPN_CMD=self.tmp / 'no-such-mu300-vpn')
                ev = self.events()
                self.assertNotIn('vpn guard', ev)
                if joins:
                    self.assertEqual(r.returncode, 0, (conf, r.stderr))
                    self.assertIn('\nwpa_supplicant ', ev)
                else:
                    self.assertNotEqual(r.returncode, 0, conf)
                    self.assertIn("the VPN's kill switch is on but the VPN module is missing", r.stderr)
                    self.assertIn('mu300-extra install vpn', r.stderr)
                    self.assertNotIn('wpa_supplicant ', ev, conf)
                    self.assertNotIn('ip_forward=1', ev, conf)

    def test_the_kill_switch_setting_comes_from_the_profile_store_first(self):
        # KILL_SWITCH moved to etc/mu300/vpn/settings (single-quoted or bare); vpn.conf's line is the legacy one
        # and counts only when the settings file has none. ENABLE stays in vpn.conf. A guard failure is fatal only
        # with the kill switch on, so the setting decides whether a broken mu300-vpn stops the join.
        settings = self.root / 'etc/mu300/vpn/settings'
        settings.parent.mkdir(exist_ok=True)
        cases = [  # (settings, vpn.conf, fatal)
            ("KILL_SWITCH='0'\n", 'ENABLE=1\nKILL_SWITCH=1\n', False),
            ('KILL_SWITCH=0\n', 'ENABLE=1\nKILL_SWITCH=1\n', False),
            ("KILL_SWITCH='1'\n", 'ENABLE=1\nKILL_SWITCH=0\n', True),
            ("REMOTE_DNS='9.9.9.9'\n", 'ENABLE=1\nKILL_SWITCH=0\n', False),   # no KILL_SWITCH there: vpn.conf's
            ("REMOTE_DNS='9.9.9.9'\n", 'ENABLE=1\nKILL_SWITCH=1\n', True),
            ("KILL_SWITCH='1'\n", 'ENABLE=0\nKILL_SWITCH=1\n', False),        # the switch is still vpn.conf's
        ]
        try:
            for shell in self.each_shell():
                for stored, conf, fatal in cases:
                    self.fresh()
                    self.stub('vpn', 'exit 1')
                    settings.write_text(stored)
                    (self.root / 'etc/mu300/vpn.conf').write_text(conf)
                    r = self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
                    self.assertEqual(r.returncode, 1 if fatal else 0, (stored, conf, r.stderr))
                    if fatal:
                        self.assertIn('kill switch', r.stderr)
                        self.assertNotIn('\nwpa_supplicant', self.events())
                    else:
                        self.assertIn('\nwpa_supplicant', self.events())
        finally:
            settings.unlink(missing_ok=True)
            self.stub('vpn', 'echo "$(basename "$0") $*" >> "$STUBLOG/events"')

    # ---- one validator, and every name printed as text ---------------------------------------------------------
    def test_the_toolkit_asks_wifi_client(self):
        for shell in self.each_shell():
            self.fresh()
            for name in self.HOSTILE_SSIDS:
                self.assertEqual(self.run_wc(shell, 'valid-name', name).returncode, 1, repr(name))
            for name in ('KEDI 5G', 'Kafe Ç', 'x' * 32, 'é' * 16, 'a\\b'):
                self.assertEqual(self.run_wc(shell, 'valid-name', name).returncode, 0, repr(name))
            r = self.run_wc(shell, 'shown-name', 'x‮Y​Z\\x1b')
            self.assertEqual(r.stdout, 'x\\xe2\\x80\\xaeY\\xe2\\x80\\x8bZ\\x5cx1b\n')
        toolkit = (BIN / 'mu300-toolkit').read_text()
        self.assertIn('wifi-client valid-name "$ssid"', toolkit)
        self.assertIn('wifi-client shown-name "$ssid"', toolkit)
        # the exit status is wifi-client's (3: saved, reboot to join), not that of the sed after it
        self.assertIn('echo "$?" >&3', toolkit)

    def test_the_toolkit_reports_wifi_clients_exit_status(self):
        # the toolkit's own pipeline, with a stand-in for wifi-client that says 3
        body = re.search(r"\{ rc=\$\(.*?\} 4>&1", (BIN / 'mu300-toolkit').read_text(), re.S).group(0)
        body = body.replace('/opt/mu300/bin/wifi-client', 'fake')
        for shell in self.each_shell():
            script = self.tmp / 'rc.sh'
            script.write_text('fake() { cat >/dev/null; echo "saved"; return 3; }\npass=x; ssid=y\n' + body +
                              '\necho "rc=$rc"\n')
            r = self.script(shell, script)
            self.assertEqual(r.stdout, '  saved\nrc=3\n', r.stderr)

    def test_scan_failure_shown_as_text(self):
        for shell in self.each_shell():
            self.fresh()
            self.stub('iw', 'case "$*" in\n'
                            '  "dev wlan0 info") printf "\\ttype managed\\n" ;;\n'
                            '  "dev wlan0 scan") printf "busy \\033]0;x\\007\\n" ;;\n'
                            'esac')
            r = self.run_wc(shell, 'scan')
            self.assertEqual(r.returncode, 1)
            self.assertIn('busy \\x1b]0;x\\x07', r.stderr)
            self.assertHostileFree(r.stderr)
        self.setUp()

    # ---- D5: disconnect means no client at the next boot either ----------------------------------------------
    def test_disconnect_turns_off_the_boot_join(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            self.assertEqual(self.saved()['ENABLE'], '1')
            r = self.run_wc(shell, 'disconnect')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.saved(), {'SSID': 'KEDI 5G', 'PSK': 'password1', 'ENABLE': '0', 'METRIC': '50'})
            self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
            self.assertIn('systemctl start mu300-hotspot', self.events())
            # the boot service: nothing joined, so the hotspot comes up
            self.ev.unlink()
            r = self.run_wc(shell, 'up')
            self.assertEqual(r.returncode, 0)
            self.assertNotIn('wpa_supplicant', self.events())
            self.assertIn('wifi-client reconnect', r.stdout)
            self.assertIn('not joined at boot', self.run_wc(shell, 'status').stdout)
            # reconnect turns it back on
            r = self.run_wc(shell, 'reconnect')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.saved()['ENABLE'], '1')
            self.assertIn('wpa_supplicant', self.events())

    def test_disconnect_keep(self):
        for shell in self.each_shell():
            self.fresh()
            self.run_wc(shell, 'connect', 'KEDI 5G', '-', stdin='password1\n')
            r = self.run_wc(shell, 'disconnect', '--keep')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.saved()['ENABLE'], '1')
            self.assertIn('joined at boot', self.run_wc(shell, 'status').stdout)

    def test_disconnect_with_nothing_saved(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.run_wc(shell, 'disconnect')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(self.conf.exists())

    def test_openwrt_service_stop_keeps_the_boot_join(self):
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-wifi-client').read_text()
        self.assertIn('wifi-client disconnect --keep', init)
        toolkit = (BIN / 'mu300-toolkit').read_text()
        self.assertIn('wifi-client reconnect', toolkit)
        self.assertIn('cut -c19-', toolkit)
        # the menu hands the passphrase over on stdin, not as an argument
        self.assertRegex(toolkit, r"printf '%s\\n' \"\$pass\" \| /opt/mu300/bin/wifi-client connect \"\$ssid\" -")


if __name__ == '__main__':
    import unittest
    unittest.main()
