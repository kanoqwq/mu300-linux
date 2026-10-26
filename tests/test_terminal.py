"""System > Terminal on openwrt-luci (ttyd): the one-time token of rpcd's mu300term, ttyd's command
mu300-terminal-login (a root shell for such a token and nothing else), the image's ttyd configuration (the LAN
address only, never a port on the cellular side), the ACL, the menu and the build."""
import json
import os
import re
import subprocess
import time
import unittest
from pathlib import Path

from helpers import TOP, ShellTest

APP = TOP / 'openwrt' / 'luci-app-mu300'
LOGIN = APP / 'root' / 'usr' / 'libexec' / 'mu300-terminal-login'
RPC = APP / 'root' / 'usr' / 'libexec' / 'rpcd' / 'mu300term'
HOTPLUG = APP / 'root' / 'etc' / 'hotplug.d' / 'iface' / '95-mu300-terminal'
TTYD_CONF = TOP / 'openwrt' / 'luci-overlay' / 'etc' / 'config' / 'ttyd'
BUILD = TOP / 'openwrt' / 'build-rootfs.sh'
ACL = APP / 'root' / 'usr' / 'share' / 'rpcd' / 'acl.d' / 'luci-app-mu300.json'
MENU = APP / 'root' / 'usr' / 'share' / 'luci' / 'menu.d' / 'luci-app-mu300.json'

# a uci that answers "uci -q get KEY" from $UCI_DB (KEY=VALUE lines), as OpenWrt's does for the ttyd config
UCI = r'''
[ "$1" = -q ] && shift
[ "$1" = get ] || exit 1
v=$(sed -n "s/^$2=//p" "$UCI_DB" | head -n1)
[ -n "$v" ] || exit 1
printf '%s\n' "$v"
'''
IMAGE_UCI = '''ttyd.lan=ttyd
ttyd.lan.enable=1
ttyd.lan.port=7681
ttyd.lan.command=/usr/libexec/mu300-terminal-login
ttyd.mu300=terminal
ttyd.mu300.idle=900
'''


def uci_config(text):
    """{section: (type, {option: value or [list]})} of a uci config file"""
    out, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r"config (\S+)(?: '([^']*)')?$", line)
        if m:
            cur = m.group(2) or f'@{len(out)}'
            out[cur] = (m.group(1), {})
            continue
        m = re.match(r"(option|list) (\S+) '([^']*)'$", line)
        assert m and cur, line
        if m.group(1) == 'list':
            out[cur][1].setdefault(m.group(2), []).append(m.group(3))
        else:
            out[cur][1][m.group(2)] = m.group(3)
    return out


