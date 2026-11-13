"""Battery-board force/auto API, boot policy and model exclusion."""
import json,time
from helpers import ShellTest, TOP, BIN

USB=TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb'

class ForcePolicy(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root';self.role=self.tmp/'role';self.role.write_text('host\n')
        self.driver=self.root/'sys/bus/platform/drivers/ump9620-tcpm'
        self.control=self.driver/'pd/usb_role_override';self.control.parent.mkdir(parents=True)
        self.control.write_text('auto\n');(self.control.parent/'usb_role_negotiated').write_text('host\n')
        port=self.root/'sys/class/typec/port0';port.mkdir(parents=True)
        for name,value in [('data_role','[host] device'),('power_role','source [sink]'),('port_type','dual')]:
            (port/name).write_text(value+'\n')
        self.stub('mu300-device','echo u30air');self.stub('id','echo 0')
        self.stub('mu300-usb','exec "'+str(BIN/'mu300-usb')+'" "$@"')
        self.stub('logger','exit 0');self.stub('uci',r'''
while :; do case "$1" in -q) shift ;; -t) shift 2 ;; *) break ;; esac; done
case $1 in
get) key=${2##*.}; case $key in usb) echo device ;; *) [ ! -f "$STUBLOG/$key" ] || cat "$STUBLOG/$key" ;; esac ;;
set) key=${2%%=*}; printf %s "${2#*=}" > "$STUBLOG/${key##*.}" ;;
commit) echo commit >> "$STUBLOG/commits" ;;
esac
''')
        self.stub('ucode','echo \'{"state":"disconnected","links":[]}\'')

    def env(self,**extra):
        values=dict(MU300_SYSROOT=self.root,MU300_USB_DEVICE='u30air',MU300_USB_ROLE_FILE=self.role,
                    MU300_USB_ROLE_DRIVER=self.driver,MU300_USB_ROLE_STATE=self.tmp/'state',
                    MU300_USB_ROLE_LOCK=self.tmp/'role.lock',MU300_USB_GADGET_DIR=self.tmp/'gadgets',
                    MU300_USB_RESCAN_MARKER=self.tmp/'rescan',MU300_USB_RESCAN_DELAY='0',
                    MU300_USB_BOOT_FILE=self.tmp/'boot',MU300_USB_NET_CLASS=self.tmp/'net',
                    MU300_USB_BUS=self.tmp/'bus',MU300_USB_UDC_CLASS=self.tmp/'udcs',
                    MU300_USB_LANE_PARAM=self.tmp/'lane',MU300_USB_LANE_STATE=self.tmp/'lane-state')
        values.update(extra);return super().env(**values)

    def status(self,shell,**extra):
        result=self.script(shell,USB,'get',**extra)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def wait_policy(self,value):
        for i in range(150):
            if self.control.read_text().strip()==value:return
            time.sleep(.02)
        self.assertEqual(self.control.read_text().strip(),value)

    def test_policy_differs_from_current_role_and_auto_is_not_pending_forever(self):
        for shell in self.each_shell():
            self.control.write_text('auto\n');self.role.write_text('host\n')
            status=self.status(shell)
            self.assertEqual((status['force_role_supported'],status['role_override']),(1,'auto'))
            result=json.loads(self.script(shell,USB,'set-role','device','0').stdout)
            self.assertEqual(result,{'ok':1,'pending':1});self.wait_policy('device')
            status=self.status(shell);self.assertEqual(status['role_pending'],1)
            self.role.write_text('device\n');self.assertEqual(self.status(shell)['role_pending'],0)
            self.script(shell,USB,'set-role','auto','0');self.wait_policy('auto')
            status=self.status(shell)
            self.assertEqual((status['role_target'],status['role_pending'],status['role_error']),('auto',0,0))

    def test_off_model_and_custom_adapters_reject_auto_without_writes(self):
        for shell in self.each_shell():
            for extra in ({'MU300_USB_DEVICE':'f50'},{'MU300_USB_DEVICE':'v50'},
                          {'MU300_USB_ROLE_COMMAND':'/custom/adapter'}):
                (self.tmp/'commits').unlink(missing_ok=True)
                self.assertEqual(self.status(shell,**extra)['force_role_supported'],0)
                result=self.script(shell,USB,'set-role','auto','0',**extra)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((self.tmp/'commits').exists())

    def test_host_boot_policy_applies_force_even_when_pd_is_already_host(self):
        for shell in self.each_shell():
            self.control.write_text('auto\n');self.role.write_text('host\n')
            (self.tmp/'role_auto').write_text('1')
            result=self.script(shell,USB,'boot')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.control.read_text().strip(),'host')

    def test_ambiguous_native_controller_is_not_selected(self):
        other=self.driver/'other/usb_role_override';other.parent.mkdir();other.write_text('auto\n')
        for shell in self.each_shell():
            self.assertEqual(self.status(shell)['force_role_supported'],0)
            self.assertNotEqual(self.script(shell,USB,'set-role','auto','0').returncode,0)
