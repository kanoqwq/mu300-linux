"""U30 Pro Type-C policy/API tests using isolated class files, never hardware."""
import json
from pathlib import Path
import shlex
import time
import unittest
from helpers import ShellTest, TOP

ROLE = TOP/'devices/u30pro/overlay/usr/sbin/u30pro-usb-role'
USB = TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb'


class NativeRoles(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root'; (self.root/'etc/mu300').mkdir(parents=True)
        (self.root/'etc/mu300/platform').write_text('u30pro\n')
        self.port=self.root/'sys/class/typec/port0';self.port.mkdir(parents=True)
        (self.port/'port_type').write_text('[dual] source sink\n')
        (self.port/'data_role').write_text('host [device]\n')
        (self.port/'power_role').write_text('source [sink]\n')
        self.cap=self.root/'sys/bus/platform/drivers/musb-sprd/controller/dual_role_capable'
        self.cap.parent.mkdir(parents=True);self.cap.write_text('1\n')
        self.stub('uci', r'''
while :; do case $1 in -q) shift ;; *) break ;; esac; done
cmd=$1;shift
case $cmd in
 get) key=${1##*.}; [ "$1" != unisoc_modem.usb ] || { echo device;exit; };
      [ ! -f "$STUBLOG/uci.$key" ] || cat "$STUBLOG/uci.$key" ;;
 set) key=${1%%=*}; printf '%s' "${1#*=}" > "$STUBLOG/uci.${key##*.}" ;;
 commit) echo "$1" >> "$STUBLOG/commits" ;;
esac
''')
        self.stub('ip','exit 0');self.stub('ubus','echo \'{"interface":[]}\'')
        self.stub('logger','exit 0')
        self.stub('i2cset','echo unexpected-i2c >> "$STUBLOG/unsafe";exit 99')
        self.stub('i2cget','echo unexpected-i2c >> "$STUBLOG/unsafe";exit 99')
        self.adapter=self.tmp/'native-role'

    def setup_shell(self,shell):
        self.adapter.write_text('#!/bin/sh\nexec '+shlex.join(shell+[str(ROLE)])+' "$@"\n')
        self.adapter.chmod(0o755)

    def env(self,**extra):
        extra.setdefault('U30PRO_USB_SYSROOT',self.root)
        extra.setdefault('MU300_USB_DEVICE','u30pro')
        extra.setdefault('MU300_USB_NATIVE_ROLE_COMMAND',self.adapter)
        extra.setdefault('MU300_USB_ROLE_FILE',self.tmp/'absent-dwc3-role')
        extra.setdefault('MU300_USB_ROLE_STATE',self.tmp/'role-state')
        extra.setdefault('MU300_USB_BOOT_FILE',self.tmp/'net-boot')
        extra.setdefault('MU300_USB_NET_CLASS',self.tmp/'net')
        extra.setdefault('MU300_USB_BUS',self.tmp/'usb-bus')
        extra.setdefault('MU300_USB_RESCAN_MARKER',self.tmp/'rescan')
        extra.setdefault('MU300_USB_GADGET_DIR',self.tmp/'gadgets')
        extra.setdefault('MU300_USB_NET_LOCK',self.tmp/'net.lock')
        return super().env(**extra)

    def call(self,shell,*args):
        self.setup_shell(shell)
        return self.script(shell,USB,*args)

    def wait_policy(self,value):
        for _ in range(100):
            if (self.port/'port_type').read_text().strip()==value:return
            time.sleep(.02)
        self.fail('Type-C policy was not applied: '+value)

    def test_native_status_reports_typec_and_compiled_host_capability(self):
        for shell in self.each_shell():
            r=self.call(shell,'get');self.assertEqual(r.returncode,0,r.stderr)
            s=json.loads(r.stdout)
            self.assertEqual((s['role'],s['role_native'],s['host_supported'],s['role_policy']),('device',1,1,'auto'))
            self.assertFalse((self.tmp/'commits').exists())

    def test_auto_host_device_and_persistence_without_raw_i2c(self):
        for shell in self.each_shell():
            for mode,wire in [('host','source'),('device','sink'),('auto','dual')]:
                r=self.call(shell,'set-role',mode,'1')
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['ok'],1)
                self.wait_policy(wire)
                s=json.loads(self.call(shell,'get').stdout)
                self.assertEqual(s['role_policy'],mode)
                self.assertEqual(s['role_saved'],mode);self.assertEqual(s['role_persist'],1)
                self.assertEqual(s['role_auto'],int(mode=='host'))
            self.assertFalse((self.tmp/'unsafe').exists())

    def test_temporary_host_returns_to_auto_at_next_boot(self):
        for shell in self.each_shell():
            r=self.call(shell,'set-role','host','0');self.assertEqual(r.returncode,0,r.stderr)
            self.wait_policy('source')
            s=json.loads(self.call(shell,'get').stdout)
            self.assertEqual((s['role_saved'],s['role_persist'],s['role_auto']),('auto',0,0))
            r=self.call(shell,'boot');self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((self.port/'port_type').read_text().strip(),'dual')

    def test_explicit_saved_device_policy_survives_boot(self):
        for shell in self.each_shell():
            (self.tmp/'uci.native_role_policy').write_text('device')
            (self.tmp/'uci.native_role_persist').write_text('1')
            (self.port/'port_type').write_text('dual')
            r=self.call(shell,'boot');self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((self.port/'port_type').read_text().strip(),'sink')

    def test_old_gadget_only_kernel_does_not_gain_host_from_typec_presence(self):
        self.cap.write_text('0')
        for shell in self.each_shell():
            s=json.loads(self.call(shell,'get').stdout);self.assertEqual(s['host_supported'],0)
            r=self.call(shell,'set-role','host','1');self.assertNotEqual(r.returncode,0)
            self.assertEqual(json.loads(r.stdout)['ok'],0)
            self.assertFalse((self.tmp/'commits').exists())

    def test_non_u30pro_cannot_opt_into_auto_or_native_policy(self):
        for shell in self.each_shell():
            self.setup_shell(shell)
            r=self.script(shell,USB,'set-role','auto','0',MU300_USB_DEVICE='f50')
            self.assertNotEqual(r.returncode,0)
            self.assertEqual(json.loads(r.stdout)['ok'],0)
            r=self.script(shell,ROLE,'host',U30PRO_USB_SYSROOT=self.root)
            self.assertEqual(r.returncode,0,r.stderr)
            (self.root/'etc/mu300/platform').write_text('f50')
            r=self.script(shell,ROLE,'auto',U30PRO_USB_SYSROOT=self.root)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual((self.port/'port_type').read_text().strip(),'source')
            (self.root/'etc/mu300/platform').write_text('u30pro')

    def test_ambiguous_ports_and_missing_kernel_capability_refuse_changes(self):
        extra=self.root/'sys/class/typec/port1';extra.mkdir()
        (extra/'data_role').write_text('device');(extra/'port_type').write_text('dual')
        for shell in self.each_shell():
            r=self.script(shell,ROLE,'host',U30PRO_USB_SYSROOT=self.root)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('more than one',r.stderr)
        (extra/'data_role').unlink();(extra/'port_type').unlink();extra.rmdir();self.cap.unlink()
        for shell in self.each_shell():
            r=self.script(shell,ROLE,'host',U30PRO_USB_SYSROOT=self.root)
            self.assertNotEqual(r.returncode,0)


if __name__=='__main__':unittest.main(verbosity=2)
