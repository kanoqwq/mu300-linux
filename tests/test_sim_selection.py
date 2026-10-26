"""SIM selection stays consistent for a boot; no hardware is contacted."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest
from test_sim_hot import FLOCK


class SimSelection(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('mu300-device', 'echo f50')
        self.stub('flock', FLOCK)
        self.state = self.tmp / 'run/mu300/sim-slot'
        self.config_lib = TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh'
        self.choice = self.tmp / 'choice'
        self.choice.write_text('0\n')
        self.stub('uci', r"""
[ "$1" != -q ] || shift
verb=$1; key=${2:-}
case $key in
 network.wan.sim_slot|network.wan.sim_slot=*) file="$STUBLOG/choice" ;;
 network.wan.sim_hot|network.wan.sim_hot=*) file="$STUBLOG/hot-choice" ;;
 network.wan.apn|network.wan.apn=*) file="$STUBLOG/apn" ;;
 network.wan.pdptype|network.wan.pdptype=*) file="$STUBLOG/pdptype" ;;
 network.wan.apn_internal|network.wan.apn_internal=*) file="$STUBLOG/apn_internal" ;;
 network.wan.pdptype_internal|network.wan.pdptype_internal=*) file="$STUBLOG/pdptype_internal" ;;
 *) file= ;;
esac
case $verb in
 get) [ -n "$file" ] && cat "$file" ;;
 set) [ -n "$file" ] && printf '%s\n' "${key#*=}" > "$file" ;;
 delete) [ -n "$file" ] && rm -f "$file" ;;
 commit) [ "${T_COMMIT_FAIL:-0}" != 1 ] ;;
 *) exit 1;;
