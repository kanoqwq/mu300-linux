"""mu300-api (FINDINGS 110): the one device-side entry point of the web installer's device manager. A method from a
fixed list, a JSON object on stdin, every field checked before anything runs, the device's own tools called with
values as one argument that cannot be an option or on their stdin, one JSON object back; secrets (the hotspot's and
the APN's passwords, VPN links and URLs, the login password, an adb key's owner) never in a reply, an argument list
or a file other than the one they belong in. Every tool is a stub that records its arguments (one line per call,
fields separated by \\x1f) and its stdin; uci is a small stand-in that keeps its settings in a JSON file. Under each
shell, and with busybox's awk where busybox is there."""
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

API = BIN / 'mu300-api'
LIB = TOP / 'rootfs/overlay/opt/mu300/lib'
HAVE_BUSYBOX = bool(shutil.which('busybox'))

# a recording tool: its name and arguments as one line, its stdin kept when the test asks (STUB_STDIN), then the
# output the test put in $STUBLOG/out/<name> (or nothing), and the status in $STUBLOG/rc/<name> (or 0)
RECORD = r'''#!/bin/sh
n=${0##*/}
{ printf '%s' "$n"; for a in "$@"; do printf '\037%s' "$a"; done; printf '\n'; } >> "$STUBLOG/calls"
case " ${STUB_STDIN:-} " in *" $n "*) cat > "$STUBLOG/stdin.$n" ;; esac
[ -f "$STUBLOG/out/$n" ] && cat "$STUBLOG/out/$n"
[ -f "$STUBLOG/err/$n" ] && cat "$STUBLOG/err/$n" >&2
exit "$(cat "$STUBLOG/rc/$n" 2>/dev/null || echo 0)"
'''

# uci: get, show, batch (set/delete/commit), as mu300-api asks; the settings in $STUBLOG/uci.json
FAKE_UCI = r'''
import json, os, shlex, sys
p = os.path.join(os.environ['STUBLOG'], 'uci.json')
db = json.load(open(p)) if os.path.exists(p) else {}
args = [a for a in sys.argv[1:] if a != '-q']
if args[:1] == ['-c']:
    args = args[2:]
with open(os.path.join(os.environ['STUBLOG'], 'calls'), 'a') as f:
    f.write('\x1f'.join(['uci'] + args) + '\n')
def resolve(path):
    # wireless.radio0.band, system.@system[0].hostname
    parts = path.split('.')
    if len(parts) >= 2 and parts[1].startswith('@'):
        parts[1] = 'system0'
    return parts
if args[0] == 'get':
    c, s, o = resolve(args[1])
    v = db.get(c, {}).get(s, {}).get(o)
    if v is None:
        sys.exit(1)
    print(v)
elif args[0] == 'show':
    for s, opts in db.get(args[1], {}).items():
        print('%s.%s=%s' % (args[1], s, opts.get('.type', 'x')))
        for k, v in opts.items():
            if k != '.type':
                print("%s.%s.%s='%s'" % (args[1], s, k, v))
elif args[0] == 'batch':
    log = open(os.path.join(os.environ['STUBLOG'], 'uci-batch'), 'a')
    for line in sys.stdin.read().splitlines():
        log.write(line + '\n')
        w = shlex.split(line)
        if not w:
            continue
        if w[0] == 'set':
            k, _, v = w[1].partition('=')
            c, s, o = resolve(k)
            db.setdefault(c, {}).setdefault(s, {})[o] = v
        elif w[0] == 'delete':
            c, s, o = resolve(w[1])
            if o not in db.get(c, {}).get(s, {}):
                sys.exit(1)
            del db[c][s][o]
        elif w[0] == 'del_list':
            k, _, v = w[1].partition('=')
            c, s, o = resolve(k)
            items = db.get(c, {}).get(s, {}).get(o, '').split()
            if v not in items:
                sys.exit(1)
            items.remove(v)
            db[c][s][o] = ' '.join(items)
        elif w[0] == 'commit':
            pass
        else:
            sys.exit(1)
    json.dump(db, open(p, 'w'))
else:
    sys.exit(1)
'''

LINK = 'vless://3f6a1c2e-0000-4000-8000-123456789abc@203.0.113.7:443?security=reality&sni=example.com#secret-link'
PUBKEY = 'QAAAA' + 'B' * 519 + '= browser@mu300-web'


