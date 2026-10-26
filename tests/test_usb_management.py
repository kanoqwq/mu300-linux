"""USB device management of the panel (device-usb): role switch, host mode, USB NIC reattachment, and the USB
network mode that boot/init applies on the next boot (D12, K7, K8). Ported from the kanoqwq fork
(tests/test_usb_management.py at 35a1c55); the fork's init-order checks are replaced by D12's file contract."""
import json
import re
import time
import unittest

from helpers import ShellTest, TOP

APP = TOP / 'openwrt/luci-app-mu300/root'
USB = APP / 'usr/libexec/unisoc-modem/device-usb'
INIT = (TOP / 'boot/init').read_text()


class USBManagement(ShellTest):
    def setUp(self):
        super().setUp()
        self.role = self.tmp / 'role'
        self.role.write_text('device\n')
        self.net_file = self.tmp / 'etc' / 'mu300' / 'usb-net'
        self.applied = self.tmp / 'applied'
        self.net_class = self.tmp / 'net'
        self.net_class.mkdir()
        self.usb_bus = self.tmp / 'usb-bus'
        self.usb_bus.mkdir()
        self.rescan_marker = self.tmp / 'rescan-done'
        self.stub('uci', '''
cmd=$1; [ "$cmd" = -q ] && { shift; cmd=$1; }; shift
case "$cmd" in
  get)
    case "$1" in
      unisoc_modem.usb) echo device ;;
      unisoc_modem.usb.*) key=${1##*.}; [ -f "$STUBLOG/uci.$key" ] && cat "$STUBLOG/uci.$key" ;;
      unisoc_modem.main.lan_device) echo br-lan ;;
      network.*.ports) [ -f "$STUBLOG/ports" ] && cat "$STUBLOG/ports" ;;
    esac ;;
  set)
    key=${1%%=*}; val=${1#*=}; printf '%s' "$val" > "$STUBLOG/uci.${key##*.}" ;;
  show) [ "$1" = network ] && echo "network.@device[0].name='br-lan'" ;;
  add_list) printf '%s' "${1##*=}" > "$STUBLOG/ports" ;;
  commit) echo "$1" >> "$STUBLOG/commits" ;;
esac
''')
        self.stub('logger', ':')
        helper = self.tmp / 'safe-role'
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$MU300_USB_ROLE_FILE"\n')
        helper.chmod(0o755)
        self.helper = helper
        reload_script = self.tmp / 'network'
        reload_script.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$STUBLOG/reloads"\n')
        reload_script.chmod(0o755)
        self.reload_script = reload_script
        self.stub('ip', 'printf "%s\\n" "$*" >> "$STUBLOG/ip-calls"')

    def call_usb(self, shell, *args, **env):
        base = dict(MU300_USB_ROLE_FILE=self.role,
                    MU300_USB_NET_FILE=self.net_file,
                    MU300_USB_APPLIED_FILE=self.applied,
                    MU300_USB_NET_CLASS=self.net_class,
                    MU300_USB_BUS=self.usb_bus,
                    MU300_USB_RESCAN_MARKER=self.rescan_marker,
                    MU300_USB_RESCAN_DELAY='0',
                    MU300_USB_ROLE_COMMAND=self.helper,
                    MU300_USB_NETWORK_SERVICE=self.reload_script,
                    MU300_USB_IP_BIN=self.stubs / 'ip')
        base.update(env)
        return self.script(shell, USB, *args, **base)

    def reply(self, result):
        self.assertEqual('', result.stderr)
        return json.loads(result.stdout)

    def wait_text(self, path, value):
        # Role application is detached until after the RPC acknowledgement; wait for the state, not a fixed time.
        for _ in range(200):
            if path.exists() and path.read_text().strip() == value:
                return
            time.sleep(0.02)
        self.assertEqual(path.read_text().strip() if path.exists() else None, value)

    def reset(self):
        for f in ('uci.role_auto', 'uci.net_auto', 'uci.net_mode', 'uci.net_scope'):
            (self.tmp / f).unlink(missing_ok=True)
        self.net_file.unlink(missing_ok=True)
        self.applied.unlink(missing_ok=True)
        self.role.write_text('device\n')

    # --- the USB network mode (D12, K7, K8)

    def test_mode_file_is_the_bare_mode_init_reads(self):
        for shell in self.each_shell():
            for mode in ('ncm', 'ecm', 'rndis'):
                self.reset()
                self.assertEqual({'ok': 1}, self.reply(self.call_usb(shell, 'set-net', mode, 'permanent', '1')))
                self.assertEqual(f'{mode}\n', self.net_file.read_text())
                self.assertEqual(0o600, self.net_file.stat().st_mode & 0o777)
                self.assertEqual([], [p.name for p in self.net_file.parent.iterdir() if p != self.net_file])

    def test_init_reads_what_the_panel_wrote(self):
        block = re.search(r'# --- usb-net begin\n(.*?)# --- usb-net end', INIT, re.S).group(1)
        root = self.net_file.parents[2]
        for shell in self.each_shell():
            for mode, functions in (('ncm', 'ncm ecm'), ('ecm', 'ecm ncm'), ('rndis', 'rndis ncm ecm')):
                self.reset()
                self.call_usb(shell, 'set-net', mode, 'permanent', '1')
                out = self.sh(shell, block + f'\nusb_net_policy "{root}"').stdout
                self.assertEqual(functions, out.strip(), mode)

    def test_auto_off_removes_the_file(self):
        for shell in self.each_shell():
            self.reset()
            self.call_usb(shell, 'set-net', 'ecm', 'permanent', '1')
            self.assertTrue(self.net_file.exists())
            self.assertEqual({'ok': 1}, self.reply(self.call_usb(shell, 'set-net', 'ecm', 'permanent', '0')))
            self.assertFalse(self.net_file.exists())

    def test_invalid_values_are_refused(self):
        for shell in self.each_shell():
            self.reset()
            for args in (('ECM', 'permanent', '1'), ('ecm;x', 'permanent', '1'), ('ecm', 'always', '1'),
                         ('ecm', 'once', '2')):
                r = self.reply(self.call_usb(shell, 'set-net', *args))
                self.assertEqual(0, r['ok'], args)
            self.assertFalse(self.net_file.exists())

    def test_once_consumed_only_after_boot_marker(self):
        for shell in self.each_shell():
            self.reset()
            (self.tmp / 'uci.role_auto').write_text('0')
            self.call_usb(shell, 'set-net', 'rndis', 'once', '1')
            self.assertEqual('rndis\n', self.net_file.read_text())
            self.call_usb(shell, 'boot')
            self.assertTrue(self.net_file.exists())
            self.applied.write_text('rndis\n')
            self.call_usb(shell, 'boot')
            self.assertFalse(self.net_file.exists())
            self.assertEqual((self.tmp / 'uci.net_auto').read_text(), '0')

    def test_permanent_survives_the_marker(self):
        for shell in self.each_shell():
            self.reset()
            self.call_usb(shell, 'set-net', 'ecm', 'permanent', '1')
            self.applied.write_text('ecm\n')
            self.call_usb(shell, 'boot')
            self.assertEqual('ecm\n', self.net_file.read_text())
            self.assertEqual((self.tmp / 'uci.net_auto').read_text(), '1')

    def test_get_reports_the_saved_policy(self):
        for shell in self.each_shell():
            self.reset()
            self.call_usb(shell, 'set-net', 'ecm', 'once', '1')
            r = self.reply(self.call_usb(shell, 'get'))
            self.assertEqual((r['net_mode'], r['net_scope'], r['net_auto'], r['role']), ('ecm', 'once', 1, 'device'))

    def test_defaults_are_the_mu300_paths(self):
        src = USB.read_text()
        self.assertIn('${MU300_USB_NET_FILE:-/etc/mu300/usb-net}', src)
        self.assertIn('${MU300_USB_APPLIED_FILE:-/run/mu300/usb-net-applied}', src)
        self.assertNotIn('usb-boot.conf', src)
        self.assertNotIn('unisoc-usb-net-applied', src)
        self.assertIn('/run/mu300/usb-net-applied', INIT)
        self.assertIn('$1/etc/mu300/usb-net', INIT)

    # --- role switch and host mode (the fork's cases)

    def test_host_auto_disables_usb_network_auto_and_blocks_changes(self):
        for shell in self.each_shell():
            self.reset()
            (self.tmp / 'uci.role_auto').write_text('0')
            self.call_usb(shell, 'set-net', 'ecm', 'permanent', '1')
            self.assertTrue(self.net_file.exists())
            response = self.call_usb(shell, 'set-role', 'host', '1')
            self.assertEqual(json.loads(response.stdout)['ok'], 1, response.stderr)
            self.wait_text(self.role, 'host')
            self.assertEqual((self.tmp / 'uci.net_auto').read_text(), '0')
            self.assertFalse(self.net_file.exists())
            denied = self.call_usb(shell, 'set-net', 'rndis', 'once', '1')
            self.assertEqual(json.loads(denied.stdout)['ok'], 0)
            self.assertFalse(self.net_file.exists())

    def test_f50_host_uses_explicit_role_adapter(self):
        for shell in self.each_shell():
            self.reset()
            result = self.call_usb(shell, 'set-role', 'host', '1', MU300_USB_DEVICE='f50')
            self.assertEqual(json.loads(result.stdout)['ok'], 1)
            self.wait_text(self.role, 'host')
            status = self.call_usb(shell, 'get', MU300_USB_DEVICE='f50')
            self.assertEqual(json.loads(status.stdout)['host_supported'], 1)
            self.role.write_text('device\n')
            # without an adapter command, the F50 writes the raw role switch
            raw = self.script(shell, USB, 'set-role', 'host', '0',
                              MU300_USB_ROLE_FILE=self.role, MU300_USB_DEVICE='f50',
                              MU300_USB_NET_FILE=self.net_file)
            self.assertEqual(json.loads(raw.stdout)['ok'], 1)
            self.wait_text(self.role, 'host')

    def test_v50_writes_the_role_switch_as_the_f50(self):
        # the V50 (FINDINGS 95): the F50's raw role switch, never the U30 Air's charger path (mu300-usb)
        self.stub('mu300-usb', 'echo called > "$STUBLOG/mu300-usb"; exit 1')
        for shell in self.each_shell():
            self.reset()
            self.role.write_text('device\n')
            raw = self.script(shell, USB, 'set-role', 'host', '0',
                              MU300_USB_ROLE_FILE=self.role, MU300_USB_DEVICE='v50',
                              MU300_USB_NET_FILE=self.net_file)
            self.assertEqual(json.loads(raw.stdout)['ok'], 1, raw.stdout)
            self.wait_text(self.role, 'host')
            self.assertFalse((self.tmp / 'mu300-usb').exists())

    def test_async_role_readback_is_pending_not_a_false_failure(self):
        slow = self.tmp / 'slow-role'
        slow.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$STUBLOG/role-request"\n')
        slow.chmod(0o755)
        for shell in self.each_shell():
            self.reset()
            result = self.call_usb(shell, 'set-role', 'host', '1', MU300_USB_ROLE_COMMAND=slow)
            self.assertEqual(json.loads(result.stdout), {'ok': 1, 'pending': 1})
            self.assertEqual((self.tmp / 'uci.role_auto').read_text(), '1')
            self.wait_text(self.tmp / 'role-request', 'host')

    def test_host_only_usb_adapter_list_and_idempotent_bridge_add(self):
        usbdev = self.tmp / 'usb1' / '1-1'
        usbdev.mkdir(parents=True)
        nic = self.net_class / 'eth0'
        nic.mkdir()
        (nic / 'device').symlink_to(usbdev, target_is_directory=True)
        (nic / 'carrier').write_text('1\n')
        other = self.net_class / 'sipa_eth0'
        other.mkdir()
        (other / 'carrier').write_text('1\n')
        gadget = self.net_class / 'rndis0'
        gadget.mkdir()
        gadget_parent = self.tmp / 'gadget.0'
        gadget_parent.mkdir()
        (gadget / 'device').symlink_to(gadget_parent, target_is_directory=True)
        gadget_bus = self.tmp / 'bus' / 'gadget'
        gadget_bus.mkdir(parents=True)
        (gadget_parent / 'subsystem').symlink_to(gadget_bus, target_is_directory=True)
        for shell in self.each_shell():
            (self.tmp / 'ports').unlink(missing_ok=True)
            (self.tmp / 'reloads').unlink(missing_ok=True)
            self.role.write_text('device\n')
            denied = self.call_usb(shell, 'net-add', 'eth0')
            self.assertEqual(json.loads(denied.stdout)['ok'], 0)
            self.role.write_text('host\n')
            listed = json.loads(self.call_usb(shell, 'net-list').stdout)
            self.assertEqual([d['name'] for d in listed['devices']], ['eth0'])
            self.assertEqual(listed['devices'][0]['carrier'], 1)
            added = self.call_usb(shell, 'net-add', 'eth0')
            self.assertEqual(json.loads(added.stdout)['ok'], 1,
                             added.stdout + added.stderr + (self.tmp / 'ip-calls').read_text())
            self.assertEqual(json.loads(self.call_usb(shell, 'net-add', 'eth0').stdout)['ok'], 1)
            self.assertEqual((self.tmp / 'reloads').read_text().splitlines(), ['reload'])
            self.assertEqual(json.loads(self.call_usb(shell, 'net-add', 'sipa_eth0').stdout)['ok'], 0)
            self.assertEqual(json.loads(self.call_usb(shell, 'net-add', 'rndis0').stdout)['ok'], 0)
            # names that are not interface names never reach a path or ip
            for bad in ('../eth0', '-eth0', 'a' * 16):
                self.assertEqual(json.loads(self.call_usb(shell, 'net-add', bad).stdout)['ok'], 0, bad)

    def test_selected_usb_nic_reattaches_without_network_reload(self):
        usbdev = self.tmp / 'usb1' / '1-1'
        usbdev.mkdir(parents=True)
        nic = self.net_class / 'eth0'
        nic.mkdir()
        (nic / 'device').symlink_to(usbdev, target_is_directory=True)
        (self.tmp / 'ports').write_text('usb0 eth0')
        for shell in self.each_shell():
            (self.tmp / 'ip-calls').unlink(missing_ok=True)
            (self.tmp / 'reloads').unlink(missing_ok=True)
            self.role.write_text('host\n')
            self.call_usb(shell, 'net-sync')
            calls = (self.tmp / 'ip-calls').read_text()
            self.assertIn('link set dev eth0 up', calls)
            self.assertIn('link set dev eth0 master br-lan', calls)
            self.assertFalse((self.tmp / 'reloads').exists())

    def test_missing_host_nic_triggers_one_hub_rescan(self):
        hub = self.usb_bus / '1-1'
        hub.mkdir()
        (hub / 'bDeviceClass').write_text('09\n')
        (hub / 'authorized').write_text('1\n')
        for shell in self.each_shell():
            self.role.write_text('host\n')
            self.rescan_marker.rmdir() if self.rescan_marker.exists() else None
            self.call_usb(shell, 'host-rescan')
            self.assertEqual((hub / 'authorized').read_text(), '1\n')
            self.assertTrue(self.rescan_marker.exists())
            (hub / 'authorized').write_text('sentinel\n')
            self.call_usb(shell, 'host-rescan')
            self.assertEqual((hub / 'authorized').read_text(), 'sentinel\n')


class USBIntegration(unittest.TestCase):
    def test_boot_step_runs_from_the_app_init(self):
        ui = (APP / 'etc/init.d/unisoc-modem-ui').read_text()
        self.assertIn('/usr/libexec/unisoc-modem/device-usb boot', ui)

    def test_host_nic_hotplug(self):
        for kind in ('net', 'iface'):
            self.assertIn('device-usb net-sync', (APP / 'etc/hotplug.d' / kind / '90-unisoc-usb-host').read_text())


if __name__ == '__main__':
    unittest.main()
