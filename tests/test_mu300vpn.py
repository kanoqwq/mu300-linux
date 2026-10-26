"""The VPN pages' rpcd backend (luci-app-mu300's mu300vpn) runs as root on input from the web UI and drives mu300-vpn,
the VPN module's CLI (its spec 2026-10-11-engine-extras, section 4). Every parameter is checked against an allow-list
before anything runs; a value that can hold a secret (a share link, a config, a subscription URL, an OpenVPN password)
reaches the CLI on its stdin, never in its argv; long work runs detached as the one background task; every reply is one
JSON object, and nothing of a reply is logged.

mu300-vpn is a stub that records its arguments and its stdin and prints what the test chose; setsid records what
would have been started detached and starts nothing (the task's own shell is run by one test on purpose)."""
import json
import shutil
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from helpers import TOP, ShellTest
from test_mu300dash_security import JSONFILTER, HOSTILE, hostile_variants

APP = TOP / 'openwrt' / 'luci-app-mu300' / 'root'
VPNB = APP / 'usr' / 'libexec' / 'rpcd' / 'mu300vpn'
ACL = APP / 'usr' / 'share' / 'rpcd' / 'acl.d' / 'luci-app-mu300.json'
MENU = APP / 'usr' / 'share' / 'luci' / 'menu.d' / 'luci-app-mu300.json'
VIEWS = TOP / 'openwrt' / 'luci-app-mu300' / 'htdocs' / 'luci-static' / 'resources' / 'view' / 'mu300'

# mu300-vpn: one record per call (argv NUL-separated, then stdin in .stdin), the output the test put in
# $STUB_OUT/vpn.<arg1>.<arg2> or $STUB_OUT/vpn.<arg1> (else {}), the status in the same name + .rc
VPN_STUB = r'''#!/bin/sh
f=$(mktemp "$STUB_CALLS/call.XXXXXX")
printf '%s\0' mu300-vpn "$@" > "$f"
cat > "$f.stdin"
for n in "vpn.${1:-}.${2:-}" "vpn.${1:-}"; do
    if [ -f "$STUB_OUT/$n" ]; then
        cat "$STUB_OUT/$n"
        [ -f "$STUB_OUT/$n.err" ] && cat "$STUB_OUT/$n.err" >&2
        [ -f "$STUB_OUT/$n.rc" ] && exit "$(cat "$STUB_OUT/$n.rc")"
        exit 0
    fi
done
echo '{}'
'''

# What the real mu300-vpn (mu300-linux-vpn, branch engine-extras, module v2) printed, run on the OpenWrt rootfs with a
# scratch store (tests/fixtures/mu300-vpn-cli: NAME.out, NAME.err, NAME.rc). capture.sh there makes them again: in a
# container of the openwrt-luci rootfs, with the module's bin/ and lib/ at /v. The stub serves these, so the backend is
# tested against the CLI's real output, not a copy of the spec.
CLI = TOP / 'tests' / 'fixtures' / 'mu300-vpn-cli'


def cli_out(name):
    return (CLI / f'{name}.out').read_text()


STATUS = json.loads(cli_out('status-running'))
PROFILES = json.loads(cli_out('profile-list'))
ENGINES = json.loads(cli_out('engines'))

METHODS = {'status', 'profiles', 'engines', 'subscriptions', 'settings', 'job', 'service', 'killswitch', 'use',
           'profile_add', 'profile_rename', 'profile_remove', 'profile_check', 'profile_option', 'profile_reveal',
           'profile_edit', 'latency', 'sub_add', 'sub_refresh', 'sub_remove', 'engine_install', 'engine_remove',
           'core_install', 'setting_set', 'logs'}
# reads: the state and the lists (the CLI masks every secret in them); the log names hosts the device talked to and
# the reveal prints credentials: write only, as mu300dash's AT history and SMS bodies
# the task's log too: whatever a program wrote there is not for the read grant (it is redacted besides)
READ = {'status', 'profiles', 'engines', 'subscriptions', 'settings'}
WRITE = METHODS - READ
# the methods whose work runs detached (setsid)
DETACHED = {'latency', 'sub_add', 'sub_refresh', 'engine_install', 'core_install', 'profile_add'}

LINK = 'vless://11111111-2222-3333-4444-555555555555@vpn.example.com:443?security=tls&type=ws#Office'
URL = 'https://sub.example.com/api/v1/client/subscribe?token=SECRETTOKEN0123'


# flock(1) for a test machine without it (macOS): flock [-w SECS] FD, on the descriptor the shell holds open
FLOCK_STUB = r'''
import fcntl, sys, time
args = sys.argv[1:]
wait = None
if args[0] == '-w':
    wait = float(args[1]); args = args[2:]
elif args[0] == '-n':
    args = args[1:]
fd = int(args[0]); end = time.monotonic() + (wait or 0)
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); sys.exit(0)
    except OSError:
        if wait is None or time.monotonic() > end: sys.exit(1)
        time.sleep(0.05)
'''
# a process that holds the lock FILE for SECS seconds
FLOCK_HOLD = '''
import fcntl, sys, time
f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); time.sleep(float(sys.argv[2]))
'''


