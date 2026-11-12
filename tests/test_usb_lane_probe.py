"""Exercise the real hotplug recovery and cached USB BOS parser."""
import os
from pathlib import Path
import subprocess
import time
import unittest
from helpers import ShellTest, TOP, ucode

USB = TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb'
BOS = USB.parent/'usb-superspeed-capable'
SS_BOS = bytes.fromhex('05 0f 16 00 02 07 10 02 02 00 00 00 0a 10 03 02 0e 00 02 0a ff 07')


class BOSParser(ShellTest):
    def test_real_ucode_accepts_ss_and_rejects_incomplete_or_usb2_caps(self):
        interpreter = ucode()
        if not interpreter:
            self.skipTest('native ucode or MU300_TEST_UCODE required')
        cases = [(SS_BOS, True), (b'', False), (SS_BOS[:-1], False),
                 (SS_BOS[:4]+b'\x03'+SS_BOS[5:], False),
                 (SS_BOS[:16]+b'\x06'+SS_BOS[17:], False),
                 (bytes.fromhex('05 0f 0c 00 01 07 10 02 02 00 00 00'), False),
                 (SS_BOS[:12]+b'\x00'+SS_BOS[13:], False)]
        for index, (data, expected) in enumerate(cases):
            path = self.tmp/f'bos-{index}'
            path.write_bytes(data)
            result = subprocess.run(interpreter+[str(BOS), str(path)], capture_output=True)
            self.assertEqual(result.returncode == 0, expected, result.stderr)


