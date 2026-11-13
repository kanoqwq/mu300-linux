"""USB device-management policy and host-only LAN attachment."""
import json
import time
import unittest
from helpers import ShellTest, TOP


USB = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb'


class USBManagement(ShellTest):
    def env(self, **extra):
        extra.setdefault('MU300_USB_ROLE_STATE', self.tmp / 'role-result')
        extra.setdefault('MU300_USB_GADGET_DIR', self.tmp / 'gadgets')
        extra.setdefault('MU300_USB_NET_LOCK', self.tmp / 'net.lock')
        extra.setdefault('MU300_USB_ROLE_LOCK', self.tmp / 'role.lock')
        extra.setdefault('MU300_USB_LANE_STATE', self.tmp / 'lane-state')
        extra.setdefault('MU300_USB_LANE_PARAM', self.tmp / 'lane-param')
        return super().env(**extra)

    def setUp(self):
        super().setUp()
        self.role = self.tmp / 'role'
        self.role.write_text('device\n')
        self.boot_file = self.tmp / 'usb-boot.conf'
        self.applied = self.tmp / 'applied'
        self.net_class = self.tmp / 'net'
        self.net_class.mkdir()
        self.usb_bus = self.tmp / 'usb-bus'
        self.usb_bus.mkdir()
        self.rescan_marker = self.tmp / 'rescan-done'
        self.stub('uci', '''
while :; do case $1 in -q) shift ;; -t) shift 2 ;; *) break ;; esac; done
cmd=$1; shift
case "$cmd" in
  get)
    case "$1" in
      unisoc_modem.usb) echo device ;;
      unisoc_modem.usb.*) key=${1##*.}; [ -f "$STUBLOG/uci.$key" ] && cat "$STUBLOG/uci.$key" ;;
      unisoc_modem.main.lan_device) echo br-lan ;;
      network.*.ports) [ -f "$STUBLOG/ports" ] && cat "$STUBLOG/ports" ;;
      network.*.type) echo bridge ;;
    esac ;;
  set)
    key=${1%%=*}; val=${1#*=}; printf '%s' "$val" > "$STUBLOG/uci.${key##*.}" ;;
  show) [ "$1" = network ] && { echo "network.@device[0].name='br-lan'"; [ ! -f "$STUBLOG/owners" ] || cat "$STUBLOG/owners"; } ;;
  changes) [ ! -f "$STUBLOG/changes.$1" ] || cat "$STUBLOG/changes.$1" ;;
  add_list) printf ' %s' "${1##*=}" >> "$STUBLOG/ports" ;;
  commit) echo "$1" >> "$STUBLOG/commits" ;;
esac
''')
        helper = self.tmp / 'safe-role'
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$MU300_USB_ROLE_FILE"\n')
        helper.chmod(0o755)
        self.helper = helper
        reload_script = self.tmp / 'network'
        reload_script.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$STUBLOG/reloads"\n')
        reload_script.chmod(0o755)
        self.reload_script = reload_script
        self.stub('ip', 'printf "%s\\n" "$*" >> "$STUBLOG/ip-calls"')
        self.stub('ubus', '[ ! -f "$STUBLOG/ubus-fail" ] || exit 1; echo \'{"interface":[]}\'')
        self.stub('jsonfilter', '''
case "$*" in
  *l3_device*) [ ! -f "$STUBLOG/runtime-devices" ] || cat "$STUBLOG/runtime-devices" ;;
  *) echo '[]' ;;
esac
''')

    def call_usb(self, shell, *args):
        return self.script(shell, USB, *args,
                           MU300_USB_ROLE_FILE=self.role,
                           MU300_USB_BOOT_FILE=self.boot_file,
                           MU300_USB_APPLIED_FILE=self.applied,
                           MU300_USB_NET_CLASS=self.net_class,
                           MU300_USB_BUS=self.usb_bus,
                           MU300_USB_RESCAN_MARKER=self.rescan_marker,
                           MU300_USB_RESCAN_DELAY='0',
                           MU300_USB_ROLE_COMMAND=self.helper,
                           MU300_USB_NETWORK_SERVICE=self.reload_script,
                           MU300_USB_IP_BIN=self.stubs / 'ip')

    def wait_text(self, path, value):
        # Role application is intentionally detached until after the RPC
        # acknowledgement. CI runners can schedule that child later than a
        # lightly loaded developer machine, so wait for the state, not a
        # fixed sub-second deadline.
        for _ in range(200):
            if path.exists() and path.read_text().strip() == value:
                return
            time.sleep(0.02)
        self.assertEqual(path.read_text().strip() if path.exists() else None, value)

    def make_nic(self, name='eth0'):
        parent = self.tmp / 'usb1' / name
        parent.mkdir(parents=True)
        nic = self.net_class / name
        nic.mkdir()
        (nic / 'device').symlink_to(parent, target_is_directory=True)
        (nic / 'type').write_text('1\n')
        (nic / 'address').write_text('02:11:22:33:44:55\n')
        driver = self.tmp / 'drivers' / 'r8152'
        driver.mkdir(parents=True, exist_ok=True)
        (parent / 'driver').symlink_to(driver, target_is_directory=True)
        return nic

    def test_auto_defaults_off_event_idempotence_and_disable_keeps_ports(self):
        self.make_nic()
        for shell in self.each_shell():
            for key in ('ports', 'commits', 'ip-calls', 'uci.lan_auto'):
                (self.tmp / key).unlink(missing_ok=True)
            self.role.write_text('host\n')
            self.assertEqual(json.loads(self.call_usb(shell, 'get').stdout)['lan_auto'], 0)
            self.call_usb(shell, 'net-sync')
            self.assertFalse((self.tmp / 'ports').exists())
            self.assertEqual(json.loads(self.call_usb(shell, 'set-lan-auto', '1').stdout)['ok'], 1)
            # Saving is policy only, scanning never enrolls existing adapters.
            self.call_usb(shell, 'net-list')
            self.assertFalse((self.tmp / 'ports').exists())
            self.call_usb(shell, 'net-sync')
            self.call_usb(shell, 'net-sync')
            self.assertEqual((self.tmp / 'ports').read_text().split(), ['eth0'])
            self.assertEqual((self.tmp / 'commits').read_text().split().count('network'), 1)
            self.assertFalse((self.tmp / 'reloads').exists())
            self.call_usb(shell, 'set-lan-auto', '0')
            (self.tmp / 'ip-calls').unlink()
            self.role.write_text('device\n') # role readback can lag enumeration
            self.call_usb(shell, 'net-sync')
            self.assertIn('master br-lan', (self.tmp / 'ip-calls').read_text())
            self.assertEqual((self.tmp / 'ports').read_text().split(), ['eth0'])

    def test_ownership_protects_static_wan_bridge_vlan_and_dynamic_wan(self):
        nic = self.make_nic()
        self.role.write_text('host\n')
        (self.tmp / 'uci.lan_auto').write_text('1')
        cases = [
            ("network.wan.device='eth0'\n", '', ''),
            ("network.guest.ports='eth1' 'eth0'\n", '', ''),
            ("network.wan.ifname='eth0.10'\n", '', ''),
            ("network.vlan.ports='eth0:t'\n", '', ''),
            ('', 'eth0\n', ''),
            ('', 'eth0.20\n', ''),
            ('', '', 'br-guest'),
        ]
        for shell in self.each_shell():
            for config, runtime, master in cases:
                with self.subTest(config=config, runtime=runtime, master=master):
                    (self.tmp / 'owners').write_text(config)
                    (self.tmp / 'runtime-devices').write_text(runtime)
                    (nic / 'master').unlink(missing_ok=True)
                    if master:
                        target = self.net_class / master
                        target.mkdir(exist_ok=True)
                        (nic / 'master').symlink_to(target, target_is_directory=True)
                    (self.tmp / 'ip-calls').unlink(missing_ok=True)
                    d = json.loads(self.call_usb(shell, 'net-list').stdout)['devices'][0]
                    self.assertEqual(d['eligible'], 0)
                    self.assertTrue(d['owner'])
                    self.call_usb(shell, 'net-sync')
                    self.assertEqual(json.loads(self.call_usb(shell, 'net-add', 'eth0').stdout)['ok'], 0)
                    self.assertFalse((self.tmp / 'ports').exists())
                    self.assertFalse((self.tmp / 'ip-calls').exists())

    def test_metadata_and_wifi_gadget_non_ethernet_exclusion(self):
        self.make_nic()
        wifi = self.make_nic('wlan1'); (wifi / 'wireless').mkdir()
        monitor = self.make_nic('mon0'); (monitor / 'type').write_text('803\n')
        gadget = self.make_nic('usb0')
        gadgetbus = self.tmp / 'bus' / 'gadget'; gadgetbus.mkdir(parents=True)
        (gadget / 'device' / 'subsystem').symlink_to(gadgetbus, target_is_directory=True)
        self.role.write_text('host\n')
        for shell in self.each_shell():
            result = json.loads(self.call_usb(shell, 'net-list').stdout)
            self.assertEqual([d['name'] for d in result['devices']], ['eth0'])
            d = result['devices'][0]
            self.assertEqual((d['driver'], d['mac'], d['eligible'], d['owner'], d['attached']),
                             ('r8152', '02:11:22:33:44:55', 1, '', 0))

    def test_pending_config_and_missing_netifd_never_enroll(self):
        self.make_nic()
        self.role.write_text('host\n')
        (self.tmp / 'uci.lan_auto').write_text('1')
        for shell in self.each_shell():
            (self.tmp / 'changes.network').write_text("network.wan.proto='dhcp'\n")
            self.call_usb(shell, 'net-sync')
            self.assertEqual(json.loads(self.call_usb(shell, 'net-add', 'eth0').stdout)['ok'], 0)
            self.assertFalse((self.tmp / 'ports').exists())
            (self.tmp / 'changes.network').unlink()
            (self.tmp / 'ubus-fail').touch()
            self.call_usb(shell, 'net-sync')
            self.assertEqual(json.loads(self.call_usb(shell, 'net-list').stdout)['ok'], 0)
            self.assertFalse((self.tmp / 'ports').exists())
            (self.tmp / 'ubus-fail').unlink()
            (self.tmp / 'changes.unisoc_modem').write_text('pending')
            self.assertEqual(json.loads(self.call_usb(shell, 'set-lan-auto', '0').stdout)['ok'], 0)
            self.assertEqual((self.tmp / 'uci.lan_auto').read_text(), '1')

    def test_auto_policy_rejects_invalid_or_non_host_enable(self):
        for shell in self.each_shell():
            for value in ('1', '2', '; reboot'):
                self.assertEqual(json.loads(self.call_usb(shell, 'set-lan-auto', value).stdout)['ok'], 0)
            self.assertEqual(json.loads(self.call_usb(shell, 'set-lan-auto', '0').stdout)['ok'], 1)

    def test_multiple_new_adapters_each_saved_once(self):
        self.make_nic('eth0'); self.make_nic('eth1')
        self.role.write_text('host\n')
        (self.tmp / 'uci.lan_auto').write_text('1')
        for shell in self.each_shell():
            (self.tmp / 'ports').unlink(missing_ok=True)
            self.call_usb(shell, 'net-sync'); self.call_usb(shell, 'net-sync')
            self.assertEqual((self.tmp / 'ports').read_text().split(), ['eth0', 'eth1'])

    def test_saved_port_does_not_override_other_bridge(self):
        nic = self.make_nic()
        bridge = self.net_class / 'br-guest'; bridge.mkdir()
        (nic / 'master').symlink_to(bridge, target_is_directory=True)
        (self.tmp / 'ports').write_text('eth0')
        for shell in self.each_shell():
            self.call_usb(shell, 'net-sync')
            self.assertFalse((self.tmp / 'ip-calls').exists())

    def test_host_auto_disables_usb_network_auto_and_blocks_changes(self):
        for shell in self.each_shell():
            self.role.write_text('device\n')
            (self.tmp / 'uci.role_auto').write_text('0')
            self.call_usb(shell, 'set-net', 'ecm', 'permanent', '1')
            self.assertTrue(self.boot_file.exists())
            response = self.call_usb(shell, 'set-role', 'host', '1')
            self.assertEqual(json.loads(response.stdout)['ok'], 1, response.stderr)
            self.wait_text(self.role, 'host')
            self.assertEqual((self.tmp / 'uci.net_auto').read_text(), '0')
            self.assertFalse(self.boot_file.exists())
            denied = self.call_usb(shell, 'set-net', 'rndis', 'once', '1')
            self.assertEqual(json.loads(denied.stdout)['ok'], 0)

    def test_once_consumed_only_after_boot_marker(self):
        for shell in self.each_shell():
            self.role.write_text('device\n')
            (self.tmp / 'uci.role_auto').write_text('0')
            self.applied.unlink(missing_ok=True)
            self.call_usb(shell, 'set-net', 'rndis', 'once', '1')
            self.assertIn('mode=rndis', self.boot_file.read_text())
            self.call_usb(shell, 'boot')
            self.assertTrue(self.boot_file.exists())
            self.applied.touch()
            self.call_usb(shell, 'boot')
            self.assertFalse(self.boot_file.exists())
            self.assertEqual((self.tmp / 'uci.net_auto').read_text(), '0')

    def test_f50_host_uses_explicit_role_adapter(self):
        for shell in self.each_shell():
            self.role.write_text('device\n')
            result = self.script(shell, USB, 'set-role', 'host', '1',
                                 MU300_USB_ROLE_FILE=self.role,
                                 MU300_USB_DEVICE='f50',
                                 MU300_USB_ROLE_COMMAND=self.helper,
                                 MU300_USB_BOOT_FILE=self.boot_file,
                                 MU300_USB_NET_CLASS=self.net_class,
                                 MU300_USB_BUS=self.usb_bus,
                                 MU300_USB_RESCAN_MARKER=self.rescan_marker,
                                 MU300_USB_RESCAN_DELAY='0')
            self.assertEqual(json.loads(result.stdout)['ok'], 1)
            self.wait_text(self.role, 'host')
            status = self.script(shell, USB, 'get', MU300_USB_ROLE_FILE=self.role,
                                 MU300_USB_DEVICE='f50', MU300_USB_BOOT_FILE=self.boot_file)
            self.assertEqual(json.loads(status.stdout)['host_supported'], 1)
            self.role.write_text('device\n')
            raw = self.script(shell, USB, 'set-role', 'host', '0',
                              MU300_USB_ROLE_FILE=self.role,
                              MU300_USB_DEVICE='f50', MU300_USB_BOOT_FILE=self.boot_file)
            self.assertEqual(json.loads(raw.stdout)['ok'], 1)
            self.wait_text(self.role, 'host')

    def test_async_role_readback_is_pending_not_a_false_failure(self):
        slow = self.tmp / 'slow-role'
        slow.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$STUBLOG/role-request"\n')
        slow.chmod(0o755)
        for shell in self.each_shell():
            self.role.write_text('device\n')
            result = self.script(shell, USB, 'set-role', 'host', '1',
                                 MU300_USB_ROLE_FILE=self.role,
                                 MU300_USB_ROLE_COMMAND=slow,
                                 MU300_USB_BOOT_FILE=self.boot_file,
                                 MU300_USB_NET_CLASS=self.net_class,
                                 MU300_USB_BUS=self.usb_bus,
                                 MU300_USB_RESCAN_MARKER=self.rescan_marker,
                                 MU300_USB_RESCAN_DELAY='0')
            self.assertEqual(json.loads(result.stdout), {'ok': 1, 'pending': 1})
            self.assertEqual((self.tmp / 'uci.role_auto').read_text(), '1')
            self.wait_text(self.tmp / 'role-request', 'host')

    def test_host_only_usb_adapter_list_and_idempotent_bridge_add(self):
        usbdev = self.tmp / 'usb1' / '1-1'
        usbdev.mkdir(parents=True)
        nic = self.net_class / 'eth0'
        nic.mkdir()
        (nic / 'type').write_text('1\n')
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

    def test_selected_usb_nic_reattaches_without_network_reload(self):
        usbdev = self.tmp / 'usb1' / '1-1'
        usbdev.mkdir(parents=True)
        nic = self.net_class / 'eth0'
        nic.mkdir()
        (nic / 'type').write_text('1\n')
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

    def test_actual_bound_protocol_is_not_saved_selection(self):
        gadget=self.tmp/'gadgets'/'linux'
        config=gadget/'configs'/'c.1';config.mkdir(parents=True)
        funcs=gadget/'functions';funcs.mkdir()
        for name in ('ncm.usb0','rndis.rn0','acm.GS0'):(funcs/name).mkdir()
        (config/'f1').symlink_to(funcs/'ncm.usb0',target_is_directory=True)
        (config/'f2').symlink_to(funcs/'acm.GS0',target_is_directory=True)
        (gadget/'UDC').write_text('test-controller\n')
        for shell in self.each_shell():
            self.call_usb(shell,'set-net','rndis','once','0')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['net_current'],state['net_mode'],state['net_next']),('ncm','rndis','ncm'))
            self.assertEqual(state['net_auto'],0)
            self.call_usb(shell,'set-net','rndis','once','1')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['net_current'],state['net_next'],state['net_policy_error']),('ncm','rndis',0))
        (gadget/'UDC').write_text('')
        self.assertEqual(json.loads(self.call_usb(self.shells[0],'get').stdout)['net_current'],'unknown')

    def test_boot_policy_mismatch_is_visible_and_get_never_changes_files(self):
        for shell in self.each_shell():
            self.call_usb(shell,'set-net','ecm','permanent','1')
            self.boot_file.write_text('mode=rndis\nscope=once\n')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['net_mode'],state['net_next'],state['net_policy_error']),('ecm','rndis',1))
            self.assertEqual(self.boot_file.read_text(),'mode=rndis\nscope=once\n')
            self.boot_file.unlink()
            self.assertEqual(json.loads(self.call_usb(shell,'get').stdout)['net_policy_error'],1)

    def test_once_consumed_shows_current_rndis_but_next_ncm(self):
        gadget=self.tmp/'gadgets'/'linux'
        config=gadget/'configs'/'c.1';config.mkdir(parents=True)
        func=gadget/'functions'/'rndis.rn0';func.mkdir(parents=True)
        (config/'f1').symlink_to(func,target_is_directory=True)
        (gadget/'UDC').write_text('controller\n')
        for shell in self.each_shell():
            self.call_usb(shell,'set-net','rndis','once','1')
            self.applied.write_text('rndis\n')
            self.call_usb(shell,'boot')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['net_current'],state['net_next'],state['net_auto']),('rndis','ncm',0))
            self.role.write_text('host\n')
            self.assertEqual(json.loads(self.call_usb(shell,'get').stdout)['net_current'],'none')
            self.role.write_text('device\n')

    def test_detached_role_failure_and_stale_request_are_reported(self):
        self.helper.write_text('#!/bin/sh\nexit 1\n')
        for shell in self.each_shell():
            result=json.loads(self.call_usb(shell,'set-role','host','0').stdout)
            self.assertEqual(result,{'ok':1,'pending':1})
            for _ in range(100):
                state=json.loads(self.call_usb(shell,'get').stdout)
                if state['role_error']:break
                time.sleep(.02)
            self.assertEqual((state['role'],state['role_target'],state['role_error'],state['role_pending']),('device','host',1,0))
            resultfile=self.tmp/'role-result'; before=resultfile.read_text()
            self.call_usb(shell,'apply-role','device','outdated-request')
            self.assertEqual(resultfile.read_text(),before)
            self.call_usb(shell,'set-role','device','0')
            self.assertEqual(json.loads(self.call_usb(shell,'get').stdout)['role_error'],0)

    def test_native_override_waits_for_data_readback_and_cancellation_replaces_policy(self):
        root=self.tmp/'native-root';port=root/'sys/class/typec/port0';port.mkdir(parents=True)
        (port/'data_role').write_text('host [device]\n')
        (port/'port_type').write_text('[dual] source sink\n')
        (port/'power_operation_mode').write_text('default\n')
        control=root/'sys/bus/platform/drivers/ump9620-tcpm/controller/usb_role_override'
        control.parent.mkdir(parents=True);control.write_text('auto\n')
        self.stub('mu300-device','echo u30air');self.stub('id','echo 0')
        self.helper.write_text('#!/bin/sh\nexec "'+str(TOP/'rootfs/overlay/opt/mu300/bin/mu300-usb')+'" "$@"\n')
        for shell in self.each_shell():
            self.role.write_text('device\n')
            result=self.script(shell,USB,'set-role','host','0',MU300_SYSROOT=root,
                MU300_USB_ROLE_COMMAND=self.helper,MU300_USB_ROLE_FILE=self.role,
                MU300_USB_BOOT_FILE=self.boot_file,MU300_USB_NET_CLASS=self.net_class,
                MU300_USB_BUS=self.usb_bus,MU300_USB_DEVICE='u30air')
            self.assertEqual(json.loads(result.stdout),{'ok':1,'pending':1})
            for _ in range(100):
                if control.read_text()=='host\n':break
                time.sleep(.02)
            self.assertEqual(control.read_text(),'host\n')
            self.assertEqual((port/'port_type').read_text(),'dual\n')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['role_pending'],state['role_error']),(1,0))
            # DWC3 can still report device while its role work is pending.
            # Cancelling must replace the kernel policy, not just read data.
            result=json.loads(self.script(shell,USB,'set-role','device','0',MU300_SYSROOT=root,
                MU300_USB_ROLE_COMMAND=self.helper,MU300_USB_ROLE_FILE=self.role,
                MU300_USB_BOOT_FILE=self.boot_file,MU300_USB_NET_CLASS=self.net_class,
                MU300_USB_BUS=self.usb_bus,MU300_USB_DEVICE='u30air').stdout)
            self.assertEqual(result,{'ok':1,'pending':1})
            for _ in range(100):
                if control.read_text()=='device\n':break
                time.sleep(.02)
            self.assertEqual(control.read_text(),'device\n')
            self.assertEqual(json.loads(self.call_usb(shell,'get').stdout)['role_error'],0)
            self.script(shell,USB,'set-role','host','0',MU300_SYSROOT=root,
                MU300_USB_ROLE_COMMAND=self.helper,MU300_USB_ROLE_FILE=self.role,
                MU300_USB_BOOT_FILE=self.boot_file,MU300_USB_NET_CLASS=self.net_class,
                MU300_USB_BUS=self.usb_bus,MU300_USB_DEVICE='u30air')
            for _ in range(100):
                if control.read_text()=='host\n':break
                time.sleep(.02)
            self.assertEqual(control.read_text(),'host\n')
            # The kernel's attach event, not a user-space timer, completes it.
            self.role.write_text('host\n')
            state=json.loads(self.call_usb(shell,'get').stdout)
            self.assertEqual((state['role'],state['role_pending'],state['role_error']),('host',0,0))