class Token(ShellTest):
    """rpcd mu300term token: a 32-hex one-time token file, 0600 in a 0700 directory, or a fixed code"""

    def setUp(self):
        super().setUp()
        self.stub('uci', UCI)
        self.db = self.tmp / 'uci'
        self.db.write_text(IMAGE_UCI)
        self.ttyd = self.tmp / 'ttyd'
        self.ttyd.write_text('#!/bin/sh\n')
        self.ttyd.chmod(0o755)
        self.dir = self.tmp / 'run' / 'mu300-terminal'
        net = self.tmp / 'network.sh'
        net.write_text('network_get_ipaddr() { eval "$1=192.168.77.1"; }\n')
        self.base = dict(UCI_DB=self.db, MU300_TTYD=self.ttyd, MU300_TERM_DIR=self.dir, MU300_NETWORK_SH=net)

    def call(self, shell, **env):
        r = self.script(shell, RPC, 'call', 'token', stdin='{}', **{**self.base, **env})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_list(self):
        for s in self.each_shell():
            r = self.script(s, RPC, 'list', **self.base)
            self.assertEqual(json.loads(r.stdout), {'token': {}})

    def test_token_is_a_private_file(self):
        for s in self.each_shell():
            r = self.call(s)
            self.assertEqual(r['ok'], 1)
            self.assertRegex(r['token'], r'^[0-9a-f]{32}$')
            self.assertEqual((r['port'], r['lan'], r['ttl'], r['idle']), (7681, '192.168.77.1', 60, 900))
            f = self.dir / r['token']
            self.assertTrue(f.is_file())
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.dir.stat().st_mode & 0o777, 0o700)
            # a second call is another token
            self.assertNotEqual(self.call(s)['token'], r['token'])

    def test_codes(self):
        for s in self.each_shell():
            self.assertEqual(self.call(s, MU300_TTYD=self.tmp / 'none'), {'ok': 0, 'code': 'missing'})
            self.db.write_text(IMAGE_UCI.replace('ttyd.lan.enable=1', 'ttyd.lan.enable=0'))
            self.assertEqual(self.call(s), {'ok': 0, 'code': 'off'})
            # an instance that runs another command (/bin/login, a shell) gets no token: the token opens nothing there
            self.db.write_text(IMAGE_UCI.replace('/usr/libexec/mu300-terminal-login', '/bin/login'))
            self.assertEqual(self.call(s), {'ok': 0, 'code': 'off'})
            self.db.write_text('')
            self.assertEqual(self.call(s), {'ok': 0, 'code': 'off'})
            self.db.write_text(IMAGE_UCI)

    def test_old_tokens_go_and_at_most_16_wait(self):
        for s in self.each_shell():
            self.dir.mkdir(parents=True, exist_ok=True)
            for f in self.dir.iterdir():
                f.unlink()
            old = self.dir / ('a' * 32)
            old.write_text('')
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            toks = [self.call(s) for _ in range(17)]
            self.assertFalse(old.exists())
            self.assertEqual([t['ok'] for t in toks], [1] * 16 + [0])
            self.assertEqual(toks[-1], {'ok': 0, 'code': 'busy'})

    def test_foreign_directory_or_link_is_replaced(self):
        for s in self.each_shell():
            target = self.tmp / 'elsewhere'
            target.mkdir(exist_ok=True)
            self.dir.parent.mkdir(parents=True, exist_ok=True)
            if self.dir.exists() or self.dir.is_symlink():
                if self.dir.is_symlink():
                    self.dir.unlink()
                else:
                    for f in self.dir.iterdir():
                        f.unlink()
                    self.dir.rmdir()
            self.dir.symlink_to(target)
            r = self.call(s)
            self.assertEqual(r['ok'], 1)
            self.assertFalse(self.dir.is_symlink())
            self.assertEqual(list(target.iterdir()), [])

    def test_unknown_method(self):
        for s in self.each_shell():
            r = self.script(s, RPC, 'call', 'shell', stdin='{"cmd":"id"}', **self.base)
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'code': 'internal'})


