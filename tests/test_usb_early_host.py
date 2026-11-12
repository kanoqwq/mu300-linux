"""Exercise initramfs's actual saved-role handling before gadget binding."""
from helpers import ShellTest, TOP


class EarlyHost(ShellTest):
    def setUp(self):
        super().setUp()
        text = (TOP/'boot/init').read_text()
        self.block = text.split('# BEGIN MU300 EARLY USB ROLE\n', 1)[1].split('# END MU300 EARLY USB ROLE', 1)[0]
        self.root = self.tmp/'root'
        (self.root/'sbin').mkdir(parents=True)
        (self.root/'sbin/uci').touch(); (self.root/'sbin/uci').chmod(0o755)
        self.role = self.tmp/'role'; self.role.write_text('device\n')
        self.stub('chroot', '''
case "$*" in
*unisoc_modem.usb.role_auto) cat "$STUBLOG/auto" ;;
*unisoc_modem.usb.role_command) cat "$STUBLOG/adapter" ;;
*) exit 1 ;;
esac
''')

    def test_only_explicit_supported_host_policy_requests_host_before_binding(self):
        for shell in self.each_shell():
            for model, auto, adapter, mode, ready, expected in [
                ('f50','1','','part','',True), ('v50','1','','sd','',True),
                ('f50','0','','part','',False), ('f50','','','part','',False),
                ('u30air','1','','part','',False), ('f50','1','/custom','part','',False),
                ('f50','1','','install-part','',False), ('f50','1','','part','1',False)]:
                (self.tmp/'auto').write_text(auto); (self.tmp/'adapter').write_text(adapter)
                self.role.write_text('device\n')
                code = self.block + '''
pick_root() { echo "$TESTROOT"; }
log() { echo "$*"; }
read_usb_boot_role
printf 'early=%s\n' "${MU300_USB_EARLY_HOST:-0}"
'''
                result = self.sh(shell, code, DEVICE=model, DESKTOP_MODE=mode,
                                 DESKTOP_USB_READY=ready, TESTROOT=self.root,
                                 MU300_USB_CHROOT=self.stubs/'chroot',
                                 MU300_USB_ROLE_FILE=self.role)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('early='+('1' if expected else '0'), result.stdout)
                self.assertEqual(self.role.read_text().strip(), 'host' if expected else 'device')

    def test_failed_role_request_keeps_rescue_without_saving_anything(self):
        (self.tmp/'auto').write_text('1'); (self.tmp/'adapter').write_text('')
        for shell in self.each_shell():
            code = self.block + '''
pick_root() { echo "$TESTROOT"; }
log() { echo "$*"; }
read_usb_boot_role
test "${MU300_USB_EARLY_HOST:-0}" = 0
'''
            result = self.sh(shell, code, DEVICE='f50', TESTROOT=self.root,
                             MU300_USB_CHROOT=self.stubs/'chroot',
                             MU300_USB_ROLE_FILE=self.tmp/'missing/role')
            self.assertEqual(result.returncode, 0)
            self.assertIn('stage=usb-early-host-failed', result.stdout)