class Mu300Vpn(ShellTest):
    def setUp(self):
        super().setUp()
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        self.stub('setsid', 't=$(mktemp "$STUB_CALLS/.tmp.XXXXXX"); printf "%s\\0" setsid "$@" > "$t"; '
                            'mv "$t" "$STUB_CALLS/call.${t##*.}"')
        if not shutil.which('flock'):
            (self.stubs / 'flock.py').write_text(FLOCK_STUB)
            self.stub('flock', f'exec python3 "{self.stubs}/flock.py" "$@"')
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        self.vpn = self.bin / 'mu300-vpn'
        self.vpn.write_text(VPN_STUB)
        self.vpn.chmod(0o755)
        self.extra = self.bin / 'mu300-extra'
        self.extra.write_text('#!/bin/sh\nf=$(mktemp "$STUB_CALLS/call.XXXXXX")\nprintf "%s\\0" mu300-extra "$@" > "$f"\n')
        self.extra.chmod(0o755)
        self.out = self.tmp / 'out'
        self.out.mkdir()
        self.dash = self.tmp / 'dash'
        self.upload = self.tmp / 'upload'
        self.upload_pkg = self.tmp / 'upload.tar.gz'
        self.vpnrun = self.tmp / 'vpnrun'
        self.vpnrun.mkdir()
        self.n = 0
        self.output('vpn.status', json.dumps(STATUS))
        self.output('vpn.profile.list', json.dumps(PROFILES))
        self.output('vpn.engines', json.dumps(ENGINES))
        self.output('vpn.subscription.list', '[]')

    def output(self, name, text, rc=None, err=None):
        (self.out / name).write_text(text)
        if rc is not None:
            (self.out / (name + '.rc')).write_text(str(rc))
        if err is not None:
            (self.out / (name + '.err')).write_text(err)

    def call(self, shell, method, params=None, raw=None, **extra):
        """`mu300vpn call METHOD` with PARAMS on stdin: (CompletedProcess, [[name, arg...]], {index: stdin})"""
        # (a fresh directory per call, made atomically: calls run in parallel threads)
        calls = Path(tempfile.mkdtemp(prefix='calls', dir=self.tmp))
        stdin = raw if raw is not None else json.dumps(params or {}, ensure_ascii=False)
        env = self.env(STUB_CALLS=calls, STUB_OUT=self.out, MU300_VPN_CMD=self.vpn, MU300_EXTRA_CMD=self.extra,
                       MU300_DASH_DIR=self.dash, MU300_VPN_UPLOAD=self.upload, MU300_VPN_UPLOAD_PKG=self.upload_pkg,
                       MU300_VPN_RUN=self.vpnrun)
        env.update({k: str(v) for k, v in extra.items()})
        r = subprocess.run(shell + [str(VPNB), 'call', method], input=stdin.encode(), capture_output=True, env=env,
                           timeout=60)
        r.stdout, r.stderr = r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
        if method in DETACHED:
            seen, deadline = None, time.monotonic() + 3
            while time.monotonic() < deadline:
                now = sorted(calls.glob('call.*'))
                if now == seen:
                    break
                seen = now
                time.sleep(0.1)
        recs, stdins = [], {}
        for f in sorted(calls.glob('call.*')):
            if f.suffix == '.stdin':
                continue
            rec = [a.decode('utf-8', 'surrogateescape') for a in f.read_bytes().split(b'\0')[:-1]]
            sin = f.with_name(f.name + '.stdin')
            if sin.exists():
                stdins[len(recs)] = sin.read_text()
            recs.append(rec)
        return r, recs, stdins

    def reply(self, r):
        try:
            obj = json.loads(r.stdout)
        except ValueError as e:
            self.fail(f'not JSON ({e}): {r.stdout!r} stderr={r.stderr!r}')
        self.assertIsInstance(obj, dict, r.stdout)
        return obj

    def lock_free(self):
        import fcntl
        p = self.dash / 'vpn-job' / 'lock'
        if not p.exists():
            return True
        with open(p, 'a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            fcntl.flock(f, fcntl.LOCK_UN)
        return True

    def staged(self):
        job = self.dash / 'vpn-job'
        return sorted(p.name for p in job.iterdir() if p.name.startswith('stage.')) if job.exists() else []

    def clean_job(self):
        import shutil
        job = self.dash / 'vpn-job'
        for p in job.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    @staticmethod
    def job_cmd(rec):
        """what a setsid record starts: [kind-of-stdin file, json flag, command...]"""
        i = rec.index('mu300vpn-job')
        return rec[i + 3:i + 5], rec[i + 5:]


class Inventory(Mu300Vpn):
    def test_list_declares_every_method(self):
        for shell in self.each_shell():
            r = self.script(shell, VPNB, 'list')
            self.assertEqual(set(json.loads(r.stdout)), METHODS)

    def test_acl_reads_and_writes(self):
        acl = json.loads(ACL.read_text())['luci-app-mu300']
        self.assertEqual(set(acl['read']['ubus']['mu300vpn']), READ)
        self.assertEqual(set(acl['write']['ubus']['mu300vpn']), WRITE)
        # the two upload paths, write only, and nothing else of the file system (beside the Languages page's and the
        # Update page's own one each)
        self.assertEqual(acl['write']['file'], {'/tmp/mu300-extra-lang.tar.gz': ['write'],
                                                '/tmp/mu300-vpn-upload': ['write'],
                                                '/tmp/mu300-vpn-extra.tar.gz': ['write'],
                                                '/mnt/mu300-disk/.mu300-upload/update.zip': ['write']})
        self.assertNotIn('file', acl['read'])

    def test_the_menu_has_the_six_pages_and_their_views_exist(self):
        menu = json.loads(MENU.read_text())
        pages = {k: v for k, v in menu.items() if k.startswith('admin/vpn/')}
        self.assertEqual(sorted(pages), sorted('admin/vpn/mu300-' + p for p in
                                               ('overview', 'profiles', 'subscriptions', 'engines', 'settings', 'logs')))
        for k, v in pages.items():
            # the pages only with the VPN module: LuCI's "fs" dependency on the module's mu300-vpn in the vpn extra
            # (mu300-extra's own test, vpn_module_in: EXTRA/vpn/bin/mu300-vpn executable); without it the VPN menu
            # is not there at all. LuCI checks it when it builds its index cache, so mu300-extra drops the cache
            # when the module comes or goes (luci_menu_refresh)
            self.assertEqual(v['depends'], {'acl': ['luci-app-mu300'],
                                            'fs': {'/mnt/mu300-disk/extra/vpn/bin/mu300-vpn': 'executable'}})
            self.assertTrue((VIEWS / (v['action']['path'].split('/')[1] + '.js')).is_file(), k)
        for k, v in menu.items():
            if not k.startswith('admin/vpn/'):
                self.assertNotIn('fs', v['depends'], k)
        extra = (TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-extra').read_text()
        self.assertIn('[ "$name" != vpn ] || luci_menu_refresh', extra)
        self.assertIn('[ "$1" != vpn ] || luci_menu_refresh', extra)
        self.assertIn('rm -f "$SYSROOT"/tmp/luci-indexcache "$SYSROOT"/tmp/luci-indexcache.*', extra)

    def test_an_unknown_method_runs_nothing(self):
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'rm -rf /', {})
            self.assertEqual(self.reply(r), {'ok': 0, 'error': 'No such method'})
            self.assertEqual(recs, [])

    def test_a_request_too_large_is_refused(self):
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'profile_edit', {'id': 'office', 'config': 'x' * 140000})
            self.assertEqual(self.reply(r), {'ok': 0, 'error': 'Request too large'})
            self.assertEqual(recs, [])

    def test_the_script_parses(self):
        for shell in self.each_shell():
            r = subprocess.run(shell + ['-n', str(VPNB)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_installed_executable(self):
        self.assertTrue(os.stat(VPNB).st_mode & stat.S_IXUSR)
        for f in (TOP / 'openwrt' / 'luci-app-mu300' / 'Makefile', TOP / 'openwrt' / 'build-rootfs.sh'):
            self.assertIn('usr/libexec/rpcd/mu300vpn', f.read_text(), f)


class Refusals(Mu300Vpn):
    FIELDS = [
        ('status', {}, 'exit_ip', '1'),
        ('engines', {}, 'check', '1'),
        ('service', {}, 'op', 'restart'),
        ('killswitch', {}, 'value', 'on'),
        ('use', {}, 'id', 'office'),
        ('profile_add', {'source': 'link', 'data': LINK}, 'source', 'link'),
        ('profile_add', {'source': 'link', 'data': LINK}, 'type', 'xray'),
        ('profile_add', {'source': 'link', 'data': LINK}, 'name', 'Office'),
        ('profile_add', {'source': 'url', 'data': URL}, 'data', URL),
        ('profile_rename', {'id': 'office', 'name': 'Home'}, 'id', 'office'),
        ('profile_remove', {}, 'id', 'office'),
        ('profile_check', {}, 'id', 'office'),
        ('profile_option', {'id': 'office', 'key': 'MIHOMO_STACK', 'value': 'gvisor'}, 'id', 'office'),
        ('profile_option', {'id': 'office', 'key': 'MIHOMO_STACK', 'value': 'gvisor'}, 'key', 'MIHOMO_STACK'),
        ('profile_option', {'id': 'office', 'key': 'MIHOMO_STACK', 'value': 'gvisor'}, 'value', 'gvisor'),
        ('profile_option', {'id': 'office', 'key': 'UPSTREAM_HTTP_PROXY', 'value': 'proxy.lan:3128'}, 'value',
         'proxy.lan:3128'),
        ('profile_option', {'id': 'office', 'key': 'TLS_PIN_SHA256', 'value': 'ab:cd'}, 'value', 'ab:cd'),
        ('profile_reveal', {}, 'id', 'office'),
        ('profile_edit', {'id': 'office', 'config': '{}'}, 'id', 'office'),
        ('latency', {}, 'id', 'office'),
        ('sub_add', {'name': 'Mine', 'url': URL, 'type': 'auto'}, 'url', URL),
        ('sub_add', {'name': 'Mine', 'url': URL, 'type': 'auto'}, 'type', 'auto'),
        ('sub_refresh', {}, 'id', 'all'),
        ('sub_remove', {'id': 'mine'}, 'id', 'mine'),
        ('sub_remove', {'id': 'mine'}, 'keep', '1'),
        ('engine_install', {'name': 'xray'}, 'name', 'xray'),
        ('engine_install', {'name': 'xray'}, 'source', 'release'),
        ('engine_remove', {}, 'name', 'mihomo'),
        ('core_install', {}, 'source', 'release'),
        ('setting_set', {'key': 'IPV6', 'value': 'auto'}, 'key', 'IPV6'),
        ('setting_set', {'key': 'IPV6', 'value': 'auto'}, 'value', 'auto'),
        ('setting_set', {'key': 'REMOTE_DNS', 'value': '1.1.1.1'}, 'value', '1.1.1.1'),
        ('setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0/8'}, 'value', '10.0.0.0/8'),
        ('logs', {}, 'engine', 'xray'),
        ('logs', {}, 'lines', '100'),
    ]

    def assert_refused(self, shell, jobs):
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda j: (j[0],) + self.call(shell, j[1], j[2]), jobs))
        for label, r, recs, _ in res:
            with self.subTest(case=label):
                obj = self.reply(r)
                self.assertEqual(obj.get('ok'), 0, (label, r.stdout))
                self.assertTrue(obj.get('error'), r.stdout)
                self.assertEqual(recs, [], f'{label}: something ran')

    def test_hostile_values_are_refused(self):
        jobs = []
        for method, base, field, valid in self.FIELDS:
            for name, bad in hostile_variants(valid).items():
                # a URL ends in a path: these characters after one are part of a valid URL, which reaches the CLI
                # on stdin only (the CLI parses it); a name is free text but never control characters or a dash first
                if field == 'data' and name.split(' after ')[0] in ('single quote', 'double quote', 'semicolon',
                                                                     'subst', 'backtick', 'pipe', 'ampersand',
                                                                     'leading dash') and ' after ' in name:
                    continue
                if field == 'url' and ' after ' in name and name.split(' after ')[0] != 'newline' \
                        and name.split(' after ')[0] not in ('nul', 'non-ascii', 'long'):
                    continue
                if field == 'name' and name in ('single quote', 'double quote', 'semicolon', 'subst', 'backtick',
                                                'pipe', 'ampersand', 'non-ascii') or \
                        field == 'name' and ' after ' in name and name.split(' after ')[0] not in ('newline', 'nul', 'long'):
                    continue
                if field == 'url' and name in ('trailing newline', 'leading space'):
                    continue   # the white space a copy and paste adds is trimmed
                if field == 'name' and name in ('object', 'array'):
                    continue   # arrives as its JSON text: free text, a valid name
                if field == 'id' and name.startswith('leading dash after '):
                    continue   # office-rf is a valid id, and not an option
                if field == 'name' and name == 'trailing newline':
                    continue   # the page trims names; one trailing newline is dropped by the JSON reader too
                jobs.append((f'{method}.{field}={name}', method, dict(base, **{field: bad})))
        for shell in self.each_shell():
            self.assert_refused(shell, jobs)

    def test_values_out_of_range_are_refused(self):
        jobs = [
            ('service start', 'service', {'op': 'start'}),
            ('service empty', 'service', {}),
            ('killswitch 1', 'killswitch', {'value': '1'}),
            ('id upper', 'use', {'id': 'Office'}),
            ('id dash first', 'use', {'id': '-office'}),
            ('id 33', 'use', {'id': 'a' * 33}),
            ('id slash', 'use', {'id': '../etc'}),
            ('id dot', 'use', {'id': '.'}),
            ('id empty', 'profile_remove', {'id': ''}),
            ('latency id All', 'latency', {'id': 'All'}),
            ('source path', 'profile_add', {'source': '/etc/shadow'}),
            ('source unknown', 'profile_add', {'source': 'stdin', 'data': LINK}),
            ('link http', 'profile_add', {'source': 'link', 'data': 'http://example.com/'}),
            ('link space', 'profile_add', {'source': 'link', 'data': 'vless://a b@c:1'}),
            ('link two lines', 'profile_add', {'source': 'link', 'data': LINK + '\n' + LINK}),
            ('link file scheme', 'profile_add', {'source': 'link', 'data': 'file:///etc/shadow'}),
            ('text empty', 'profile_add', {'source': 'text', 'data': ''}),
            ('text control', 'profile_add', {'source': 'text', 'data': '[Interface]\n\x1b[2J'}),
            ('text too large', 'profile_add', {'source': 'text', 'data': 'x' * 70000}),
            ('file with data', 'profile_add', {'source': 'file', 'data': '/etc/shadow'}),
            ('url ftp', 'profile_add', {'source': 'url', 'data': 'ftp://example.com/x'}),
            ('url file', 'profile_add', {'source': 'url', 'data': 'file:///etc/shadow'}),
            ('url no host', 'profile_add', {'source': 'url', 'data': 'https:///x'}),
            ('url userinfo', 'profile_add', {'source': 'url', 'data': 'https://a@b/x'}),
            ('url with a type', 'profile_add', {'source': 'url', 'data': URL, 'type': 'xray'}),
            ('type unknown', 'profile_add', {'source': 'link', 'data': LINK, 'type': 'l2tp'}),
            ('name 65', 'profile_add', {'source': 'link', 'data': LINK, 'name': 'n' * 65}),
            ('name dash', 'profile_rename', {'id': 'office', 'name': '-rf'}),
            ('name empty', 'profile_rename', {'id': 'office', 'name': ''}),
            ('name space first', 'profile_rename', {'id': 'office', 'name': ' x'}),
            ('name tab', 'profile_rename', {'id': 'office', 'name': 'a\tb'}),
            ('option key', 'profile_option', {'id': 'office', 'key': 'NAME', 'value': 'x'}),
            ('option key type', 'profile_option', {'id': 'office', 'key': 'TYPE', 'value': 'dae'}),
            ('option key lower', 'profile_option', {'id': 'office', 'key': 'mihomo_stack', 'value': 'gvisor'}),
            ('option stack', 'profile_option', {'id': 'office', 'key': 'MIHOMO_STACK', 'value': 'lwip'}),
            ('option proxy no port', 'profile_option', {'id': 'office', 'key': 'UPSTREAM_HTTP_PROXY', 'value': 'proxy'}),
            ('option proxy url', 'profile_option', {'id': 'office', 'key': 'UPSTREAM_HTTP_PROXY',
                                                   'value': 'http://proxy:3128'}),
            ('option pin', 'profile_option', {'id': 'office', 'key': 'TLS_PIN_SHA256', 'value': 'xyz'}),
            ('ovpn pass newline', 'profile_option', {'id': 'office', 'key': 'OVPN_PASS', 'value': 'a\nb'}),
            ('ovpn user long', 'profile_option', {'id': 'office', 'key': 'OVPN_USER', 'value': 'u' * 129}),
            ('edit empty', 'profile_edit', {'id': 'office', 'config': ''}),
            ('edit control', 'profile_edit', {'id': 'office', 'config': 'a\x00b'}),
            ('sub no name', 'sub_add', {'name': '', 'url': URL}),
            ('sub url ftp', 'sub_add', {'name': 'x', 'url': 'ftp://x/y'}),
            ('sub type', 'sub_add', {'name': 'x', 'url': URL, 'type': 'clash'}),
            ('sub plain http', 'sub_add', {'name': 'x', 'url': 'http://sub.example.com/x'}),
            ('url plain http', 'profile_add', {'source': 'url', 'data': 'http://sub.example.com/x'}),
            ('sub interval 0', 'sub_add', {'name': 'x', 'url': URL, 'interval': '0'}),
            ('sub interval 1000', 'sub_add', {'name': 'x', 'url': URL, 'interval': '1000'}),
            ('sub interval word', 'sub_add', {'name': 'x', 'url': URL, 'interval': 'daily'}),
            ('sub interval option', 'sub_add', {'name': 'x', 'url': URL, 'interval': '--insecure'}),
            ('sub keep 2', 'sub_remove', {'id': 'mine', 'keep': '2'}),
            ('engine alias', 'engine_install', {'name': 'singbox'}),
            ('engine hev', 'engine_install', {'name': 'hev-socks5-tunnel'}),
            ('engine path', 'engine_install', {'name': '../../bin/sh'}),
            ('engine source url', 'engine_install', {'name': 'xray', 'source': 'http://x/y.tar.gz'}),
            ('engine source path', 'engine_install', {'name': 'xray', 'source': '/tmp/x'}),
            ('core source', 'core_install', {'source': '/tmp/x'}),
            ('setting kill switch', 'setting_set', {'key': 'KILL_SWITCH', 'value': '0'}),
            ('setting binary', 'setting_set', {'key': 'XRAY', 'value': '/tmp/evil'}),
            ('setting dae binary', 'setting_set', {'key': 'DAE', 'value': '/tmp/evil'}),
            ('setting ipv6 2', 'setting_set', {'key': 'IPV6', 'value': '2'}),
            ('setting dns name', 'setting_set', {'key': 'REMOTE_DNS', 'value': 'dns.google'}),
            ('setting dns 256', 'setting_set', {'key': 'REMOTE_DNS', 'value': '1.1.1.256'}),
            ('setting cidr no mask', 'setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0'}),
            ('setting cidr 33', 'setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0/33'}),
            ('setting cidr space', 'setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0/8, 192.168.0.0/16'}),
            ('setting cidr trailing comma', 'setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0/8,'}),
            ('logs engine', 'logs', {'engine': 'kernel'}),
            ('logs 0', 'logs', {'lines': '0'}),
            ('logs 1001', 'logs', {'lines': '1001'}),
            ('logs negative', 'logs', {'lines': '-5'}),
        ]
        for shell in self.each_shell():
            self.assert_refused(shell, jobs)