class LaneProbe(ShellTest):
    def setUp(self):
        super().setUp()
        if not ucode():
            self.skipTest('native ucode or MU300_TEST_UCODE required')
        self.role = self.tmp/'role'
        self.param = self.tmp/'lane-param'
        self.state = self.tmp/'lane-state'
        self.request = self.tmp/'role-result'
        self.net = self.tmp/'net'
        self.bus = self.tmp/'bus/usb/devices'
        self.controller = self.tmp/'controller'
        self.block = self.tmp/'block'
        self.block.mkdir()
        self.net.mkdir()
        self.bus.mkdir(parents=True)
        self.controller.mkdir()
        self.make_usb('usb1', '480', '09', '1d6b', '0002')
        self.make_usb('usb2', '10000', '09', '1d6b', '0003')
        self.low = self.make_usb('1-1', '480')
        self.high = self.make_usb('2-1', '5000', publish=False)
        self.interface = self.low/'1-1:1.0'
        self.interface.mkdir()
        (self.interface/'subsystem').symlink_to(self.bus.parent)
        (self.net/'eth0').mkdir()
        (self.net/'eth0/type').write_text('1\n')
        (self.net/'eth0/device').symlink_to(self.interface)
        (self.low/'bos_descriptors').write_bytes(SS_BOS)
        self.role.write_text('host\n')
        self.param.write_text('-1\n')
        self.request.write_text('initial\nhost\ndone\n\n')
        self.stub('uci', '''
while :; do case $1 in -q) shift ;; -t) shift 2 ;; *) break ;; esac; done
case "$1:$2" in
get:unisoc_modem.usb.role_command) [ ! -e "$STUBLOG/custom" ] || cat "$STUBLOG/custom" ;;
get:*) exit 1 ;;
*) echo "$*" >> "$STUBLOG/uci-writes" ;;
esac
''')
        self.stub('ucode', 'exec $MU300_TEST_UCODE "$@"')
        self.stub('logger', 'printf "%s\\n" "$*" >> "$STUBLOG/logger"')
        self.stub('sleep', '''
choice=$(cat "$MU300_USB_LANE_PARAM")
printf '%s\n' "$choice" >> "$STUBLOG/steps"
if [ -e "$STUBLOG/barrier" ]; then
    touch "$STUBLOG/entered"
    for n in $(seq 1 300); do [ ! -e "$STUBLOG/barrier" ] && break; /bin/sleep .01; done
fi
if [ -e "$STUBLOG/cancel" ]; then
    printf 'new-request\ndevice\npending\n\n' > "$MU300_USB_ROLE_STATE"
fi
if [ "$choice" = "$(cat "$STUBLOG/success" 2>/dev/null)" ]; then
    rm -f "$MU300_USB_BUS/1-1"
    ln -sf "$STUBLOG/controller/usb2/2-1" "$MU300_USB_BUS/2-1"
else
    rm -f "$MU300_USB_BUS/2-1"
    ln -sf "$STUBLOG/controller/usb1/1-1" "$MU300_USB_BUS/1-1"
fi
''')

    def make_usb(self, name, speed, cls='00', vid='0bda', pid='8153', publish=True):
        root = 'usb2' if name.startswith('2-') else 'usb1'
        path = self.controller/(name if name.startswith('usb') else root+'/'+name)
        path.mkdir(parents=True, exist_ok=True)
        for key, value in dict(speed=speed, bDeviceClass=cls, idVendor=vid,
                               idProduct=pid, devpath='1' if '-' in name else '0').items():
            (path/key).write_text(value+'\n')
        if publish:
            (self.bus/name).symlink_to(path)
        return path

    def env(self, **extra):
        values = dict(MU300_USB_DEVICE='f50', MU300_USB_ROLE_FILE=self.role,
                      MU300_USB_ROLE_LOCK=self.tmp/'role.lock',
                      MU300_USB_ROLE_STATE=self.request,
                      MU300_USB_GADGET_DIR=self.tmp/'gadgets',
                      MU300_USB_UDC_CLASS=self.tmp/'udc',
                      MU300_USB_LANE_PARAM=self.param, MU300_USB_LANE_STATE=self.state,
                      MU300_USB_LANE_BOS_TOOL=BOS, MU300_USB_LANE_SETTLE='0',
                      MU300_USB_LANE_WAIT=self.stubs/'sleep',
                      MU300_USB_CONTROLLER_ROOT=self.controller,
                      MU300_USB_BLOCK_CLASS=self.block,
                      MU300_USB_NET_CLASS=self.net, MU300_USB_BUS=self.bus)
        values.update(extra)
        return super().env(**values)

    def probe(self, shell, **extra):
        return self.script(shell, USB, 'probe-lanes', 'eth0', **extra)

    def test_first_direction_is_learned_once_without_config_writes(self):
        for shell in self.each_shell():
            self.param.write_text('-1\n'); self.state.unlink(missing_ok=True)
            (self.tmp/'steps').unlink(missing_ok=True)
            (self.tmp/'success').write_text('0')
            (self.bus/'2-1').unlink(missing_ok=True)
            if not (self.bus/'1-1').exists(): (self.bus/'1-1').symlink_to(self.low)
            self.assertEqual(self.probe(shell).returncode, 0)
            self.assertEqual(self.param.read_text().strip(), '0')
            self.assertEqual(self.state.read_text().strip(), '0')
            self.assertEqual(self.probe(shell).returncode, 0)
            self.assertEqual((self.tmp/'steps').read_text().split(), ['0'])
            self.assertFalse((self.tmp/'uci-writes').exists())

    def test_second_direction_and_failed_probe_are_bounded(self):
        for shell in self.each_shell():
            for success, final in [('1', '1'), ('none', '-1')]:
                self.param.write_text('-1\n'); self.state.unlink(missing_ok=True)
                (self.tmp/'steps').unlink(missing_ok=True)
                (self.tmp/'success').write_text(success)
                (self.bus/'2-1').unlink(missing_ok=True)
                if not (self.bus/'1-1').exists(): (self.bus/'1-1').symlink_to(self.low)
                self.assertEqual(self.probe(shell).returncode, 0)
                self.assertEqual(self.param.read_text().strip(), final)
                self.assertEqual((self.tmp/'steps').read_text().split(), ['0', '1'])
                self.assertEqual(self.probe(shell).returncode, 0)
                self.assertEqual((self.tmp/'steps').read_text().split(), ['0', '1'])

    def test_device_mode_u30air_manual_override_and_storage_are_untouched(self):
        for shell in self.each_shell():
            for mode in ('device', 'u30air', 'manual', 'storage', 'custom', 'usb2'):
                self.param.write_text('1\n' if mode == 'manual' else '-1\n')
                self.role.write_text('device\n' if mode == 'device' else 'host\n')
                (self.low/'bos_descriptors').write_bytes(SS_BOS if mode != 'usb2' else b'')
                (self.tmp/'steps').unlink(missing_ok=True)
                if mode == 'storage': self.make_usb('1-2', '480', '08', '1234', '5678')
                if mode == 'custom': (self.tmp/'custom').write_text('/custom-role')
                self.assertEqual(self.probe(shell, MU300_USB_DEVICE='u30air' if mode == 'u30air' else 'f50').returncode, 0)
                self.assertFalse((self.tmp/'steps').exists(), mode)
                self.assertFalse(self.state.exists(), mode)
                (self.bus/'1-2').unlink(missing_ok=True)
                (self.tmp/'custom').unlink(missing_ok=True)

    def test_newer_device_request_cancels_probe_and_releases_role_lock(self):
        for shell in self.each_shell():
            self.param.write_text('-1\n'); self.state.unlink(missing_ok=True)
            self.request.write_text('initial\nhost\ndone\n\n')
            self.role.write_text('host\n')
            (self.tmp/'steps').unlink(missing_ok=True)
            (self.tmp/'entered').unlink(missing_ok=True)
            (self.tmp/'barrier').touch()
            probe = subprocess.Popen(shell+[str(USB), 'probe-lanes', 'eth0'], env=self.env(),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(300):
                if (self.tmp/'entered').exists(): break
                time.sleep(.01)
            self.assertTrue((self.tmp/'entered').exists())
            self.request.write_text('new-request\ndevice\npending\n\n')
            request = subprocess.Popen(shell+[str(USB), 'apply-role', 'device', 'new-request'],
                                       env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (self.tmp/'barrier').unlink()
            probe.communicate(timeout=15); request.communicate(timeout=15)
            self.assertEqual(probe.returncode, 0)
            self.assertEqual(request.returncode, 0)
            self.assertEqual(self.role.read_text().strip(), 'device')
            self.assertEqual(self.param.read_text().strip(), '-1')
            self.assertFalse(self.state.exists())

    def test_empty_dock_reader_allowed_but_media_or_unknown_interfaces_block(self):
        reader = self.make_usb('1-2', '480', '00', '1234', '5678')
        (reader/'devpath').write_text('2\n')
        iface = reader/'1-2:1.0'
        iface.mkdir()
        (iface/'bInterfaceClass').write_text('08\n')
        driver = self.bus.parent/'drivers/usb-storage'
        driver.mkdir(parents=True)
        (iface/'driver').symlink_to(driver)
        disk = self.block/'sda'
        disk.mkdir()
        (disk/'device').symlink_to(iface)
        (disk/'removable').write_text('1\n')
        (disk/'holders').mkdir()
        for shell in self.each_shell():
            for mode in ('empty', 'media', 'unknown', 'composite', 'held'):
                with self.subTest(shell=shell, mode=mode):
                    self.param.write_text('-1\n'); self.state.unlink(missing_ok=True)
                    (self.tmp/'steps').unlink(missing_ok=True)
                    (self.tmp/'success').write_text('0')
                    (self.bus/'2-1').unlink(missing_ok=True)
                    if not (self.bus/'1-1').exists(): (self.bus/'1-1').symlink_to(self.low)
                    (disk/'size').write_text('2048\n' if mode == 'media' else '0\n')
                    if mode == 'unknown': (disk/'size').unlink()
                    extra = reader/'1-2:1.1'
                    if mode == 'composite':
                        extra.mkdir(); (extra/'bInterfaceClass').write_text('01\n')
                    if mode == 'held': (disk/'holders/dm-test').touch()
                    self.assertEqual(self.probe(shell).returncode, 0)
                    self.assertEqual((self.tmp/'steps').exists(), mode == 'empty')
                    self.assertEqual(self.param.read_text().strip(), '0' if mode == 'empty' else '-1')
                    (disk/'holders/dm-test').unlink(missing_ok=True)
                    if extra.exists():
                        (extra/'bInterfaceClass').unlink(); extra.rmdir()


if __name__ == '__main__':
    unittest.main()