class USBIntegration(unittest.TestCase):
    def test_tf_early_policy_precedes_gadget_enumeration(self):
        init = (TOP / 'boot/init').read_text()
        # Independent GPT roots now mount before policy lookup, too. Desktop
        # recovery can already own the gadget; normal boot must still read the
        # mounted root's USB policy before its conditional first enumeration.
        policy = init.index('[ "$root_mounted" = 1 ] && read_usb_boot_policy\n')
        enumeration = init.index('[ "${DESKTOP_USB_READY:-}" = 1 ] || setup_usb_gadget\n')
        self.assertLess(init.index('stage=sd-scan dev='), policy)
        self.assertLess(init.index('stage=sd-root dev='), policy)
        self.assertLess(init.index('stage=root-part dev='), policy)
        self.assertLess(policy, enumeration)
        self.assertLess(enumeration,
                        init.index('if [ "$root_mounted" = 0 ] && [ "$internal_root" = 1 ] && root_region_valid'))
        self.assertIn('/disk/openwrt/etc/unisoc-modem/usb-boot.conf', init)
        self.assertIn('/run/unisoc-usb-net-applied', init)

    def test_rndis_is_single_configuration_with_own_revision_and_early_dhcp(self):
        init = (TOP / 'boot/init').read_text()
        self.assertIn('[ -z "$netfunc$rndis" ] && mkdir -p "$g/functions/$f.usb0"', init)
        self.assertNotIn('cdc=c.2', init)
        self.assertIn('echo 0x0302 > "$g/bcdDevice"', init)
        self.assertIn('early_net=rndis0', init)
        self.assertIn('interface $early_net', init)
        reset = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-usb-reset').read_text()
        self.assertIn('for nic in usb0 rndis0', reset)
        self.assertIn('ACTION=ifup INTERFACE=lan /etc/hotplug.d/iface/10-mu300-usb', reset)
        self.assertIn('/run/mu300-usb-rebind-done', reset)

    def test_rndis_enters_lan_on_firstboot_and_reload(self):
        first = (TOP / 'openwrt/overlay/etc/uci-defaults/90-mu300').read_text()
        hotplug = (TOP / 'openwrt/overlay/etc/hotplug.d/iface/10-mu300-usb').read_text()
        self.assertIn("uci add_list network.$dev.ports='rndis0'", first)
        self.assertIn('ip link set rndis0 master br-lan', hotplug)

    def test_host_boot_cannot_be_undone_by_gadget_rebind(self):
        ui = (TOP / 'openwrt/luci-app-mu300/root/etc/init.d/unisoc-modem-ui').read_text()
        post = (TOP / 'openwrt/overlay/etc/init.d/mu300-post').read_text()
        reset = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-usb-reset').read_text()
        self.assertIn('/usr/libexec/unisoc-modem/device-usb boot', ui)
        self.assertIn('!= host', post)
        self.assertIn('= host ] && exit 0', reset)

    def test_host_nic_hotplug_and_build_inventory(self):
        pkg = TOP / 'openwrt/luci-app-mu300/root/etc/hotplug.d'
        net = (pkg / 'net/90-unisoc-usb-host').read_text()
        iface = (pkg / 'iface/90-unisoc-usb-host').read_text()
        self.assertIn('device-usb net-sync', net)
        self.assertIn('device-usb net-sync', iface)
        for script in ('openwrt/build-rootfs.sh', 'tools/build-openwrt-tf-magisk.sh', 'tools/make-release.sh'):
            content = (TOP / script).read_text()
            self.assertIn('90-unisoc-usb-host', content)


if __name__ == '__main__':
    import unittest
    unittest.main()