class Passthrough(Mu300Vpn):
    def run1(self, shell, method, params):
        r, recs, stdins = self.call(shell, method, params)
        return self.reply(r), recs, stdins

    def test_valid_values_reach_the_cli_as_exact_arguments(self):
        cases = [
            ('status', {}, ['status', '--json']),
            ('status', {'exit_ip': '1'}, ['status', '--json', '--exit-ip']),
            ('profiles', {}, ['profile', 'list', '--json']),
            ('engines', {'check': '1'}, ['engines', '--json', '--check']),
            ('subscriptions', {}, ['subscription', 'list', '--json']),
            ('service', {'op': 'on'}, ['on']),
            ('service', {'op': 'off'}, ['off']),
            ('service', {'op': 'restart'}, ['restart']),
            ('killswitch', {'value': 'off'}, ['killswitch', 'off']),
            ('use', {'id': 'office-2'}, ['use', 'office-2']),
            ('profile_rename', {'id': 'office', 'name': 'Ofis — İstanbul 東京'},
             ['profile', 'set', 'office', 'NAME', 'Ofis — İstanbul 東京']),
            ('profile_rename', {'id': 'office', 'name': 'Ali\'s "VPN"; $(x)'},
             ['profile', 'set', 'office', 'NAME', 'Ali\'s "VPN"; $(x)']),
            ('profile_remove', {'id': 'office'}, ['profile', 'remove', 'office']),
            # (check ID, the command every module has: "profile check ID" is in the spec's table, but module
            # v2.0.0 answers it with its usage text - the page's check never worked, docs/FINDINGS.md 56)
            ('profile_check', {'id': 'office'}, ['check', 'office']),
            ('profile_option', {'id': 'm', 'key': 'MIHOMO_STACK', 'value': 'system'},
             ['profile', 'set', 'm', 'MIHOMO_STACK', 'system']),
            ('profile_option', {'id': 'm', 'key': 'MIHOMO_STACK', 'value': ''}, ['profile', 'set', 'm', 'MIHOMO_STACK', '']),
            ('profile_option', {'id': 'o', 'key': 'UPSTREAM_HTTP_PROXY', 'value': '[fd00::1]:3128'},
             ['profile', 'set', 'o', 'UPSTREAM_HTTP_PROXY', '[fd00::1]:3128']),
            ('profile_reveal', {'id': 'office'}, ['profile', 'show', 'office', '--json', '--reveal']),
            ('latency', {'id': 'office'}, ['latency', 'office', '--json']),
            ('sub_remove', {'id': 'mine'}, ['subscription', 'remove', 'mine']),
            ('sub_remove', {'id': 'mine', 'keep': '1'}, ['subscription', 'remove', 'mine', '--keep-profiles']),
            ('engine_remove', {'name': 'sing-box'}, ['engine', 'remove', 'sing-box']),
            ('setting_set', {'key': 'IPV6', 'value': 'auto'}, ['settings', 'set', 'IPV6', 'auto']),
            ('setting_set', {'key': 'TAILSCALE', 'value': '0'}, ['settings', 'set', 'TAILSCALE', '0']),
            ('setting_set', {'key': 'REMOTE_DNS', 'value': '2606:4700:4700::1111'},
             ['settings', 'set', 'REMOTE_DNS', '2606:4700:4700::1111']),
            ('setting_set', {'key': 'BOOTSTRAP_DNS', 'value': ''}, ['settings', 'set', 'BOOTSTRAP_DNS', '']),
            ('setting_set', {'key': 'LAN_CIDRS', 'value': '10.0.0.0/8,192.168.0.0/16,fd00::/8'},
             ['settings', 'set', 'LAN_CIDRS', '10.0.0.0/8,192.168.0.0/16,fd00::/8']),
            ('logs', {}, ['logs', '-n', '100']),
            ('logs', {'engine': 'mihomo', 'lines': '1000'}, ['logs', 'mihomo', '-n', '1000']),
        ]
        self.output('vpn.profile.show', json.dumps(dict(PROFILES[0], config='[Interface]\n')))
        self.output('vpn.latency', json.dumps({"id": "office", "host": "vpn.example.com", "port": 443, "ms": 48,
                                               "via": "direct"}))
        for shell in self.each_shell():
            for method, params, argv in cases:
                with self.subTest(case=(method, params)):
                    obj, recs, _ = self.run1(shell, method, params)
                    self.assertEqual(obj.get('ok'), 1, obj)
                    self.assertEqual(recs, [['mu300-vpn'] + argv])

    def test_secrets_go_on_stdin_never_in_argv(self):
        cfg = '[Interface]\nPrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5eHg=\n'
        cases = [
            ('profile_add', {'source': 'link', 'data': '  ' + LINK + '\r\n'}, ['profile', 'import', '-'], LINK),
            ('profile_add', {'source': 'link', 'data': LINK, 'name': 'Work'}, ['profile', 'import', '-', 'Work'], LINK),
            ('profile_add', {'source': 'link', 'data': 'hysteria2://pw@h.example.com:443#H', 'type': 'dae',
                             'name': 'H'}, ['profile', 'add', 'dae', 'H', '-'], 'hysteria2://pw@h.example.com:443#H'),
            ('profile_add', {'source': 'text', 'data': cfg}, ['profile', 'import', '-'], cfg),
            ('profile_add', {'source': 'text', 'data': cfg, 'type': 'wireguard'},
             ['profile', 'add', 'wireguard', 'wireguard', '-'], cfg),
            ('profile_edit', {'id': 'office', 'config': cfg}, ['profile', 'edit', 'office', '-'], cfg),
            ('profile_option', {'id': 'vpn', 'key': 'OVPN_PASS', 'value': 'p@ss w0rd;$(x)'},
             ['profile', 'set', 'vpn', 'OVPN_PASS', '-'], 'p@ss w0rd;$(x)'),
            ('profile_option', {'id': 'vpn', 'key': 'OVPN_USER', 'value': 'kaan'},
             ['profile', 'set', 'vpn', 'OVPN_USER', '-'], 'kaan'),
        ]
        self.output('vpn.profile', 'office-2\n')
        for shell in self.each_shell():
            for method, params, argv, secret in cases:
                with self.subTest(case=(method, argv)):
                    obj, recs, stdins = self.run1(shell, method, params)
                    self.assertEqual(obj.get('ok'), 1, obj)
                    self.assertEqual(recs, [['mu300-vpn'] + argv])
                    self.assertEqual(stdins[0].rstrip('\n'), secret.rstrip('\n'))
                    if method == 'profile_add':
                        self.assertEqual(obj['id'], 'office-2')
            # nothing of them stays in the runtime directory
            self.assertEqual([p.name for p in (self.dash / 'vpn-job').iterdir()], [])

    def test_a_subscription_url_reaches_the_task_in_a_private_file(self):
        for shell in self.each_shell():
            obj, recs, _ = self.run1(shell, 'sub_add', {'name': 'Mine', 'url': URL, 'type': 'mihomo'})
            self.assertEqual(obj, {'ok': 1, 'started': 1, 'kind': 'sub-add'})
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0][0], 'setsid')
            (stage, flag), cmd = self.job_cmd(recs[0])
            self.assertEqual(cmd, [str(self.vpn), 'subscription', 'add', 'Mine', '-', '--type', 'mihomo'])
            self.assertNotIn(URL, '\0'.join(recs[0]))
            # a fresh 0700 directory of its own (mktemp), under the 0700 job directory; the URL in it, 0600
            st = Path(stage)
            self.assertEqual(st.parent, self.dash / 'vpn-job')
            self.assertRegex(st.name, r'^stage\.[A-Za-z0-9]{6}$')
            self.assertEqual(stat.S_IMODE(st.stat().st_mode), 0o700)
            p = st / 'in'
            self.assertEqual(p.read_text(), URL + '\n')
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((self.dash / 'vpn-job').stat().st_mode), 0o700)
            # a config from a URL: the same, in another directory
            obj, recs, _ = self.run1(shell, 'profile_add', {'source': 'url', 'data': URL, 'name': 'Dl'})
            self.assertEqual(obj['kind'], 'import-url')
            (stage2, flag), cmd = self.job_cmd(recs[0])
            self.assertNotEqual(stage2, stage)
            self.assertEqual(cmd, [str(self.vpn), 'profile', 'import', '--url', '-', 'Dl'])
            self.assertEqual((Path(stage2) / 'in').read_text(), URL + '\n')
            self.clean_job()
            # with a refresh interval
            obj, recs, _ = self.run1(shell, 'sub_add', {'name': 'Mine', 'url': URL, 'interval': '24'})
            (stage, flag), cmd = self.job_cmd(recs[0])
            self.assertEqual(cmd, [str(self.vpn), 'subscription', 'add', 'Mine', '-', '--type', 'auto', '--interval', '24'])
            self.clean_job()

    def test_long_work_runs_as_the_one_task(self):
        cases = [
            ('latency', {'id': 'all'}, 'latency-all', '1', [str(self.vpn), 'latency', '--all', '--json']),
            ('sub_refresh', {'id': 'all'}, 'sub-refresh', '0', [str(self.vpn), 'subscription', 'refresh', '--all']),
            ('sub_refresh', {'id': 'mine'}, 'sub-refresh', '0', [str(self.vpn), 'subscription', 'refresh', 'mine']),
            ('sub_refresh', {'id': 'due'}, 'sub-refresh', '0', [str(self.vpn), 'subscription', 'refresh', '--due']),
            ('engine_install', {'name': 'dae'}, 'engine-install', '0', [str(self.vpn), 'engine', 'install', 'dae']),
            ('engine_install', {'name': 'openvpn', 'source': 'release'}, 'engine-install', '0',
             [str(self.vpn), 'engine', 'install', 'openvpn']),
            ('core_install', {}, 'core-install', '0', [str(self.extra), 'install', 'vpn']),
        ]
        for shell in self.each_shell():
            for method, params, kind, flag, cmd in cases:
                with self.subTest(case=(method, params)):
                    obj, recs, _ = self.run1(shell, method, params)
                    self.assertEqual(obj, {'ok': 1, 'started': 1, 'kind': kind})
                    (sin, fl), c = self.job_cmd(recs[0])
                    self.assertEqual((sin, fl, c), ('', flag, cmd))
                    self.assertTrue(self.lock_free(), 'the lock is released')
                    self.assertEqual((self.dash / 'vpn-job' / 'state').read_text(), 'running\n')

    def test_a_running_task_refuses_another(self):
        for shell in self.each_shell():
            job = self.dash / 'vpn-job'
            job.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dash, 0o700)
            os.chmod(job, 0o700)
            sleeper = subprocess.Popen(['sleep', '30'])
            try:
                (job / 'pid').write_text(f'{sleeper.pid}\n')
                for method, params in (('latency', {'id': 'all'}), ('sub_add', {'name': 'x', 'url': URL}),
                                       ('engine_remove', {'name': 'xray'})):
                    obj, recs, _ = self.run1(shell, method, params)
                    self.assertEqual(obj, {'ok': 0, 'error': 'Another VPN task is still running'})
                    self.assertEqual(recs, [])
                # nothing was staged, and the lock is free again
                self.assertEqual(sorted(p.name for p in job.iterdir() if p.name.startswith('stage.')), [])
                self.assertTrue(self.lock_free())
                # an upload is not even taken while a task runs: the running one's file cannot be replaced
                self.upload_pkg.write_bytes(b'NEW')
                obj, recs, _ = self.run1(shell, 'engine_install', {'name': 'xray', 'source': 'file'})
                self.assertEqual(obj, {'ok': 0, 'error': 'Another VPN task is still running'})
                self.assertEqual(self.upload_pkg.read_bytes(), b'NEW')
                self.upload_pkg.unlink()
            finally:
                sleeper.kill()
                sleeper.wait()
                (job / 'pid').unlink()

    def test_uploads_are_moved_out_of_tmp_before_anything_reads_them(self):
        for shell in self.each_shell():
            self.upload.write_text('[Interface]\nPrivateKey = x\n')
            self.output('vpn.profile', 'wg\n')
            obj, recs, stdins = self.run1(shell, 'profile_add', {'source': 'file', 'name': 'wg0'})
            self.assertEqual(obj, {'ok': 1, 'id': 'wg'})
            self.assertEqual(recs, [['mu300-vpn', 'profile', 'import', '-', 'wg0']])
            self.assertEqual(stdins[0], '[Interface]\nPrivateKey = x\n')
            self.assertFalse(self.upload.exists())
            self.assertEqual(self.staged(), [])
            # nothing uploaded, or a link planted at the path: refused, nothing read
            obj, recs, _ = self.run1(shell, 'profile_add', {'source': 'file'})
            self.assertEqual(obj, {'ok': 0, 'error': 'No uploaded file'})
            secret = self.tmp / 'shadow'
            secret.write_text('root:x')
            os.symlink(secret, self.upload)
            obj, recs, _ = self.run1(shell, 'profile_add', {'source': 'file'})
            self.assertEqual(obj, {'ok': 0, 'error': 'No uploaded file'})
            self.assertEqual(recs, [])
            self.upload.unlink()
            # too large
            self.upload.write_bytes(b'x' * (1048576 + 1))
            obj, recs, _ = self.run1(shell, 'profile_add', {'source': 'file'})
            self.assertEqual(obj, {'ok': 0, 'error': 'The file is larger than 1 MiB'})
            self.assertEqual(recs, [])
            # an engine's tarball: into a stage directory of its own under its release name; the task verifies that
            # very file, then hands it to --from
            self.upload_pkg.write_bytes(b'TAR')
            obj, recs, _ = self.run1(shell, 'engine_install', {'name': 'sing-box', 'source': 'file'})
            self.assertEqual(obj['ok'], 1)
            (stage, _), cmd = self.job_cmd(recs[0])
            dest = Path(stage) / 'mu300-linux-vpn-singbox.tar.gz'
            self.assertEqual(cmd, ['sh', str(VPNB), '__verify', str(dest), 'mu300-linux-vpn-singbox.tar.gz', '0',
                                   str(self.vpn), 'engine', 'install', 'sing-box', '--from', str(dest)])
            self.assertEqual(dest.read_bytes(), b'TAR')
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
            self.assertFalse(self.upload_pkg.exists())
            obj, recs, _ = self.run1(shell, 'engine_install', {'name': 'wireguard', 'source': 'file'})
            self.assertEqual(obj['ok'], 0)
            self.upload_pkg.write_bytes(b'CORE')
            obj, recs, _ = self.run1(shell, 'core_install', {'source': 'file', 'unverified': '1'})
            (stage, _), cmd = self.job_cmd(recs[0])
            dest = Path(stage) / 'mu300-linux-vpn-core.tar.gz'
            self.assertEqual(cmd, ['sh', str(VPNB), '__verify', str(dest),
                                   'mu300-linux-vpn-core.tar.gz mu300-linux-vpn.tar.gz', '1',
                                   str(self.extra), 'install', 'vpn', '--from', str(dest)])
            self.clean_job()


