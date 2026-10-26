"""mu300-backup (FINDINGS 155): the user's settings as one file and back. Export reads the settings where they live
(hotspot.conf or UCI, mobile-data.conf, the VPN module, the lock tool's directory, the panel's SMS forwarding file,
the login user's keys and hash), import checks every value by the rules of the setter that takes it (mu300-api's
check mode, the forwarding adapter's, the module's own) and changes nothing when one is refused; a file of another
device type is refused without --force; the file is 0600 and, with a passphrase, sealed. Hostile files: unknown
keys, wrong kinds, oversize, control characters, an engine binary's path, options in front of an SSH key, a hash
another C library cannot read, a tampered seal. Secrets never on a command line or in what is printed. Under each
shell, on the fake device of test_api."""
import json
import os
import shutil
import stat
import subprocess
import unittest

from helpers import BIN, TOP
from test_api import ApiCase
from test_mu300dash_security import JSONFILTER

BACKUP = BIN / 'mu300-backup'
ADAPTER = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/forward'
HASH = '$6$saltsalt$' + 'a' * 86
KEY1 = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOm' + 'x' * 40 + ' me@laptop'
KEY2 = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ' + 'y' * 60 + ' other'
VLESS = 'vless://3f6a1c2e-0000-4000-8000-123456789abc@203.0.113.7:443?security=reality#vpnlinksecret'
SUB_URL = 'https://sub.example.com/api/subtokensecret'

# the VPN module's CLI as mu300-backup uses it; its store in $STUBLOG/vpn.json, every argument list recorded
FAKE_VPN = r'''
import json, os, sys
log = os.environ['STUBLOG']
p = os.path.join(log, 'vpn.json')
db = json.load(open(p)) if os.path.exists(p) else {'profiles': [], 'subs': [], 'settings': {}, 'active': None}
a = sys.argv[1:]
with open(os.path.join(log, 'calls'), 'a') as f:
    f.write('\x1f'.join(['mu300-vpn'] + a) + '\n')
def save(): json.dump(db, open(p, 'w'))
def prof(i): return next(x for x in db['profiles'] if x['id'] == i)
if a[:2] == ['profile', 'list']:
    print(json.dumps([{'id': x['id'], 'name': x['name'], 'type': x['type'], 'active': x['id'] == db['active'],
                       'source': x.get('source', 'manual'), 'subscription': x.get('sub'), 'options': {}} for x in db['profiles']]))
elif a[:2] == ['profile', 'show']:
    x = prof(a[2])
    o = {'id': x['id'], 'name': x['name'], 'type': x['type'],
         'options': {k: ('set' if 'PASS' in k else v) for k, v in x.get('options', {}).items()}}
    if '--reveal' in a:
        o['config'] = x['config']
    print(json.dumps(o))
elif a[:2] == ['profile', 'add']:
    cfg = sys.stdin.read()
    if 'reject-me' in cfg:
        sys.stderr.write('profile not added\n'); sys.exit(1)
    i = 'p-%d' % (len(db['profiles']) + 1)
    db['profiles'].append({'id': i, 'type': a[2], 'name': a[3], 'config': cfg, 'options': {}})
    os.makedirs(os.path.join(os.environ['VPN_STORE'], 'profiles', i), exist_ok=True)
    save(); print(i)
elif a[:2] == ['profile', 'edit']:
    prof(a[2])['config'] = sys.stdin.read(); save()
elif a[:2] == ['profile', 'set']:
    prof(a[2]).setdefault('options', {})[a[3]] = a[4]; save()
elif a[:2] == ['profile', 'use']:
    db['active'] = a[2]; save()
elif a[:2] == ['subscription', 'list']:
    print(json.dumps([{'id': s['id'], 'name': s['name']} for s in db['subs']]))
elif a[:2] == ['subscription', 'show']:
    s = next(s for s in db['subs'] if s['id'] == a[2])
    print(json.dumps(dict(s, url=s['url']) if '--reveal' in a else {k: v for k, v in s.items() if k != 'url'}))
elif a[:2] == ['subscription', 'add']:
    url = sys.stdin.read().strip()
    t = a[a.index('--type') + 1]
    iv = int(a[a.index('--interval') + 1]) if '--interval' in a else None
    db['subs'].append({'id': 's-%d' % (len(db['subs']) + 1), 'name': a[2], 'url': url, 'type': t, 'interval': iv,
                       'insecure': '--insecure' in a}); save()
elif a == ['settings', '--json']:
    print(json.dumps(db['settings']))
elif a[:2] == ['settings', 'set']:
    db['settings'][a[2]] = a[3]; save()
elif a[0] == 'killswitch':
    db['settings']['KILL_SWITCH'] = '1' if a[1] == 'on' else '0'; save()
elif a[0] in ('on', 'off'):
    open(os.environ['VPN_CONF'], 'w').write('ENABLE=%d\n' % (a[0] == 'on'))
else:
    sys.exit(2)
'''


