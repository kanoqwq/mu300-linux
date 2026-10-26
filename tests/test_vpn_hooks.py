"""The VPN is a module of its own (dikeckaan/mu300-linux-vpn, installed as the vpn extra): the images carry no
mu300-vpn, and every place of mu300-linux that works with it must do nothing harmful without it - and, with the VPN
on and its kill switch on, fail closed (lib/vpn-orphan.sh). mu300-power and wifi-client have their own tests of this
(test_power, test_wifi_client); here the orphan check itself, mobile-data, the toolkit's menu and the LuCI panel."""
import json
import shutil
import unittest

from helpers import BIN, TOP, ShellTest

TOOLKIT = BIN / 'mu300-toolkit'
ADAPTERS = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem'
ORPHAN_LIB = BIN.parent / 'lib' / 'vpn-orphan.sh'


class Orphaned(ShellTest):
    """vpn_killswitch_orphaned: the VPN wanted with its kill switch, read from the files as mu300-vpn reads them, and
    no mu300-vpn"""

    def check(self, shell, conf=None, settings=None, store=True, cmd=None):
        root = self.tmp / 'root'
        shutil.rmtree(root, ignore_errors=True)
        (root / 'etc/mu300').mkdir(parents=True)
        if conf is not None:
            (root / 'etc/mu300/vpn.conf').write_text(conf)
        if settings is not None:
            (root / 'etc/mu300/vpn').mkdir()
            (root / 'etc/mu300/vpn/settings').write_text(settings)
            if store:
                (root / 'etc/mu300/vpn/profiles').mkdir()
        r = self.sh(shell, f'. "{ORPHAN_LIB}"; vpn_killswitch_orphaned "{cmd or self.tmp / "no-mu300-vpn"}" "{root}" '
                           '&& echo yes || echo no')
        return r.stdout.strip()

    def test_the_matrix(self):
        self.stub('mu300-vpn', 'exit 0')
        cases = [
            # (vpn.conf, store settings, store in use, orphaned)
            (None, None, True, 'no'),
            ('ENABLE=0\n', None, True, 'no'),
            ('ENABLE=1\n', None, True, 'yes'),                       # no KILL_SWITCH line: on, mu300-vpn's default
            ('ENABLE=1\nKILL_SWITCH=1\n', None, True, 'yes'),
            ('ENABLE=1\nKILL_SWITCH=0\n', None, True, 'no'),
            ("ENABLE='1'\nKILL_SWITCH='0'\n", None, True, 'no'),
            ('ENABLE="1"\nKILL_SWITCH="1"\n', None, True, 'yes'),
            ('ENABLE=1  # on\n', None, True, 'yes'),
            ('ENABLE=0\nENABLE=1\n', None, True, 'yes'),             # the last line wins
            ('ENABLE=1\nENABLE=0\n', None, True, 'no'),
            ('ENABLE=yes\n', None, True, 'no'),                      # only 1 is on
            ('ENABLE=1\nKILL_SWITCH=yes\n', None, True, 'yes'),       # only 0 is off
            # the store: its settings, not vpn.conf's legacy line
            ('ENABLE=1\nKILL_SWITCH=1\n', "KILL_SWITCH='0'\n", True, 'no'),
            ('ENABLE=1\nKILL_SWITCH=0\n', "KILL_SWITCH='1'\n", True, 'yes'),
            ('ENABLE=1\nKILL_SWITCH=0\n', 'TAILSCALE=1\n', True, 'yes'),  # not in the settings: the default, on
            ('ENABLE=1\nKILL_SWITCH=0\n', "KILL_SWITCH='1'\n", False, 'no'),  # no profiles: not the store yet
            ('ENABLE=0\n', "KILL_SWITCH='1'\n", True, 'no'),
        ]
        for shell in self.each_shell():
            for conf, settings, store, want in cases:
                self.assertEqual(self.check(shell, conf, settings, store), want, (shell, conf, settings, store))
            # mu300-vpn there: never orphaned
            self.assertEqual(self.check(shell, 'ENABLE=1\n', cmd=self.stubs / 'mu300-vpn'), 'no')

    def test_the_copies_are_the_same(self):
        # mu300-update (runs before the new system exists) and android-install.sh (runs on Android) carry their own
        # copy of the check: the same two functions, word for word
        import re
        lib = ORPHAN_LIB.read_text()

        def fns(text):
            return [re.search(rf'\n{n}\(\) \{{\n.*?\n\}}\n', '\n' + text, re.S).group(0)
                    for n in ('vpn_conf_value', 'vpn_killswitch_wanted')]
        for f in (BIN / 'mu300-update', TOP / 'tools/android-install.sh'):
            self.assertEqual(fns(f.read_text()), fns(lib), f)

    def test_the_engine_missing(self):
        """vpn_engine_missing: the VPN on, and its active profile's engine extra not installed (module v2: each
        engine is an extra; module v1 and the bundle carry xray and sing-box in the vpn extra)"""
        root = self.tmp / 'root'
        disk = self.tmp / 'disk'

        def case(shell, typ, enable='1', have=()):
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(disk, ignore_errors=True)
            (root / 'etc/mu300/vpn/profiles/p').mkdir(parents=True)
            (root / 'etc/mu300/vpn.conf').write_text(f'ENABLE={enable}\n')
            (root / 'etc/mu300/vpn/active').write_text('p\n')
            (root / 'etc/mu300/vpn/profiles/p/meta').write_text(f"NAME='p'\nTYPE='{typ}'\n")
            for path in have:
                (disk / 'extra' / path).parent.mkdir(parents=True, exist_ok=True)
                (disk / 'extra' / path).write_text('#!/bin/sh\n')
                (disk / 'extra' / path).chmod(0o755)
            r = self.sh(shell, f'. "{ORPHAN_LIB}"; vpn_engine_missing "{root}" || echo none', MU300_DISK=disk)
            return r.stdout.strip()

        for shell in self.each_shell():
            self.assertEqual(case(shell, 'xray'), 'vpn-xray')
            self.assertEqual(case(shell, 'xray', enable='0'), 'none')
            self.assertEqual(case(shell, 'xray', have=('vpn-xray/bin/xray',)), 'none')
            self.assertEqual(case(shell, 'xray', have=('vpn/bin/xray',)), 'none')          # v1, the bundle
            self.assertEqual(case(shell, 'sing-box', have=('vpn/bin/sing-box',)), 'none')
            self.assertEqual(case(shell, 'dae', have=('vpn/bin/xray',)), 'vpn-dae')
            self.assertEqual(case(shell, 'mihomo', have=('vpn-mihomo/bin/mihomo',)), 'none')
            self.assertEqual(case(shell, 'wireguard'), 'none')                           # a package, not an extra

    def test_nothing_is_sourced(self):
        for shell in self.each_shell():
            self.check(shell, 'ENABLE=1\nKILL_SWITCH=$(touch "$STUBLOG/ran")\n',
                       "KILL_SWITCH='$(touch \"$STUBLOG/ran\")'\n")
            self.assertFalse((self.tmp / 'ran').exists())