class Replies(Mu300Vpn):
    def test_reads_wrap_the_cli_json(self):
        for shell in self.each_shell():
            r, _, _ = self.call(shell, 'profiles')
            self.assertEqual(self.reply(r), {'ok': 1, 'profiles': PROFILES})
            r, _, _ = self.call(shell, 'engines')
            self.assertEqual(self.reply(r), {'ok': 1, 'engines': ENGINES})
            (self.vpnrun / 'iface').write_text('sbtun\n')
            obj = self.reply(self.call(shell, 'status')[0])
            self.assertEqual((obj['ok'], obj['installed'], obj['status']), (1, 1, STATUS))
            self.assertIsInstance(obj['since'], int)
            self.assertIsInstance(obj['now'], int)

    def test_settings_are_an_object_of_the_shown_keys(self):
        for shell in self.each_shell():
            self.output('vpn.settings.--json', cli_out('settings-json'))
            obj = self.reply(self.call(shell, 'settings')[0])
            self.assertEqual(obj, {'ok': 1, 'settings': {'KILL_SWITCH': '1', 'TAILSCALE': '1', 'IPV6': 'auto',
                                                         'REMOTE_DNS': '1.1.1.1', 'BOOTSTRAP_DNS': '1.1.1.1',
                                                         'LAN_CIDRS': ''}})
            # the binaries' paths are not passed on, whatever they hold
            self.output('vpn.settings.--json', json.dumps(dict(json.loads(cli_out('settings-json')), XRAY='/x"y')))
            self.assertNotIn('XRAY', self.reply(self.call(shell, 'settings')[0])['settings'])
            self.output('vpn.settings.--json', 'KILL_SWITCH=1\n')
            self.assertEqual(self.reply(self.call(shell, 'settings')[0]),
                             {'ok': 0, 'error': 'The VPN settings could not be read'})

    def test_the_module_missing_or_too_old(self):
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'status', MU300_VPN_CMD=self.tmp / 'nope')[0])
            self.assertEqual(obj, {'ok': 1, 'installed': 0, 'extra': 1})
            obj = self.reply(self.call(shell, 'status', MU300_VPN_CMD=self.tmp / 'nope',
                                       MU300_EXTRA_CMD=self.tmp / 'nope')[0])
            self.assertEqual(obj, {'ok': 1, 'installed': 0, 'extra': 0})
            for method, params in (('profiles', {}), ('service', {'op': 'on'}), ('logs', {})):
                obj = self.reply(self.call(shell, method, params, MU300_VPN_CMD=self.tmp / 'nope')[0])
                self.assertEqual(obj, {'ok': 0, 'error': 'The VPN module is not installed'})
            # v1: status is text, --json unknown
            self.output('vpn.status', 'VPN: on\nprofile: office\n')
            obj = self.reply(self.call(shell, 'status')[0])
            self.assertEqual(obj, {'ok': 1, 'installed': 1, 'legacy': 1, 'extra': 1})
            self.output('vpn.status', json.dumps(STATUS))

    def test_cli_failures_carry_a_fixed_error_and_the_cli_message(self):
        cases = [
            ('vpn.use', '', 1, 'no such profile: x\n', 'use', {'id': 'x'},
             {'ok': 0, 'error': 'The profile could not be made the active one', 'detail': 'no such profile: x'}),
            ('vpn.profile.list', '{"error": "the profile store cannot be read here"}', 1, '', 'profiles', {},
             {'ok': 0, 'error': 'The profiles could not be read', 'detail': 'the profile store cannot be read here'}),
            ('vpn.service', '', 2, 'usage: mu300-vpn ...\n', 'service', {'op': 'on'}, None),
            ('vpn.on', '', 2, 'usage: mu300-vpn on\n', 'service', {'op': 'on'},
             {'ok': 0, 'error': 'The VPN module refused the request', 'detail': 'usage: mu300-vpn on'}),
            ('vpn.profile.list', 'not json', 0, '', 'profiles', {},
             {'ok': 0, 'error': 'The profiles could not be read'}),
            ('vpn.status', '{"error": "cannot read \\u001b[31m"}', 1, '', 'status', {},
             {'ok': 0, 'error': 'The VPN status could not be read', 'detail': 'cannot read [31m'}),
            ('vpn.latency', '{"id":"o","host":"h","port":443,"ms":null,"error":"timeout"}', 1, '', 'latency',
             {'id': 'o'}, {'ok': 1, 'result': {'id': 'o', 'host': 'h', 'port': 443, 'ms': None, 'error': 'timeout'}}),
        ]
        for shell in self.each_shell():
            for name, out, rc, err, method, params, want in cases:
                if want is None:
                    continue
                with self.subTest(case=(name, method)):
                    for p in self.out.iterdir():
                        p.unlink()
                    self.output(name, out, rc=rc, err=err)
                    obj = self.reply(self.call(shell, method, params)[0])
                    self.assertEqual(obj, want)

    def test_a_refusal_shows_its_reason_not_the_closing_line(self):
        """The CLI ends a refused import, edit or switch with a line that says only that nothing was done ("profile
        not added"); the reason is the line before it. The page showed the closing line for every refused config -
        "The profile could not be added: profile not added" - and never why (docs/FINDINGS.md 56)."""
        note = ('note: top-level keys of the file that are not used (this driver writes its own tun and dns; nothing '
                'listens but the TUN): port tun dns\n')
        cases = [
            ('vpn.profile', note + 'the mihomo config has no proxies or proxy-providers at the top level\n'
                                   'profile not added\n',
             'profile_add', {'source': 'text', 'data': 'mode: rule\n', 'type': 'mihomo', 'name': 'x'},
             'The profile could not be added', 'the mihomo config has no proxies or proxy-providers at the top level'),
            ('vpn.profile', 'the mihomo config (line 3) has a second YAML document (---)\nprofile office not changed\n',
             'profile_edit', {'id': 'office', 'config': 'a: b\n'},
             'The config was not saved', 'the mihomo config (line 3) has a second YAML document (---)'),
            ('vpn.use', note + 'mihomo rejects the config as this driver rebuilds it\n'
                               'profile sub does not check: the VPN stays on profile home\n',
             'use', {'id': 'sub'},
             'The profile could not be made the active one', 'mihomo rejects the config as this driver rebuilds it'),
            ('vpn.use', 'dae needs Linux 5.17 or newer with BTF, and this kernel is 5.4.210\n'
                        'profile hy cannot run on this system: the VPN stays on profile home\n',
             'use', {'id': 'hy'},
             'The profile could not be made the active one',
             'dae needs Linux 5.17 or newer with BTF, and this kernel is 5.4.210'),
            # a closing line alone is still the message, and so is a last line that is a reason itself
            ('vpn.profile', 'profile not added\n', 'profile_add',
             {'source': 'text', 'data': 'mode: rule\n', 'type': 'mihomo', 'name': 'x'},
             'The profile could not be added', 'profile not added'),
            ('vpn.use', 'the engines of profile sub are not installed: run mu300-vpn engines install mihomo '
                        '(the VPN stays on profile home)\n', 'use', {'id': 'sub'},
             'The profile could not be made the active one',
             'the engines of profile sub are not installed: run mu300-vpn engines install mihomo '
             '(the VPN stays on profile home)'),
            # a note is never the reason
            ('vpn.profile', note + 'profile not added\n', 'profile_add',
             {'source': 'text', 'data': 'mode: rule\n', 'type': 'mihomo', 'name': 'x'},
             'The profile could not be added', 'profile not added'),
        ]
        for shell in self.each_shell():
            for name, err, method, params, error, detail in cases:
                with self.subTest(case=(method, detail)):
                    for p in self.out.iterdir():
                        p.unlink()
                    self.output(name, '', rc=1, err=err)
                    obj = self.reply(self.call(shell, method, params)[0])
                    self.assertEqual(obj, {'ok': 0, 'error': error, 'detail': detail})

    def test_errors_are_english_sentences_in_the_catalogs(self):
        known = set(subprocess.run([sys.executable, str(TOP / 'tools' / 'luci-i18n.py'), 'extract'], check=True,
                                   capture_output=True, text=True, encoding='utf-8').stdout.splitlines())
        src = VPNB.read_text()
        import re
        msgs = set(re.findall(r"(?:refuse|cli_fail) '([^']+)'", src)) | set(re.findall(r"reply_val \w+ '([^']+)'", src))
        self.assertGreater(len(msgs), 30)
        self.assertEqual(msgs - known, set())

    def test_nothing_is_logged_and_a_revealed_config_stays_nowhere(self):
        self.output('vpn.profile.show', json.dumps(dict(PROFILES[0], config='PrivateKey = SECRETKEY\n')))
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'profile_reveal', {'id': 'office'})[0])
            self.assertEqual(obj, {'ok': 1, 'config': 'PrivateKey = SECRETKEY\n'})
            for root, _, files in os.walk(self.dash):
                for f in files:
                    self.assertNotIn(b'SECRETKEY', (Path(root) / f).read_bytes(), f)
            self.assertFalse((self.dash / 'last-outputs.log').exists())

    def test_the_log_is_escaped_and_capped(self):
        self.output('vpn.logs', 'line "1"\t\x1b[0m\n' + 'y' * 70000 + '\nlast\n')
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'logs')[0])
            self.assertEqual(obj['ok'], 1)
            self.assertLessEqual(len(obj['log']), 65536)
            self.assertTrue(obj['log'].endswith('last'))

    def test_the_job_reports_its_state(self):
        job = self.dash / 'vpn-job'
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'job')[0])
            self.assertEqual(obj['ok'], 1)
            job.mkdir(parents=True, exist_ok=True)
            (job / 'kind').write_text('latency-all\n')
            (job / 'state').write_text('done\n')
            (job / 'started').write_text('1760000000\n')
            (job / 'log').write_text('a "quoted" line\n\x1b[1mbold\n')
            (job / 'result').write_text('[{"id":"office","ms":48}]')
            obj = self.reply(self.call(shell, 'job')[0])
            self.assertEqual(obj['job'], {'kind': 'latency-all', 'state': 'done', 'started': 1760000000,
                                          'log': 'a "quoted" line\n\x1b[1mbold', 'result': [{'id': 'office', 'ms': 48}]})
            # running without a live process, long ago: failed
            (job / 'state').write_text('running\n')
            (job / 'pid').write_text('999999\n')
            self.assertEqual(self.reply(self.call(shell, 'job')[0])['job']['state'], 'failed')
            # a kind or state it does not know is not repeated
            (job / 'kind').write_text('$(reboot)\n')
            (job / 'state').write_text('exploded\n')
            (job / 'result').write_text('not json')
            got = self.reply(self.call(shell, 'job')[0])['job']
            self.assertEqual((got['kind'], got['state'], got['result']), (None, None, None))
            for p in job.iterdir():
                p.unlink()

    def test_the_task_runs_its_command_and_says_how_it_ended(self):
        """the detached shell itself (setsid is a pass-through here): stdin from the secret file, which is gone once
        open; the result and the log; upload.* removed"""
        self.stub('setsid', 'exec "$@"')
        self.output('vpn.subscription', 'imported 3 profiles\n')
        self.output('vpn.latency', '[{"id":"office","ms":48}]')
        job = self.dash / 'vpn-job'
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'sub_add', {'name': 'Mine', 'url': URL})[0])
            self.assertEqual(obj['ok'], 1)
            deadline = time.monotonic() + 10
            while (job / 'state').read_text() == 'running\n' and time.monotonic() < deadline:
                time.sleep(0.1)
            got = self.reply(self.call(shell, 'job')[0])['job']
            self.assertEqual((got['kind'], got['state'], got['log']), ('sub-add', 'done', 'imported 3 profiles'))
            self.assertEqual(self.staged(), [])
            self.reply(self.call(shell, 'latency', {'id': 'all'})[0])
            deadline = time.monotonic() + 10
            while (job / 'state').read_text() == 'running\n' and time.monotonic() < deadline:
                time.sleep(0.1)
            got = self.reply(self.call(shell, 'job')[0])['job']
            self.assertEqual((got['state'], got['result']), ('done', [{'id': 'office', 'ms': 48}]))


