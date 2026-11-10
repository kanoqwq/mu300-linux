"""Exercise the real Android staging/install loop on a temporary TF directory."""
import shutil
import tarfile

from helpers import TOP, ShellTest
from test_user_packages import openwrt_old, openwrt_new, write


class AndroidUpgrade(ShellTest):
    def test_keep_personal_settings_add_new_configs_and_restore_packages(self):
        src = (TOP / 'tools/android-install.sh').read_text()
        # No mount, formatter, boot-chain or block-device code runs in this test.
        functions = src[src.index('# --- user-packages begin'):src.index('# --- install-os begin')]
        stage = src[src.index('# --- install-os begin'):src.index('\nmkdir -p $M/.mu300\n')]
        disk = self.tmp / 'disk'; payload = self.tmp / 'payload'; image = self.tmp / 'image'
        payload.mkdir()
        for shell in self.each_shell():
            for directory in (disk, image):
                shutil.rmtree(directory, ignore_errors=True)
            old = disk / 'openwrt'
            openwrt_old(old)
            openwrt_new(image, {'sqm-scripts': ['etc/config/sqm']})
            write(old, {'etc/shadow': 'root:kept-hash:0:0:99999:7:::\n',
                        'etc/config/wireless': "config wifi-iface user_ap\n option key 'user-new-password!'\n",
                        'etc/unisoc-modem/sms.json': 'messages\n',
                        'etc/mu300/hotspot.conf': 'SSID=Personal\nPSK=personal-password\n',
                        'etc/rc.local': 'personal rc\n'})
            write(image, {'etc/shadow': 'root:new-image-default-hash:0:0:99999:7:::\n', 'etc/config/sqm': 'image sqm\n',
                          'etc/init.d/dnsmasq': '#!/bin/sh\n', 'sbin/init': '#!/bin/sh\n'})
            (image / 'etc/init.d/dnsmasq').chmod(0o755)
            (image / 'etc/rc.d').mkdir()
            with tarfile.open(payload / 'mu300-openwrt.tar.gz', 'w:gz') as archive:
                archive.add(image, arcname='.')
            code = ('set -e\nsay() { echo "$*"; }; say_i18n() { echo "$2"; }\n' + functions + '\n' + stage)
            result = self.sh(shell, code, M=disk, P=payload, OSES='openwrt', UPDATE=1,
                             SD_MODE=1, DEFAULT_LINUX=1, PWHASH='', ssid='', psk='',
                             MU300_KEEP_PAYLOAD=1)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((old / 'etc/config/dhcp').read_text(), 'old dhcp\n')
            self.assertEqual((old / 'etc/config/wireless').read_text(), "config wifi-iface user_ap\n option key 'user-new-password!'\n")
            self.assertEqual((old / 'etc/config/sqm').read_text(), 'image sqm\n')
            self.assertEqual((old / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertEqual((old / 'etc/unisoc-modem/sms.json').read_text(), 'messages\n')
            self.assertIn('kept-hash', (old / 'etc/shadow').read_text())
            self.assertIn('personal-password', (old / 'etc/mu300/hotspot.conf').read_text())
            self.assertEqual((old / 'etc/rc.local').read_text(), 'personal rc\n')
            self.assertIn('luci-app-openclash', (old / 'etc/mu300/user-packages/wanted').read_text())
            self.assertFalse((old / 'etc/config/openclash').exists())
            self.assertTrue((old / 'etc/mu300/orphaned-config/etc/config/openclash').exists())
            self.assertFalse((disk / 'openwrt.new').exists())
