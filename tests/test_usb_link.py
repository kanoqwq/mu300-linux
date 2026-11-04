"""Read-only negotiated C-port speed, using actual target ucode and fake sysfs."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP, ucode

HELPER = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/usb-link.uc'


class UsbLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uc = ucode()
        if not cls.uc:
            raise unittest.SkipTest('target ucode required')

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='usb-link-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.udcs, self.roles, self.bus, self.gadgets = [self.root / x for x in ('udc', 'roles', 'bus', 'gadgets')]
        for p in (self.udcs, self.roles, self.bus, self.gadgets):
            p.mkdir()
        self.controller = self.root / 'devices/platform/typec/musb'
        self.udc = self.controller / 'udc/musb.0'
        self.udc.mkdir(parents=True)
        (self.udcs / 'musb.0').symlink_to(self.udc)
        self.write(self.udc / 'current_speed', 'high-speed')
        self.write(self.udc / 'maximum_speed', 'super-speed')
        self.write(self.udc / 'state', 'configured')
        self.write(self.gadgets / 'g/UDC', 'musb.0')
        self.role = self.root / 'missing-role'

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + '\n')

    def read(self, role='device'):
        r = subprocess.run(self.uc + [str(HELPER), role, str(self.role), str(self.gadgets),
                                      str(self.bus), str(self.udcs), str(self.roles)],
                           text=True, capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def root_bus(self, n, controller=None):
        path = (controller or self.controller) / ('usb' + str(n))
        path.mkdir(parents=True, exist_ok=True)
        (self.bus / path.name).symlink_to(path)
        self.write(path / 'speed', '20000')  # capability must never appear
        return path

    def peer(self, root, name, speed):
        path = root / name
        path.mkdir()
        (self.bus / name).symlink_to(path)
        self.write(path / 'speed', speed)
        return path

    def test_device_reads_negotiated_not_maximum_and_preserves_files(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.read(), {'state': 'connected', 'links': [{'mbps': 480, 'speed': 'high-speed'}]})
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_device_speed_states_and_superspeed_plus_not_guessed(self):
        for speed, rate in (('low-speed', 1.5), ('full-speed', 12), ('super-speed', 5000), ('super-speed-plus', None)):
            self.write(self.udc / 'current_speed', speed)
            self.assertEqual(self.read()['links'], [{'mbps': rate, 'speed': speed}])
        self.write(self.udc / 'state', 'not-attached')
        self.assertEqual(self.read(), {'state': 'disconnected', 'links': []})
        self.write(self.udc / 'state', 'suspended')
        self.assertEqual(self.read()['state'], 'connected')
        self.write(self.udc / 'current_speed', 'UNKNOWN')
        self.assertEqual(self.read()['state'], 'unavailable')

    def test_unavailable_missing_or_ambiguous_udc_and_invalid_role(self):
        self.assertEqual(self.read('unavailable')['state'], 'unavailable')
        (self.gadgets / 'g/UDC').unlink()
        self.assertEqual(self.read()['state'], 'connected')  # single unbound UDC
        (self.udcs / 'second').mkdir()
        self.assertEqual(self.read()['state'], 'unavailable')
        self.write(self.gadgets / 'g/UDC', 'musb.0')
        self.write(self.gadgets / 'g2/UDC', 'second')
        self.assertEqual(self.read()['state'], 'unavailable')

    def test_host_direct_peer_excludes_root_capability_interfaces_and_downstream(self):
        root = self.root_bus(1)
        hub = self.peer(root, '1-1', '480')
        child = hub / '1-1.2'; child.mkdir()
        (self.bus / '1-1.2').symlink_to(child)
        self.write(child / 'speed', '12')
        iface = hub / '1-1:1.0'; iface.mkdir()
        (self.bus / iface.name).symlink_to(iface)
        self.write(iface / 'speed', '5000')
        unrelated = self.root_bus(9, self.root / 'unrelated')
        self.peer(unrelated, '9-1', '10000')
        self.assertEqual(self.read('host'), {'state': 'connected', 'links': [{'mbps': 480}]})

    def test_host_combined_usb2_usb3_hub_reports_both_and_deduplicates(self):
        usb2 = self.root_bus(1); usb3 = self.root_bus(2)
        self.peer(usb2, '1-1', '480'); self.peer(usb2, '1-2', '480')
        self.peer(usb3, '2-1', '5000')
        self.assertEqual(self.read('host'), {'state': 'connected', 'links': [{'mbps': 480}, {'mbps': 5000}]})

    def test_host_no_peer_unknown_speed_and_missing_controller(self):
        self.assertEqual(self.read('host')['state'], 'unavailable')
        root = self.root_bus(1)
        self.assertEqual(self.read('host')['state'], 'disconnected')
        self.peer(root, '1-1', 'unknown')
        self.assertEqual(self.read('host')['state'], 'unavailable')
        self.write(root / '1-1/speed', '0')
        self.assertEqual(self.read('host')['state'], 'unavailable')

    def test_host_role_class_mapping_when_udc_disappears(self):
        (self.udcs / 'musb.0').unlink()
        (self.gadgets / 'g/UDC').unlink()
        role = self.controller.parent / 'usb_role/c-port'
        role.mkdir(parents=True)
        self.write(role / 'role', 'host')
        (self.roles / 'c-port').symlink_to(role)
        root = self.root_bus(1)
        self.peer(root, '1-1', '480')
        self.assertEqual(self.read('host')['links'], [{'mbps': 480}])
        # Explicit role file also works with more than one role class.
        (self.roles / 'other-port').mkdir()
        self.assertEqual(self.read('host')['state'], 'unavailable')
        self.role = self.roles / 'c-port/role'
        self.assertEqual(self.read('host')['links'], [{'mbps': 480}])

    def test_host_platform_role_policy_identifies_the_same_c_port(self):
        (self.udcs / 'musb.0').unlink()
        (self.gadgets / 'g/UDC').unlink()
        self.role = self.controller / 'usb_role_policy'
        self.write(self.role, 'host-sink')
        root = self.root_bus(1)
        self.peer(root, '1-1', '480')
        self.assertEqual(self.read('host')['links'], [{'mbps': 480}])


if __name__ == '__main__':
    unittest.main()