class Hardening(Mu300Vpn):
    """The second review: what is checked is what is used (no swap between the check and the use), one task at a time
    under a lock, an uploaded tarball installed only when the official SHA256SUMS lists it (or the user allowed an
    unverified file), and no URL or link in a message or a log."""

    SUMS_NAME = 'mu300-linux-vpn-xray.tar.gz'

    def verify(self, shell, data, sums, unv='0', curl_rc=0, url='https://example.invalid/SHA256SUMS',
               assets='mu300-linux-vpn-xray.tar.gz'):
        """run the task's __verify step on a file holding DATA, the official SHA256SUMS being SUMS"""
        f = self.tmp / 'stagefile'
        f.write_bytes(data)
        (self.tmp / 'sums').write_text(sums)
        self.stub('curl', f'cat "{self.tmp}/sums"; exit {curl_rc}')
        ran = self.tmp / 'ran'
        for old in (ran, self.tmp / 'ran.env', self.tmp / 'SHA256SUMS'):
            if old.exists():
                old.unlink()
        # (the command records what it installs, and whether mu300-extra would be told to take it unverified)
        r = subprocess.run(shell + [str(VPNB), '__verify', str(f), assets, unv, 'sh', '-c',
                                    f'cat "$1" > "{ran}"; printf %s "${{MU300_VPN_UNVERIFIED:-}}" > "{ran}.env"',
                                    'x', str(f)],
                           capture_output=True, text=True, env=self.env(MU300_VPN_SUMS_URL=url,
                                                                         MU300_DASH_DIR=self.dash), timeout=60)
        return r, (ran.read_bytes() if ran.exists() else None)

    def test_an_upload_is_installed_only_when_the_official_sums_list_it(self):
        import hashlib
        data = b'tarball'
        h = hashlib.sha256(data).hexdigest()
        for shell in self.each_shell():
            # listed under its own name: installed (the very file checked)
            r, ran = self.verify(shell, data, f'{h}  {self.SUMS_NAME}\n')
            self.assertEqual((r.returncode, ran), (0, data), r.stderr)
            self.assertIn('verified', r.stdout)
            # mu300-extra checks it again: the verified hash in a SHA256SUMS next to the staged file, nothing unverified
            self.assertEqual((self.tmp / 'SHA256SUMS').read_text(), f'{h}  stagefile\n')
            self.assertEqual((self.tmp / 'ran.env').read_text(), '')
            r, ran = self.verify(shell, data, f'{h.upper()} *{self.SUMS_NAME}\n')
            self.assertEqual((r.returncode, ran), (0, data), r.stderr)
            # not listed, listed under another asset, sums unreachable, a plain-http source: refused, nothing run
            for label, kw in (('other hash', {'sums': f'{"0" * 64}  {self.SUMS_NAME}\n'}),
                              ('other asset', {'sums': f'{h}  mu300-linux-vpn-dae.tar.gz\n'}),
                              ('fetch failed', {'sums': '', 'curl_rc': 22}),
                              ('http', {'sums': f'{h}  {self.SUMS_NAME}\n', 'url': 'http://example.invalid/S'})):
                with self.subTest(case=label):
                    r, ran = self.verify(shell, data, **kw)
                    self.assertEqual(r.returncode, 1)
                    self.assertIsNone(ran)
                    self.assertIn('not installed', r.stderr)
            # the user allowed an unverified file: installed, and the log says so
            r, ran = self.verify(shell, data, f'{"0" * 64}  {self.SUMS_NAME}\n', unv='1')
            self.assertEqual((r.returncode, ran), (0, data))
            self.assertIn('NOT VERIFIED', r.stdout)
            # (and mu300-extra, which refuses a file no SHA256SUMS lists, is told the user allowed it)
            self.assertEqual((self.tmp / 'ran.env').read_text(), '1')
            self.assertFalse((self.tmp / 'SHA256SUMS').exists())
            # the core accepts the core tarball or the bundle
            r, ran = self.verify(shell, data, f'{h}  mu300-linux-vpn.tar.gz\n',
                                 assets='mu300-linux-vpn-core.tar.gz mu300-linux-vpn.tar.gz')
            self.assertEqual(r.returncode, 0)

    def test_the_unverified_flag_is_checked(self):
        for shell in self.each_shell():
            for method, params in (('engine_install', {'name': 'xray', 'source': 'file', 'unverified': 'yes'}),
                                   ('core_install', {'source': 'file', 'unverified': '1; reboot'})):
                r, recs, _ = self.call(shell, method, params)
                self.assertEqual(self.reply(r), {'ok': 0, 'error': 'The unverified parameter must be 0 or 1'})
                self.assertEqual(recs, [])

    def test_a_swap_after_the_check_changes_nothing(self):
        """the task gets the staged copy: replacing the /tmp path afterwards (the attacker's swap) does not reach it"""
        for shell in self.each_shell():
            self.upload_pkg.write_bytes(b'GOOD')
            r, recs, _ = self.call(shell, 'engine_install', {'name': 'xray', 'source': 'file'})
            self.assertEqual(self.reply(r)['ok'], 1)
            (stage, _), cmd = self.job_cmd(recs[0])
            staged = Path(stage) / 'mu300-linux-vpn-xray.tar.gz'
            self.upload_pkg.write_bytes(b'EVIL')          # swapped in after the request took the file
            self.assertEqual(staged.read_bytes(), b'GOOD')
            self.assertEqual(cmd[3], str(staged))         # verified ...
            self.assertEqual(cmd[-1], str(staged))        # ... and installed: the same file
            self.assertNotIn(str(self.upload_pkg), cmd)
            self.clean_job()
            self.upload_pkg.unlink()

    def test_links_at_the_upload_path_are_refused(self):
        for shell in self.each_shell():
            target = self.tmp / 'target'
            target.write_bytes(b'root:x:0:0')
            for kind in ('symlink', 'hardlink'):
                with self.subTest(kind=kind):
                    (os.symlink if kind == 'symlink' else os.link)(target, self.upload_pkg)
                    r, recs, _ = self.call(shell, 'engine_install', {'name': 'xray', 'source': 'file'})
                    self.assertEqual(self.reply(r), {'ok': 0, 'error': 'No uploaded file'})
                    self.assertEqual(recs, [])
                    self.assertEqual(target.read_bytes(), b'root:x:0:0')
                    self.assertEqual(self.staged(), [])
                    if self.upload_pkg.exists() or self.upload_pkg.is_symlink():
                        self.upload_pkg.unlink()
            # a directory is not a file either
            self.upload_pkg.mkdir()
            r, recs, _ = self.call(shell, 'core_install', {'source': 'file'})
            self.assertEqual(self.reply(r), {'ok': 0, 'error': 'No uploaded file'})
            self.upload_pkg.rmdir()
            self.assertTrue(self.lock_free())

    def test_concurrent_requests_start_one_task(self):
        # setsid that takes a while: the task "runs" for 4 s
        self.stub('setsid', 't=$(mktemp "$STUB_CALLS/.tmp.XXXXXX"); printf "%s\\0" setsid "$@" > "$t"; '
                            'mv "$t" "$STUB_CALLS/call.${t##*.}"; sleep 4')
        for shell in self.each_shell():
            with ThreadPoolExecutor(max_workers=6) as ex:
                res = list(ex.map(lambda i: self.call(shell, 'engine_install', {'name': 'xray'}), range(6)))
            replies = [self.reply(r) for r, _, _ in res]
            started = [x for x in replies if x.get('ok') == 1]
            self.assertEqual(len(started), 1, replies)
            for x in replies:
                if x.get('ok') != 1:
                    self.assertIn(x['error'], ('Another VPN task is still running', 'The VPN panel is busy, try again'))
            self.assertEqual(sum(len(recs) for _, recs, _ in res), 1)
            time.sleep(4.5)
            self.clean_job()

    def test_the_lock_waits_for_its_holder_and_goes_with_it(self):
        """a request holding the lock (here: another process) makes the next one wait; it ends (even killed), the
        kernel releases the lock and the next one goes through. Nothing the request started keeps it."""
        job = self.dash / 'vpn-job'
        for shell in self.each_shell():
            job.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dash, 0o700)
            os.chmod(job, 0o700)
            holder = subprocess.Popen([sys.executable, '-c', FLOCK_HOLD, str(job / 'lock'), '3'])
            time.sleep(0.5)
            t0 = time.monotonic()
            r, recs, _ = self.call(shell, 'latency', {'id': 'all'})
            waited = time.monotonic() - t0
            holder.wait()
            self.assertEqual(self.reply(r)['ok'], 1)
            self.assertGreater(waited, 1.5)
            # a holder killed outright releases it too
            holder = subprocess.Popen([sys.executable, '-c', FLOCK_HOLD, str(job / 'lock'), '30'])
            time.sleep(0.5)
            holder.kill()
            holder.wait()
            self.clean_job()
            r, recs, _ = self.call(shell, 'latency', {'id': 'all'})
            self.assertEqual(self.reply(r)['ok'], 1)
            self.clean_job()

    def test_links_and_urls_are_redacted_in_messages_and_logs(self):
        link = 'vless://11111111-2222-3333-4444-555555555555@vpn.example.com:443#x'
        self.output('vpn.use', '', rc=1, err=f'bad link {link} and https://sub.example.com/x?token=SECRET\n')
        job = self.dash / 'vpn-job'
        for shell in self.each_shell():
            obj = self.reply(self.call(shell, 'use', {'id': 'x'})[0])
            self.assertEqual(obj['detail'], 'bad link vless://[hidden] and https://[hidden]')
            job.mkdir(parents=True, exist_ok=True)
            (job / 'kind').write_text('sub-add\n')
            (job / 'state').write_text('failed\n')
            # (uclient-fetch and wget name a redirect's target without its scheme: a signed download URL)
            (job / 'log').write_text(f'fetching https://sub.example.com/api?token=SECRET\nnode {link}\n'
                                     'Redirected to /asset/1?sig=SIGNED&jwt=SIGNED on release-assets.example.com\n')
            got = self.reply(self.call(shell, 'job')[0])['job']['log']
            self.assertNotIn('SECRET', got)
            self.assertNotIn('11111111', got)
            self.assertNotIn('SIGNED', got)
            self.assertIn('Redirected to [hidden] on release-assets.example.com', got)
            self.clean_job()