class MobileData(ShellTest):
    """mobile-data up with the kill switch orphaned: the bearer stays down, every attempt says why"""

    def up(self, conf):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        root = self.tmp / 'root'
        (root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (root / 'etc/mu300/vpn.conf').write_text(conf)
        return self.sh(['bash'], f'MU300_LIB=1; . "{BIN}/mobile-data"; modem_stack() {{ true; }}; '
                                 'tty_setup() { echo TTY-SETUP; exit 0; }; up_locked || echo "rc=$?"',
                       MU300_SYSROOT=root, MU300_VPN_CMD=self.tmp / 'no-mu300-vpn', MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)

    def test_refused_with_the_kill_switch(self):
        r = self.up('ENABLE=1\nKILL_SWITCH=1\n')
        self.assertIn('rc=1', r.stdout)
        self.assertNotIn('TTY-SETUP', r.stdout)
        self.assertIn("the VPN's kill switch is on but the VPN module is missing: mobile data stays down", r.stderr)
        self.assertIn('mu300-extra install vpn', r.stderr)

    def test_goes_on_without_it(self):
        for conf in ('ENABLE=0\n', 'ENABLE=1\nKILL_SWITCH=0\n'):
            r = self.up(conf)
            self.assertIn('TTY-SETUP', r.stdout, conf)
            self.assertNotIn('kill switch', r.stderr, conf)


class Dashboard(ShellTest):
    def info(self, shell, conf, cmd=None):
        root = self.tmp / 'root'
        (root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (root / 'etc/mu300/vpn.conf').write_text(conf)
        self.stub('busybox', 'shift 4; exec "$@"')
        for name in ('ubus', 'ip', 'iw', 'uci'):
            self.stub(name, 'exit 1')
        r = self.script(shell, ADAPTERS / 'dashboard-info', MU300_SYSROOT=root, MU300_VPN_ORPHAN_LIB=ORPHAN_LIB,
                        MU300_VPN_CMD=cmd or self.tmp / 'no-mu300-vpn', MU300_POWER_SUPPLY_DIR=self.tmp / 'psy')
        return json.loads(r.stdout)['vpn']

    def test_orphaned_flag(self):
        self.stub('mu300-vpn', 'exit 0')
        for shell in self.each_shell():
            self.assertEqual(self.info(shell, 'ENABLE=1\nKILL_SWITCH=1\n')['orphaned'], 1)
            self.assertEqual(self.info(shell, 'ENABLE=1\nKILL_SWITCH=0\n')['orphaned'], 0)
            self.assertEqual(self.info(shell, 'ENABLE=1\n', cmd=self.stubs / 'mu300-vpn')['orphaned'], 0)


class Toolkit(ShellTest):
    """menu_vpn of mu300-toolkit, run on its own: the VPN menu with the module, how to get it without"""

    def setUp(self):
        super().setUp()
        src = TOOLKIT.read_text()
        self.assertLess(src.index('\nvpn_enabled() {'), src.index('\nmenu_signal() {'))
        self.block = src[src.index('\nvpn_enabled() {'):src.index('\nmenu_signal() {')]
        self.stub('mu300-extra', 'echo "mu300-extra $*" >> "$STUBLOG/calls"')

    def run_menu(self, shell, vpn, answers, conf=None):
        root = self.tmp / 'root'
        (root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (root / 'etc/mu300/vpn.conf').write_text(conf or 'ENABLE=0\n')
        return self._run_menu(shell, vpn, answers, root)

    def _run_menu(self, shell, vpn, answers, root):
        pre = ('title() { echo "== $1"; }; pause() { :; }; state_color() { echo "$1"; }; svc() { echo inactive; }\n'
               'C=; N=; D=; B=; G=\nVPN_CONF=/nonexistent\n')
        return self.sh(shell, pre + self.block + '\nmenu_vpn; echo "rc=$?"', stdin=answers, MU300_VPN_CMD=vpn,
                       MU300_EXTRA_CMD=self.stubs / 'mu300-extra', MU300_SYSROOT=root,
                       MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)

    def calls(self):
        f = self.tmp / 'calls'
        c = f.read_text() if f.exists() else ''
        f.unlink(missing_ok=True)
        return c

    def test_without_the_module_it_says_how_to_get_it(self):
        for shell in self.each_shell():
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '0\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn', r.stdout)
            self.assertIn('sudo mu300-extra install vpn', r.stdout)
            self.assertIn('--from', r.stdout)
            self.assertEqual(self.calls(), '')
            # 1: installs it there and then
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '1\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.calls(), 'mu300-extra install vpn\n')

    def test_without_the_module_and_the_kill_switch_on_it_says_so(self):
        for shell in self.each_shell():
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '0\n', conf='ENABLE=1\nKILL_SWITCH=1\n')
            self.assertIn('mobile data and the Wi-Fi client', r.stdout)
            self.assertIn('stay down until it is installed', r.stdout)
            self.assertIn('Install it now', r.stdout)
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '0\n')
            self.assertNotIn('stay down', r.stdout)

    def test_with_the_module_it_is_the_vpn_menu(self):
        self.stub('mu300-vpn', 'echo "mu300-vpn $*" >> "$STUBLOG/calls"; [ "$1" != status ] || echo "state: off"')
        for shell in self.each_shell():
            r = self.run_menu(shell, self.stubs / 'mu300-vpn', '0\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('state: off', r.stdout)
            self.assertNotIn('mu300-extra install vpn', r.stdout)
            self.assertIn('mu300-vpn status', self.calls())

    def test_the_menus_name_it_and_leave_its_service_out(self):
        src = TOOLKIT.read_text()
        self.assertIn('VPN (not installed: how to add it)', src)
        # the services list shows mu300-vpn only with the module
        self.assertIn('[ "$s" != mu300-vpn ] || vpn_present', src)
        # the menus name the VPN only to someone who has the module (mu300-extra's test: its mu300-vpn in the vpn
        # extra on the disk), or had it (the kill switch orphaned): the main menu's label and the Network item
        self.assertIn('vpn_installed() { [ -x "${MU300_DISK:-/mnt/mu300-disk}/extra/vpn/bin/mu300-vpn" ]; }', src)
        self.assertIn('vpn_shown() { vpn_installed || vpn_orphaned; }', src)
        self.assertIn('"Network, mobile data$(vpn_shown && echo \', VPN\')"', src)

    def test_the_network_menu_lists_the_vpn_only_with_the_module(self):
        src = TOOLKIT.read_text()
        network = src[src.index('\nmenu_network() {'):src.index('\nMU300_SERVICES=')]
        pre = ('menu() { shift; for l in "$@"; do echo "item: $l"; done; read -r CHOICE || CHOICE=0; }\n'
               'menu_vpn() { echo "-> vpn"; }; menu_ttl() { echo "-> ttl"; }; menu_wifi() { echo "-> wifi"; }\n'
               'menu_roaming() { echo "-> roaming"; }\n'
               'title() { :; }; pause() { :; }; svc() { :; }; monitor() { :; }; menu_signal() { :; }; is_openwrt() { false; }\n'
               'C=; N=; D=; B=; G=\nVPN_CONF=/nonexistent\n')
        # (the stubs after the block: its own menu_vpn is not what is under test here)
        code = self.block + '\n' + pre + network + '\nmenu_network; echo "rc=$?"'
        disk = self.tmp / 'disk'
        root = self.tmp / 'root'
        module = disk / 'extra/vpn/bin/mu300-vpn'

        def run(shell, answers, conf='ENABLE=0\n'):
            (root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
            (root / 'etc/mu300/vpn.conf').write_text(conf)
            return self.sh(shell, code, stdin=answers, MU300_DISK=disk, MU300_SYSROOT=root,
                           MU300_VPN_CMD=self.tmp / 'no-such-mu300-vpn', MU300_EXTRA_CMD=self.stubs / 'mu300-extra',
                           MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)

        for shell in self.each_shell():
            shutil.rmtree(disk, ignore_errors=True)
            # without the module: no word of a VPN, TTL is item 8, data roaming 9 and 10 goes back
            r = run(shell, '8\n9\n10\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertNotIn('VPN', r.stdout)
            self.assertEqual(r.stdout.count('-> ttl'), 1)
            self.assertEqual(r.stdout.count('-> roaming'), 1)
            self.assertNotIn('-> vpn', r.stdout)
            # the VPN on with its kill switch and the module gone: the item is there (it says why mobile data is down)
            r = run(shell, '8\n0\n', conf='ENABLE=1\nKILL_SWITCH=1\n')
            self.assertIn('item: VPN (not installed: how to add it)', r.stdout)
            self.assertIn('-> vpn', r.stdout)
            # the module on the disk (mu300-extra's test), not linked into this system: the item, TTL is 9, data
            # roaming 10
            module.parent.mkdir(parents=True)
            module.write_text('#!/bin/sh\n')
            module.chmod(0o755)
            r = run(shell, '8\n9\n10\n0\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('item: VPN (not installed: how to add it)', r.stdout)
            self.assertEqual(r.stdout.count('-> vpn'), 1)
            self.assertEqual(r.stdout.count('-> ttl'), 1)
            self.assertEqual(r.stdout.count('-> roaming'), 1)
            # linked too: the plain item
            r = self.sh(shell, code, stdin='0\n', MU300_DISK=disk, MU300_SYSROOT=root, MU300_VPN_CMD=module,
                        MU300_EXTRA_CMD=self.stubs / 'mu300-extra', MU300_VPN_ORPHAN_LIB=ORPHAN_LIB)
            self.assertIn('item: VPN\n', r.stdout)


class Static(unittest.TestCase):
    def test_mobile_data_guard_is_optional(self):
        # a missing mu300-vpn must never stop mobile data from coming up
        src = (BIN / 'mobile-data').read_text()
        self.assertRegex(src, r'"\$\{MU300_VPN_CMD:-/opt/mu300/bin/mu300-vpn\}" guard >/dev/null 2>&1 \|\| true')

    def test_wifi_client_asks_only_a_present_mu300_vpn(self):
        self.assertIn('if [ -x "$VPN_CMD" ] && ! "$VPN_CMD" guard', (BIN / 'wifi-client').read_text())

    def test_luci_vpn_capability_is_the_module(self):
        info = (ADAPTERS / 'dashboard-info').read_text()
        self.assertIn('CAP_VPN=0; [ -x /opt/mu300/bin/mu300-vpn ] && [ -x /etc/init.d/mu300-vpn ] && CAP_VPN=1', info)
        act = (ADAPTERS / 'action').read_text()
        self.assertIn('if [ -x /opt/mu300/bin/mu300-vpn ] && [ -x /etc/init.d/mu300-vpn ]; then', act)


if __name__ == '__main__':
    unittest.main()