class Login(ShellTest):
    """mu300-terminal-login: a token from mu300term once, within its TTL, else a message and no shell"""

    def setUp(self):
        super().setUp()
        self.stub('uci', UCI)
        self.db = self.tmp / 'uci'
        self.db.write_text(IMAGE_UCI)
        self.dir = self.tmp / 'run'
        self.dir.mkdir(mode=0o700)
        self.login = self.tmp / 'login'
        self.login.write_text('#!/bin/sh\necho "LOGIN $*"\n')
        self.login.chmod(0o755)
        self.base = dict(UCI_DB=self.db, MU300_TERM_DIR=self.dir, MU300_TERM_LOGIN=self.login, MU300_TERM_DENY_WAIT=0,
                         MU300_TERM_IDLE=0)

    def token(self, age=0):
        tok = os.urandom(16).hex()
        f = self.dir / tok
        f.write_text('')
        if age:
            os.utime(f, (time.time() - age, time.time() - age))
        return tok

    def run_login(self, shell, *args, **env):
        return self.script(shell, LOGIN, *args, **{**self.base, **env})

    def test_a_token_opens_a_root_login_once(self):
        for s in self.each_shell():
            tok = self.token()
            r = self.run_login(s, tok)
            self.assertEqual(r.stdout.strip(), 'LOGIN -f root')
            self.assertEqual(list(self.dir.iterdir()), [])
            again = self.run_login(s, tok)
            self.assertEqual(again.returncode, 1)
            self.assertIn('expired or was already used', again.stdout)
            self.assertNotIn('LOGIN', again.stdout)

    def test_everything_else_is_refused(self):
        for s in self.each_shell():
            tok = self.token()
            for args in [(), ('0' * 32,), (tok.upper(),), (tok[:31],), (tok + '0',), (tok, tok), ('../' + tok,),
                         ('-f',), ("$(id)",)]:
                with self.subTest(args=args):
                    r = self.run_login(s, *args)
                    self.assertEqual(r.returncode, 1)
                    self.assertNotIn('LOGIN', r.stdout)
            # the token those refusals did not use still works
            self.assertIn('LOGIN -f root', self.run_login(s, tok).stdout)

    def test_an_old_token_is_refused_and_removed(self):
        for s in self.each_shell():
            tok = self.token(age=120)
            r = self.run_login(s, tok)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn('LOGIN', r.stdout)
            self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_directory_that_is_a_link_is_not_trusted(self):
        for s in self.each_shell():
            tok = self.token()
            link = self.tmp / 'link'
            if not link.is_symlink():
                link.symlink_to(self.dir)
            r = self.run_login(s, tok, MU300_TERM_DIR=link)
            self.assertNotIn('LOGIN', r.stdout)
            (self.dir / tok).unlink()


class Image(unittest.TestCase):
    """the image's ttyd: on the LAN address only, the token command, origin check; no port on the cellular side"""

    def test_ttyd_config(self):
        conf = uci_config(TTYD_CONF.read_text())
        ttyd = {n: o for n, (t, o) in conf.items() if t == 'ttyd'}
        self.assertEqual(set(ttyd), {'lan'})
        o = ttyd['lan']
        self.assertEqual(o['interface'], '@lan')
        self.assertEqual(o['command'], '/usr/libexec/mu300-terminal-login')
        self.assertEqual((o['url_arg'], o['check_origin'], o['enable']), ('1', '1', '1'))
        self.assertEqual(o['port'], '7681')
        self.assertNotIn('ipv6', o)
        self.assertNotIn('credential', o)
        self.assertNotIn('readonly', o)
        self.assertEqual(conf['mu300'], ('terminal', {'idle': '900'}))

    def test_no_port_of_the_terminal_on_the_cellular_side(self):
        # nothing the OpenWrt images carry opens the terminal's port in the firewall, or puts ttyd on wan
        for d in ('overlay', 'luci-overlay', 'luci-app-mu300'):
            for p in (TOP / 'openwrt' / d).rglob('*'):
                if not p.is_file() or p.suffix in ('.po', '.md'):
                    continue
                text = p.read_text(errors='replace')
                with self.subTest(p=str(p.relative_to(TOP))):
                    self.assertNotRegex(text, r"(dest_port|src_dport|dport)\D{0,8}768[12]")
                    self.assertNotRegex(text, r"firewall\.[^\n]*768[12]")
                    self.assertNotRegex(text, r"ttyd\.[^\n]*interface=.?@?wan")
        # the wan zone keeps OpenWrt's input REJECT: no default the images set opens it
        for p in list((TOP / 'openwrt' / 'overlay' / 'etc' / 'uci-defaults').iterdir()) + \
                list((TOP / 'openwrt' / 'luci-overlay' / 'etc' / 'uci-defaults').iterdir()):
            text = p.read_text()
            with self.subTest(p=p.name):
                self.assertNotRegex(text, r"\$zone\.input='?ACCEPT")
                for m in re.finditer(r"name='wan'.*?(?:\n\n|$)", text, re.S):
                    self.assertNotIn("input='ACCEPT'", m.group(0))

    def test_idle_notice_reaches_the_browser_before_the_shell_ends(self):
        # ttyd closes the websocket as soon as the shell ends: the line first, a pause, then the hangup
        text = LOGIN.read_text()
        notice = text.index('"Closed after $took without activity."')
        self.assertLess(notice, text.index('sleep "${MU300_TERM_NOTICE_WAIT:-3}"'))
        self.assertLess(text.index('sleep "${MU300_TERM_NOTICE_WAIT:-3}"'), text.index('kill -HUP "$pid"'))
        # one minute is singular, under a minute in seconds
        case = text[text.index('case $IDLE in [0-9]'):text.index('esac', text.index('case $IDLE in [0-9]')) + 4]
        for idle, want in ((30, '30 seconds'), (60, '1 minute'), (119, '1 minute'), (120, '2 minutes'), (900, '15 minutes')):
            r = subprocess.run(['sh', '-c', f'IDLE={idle}; {case}; printf %s "$took"'], capture_output=True, text=True)
            self.assertEqual(r.stdout, want, idle)

    def test_hotplug_restarts_ttyd_when_the_lan_comes_up(self):
        text = HOTPLUG.read_text()
        self.assertIn('[ "$ACTION" = ifup ] && [ "$INTERFACE" = lan ] || exit 0', text)
        self.assertIn('/etc/init.d/ttyd restart', text)

    def test_build_installs_and_configures_ttyd_for_the_panel_only(self):
        b = BUILD.read_text()
        self.assertIn('[ -d /in/luci-plugin ] && apk add ttyd luci-app-ttyd >/dev/null', b)
        self.assertEqual(b.count('apk add ttyd'), 1)
        # before the translation loop, which adds luci-i18n-ttyd-tr/zh-cn for the installed luci-app-ttyd
        self.assertLess(b.index('apk add ttyd luci-app-ttyd'), b.index('for c in base $(apk list --installed'))
        block = b[b.index('if [ -d /in/luci-plugin ]; then\n    [ -d /in/luci-overlay ]'):]
        self.assertIn('rm -f $R/usr/share/luci/menu.d/luci-app-ttyd.json', block)
        self.assertIn('/usr/libexec/rpcd/mu300term', block)
        self.assertIn('ttyd is not configured for the panel', block)
        self.assertIn('[ -e $R/etc/rc.d/S99ttyd ]', b)

    def test_programs_are_executable(self):
        for p in (LOGIN, RPC, HOTPLUG):
            with self.subTest(p=p.name):
                self.assertTrue(p.stat().st_mode & 0o111)