class Contract(Mu300Vpn):
    """Every output the real CLI gave (tests/fixtures/mu300-vpn-cli) through the backend: what the pages get"""

    def serve(self, stub, name):
        self.output(stub, cli_out(name), rc=int((CLI / f'{name}.rc').read_text()),
                    err=(CLI / f'{name}.err').read_text())

    def test_the_real_outputs_reach_the_pages(self):
        cases = [
            # (stub file, fixture, method, params, check(reply))
            ('vpn.status', 'status', 'status', {}, lambda o: self.assertEqual(o['status'], json.loads(cli_out('status')))),
            ('vpn.status', 'status-exit-ip', 'status', {'exit_ip': '1'},
             lambda o: self.assertEqual(o['status']['exit_ip'], '203.0.113.77')),
            ('vpn.profile.list', 'profile-list-sub', 'profiles', {},
             lambda o: self.assertEqual([p['source'] for p in o['profiles']],
                                        [p['source'] for p in json.loads(cli_out('profile-list-sub'))])),
            ('vpn.profile.show', 'profile-show-reveal', 'profile_reveal', {'id': 'home-wg'},
             lambda o: self.assertTrue(o['config'].startswith('[Interface]\n'))),
            ('vpn.profile.show', 'profile-show-missing', 'profile_reveal', {'id': 'nosuch'},
             lambda o: self.assertEqual(o, {'ok': 0, 'error': 'The config could not be read',
                                            'detail': 'no such profile: nosuch'})),
            ('vpn.engines', 'engines', 'engines', {}, lambda o: self.assertEqual(len(o['engines']), 6)),
            ('vpn.subscription.list', 'sub-list', 'subscriptions', {},
             lambda o: self.assertEqual((o['subscriptions'][0]['format'], o['subscriptions'][0]['count']), ('links', 2))),
            ('vpn.latency', 'latency-one', 'latency', {'id': 'trojan'},
             lambda o: self.assertEqual(o['result'], json.loads(cli_out('latency-one')))),
            ('vpn.status', 'status-running', 'status', {},
             lambda o: self.assertIn('since', o['status'])),
            ('vpn.latency', 'latency-udp', 'latency', {'id': 'home-wg'},
             lambda o: self.assertEqual((o['ok'], o['result']['error']), (1, 'udp'))),
            ('vpn.use', 'use-missing', 'use', {'id': 'nosuch'},
             lambda o: self.assertEqual(o, {'ok': 0, 'error': 'The profile could not be made the active one',
                                            'detail': 'no such profile: nosuch'})),
            ('vpn.restart', 'restart-off', 'service', {'op': 'restart'},
             lambda o: self.assertEqual(o['detail'], 'the VPN is off: mu300-vpn on')),
            ('vpn.profile', 'remove-active', 'profile_remove', {'id': 'office'},
             lambda o: self.assertEqual(o['error'], 'The profile could not be removed')),
        ]
        for shell in self.each_shell():
            for stub, name, method, params, check in cases:
                with self.subTest(case=name):
                    for f in self.out.iterdir():
                        f.unlink()
                    self.serve(stub, name)
                    check(self.reply(self.call(shell, method, params)[0]))

    def test_no_output_but_the_reveal_carries_a_secret(self):
        # the fixtures' credentials: a UUID, a trojan password, a WireGuard private key, a subscription token
        secrets = ['11111111-2222-3333-4444-555555555555', 'SECRETPASS', 'PASSWORD2', 'cHJpdmF0ZWtleXByaXZh',
                   '22222222-2222', 'sub-test.txt']
        for f in sorted(CLI.glob('*.out')):
            if f.stem == 'profile-show-reveal':
                continue
            for x in secrets:
                self.assertNotIn(x, f.read_text(), f.name)


class Views(unittest.TestCase):
    """the pages put what the backend sends into E() as text (inside an array), never as a lone string (HTML)"""

    def test_no_backend_value_as_html(self):
        import re
        for p in sorted(VIEWS.glob('vpn-*.js')) + [VIEWS.parent.parent / 'mu300' / 'vpn.js']:
            src = p.read_text()
            # E('tag', {...}, String(x)) or E('tag', {...}, p.name): a lone non-literal third argument
            for m in re.finditer(r"E\('\w+', \{[^{}]*\}, (?!\[|'|_\(|E\(|\w+\)|null|\w+\.map\(|Array\.isArray)([^,\n)]+)\)", src):
                self.fail(f'{p.name}: E() with a non-array content {m.group(1)!r}')
            self.assertNotIn('innerHTML', src, p.name)


if __name__ == '__main__':
    unittest.main()