class BackupCase(ApiCase):
    def setUp(self):
        super().setUp()
        (self.stubs / 'vpn.py').write_text(FAKE_VPN)
        (self.root / 'etc/mu300/vpn').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/shadow').write_text(f'root:*:19000:0:99999:7:::\nubuntu:{HASH}:19000:0:99999:7:::\n')
        (self.root / 'etc/passwd').write_text(f'root:x:0:0::/root:/bin/sh\nubuntu:x:{os.getuid()}:{os.getgid()}::/home/ubuntu:/bin/bash\n')
        if self.system == 'ubuntu':
            (self.root / 'etc/mu300/mobile-data.conf').write_text('MU300_APN=internet\nMU300_APN_USER=apnuser\nMU300_APN_PASSWORD=apnsecret7\n')
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.77.1\n')
            (self.root / 'etc/hostname').write_text('mu300\n')
            os.symlink('/usr/share/zoneinfo/Europe/Istanbul', self.root / 'etc/localtime')
            (self.root / 'home/ubuntu/.ssh').mkdir(parents=True)
            (self.root / 'home/ubuntu/.ssh/authorized_keys').write_text(KEY1 + '\ncommand="rm -rf /" ' + KEY2 + '\n')
        self.out('mu300-sim', '0\n')
        self.out('mobile-data', '{"allowed":false,"held":false}\n')
        self.tool('mobile-data')
        self.tool('busybox')

    def fresh(self):
        """the fake device as setUp made it (each shell starts from the same one)"""
        self.tearDown()
        self.setUp()

    def vpn(self, db=None, enabled=False):
        self.stub('mu300-vpn-real', f'exec python3 "{self.stubs}/vpn.py" "$@"')
        self.tool('mu300-vpn', f'exec "{self.stubs}/mu300-vpn-real" "$@"')
        (self.root / 'etc/mu300/vpn.conf').write_text(f'ENABLE={int(enabled)}\n')
        (self.tmp / 'vpn.json').write_text(json.dumps(db or {'profiles': [], 'subs': [], 'settings': {}, 'active': None}))

    def env(self, **extra):
        e = super().env(**extra)
        e.update(VPN_STORE=str(self.root / 'etc/mu300/vpn'), VPN_CONF=str(self.root / 'etc/mu300/vpn.conf'),
                 MU300_BACKUP_ITER='10000')
        return e

    def backup(self, shell, *args, stdin=None, **env):
        return subprocess.run(shell + [str(BACKUP)] + [str(a) for a in args], input=stdin, capture_output=True,
                              text=True, env=self.env(**env), cwd=self.tmp, timeout=300)

    def export(self, shell, *args, **env):
        out = self.tmp / 'b.json'
        out.unlink(missing_ok=True)
        r = self.backup(shell, 'export', '-o', out, *args, **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return out

    def doc(self, sections, device='f50', system='ubuntu'):
        return {'format': 'mu300-backup', 'version': 1, 'created': 1791000000,
                'source': {'device': device, 'system': system, 'release': 'v2026.10.16.1', 'hostname': 'h'},
                'sections': sections}

    def write(self, doc):
        p = self.tmp / 'in.json'
        p.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
        return p

    def argv_text(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''


class ExportUbuntu(BackupCase):
    def test_every_section_and_a_private_file(self):
        for shell in self.each_shell():
            self.fresh()
            self.vpn({'profiles': [{'id': 'work', 'name': 'Work', 'type': 'xray', 'config': VLESS, 'options': {}},
                                   {'id': 'sub-1', 'name': 'From sub', 'type': 'xray', 'config': 'x', 'source': 'subscription', 'sub': 's-1'}],
                      'subs': [{'id': 's-1', 'name': 'Provider', 'url': SUB_URL, 'type': 'auto', 'interval': 24, 'insecure': False}],
                      'settings': {'KILL_SWITCH': '1', 'IPV6': 'auto', 'XRAY': '/tmp/evil'}, 'active': 'work'}, enabled=True)
            r = self.backup(shell, 'export', '-o', self.tmp / 'b.json', '--with-password')
            self.assertEqual(r.returncode, 0, r.stderr)
            f = self.tmp / 'b.json'
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            d = json.loads(f.read_text())
            s = d['sections']
            self.assertEqual((d['format'], d['version'], d['source']['device'], d['source']['system']),
                             ('mu300-backup', 1, 'f50', 'ubuntu'))
            self.assertEqual(s['wifi'], {'ssid': 'MU300-AB12', 'password': 'hotspotsecret1', 'band': '5', 'channel': 'auto',
                                         'hidden': False, 'enabled': True})
            self.assertEqual(s['data'], {'apn': 'internet', 'pdp': 'IP', 'auth': '', 'user': 'apnuser',
                                         'password': 'apnsecret7', 'roaming': False})
            self.assertEqual(s['sim'], {'default': 1})
            self.assertEqual(s['lan'], {'ip': '192.168.77.1'})
            self.assertEqual(s['system'], {'hostname': 'mu300', 'timezone': 'Europe/Istanbul', 'ssh': True})
            self.assertEqual(s['ssh_keys'], [KEY1])        # the line with options in front is left out, and said so
            self.assertIn('ssh_keys: 1 line(s)', r.stderr)
            self.assertEqual(s['password'], {'hash': HASH})
            self.assertNotIn('locks', s)                   # Ubuntu has none
            self.assertNotIn('sms_forward', s)
            v = s['vpn']
            self.assertEqual((v['enabled'], v['active']), (True, 'Work'))
            self.assertEqual(v['profiles'], [{'name': 'Work', 'type': 'xray', 'config': VLESS, 'options': {}}])
            self.assertEqual(v['subscriptions'], [{'name': 'Provider', 'url': SUB_URL, 'type': 'auto', 'interval': 24, 'insecure': False}])
            self.assertEqual(v['settings'], {'KILL_SWITCH': '1', 'IPV6': 'auto'})   # never an engine's path
            # nothing secret printed or on a command line
            for secret in ('hotspotsecret1', 'apnsecret7', 'vpnlinksecret', 'subtokensecret', HASH):
                self.assertNotIn(secret, r.stdout + r.stderr)
                self.assertNotIn(secret, self.argv_text())
            # without --with-password: no hash
            self.assertNotIn('password', json.loads(self.export(shell).read_text())['sections'])

    def test_default_name_and_never_through_a_link(self):
        for shell in self.each_shell():
            self.fresh()
            for f in self.tmp.glob('mu300-backup-*'):
                f.unlink()
            r = self.backup(shell, 'export', '--only', 'wifi,lan')
            self.assertEqual(r.returncode, 0, r.stderr)
            made = list(self.tmp.glob('mu300-backup-*.json'))
            self.assertEqual(len(made), 1)
            self.assertEqual(stat.S_IMODE(made[0].stat().st_mode), 0o600)
            self.assertEqual(sorted(json.loads(made[0].read_text())['sections']), ['lan', 'wifi'])
            target = self.tmp / 'elsewhere'
            target.write_text('keep\n')
            link = self.tmp / 'link.json'
            link.unlink(missing_ok=True)
            link.symlink_to(target)
            r = self.backup(shell, 'export', '-o', link)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(target.read_text(), 'keep\n')
            self.assertEqual(self.backup(shell, 'export', '--only', 'wifi,nonsense').returncode, 2)


class Encryption(BackupCase):
    def test_sealed_with_a_passphrase_and_refused_when_wrong_or_changed(self):
        for shell in self.each_shell():
            self.fresh()
            pw = self.tmp / 'pass'
            pw.write_text('correct horse battery\n')
            out = self.export(shell, '--passphrase-file', pw)
            text = out.read_text()
            self.assertTrue(text.startswith('MU300-BACKUP-ENCRYPTED 1\ncipher=aes-256-cbc kdf=pbkdf2 digest=sha256 iter=10000\n'))
            for secret in ('hotspotsecret1', 'apnsecret7', 'MU300-AB12'):
                self.assertNotIn(secret, text)
            r = self.backup(shell, 'show', out, '--passphrase-file', pw)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('from: f50, ubuntu', r.stdout)
            self.assertNotIn('hotspotsecret1', r.stdout)
            # the passphrase on stdin, the file after it
            r = self.backup(shell, 'show', '-', '--passphrase-stdin', stdin='correct horse battery\n' + text)
            self.assertEqual(r.returncode, 0, r.stderr)
            wrong = self.tmp / 'wrong'
            wrong.write_text('not the right one\n')
            r = self.backup(shell, 'import', out, '--passphrase-file', wrong)
            self.assertEqual(r.returncode, 1)
            self.assertIn('wrong passphrase', r.stderr)
            # one character of the sealed part changed
            lines = text.splitlines()
            body = lines[3]
            lines[3] = body[:10] + ('A' if body[10] != 'A' else 'B') + body[11:]
            bad = self.tmp / 'bad.enc'
            bad.write_text('\n'.join(lines) + '\n')
            r = self.backup(shell, 'import', bad, '--passphrase-file', pw)
            self.assertEqual(r.returncode, 1, r.stdout)
            # rounds from a hostile header are bounded
            lines = text.splitlines()
            lines[1] = 'cipher=aes-256-cbc kdf=pbkdf2 digest=sha256 iter=999999999'
            bad.write_text('\n'.join(lines) + '\n')
            self.assertEqual(self.backup(shell, 'import', bad, '--passphrase-file', pw).returncode, 2)
            short = self.tmp / 'short'
            short.write_text('short\n')
            self.assertEqual(self.backup(shell, 'export', '-o', self.tmp / 'x.enc', '--passphrase-file', short).returncode, 1)
            self.assertNotIn('correct horse', self.argv_text())


class ImportUbuntu(BackupCase):
    def test_dry_run_changes_nothing_and_import_sets_what_differs(self):
        for shell in self.each_shell():
            self.fresh()
            hs = self.root / 'etc/mu300/hotspot.conf'
            hs.write_text('SSID=MU300-AB12\nPSK=hotspotsecret1\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n')
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.77.1\n')
            keys = self.root / 'home/ubuntu/.ssh/authorized_keys'
            keys.write_text(KEY1 + '\n')
            new = self.write(self.doc({
                'wifi': {'ssid': 'Home net', 'password': 'newsecret99', 'band': '2.4', 'channel': '6', 'hidden': False, 'enabled': True},
                'data': {'apn': 'internet', 'pdp': 'IP', 'auth': '', 'user': 'apnuser', 'password': 'apnsecret7', 'roaming': True},
                'lan': {'ip': '192.168.50.1'},
                'system': {'hostname': 'mu300', 'timezone': 'Europe/Istanbul', 'ssh': True},
                'ssh_keys': [KEY1, KEY2],
                'password': {'hash': HASH.replace('a' * 86, 'b' * 86)}}))
            self.clear_calls()
            r = self.backup(shell, 'import', new, '--dry-run')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('would  hotspot: ssid "MU300-AB12" -> "Home net", the password changes, band "5" -> "2.4", channel "auto" -> "6"', r.stdout)
            self.assertIn('would  data roaming: allowed', r.stdout)
            self.assertIn('would  LAN address: "192.168.77.1" -> "192.168.50.1"', r.stdout)
            self.assertIn('would  SSH keys: 1 to add', r.stdout)
            self.assertIn('would  login password: changes', r.stdout)
            self.assertNotIn('mobile data:', r.stdout)            # the same
            self.assertNotIn('host name', r.stdout)
            self.assertIn('dry run: nothing was changed', r.stdout)
            self.assertIn('SSID=MU300-AB12', hs.read_text())
            self.assertEqual(keys.read_text(), KEY1 + '\n')
            self.assertEqual(self.calls('later'), [])
            self.assertEqual(self.calls('chpasswd'), [])
            r = self.backup(shell, 'import', new)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('restored', r.stdout)
            t = hs.read_text()
            for want in ('SSID=Home net', 'PSK=newsecret99', 'BAND=2.4', 'CHANNEL=6'):
                self.assertIn(want, t)
            self.assertEqual(stat.S_IMODE(hs.stat().st_mode), 0o600)
            self.assertIn('LAN_IP=192.168.50.1', (self.root / 'etc/mu300/lan.conf').read_text())
            self.assertEqual(keys.read_text(), KEY1 + '\n' + KEY2 + '\n')
            self.assertEqual(stat.S_IMODE(keys.stat().st_mode), 0o600)
            self.assertIn(['mobile-data', 'roaming', 'on'], self.calls('mobile-data'))
            self.assertEqual((self.tmp / 'stdin.chpasswd').read_text(), 'ubuntu:' + HASH.replace('a' * 86, 'b' * 86) + '\n')
            self.assertEqual(self.calls('chpasswd')[-1], ['chpasswd', '-e'])
            for secret in ('newsecret99', 'apnsecret7', 'b' * 86):
                self.assertNotIn(secret, r.stdout + r.stderr)
                self.assertNotIn(secret, self.argv_text())
            # a second import: nothing to change
            r = self.backup(shell, 'import', new, '--skip', 'password,data')   # (the stub's roaming stays off)
            self.assertIn('nothing to change', r.stdout, r.stdout + r.stderr)

    def test_a_round_trip(self):
        for shell in self.each_shell():
            self.fresh()
            self.vpn({'profiles': [{'id': 'work', 'name': 'Work', 'type': 'xray', 'config': VLESS, 'options': {}}],
                      'subs': [{'id': 's-1', 'name': 'Provider', 'url': SUB_URL, 'type': 'auto', 'interval': 24, 'insecure': False}],
                      'settings': {'KILL_SWITCH': '1', 'IPV6': 'auto'}, 'active': 'work'}, enabled=True)
            pw = self.tmp / 'pass'
            pw.write_text('correct horse battery\n')
            out = self.export(shell, '--passphrase-file', pw)
            # everything changed afterwards ...
            (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=Other\nPSK=othersecret1\nBAND=2.4\nCHANNEL=1\nCOUNTRY=TR\n')
            (self.root / 'etc/mu300/mobile-data.conf').write_text('MU300_APN=other.apn\n')
            self.vpn()
            # ... and back
            r = self.backup(shell, 'import', out, '--passphrase-file', pw)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            t = (self.root / 'etc/mu300/hotspot.conf').read_text()
            for want in ('SSID=MU300-AB12', 'PSK=hotspotsecret1', 'BAND=5', 'CHANNEL=auto'):
                self.assertIn(want, t)
            md = (self.root / 'etc/mu300/mobile-data.conf').read_text()
            for want in ('MU300_APN=internet', 'MU300_APN_USER=apnuser', 'MU300_APN_PASSWORD=apnsecret7'):
                self.assertIn(want, md)
            db = json.loads((self.tmp / 'vpn.json').read_text())
            self.assertEqual([(p['name'], p['type'], p['config']) for p in db['profiles']], [('Work', 'xray', VLESS)])
            self.assertEqual(db['active'], db['profiles'][0]['id'])
            self.assertEqual([(s['name'], s['url'], s['interval']) for s in db['subs']], [('Provider', SUB_URL, 24)])
            self.assertEqual(db['settings'], {'KILL_SWITCH': '1', 'IPV6': 'auto'})
            self.assertEqual((self.root / 'etc/mu300/vpn.conf').read_text(), 'ENABLE=1\n')
            # the link and the subscription's address went on stdin, never on a command line
            for secret in ('vpnlinksecret', 'subtokensecret', 'hotspotsecret1', 'apnsecret7'):
                self.assertNotIn(secret, self.argv_text())
                self.assertNotIn(secret, r.stdout + r.stderr)

    def test_other_device_needs_force(self):
        for shell in self.each_shell():
            self.fresh()
            f = self.write(self.doc({'lan': {'ip': '192.168.60.1'}}, device='u30air'))
            r = self.backup(shell, 'import', f)
            self.assertEqual(r.returncode, 1)
            self.assertIn('from a u30air, this device is a f50: refused', r.stderr)
            self.assertIn('LAN_IP=192.168.77.1', (self.root / 'etc/mu300/lan.conf').read_text())
            r = self.backup(shell, 'import', f, '--force')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('LAN_IP=192.168.60.1', (self.root / 'etc/mu300/lan.conf').read_text())

    def test_openwrt_only_sections_are_skipped_here(self):
        for shell in self.each_shell():
            self.fresh()
            f = self.write(self.doc({'locks': {'lte': '1,3'}, 'sms_forward': {'enabled': False}, 'lan': {'ip': '192.168.77.1'}},
                                    system='openwrt-luci'))
            r = self.backup(shell, 'import', f)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('note: from openwrt-luci, taken on ubuntu', r.stdout)
            self.assertIn('skip  network lock: lte "1,3" (no network locks on this system)', r.stdout)
            self.assertIn('skip  SMS forwarding', r.stdout)


class Hostile(BackupCase):
    """A file refused whole: nothing changes, whatever part of it was fine."""

    def assert_refused(self, shell, doc, code=None, text=None):
        hs = self.root / 'etc/mu300/hotspot.conf'
        before = hs.read_text()
        lan = (self.root / 'etc/mu300/lan.conf').read_text()
        self.clear_calls()
        r = self.backup(shell, 'import', self.write(doc))
        self.assertIn(r.returncode, (code,) if code else (1, 2), (doc, r.stdout, r.stderr))
        if text:
            self.assertIn(text, r.stdout + r.stderr)
        self.assertEqual(hs.read_text(), before)
        self.assertEqual((self.root / 'etc/mu300/lan.conf').read_text(), lan)
        self.assertEqual(self.calls('later'), [])
        self.assertEqual(self.calls('chpasswd'), [])
        return r

    def test_format(self):
        for shell in self.each_shell():
            self.fresh()
            good = {'lan': {'ip': '192.168.60.1'}}
            for doc in ('', 'not json', '[]', '{"format":"other","version":1}', json.dumps(dict(self.doc(good), version=2)),
                        json.dumps(dict(self.doc(good), extra=1)),
                        json.dumps(self.doc(dict(good, mystery={}))),
                        json.dumps(self.doc({'lan': {'ip': '192.168.60.1', 'gateway': 'x'}})),
                        json.dumps(self.doc({'lan': {'ip': 5}})),
                        json.dumps(self.doc({'wifi': {'ssid': 'x' * 33}})),
                        json.dumps(self.doc({'wifi': {'ssid': 'bad\u0001name'}})),
                        json.dumps(self.doc({'vpn': {'subscriptions': [{'name': 'ok', 'url': 'https://a.example.com/x\n'}]}})),
                        json.dumps(self.doc({'ssh_keys': [KEY1 + '\nssh-rsa AAAAB3NzaC1yc2EAAAADAQAB evil']})),
                        json.dumps(self.doc({'ssh_keys': 'ssh-ed25519 AAAA'})),
                        json.dumps(self.doc({'sim': {'default': 3}})),
                        json.dumps(self.doc({'vpn': {'settings': {'XRAY': '/tmp/evil'}}})),
                        json.dumps(self.doc({'vpn': {'profiles': [{'name': 'a', 'type': '../x', 'config': 'c'}]}})),
                        # the security review of #285: a value that would be an option, a line break inside a
                        # value or a key (jq's ^ and $ are line anchors), a second shadow line after the hash
                        json.dumps(self.doc({'vpn': {'profiles': [{'name': 'a', 'type': '--json', 'config': 'c'}]}})),
                        json.dumps(self.doc({'vpn': {'profiles': [{'name': 'a', 'type': 'xray\n--json', 'config': 'c'}]}})),
                        json.dumps(self.doc({'vpn': {'profiles': [{'name': 'a', 'type': 'xray', 'config': 'c',
                                                                   'options': {'A\nB': 'x'}}]}})),
                        json.dumps(self.doc({'password': {'hash': HASH + '\nroot:' + HASH}})),
                        json.dumps(self.doc({'password': {'hash': HASH + '\n'}})),
                        json.dumps(self.doc({'locks': {'lte': '1\n--help'}})),
                        json.dumps(dict(self.doc(good), source={'device': 'f50\nx', 'system': 'ubuntu', 'release': '', 'hostname': ''})),
                        json.dumps(self.doc({'vpn': {'subscriptions': [{'name': 'a', 'url': 'u', 'interval': 9999}]}})),
                        json.dumps(dict(self.doc(good), source={'device': 'f50; reboot', 'system': 'ubuntu', 'release': '', 'hostname': ''})),
                        '{"format":"mu300-backup","version":1,"created":1,"source":"x","sections":{}}'):
                self.assert_refused(shell, doc, code=2)
            # larger than 4 MiB
            self.assert_refused(shell, json.dumps(self.doc({'vpn': {'profiles': [{'name': 'a', 'type': 'xray', 'config': 'x' * (4 << 20)}]}})), code=2)

    def test_values_by_the_setters_rules(self):
        for shell in self.each_shell():
            self.fresh()
            for sections, why in (
                    ({'lan': {'ip': '192.168.60.1'}, 'wifi': {'ssid': ' leading space'}}, 'bad-value: ssid'),
                    ({'lan': {'ip': '192.168.60.1'}, 'wifi': {'password': 'short'}}, 'bad-value: password'),
                    ({'lan': {'ip': '192.168.60.1'}, 'wifi': {'band': '5', 'channel': '14'}}, 'bad-value: channel'),
                    ({'lan': {'ip': '8.8.8.1'}}, 'bad-value: ip'),
                    ({'lan': {'ip': '192.168.60.1'}, 'system': {'hostname': 'a;reboot'}}, 'bad-value: name'),
                    ({'lan': {'ip': '192.168.60.1'}, 'system': {'timezone': 'Europe/../../etc/shadow'}}, 'bad-value: zone'),
                    ({'lan': {'ip': '192.168.60.1'}, 'data': {'apn': 'a b', 'password': 'p'}}, 'bad-value: apn'),
                    ({'lan': {'ip': '192.168.60.1'}, 'data': {'apn': 'internet', 'user': 'u"x', 'password': 'p'}}, 'bad-value: user'),
                    ({'lan': {'ip': '192.168.60.1'}, 'ssh_keys': ['command="sh" ' + KEY1]}, 'not an OpenSSH public key'),
                    ({'lan': {'ip': '192.168.60.1'}, 'password': {'hash': 'x:0:0:root'}}, 'not a password hash'),
                    ({'lan': {'ip': '192.168.60.1'}, 'password': {'hash': ''}}, 'not a password hash')):
                r = self.assert_refused(shell, self.doc(sections), code=1)
                self.assertIn(why, r.stdout, (sections, r.stdout))
                self.assertIn('nothing was changed', r.stderr)

    def test_vpn_values(self):
        for shell in self.each_shell():
            self.fresh()
            self.vpn()
            for vpn, why in (({'profiles': [{'name': '-rf', 'type': 'xray', 'config': VLESS}]}, 'a VPN profile name'),
                             ({'profiles': [{'name': 'ok', 'type': 'xray', 'config': 'a\u0000b'}]}, 'not a text configuration'),
                             ({'profiles': [{'name': 'ok', 'type': 'xray', 'config': ''}]}, 'no configuration'),
                             ({'subscriptions': [{'name': 'ok', 'url': 'http://plain.example.com/x'}]}, 'not an https:// address'),
                             ({'subscriptions': [{'name': 'ok', 'url': 'https://a.example.com/x y'}]}, 'not an https:// address'),
                             ({'settings': {'REMOTE_DNS': '1.1.1.1; reboot'}}, 'VPN setting REMOTE_DNS'),
                             ({'settings': {'LAN_CIDRS': '*'}}, 'VPN setting LAN_CIDRS'),
                             # never an option on the module's command line
                             ({'profiles': [{'name': 'ok', 'type': 'xray', 'config': VLESS, 'options': {'UPSTREAM_HTTP_PROXY': '--insecure'}}]}, 'VPN option UPSTREAM_HTTP_PROXY'),
                             ({'subscriptions': [{'name': '--json', 'url': SUB_URL}]}, 'a VPN subscription name'),
                             ({'active': '-x'}, 'a VPN profile name')):
                r = self.assert_refused(shell, self.doc({'lan': {'ip': '192.168.60.1'}, 'vpn': vpn}), code=1)
                self.assertIn(why, r.stdout, (vpn, r.stdout))
                self.assertEqual(json.loads((self.tmp / 'vpn.json').read_text())['profiles'], [])


class OpenWrt(BackupCase):
    system = 'openwrt'

    def setUp(self):
        super().setUp()
        (self.root / 'etc/dropbear').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/dropbear/authorized_keys').write_text(KEY2 + '\n')
        (self.root / 'etc/init.d').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/init.d/dropbear').write_text('#!/bin/sh\n')
        (self.root / 'etc/init.d/dropbear').chmod(0o755)
        (self.root / 'etc/rc.d').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/rc.d/S50dropbear').write_text('')

    def test_export_from_uci_and_an_ubuntu_file_taken(self):
        for shell in self.each_shell():
            self.fresh()
            d = json.loads(self.export(shell).read_text())
            s = d['sections']
            self.assertEqual(d['source']['system'], 'openwrt')
            self.assertEqual(s['wifi'], {'ssid': 'OpenWrt-1', 'password': 'wrtsecret99', 'band': '5', 'channel': '36',
                                         'hidden': False, 'enabled': True})
            self.assertEqual((s['data']['apn'], s['data']['password']), ('internet', 'apnsecret7'))
            self.assertEqual(s['ssh_keys'], [KEY2])
            self.assertEqual(s['system']['ssh'], True)
            # an Ubuntu backup: what both systems have
            f = self.write(self.doc({'wifi': {'ssid': 'From Ubuntu', 'password': 'ubuntusecret1', 'band': '5', 'channel': '36',
                                              'hidden': False, 'enabled': True},
                                     'ssh_keys': [KEY1], 'password': {'hash': '$y$j9T$abcdefgh$' + 'c' * 43}}))
            r = self.backup(shell, 'import', f)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn('not a password hash this system takes', r.stdout)   # OpenWrt's musl has no yescrypt
            r = self.backup(shell, 'import', f, '--skip', 'password')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            w = self.ucidb()['wireless']['default_radio0']
            self.assertEqual((w['ssid'], w['key']), ('From Ubuntu', 'ubuntusecret1'))
            self.assertEqual((self.root / 'etc/dropbear/authorized_keys').read_text(), KEY2 + '\n' + KEY1 + '\n')
            self.assertNotIn('ubuntusecret1', self.argv_text().replace((self.tmp / 'uci-batch').read_text(), ''))


class Luci(BackupCase):
    """openwrt-luci: the network locks through the lock tool, SMS forwarding through the panel's adapter."""
    system = 'openwrt'

    def setUp(self):
        super().setUp()
        rpc = self.tmp / 'mu300dash'
        rpc.write_text('#!/bin/sh\nexit 0\n')
        rpc.chmod(0o755)
        self.lockdir = self.root / 'etc/unisoc-modem/lock-state.d'
        self.lockdir.mkdir(parents=True)
        (self.lockdir / 'lte').write_text('1,3')
        (self.lockdir / 'cell').write_text('lte:100,69')
        self.tool('lock')
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        lib = self.root / 'usr/share/unisoc-modem'
        lib.mkdir(parents=True)
        (lib / 'lib.sh').write_text((TOP / 'openwrt/luci-app-mu300/root/usr/share/unisoc-modem/lib.sh').read_text())
        ad = self.root / 'usr/libexec/unisoc-modem'
        ad.mkdir(parents=True)
        (ad / 'forward').write_text(ADAPTER.read_text())
        (ad / 'forward').chmod(0o755)
        self.fwdconf = self.root / 'etc/mu300/sms-forward.conf'
        self.fwdconf.write_text('enabled=1\ntemplate={text}\\n-- {sender}\nallow=BANK*\nwebhook=1\n'
                                'webhook_url=https://hook.example.com/hooksecret\nwebhook_format=json\nwebhook_body=\n'
                                'webhook_expect=\nwebhook_header=Authorization: Bearer hdrsecret\ntelegram=0\ntelegram_token=\n'
                                'telegram_chat=\nemail=0\nemail_host=\nemail_port=587\nemail_tls=starttls\nemail_user=\n'
                                'email_password=\nemail_from=\nemail_to=\nsms=0\nsms_number=\n')
        self.fwdconf.chmod(0o600)
        self.tool('fwd', 'case $1 in status) echo "email 0" ;; esac')

    def env(self, **extra):
        e = super().env(**extra)
        e.update(MU300_LOCK_TOOL=str(self.bin / 'lock'), MU300_FWD_CONF=str(self.fwdconf), MU300_FWD_BIN=str(self.bin / 'fwd'))
        return e

    def test_locks_and_forwarding(self):
        for shell in self.each_shell():
            self.fresh()
            out = self.export(shell)
            s = json.loads(out.read_text())['sections']
            self.assertEqual(s['locks'], {'lte': '1,3', 'cell': 'lte:100,69'})
            fw = s['sms_forward']
            self.assertEqual((fw['enabled'], fw['template'], fw['webhook_url'], fw['webhook_headers']),
                             (True, '{text}\n-- {sender}', 'https://hook.example.com/hooksecret', 'Authorization: Bearer hdrsecret'))
            # the device changed since: other locks, forwarding off; the backup puts it back
            (self.lockdir / 'cell').unlink()
            (self.lockdir / 'mode').write_text('4g')
            saved = self.fwdconf.read_text()
            self.fwdconf.write_text('enabled=0\nwebhook=0\nwebhook_url=\n')
            self.clear_calls()
            r = self.backup(shell, 'import', out)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            calls = self.calls('lock')
            self.assertEqual(calls, [['lock', 'reset'], ['lock', 'apply', 'lte', '1,3'], ['lock', 'apply', 'cell', 'lte:100,69']])
            self.assertEqual(self.fwdconf.read_text(), saved)
            self.assertNotIn('hooksecret', self.argv_text() + r.stdout + r.stderr)
            # a lock value the API's rules refuse; one that would be an option of the lock tool
            for locks in ({'cell': 'lte:1;reboot'}, {'lte': '--help'}, {'mode': '-x'}):
                f = self.write(self.doc({'locks': locks}, system='openwrt-luci'))
                self.clear_calls()
                r = self.backup(shell, 'import', f)
                self.assertEqual(r.returncode, 1, (locks, r.stdout))
                self.assertIn('bad-value: val', r.stdout)
                self.assertEqual(self.calls('lock'), [])
            # a forwarding setting the adapter refuses: nothing written
            before = self.fwdconf.read_text()
            f = self.write(self.doc({'sms_forward': dict(fw, webhook_url='ftp://x')}, system='openwrt-luci'))
            r = self.backup(shell, 'import', f)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn('webhook address', r.stdout)
            self.assertEqual(self.fwdconf.read_text(), before)



class ApiMethods(BackupCase):
    """mu300-api backup.export and backup.import: the device manager's (and the LuCI page's) way in."""

    def test_export_and_import_through_the_api(self):
        for shell in self.each_shell():
            self.fresh()
            o = self.ok(shell, 'backup.export', {})
            self.assertFalse(o['encrypted'])
            self.assertTrue(o['name'].endswith('.json'))
            doc = json.loads(o['file'])
            self.assertEqual(doc['sections']['wifi']['password'], 'hotspotsecret1')
            self.assertNotIn('password', doc['sections'])
            o = self.ok(shell, 'backup.export', {'passphrase': 'correct horse battery', 'with_password': True})
            self.assertTrue(o['encrypted'])
            self.assertTrue(o['file'].startswith('MU300-BACKUP-ENCRYPTED 1\n'))
            sealed = o['file']
            self.refused(shell, 'backup.export', {'passphrase': 'short'}, 'bad-value', 'passphrase')
            self.refused(shell, 'backup.export', {'path': '/etc/shadow'}, 'unknown-field', 'path')
            # the dry run: what would change, nothing changed
            (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=Changed\nPSK=othersecret1\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n')
            o = self.ok(shell, 'backup.import', {'file': sealed, 'passphrase': 'correct horse battery', 'dry_run': True,
                                                 'skip': 'password'})
            self.assertTrue(o['valid'], o['text'])
            self.assertIn('would  hotspot: ssid "Changed" -> "MU300-AB12", the password changes', o['text'])
            self.assertIn('SSID=Changed', (self.root / 'etc/mu300/hotspot.conf').read_text())
            o = self.ok(shell, 'backup.import', {'file': sealed, 'passphrase': 'not the passphrase', 'dry_run': True})
            self.assertFalse(o['valid'])
            self.assertIn('wrong passphrase', o['text'])
            o = self.ok(shell, 'backup.import', {'file': '{"format":"x"}', 'dry_run': True})
            self.assertFalse(o['valid'])
            self.refused(shell, 'backup.import', {'file': sealed, 'only': 'wifi;reboot'}, 'bad-value', 'only')
            self.refused(shell, 'backup.import', {}, 'missing', 'file')
            # the restore itself: the job
            o = self.ok(shell, 'backup.import', {'file': sealed, 'passphrase': 'correct horse battery', 'skip': 'password'})
            self.assertEqual(o['job'], 'backup-import')
            j = self.wait_job(shell, timeout=120)
            self.assertEqual(j['rc'], 0, j['text'])
            self.assertIn('restored', j['text'])
            self.assertIn('SSID=MU300-AB12', (self.root / 'etc/mu300/hotspot.conf').read_text())
            # the passphrase and the secrets: never on a command line, in a job file or in its log
            written = self.everything_written()
            for secret in ('correct horse battery', 'hotspotsecret1', 'apnsecret7'):
                self.assertNotIn(secret, written)



# OpenWrt's jq is built without Oniguruma: test/capture/gsub/sub/match/scan/splits fail there ("jq was compiled
# without ONIGURUMA regex library"), and every file was refused as "a value of the wrong kind" (F50-C, 2026-10-17).
# A jq that refuses any program using them, in front of the real one: the OpenWrt and panel paths run through it.
NO_REGEX_JQ = ('for a in "$@"; do case $a in *test\\(*|*capture\\(*|*sub\\(*|*match\\(*|*scan\\(*|*splits\\(*) '
               'echo "jq: error: jq was compiled without ONIGURUMA regex library." >&2; exit 5 ;; esac; done\n'
               f'exec "{shutil.which("jq")}" "$@"')


class OpenWrtJqWithoutRegex(OpenWrt):
    def setUp(self):
        super().setUp()
        self.stub('jq', NO_REGEX_JQ)


class LuciJqWithoutRegex(Luci):
    def setUp(self):
        super().setUp()
        self.stub('jq', NO_REGEX_JQ)


class Wiring(unittest.TestCase):
    def test_on_the_path(self):
        # the wiki and FINDINGS 155 say "mu300-backup export": a command on PATH on both systems (path-commands)
        self.assertIn('mu300-backup', (BIN.parent / 'lib' / 'path-commands').read_text().split())
        self.assertTrue(os.access(BIN / 'mu300-backup', os.X_OK))


if __name__ == '__main__':
    unittest.main()
