"""Exact M3 matching and NFC refusal, using fake roots/properties only."""
import re
import runpy
import shlex
import shutil
from helpers import BIN, TOP, ShellTest


class M3Model(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp/'root'
        (self.root/'proc/device-tree/charger_policy_service').mkdir(parents=True)
        (self.root/'etc/mu300').mkdir(parents=True)
        self.marker = self.root/'etc/mu300/device-model'
        self.marker.write_text('m3\n')
        self.stub('getprop', 'case "$1" in ro.product.model) echo "$TEST_MODEL";; ro.product.device) echo "$TEST_DEVICE";; ro.board.platform) echo "$TEST_PLATFORM";; esac')

    def test_m3_refines_the_model_without_changing_hardware_dispatch(self):
        for shell in self.each_shell():
            self.assertEqual(self.script(shell,BIN/'mu300-device',MU300_SYSROOT=self.root).stdout.strip(),'u30air')
            self.assertEqual(self.script(shell,BIN/'mu300-device','--model',MU300_SYSROOT=self.root).stdout.strip(),'m3')
        shutil.rmtree(self.root/'proc/device-tree/charger_policy_service')
        for shell in self.each_shell():
            self.assertEqual(self.script(shell,BIN/'mu300-device','--model',MU300_SYSROOT=self.root).stdout.strip(),'f50')

    def test_nfc_rejects_m3_before_gpio_or_i2c_access(self):
        for name in ('gpioset','i2ctransfer','busybox'):
            self.stub(name, 'echo hardware-access >> "$STUBLOG/calls"; exit 99')
        for shell in self.each_shell():
            for action in ('supported','wifi','sync','on','off'):
                result=self.script(shell,BIN/'mu300-nfc',action,MU300_SYSROOT=self.root,MU300_BIN=BIN)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('M3 has no supported NFC tag',result.stderr)
                self.assertFalse((self.tmp/'calls').exists())

    def test_unlabelled_u30air_retains_nfc_support(self):
        self.marker.unlink()
        for shell in self.each_shell():
            result=self.script(shell,BIN/'mu300-nfc','supported',MU300_SYSROOT=self.root,MU300_BIN=BIN)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.script(shell,BIN/'mu300-device','--model',MU300_SYSROOT=self.root).stdout.strip(),'u30air')

    def test_install_marker_is_derived_from_current_android_after_restore(self):
        source=(TOP/'tools/android-install.sh').read_text()
        match=re.search(r'^device_model_save\(\) \{.*?^\}',source,re.M|re.S)
        self.assertIsNotNone(match)
        self.assertLess(source.index('image_own_restore "$M/$os.new"',source.index('for os in')),source.index('device_model_save "$R"'))
        code=match[0]+'\ndevice_model_save '+shlex.quote(str(self.root))
        for shell in self.each_shell():
            for model,device,platform,want in [('MU5352','MU5352','ums9620',True),
                                               ('U30Air','U30Air','ums9620',False),
                                               ('MU5352','MU5352','ums9632',False)]:
                self.marker.write_text('m3\n')
                result=self.sh(shell,code,TEST_MODEL=model,TEST_DEVICE=device,TEST_PLATFORM=platform)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(self.marker.exists(),want)

    def test_host_profile_rejects_inconsistent_m3_properties(self):
        helper=shlex.quote(str(TOP/'tools/android-profile.sh'))
        for shell in self.each_shell():
            for args,want in [('MU5352 MU5352 ums9620','u30air'),('MU5352 MU5352 ums9632',''),('M3 MU5352 ums9620','')]:
                result=self.sh(shell,'. '+helper+'; mu300_android_profile '+args)
                self.assertEqual(result.stdout.strip(),want)
                self.assertEqual(result.returncode,0 if want else 3)

    def test_desktop_profile_uses_the_same_exact_identity(self):
        profile=runpy.run_path(str(TOP/'desktop/installer.py'))['android_profile']
        self.assertEqual(profile('MU5352\nMU5352','ums9620'),'u30air')
        for identity,platform in [('MU5352\nMU5352','ums9632'),('M3\nMU5352','ums9620')]:
            with self.assertRaises(ValueError):profile(identity,platform)