class ApiCase(ShellTest):
    system = 'ubuntu'

    def setUp(self):
        super().setUp()
        t = self.tmp
        self.root, self.disk, self.bin, self.run_dir = t / 'root', t / 'disk', t / 'bin', t / 'run'
        for d in ('etc/mu300', 'proc', 'tmp', 'run', 'usr/share/zoneinfo/Europe', 'sys/class/thermal/thermal_zone0'):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for d in ('out', 'err', 'rc'):
            (t / d).mkdir()
        self.bin.mkdir()
        (self.disk / 'boot').mkdir(parents=True)
        (self.disk / '.mu300').mkdir()
        (self.disk / 'boot/kernel').write_text('6.18\n')
        (self.disk / '.mu300/boot-os').write_text('ubuntu\n')
        (self.disk / 'ubuntu/etc').mkdir(parents=True)
        (self.root / 'etc/mu300/image-version').write_text('v2026.10.15\n')
        (self.root / 'etc/mu300/default-boot').write_text('linux\n')
        (self.root / 'proc/uptime').write_text('3725.12 100.0\n')
        (self.root / 'proc/meminfo').write_text('MemTotal:  500000 kB\nMemAvailable:  250000 kB\n')
        (self.root / 'sys/class/thermal/thermal_zone0/type').write_text('soc-thmzone\n')
        (self.root / 'sys/class/thermal/thermal_zone0/temp').write_text('048250\n')
        (self.root / 'usr/share/zoneinfo/Europe/Istanbul').write_text('TZif...\n<+03>-3\n')
        if self.system == 'ubuntu':
            (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
            (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=MU300-AB12\nPSK=hotspotsecret1\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n')
        else:
            (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
            (self.root / 'etc/os-release').write_text('ID=openwrt\nVERSION_ID="25.12.5"\n')
            self.uci({'wireless': {'radio0': {'.type': 'wifi-device', 'band': '5g', 'channel': '36', 'country': 'TR', 'htmode': 'VHT80'},
                                   'default_radio0': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap', 'ssid': 'OpenWrt-1',
                                                      'encryption': 'psk2', 'key': 'wrtsecret99'}},
                      'network': {'wan': {'.type': 'interface', 'apn': 'internet', 'pdptype': 'IP', 'password': 'apnsecret7'},
                                  'lan': {'.type': 'interface', 'ipaddr': '192.168.77.1'}},
                      'system': {'system0': {'.type': 'system', 'hostname': 'OpenWrt', 'zonename': 'UTC'}}})
        for name in ('mu300-lan-ip', 'mu300-traffic', 'mu300-sim', 'mu300-wifi-band', 'wifi-client', 'sms', 'mu300-usb',
                     'mu300-next-boot', 'mu300-os', 'mu300-update', 'mu300-extra', 'mu300-at', 'busybox'):
            self.tool(name)
        self.tool('mu300-device', 'cat "$STUBLOG/device"')
        self.device('f50')
        self.out('mu300-lan-ip', '192.168.77.1\n')
        for name in ('systemctl', 'iw', 'ip', 'hostnamectl', 'timedatectl', 'chpasswd', 'ifup', 'ifdown', 'ifstatus', 'wifi',
                     'reboot', 'dmesg', 'journalctl', 'logread'):
            (self.stubs / name).write_text(RECORD)
            (self.stubs / name).chmod(0o755)
        (self.stubs / 'uci.py').write_text(FAKE_UCI)
        self.stub('uci', f'exec python3 "{self.stubs}/uci.py" "$@"')
        # setsid: the job runs (in the background, as on the device); anything else is only recorded ("later")
        self.stub('setsid', 'if [ "${4:-}" = mu300-api-job ]; then "$@" & exit 0; fi\n'
                  'l=later; for a in "$@"; do l="$l$(printf "\\037")$a"; done; printf "%s\\n" "$l" >> "$STUBLOG/calls"')
        if HAVE_BUSYBOX and os.environ.get('MU300_TEST_BUSYBOX_AWK', '1') == '1':
            self.stub('awk', 'exec busybox awk "$@"')

    def uci(self, db):
        (self.tmp / 'uci.json').write_text(json.dumps(db))

    def ucidb(self):
        return json.loads((self.tmp / 'uci.json').read_text())

    def tool(self, name, body=None):
        p = self.bin / name
        p.write_text(RECORD if body is None else '#!/bin/sh\n' + body + '\n')
        p.chmod(0o755)

    def device(self, d):
        (self.tmp / 'device').write_text(d + '\n')

    def out(self, name, text, rc=0, err=''):
        (self.tmp / 'out' / name).write_text(text)
        (self.tmp / 'rc' / name).write_text(str(rc))
        (self.tmp / 'err' / name).write_text(err)

    def env(self, **extra):
        e = super().env(**extra)
        e.update(MU300_SYSROOT=str(self.root), MU300_BIN=str(self.bin), MU300_LIBDIR=str(LIB), MU300_DISK=str(self.disk),
                 MU300_API_RUN=str(self.run_dir), MU300_API_NOROOT='1', MU300_DASH_RPC=str(self.tmp / 'mu300dash'),
                 STUB_STDIN='mu300-vpn chpasswd mu300-usb busybox wifi-client mu300dash mu300-at sms')
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def call(self, shell, method, req=None, raw=None, **env):
        """-> (exit status, the answer as an object, the CompletedProcess)"""
        data = raw if raw is not None else json.dumps(req if req is not None else {})
        r = subprocess.run(shell + [str(API), method], input=data.encode(), capture_output=True, env=self.env(**env), timeout=60)
        out = r.stdout.decode('utf-8', 'replace')
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, (method, out, r.stderr))
        return r.returncode, json.loads(lines[0]), r

    def ok(self, shell, method, req=None, **env):
        rc, o, r = self.call(shell, method, req, **env)
        self.assertEqual((rc, o.get('ok')), (0, 1), (method, o, r.stderr))
        return o

    def refused(self, shell, method, req, code, detail=None):
        rc, o, r = self.call(shell, method, req)
        self.assertEqual(rc, 1, (method, req, o))
        self.assertEqual(o['ok'], 0)
        self.assertEqual(o['error'], code, (method, req, o))
        if detail is not None:
            self.assertEqual(o.get('detail'), detail, (method, req, o))
        return o

    def calls(self, name=None):
        p = self.tmp / 'calls'
        # what mu300-api starts for later is started in the background: a moment for it to be recorded
        end = time.time() + (2 if name == 'later' else 0)
        while True:
            rows = [l.split('\x1f') for l in p.read_text().splitlines()] if p.exists() else []
            rows = [r for r in rows if name is None or r[0] == name]
            if rows or time.time() >= end:
                return rows
            time.sleep(0.05)

    def clear_calls(self):
        time.sleep(0.1)
        (self.tmp / 'calls').unlink(missing_ok=True)

    def everything_written(self):
        """every argument list, every reply file and job file: where a secret must never be"""
        text = (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else ''
        if self.run_dir.exists():
            for f in self.run_dir.rglob('*'):
                if f.is_file():
                    text += f.read_text(errors='replace')
        return text

    def wait_job(self, shell, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            j = self.ok(shell, 'job.status', {'from': 0})['job']
            if j['state'] != 'running':
                return j
            time.sleep(0.2)
        self.fail('the job did not end')


class Requests(ApiCase):
    BAD = ['', 'x', '[]', '"s"', '{"a":', '{"a":1,"a":2}', '{"a":{"b":1}}', '{"a":[1]}', '{"A":1}', '{"a":1} {}',
           '{"a":"\\u0000"}', '{"a":"\\u0001"}', '{"a":"x\ty"}', '{"a":"\\ud800"}', '{"a":01}', '{"a":tru}',
           '{"' + 'k' * 33 + '":1}']

    def test_only_one_json_object_of_plain_values(self):
        for shell in self.each_shell():
            for raw in self.BAD:
                if raw == '':
                    continue
                rc, o, _ = self.call(shell, 'job.status', raw=raw)
                self.assertEqual((rc, o['error']), (1, 'bad-request'), raw)
            # nothing at all is the empty request; so is white space
            for raw in ('', ' \n'):
                self.assertEqual(self.call(shell, 'extra.list', raw=raw)[0], 0)
            # larger than 64 KiB
            rc, o, _ = self.call(shell, 'extra.list', raw='{"a":"' + 'x' * 70000 + '"}')
            self.assertEqual(o['error'], 'bad-request')

    def test_methods_fields_arguments_and_root(self):
        for shell in self.each_shell():
            for m in ('nope', 'wifi.get;reboot', '../x', 'WIFI.GET', 'hello overview'):
                rc, o, _ = self.call(shell, m)
                self.assertEqual((rc, o['error']), (1, 'unknown-method'), m)
            self.refused(shell, 'extra.list', {'x': 1}, 'unknown-field', 'x')
            self.refused(shell, 'wifi.power', {'state': 'on', 'also': 'x'}, 'unknown-field', 'also')
            r = subprocess.run(shell + [str(API), 'hello', 'extra'], input=b'{}', capture_output=True, env=self.env())
            self.assertEqual(json.loads(r.stdout)['error'], 'bad-request')
            e = self.env()
            del e['MU300_API_NOROOT']
            if os.geteuid() != 0:
                r = subprocess.run(shell + [str(API), 'hello'], input=b'{}', capture_output=True, env=e)
                self.assertEqual(json.loads(r.stdout)['error'], 'not-root')
            r = subprocess.run(shell + [str(API), '--methods'], capture_output=True, text=True, env=self.env())
            self.assertIn('wifi.set', r.stdout.split())

    def test_wrong_types(self):
        for shell in self.each_shell():
            for req in ({'state': 1}, {'state': True}, {'state': None}, {'state': 'ON'}, {'state': 'on\n'}):
                o = self.call(shell, 'wifi.power', req)[1]
                self.assertIn(o['error'], ('bad-value', 'missing'), req)
            self.refused(shell, 'wifi.set', {'hidden': 'yes'}, 'bad-value', 'hidden')
            self.refused(shell, 'wifi.set', {'ssid': 5}, 'bad-value', 'ssid')
            self.refused(shell, 'job.status', {'from': -1}, 'bad-value', 'from')
            self.refused(shell, 'job.status', {'from': 1.5}, 'bad-value', 'from')
            self.assertEqual(self.calls('systemctl'), [])


class Overview(ApiCase):
    def test_hello_says_what_there_is(self):
        for shell in self.each_shell():
            o = self.ok(shell, 'hello')
            self.assertEqual((o['api'], o['system'], o['device'], o['release']), (1, 'ubuntu', 'f50', 'v2026.10.15'))
            listed = subprocess.run(shell + [str(API), '--methods'], capture_output=True, text=True, env=self.env()).stdout.split()
            self.assertEqual(o['methods'], listed)
            self.assertFalse(o['features']['vpn'])
            self.assertTrue(o['features']['wifi_client'])
            self.assertEqual(o['features']['sms'], 'cli')
            self.assertFalse(o['features']['locks'])
            self.assertEqual(o['job'], {'kind': '', 'state': 'none'})
            self.tool('mu300-vpn')
            self.assertTrue(self.ok(shell, 'hello')['features']['vpn'])
            (self.bin / 'mu300-vpn').unlink()

    def test_overview(self):
        self.out('mu300-at', '')
        (self.tmp / 'out/mu300-at').unlink()
        self.tool('mu300-at', 'case "$*" in *COPS*) printf \'+COPS: 0,0,"Evil\\\\x <b>",7\\r\\nOK\\r\\n\' ;; '
                  '*CEREG*) printf \'+CEREG: 0,5\\r\\nOK\\r\\n\' ;; *CESQ*) printf \'+CESQ: 99,99,255,255,30,60\\r\\nOK\\r\\n\' ;; esac')
        self.out('mu300-traffic', '{"today":{"rx":1,"tx":2},"cycle":{"used":3}}\n')
        self.out('ip', '5: sipa_eth0    inet 10.0.0.2/30 scope global sipa_eth0\n')
        self.out('systemctl', 'active\n')
        for shell in self.each_shell():
            shutil.rmtree(self.run_dir, ignore_errors=True)
            o = self.ok(shell, 'overview')
            self.assertEqual((o['system'], o['os'], o['device'], o['kernel_choice']), ('ubuntu', 'ubuntu 24.04', 'f50', '6.18'))
            self.assertEqual(o['uptime'], 3725)
            self.assertEqual(o['memory'], {'total_kb': 500000, 'avail_kb': 250000})
            self.assertEqual(o['signal']['operator'], 'Evil\\x <b>')
            self.assertEqual((o['signal']['rat'], o['signal']['registration'], o['signal']['rsrp']), ('LTE', 'roaming', -80))
            self.assertEqual(o['data']['address'], '10.0.0.2/30')
            self.assertEqual(o['traffic']['cycle'], {'used': 3})
            self.assertEqual(o['temperatures'], [{'name': 'soc-thmzone', 'millicelsius': 48250}])
            self.assertIsNone(o['battery'])
            self.assertEqual(o['hotspot']['ssid'], 'MU300-AB12')
            self.assertEqual(o['lan'], '192.168.77.1')
            self.assertEqual(o['boot'], {'default': 'linux', 'os': 'ubuntu', 'systems': ['ubuntu']})
            self.assertNotIn('hotspotsecret1', json.dumps(o))
            # a tool's answer that is not JSON is null, never pasted in
            self.out('mu300-traffic', '{"x": 1} , "injected": true\n')
            shutil.rmtree(self.run_dir, ignore_errors=True)
            self.assertIsNone(self.ok(shell, 'overview')['traffic'])
            self.out('mu300-traffic', '{"today":{"rx":1,"tx":2},"cycle":{"used":3}}\n')

    def test_the_u30_airs_battery(self):
        b = self.root / 'sys/class/power_supply/battery'
        b.mkdir(parents=True)
        for k, v in (('type', 'Battery'), ('capacity', '81'), ('status', 'Discharging'), ('voltage_now', '3900000')):
            (b / k).write_text(v + '\n')
        for shell in self.each_shell():
            self.assertIsNone(self.ok(shell, 'overview')['battery'])
            self.device('u30air')
            self.assertEqual(self.ok(shell, 'overview')['battery']['capacity'], 81)
            self.device('f50')


class HotspotUbuntu(ApiCase):
    HOSTILE = ['', 'x' * 33, 'a\nb', 'a\tb', ' lead', 'trail ', 'ok\u0007']

    def conf(self):
        return (self.root / 'etc/mu300/hotspot.conf').read_text().splitlines()

    def test_get_never_shows_the_password(self):
        self.out('iw', 'Station AA:BB:CC:DD:EE:01 (on wlan0)\n\tinactive time: 10 ms\nStation aa:bb:cc:dd:ee:02 (on wlan0)\n')
        (self.root / 'run/mu300-lan.leases').write_text('1700000000 aa:bb:cc:dd:ee:01 192.168.77.23 <b>laptop</b> *\n')
        for shell in self.each_shell():
            o = self.ok(shell, 'wifi.get')
            self.assertEqual((o['ssid'], o['band'], o['channel'], o['security'], o['password_set']), ('MU300-AB12', '5', 'auto', 'wpa2', True))
            self.assertEqual(o['channels']['5'], [36, 40, 44, 48])     # Turkey: no 149-165
            self.assertEqual(o['clients'][0], {'mac': 'aa:bb:cc:dd:ee:01', 'ip': '192.168.77.23', 'name': '<b>laptop</b>'})
            self.assertEqual(o['clients'][1]['ip'], '')
            self.assertNotIn('hotspotsecret1', json.dumps(o))

    def test_set_checks_every_value(self):
        before = (self.root / 'etc/mu300/hotspot.conf').read_text()
        for shell in self.each_shell():
            for ssid in self.HOSTILE:
                # (a control character other than tab, newline and CR is refused with the request itself)
                o = self.call(shell, 'wifi.set', {'ssid': ssid})[1]
                self.assertIn(o['error'], ('bad-value', 'bad-request'), ssid)
            for pw in ('short', 'x' * 64, 'pässword1', 'line\nbreak1', 'tab\there1'):
                self.refused(shell, 'wifi.set', {'password': pw}, 'bad-value', 'password')
            for ch in ('52', '149', '0', '14', 'auto;reboot'):
                self.refused(shell, 'wifi.set', {'channel': ch}, 'bad-value', 'channel')
            self.refused(shell, 'wifi.set', {'band': '6'}, 'bad-value', 'band')
            self.refused(shell, 'wifi.set', {'band': '2.4', 'channel': '36'}, 'bad-value', 'channel')
            self.refused(shell, 'wifi.set', {}, 'missing')
            self.assertEqual((self.root / 'etc/mu300/hotspot.conf').read_text(), before)
            self.assertEqual(self.calls('later'), [])

    def test_set_writes_hotspot_conf_and_restarts(self):
        conf = self.root / 'etc/mu300/hotspot.conf'
        for shell in self.each_shell():
            conf.write_text('SSID=MU300-AB12\nPSK=hotspotsecret1\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n')
            self.clear_calls()
            o = self.ok(shell, 'wifi.set', {'ssid': 'Café $(id) "x"', 'password': "p@ss $(reboot)'`", 'channel': '44', 'hidden': True})
            self.assertEqual(o, {'ok': 1, 'restart': True})
            self.assertEqual(self.conf(), ['BAND=5', 'COUNTRY=TR', 'SSID=Café $(id) "x"', "PSK=p@ss $(reboot)'`", 'CHANNEL=44', 'HIDDEN=1'])
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.calls('later'), [['later', 'sh', '-c', 'sleep 2; "$@"', 'sh', 'systemctl', 'restart', 'mu300-hotspot']])
            self.assertNotIn('p@ss', self.everything_written())
            # a new band starts on its automatic channel
            self.ok(shell, 'wifi.set', {'band': '2.4'})
            self.assertIn('BAND=2.4', self.conf())
            self.assertIn('CHANNEL=auto', self.conf())
            self.ok(shell, 'wifi.set', {'channel': '11'})
            self.assertIn('CHANNEL=11', self.conf())
            # while the radio is a client of another network, the hotspot is not restarted
            (self.root / 'run/mu300-wifi-client.active').write_text('')
            self.clear_calls()
            self.ok(shell, 'wifi.set', {'hidden': False})
            self.assertEqual(self.calls('later'), [])
            (self.root / 'run/mu300-wifi-client.active').unlink()

    def test_on_while_starting(self):
        """The hotspot restarted by wifi.set stays "activating" for about 20 s (its ExecStartPost checks): on, not off."""
        (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=MU300-AB12\nPSK=hotspotsecret1\n')
        for state, want in (('activating\n', True), ('active\n', True), ('inactive\n', False), ('failed\n', False)):
            self.out('systemctl', state)
            for shell in self.each_shell():
                self.assertEqual(self.ok(shell, 'wifi.get')['enabled'], want, state)

    def test_power(self):
        for shell in self.each_shell():
            self.clear_calls()
            self.ok(shell, 'wifi.power', {'state': 'off'})
            self.assertIn(['systemctl', 'disable', 'mu300-hotspot'], self.calls('systemctl'))
            self.assertEqual(self.calls('later')[0][-2:], ['stop', 'mu300-hotspot'])


class HotspotOpenWrt(ApiCase):
    system = 'openwrt'

    def test_get_and_set_through_uci(self):
        start = self.ucidb()
        for shell in self.each_shell():
            self.uci(start)
            o = self.ok(shell, 'wifi.get')
            self.assertEqual((o['ssid'], o['band'], o['channel'], o['security'], o['password_set'], o['enabled']),
                             ('OpenWrt-1', '5', '36', 'wpa2', True, True))
            self.assertNotIn('wrtsecret99', json.dumps(o))
            self.ok(shell, 'wifi.set', {'ssid': "it's \"$(x)\"", 'password': "new'secret$1", 'channel': '44', 'hidden': True})
            w = self.ucidb()['wireless']
            self.assertEqual((w['default_radio0']['ssid'], w['default_radio0']['key'], w['default_radio0']['hidden']),
                             ("it's \"$(x)\"", "new'secret$1", '1'))
            self.assertEqual((w['radio0']['channel'], w['radio0']['htmode']), ('44', 'VHT80'))
            # the password went to uci on its stdin (batch), never as an argument
            self.assertNotIn('new\'secret', ''.join('\x1f'.join(c) for c in self.calls()))
            self.assertEqual(self.calls('later')[-1][-2:], ['wifi', 'reload'])
            # an open hotspot gets WPA2 with its first password; WPA3 is never switched on here
            self.uci({**self.ucidb(), 'wireless': {**w, 'default_radio0': {**w['default_radio0'], 'encryption': 'none'}}})
            self.ok(shell, 'wifi.set', {'password': 'firstpassword'})
            self.assertEqual(self.ucidb()['wireless']['default_radio0']['encryption'], 'psk2')
            # a band goes through mu300-wifi-band (its channel list and widths)
            self.ok(shell, 'wifi.set', {'band': '2.4'})
            self.assertIn(['mu300-wifi-band', '2.4'], self.calls('mu300-wifi-band'))
            # a band with a channel: the channel after mu300-wifi-band (which puts the band on auto), then a reload
            # (a U30 Air: "2.4 GHz, channel 11" came up on 6). Here the stub leaves the band as it was.
            db = self.ucidb()
            self.uci({**db, 'wireless': {**db['wireless'], 'radio0': {**db['wireless']['radio0'], 'band': '2g', 'channel': 'auto'}}})
            self.clear_calls()
            self.ok(shell, 'wifi.set', {'band': '2.4', 'channel': '11'})
            self.assertEqual(self.calls('mu300-wifi-band'), [['mu300-wifi-band', '2.4']])
            self.assertEqual(self.ucidb()['wireless']['radio0']['channel'], '11')
            self.assertEqual(self.calls('later')[-1][-2:], ['wifi', 'reload'])

    def test_a_hotspot_left_on_sae_becomes_wpa2(self):
        """WPA3/SAE is disabled on this chip (FINDINGS 80): any change through the API puts a hotspot still set to
        it on WPA2-PSK (CCMP), with its password or the new one, and restarts Wi-Fi fully; never SAE, never open"""
        start = self.ucidb()
        for shell in self.each_shell():
            for enc in ('sae', 'sae-mixed', 'psk3'):
                db = json.loads(json.dumps(start))
                db['wireless']['default_radio0'].update({'encryption': enc, 'ieee80211w': '2', 'sae_pwe': '0',
                                                         'hostapd_bss_options': 'max_listen_interval=10 u30_sae_offload=1'})
                self.uci(db)
                self.assertEqual(self.ok(shell, 'wifi.get')['security'], 'wpa2-wpa3' if enc == 'sae-mixed' else 'wpa3')
                self.clear_calls()
                self.ok(shell, 'wifi.set', {'hidden': True})
                w = self.ucidb()['wireless']['default_radio0']
                self.assertEqual((w['encryption'], w['key'], w['hostapd_bss_options']),
                                 ('psk2+ccmp', 'wrtsecret99', 'max_listen_interval=10'))
                self.assertNotIn('ieee80211w', w)
                self.assertNotIn('sae_pwe', w)
                self.assertEqual(self.calls('later')[-1][-1], 'wifi down; sleep 1; wifi up')
                self.assertEqual(self.ok(shell, 'wifi.get')['security'], 'wpa2')
            # without a password it has, and none given: refused, nothing changed
            db = json.loads(json.dumps(start))
            db['wireless']['default_radio0'].update({'encryption': 'sae'})
            del db['wireless']['default_radio0']['key']
            self.uci(db)
            self.refused(shell, 'wifi.set', {'hidden': True}, 'missing', 'password')
            self.assertEqual(self.ucidb(), db)
            self.ok(shell, 'wifi.set', {'password': 'a-new-password'})
            w = self.ucidb()['wireless']['default_radio0']
            self.assertEqual((w['encryption'], w['key']), ('psk2+ccmp', 'a-new-password'))


class MobileData(ApiCase):
    def test_get_never_shows_the_password(self):
        conf = self.root / 'etc/mu300/mobile-data.conf'
        conf.write_text('MU300_APN=internet\nMU300_APN_USER="user1"\nMU300_APN_PASSWORD=apnsecret7\n')
        self.out('mu300-sim', '{"version":1,"ok":1,"active":1,"default":1,"slot1_supported":0}\n')
        for shell in self.each_shell():
            o = self.ok(shell, 'data.get')
            self.assertEqual((o['apn'], o['pdp'], o['user'], o['password_set']), ('internet', 'IP', 'user1', True))
            self.assertEqual(o['sim']['active'], 1)
            self.assertNotIn('apnsecret7', json.dumps(o))

    def test_set_checks_and_writes_the_dials_settings(self):
        conf = self.root / 'etc/mu300/mobile-data.conf'
        for shell in self.each_shell():
            conf.write_text('# mine\nMU300_APN=old\n')
            for apn in ('-x', 'a b', 'a"b', 'x' * 101, 'ä', 'a.'):
                self.refused(shell, 'data.set', {'apn': apn}, 'bad-value', 'apn')
            for cred in ('a"b', "a'b", 'a\\b', 'x' * 65, ' x', 'ü'):
                self.refused(shell, 'data.set', {'user': cred}, 'bad-value', 'user')
                self.refused(shell, 'data.set', {'password': cred}, 'bad-value', 'password')
            self.refused(shell, 'data.set', {'pdp': 'IPv4'}, 'bad-value', 'pdp')
            self.refused(shell, 'data.set', {'auth': 'mschap'}, 'bad-value', 'auth')
            self.assertEqual(conf.read_text(), '# mine\nMU300_APN=old\n')
            self.clear_calls()
            self.ok(shell, 'data.set', {'apn': 'corp.example-1', 'pdp': 'IPV4V6', 'auth': 'chap', 'user': 'u$(x)', 'password': 'p@$$;w0rd'})
            self.assertEqual(conf.read_text().splitlines(), ['# mine', 'MU300_APN=corp.example-1', 'MU300_PDP_TYPE=IPV4V6',
                                                             'MU300_APN_AUTH=chap', 'MU300_APN_USER=u$(x)', 'MU300_APN_PASSWORD=p@$$;w0rd'])
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.calls('later')[0][-2:], ['restart', 'mu300-mobile-data'])
            self.assertNotIn('p@$$', self.everything_written())
            self.ok(shell, 'data.set', {'clear_credentials': True})
            self.assertEqual(conf.read_text().splitlines(), ['# mine', 'MU300_APN=corp.example-1', 'MU300_PDP_TYPE=IPV4V6'])

    def test_data_roaming(self):
        """FINDINGS 120: mobile-data roaming --json in data.get; data.set roaming true/false runs mobile-data roaming
        on/off and restarts nothing; a system without the command has no switch."""
        for shell in self.each_shell():
            o = self.ok(shell, 'data.get')
            self.assertEqual((o['roaming'], o['roaming_switch']), (None, False))
            self.refused(shell, 'data.set', {'roaming': False}, 'unsupported', 'roaming')
            self.tool('mobile-data')
            self.out('mobile-data', '{"allowed":true,"held":false,"slot":0,"per_sim":false,"slots":[true,true]}\n')
            o = self.ok(shell, 'data.get')
            self.assertEqual((o['roaming']['allowed'], o['roaming']['held'], o['roaming_switch']), (True, False, True))
            self.refused(shell, 'data.set', {'roaming': [True]}, 'bad-request')
            for bad in ('off', 'no', 2):
                self.refused(shell, 'data.set', {'roaming': bad}, 'bad-value', 'roaming')
            self.clear_calls()
            o = self.ok(shell, 'data.set', {'roaming': False})
            self.assertEqual(o.get('restart'), False)
            self.assertEqual(self.calls('mobile-data')[-1], ['mobile-data', 'roaming', 'off'])
            self.assertEqual(self.calls('later'), [])
            self.ok(shell, 'data.set', {'roaming': True})
            self.assertEqual(self.calls('mobile-data')[-1], ['mobile-data', 'roaming', 'on'])
            # mobile-data refuses: the request fails with its last line
            self.out('mobile-data', '', rc=1, err='the data roaming setting could not be saved')
            self.refused(shell, 'data.set', {'roaming': True}, 'failed', 'the data roaming setting could not be saved')
            (self.bin / 'mobile-data').unlink()

    def test_sim_select_and_power(self):
        for shell in self.each_shell():
            self.refused(shell, 'sim.select', {'slot': 3}, 'bad-value', 'slot')
            self.refused(shell, 'sim.select', {'slot': '1;x'}, 'bad-value', 'slot')
            self.ok(shell, 'sim.select', {'slot': 2})
            self.assertEqual(self.calls('mu300-sim')[-1], ['mu300-sim', 'default', '2'])
            self.clear_calls()
            self.ok(shell, 'data.power', {'state': 'off'})
            self.assertEqual(self.calls('later')[0][-2:], ['stop', 'mu300-mobile-data.service'])

    def test_sim_card(self):
        """FINDINGS 204: the ZTE V50's card, 0-3 or "none", passed to mu300-sim card; anything else refused here."""
        for shell in self.each_shell():
            self.refused(shell, 'sim.card', {'card': 4}, 'bad-value', 'card')
            self.refused(shell, 'sim.card', {'card': '1;x'}, 'bad-value', 'card')
            self.refused(shell, 'sim.card', {}, 'missing', 'card')
            self.ok(shell, 'sim.card', {'card': 3})
            self.assertIn(['mu300-sim', 'card', '3'], self.calls('mu300-sim'))
            self.clear_calls()
            self.ok(shell, 'sim.card', {'card': 'none'})
            self.assertIn(['mu300-sim', 'card', 'none'], self.calls('mu300-sim'))
            self.clear_calls()

    def test_voice(self):
        """FINDINGS 184: voice.get is mobile-data voice --json --read; voice.set the fixed words, the slot 1/2 as 0/1."""
        for shell in self.each_shell():
            self.refused(shell, 'voice.get', {}, 'unsupported', 'mobile-data')
            self.tool('mobile-data')
            self.out('mobile-data', '{"slot":0,"slots":[{"volte":true,"vonr":null},{"volte":true,"vonr":null}],"live":null}\n')
            o = self.ok(shell, 'voice.get')
            self.assertEqual(o['voice']['slots'][0], {'volte': True, 'vonr': None})
            self.assertEqual(self.calls('mobile-data')[-1], ['mobile-data', 'voice', '--json', '--read'])
            for bad in ({'slot': 3, 'kind': 'volte', 'state': 'on'}, {'slot': 1, 'kind': 'ims', 'state': 'on'},
                        {'slot': 1, 'kind': 'volte', 'state': '-x'}):
                self.refused(shell, 'voice.set', bad, 'bad-value')
            self.refused(shell, 'voice.set', {'slot': 1, 'kind': 'volte'}, 'missing', 'state')
            self.out('mobile-data', 'vonr off for the SIM in slot 1 (saved; sent when that card\'s radio next starts)\n')
            o = self.ok(shell, 'voice.set', {'slot': 2, 'kind': 'vonr', 'state': 'off'})
            self.assertEqual(self.calls('mobile-data')[-1], ['mobile-data', 'voice', 'vonr', 'off', '--slot', '1'])
            self.out('mobile-data', '', rc=1, err='the modem did not take volte off; nothing was saved')
            self.refused(shell, 'voice.set', {'slot': 1, 'kind': 'volte', 'state': 'off'}, 'failed',
                         'the modem did not take volte off; nothing was saved')
            (self.bin / 'mobile-data').unlink()

    def test_radio(self):
        """FINDINGS 185: radio.get is mu300-sim-radio status --json; radio.set slot 1/2/"all" as 0/1/all."""
        for shell in self.each_shell():
            self.refused(shell, 'radio.get', {}, 'unsupported', 'mu300-sim-radio')
            self.tool('mu300-sim-radio')
            self.out('mu300-sim-radio', '{"ok":1,"enabled":[true,false],"all_off":false,"active":0}\n')
            o = self.ok(shell, 'radio.get')
            self.assertEqual(o['radio']['enabled'], [True, False])
            for bad in ({'slot': 3, 'state': 'on'}, {'slot': 'both', 'state': 'on'}, {'slot': 1, 'state': 'maybe'}):
                self.refused(shell, 'radio.set', bad, 'bad-value')
            self.refused(shell, 'radio.set', {'state': 'on'}, 'missing', 'slot')
            self.out('mu300-sim-radio', 'SIM1 radio off (saved)\n')
            for slot, arg in ((1, '0'), (2, '1'), ('all', 'all')):
                o = self.ok(shell, 'radio.set', {'slot': slot, 'state': 'off'})
                self.assertEqual(self.calls('mu300-sim-radio')[-1], ['mu300-sim-radio', 'set', arg, 'off'])
            self.out('mu300-sim-radio', '', rc=75, err='mu300-sim-radio: a radio change is already running')
            self.refused(shell, 'radio.set', {'slot': 1, 'state': 'on'}, 'busy')
            (self.bin / 'mu300-sim-radio').unlink()

    def test_locks_are_the_panels_alone(self):
        for shell in self.each_shell():
            self.refused(shell, 'lock.get', {}, 'unsupported')


class MobileDataOpenWrt(ApiCase):
    system = 'openwrt'

    def test_set_through_uci(self):
        start = self.ucidb()
        for shell in self.each_shell():
            self.uci(start)
            o = self.ok(shell, 'data.get')
            self.assertEqual((o['apn'], o['password_set']), ('internet', True))
            self.assertNotIn('apnsecret7', json.dumps(o))
            self.ok(shell, 'data.set', {'apn': 'corp', 'password': 'n3w$ecret'})
            self.assertEqual(self.ucidb()['network']['wan']['password'], 'n3w$ecret')
            self.assertNotIn('n3w$ecret', ''.join('\x1f'.join(c) for c in self.calls()))
            self.assertEqual(self.calls('later')[-1][-2:], ['ifup', 'wan'])


class Luci(ApiCase):
    """openwrt-luci: what only the control panel has goes to its backend, called as rpcd calls it"""
    system = 'openwrt'

    def setUp(self):
        super().setUp()
        p = self.tmp / 'mu300dash'
        p.write_text('#!/bin/sh\n{ printf mu300dash; for a in "$@"; do printf "\\037%s" "$a"; done; printf "\\n"; } >> "$STUBLOG/calls"\n'
                     'cat > "$STUBLOG/stdin.mu300dash"\ncat "$STUBLOG/out/mu300dash" 2>/dev/null\n')
        p.chmod(0o755)

    def test_locks_sms_and_forwarding(self):
        for shell in self.each_shell():
            self.assertEqual(self.ok(shell, 'hello')['system'], 'openwrt-luci')
            self.out('mu300dash', '{"mode":"auto","lte":""}\n')
            self.assertEqual(self.ok(shell, 'lock.get')['data'], {'mode': 'auto', 'lte': ''})
            self.assertEqual(self.calls('mu300dash')[-1], ['mu300dash', 'call', 'lock_get'])
            for kind, val in (('mode', 'lte'), ('lte', '1;3'), ('cell', 'nr:1,'), ('nope', 'x'), ('lte', '1,' * 33)):
                self.assertEqual(self.call(shell, 'lock.set', {'kind': kind, 'val': val})[1]['error'], 'bad-value', (kind, val))
            self.ok(shell, 'lock.set', {'kind': 'lte', 'val': '1,3,20'})
            self.assertEqual(json.loads((self.tmp / 'stdin.mu300dash').read_text()), {'kind': 'lte', 'val': '1,3,20'})
            self.ok(shell, 'sms.send', {'number': '+905551112233', 'text': 'Merhaba "x" $(id)\nsatır 2'})
            self.assertEqual(json.loads((self.tmp / 'stdin.mu300dash').read_text()),
                             {'num': '+905551112233', 'text': 'Merhaba "x" $(id)\nsatır 2'})
            for num in ('12a', '+', '1' * 21, '-1'):
                self.refused(shell, 'sms.send', {'number': num, 'text': 'x'}, 'bad-value', 'number')
            self.refused(shell, 'sms.send', {'number': '1', 'text': 'x' * 481}, 'bad-value', 'text')
            self.refused(shell, 'sms.send', {'number': '1', 'text': ''}, 'bad-value', 'text')
            # forwarding: the panel's adapter itself; an answer that is not JSON is a failure, never pasted in
            self.refused(shell, 'sms.forward', {}, 'unsupported')
            ad = self.root / 'usr/libexec/unisoc-modem'
            ad.mkdir(parents=True, exist_ok=True)
            (ad / 'forward').write_text('#!/bin/sh\necho garbage\n')
            (ad / 'forward').chmod(0o755)
            self.refused(shell, 'sms.forward', {}, 'failed')
            (ad / 'forward').unlink()


class Sms(ApiCase):
    def test_list_send_delete(self):
        self.out('sms', '{"store":"SM","messages":[{"index":[3,4],"sender":"+90","text":"hi"}]}\n')
        for shell in self.each_shell():
            self.assertEqual(self.ok(shell, 'sms.list')['data']['messages'][0]['index'], [3, 4])
            self.ok(shell, 'sms.send', {'number': '+90555', 'text': '-x $(id)'})
            # the number and the text on sms's stdin, never in its arguments
            self.assertEqual(self.calls('sms')[-1], ['sms', 'send', '--stdin'])
            self.assertEqual((self.tmp / 'stdin.sms').read_text(), '+90555\n-x $(id)')
            self.assertEqual([f.name for f in self.run_dir.iterdir() if f.name.startswith('sms.')], [])
            self.ok(shell, 'sms.delete', {'id': '3'})
            self.assertEqual(self.calls('sms')[-1], ['sms', 'delete', '3'])
            self.refused(shell, 'sms.delete', {'id': '3 4'}, 'bad-value', 'id')


class Vpn(ApiCase):
    def setUp(self):
        super().setUp()
        self.tool('mu300-vpn')

    def test_without_the_module(self):
        (self.bin / 'mu300-vpn').unlink()
        for shell in self.each_shell():
            self.assertEqual(self.ok(shell, 'vpn.status'), {'ok': 1, 'installed': False, 'extra': True})
            self.refused(shell, 'vpn.profiles', {}, 'not-installed')
            self.assertEqual(self.ok(shell, 'vpn.install'), {'ok': 1, 'job': 'vpn-install'})
            j = self.wait_job(shell)
            self.assertEqual((j['kind'], j['state'], j['rc']), ('vpn-install', 'done', 0))
            self.assertIn(['mu300-extra', 'install', 'vpn'], self.calls('mu300-extra'))

    def test_a_link_goes_to_the_clis_stdin_only(self):
        self.out('mu300-vpn', 'p-1\n')
        for shell in self.each_shell():
            o = self.ok(shell, 'vpn.add', {'source': 'link', 'data': '  ' + LINK + '\r\n', 'name': 'Work $(x)'})
            self.assertEqual(o['id'], 'p-1')
            self.assertEqual(self.calls('mu300-vpn')[-1], ['mu300-vpn', 'profile', 'import', '-', 'Work $(x)'])
            self.assertEqual((self.tmp / 'stdin.mu300-vpn').read_text(), LINK + '\n')
            self.assertNotIn('3f6a1c2e', self.everything_written())
            for bad in ('http://x', 'vless://', 'vless://a b', 'javascript:alert(1)', 'vless://' + 'a' * 8000):
                self.refused(shell, 'vpn.add', {'source': 'link', 'data': bad}, 'bad-value', 'data')
            for name in ('-x', ' x', 'a\nb', 'x' * 65):
                self.refused(shell, 'vpn.add', {'source': 'link', 'data': LINK, 'name': name}, 'bad-value', 'name')
            # a config pasted whole: tabs and newlines are fine, other control characters are not
            self.ok(shell, 'vpn.add', {'source': 'text', 'data': '[Interface]\n\tPrivateKey = abc\n'})
            self.refused(shell, 'vpn.add', {'source': 'text', 'data': 'a\u0007'}, 'bad-request')
            # what the CLI says when it refuses: its last line, without the link's secret part
            self.out('mu300-vpn', '', rc=1, err='bad link vless://3f6a1c2e@host:1?x=y\nprofile not added\n')
            o = self.refused(shell, 'vpn.add', {'source': 'link', 'data': LINK}, 'failed')
            self.assertNotIn('3f6a1c2e', json.dumps(o))
            self.out('mu300-vpn', 'p-1\n')

    def test_a_url_job_takes_it_on_stdin_from_a_file_that_is_gone(self):
        url = 'https://sub.example.com/api/v1/client/subscribe?token=abcdef123456'
        for shell in self.each_shell():
            self.ok(shell, 'job.clear')
            self.refused(shell, 'vpn.add_url', {'url': 'http://sub.example.com/x'}, 'bad-value', 'url')
            self.refused(shell, 'vpn.add_url', {'url': 'https://a b'}, 'bad-value', 'url')
            self.assertEqual(self.ok(shell, 'vpn.add_url', {'url': url}), {'ok': 1, 'job': 'vpn-import'})
            j = self.wait_job(shell)
            self.assertEqual(j['rc'], 0)
            self.assertEqual(self.calls('mu300-vpn')[-1], ['mu300-vpn', 'profile', 'import', '--url', '-'])
            self.assertEqual((self.tmp / 'stdin.mu300-vpn').read_text(), url + '\n')
            self.assertFalse((self.run_dir / 'job/in').exists())
            self.assertNotIn('abcdef123456', self.everything_written())

    def test_reads_and_switches(self):
        for shell in self.each_shell():
            self.out('mu300-vpn', '{"enabled":true,"profile":"p-1","killswitch":"on"}\n')
            self.assertEqual(self.ok(shell, 'vpn.status')['status']['profile'], 'p-1')
            self.ok(shell, 'vpn.status', {'exit_ip': True})
            self.assertEqual(self.calls('mu300-vpn')[-1], ['mu300-vpn', 'status', '--json', '--exit-ip'])
            self.out('mu300-vpn', '{"KILL_SWITCH":"on","IPV6":"0","XRAY_BIN":"/x"}\n')
            self.assertEqual(self.ok(shell, 'vpn.settings')['settings'], {'KILL_SWITCH': 'on', 'IPV6': '0'})
            for m, req, argv in (('vpn.service', {'op': 'off'}, ['off']), ('vpn.killswitch', {'value': 'on'}, ['killswitch', 'on']),
                                 ('vpn.use', {'id': 'p-2'}, ['use', 'p-2']), ('vpn.rename', {'id': 'p-2', 'name': 'Ev'}, ['profile', 'set', 'p-2', 'NAME', 'Ev']),
                                 ('vpn.remove', {'id': 'p-2'}, ['profile', 'remove', 'p-2']),
                                 ('vpn.setting', {'key': 'IPV6', 'value': 'auto'}, ['settings', 'set', 'IPV6', 'auto']),
                                 ('vpn.sub_remove', {'id': 's-1', 'keep': True}, ['subscription', 'remove', 's-1', '--keep-profiles'])):
                self.ok(shell, m, req)
                self.assertEqual(self.calls('mu300-vpn')[-1], ['mu300-vpn'] + argv, m)
            for m, req in (('vpn.use', {'id': '-x'}), ('vpn.use', {'id': 'P1'}), ('vpn.setting', {'key': 'XRAY_BIN', 'value': '/bin/sh'}),
                           ('vpn.service', {'op': 'start'}), ('vpn.logs', {'lines': 5000})):
                self.assertEqual(self.call(shell, m, req)[1]['error'], 'bad-value', (m, req))
            self.out('mu300-vpn', '[{"name":"xray","installed":true}]\n')
            self.assertEqual(self.ok(shell, 'vpn.engines')['engines'], [{'name': 'xray', 'installed': True}])
            self.assertEqual(self.calls('mu300-vpn')[-1], ['mu300-vpn', 'engines', '--json'])
            # JSON that is not JSON is refused, not passed on
            self.out('mu300-vpn', '[1,2\n')
            self.refused(shell, 'vpn.profiles', {}, 'failed')


class UsbLanAdb(ApiCase):
    def test_lan_address(self):
        for shell in self.each_shell():
            for ip in ('8.8.8.1', '192.168.1.2', '192.168.1.1;reboot', '172.32.0.1', '10.0.0.256', '192.168.01.1'):
                self.refused(shell, 'lan.set', {'ip': ip}, 'bad-value', 'ip')
            self.ok(shell, 'lan.set', {'ip': '10.20.30.1'})
            self.assertEqual((self.root / 'etc/mu300/lan.conf').read_text(), 'LAN_IP=10.20.30.1\n')
            self.assertEqual(self.ok(shell, 'lan.get')['ip'], '10.20.30.1')
            self.assertEqual(self.calls('later')[-1][-2:], ['restart', 'mu300-lan'])

    def test_adb_keys_mode_takes_the_callers_key_first(self):
        for shell in self.each_shell():
            self.refused(shell, 'adb.auth', {'mode': 'keys'}, 'missing', 'key')
            for key in ('short', PUBKEY + '\nssh-rsa x', PUBKEY.replace('B', '$', 1), '-' + PUBKEY):
                self.refused(shell, 'adb.auth', {'mode': 'keys', 'key': key}, 'bad-value', 'key')
            self.clear_calls()
            self.ok(shell, 'adb.auth', {'mode': 'keys', 'key': PUBKEY})
            self.assertEqual(self.calls('mu300-usb')[0], ['mu300-usb', 'adb', 'key', '-'])
            self.assertEqual((self.tmp / 'stdin.mu300-usb').read_text(), PUBKEY + '\n')
            self.assertEqual(self.calls('later')[-1][-4:], [str(self.bin / 'mu300-usb'), 'adb', 'auth', 'keys'])
            # a key that is not taken: keys mode is not switched on
            self.out('mu300-usb', '', rc=1, err='mu300-usb: not a key\n')
            self.clear_calls()
            self.refused(shell, 'adb.auth', {'mode': 'keys', 'key': PUBKEY}, 'failed')
            self.assertEqual(self.calls('later'), [])
            self.out('mu300-usb', '')

    def test_usb_role_is_the_u30_airs(self):
        for shell in self.each_shell():
            self.refused(shell, 'usb.role', {'role': 'host'}, 'unsupported')
            self.device('u30air')
            self.ok(shell, 'usb.role', {'role': 'host'})
            self.assertEqual(self.calls('later')[-1][-2:], [str(self.bin / 'mu300-usb'), 'host'])
            self.device('f50')


class System(ApiCase):
    def test_password_goes_to_chpasswd_on_stdin(self):
        for shell in self.each_shell():
            self.refused(shell, 'system.password', {'password': ''}, 'missing', 'password')
            self.refused(shell, 'system.password', {'password': 'a\nroot:x'}, 'bad-value', 'password')
            self.refused(shell, 'system.password', {'password': 'x' * 129}, 'bad-value', 'password')
            o = self.ok(shell, 'system.password', {'password': 'Gizli şifre: $(x) "y"'})
            self.assertEqual(o, {'ok': 1, 'user': 'ubuntu'})
            self.assertEqual(self.calls('chpasswd')[-1], ['chpasswd'])
            self.assertEqual((self.tmp / 'stdin.chpasswd').read_text(), 'ubuntu:Gizli şifre: $(x) "y"\n')
            self.assertNotIn('Gizli', self.everything_written())

    def test_hostname_timezone_ssh_boot(self):
        for shell in self.each_shell():
            for name in ('-x', 'a_b', 'x' * 64, 'a b', 'a;b'):
                self.refused(shell, 'system.hostname', {'name': name}, 'bad-value', 'name')
            self.ok(shell, 'system.hostname', {'name': 'my-router1'})
            self.assertEqual(self.calls('hostnamectl')[-1], ['hostnamectl', 'set-hostname', 'my-router1'])
            for z in ('../../etc/passwd', 'Europe/../x', 'Europe/Nowhere', 'europe/istanbul'):
                self.refused(shell, 'system.timezone', {'zone': z}, 'bad-value', 'zone')
            self.ok(shell, 'system.timezone', {'zone': 'Europe/Istanbul'})
            self.assertEqual(self.calls('timedatectl')[-1], ['timedatectl', 'set-timezone', 'Europe/Istanbul'])
            self.ok(shell, 'system.ssh', {'state': 'off'})
            self.assertEqual(self.calls('systemctl')[-1], ['systemctl', 'disable', '--now', 'ssh.socket', 'ssh.service'])
            self.ok(shell, 'system.boot', {'target': 'android'})
            self.assertEqual(self.calls('mu300-next-boot')[-1], ['mu300-next-boot', 'android'])
            self.refused(shell, 'system.boot', {'target': 'recovery'}, 'bad-value', 'target')
            o = self.ok(shell, 'system.get')
            self.assertEqual((o['user'], o['boot']['default'], o['kernel_choice']), ('ubuntu', 'linux', '6.18'))
            self.assertEqual(o['extras'][0], {'name': 'vpn', 'installed': False, 'release': ''})

    def test_openwrt_password_and_timezone(self):
        (self.root / 'etc/openwrt_release').write_text('x\n')
        for shell in self.each_shell():
            self.ok(shell, 'system.password', {'password': 'rootpw1'})
            self.assertEqual(self.calls('busybox')[-1], ['busybox', 'chpasswd', '-c', 'sha512'])
            self.assertEqual((self.tmp / 'stdin.busybox').read_text(), 'root:rootpw1\n')
            self.ok(shell, 'system.timezone', {'zone': 'Europe/Istanbul'})
            self.assertIn("set system.@system[0].timezone='<+03>-3'", (self.tmp / 'uci-batch').read_text())
            self.refused(shell, 'system.timezone', {'zone': 'Asia/Tokyo'}, 'unsupported')


class Jobs(ApiCase):
    def test_one_job_that_outlives_the_call(self):
        self.tool('mu300-update', 'echo "line 1 https://github.com/x/y/releases/download/v1/secret?token=abc"; '
                  'echo "line 2"; [ "$1" = check ] && sleep 2; exit 3')
        for shell in self.each_shell():
            self.ok(shell, 'job.clear')
            self.assertEqual(self.ok(shell, 'update.check'), {'ok': 1, 'job': 'update-check'})
            self.refused(shell, 'update.apply', {}, 'busy')
            self.assertEqual(self.ok(shell, 'hello')['job'], {'kind': 'update-check', 'state': 'running'})
            j = self.wait_job(shell)
            self.assertEqual((j['state'], j['rc']), ('done', 3))
            self.assertEqual(j['text'].splitlines(), ['line 1 https://...', 'line 2'])
            self.assertNotIn('token=abc', json.dumps(j))
            self.assertEqual(self.ok(shell, 'job.status', {'from': 1})['job']['text'], 'line 2')
            self.assertEqual(self.ok(shell, 'job.status', {'from': 2})['job']['text'], '')
            # an offset past the end is a job that is gone: from the start
            self.assertEqual(self.ok(shell, 'job.status', {'from': 99})['job']['next'], 2)
            self.refused(shell, 'job.cancel', {}, 'failed')
            self.ok(shell, 'job.clear')
            self.assertEqual(self.ok(shell, 'job.status')['job']['state'], 'none')

    def test_an_update_or_a_kernel_is_never_stopped_half_way(self):
        self.tool('mu300-update', 'echo "mu300-update\x1f$1\x1f$2" >> "$STUBLOG/calls"; sleep 3')
        for shell in self.each_shell():
            self.ok(shell, 'job.clear')
            self.refused(shell, 'kernel.set', {'version': '6.1'}, 'bad-value', 'version')
            self.refused(shell, 'kernel.set', {'version': '7.4'}, 'bad-value', 'version')
            self.ok(shell, 'kernel.set', {'version': '7.2'})
            self.assertFalse(self.ok(shell, 'job.status')['job']['cancel'])
            self.refused(shell, 'job.cancel', {}, 'unsupported')
            self.wait_job(shell)
            self.assertIn(['mu300-update', 'kernel', '7.2'], self.calls('mu300-update'))

    def test_extras(self):
        for shell in self.each_shell():
            self.ok(shell, 'job.clear')
            for n in ('vpn;reboot', 'bogus', '--from'):
                self.refused(shell, 'extra.install', {'name': n}, 'bad-value', 'name')
            self.ok(shell, 'extra.install', {'name': 'panel'})
            self.wait_job(shell)
            self.assertIn(['mu300-extra', 'install', 'panel'], self.calls('mu300-extra'))

    def test_joining_a_network_takes_the_password_on_stdin(self):
        for shell in self.each_shell():
            self.ok(shell, 'job.clear')
            self.refused(shell, 'wificlient.join', {'ssid': 'Cafe'}, 'missing', 'password')
            self.refused(shell, 'wificlient.join', {'ssid': '-x', 'password': 'longenough'}, 'bad-value', 'ssid')
            self.ok(shell, 'wificlient.join', {'ssid': 'Café "1"', 'password': 'wifi$ecret1'})
            self.wait_job(shell)
            self.assertEqual(self.calls('wifi-client')[-1], ['wifi-client', 'connect', 'Café "1"', '-'])
            self.assertEqual((self.tmp / 'stdin.wifi-client').read_text(), 'wifi$ecret1\n')
            self.assertNotIn('wifi$ecret1', self.everything_written())

    def test_a_tool_message_with_its_indented_lines_is_the_reason(self):
        """wifi-client scan with the hotspot on (an F50): the reason is the whole message, not its last line."""
        self.out('wifi-client', '', rc=1, err='some earlier line\n'
                 'wifi-client: the radio is an access point right now, and this driver cannot turn it back into a\n'
                 '             client without a reboot.\n\n'
                 '             That saves it, and after a reboot the device joins it.\n')
        for shell in self.each_shell():
            self.refused(shell, 'wificlient.scan', {}, 'failed',
                         'wifi-client: the radio is an access point right now, and this driver cannot turn it back '
                         'into a client without a reboot. That saves it, and after a reboot the device joins it.')
        self.out('wifi-client', '', rc=1, err='wifi-client: no wlan0\n')
        for shell in self.each_shell():
            self.refused(shell, 'wificlient.scan', {}, 'failed', 'wifi-client: no wlan0')


class Static(unittest.TestCase):
    def test_no_backslash_in_a_gsub_replacement(self):
        """busybox 1.37's awk (OpenWrt 25.12) read gsub's "\\\\\\\\" as one backslash: quotes came out unescaped and
        backslashes were lost on a U30 Air. json.awk replaces literally (rep); no gsub/sub may carry a backslash."""
        import re
        for p in (LIB / 'json.awk', API):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                for m in re.finditer(r'\bg?sub\(([^;]*)\)', line):
                    self.assertNotRegex(m.group(1).split(',', 1)[-1], r'"[^"]*\\\\', f'{p.name}:{n}: {line.strip()}')

    def test_on_the_path_and_every_method_dispatched(self):
        self.assertIn('mu300-api', (LIB / 'path-commands').read_text().split())
        text = API.read_text()
        methods = text[text.index("METHODS='") + 9:text.index("'", text.index("METHODS='") + 9)].split()
        dispatch = text[text.index('case $METHOD in\n'):]
        for m in methods:
            self.assertIn(f'    {m}) m_{m.replace(".", "_")} ;;', dispatch, m)
        self.assertTrue(os.access(API, os.X_OK))


class PanelAdapters(ApiCase):
    """Ubuntu has the control panel's lock and forwarding programs too (FINDINGS 189): lock.get/lock.set and the
    sms.forward methods call them directly; the AT terminal's history, the card's state, the USB network mode."""

    def setUp(self):
        super().setUp()
        self.dash = self.tmp / 'dash'
        self.dash.mkdir()
        self.tool('lock', '{ printf "lock"; for a in "$@"; do printf "\037%s" "$a"; done; printf "\n"; } >> "$STUBLOG/calls"\n'
                          'case "$1 $2" in "get cached") cat "$STUBLOG/lockout" ;; esac')
        (self.tmp / 'lockout').write_text('{"auto_apply":1,"ts":1,"mode":{"label":"auto"},"lte":{"locked":""},"nr":{"locked":"78"},'
                                          '"cell":"","cells":[],"caps":{"nr":"1,78","lte":"1,3"}}\n')
        # the forwarding adapter: what it was asked and its stdin (and MU300_FWD_CHECK), then the answer in fwdout
        self.tool('forward', '{ printf "forward"; for a in "$@"; do printf "\037%s" "$a"; done; printf "\n"; } >> "$STUBLOG/calls"\n'
                             'cat > "$STUBLOG/stdin.forward"; echo "${MU300_FWD_CHECK:-}" > "$STUBLOG/fwdcheck"\n'
                             'cat "$STUBLOG/fwdout"')
        (self.tmp / 'fwdout').write_text('{"ok":1}\n')

    def env(self, **extra):
        e = super().env(**extra)
        e.update(MU300_LOCK_BIN=str(self.bin / 'lock'), MU300_FWD_ADAPTER=str(self.bin / 'forward'),
                 MU300_DASH_DIR=str(self.dash), MU300_API_LOCK_WAIT='2')
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def test_hello_has_them(self):
        for shell in self.each_shell():
            f = self.ok(shell, 'hello')['features']
            self.assertEqual((f['locks'], f['forward'], f['usb_net']), (True, True, True))
            for m in ('sim.state', 'sms.forward_set', 'sms.forward_test', 'sms.forward_log_clear', 'usb.net', 'at.history'):
                self.assertIn(m, self.ok(shell, 'hello')['methods'])

    def test_locks_through_the_lock_program(self):
        for shell in self.each_shell():
            (self.dash / 'lock.json').write_text('{}')
            o = self.ok(shell, 'lock.get')
            self.assertEqual(o['data']['nr']['locked'], '78')
            self.assertEqual(self.calls('lock')[-1], ['lock', 'get', 'cached'])
            self.assertEqual(self.calls('later'), [])
            # fresh: a reading in the background first (the cache is served when it does not come in time)
            self.ok(shell, 'lock.get', {'fresh': True})
            self.assertEqual(self.calls('later')[-1][1:], [str(self.bin / 'lock'), 'refresh'])
            for kind, val in (('mode', '4g'), ('lte', '1,3'), ('nr', ''), ('cell', 'nr:627264,393'), ('auto_apply', 'off'),
                              ('endc', 'on')):
                self.clear_calls()
                o = self.ok(shell, 'lock.set', {'kind': kind, 'val': val})
                self.assertEqual((o['data']['started'], o['data']['queued']), (1, 0))
                self.assertEqual(self.calls('later')[-1][1:], [str(self.bin / 'lock'), 'apply', kind, val])
            self.clear_calls()
            self.ok(shell, 'lock.set', {'kind': 'reset', 'val': 'auto'})
            self.assertEqual(self.calls('later')[-1][1:], [str(self.bin / 'lock'), 'reset'])
            for kind, val in (('mode', 'lte'), ('lte', '1;3'), ('cell', 'nr:1,'), ('auto_apply', 'maybe'), ('nope', 'x'),
                              ('lte', '-1'), ('cell', '--help')):
                self.refused(shell, 'lock.set', {'kind': kind, 'val': val}, 'bad-value')
            self.refused(shell, 'lock.set', {'kind': 'mode'}, 'missing', 'val')
            # checked only (a backup's restore): nothing applied
            self.clear_calls()
            rc, o, _ = self.call(shell, 'lock.set', {'kind': 'mode', 'val': 'sa'}, MU300_API_CHECK='1')
            self.assertEqual(o, {'ok': 1, 'checked': True})
            self.assertEqual(self.calls('later'), [])
            (self.dash / 'lock.json').unlink()

    def test_forwarding_settings_on_stdin_only(self):
        req = {'enabled': True, 'template': '{text}\n-- {sender}', 'allow': 'BANK*\n+90555', 'webhook': True,
               'webhook_url': 'https://hook.example.com/s3cret', 'webhook_headers': 'Authorization: Bearer hdrsecret',
               'webhook_format': 'json', 'webhook_expect': 'errcode=0', 'telegram': False, 'telegram_token': '123456:' + 'A' * 30,
               'email': False, 'email_password': 'mailsecret', 'sms': False, 'clear': 'telegram_token'}
        for shell in self.each_shell():
            self.assertEqual(self.ok(shell, 'sms.forward')['data'], {'ok': 1})
            self.assertEqual(self.calls('forward')[-1], ['forward', 'get'])
            self.ok(shell, 'sms.forward_set', req)
            self.assertEqual(self.calls('forward')[-1], ['forward', 'set'])
            sent = json.loads((self.tmp / 'stdin.forward').read_text())
            self.assertEqual((sent['enabled'], sent['webhook'], sent['telegram'], sent['email'], sent['sms']), ('1', '1', '0', '0', '0'))
            for k in ('template', 'allow', 'webhook_url', 'webhook_headers', 'telegram_token', 'email_password', 'clear'):
                self.assertEqual(sent[k], req[k], k)
            self.assertEqual((self.tmp / 'fwdcheck').read_text().strip(), '')
            # no secret in an argument list, and no request file left behind
            for c in self.calls():
                self.assertFalse(any(x in '\x1f'.join(c) for x in ('s3cret', 'hdrsecret', 'mailsecret', 'AAAAAAAAAA')), c)
            self.assertEqual([f.name for f in self.run_dir.iterdir() if f.name.startswith('fwd.')], [])
            # the adapter's refusal is the answer's detail
            (self.tmp / 'fwdout').write_text('{"ok":0,"error":"Invalid Telegram bot token"}\n')
            self.refused(shell, 'sms.forward_set', {'telegram': True}, 'failed', 'Invalid Telegram bot token')
            (self.tmp / 'fwdout').write_text('{"ok":1,"checked":1}\n')
            rc, o, _ = self.call(shell, 'sms.forward_set', req, MU300_API_CHECK='1')
            self.assertEqual(o, {'ok': 1, 'checked': True})
            self.assertEqual((self.tmp / 'fwdcheck').read_text().strip(), '1')
            (self.tmp / 'fwdout').write_text('{"ok":1}\n')
            self.refused(shell, 'sms.forward_set', {'nope': '1'}, 'unknown-field', 'nope')
            self.refused(shell, 'sms.forward_set', {'enabled': 'yes'}, 'bad-value', 'enabled')
            self.refused(shell, 'sms.forward_set', {'template': 5}, 'bad-value', 'template')
            # a control character is refused when the request is read (json.awk)
            self.refused(shell, 'sms.forward_set', {'template': 'a\u007fb'}, 'bad-request')
            self.refused(shell, 'sms.forward_set', {'webhook_body': 'x' * 4097}, 'bad-value', 'webhook_body')
            (self.tmp / 'fwdout').write_text('{"ok":1,"started":1}\n')
            self.assertEqual(self.ok(shell, 'sms.forward_test'), {'ok': 1, 'started': True})
            self.assertEqual(self.calls('forward')[-1], ['forward', 'test'])
            (self.tmp / 'fwdout').write_text('{"ok":1}\n')
            self.ok(shell, 'sms.forward_log_clear')
            self.assertEqual(self.calls('forward')[-1], ['forward', 'logclear'])

    def test_at_history(self):
        for shell in self.each_shell():
            self.out('mu300-at', 'OK\n')
            for c in ('AT+CSQ', 'at+cpin="1234"', 'AT+CGAUTH=1,1,"user","pw"', 'ATI'):
                self.ok(shell, 'at.send', {'command': c})
            h = self.ok(shell, 'at.history')['history']
            self.assertEqual([x['command'] for x in h], ['ATI', 'AT+CGAUTH=...', 'at+cpin=...', 'AT+CSQ'])
            self.assertRegex(h[0]['time'], r'^[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')
            self.assertNotIn('1234', (self.dash / 'at-history').read_text())
            for _ in range(45):
                self.ok(shell, 'at.send', {'command': 'AT'})
            self.assertEqual(len(self.ok(shell, 'at.history')['history']), 40)
            (self.dash / 'at-history').unlink()
            self.assertEqual(self.ok(shell, 'at.history')['history'], [])

    def test_sim_state(self):
        self.tool('mu300-sim-state', 'echo "$1" >> "$STUBLOG/simstate"; case $1 in get) cat "$STUBLOG/kept" 2>/dev/null ;; check) echo absent ;; esac')
        self.out('mu300-sim', '0\n')
        for shell in self.each_shell():
            (self.tmp / 'simstate').unlink(missing_ok=True)
            (self.tmp / 'kept').write_text('ready\n')
            self.assertEqual(self.ok(shell, 'sim.state'), {'ok': 1, 'state': 'ready', 'slot': 1})
            self.assertEqual((self.tmp / 'simstate').read_text().split(), ['get'])
            (self.tmp / 'kept').unlink()
            self.assertEqual(self.ok(shell, 'sim.state')['state'], 'absent')
            self.assertEqual((self.tmp / 'simstate').read_text().split(), ['get', 'get', 'check'])
            (self.tmp / 'kept').write_text('ready\n')
            self.assertEqual(self.ok(shell, 'sim.state', {'fresh': True})['state'], 'absent')
            self.assertEqual((self.tmp / 'simstate').read_text().split()[-1], 'check')
        (self.bin / 'mu300-sim-state').unlink()
        for shell in self.each_shell():
            self.refused(shell, 'sim.state', {}, 'unsupported')

    def test_usb_network_mode(self):
        (self.bin / 'mu300-usb').write_text((BIN / 'mu300-usb').read_text())
        for shell in self.each_shell():
            for f in ('usb-net', 'usb-net.once'):
                (self.root / 'etc/mu300' / f).unlink(missing_ok=True)
            self.assertEqual(self.ok(shell, 'usb.net'), {'ok': 1, 'mode': 'default', 'once': False, 'now': None})
            self.assertEqual(self.ok(shell, 'usb.net', {'mode': 'ecm', 'scope': 'once'})['mode'], 'ecm')
            self.assertEqual((self.root / 'etc/mu300/usb-net').read_text(), 'ecm\n')
            self.assertTrue((self.root / 'etc/mu300/usb-net.once').exists())
            (self.root / 'run/mu300').mkdir(parents=True, exist_ok=True)
            (self.root / 'run/mu300/usb-net-applied').write_text('ecm\n')
            self.assertEqual(self.ok(shell, 'usb.net'), {'ok': 1, 'mode': 'ecm', 'once': True, 'now': 'ecm'})
            # at the next boot the one-boot choice is used up (mu300-usb boot, mu300-fixups)
            r = self.script(shell, self.bin / 'mu300-usb', 'boot', MU300_SYSROOT=self.root, PATH=f'{self.bin}:{self.env()["PATH"]}')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((self.root / 'etc/mu300/usb-net').exists())
            self.assertFalse((self.root / 'etc/mu300/usb-net.once').exists())
            self.ok(shell, 'usb.net', {'mode': 'rndis'})
            self.assertEqual(self.ok(shell, 'usb.net')['mode'], 'rndis')
            self.assertFalse((self.root / 'etc/mu300/usb-net.once').exists())
            self.ok(shell, 'usb.net', {'mode': 'default'})
            self.assertFalse((self.root / 'etc/mu300/usb-net').exists())
            for bad in ({'mode': 'ppp'}, {'mode': 'ncm', 'scope': 'twice'}, {'mode': '-x'}):
                self.refused(shell, 'usb.net', bad, 'bad-value')
            (self.root / 'run/mu300/usb-net-applied').unlink()


class ModemTools(ApiCase):
    """The methods of Cockpit's MU300 page (the LuCI panel's pages, through the device's own tools): the AT terminal,
    USSD, TTL, data usage, power, LEDs, zram, CPU, updates, packages, Docker"""

    def setUp(self):
        super().setUp()
        for name in ('mu300-ttl', 'mu300-power', 'mu300-cpu', 'mu300-zram', 'mu300-led', 'mu300-ussd', 'mu300-user-packages'):
            self.tool(name)
        (self.stubs / 'docker').write_text(RECORD)
        (self.stubs / 'docker').chmod(0o755)

    def test_at_command_on_stdin(self):
        for shell in self.each_shell():
            self.out('mu300-at', '+CSQ: 20,99\r\nOK\r\n')
            o = self.ok(shell, 'at.send', {'command': '  AT+CSQ  '})
            self.assertEqual(o['text'], '+CSQ: 20,99\nOK')
            self.assertEqual(self.calls('mu300-at')[-1], ['mu300-at', '-t', '10', '-'])
            self.assertEqual((self.tmp / 'stdin.mu300-at').read_text(), 'AT+CSQ\n')
            self.ok(shell, 'at.send', {'command': 'at+cgdcont="1","IP","x;y"', 'timeout': 30})
            self.assertEqual(self.calls('mu300-at')[-1], ['mu300-at', '-t', '30', '-'])
            for bad in ('-t 99 AT', 'ATI\nAT+CFUN=0', 'reboot', '', 'AT' + 'x' * 511, 'ATç'):
                self.refused(shell, 'at.send', {'command': bad}, 'bad-value', 'command')
            self.refused(shell, 'at.send', {'command': 'ATI', 'timeout': 61}, 'bad-value', 'timeout')
            # an ERROR of the modem is an answer, with mu300-at's status
            self.out('mu300-at', 'ERROR\n', rc=1)
            self.assertEqual(self.ok(shell, 'at.send', {'command': 'AT+X'})['rc'], 1)

    def test_ussd(self):
        for shell in self.each_shell():
            self.out('mu300-ussd', 'Balance: 10 TL')
            self.assertEqual(self.ok(shell, 'ussd.send', {'code': '*100#'})['text'], 'Balance: 10 TL')
            self.assertEqual(self.calls('mu300-ussd')[-1], ['mu300-ussd', '-t', '45', '*100#'])
            self.ok(shell, 'ussd.cancel')
            self.assertEqual(self.calls('mu300-ussd')[-1], ['mu300-ussd', '--cancel'])
            for bad in ('-t', '*100#;reboot', '', '1 2'):
                self.refused(shell, 'ussd.send', {'code': bad}, 'bad-value', 'code')

    def test_modem_restart(self):
        for shell in self.each_shell():
            self.ok(shell, 'modem.restart')
            self.assertEqual(self.calls('later')[-1][-2:], ['restart', 'mu300-mobile-data.service'])

    def test_ttl(self):
        for shell in self.each_shell():
            self.out('mu300-ttl', 'TTL=64\nBACKEND=tc\nIFACES=sipa_eth0\nOFFLOAD=\n')
            self.assertEqual(self.ok(shell, 'ttl.get'), {'ok': 1, 'enabled': True, 'value': 64, 'backend': 'tc'})
            self.out('mu300-ttl', 'TTL=\nBACKEND=weird\n')
            self.assertEqual(self.ok(shell, 'ttl.get'), {'ok': 1, 'enabled': False, 'value': None, 'backend': 'none'})
            self.ok(shell, 'ttl.set', {'value': '65'})
            self.assertEqual(self.calls('mu300-ttl')[-1], ['mu300-ttl', 'set', '65'])
            self.ok(shell, 'ttl.set', {'value': 'off'})
            self.assertEqual(self.calls('mu300-ttl')[-1], ['mu300-ttl', 'off'])
            for bad in ('0', '256', '064', '-1', 'on'):
                self.refused(shell, 'ttl.set', {'value': bad}, 'bad-value', 'value')

    def test_traffic(self):
        for shell in self.each_shell():
            self.out('mu300-traffic', '{"today":{"rx":1,"tx":2}}\n')
            self.assertEqual(self.ok(shell, 'traffic.get')['traffic'], {'today': {'rx': 1, 'tx': 2}})
            for req, argv in (({'key': 'reset_day', 'value': 15}, ['set', 'reset-day', '15']),
                              ({'key': 'cap', 'value': '53687091200'}, ['set', 'cap', '53687091200']),
                              ({'key': 'cap', 'value': '0'}, ['set', 'cap', 'off']),
                              ({'key': 'warn', 'value': 90}, ['set', 'warn', '90']),
                              ({'key': 'cut', 'value': 'on'}, ['set', 'cut', 'on']),
                              ({'key': 'used', 'value': '1000'}, ['used', '1000']),
                              ({'key': 'clear', 'value': 'yes'}, ['clear', '--yes'])):
                self.ok(shell, 'traffic.set', req)
                self.assertEqual(self.calls('mu300-traffic')[-1], ['mu300-traffic'] + argv, req)
            for req in ({'key': 'reset_day', 'value': 32}, {'key': 'cap', 'value': '-1'}, {'key': 'cut', 'value': 'x'},
                        {'key': 'clear', 'value': 'no'}, {'key': 'device', 'value': 'eth0'}):
                self.assertEqual(self.call(shell, 'traffic.set', req)[1]['error'], 'bad-value', req)
            self.out('mu300-traffic', 'nope\n')
            self.refused(shell, 'traffic.get', {}, 'failed')

    def test_power(self):
        status = ('state: active\nprofile: battery (automatic)\nsupply: battery\nbattery: 80 %\n'
                  'SAVER_BELOW=20 CHARGE_TO=80\nPOWER_ON_CHARGER=1\nplugged: WIFI_IDLE=0 RADIO_IDLE=keep LEDS_IDLE=on CPU=full\n'
                  'battery: WIFI_IDLE=10 RADIO_IDLE=lte LEDS_IDLE=off CPU=eco\nsaver: WIFI_IDLE=5 RADIO_IDLE=off LEDS_IDLE=off CPU=eco\n')
        (self.root / 'etc/mu300/power.conf').write_text('PROFILE=saver\n')
        for shell in self.each_shell():
            self.out('mu300-power', status)
            o = self.ok(shell, 'power.get')
            self.assertEqual((o['state'], o['profile'], o['why'], o['supply']), ('active', 'battery', 'automatic', 'battery'))
            self.assertEqual(o['conf']['PROFILE'], 'saver')
            self.assertEqual((o['conf']['SAVER_BELOW'], o['conf']['CHARGE_TO']), (20, 80))
            self.assertEqual(o['conf']['POWER_ON_CHARGER'], 1)
            self.assertEqual(o['conf']['battery'], {'WIFI_IDLE': 10, 'RADIO_IDLE': 'lte', 'LEDS_IDLE': 'off', 'CPU': 'eco'})
            for req, argv in (({'key': 'PROFILE', 'value': 'auto'}, ['profile', 'auto']),
                              ({'key': 'CHARGE_TO', 'value': 80}, ['set', 'CHARGE_TO', '80']),
                              ({'key': 'POWER_ON_CHARGER', 'value': 0}, ['set', 'POWER_ON_CHARGER', '0']),
                              ({'key': 'saver.RADIO_IDLE', 'value': 'off'}, ['set', 'saver.RADIO_IDLE', 'off']),
                              ({'key': 'battery.WIFI_IDLE', 'value': 15}, ['set', 'battery.WIFI_IDLE', '15'])):
                self.ok(shell, 'power.set', req)
                self.assertEqual(self.calls('mu300-power')[-1], ['mu300-power'] + argv)
            for req in ({'key': 'PROFILE', 'value': 'x'}, {'key': 'FOO', 'value': 1}, {'key': 'CHARGE_TO', 'value': 101},
                        {'key': 'POWER_ON_CHARGER', 'value': 2},
                        {'key': 'battery.CPU', 'value': 'turbo'}):
                self.assertEqual(self.call(shell, 'power.set', req)[1]['error'], 'bad-value', req)

    def test_leds_and_zram(self):
        for shell in self.each_shell():
            (self.root / 'etc/mu300/led.conf').unlink(missing_ok=True)
            self.assertEqual(self.ok(shell, 'led.get'), {'ok': 1, 'timeout': None, 'device': 'f50'})
            self.ok(shell, 'led.set', {'timeout': 120})
            self.assertEqual((self.root / 'etc/mu300/led.conf').read_text(), 'LED_TIMEOUT=120\n')
            self.assertEqual(self.ok(shell, 'led.get')['timeout'], 120)
            self.clear_calls()
            self.ok(shell, 'led.set', {'test': True})
            self.assertEqual(self.calls('later')[-1][-1], 'test')
            self.refused(shell, 'led.set', {}, 'missing', 'timeout')
            self.refused(shell, 'led.set', {'timeout': -1}, 'bad-value', 'timeout')
            self.out('mu300-zram', 'zram swap: on, 700 MiB, lz4, priority 100\n  empty\n')
            o = self.ok(shell, 'zram.get')
            self.assertEqual((o['active'], o['enabled']), (True, True))
            self.out('mu300-zram', 'zram swap: off (/etc/mu300/zram.conf: ZRAM_ENABLE=0; mu300-zram on)\n')
            o = self.ok(shell, 'zram.get')
            self.assertEqual((o['active'], o['enabled']), (False, False))
            self.ok(shell, 'zram.set', {'state': 'on'})
            self.assertEqual(self.calls('mu300-zram')[-1], ['mu300-zram', 'on'])
            self.refused(shell, 'zram.set', {'state': 'stop'}, 'bad-value', 'state')

    def test_cpu(self):
        for shell in self.each_shell():
            self.out('mu300-cpu', '{"profile":"balanced"}\n')
            o = self.ok(shell, 'cpu.get')
            self.assertEqual((o['cpu'], o['voltage']), ({'profile': 'balanced'}, {'profile': 'balanced'}))
            self.ok(shell, 'cpu.profile', {'name': 'saving'})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'profile', 'saving'])
            self.refused(shell, 'cpu.profile', {'name': 'turbo'}, 'bad-value', 'name')
            # the voltage offset: never without the word
            self.refused(shell, 'cpu.voltage', {'op': 'set', 'offsets': '-3125,0,0'}, 'missing', 'confirm')
            self.refused(shell, 'cpu.voltage', {'op': 'set', 'offsets': '-3125,0,0', 'confirm': 'yes'}, 'bad-value', 'confirm')
            self.refused(shell, 'cpu.voltage', {'op': 'set', 'offsets': '1,2', 'confirm': 'EXPERIMENTAL'}, 'bad-value', 'offsets')
            self.ok(shell, 'cpu.voltage', {'op': 'set', 'offsets': '-3125,0,0', 'confirm': 'EXPERIMENTAL'})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'voltage', 'set', '-3125,0,0', '--confirm', 'EXPERIMENTAL'])
            self.ok(shell, 'cpu.voltage', {'op': 'off'})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'voltage', 'off'])
            self.ok(shell, 'cpu.voltage', {'op': 'stress', 'minutes': 10})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'voltage', 'stress', '10', '--background'])
            self.refused(shell, 'cpu.voltage', {'op': 'stress', 'minutes': 121}, 'bad-value', 'minutes')
            # a core and CPU0's low latency (FINDINGS 200); never CPU0
            self.ok(shell, 'cpu.core', {'cpu': 7, 'state': 'off'})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'core', '7', 'off'])
            self.refused(shell, 'cpu.core', {'cpu': 0, 'state': 'off'}, 'bad-value', 'cpu')
            self.refused(shell, 'cpu.core', {'cpu': 7, 'state': 'down'}, 'bad-value', 'state')
            self.ok(shell, 'cpu.lowlatency', {'state': 'on'})
            self.assertEqual(self.calls('mu300-cpu')[-1], ['mu300-cpu', 'lowlatency', 'on'])
            self.refused(shell, 'cpu.lowlatency', {'state': '1'}, 'bad-value', 'state')

    def test_update_status_and_source(self):
        for shell in self.each_shell():
            self.out('mu300-update', '{"systems":[]}\n')
            o = self.ok(shell, 'update.status')
            self.assertEqual(o['status'], {'systems': []})
            self.ok(shell, 'update.source', {'url': 'https://mirror.example.com/mu300'})
            self.assertEqual(self.calls('mu300-update')[-1], ['mu300-update', 'source', 'https://mirror.example.com/mu300'])
            self.ok(shell, 'update.source', {'url': 'off'})
            self.assertEqual(self.calls('mu300-update')[-1], ['mu300-update', 'source', 'off'])
            for bad in ('ftp://x', '-x', 'https://a b', 'https://x/\n'):
                self.refused(shell, 'update.source', {'url': bad}, 'bad-value' if bad != 'https://x/\n' else 'bad-value', 'url')

    def test_packages_and_docker(self):
        for shell in self.each_shell():
            self.out('mu300-user-packages', 'nothing written down')
            self.assertEqual(self.ok(shell, 'packages.get')['text'], 'nothing written down')
            self.ok(shell, 'packages.act', {'op': 'dismiss'})
            self.assertEqual(self.calls('mu300-user-packages')[-1], ['mu300-user-packages', 'dismiss'])
            self.ok(shell, 'job.clear')
            self.assertEqual(self.ok(shell, 'packages.act', {'op': 'retry'}), {'ok': 1, 'job': 'packages-retry'})
            self.wait_job(shell)
            self.refused(shell, 'packages.act', {'op': 'run'}, 'bad-value', 'op')
            self.out('docker', 'web\tnginx:1\trunning\tUp 2 hours\ndb "x"\tpg\texited\tExited (0)\n')
            self.out('systemctl', 'active\n')
            o = self.ok(shell, 'docker.status')
            self.assertEqual(o['containers'][1], {'name': 'db "x"', 'image': 'pg', 'state': 'exited', 'status': 'Exited (0)'})
            self.assertEqual((o['installed'], o['active'], len(o['containers'])), (True, 'active', 2))
            (self.stubs / 'docker').unlink()
            # without Docker (a machine that has one of its own on /usr/bin cannot show this)
            if not shutil.which('docker', path='/usr/bin:/bin'):
                self.assertEqual(self.ok(shell, 'docker.status', PATH=f'{self.stubs}:/usr/bin:/bin'), {'ok': 1, 'installed': False})
            (self.stubs / 'docker').write_text(RECORD)
            (self.stubs / 'docker').chmod(0o755)


if __name__ == '__main__':
    unittest.main()