class Panel(unittest.TestCase):
    def test_acl_grants_the_token_and_nothing_else(self):
        acl = json.loads(ACL.read_text())
        t = acl['luci-app-mu300-terminal']
        self.assertEqual(t['write'], {'ubus': {'mu300term': ['token']}})
        self.assertEqual(t['read'], {'uci': ['ttyd']})
        # the panel's own group does not reach the terminal
        self.assertNotIn('mu300term', json.dumps(acl['luci-app-mu300']))

    def test_menu(self):
        menu = json.loads(MENU.read_text())
        self.assertEqual(menu['admin/system/terminal/shell']['action'], {'type': 'view', 'path': 'mu300/terminal'})
        self.assertEqual(menu['admin/system/terminal']['depends']['acl'], ['luci-app-mu300-terminal'])
        self.assertEqual(menu['admin/system/terminal/shell']['depends']['acl'], ['luci-app-mu300-terminal'])
        self.assertEqual(menu['admin/system/terminal/config']['action']['path'], 'ttyd/config')

    def test_page_takes_the_token_only_from_the_backend(self):
        js = (APP / 'htdocs/luci-static/resources/view/mu300/terminal.js').read_text()
        self.assertIn("/^[0-9a-f]{32}$/.test(String(r.token || ''))", js)
        self.assertIn("'http://' + host + ':' + port + '/?arg=' + r.token", js)
        common = (APP / 'htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("object: 'mu300term', method: 'token'", common)


if __name__ == '__main__':
    unittest.main()