esac
""")

    def env(self, **extra):
        extra.setdefault('MU300_DEVICE_CMD', self.stubs / 'mu300-device')
        extra.setdefault('MU300_SIM_CONFIG_LIB', self.config_lib)
        return super().env(**extra)

    def sim(self, shell, *args):
        return self.script(shell, BIN / 'mu300-sim', *args, MU300_SIM_ROOT=self.tmp)

    def native(self):
        (self.tmp / 'etc').mkdir(exist_ok=True)
        (self.tmp / 'etc/openwrt_release').touch()
        self.stub('mu300-device', 'echo u30air')
        self.backend = self.tmp / 'mu300-sim-u30'
        self.backend.write_text((BIN / 'mu300-sim-u30').read_text()
                                .replace('/run/', str(self.tmp / 'run') + '/')
                                .replace('/etc/', str(self.tmp / 'etc') + '/')
                                .replace('/opt/mu300/bin/mu300-device', str(self.stubs / 'mu300-device')))
        self.backend.chmod(0o755)

    def test_u30_manual_default_needs_no_hot_mode_modem_or_wan(self):
        self.native()
        for shell in self.each_shell():
            self.state.unlink(missing_ok=True)
            self.choice.write_text('0\n')
            result = self.script(shell, BIN / 'mu300-sim', 'default', '2',
                                 MU300_SIM_ROOT=self.tmp, MU300_SIM_U30_BACKEND=self.backend)
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual((status['version'], status['active'], status['default'], status['hot']), (1, 1, 2, 0))
            self.assertFalse(status['available'])
            self.assertEqual(self.state.read_text(), '0\n')
            self.assertEqual(self.choice.read_text(), '1\n')
            self.assertFalse((self.tmp / 'run/mu300-radio-freeze').exists())
            self.assertFalse((self.tmp / 'run/mu300/sim-switch.log').exists())

    def test_default_uses_card_numbers_and_says_so(self):
        """`default 1|2` takes the public card numbers (1 external, 2 internal); `select` takes 0/1. `default 0`
        used to print select's "must be 0 or 1" text, which made the command look broken. Each refusal now names
        its own numbering, and both valid values save the slot."""
        for shell in self.each_shell():
            result = self.sim(shell, 'default', '0')
            self.assertEqual(result.returncode, 2)
            self.assertIn('SIM card must be 1 (external) or 2 (internal)', result.stderr)
            self.assertNotIn('SIM slot must be 0 (external) or 1 (internal)', result.stderr)
            result = self.sim(shell, 'select', '2')
            self.assertEqual(result.returncode, 2)
            self.assertIn('SIM slot must be 0 (external) or 1 (internal)', result.stderr)
            result = self.sim(shell, 'default', '1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.choice.read_text(), '0\n')
            result = self.sim(shell, 'default', '2')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.choice.read_text(), '1\n')

    def test_u30_migration_copies_legacy_preference_and_profile_once(self):
        self.native()
        self.choice.unlink()
        (self.tmp / 'etc/mu300-sim-slot').write_text('1\n')
        (self.tmp / 'etc/mu300-sim-hot').write_text('1\n')
        (self.tmp / 'apn').write_text('carrier.example\n')
        (self.tmp / 'pdptype').write_text('IP\n')
        result = self.sim(['sh'], 'migrate')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.choice.read_text(), '1\n')
        self.assertEqual((self.tmp / 'hot-choice').read_text(), '1\n')
        self.assertEqual((self.tmp / 'apn_internal').read_text(), 'carrier.example\n')
        self.assertEqual((self.tmp / 'pdptype_internal').read_text(), 'IP\n')
        self.assertTrue((self.tmp / 'etc/mu300/sim-migration/mu300-sim-slot.migrated').exists())
        self.assertEqual(self.sim(['sh'], 'migrate').returncode, 0)

    def test_u30_migration_preserves_existing_choice_and_explicit_empty_apn(self):
        self.native()
        (self.tmp / 'hot-choice').write_text('0\n')
        (self.tmp / 'apn_internal').write_text('')
        (self.tmp / 'apn').write_text('old-carrier\n')
        (self.tmp / 'etc/mu300-sim-slot').write_text('1\n')
        (self.tmp / 'etc/mu300-sim-hot').write_text('1\n')
        self.assertEqual(self.sim(['sh'], 'migrate').returncode, 0)
        self.assertEqual(self.choice.read_text(), '0\n')
        self.assertEqual((self.tmp / 'hot-choice').read_text(), '0\n')
        self.assertEqual((self.tmp / 'apn_internal').read_text(), '')

    def test_u30_commit_failure_restores_visible_preference_and_keeps_legacy(self):
        self.native()
        result = self.script(['sh'], BIN / 'mu300-sim', 'default', '2', MU300_SIM_ROOT=self.tmp,
                             MU300_SIM_U30_BACKEND=self.backend, T_COMMIT_FAIL=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.choice.read_text(), '0\n')
        self.choice.unlink()
        (self.tmp / 'etc/mu300-sim-slot').write_text('1\n')
        result = self.script(['sh'], BIN / 'mu300-sim', 'migrate', MU300_SIM_ROOT=self.tmp, T_COMMIT_FAIL=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.choice.exists())
        self.assertTrue((self.tmp / 'etc/mu300-sim-slot').exists())
        self.assertFalse((self.tmp / 'etc/mu300/sim-migration/mu300-sim-slot.migrated').exists())

    def test_selection_refuses_other_devices(self):
        # (the U30 Air outside OpenWrt's native backend; the V50 boots SIM2 as the F50 does since FINDINGS 204,
        # tests/test_v50_sim.py)
        device = self.tmp / 'mu300-device'
        for shell, name in [(s, n) for s in self.each_shell() for n in ('u30air',)]:
            device.write_text(f'#!/bin/sh\necho {name}\n')
            device.chmod(0o755)
            self.choice.write_text('0\n')
            self.state.unlink(missing_ok=True)
            r = self.script(shell, BIN / 'mu300-sim', 'select', 'internal',
                            MU300_SIM_ROOT=self.tmp, MU300_DEVICE_CMD=device)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('F50 and V50 only', r.stderr)
            self.assertEqual(self.choice.read_text().strip(), '0')
            status = self.script(shell, BIN / 'mu300-sim', 'status', '--json',
                                 MU300_SIM_ROOT=self.tmp, MU300_DEVICE_CMD=device)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)['slot1_supported'], 0)
            self.state.write_text('1\n')
            active = self.script(shell, BIN / 'mu300-sim', 'active',
                                 MU300_SIM_ROOT=self.tmp, MU300_DEVICE_CMD=device)
            self.assertEqual(active.returncode, 0, active.stderr)
            self.assertEqual(active.stdout.strip(), '0')

    def test_common_default_and_json_status_interface(self):
        for shell in self.each_shell():
            self.state.unlink(missing_ok=True)
            self.choice.write_text('0\n')
            r = self.sim(shell, 'default', '2')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.choice.read_text().strip(), '1')
            status = self.sim(shell, 'status', '--json')
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout), {
                'version': 1, 'ok': 1, 'active': 1, 'default': 2, 'slot1_supported': 1, 'hot': 0,
                'available': False, 'result': 'reboot_required', 'busy': False, 'phase': 'idle',
                'error': None, 'capabilities': {'boot_selection': True, 'live_switch': False},
                'radio_ready': None, 'registered': None, 'wan_up': None,
            })
            hot = self.sim(shell, 'hot', '1')
            self.assertNotEqual(hot.returncode, 0)
            self.assertIn('live SIM switching', hot.stderr)

    def test_selection_requires_reboot_and_is_idempotent(self):
        for shell in self.each_shell():
            self.state.unlink(missing_ok=True)
            self.choice.write_text('0\n')
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '0')
            r = self.sim(shell, 'select', 'internal')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('until reboot', r.stdout)
            self.assertEqual(self.sim(shell, 'configured').stdout.strip(), '1')
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '0')
            self.state.unlink()  # /run is cleared by a reboot
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '1')
            self.assertEqual(self.sim(shell, 'select', 'external').returncode, 0)
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '1')
            self.state.unlink()
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '0')

    def test_the_pinned_external_card_is_answered_from_one_file_read(self):
        # every AT client asks "active" before every command: with the pin at 0 (nearly every device) neither the
        # settings library nor the device's name is read (measured on a U30 Air: 2.8 ms a call, 6.7 ms with them)
        self.stub('mu300-device', 'echo asked >> "$STUBLOG/device-asked"; echo f50')
        for shell in self.each_shell():
            (self.tmp / 'device-asked').unlink(missing_ok=True)
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text('0\n')
            r = self.script(shell, BIN / 'mu300-sim', 'active', MU300_SIM_ROOT=self.tmp,
                            MU300_SIM_CONFIG_LIB=self.tmp / 'no-such-library.sh')
            self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, '0', ''))
            self.assertFalse((self.tmp / 'device-asked').exists())
            # a pinned internal card is still checked against the device, as before
            self.state.write_text('1\n')
            self.assertEqual(self.sim(shell, 'active').stdout.strip(), '1')
            self.assertTrue((self.tmp / 'device-asked').exists())
            self.state.write_text('junk\n')
            self.assertNotEqual(self.sim(shell, 'active').returncode, 0)
            self.state.unlink()

    def test_invalid_selection_never_writes_or_executes(self):
        for shell in self.each_shell():
            for bad in ['2', '-1', '1;touch injected', '$(touch injected)', '1\n0']:
                self.choice.write_text('0\n')
                r = self.sim(shell, 'select', bad)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(self.choice.read_text().strip(), '0')
                self.assertFalse((self.tmp / 'injected').exists())
                # a bad stored value (hand-edited) never fails the AT owners that ask at start: it pins the
                # external card for this boot, with a warning, and the stored value stays as it is
                self.state.unlink(missing_ok=True)
                self.choice.write_text(bad)
                self.assertNotEqual(self.sim(shell, 'configured').returncode, 0)
                active = self.sim(shell, 'active')
                self.assertEqual(active.returncode, 0, active.stderr)
                self.assertEqual(active.stdout.strip(), '0')
                self.assertIn('not 0 or 1', active.stderr)
                self.assertEqual(self.state.read_text().strip(), '0')
                self.assertEqual(self.choice.read_text(), bad)

    def test_apn_with_a_quote_or_line_break_never_reaches_the_modem(self):
        s = (BIN / 'mobile-data').read_text()
        # define_context and what it calls (tests/test_mu300cell.py MobileDataContext has the rest of it)
        start = s.index('# settings_private FILE:')
        block = s[start:s.index('# Only one bring-up at a time', start)]
        self.assertIn('    define_context "$1" || exit 1\n', s)
        for shell in self.each_shell():
            for apn, rc in [('internet', 0), ('cmnet', 0), ('a-b.c_d', 0), ('x"y', 1), ('x\\y', 1), ('x\ry', 1), ('x\ny', 1), ('', 0)]:
                (self.tmp / 'calls').unlink(missing_ok=True)
                code = ('at() { echo "$1" >> "$STUBLOG/calls"; }; CID=1; APN_AUTH_MARK=$STUBLOG/mark; MD_CONF=$STUBLOG/conf\n' +
                        block + '\ndefine_context "$APN" || exit 1; echo defined')
                r = self.sh(shell, code, APN=apn, SIM_SLOT='0')
                self.assertEqual(r.returncode, rc, (apn, r.stderr))
                self.assertEqual('defined' in r.stdout, rc == 0, apn)
                calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
                self.assertEqual(calls, [f'AT+CGDCONT=1,"IP","{apn}"'] if apn and rc == 0 else [], apn)

    def test_concurrent_first_readers_publish_one_complete_slot(self):
        for shell in self.each_shell():
            self.state.unlink(missing_ok=True)
            self.choice.write_text('1\n')
            ps = [subprocess.Popen(shell + [str(BIN / 'mu300-sim'), 'active'],
                                   env=self.env(MU300_SIM_ROOT=self.tmp), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True) for _ in range(8)]
            for p in ps:
                out, err = p.communicate(timeout=15)
                self.assertEqual(p.returncode, 0, err)
                self.assertEqual(out.strip(), '1')

    def test_procd_channels_and_dashboard_follow_active_slot(self):
        helper = self.tmp / 'mu300-sim'
        helper.write_text('#!/bin/sh\ncat "$STUBLOG/choice"\n')
        helper.chmod(0o755)
        for shell in self.each_shell():
            for slot in (0, 1):
                self.choice.write_text(str(slot))
                for source, dashboard in [
                    (TOP / 'openwrt/overlay/etc/init.d/mu300-atd', False),
                    (TOP / 'openwrt/luci-overlay/etc/init.d/mu300-atd-dash', True),
                ]:
                    s = source.read_text().replace('/opt/mu300/bin/mu300-sim', str(helper))
                    code = 'procd_open_instance() { echo instance "$@"; }; procd_set_param() { echo param "$@"; }; procd_close_instance() { :; };\n' + s + '\nstart_service\n'
                    r = self.sh(shell, code)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    if dashboard:
                        if slot == 1: self.assertNotIn('instance ', r.stdout)
                        else:
                            self.assertIn('/dev/stty_nr6', r.stdout)
                            self.assertIn('/dev/stty_nr7', r.stdout)
                    else:
                        self.assertIn('/dev/stty_nr' + str(slot * 3 + 1), r.stdout)
                        self.assertIn('MU300_AT_DEV=/dev/stty_nr' + str(slot * 3 + 2), r.stdout)

    def test_data_route_command_failure_stops_slot1_only(self):
        s = (BIN / 'mobile-data').read_text()
        start = s.index('    if [ "$SIM_SLOT:$SIM_NATIVE" = 1:0 ]; then', s.index('mark dial-addr-ok'))
        block = s[start:s.index('    prefix=$(mask2prefix', start)]
        for shell in self.each_shell():
            for slot, reply, rc in [(0, 'ERROR', 0), (1, 'OK', 0), (1, 'ERROR', 1)]:
                calls = self.tmp / 'calls'; calls.write_text('')
                code = 'at() { echo "$1" >> "$STUBLOG/calls"; echo "$REPLY"; }; dial() {\n' + block + '\necho connected; }; dial'
                r = self.sh(shell, code, SIM_SLOT=slot, SIM_NATIVE=0, REPLY=reply)
                self.assertEqual(r.returncode, rc, r.stderr)
                self.assertEqual(calls.read_text().splitlines(), [] if slot == 0 else ['AT+SPSWDATA'])
                if rc: self.assertNotIn('connected', r.stdout)

    def test_missing_neighbours_do_not_abort_dashboard(self):
        import shutil
        node = shutil.which('node')
        if not node: self.skipTest('node unavailable')
        js = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        start = js.index('function neighborRows(')
        body = js[start:js.index('\n}', start) + 2]
        code = '''const _=s=>s, esc=s=>s, qLevel=()=>'', qCol=()=>'';
''' + body + '''
for (const neigh of [null, [null], {}, [null, {rat:'nr', band:41, pci:315, arfcn:504990, rsrp:-85}]]) {
  if (!neighborRows({neigh}, []).includes('<tr>')) throw Error('missing row');
}
'''
        r = subprocess.run([node, '-e', code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_empty_lte_neighbours_are_not_null_entries(self):
        s = (TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/cell').read_text()
        start = s.index('LN=$(sp_fragment ltenb')
        block = s[start:s.index('\nCOPSJ=', start)]
        for shell in self.each_shell():
            r = self.sh(shell, 'sp_fragment() { echo null; }; nr_neighbours() { :; };\n' + block + '\necho "$NEIGH"')
            value = json.loads(r.stdout)
            self.assertIn(value, [None, []])

    def test_profile_fields_survive_changing_selector(self):
        # LuCI removes hidden options unless retain is set: changing slots must not erase an APN.
        s = (TOP / 'openwrt/overlay/www/luci-static/resources/protocol/mu300cell.js').read_text()
        for field in ['apn', 'apn_internal', 'pdptype', 'pdptype_internal']:
            block = re.search(r"o = s.taboption\([^;]*'" + field + r"'[^;]*;([\s\S]*?)(?=\n\s*o =|\n\s*}\n)", s)
            self.assertIsNotNone(block, field)
            self.assertIn('o.retain = true', block[1], field)

    def test_ipv6_prefix_comes_from_the_selected_card_only(self):
        source = TOP / 'openwrt/luci-overlay/opt/mu300/bin/ndp-learn'
        helper = self.tmp / 'mu300-sim'
        helper.write_text('#!/bin/sh\necho 1\n')
        helper.chmod(0o755)
        script = self.tmp / 'ndp-learn'
        script.write_text(source.read_text().replace('/opt/mu300/bin/mu300-sim', str(helper)))
        addresses = self.tmp / 'if_inet6'
        addresses.write_text('24090001000200030000000000000001 05 40 00 00 sipa_eth0\n'
                             '24090004000500060000000000000001 0d 40 00 00 sipa_eth8\n')
        for shell in self.each_shell():
            r = self.script(shell, script, '--prefix', MU300_IF_INET6=addresses)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), '2409:4:5:6::/64')


INFO = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/dashboard-info'
HOME = TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js'
PROTO = TOP / 'openwrt/overlay/www/luci-static/resources/protocol/mu300cell.js'


class SimInTheSnapshot(ShellTest):
    """Issue #194: dashboard-info's "sim" is mu300-sim's own account - the card in use, the one asked for, whether
    the device has slot 1 - so that a page can say that an internal SIM chosen on a device without one is not used.
    The real mu300-sim runs, on a fake root, with a fake mu300-device."""

    def setUp(self):
        super().setUp()
        self.stub('busybox', 'shift 4; exec "$@"')   # busybox timeout -s KILL N CMD...
        for name in ('ip', 'ubus', 'iw', 'jsonfilter'):
            self.stub(name, 'exit 1')
        self.choice = self.tmp / 'choice'
        self.stub('uci', 'case "$*" in "-q get network.wan.sim_slot") cat "$STUBLOG/choice" ;; *) exit 1 ;; esac')
        self.device = self.tmp / 'mu300-device'
        self.root = self.tmp / 'root'
        # the selector as the pages' backend finds it: the real script, on a root of the test's
        self.simbin = self.tmp / 'mu300-sim'
        self.simbin.write_text(f'#!/bin/sh\nMU300_SIM_ROOT="{self.root}" MU300_DEVICE_CMD="{self.device}" '
                               f'MU300_SIM_CONFIG_LIB="{TOP / "rootfs/overlay/opt/mu300/lib/sim-config.sh"}" '
                               f'exec sh "{BIN / "mu300-sim"}" "$@"\n')
        self.simbin.chmod(0o755)

    def info(self, shell, device, wanted, boot_slot=None, **env):
        self.device.write_text(f'#!/bin/sh\necho {device}\n')
        self.device.chmod(0o755)
        self.choice.write_text(f'{wanted}\n')
        state = self.root / 'run/mu300/sim-slot'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.unlink(missing_ok=True)
        if boot_slot is not None:
            state.write_text(f'{boot_slot}\n')
        r = self.script(shell, INFO, MU300_SIM_BIN=env.pop('simbin', self.simbin), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def native(self):
        (self.root / 'etc').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        self.backend = self.tmp / 'mu300-sim-u30'
        self.backend.write_text((BIN / 'mu300-sim-u30').read_text()
                               .replace('/run/', str(self.root / 'run') + '/')
                               .replace('/etc/', str(self.root / 'etc') + '/')
                               .replace('/opt/mu300/bin/mu300-device', str(self.device))
                               .replace('/opt/mu300/bin/mu300-at', str(self.stubs / 'mu300-at')))
        self.backend.chmod(0o755)
        self.stub('flock', FLOCK)
        self.stub('mu300-at', 'touch "$STUBLOG/unexpected-at"; exit 1')

    def test_native_u30_snapshot_keeps_slot_1_supported_without_probing_the_modem(self):
        self.native()
        for shell in self.each_shell():
            for boot_slot in (0, 1):
                i = self.info(shell, 'u30air', 1, boot_slot, MU300_SIM_U30_BACKEND=self.backend)
                self.assertEqual(i['sim'], {'slot': boot_slot, 'wanted': 1, 'slot1_supported': 1})
                self.assertEqual(i['data_device'], 'sipa_eth' + str(boot_slot * 8))
                self.assertEqual(self.choice.read_text(), '1\n')
        self.assertFalse((self.tmp / 'unexpected-at').exists())

    def test_unknown_u30_active_card_is_not_replaced_with_the_routing_pin(self):
        self.native()
        marker = self.root / 'run/mu300/sim-active-unknown'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        for shell in self.each_shell():
            i = self.info(shell, 'u30air', 1, 1, MU300_SIM_U30_BACKEND=self.backend)
            self.assertIsNone(i['sim'])
            self.assertEqual(i['data_device'], 'sipa_eth8')
        self.assertFalse((self.tmp / 'unexpected-at').exists())

    def test_the_reported_case_an_internal_sim_chosen_on_a_u30_air(self):
        for shell in self.each_shell():
            for boot_slot in (None, 1):     # chosen before this boot (the state says 1), or the state not yet written
                i = self.info(shell, 'u30air', 1, boot_slot)
                self.assertEqual(i['sim'], {'slot': 0, 'wanted': 1, 'slot1_supported': 0})
                self.assertEqual(i['data_device'], 'sipa_eth0')     # and the page's figures are slot 0's

    def test_the_f50(self):
        for shell in self.each_shell():
            self.assertEqual(self.info(shell, 'f50', 0)['sim'], {'slot': 0, 'wanted': 0, 'slot1_supported': 1})
            i = self.info(shell, 'f50', 1, boot_slot=1)
            self.assertEqual(i['sim'], {'slot': 1, 'wanted': 1, 'slot1_supported': 1})
            self.assertEqual(i['data_device'], 'sipa_eth8')
            # selected, not yet rebooted
            self.assertEqual(self.info(shell, 'f50', 1, boot_slot=0)['sim'], {'slot': 0, 'wanted': 1, 'slot1_supported': 1})

    def test_a_u30_air_on_the_external_sim_has_nothing_to_say(self):
        for shell in self.each_shell():
            self.assertEqual(self.info(shell, 'u30air', 0)['sim'], {'slot': 0, 'wanted': 0, 'slot1_supported': 0})

    def test_without_the_selector_or_its_status_the_field_is_null(self):
        odd = self.tmp / 'odd-sim'
        odd.write_text('#!/bin/sh\ncase $1 in active) echo 0 ;; *) echo \'{"ok":1,"active":x,"default":"; rm -rf /"}\' ;; esac\n')
        odd.chmod(0o755)
        for shell in self.each_shell():
            self.assertIsNone(self.info(shell, 'f50', 0, simbin=self.tmp / 'none')['sim'])
            self.assertIsNone(self.info(shell, 'f50', 0, simbin=odd)['sim'])


def _node(js):
    r = subprocess.run(['node', '-'], input=js, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the LuCI JS tests')
class SimSourceOnThePages(unittest.TestCase):
    """The interface page (protocol/mu300cell.js) offers the internal SIM only where mu300-sim supports it, and the
    home page says when the one chosen is not the one in use. Both run here under Node with LuCI's objects faked."""

    def options(self, sysinfo, saved, fail=False):
        """The SIM source option after its load(): its choices, labels and description. sysinfo is what mu300dash
        sysinfo answers (fail: the call is rejected - no panel, no access), saved the uci value."""
        js = r'''
const src = ''' + json.dumps(PROTO.read_text(encoding='utf-8')) + r''';
const opts = {};
function Opt(name, description) { this.name = name; this.description = description; this.keylist = []; this.vallist = []; }
Opt.prototype.value = function(k, v) { this.keylist.push(k); this.vallist.push(v); };
Opt.prototype.depends = function() {};
const form = {};
for (const c of [ 'ListValue', 'Value', 'Flag', 'DynamicList' ]) form[c] = function() {};
form.ListValue.prototype.load = function(section) { return ''' + json.dumps(saved) + r'''; };
const s = { taboption: (tab, cls, name, title, description) => (opts[name] = new Opt(name, description)) };
const network = { registerProtocol: (name, proto) => proto, getIfnameOf: (x) => x };
const calls = [];
const rpc = { declare: (spec) => () => { calls.push(spec.object + '.' + spec.method);
	return ''' + ('Promise.reject(new Error("no such object"))' if fail else 'Promise.resolve(' + json.dumps(sysinfo) + ')') + r'''; } };
const L = { resolveDefault: (p, d) => Promise.resolve(p).catch(() => d) };
const proto = new Function('form', 'network', 'rpc', 'L', '_', src)(form, network, rpc, L, (t) => t);
proto.renderFormOptions(s);
const o = opts.sim_slot;
Promise.resolve(o.load('wan')).then((cur) => {
	process.stdout.write(JSON.stringify({ cur: cur, keys: o.keylist, labels: o.vallist, description: o.description,
		calls: calls, others: Object.keys(opts) }));
});
'''
        return _node(js)

    BOTH = ['External SIM (slot 0)', 'Internal SIM (slot 1)']

    def test_a_device_without_slot_1_is_not_offered_the_internal_sim(self):
        o = self.options({'sim': {'slot': 0, 'wanted': 0, 'slot1_supported': 0}}, '0')
        self.assertEqual((o['keys'], o['labels']), (['0'], ['External SIM (slot 0)']))
        self.assertIn('not supported on this device', o['description'])
        self.assertEqual(o['calls'], ['mu300dash.sysinfo'])     # the local snapshot: no AT command behind it
        o = self.options({'sim': {'slot': 0, 'wanted': 0, 'slot1_supported': 0}}, None)   # nothing saved yet
        self.assertEqual(o['keys'], ['0'])

    def test_an_internal_sim_already_saved_is_shown_for_what_it_is(self):
        # the reporter's state: the page must show what is saved and that it has no effect, not "External"
        o = self.options({'sim': {'slot': 0, 'wanted': 1, 'slot1_supported': 0}}, '1')
        self.assertEqual((o['cur'], o['keys']), ('1', ['0', '1']))
        self.assertEqual(o['labels'], ['External SIM (slot 0)', 'Internal SIM (slot 1): not supported on this device'])
        self.assertIn('the external SIM is used', o['description'])

    def test_the_f50_keeps_both(self):
        for saved in ('0', '1'):
            o = self.options({'sim': {'slot': 0, 'wanted': int(saved), 'slot1_supported': 1}}, saved)
            self.assertEqual((o['cur'], o['keys'], o['labels']), (saved, ['0', '1'], self.BOTH))
            self.assertIn('Applies after reboot', o['description'])

    def test_native_u30_keeps_both_cards_in_the_wan_form(self):
        for saved in ('0', '1'):
            o = self.options({'sim': {'slot': 1, 'wanted': int(saved), 'slot1_supported': 1}}, saved)
            self.assertEqual((o['cur'], o['keys'], o['labels']), (saved, ['0', '1'], self.BOTH))

    def test_nothing_known_changes_nothing(self):
        # no panel (the call fails), an older panel (no "sim"), no selector (null), or an answer that is not 0 or 1
        for sysinfo, fail in (({}, True), ({}, False), ({'error': 'Dashboard info failed'}, False), ({'sim': None}, False),
                              ({'sim': {'slot1_supported': 'no'}}, False), ({'sim': {}}, False)):
            o = self.options(sysinfo, '1', fail)
            self.assertEqual((o['cur'], o['keys'], o['labels']), ('1', ['0', '1'], self.BOTH), sysinfo)
            self.assertIn('Applies after reboot', o['description'])

    def test_the_other_options_are_still_there(self):
        o = self.options({'sim': {'slot': 0, 'wanted': 0, 'slot1_supported': 0}}, '0')
        self.assertEqual(o['others'], ['sim_slot', 'apn_internal', 'pdptype_internal', 'roaming_internal', 'apn', 'pdptype', 'roaming',
                                       'username', 'password', 'auth', 'peerdns', 'dns'])

    def paint(self, sim):
        src = HOME.read_text(encoding='utf-8')
        start = src.index('paintSim: function(sim) {')
        start = src.index('function', start)
        end = src.index('\n\t},', start)
        js = r'''
const _ = (s) => s;
const out = {}, row = { style: { display: 'none' } };
const M = { set: (id, t) => { out[id] = t; }, v: (id) => id === 'simsrc-r' ? row : null };
const self = { paintSim: ''' + src[start:end] + '\n\t}' + r''' };
self.paintSim(''' + json.dumps(sim) + r''');
process.stdout.write(JSON.stringify({ shown: row.style.display !== 'none', text: out.simsrc }));
'''
        return _node(js)

    def test_the_home_page_says_so(self):
        src = HOME.read_text(encoding='utf-8')
        self.assertIn('id="mud-simsrc-r" style="display:none"', src)
        self.assertIn('this.paintSim(i.sim);', src)
        t = self.paint({'slot': 0, 'wanted': 1, 'slot1_supported': 0})
        self.assertEqual(t, {'shown': True,
                             'text': 'Internal SIM selected, but not supported on this device: the external SIM is in use'})
        t = self.paint({'slot': 0, 'wanted': 1, 'slot1_supported': 1})
        self.assertEqual(t, {'shown': True, 'text': 'Internal SIM selected: in use after a reboot'})
        for sim in ({'slot': 0, 'wanted': 0, 'slot1_supported': 0}, {'slot': 0, 'wanted': 0, 'slot1_supported': 1},
                    {'slot': 1, 'wanted': 1, 'slot1_supported': 1}, {'slot': 1, 'wanted': 0, 'slot1_supported': 1},
                    None, {}):
            self.assertEqual(self.paint(sim), {'shown': False, 'text': '--'}, sim)
