"""An early host boot must retain later device networking without polling."""
from helpers import ShellTest, TOP

USB = TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb'


class GadgetHandover(ShellTest):
    def setUp(self):
        super().setUp()
        self.role = self.tmp/'role'; self.role.write_text('host\n')
        self.state = self.tmp/'state'
        self.gadget = self.tmp/'gadgets/linux'
        self.gadget.mkdir(parents=True)
        self.bound = self.gadget/'UDC'; self.bound.write_text('')
        self.udcs = self.tmp/'udc'; self.udcs.mkdir()
        (self.udcs/'controller').mkdir()
        self.stub('logger', 'exit 0')
        self.stub('ifup', 'printf "%s:%s\\n" "$ACTION" "$INTERFACE" >> "$STUBLOG/ifup"')
        self.stub('ip', 'printf "%s\\n" "$*" >> "$STUBLOG/ip"')

    def env(self, **extra):
        values = dict(MU300_USB_DEVICE='f50', MU300_USB_ROLE_FILE=self.role,
                      MU300_USB_ROLE_STATE=self.state, MU300_USB_GADGET_DIR=self.gadget.parent,
                      MU300_USB_UDC_CLASS=self.udcs, MU300_USB_ROLE_LOCK=self.tmp/'lock',
                      MU300_USB_GADGET_IFUP_HOOK=self.stubs/'ifup', MU300_USB_IP_BIN=self.stubs/'ip')
        values.update(extra)
        return super().env(**values)

    def test_pending_device_event_binds_once_even_before_role_readback_changes(self):
        for shell in self.each_shell():
            self.bound.write_text('')
            self.state.write_text('request\ndevice\npending\n\n')
            (self.tmp/'ifup').unlink(missing_ok=True)
            result = self.script(shell, USB, 'gadget-bind')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.bound.read_text().strip(), 'controller')
            self.assertEqual(self.script(shell, USB, 'gadget-bind').returncode, 0)
            self.assertEqual((self.tmp/'ifup').read_text().splitlines(), ['ifup:lan'])

    def test_host_failed_device_and_other_models_never_bind(self):
        for shell in self.each_shell():
            for state, model in [('request\nhost\npending\n', 'f50'),
                                 ('request\ndevice\nerror\n', 'f50'),
                                 ('', 'f50'), ('request\ndevice\npending\n', 'u30air')]:
                self.bound.write_text(''); self.state.write_text(state)
                result = self.script(shell, USB, 'gadget-bind', MU300_USB_DEVICE=model)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.bound.read_text(), '')

    def test_missing_or_ambiguous_udc_waits_for_a_real_event(self):
        self.state.write_text('request\ndevice\npending\n')
        for shell in self.each_shell():
            for count in (0, 2):
                for entry in self.udcs.iterdir(): entry.rmdir()
                for index in range(count): (self.udcs/str(index)).mkdir()
                self.bound.write_text('')
                self.assertEqual(self.script(shell, USB, 'gadget-bind').returncode, 0)
                self.assertEqual(self.bound.read_text(), '')

    def test_rndis_retains_the_existing_lan_interface_name(self):
        self.state.write_text('request\ndevice\npending\n')
        rndis = self.gadget/'functions/rndis.rn0'; rndis.mkdir(parents=True)
        (rndis/'ifname').write_text('usb0\n')
        for shell in self.each_shell():
            self.bound.write_text(''); (self.tmp/'ip').unlink(missing_ok=True)
            self.assertEqual(self.script(shell, USB, 'gadget-bind').returncode, 0)
            self.assertEqual((self.tmp/'ip').read_text().strip(), 'link set usb0 name rndis0')
