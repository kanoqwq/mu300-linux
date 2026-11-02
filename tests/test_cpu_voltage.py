"""Boot-only voltage ABI, packaging and standalone capability boundaries."""
import json
import shutil
import subprocess
import unittest
from helpers import TOP, ShellTest

APP = TOP / 'openwrt/luci-app-mu300'

class CpuVoltage(unittest.TestCase):
    def test_firmware_quantization_roundtrip(self):
        for uv in range(-50000, 25001, 3125):
            magnitude = abs(uv) // 1000
            decoded = ((magnitude * 1000 + 3124) // 3125) * 3125
            self.assertEqual(-decoded if uv < 0 else decoded, uv)
            self.assertLessEqual(magnitude, 100)

    def test_boot_only_kernel_and_loader(self):
        driver = (TOP/'upstream/port/drivers/cpufreq/sprd-cpufreq-v2-driver.c').read_text()
        self.assertIn('voltage_offset_count, 0444', driver)
        self.assertIn('.suppress_bind_attrs = true', driver)
        self.assertNotIn('module_exit(', driver)
        self.assertIn('if (firmware_initialized)', driver)
        self.assertIn('CONFIG_ARM_SPRD_CPUFREQ_V2=m', (TOP/'upstream/mu300-mainline.config').read_text())
        self.assertNotIn('sprd-cpufreq-v2', (TOP/'upstream/module-order.txt').read_text())
        loader = (TOP/'rootfs/overlay/opt/mu300/bin/cpu-voltage-platform').read_text()
        self.assertLess(loader.index('sync #'), loader.index('insmod "$ko"'))
        self.assertNotIn('sha256sum', loader)
        self.assertIn('sml-voltage-check', loader)
        self.assertIn('parm=voltage_offset_uv:', loader)
        self.assertIn('flock -x 9', loader)

    def test_permissions_and_build_contract(self):
        acl=json.loads((APP/'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertIn('cpu_voltage_get', acl['read']['ubus']['mu300dash'])
        self.assertNotIn('cpu_voltage_save', acl['read']['ubus']['mu300dash'])
        for script in ('build-openwrt-tf-magisk.sh','make-release.sh'):
            text=(TOP/'tools'/script).read_text()
            for entry in ('cpu-voltage.uc', 'cpu-voltage-platform', 'S08mu300-cpu-driver', 'K89unisoc-cpu'):
                self.assertIn(entry,text)
        init=(APP/'root/etc/init.d/unisoc-cpu').read_text()
        self.assertIn('shutdown()',init)
        self.assertNotIn('stop()',init)
        self.assertIn('cpu-voltage confirm', init)
        self.assertIn('START=99', init)

    @unittest.skipUnless(shutil.which('ucode') and shutil.which('jsonfilter'), 'requires OpenWrt ucode/jsonfilter; also run cpu_voltage_fixture.sh in the OpenWrt container')
    def test_isolated_profiles(self):
        subprocess.run(['sh',str(TOP/'tests/cpu_voltage_fixture.sh'),str(APP/'root/usr/libexec/unisoc-modem/cpu-voltage.uc')],check=True,timeout=30)

class DriverVoltageDiscovery(ShellTest):
    def setUp(self):
        super().setUp()
        source=(TOP/'rootfs/overlay/opt/mu300/bin/cpu-voltage-platform').read_text()
        self.check=source[source.index('voltage_parameter() {'):source.index('case ${1:-load}')]
        self.check=self.check.replace('/sys/',str(self.tmp)+'/sys/').replace('/dev/',str(self.tmp)+'/dev/').replace('/opt/mu300/bin/',str(self.tmp)+'/stubs/').replace('[ -b ','[ -f ').replace('[ ! -b ','[ ! -f ')
        self.compatible=self.tmp/'sys/firmware/devicetree/base/compatible'
        self.compatible.parent.mkdir(parents=True)
        self.compatible.write_bytes(b'zte,f50\0sprd,ums9620\0')
        self.driver=self.tmp/'driver.ko';self.driver.write_bytes(b'\x7fELF\0parm=voltage_offset_uv:Boot offsets\0')
        self.stub('sha256sum','exit 99')  # Firmware version hashes must never be consulted.
        self.stub('sml-voltage-check','exit 0')
        for slot in ('a','b'):
            part=self.tmp/'dev/disk/by-partlabel'/('sml_'+slot)
            part.parent.mkdir(parents=True,exist_ok=True);part.write_bytes(b'firmware '+slot.encode())

    def script(self):return 'ko='+str(self.driver)+'\n'+self.check+'\nsupported'

    def test_missing_algorithm_helper_cannot_enable_offsets(self):
        (self.stubs/'sml-voltage-check').unlink()
        for shell in self.each_shell():
            result=self.sh(shell,self.script());self.assertNotEqual(result.returncode,0,result.stderr)

    def test_every_firmware_uses_algorithm_helper_and_failure_is_not_ignored(self):
        self.stub('sml-voltage-check','echo "$1" >> "$STUBLOG/algorithm"; exit 0')
        for shell in self.each_shell():self.assertEqual(self.sh(shell,self.script()).returncode,0)
        self.assertIn('sml_a',(self.tmp/'algorithm').read_text())
        self.assertIn('sml_b',(self.tmp/'algorithm').read_text())
        self.stub('sml-voltage-check','exit 1')
        for shell in self.each_shell():self.assertNotEqual(self.sh(shell,self.script()).returncode,0)

    def test_sysfs_labels_work_before_by_name_links_for_mmc_and_ufs(self):
        for slot,device in (('a','sda6'),('b','sda7')):
            (self.tmp/'dev/disk/by-partlabel'/('sml_'+slot)).unlink()
            uevent=self.tmp/'sys/class/block'/device/'uevent'
            uevent.parent.mkdir(parents=True);uevent.write_text('PARTNAME=sml_'+slot+'\n')
            (self.tmp/'dev'/device).write_bytes(b'firmware '+slot.encode())
        for shell in self.each_shell():self.assertEqual(self.sh(shell,self.script()).returncode,0)

    def test_old_missing_module_or_unadapted_soc_does_not_offer_voltage(self):
        self.driver.write_bytes(b'old parameterless module')
        for shell in self.each_shell():self.assertNotEqual(self.sh(shell,self.script()).returncode,0)
        self.driver.unlink()
        for shell in self.each_shell():self.assertNotEqual(self.sh(shell,self.script()).returncode,0)
        self.driver.write_bytes(b'\0parm=voltage_offset_uv:Boot offsets\0')
        self.compatible.write_bytes(b'other,sprd,ums9620\0')
        for shell in self.each_shell():self.assertNotEqual(self.sh(shell,self.script()).returncode,0)

    def test_packaging_installs_native_matcher_and_source_matches_e5(self):
        build=(TOP/'openwrt/build-rootfs.sh').read_text()
        self.assertIn('tools/sml-voltage/build.py',build)
        self.assertIn('cp /in/sml-voltage-check $R/opt/mu300/bin/sml-voltage-check',build)
        for name in ('tools/make-release.sh','tools/build-openwrt-tf-magisk.sh'):
            self.assertIn('./opt/mu300/bin/sml-voltage-check',(TOP/name).read_text())
        sibling=TOP.parent/'e5-linux/devices/common/sml-voltage-check.c'
        if sibling.exists():self.assertEqual(sibling.read_bytes(),(TOP/'tools/sml-voltage/sml-voltage-check.c').read_bytes())
