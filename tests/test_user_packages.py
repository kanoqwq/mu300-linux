"""Packages installed on the device across an update (#116): mu300-update's upk_record writes down what was added to
a system (OpenWrt's world file, dpkg's manual packages) less the image's own set and sets the UCI files of those
packages aside; mu300-user-packages installs them again at the first boot, puts their settings back, takes a dead DNS
forwarder and a missing LuCI theme out, and says what it did. apk, apt-get, dpkg-query and uci are stubs."""
import os
import re
import shutil
import subprocess
import sys

from helpers import BIN, TOP, ShellTest

UPD = BIN / 'mu300-update'
UPK = BIN / 'mu300-user-packages'


def write(root, files):
    for f, data in files.items():
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data)


def apk_db(pkgs):
    """lib/apk/db/installed: {name: [paths]} in apk's text format (P:, F: directory, R: file)."""
    out = []
    for name, paths in pkgs.items():
        out += ['C:Q1x=', f'P:{name}', 'V:1.0-r1']
        dirs = {}
        for p in paths:
            d, f = p.rsplit('/', 1)
            dirs.setdefault(d, []).append(f)
        for d, fs in dirs.items():
            out.append(f'F:{d}')
            out += [f'R:{f}' for f in fs]
        out.append('')
    return '\n'.join(out) + '\n'


IMAGE_WORLD = 'base-files=1711~f5dae5ece4\nbusybox\ndnsmasq\nluci\nluci-theme-aurora><Q1abc=\n'
IMAGE_DB = {'base-files': ['etc/config/system'], 'busybox': ['bin/busybox'], 'dnsmasq': ['etc/config/dhcp', 'etc/init.d/dnsmasq'],
            'luci': ['etc/config/luci'], 'luci-theme-aurora': ['www/luci-static/aurora/main.css']}


def openwrt_old(root, image_list=True):
    """An OpenWrt the user added OpenClash (with an init script and a UCI file), the Argon theme from a file and a
    kmod to."""
    db = dict(IMAGE_DB)
    db['luci-app-openclash'] = ['etc/config/openclash', 'etc/init.d/openclash', 'usr/share/openclash/x.sh']
    db['luci-theme-argon'] = ['etc/config/argon', 'www/luci-static/argon/x.css']
    db['kmod-tun'] = ['lib/modules/6.12/tun.ko']
    db['coreutils'] = ['usr/bin/coreutils']      # a dependency, not in the world file
    files = {'etc/apk/world': IMAGE_WORLD + 'luci-app-openclash\nluci-theme-argon><Q1xyz=\nkmod-tun\n',
             'lib/apk/db/installed': apk_db(db),
             'etc/config/openclash': "config openclash 'config'\n\toption enable '1'\n",
             'etc/config/argon': 'config global\n', 'etc/config/dhcp': 'old dhcp\n', 'etc/config/luci': 'old luci\n',
             'etc/config/system': 'old system\n', 'etc/config/mine': 'by hand\n',
             'etc/mu300/image-version': 'v1\n', 'etc/mu300/vpn.conf': 'ENABLE=1\n'}
    if image_list:
        files['etc/mu300/image-packages'] = 'base-files\nbusybox\ndnsmasq\nluci\nluci-theme-aurora\n'
    write(root, files)
    (root / 'etc/rc.d').mkdir(parents=True, exist_ok=True)
    for n in ('S99openclash', 'S19dnsmasq'):
        os.symlink(f'../init.d/{n[3:]}', root / 'etc/rc.d' / n)


def openwrt_new(root, extra_db=None):
    """The new image as unpacked: its own world, package database, etc/config and lists."""
    db = dict(IMAGE_DB)
    db.update(extra_db or {})
    write(root, {'etc/apk/world': IMAGE_WORLD + ''.join(f'{n}\n' for n in (extra_db or {})),
                 'lib/apk/db/installed': apk_db(db),
                 'etc/config/dhcp': 'image dhcp\n', 'etc/config/luci': 'image luci\n', 'etc/config/system': 'image\n',
                 'etc/mu300/image-version': 'v2\n',
                 'etc/mu300/image-packages': 'base-files\nbusybox\ndnsmasq\nluci\nluci-theme-aurora\n'})


def keep(old, new):
    """What apply_one copies between image_own_save and upk_record (keep_list of OpenWrt)."""
    for k in ('etc/config', 'etc/mu300'):
        shutil.rmtree(new / k, ignore_errors=True)
        shutil.copytree(old / k, new / k, symlinks=True)


class Record(ShellTest):
    """upk_record: what was added, written into the new system; the UCI files of those packages set aside."""

    def setUp(self):
        super().setUp()
        self.old, self.new = self.tmp / 'old', self.tmp / 'new'

    def fresh(self, **kw):
        for d in (self.old, self.new):
            shutil.rmtree(d, ignore_errors=True)
        openwrt_old(self.old, **{k: v for k, v in kw.items() if k == 'image_list'})
        openwrt_new(self.new, kw.get('new_db'))

    def record(self, shell, os_name='openwrt-luci', pre='', **env):
        code = (f'. "{UPD}"; {pre} image_own_save "{self.new}" && '
                f'keep() {{ for k in etc/config etc/mu300; do rm -rf "{self.new}/$k"; cp -a "{self.old}/$k" "{self.new}/$k"; done; }} && keep && '
                f'image_own_restore "{self.new}" && upk_record "{self.old}" "{self.new}" {os_name}; echo "rc=$?"')
        return self.sh(shell, code, MU300_LIB=1, **env)

    def rec(self, name):
        p = self.new / 'etc/mu300/user-packages' / name
        return p.read_text() if p.exists() else None

    def test_openwrt_added_packages_are_written_down_and_their_config_set_aside(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            # by hand, less the image's set, the kmod (another kernel's) and a dependency; versions and pins off
            self.assertEqual(self.rec('wanted'), 'luci-app-openclash\nluci-theme-argon\n')
            self.assertEqual(self.rec('state'), 'pending\n')
            self.assertEqual(self.rec('from'), 'v1\n')
            self.assertEqual(self.rec('done'), '')
            self.assertIn('luci-app-openclash etc/config/openclash\n', self.rec('files'))
            self.assertIn('luci-app-openclash etc/init.d/openclash\n', self.rec('files'))
            self.assertNotIn('usr/share', self.rec('files'))   # only etc/
            self.assertIn('S99openclash\n', self.rec('rc.d'))
            orph = self.new / 'etc/mu300/orphaned-config/etc/config'
            self.assertEqual((orph / 'openclash').read_text(), "config openclash 'config'\n\toption enable '1'\n")
            self.assertEqual((orph / 'argon').read_text(), 'config global\n')
            self.assertFalse((self.new / 'etc/config/openclash').exists())
            # the image's own files and one made by hand stay where they are, as the device had them
            self.assertEqual((self.new / 'etc/config/dhcp').read_text(), 'old dhcp\n')
            self.assertEqual((self.new / 'etc/config/mine').read_text(), 'by hand\n')
            self.assertEqual((self.new / 'etc/mu300/vpn.conf').read_text(), 'ENABLE=1\n')
            # the new image's version and list, not the old ones that came with etc/mu300
            self.assertEqual((self.new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertFalse((self.new / '.mu300-image').exists())
            self.assertIn('luci-app-openclash luci-theme-argon', r.stdout)
            self.assertIn('/etc/config/openclash -> /etc/mu300/orphaned-config/etc/config/openclash', r.stdout)
            self.assertIn('first boot installs them again', r.stdout)

    def test_a_file_the_new_image_has_is_never_set_aside(self):
        # a package that owned (or claimed) dhcp: the image has its own, so it stays
        for shell in self.each_shell():
            self.fresh()
            db = (self.old / 'lib/apk/db/installed').read_text().replace('F:etc/config\nR:openclash',
                                                                       'F:etc/config\nR:openclash\nR:dhcp')
            (self.old / 'lib/apk/db/installed').write_text(db)
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual((self.new / 'etc/config/dhcp').read_text(), 'old dhcp\n')
            self.assertFalse((self.new / 'etc/mu300/orphaned-config/etc/config/dhcp').exists())

    def test_a_package_the_new_image_has_is_neither_wanted_nor_set_aside(self):
        for shell in self.each_shell():
            self.fresh(new_db={'luci-app-openclash': ['etc/config/openclash', 'etc/init.d/openclash']})
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('wanted'), 'luci-theme-argon\n')
            self.assertEqual((self.new / 'etc/config/openclash').read_text(), "config openclash 'config'\n\toption enable '1'\n")

    def test_without_the_old_images_list_the_new_ones_and_packages_txt_stand_in(self):
        for shell in self.each_shell():
            self.fresh(image_list=False)
            # a package the old image had and the new one dropped on purpose: in its packages.txt, not added by hand
            w = self.old / 'etc/apk/world'
            w.write_text(w.read_text() + 'oldthing\n')
            (self.old / 'etc/mu300/packages.txt').write_text(
                'oldthing-2024.10.04~abc-r1 aarch64_generic {feeds/x} (GPL) [installed]\n'
                'busybox-1.37.0-r5 aarch64_generic {feeds/base} (GPL) [installed]\n')
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('wanted'), 'luci-app-openclash\nluci-theme-argon\n')

    def test_nothing_added_leaves_no_record(self):
        for shell in self.each_shell():
            self.fresh()
            (self.old / 'etc/apk/world').write_text(IMAGE_WORLD + 'kmod-tun\n')
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('none', r.stdout)
            self.assertFalse((self.new / 'etc/mu300/user-packages').exists())
            self.assertTrue((self.new / 'etc/config/openclash').exists())

    def test_an_old_record_is_replaced_and_what_it_never_got_back_stays_wanted(self):
        for shell in self.each_shell():
            self.fresh()
            write(self.old, {'etc/mu300/user-packages/wanted': 'adguardhome\nluci-app-sqm\nnano\n',
                             'etc/mu300/user-packages/done': 'nano\n',
                             'etc/mu300/user-packages/failed': 'luci-app-sqm ERROR: unable to select\n',
                             'etc/mu300/user-packages/files': 'adguardhome etc/config/adguardhome\n',
                             'etc/mu300/user-packages/log': 'old log\n'})
            r = self.record(shell)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('wanted'), 'adguardhome\nluci-app-openclash\nluci-theme-argon\n')
            self.assertIn('adguardhome etc/config/adguardhome\n', self.rec('files'))
            self.assertIsNone(self.rec('log'))
            self.assertEqual(self.rec('failed'), '')

    def test_skipped_still_sets_the_settings_aside(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.record(shell, MU300_KEEP_PACKAGES='0')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('state'), 'skipped\n')
            self.assertIn('not installed again', r.stdout)
            self.assertTrue((self.new / 'etc/mu300/orphaned-config/etc/config/openclash').exists())

    def test_safe_under_set_e(self):
        # tools/android-install.sh runs with set -e
        for shell in self.each_shell():
            self.fresh()
            r = self.record(shell, pre='set -e;')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('wanted'), 'luci-app-openclash\nluci-theme-argon\n')

    def test_ubuntu_manual_packages_less_the_images(self):
        def dpkg(names, auto=()):
            st = ''.join(f'Package: {n}\nStatus: install ok installed\nVersion: 1\n\n' for n in names)
            st += 'Package: removed\nStatus: deinstall ok config-files\n\n'
            es = ''.join(f'Package: {n}\nArchitecture: arm64\nAuto-Installed: 1\n\n' for n in auto)
            return {'var/lib/dpkg/status': st, 'var/lib/apt/extended_states': es}
        for shell in self.each_shell():
            for d in (self.old, self.new):
                shutil.rmtree(d, ignore_errors=True)
            write(self.old, dpkg(['bash', 'openssh-server', 'htop', 'libfoo', 'linux-image-6.8'], auto=['libfoo']))
            write(self.old, {'etc/mu300/image-packages': 'bash\nopenssh-server\n', 'etc/mu300/image-version': 'v1\n',
                             'etc/config/x': ''})
            write(self.new, dpkg(['bash', 'openssh-server']))
            write(self.new, {'etc/mu300/image-version': 'v2\n', 'etc/mu300/image-packages': 'bash\nopenssh-server\n'})
            r = self.record(shell, 'ubuntu')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.rec('wanted'), 'htop\n')
            self.assertEqual(self.rec('files'), '')
            # without the old image's list: the new image's manual set
            (self.old / 'etc/mu300/image-packages').unlink()
            shutil.rmtree(self.new / 'etc/mu300/user-packages')
            r = self.record(shell, 'ubuntu')
            self.assertEqual(self.rec('wanted'), 'htop\n', r.stderr)

    def ubuntu_roots(self, old_codename='noble', new_codename='noble', manual=('bash', 'htop')):
        def dpkg(names):
            return {'var/lib/dpkg/status': ''.join(f'Package: {n}\nStatus: install ok installed\n\n' for n in names),
                    'var/lib/apt/extended_states': ''}
        for d in (self.old, self.new):
            shutil.rmtree(d, ignore_errors=True)
        write(self.old, dpkg(manual))
        write(self.old, {
            'usr/lib/os-release': f'NAME="Ubuntu"\nVERSION_CODENAME={old_codename}\n',
            'etc/mu300/image-packages': 'bash\n', 'etc/mu300/image-version': 'v1\n',
            # the image's own source (the new image has it too) and two the user added, one deb822 with a key
            'etc/apt/sources.list.d/ubuntu.sources': 'old\n',
            'etc/apt/sources.list.d/ppa-htop.list': 'deb [signed-by=/etc/apt/keyrings/htop.gpg] http://ppa noble main\n',
            'etc/apt/sources.list.d/vendor.sources': 'Types: deb\nSigned-By: /usr/share/keyrings/vendor.gpg\n',
            'etc/apt/keyrings/htop.gpg': 'k1', 'usr/share/keyrings/vendor.gpg': 'k2',
            'usr/share/keyrings/unrelated.gpg': 'k3', 'usr/share/keyrings/ubuntu-archive-keyring.gpg': 'old key',
            'etc/apt/trusted.gpg.d/legacy.gpg': 'k4',
            # pinning: the user's own, and one the new image ships itself (never replaced)
            'etc/apt/preferences.d/htop-ppa': 'Package: *\nPin: release o=LP-PPA-htop\nPin-Priority: 600\n',
            'etc/apt/preferences.d/image-pin': 'old pin\n',
            # htop's units: one enabled, one masked, one disabled, a template (skipped)
            'var/lib/dpkg/info/htop:arm64.list': '/.\n/usr/lib/systemd/system/htopd.service\n'
                                                 '/lib/systemd/system/htop.timer\n/usr/lib/systemd/system/htop-x.socket\n'
                                                 '/usr/lib/systemd/system/htop@.service\n/usr/bin/htop\n'
                                                 '/usr/lib/systemd/system/htop-alias.service\n'})
        os.makedirs(self.old / 'etc/systemd/system/multi-user.target.wants')
        os.symlink('/usr/lib/systemd/system/htopd.service', self.old / 'etc/systemd/system/multi-user.target.wants/htopd.service')
        os.symlink('/dev/null', self.old / 'etc/systemd/system/htop.timer')
        # an alias of htopd.service (smartd.service of smartmontools): it follows its unit, never recorded itself
        os.makedirs(self.old / 'usr/lib/systemd/system', exist_ok=True)
        os.symlink('htopd.service', self.old / 'usr/lib/systemd/system/htop-alias.service')
        write(self.new, dpkg(['bash']))
        write(self.new, {'usr/lib/os-release': f'NAME="Ubuntu"\nVERSION_CODENAME={new_codename}\n',
                         'etc/mu300/image-packages': 'bash\n', 'etc/mu300/image-version': 'v2\n',
                         'etc/apt/sources.list.d/ubuntu.sources': 'image\n',
                         'etc/apt/preferences.d/image-pin': 'image pin\n',
                         'usr/share/keyrings/ubuntu-archive-keyring.gpg': 'image key'})

    def test_ubuntu_apt_sources_and_units(self):
        for shell in self.each_shell():
            self.ubuntu_roots()
            r = self.record(shell, 'ubuntu')
            self.assertIn('rc=0', r.stdout, r.stderr)
            src = self.new / 'etc/apt/sources.list.d'
            # the image's own never replaced; the user's kept, with the keys they name and trusted.gpg.d's
            self.assertEqual((src / 'ubuntu.sources').read_text(), 'image\n')
            self.assertTrue((src / 'ppa-htop.list').exists())
            self.assertTrue((src / 'vendor.sources').exists())
            self.assertEqual((self.new / 'etc/apt/keyrings/htop.gpg').read_text(), 'k1')
            self.assertEqual((self.new / 'usr/share/keyrings/vendor.gpg').read_text(), 'k2')
            self.assertEqual((self.new / 'etc/apt/trusted.gpg.d/legacy.gpg').read_text(), 'k4')
            self.assertFalse((self.new / 'usr/share/keyrings/unrelated.gpg').exists())
            self.assertEqual((self.new / 'usr/share/keyrings/ubuntu-archive-keyring.gpg').read_text(), 'image key')
            self.assertEqual(self.rec('apt-sources'),
                             'etc/apt/sources.list.d/ppa-htop.list\netc/apt/sources.list.d/vendor.sources\n')
            self.assertEqual(self.rec('units'), 'htop htop-x.socket disabled\nhtop htop.timer masked\n'
                                                'htop htopd.service enabled\n')
            self.assertIn('apt: /etc/apt/sources.list.d/ppa-htop.list kept', r.stdout)
            pref = self.new / 'etc/apt/preferences.d'
            self.assertIn('LP-PPA-htop', (pref / 'htop-ppa').read_text())
            self.assertEqual((pref / 'image-pin').read_text(), 'image pin\n')
            self.assertEqual(self.rec('apt-prefs'), 'etc/apt/preferences.d/htop-ppa\n')

    def test_ubuntu_sources_of_another_release_with_nothing_to_install_stay_off(self):
        # 24.04 -> 26.04 and no package to bring back: no first-boot check, so they are kept disabled
        for shell in self.each_shell():
            self.ubuntu_roots('noble', 'resolute', manual=('bash',))
            r = self.record(shell, 'ubuntu')
            self.assertIn('rc=0', r.stdout, r.stderr)
            src = self.new / 'etc/apt/sources.list.d'
            self.assertFalse((src / 'ppa-htop.list').exists())
            self.assertTrue((src / 'ppa-htop.list.disabled').exists())
            self.assertIn('is for Ubuntu noble, this is resolute: kept disabled', r.stdout)
            self.assertFalse((self.new / 'etc/mu300/user-packages').exists())
            # with a package to bring back they stay on: the first boot tries them (mu300-user-packages)
            self.ubuntu_roots('noble', 'resolute')
            self.record(shell, 'ubuntu')
            self.assertTrue((src / 'ppa-htop.list').exists())

    def test_android_install_has_the_same_block(self):
        upd = UPD.read_text()
        ai = (TOP / 'tools/android-install.sh').read_text()
        blk = re.compile(r'# --- user-packages begin\n.*?# --- user-packages end\n', re.S)
        self.assertEqual(blk.search(ai).group(0), blk.search(upd).group(0))
        # and it runs where mu300-update's does: after the settings, before the switch
        self.assertLess(ai.index('image_own_save "$M/$os.new"'), ai.index('for k in $keep'))
        self.assertLess(ai.index('upk_record "$M/$os"'), ai.index('mv $M/$os.new $M/$os'))


class ApplyOne(ShellTest):
    """apply_one with an OpenWrt that has OpenClash: the record, the set-aside UCI file, its rc.d link not carried
    over to a system without the service, the new image's version."""

    def test_apply_one(self):
        disk = self.tmp / 'disk'
        img = self.tmp / 'img'
        openwrt_new(img)
        write(img, {'sbin/init': '#!/bin/sh\n', 'etc/init.d/dnsmasq': '#!/bin/sh\n'})
        (img / 'sbin/init').chmod(0o755)
        (img / 'etc/init.d/dnsmasq').chmod(0o755)
        (img / 'etc/rc.d').mkdir()
        stage = disk / '.mu300-update'
        for shell in self.each_shell():
            shutil.rmtree(disk, ignore_errors=True)
            openwrt_old(disk / 'openwrt')
            write(disk / 'openwrt', {'sbin/init': '#!/bin/sh\n'})
            stage.mkdir(parents=True)
            subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
            r = self.sh(shell, f'. "{UPD}"; is_root() {{ false; }}; apply_one openwrt v2', MU300_LIB=1, MU300_DISK=disk,
                        MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0, r.stderr)
            new = disk / 'openwrt'
            self.assertEqual((new / 'etc/mu300/user-packages/wanted').read_text(), 'luci-app-openclash\nluci-theme-argon\n')
            self.assertTrue((new / 'etc/mu300/orphaned-config/etc/config/openclash').exists())
            self.assertFalse((new / 'etc/config/openclash').exists())
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertFalse((new / '.mu300-image').exists())
            # the service of a package the new system does not have: no dangling link; dnsmasq's comes along
            self.assertFalse(os.path.lexists(new / 'etc/rc.d/S99openclash'))
            self.assertTrue(os.path.islink(new / 'etc/rc.d/S19dnsmasq'))
            self.assertIn('packages installed on the device', r.stdout)
            self.assertIn('luci-app-openclash luci-theme-argon', r.stdout)

    def test_stray_config_of_v2026_10_18_goes(self):
        # the old system (a device updated to v2026.10.18) and the image both carry etc/config/attendedsysupgrade
        # and uhttpd.apk-new; neither stays, except attendedsysupgrade with its package installed in the new image
        disk = self.tmp / 'disk'
        stage = disk / '.mu300-update'
        for shell in self.each_shell():
            for extra_db, keeps in ((None, False), ({'attendedsysupgrade-common': ['etc/config/attendedsysupgrade']}, True)):
                shutil.rmtree(disk, ignore_errors=True)
                img = self.tmp / 'img'
                shutil.rmtree(img, ignore_errors=True)
                openwrt_new(img, extra_db)
                write(img, {'sbin/init': '#!/bin/sh\n', 'etc/config/attendedsysupgrade': 'image\n',
                            'etc/config/uhttpd.apk-new': 'image\n'})
                (img / 'sbin/init').chmod(0o755)
                (img / 'etc/rc.d').mkdir()
                openwrt_old(disk / 'openwrt')
                write(disk / 'openwrt', {'sbin/init': '#!/bin/sh\n', 'etc/config/attendedsysupgrade': 'stray\n',
                                         'etc/config/uhttpd.apk-new': 'stray\n', 'etc/config/uhttpd': 'mine\n'})
                stage.mkdir(parents=True)
                subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
                r = self.sh(shell, f'. "{UPD}"; is_root() {{ false; }}; apply_one openwrt v2', MU300_LIB=1, MU300_DISK=disk,
                            MU300_BIN=BIN)
                self.assertEqual(r.returncode, 0, r.stderr)
                new = disk / 'openwrt'
                self.assertFalse((new / 'etc/config/uhttpd.apk-new').exists(), keeps)
                self.assertEqual((new / 'etc/config/attendedsysupgrade').exists(), keeps)
                self.assertEqual((new / 'etc/config/uhttpd').read_text(), 'mine\n')   # the device's own stays
                # deleted, not set aside (the fixture's OpenClash config is, as always)
                orph = new / 'etc/mu300/orphaned-config/etc/config'
                self.assertFalse((orph / 'attendedsysupgrade').exists())
                self.assertFalse((orph / 'uhttpd.apk-new').exists())

    def test_with_the_image_config_files_added(self):
        # (#121) the update adds the config files of the image's packages the old system did not have, then sets the
        # orphaned ones aside: sqm-scripts, a package of the image the user had removed, gets the image's
        # /etc/config/sqm back, never set aside; OpenClash's (the user's own package) still goes aside
        disk = self.tmp / 'disk'
        img = self.tmp / 'img'
        openwrt_new(img, {'sqm-scripts': ['etc/config/sqm']})
        write(img, {'sbin/init': '#!/bin/sh\n', 'etc/config/sqm': 'image sqm\n',
                    'etc/mu300/image-packages': 'base-files\nbusybox\ndnsmasq\nluci\nluci-theme-aurora\nsqm-scripts\n'})
        (img / 'sbin/init').chmod(0o755)
        (img / 'etc/rc.d').mkdir()
        stage = disk / '.mu300-update'
        for shell in self.each_shell():
            shutil.rmtree(disk, ignore_errors=True)
            openwrt_old(disk / 'openwrt')
            old = disk / 'openwrt'
            write(old, {'sbin/init': '#!/bin/sh\n',
                        'etc/mu300/image-packages': 'base-files\nbusybox\ndnsmasq\nluci\nluci-theme-aurora\nsqm-scripts\n'})
            stage.mkdir(parents=True)
            subprocess.run(['tar', '-czf', str(stage / 'mu300-openwrt-rootfs.tar.gz'), '-C', str(img), '.'], check=True)
            r = self.sh(shell, f'. "{UPD}"; is_root() {{ false; }}; apply_one openwrt v2', MU300_LIB=1, MU300_DISK=disk,
                        MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0, r.stderr)
            new = disk / 'openwrt'
            self.assertEqual((new / 'etc/config/sqm').read_text(), 'image sqm\n')
            self.assertFalse((new / 'etc/mu300/orphaned-config/etc/config/sqm').exists())
            self.assertNotIn('sqm-scripts', (new / 'etc/mu300/user-packages/wanted').read_text())
            self.assertTrue((new / 'etc/mu300/orphaned-config/etc/config/openclash').exists())
            self.assertEqual((new / 'etc/config/dhcp').read_text(), 'old dhcp\n')     # the device's, not the image's

    def test_no_reinstall_flag(self):
        src = UPD.read_text()
        self.assertIn('--no-reinstall) MU300_KEEP_PACKAGES=0', src)


FAKE_UCI = r'''
import sys, os
db = os.path.join(os.environ['STUBLOG'], 'uci.db')
args = [a for a in sys.argv[1:] if a != '-q']
lines = open(db).read().splitlines() if os.path.exists(db) else []
kv = {}
order = []
for l in lines:
    k, _, v = l.partition('=')
    kv[k] = v; order.append(k)
def save():
    with open(db, 'w') as f:
        for k in order:
            if k in kv:
                f.write(f'{k}={kv[k]}\n')
with open(os.path.join(os.environ['STUBLOG'], 'uci.log'), 'a') as f:
    f.write(' '.join(args) + '\n')
cmd = args[0]
if cmd == 'show':
    for k in order:
        if k in kv and (k == args[1] or k.startswith(args[1] + '.')):
            v = kv[k]
            print(f'{k}={v}' if k.count('.') == 1 and ' ' not in v and not v.startswith('/') else f"{k}='{v}'")
elif cmd == 'get':
    if args[1] not in kv: sys.exit(1)
    print(kv[args[1]])
elif cmd == 'set':
    k, _, v = args[1].partition('=')
    if k not in kv: order.append(k)
    kv[k] = v; save()
elif cmd == 'delete':
    kv.pop(args[1], None); save()
elif cmd == 'del_list':
    k, _, v = args[1].partition('=')
    kv[k] = ' '.join(x for x in kv.get(k, '').split() if x != v)
    if not kv[k]: kv.pop(k)
    save()
elif cmd == 'commit':
    pass
'''


class Service(ShellTest):
    """mu300-user-packages on a fake root (MU300_SYSROOT): apk/apt-get, dpkg-query, uci, the init scripts stubbed."""

    def setUp(self):
        super().setUp()
        self.R = self.tmp / 'root'
        (self.tmp / 'fakeuci.py').write_text(FAKE_UCI)
        self.stub('uci', f'exec "{sys.executable}" "{self.tmp}/fakeuci.py" "$@"')
        self.stub('id', 'echo 0')
        self.stub('logger', ':')
        # apk: "update" works when $STUBLOG/online exists; "add" fails for a name in $STUBLOG/missing (the whole
        # transaction, as apk does); "info -e" says installed for a name in $STUBLOG/installed
        self.stub('apk', 'echo "apk $*" >> "$STUBLOG/pm.log"\n'
                         'case $1 in\n'
                         '  update) n=$(cat "$MU300_SYSROOT"/etc/apk/repositories "$MU300_SYSROOT"/etc/apk/repositories.d/*.list 2>/dev/null | grep -c .)\n'
                         '          [ -e "$STUBLOG/online" ] || { echo "ERROR: unable to fetch index"; echo "$n unavailable, 0 stale; 0 distinct packages available"; exit 1; }\n'
                         '          [ ! -e "$STUBLOG/deadfeed" ] || { echo " [https://example.invalid/packages.adb]"; echo "1 unavailable, 0 stale; 11141 distinct packages available"; exit 1; } ;;\n'
                         '  add) shift; for p; do grep -qx "$p" "$STUBLOG/missing" 2>/dev/null && { echo "ERROR: unable to select packages:"; echo "  $p (no such package)"; exit 1; }; done\n'
                         '       for p; do echo "$p" >> "$STUBLOG/installed"; done ;;\n'
                         '  info) grep -qx "$3" "$STUBLOG/installed" 2>/dev/null ;;\n'
                         'esac')
        self.stub('apt-get', 'echo "apt-get $*" >> "$STUBLOG/pm.log"\n'
                             # a source whose suite is gone (another Ubuntu release) fails the update while it is on
                             'case $1 in update) [ -e "$STUBLOG/online" ] || exit 100\n'
                             '    [ ! -e "$MU300_SYSROOT/etc/apt/sources.list.d/gone.list" ] || exit 100 ;;\n'
                             '  *) for p; do case $p in -*|install) continue ;; esac; grep -qx "$p" "$STUBLOG/missing" 2>/dev/null && { echo "E: Unable to locate package $p"; exit 100; }; done ;;\n'
                             'esac\nexit 0')
        self.stub('dpkg-query', 'exit 1')

    def setup_openwrt(self, server='127.0.0.1#7874', listening=False):
        shutil.rmtree(self.R, ignore_errors=True)
        for f in ('online', 'missing', 'installed', 'pm.log', 'uci.db', 'uci.log', 'init.log'):
            (self.tmp / f).unlink(missing_ok=True)
        d = 'etc/mu300/user-packages'
        write(self.R, {'etc/openwrt_release': 'DISTRIB_ID=OpenWrt\n',
                       f'{d}/wanted': 'luci-app-openclash\nluci-theme-argon\n', f'{d}/done': '', f'{d}/failed': '',
                       f'{d}/from': 'v1\n', f'{d}/state': 'pending\n', f'{d}/rc.d': 'S99openclash\nS19dnsmasq\n',
                       f'{d}/files': 'luci-app-openclash etc/config/openclash\nluci-app-openclash etc/init.d/openclash\n'
                                     'luci-app-openclash etc/init.d/openclash-watch\n'
                                     'luci-theme-argon etc/config/argon\n',
                       'etc/mu300/orphaned-config/etc/config/openclash': "config openclash 'config'\n\toption dns_port '7874'\n",
                       'etc/mu300/orphaned-config/etc/config/argon': 'argon\n',
                       'etc/config/dhcp': 'dhcp file\n',
                       'www/luci-static/aurora/main.css': '', 'www/luci-static/bootstrap/x.css': '',
                       'proc/net/udp': '  sl  local_address rem_address   st\n   0: 0100007F:0035 00000000:0000 07\n'
                                       + ('   1: 0100007F:1EC2 00000000:0000 07\n' if listening else ''),
                       'proc/net/udp6': '  sl  local_address\n'})
        # the package's init scripts appear when apk installs it (a stub: it logs what it is asked)
        (self.tmp / 'uci.db').write_text(
            'dhcp.cfg01=dnsmasq\n'
            f'dhcp.cfg01.server={server}\n'
            'dhcp.cfg01.noresolv=1\n'
            'luci.main=core\nluci.main.mediaurlbase=/luci-static/argon\n'
            'luci.themes=internal\nluci.themes.Aurora=/luci-static/aurora\nluci.themes.Argon=/luci-static/argon\n'
            'luci.themes.Bootstrap=/luci-static/bootstrap\n')

    def init_scripts(self):
        for n in ('openclash', 'openclash-watch', 'dnsmasq'):
            write(self.R, {f'etc/init.d/{n}': f'#!/bin/sh\necho "{n} $1" >> "$STUBLOG/init.log"\n'})
            (self.R / f'etc/init.d/{n}').chmod(0o755)

    def run_upk(self, shell, *args, **env):
        e = dict(MU300_SYSROOT=self.R, MU300_BIN=BIN, MU300_PKG_SETTLE=0, MU300_PKG_DELAY=0, MU300_PKG_TRIES=2,
                 MU300_DNS_CHECKS=2, MU300_DNS_INTERVAL=0,
                 MU300_RUN=self.tmp / 'run', MU300_MOTD_DIR=self.tmp / 'motd')
        e.update(env)
        return self.script(shell, UPK, *args, **e)

    def rec(self, name):
        p = self.R / 'etc/mu300/user-packages' / name
        return p.read_text() if p.exists() else None

    def uci(self):
        return (self.tmp / 'uci.db').read_text()

    def test_online_installs_again_and_puts_the_settings_back(self):
        for shell in self.each_shell():
            self.setup_openwrt(listening=True)
            self.init_scripts()
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-theme-argon\n')   # from a file: not in the feeds
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.rec('done'), 'luci-app-openclash\n')
            self.assertIn('luci-theme-argon ERROR: unable to select packages', self.rec('failed'))
            self.assertEqual(self.rec('state'), 'failed\n')
            # its settings back, its services as they were: enabled (and restarted with them) or left disabled
            self.assertEqual((self.R / 'etc/config/openclash').read_text(), "config openclash 'config'\n\toption dns_port '7874'\n")
            self.assertFalse((self.R / 'etc/mu300/orphaned-config/etc/config/openclash').exists())
            init = (self.tmp / 'init.log').read_text()
            self.assertIn('openclash enable\nopenclash restart\n', init)
            self.assertIn('openclash-watch disable\n', init)
            # the failed one's stay aside
            self.assertEqual((self.R / 'etc/mu300/orphaned-config/etc/config/argon').read_text(), 'argon\n')
            # a forwarder something listens on stays
            self.assertIn('dhcp.cfg01.server=127.0.0.1#7874', self.uci())
            # Argon is gone: off the list, and the default is a theme that is there
            self.assertNotIn('Argon', self.uci())
            self.assertIn('luci.main.mediaurlbase=/luci-static/aurora', self.uci())
            self.assertIn('1 of 2 packages back, 1 could not be installed again', self.rec('note'))
            # all at once first, then one by one
            self.assertIn('apk add luci-app-openclash luci-theme-argon\napk add luci-app-openclash\napk add luci-theme-argon\n',
                          (self.tmp / 'pm.log').read_text())
            s = self.run_upk(shell, 'status').stdout
            self.assertIn('mv /etc/mu300/orphaned-config/etc/config/argon /etc/config/argon', s)
            self.assertIn('apk add --allow-untrusted', s)
            # a second boot: nothing left to do, no package manager
            (self.tmp / 'pm.log').unlink()
            self.assertEqual(self.run_upk(shell, 'run').returncode, 0)
            self.assertFalse((self.tmp / 'pm.log').exists())

    # the DNS left by a proxy (dns_check): never weakened by itself, only a provably orphaned forwarder is touched
    def dhcp(self):
        return '\n'.join(l for l in self.uci().splitlines() if l.startswith('dhcp.'))

    def test_dns_forwarder_of_a_package_gone_for_good_goes_when_another_server_stays(self):
        for shell in self.each_shell():
            self.setup_openwrt(server='127.0.0.1#7874 8.8.8.8')
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-app-openclash\n')     # not in the feeds: failed for good
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            u = self.uci()
            self.assertIn('dhcp.cfg01.server=8.8.8.8\n', u)
            self.assertIn('dhcp.cfg01.noresolv=1', u)         # never touched
            self.assertTrue((self.R / 'etc/mu300/orphaned-config/etc/config/dhcp.before-dns-repair').exists())
            self.assertIn('DNS: removed the forwarder 127.0.0.1#7874 of luci-app-openclash', self.rec('log'))

    def test_dns_only_forwarder_is_reported_and_removed_on_request_only(self):
        for shell in self.each_shell():
            self.setup_openwrt()
            for _ in range(6):                                # offline at five boots: given up
                self.run_upk(shell, 'run')
            self.assertEqual(self.rec('state'), 'gave-up\n')
            before = self.dhcp()
            self.assertIn('dhcp.cfg01.server=127.0.0.1#7874', before)
            self.assertIn('dhcp.cfg01.noresolv=1', before)
            self.assertIn('mu300-user-packages repair-dns', self.rec('note'))
            self.assertIn('repair-dns', self.run_upk(shell, 'status').stdout)
            # the user's own decision: the forwarder goes, and then the connection's resolvers
            r = self.run_upk(shell, 'repair-dns')
            self.assertEqual(r.returncode, 0, r.stderr)
            u = self.dhcp()
            self.assertNotIn('dhcp.cfg01.server', u)
            self.assertIn('dhcp.cfg01.noresolv=0', u)
            self.assertNotIn('repair-dns', self.rec('note') or '')

    def test_dns_proxy_not_listening_yet_while_its_package_may_come_back(self):
        # offline: the package is still to be installed again - nothing is touched, not even with another server
        for shell in self.each_shell():
            self.setup_openwrt(server='127.0.0.1#7874 8.8.8.8')
            before = self.dhcp()
            self.run_upk(shell, 'run')
            self.assertEqual(self.rec('state'), 'offline\n')
            self.assertEqual(self.dhcp(), before)

    def test_dns_untouched_with_the_vpn_on(self):
        for shell in self.each_shell():
            self.setup_openwrt(server='127.0.0.1#7874 8.8.8.8')
            write(self.R, {'etc/mu300/vpn.conf': 'ENABLE=1\nSECRET=do-not-print\n'})
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-app-openclash\n')
            before = self.dhcp()
            r = self.run_upk(shell, 'run')
            self.assertEqual(self.dhcp(), before)
            self.assertIn('the VPN is on', self.rec('note'))
            self.assertNotIn('do-not-print', r.stdout + r.stderr + self.rec('log') + self.run_upk(shell, 'status').stdout)
            self.run_upk(shell, 'repair-dns')
            self.assertEqual(self.dhcp(), before)

    def test_dns_unknown_owner_or_the_projects_port_untouched(self):
        for shell in self.each_shell():
            # a port no set-aside settings name (a user's own resolver, say): not attributable
            self.setup_openwrt(server='127.0.0.1#5353 8.8.8.8')
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-app-openclash\n')
            before = self.dhcp()
            self.run_upk(shell, 'run')
            self.run_upk(shell, 'repair-dns')
            self.assertEqual(self.dhcp(), before)
            # a port the project's own settings name
            self.setup_openwrt(server='127.0.0.1#7874 8.8.8.8')
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-app-openclash\n')
            write(self.R, {'etc/mu300/vpn/settings': 'DNS_PORT=7874\n'})
            before = self.dhcp()
            self.run_upk(shell, 'run')
            self.assertEqual(self.dhcp(), before)

    def test_dns_port_that_comes_up_during_the_window_is_left(self):
        for shell in self.each_shell():
            self.setup_openwrt(server='127.0.0.1#7874 8.8.8.8')
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('luci-app-openclash\n')
            # the proxy binds its port while the service looks again (a wait of its own: busybox sh's sleep is a
            # builtin, a stub named sleep never runs there)
            self.stub('dnswait', f'printf "   1: 0100007F:1EC2 00000000:0000 07\\n" >> "{self.R}/proc/net/udp"')
            before = self.dhcp()
            r = self.run_upk(shell, 'run', MU300_DNS_CHECKS=3, MU300_DNS_WAIT=self.stubs / 'dnswait')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.dhcp(), before)
            self.assertIn('0100007F:1EC2', (self.R / 'proc/net/udp').read_text())   # the wait did run

    def test_a_dead_feed_of_the_users_is_not_offline(self):
        """apk update fails for one feed that does not answer (a U30 Air: a custom feed of example.invalid); the
        others answered, so the packages are installed again. With every feed unavailable it is offline."""
        for shell in self.each_shell():
            self.setup_openwrt(listening=True)
            write(self.R, {'etc/apk/repositories.d/distfeeds.list': 'https://downloads.openwrt.org/a/packages.adb\n'
                                                                    'https://downloads.openwrt.org/b/packages.adb\n',
                           'etc/apk/repositories.d/customfeeds.list': '# mine\nhttps://example.invalid/packages.adb\n'})
            (self.tmp / 'deadfeed').write_text('')
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.rec('state'), 'offline\n')
            (self.tmp / 'online').write_text('')
            r = self.run_upk(shell, 'retry')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.rec('state'), 'done\n')
            (self.tmp / 'deadfeed').unlink()

    def test_offline_tries_again_at_the_next_boots_then_gives_up(self):
        for shell in self.each_shell():
            self.setup_openwrt(listening=True)
            for boot in range(1, 6):
                r = self.run_upk(shell, 'run')
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                self.assertEqual(self.rec('state'), 'offline\n')
                self.assertEqual(self.rec('boots'), f'{boot}\n')
            self.assertEqual((self.tmp / 'pm.log').read_text().count('apk update'), 10)   # 2 tries a boot
            self.assertIn('wait for the internet', self.rec('note'))
            self.run_upk(shell, 'run')
            self.assertEqual(self.rec('state'), 'gave-up\n')
            self.run_upk(shell, 'run')
            self.assertEqual((self.tmp / 'pm.log').read_text().count('apk update'), 10)
            # retry: now, online
            (self.tmp / 'online').write_text('')
            r = self.run_upk(shell, 'retry')
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.rec('state'), 'done\n')
            self.assertIsNone(self.rec('note'))
            self.assertIn('2 of 2 packages', r.stdout)

    def test_skip(self):
        for shell in self.each_shell():
            self.setup_openwrt()
            (self.tmp / 'online').write_text('')
            r = self.run_upk(shell, 'skip')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.run_upk(shell, 'run')
            pm = (self.tmp / 'pm.log').read_text() if (self.tmp / 'pm.log').exists() else ''
            self.assertNotIn('apk add', pm)
            self.assertNotIn('apk update', pm)
            self.assertEqual(self.rec('state'), 'skipped\n')
            # the forwarder is the only server: reported, never replaced by the connection's resolvers by itself
            self.assertIn('dhcp.cfg01.server=127.0.0.1#7874', self.uci())
            self.assertIn('dhcp.cfg01.noresolv=1', self.uci())
            self.assertIn('repair-dns', self.rec('note'))
            self.assertTrue((self.R / 'etc/mu300/orphaned-config/etc/config/openclash').exists())
            self.assertIn('skipped', self.rec('note'))
            self.run_upk(shell, 'retry')
            self.assertEqual(self.rec('state'), 'done\n')
            self.assertEqual(self.run_upk(shell, 'dismiss').returncode, 0)

    def test_retry_with_everything_back_is_done(self):
        # (device test, U30) retry after all were back left "pending" for good: "0 packages are installed again at the
        # next boot", the panel "Not installed yet"
        for shell in self.each_shell():
            self.setup_openwrt(listening=True)
            write(self.R, {'etc/mu300/user-packages/done': 'luci-app-openclash\nluci-theme-argon\n',
                           'etc/mu300/user-packages/state': 'done\n'})
            r = self.run_upk(shell, 'retry')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.rec('state'), 'done\n')
            self.assertIn('2 of 2 packages', self.run_upk(shell, 'brief').stdout)
            self.assertFalse((self.tmp / 'pm.log').exists())
            # a record an older retry left pending: the next run ends it
            write(self.R, {'etc/mu300/user-packages/state': 'pending\n'})
            self.run_upk(shell, 'run')
            self.assertEqual(self.rec('state'), 'done\n')

    def test_installed_by_hand_meanwhile(self):
        for shell in self.each_shell():
            self.setup_openwrt(listening=True)
            (self.tmp / 'installed').write_text('luci-app-openclash\nluci-theme-argon\n')
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.rec('state'), 'done\n')
            self.assertNotIn('apk update', (self.tmp / 'pm.log').read_text())
            self.assertEqual((self.R / 'etc/config/openclash').read_text(), "config openclash 'config'\n\toption dns_port '7874'\n")

    def test_one_run_at_a_time(self):
        for shell in self.each_shell():
            self.setup_openwrt()
            (self.tmp / 'run/mu300-user-packages.lock').mkdir(parents=True, exist_ok=True)
            r = self.run_upk(shell, 'run')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('running already', r.stderr)
            (self.tmp / 'run/mu300-user-packages.lock').rmdir()

    def test_nothing_recorded_does_nothing(self):
        for shell in self.each_shell():
            shutil.rmtree(self.R, ignore_errors=True)
            write(self.R, {'etc/openwrt_release': ''})
            r = self.run_upk(shell, 'run')
            self.assertEqual((r.returncode, r.stdout), (0, ''), r.stderr)
            self.assertIn('No packages', self.run_upk(shell, 'status').stdout)

    def test_ubuntu(self):
        for shell in self.each_shell():
            shutil.rmtree(self.R, ignore_errors=True)
            for f in ('online', 'missing', 'pm.log'):
                (self.tmp / f).unlink(missing_ok=True)
            shutil.rmtree(self.tmp / 'motd', ignore_errors=True)
            d = 'etc/mu300/user-packages'
            write(self.R, {f'{d}/wanted': 'htop\nppa-thing\n', f'{d}/done': '', f'{d}/failed': '', f'{d}/files': '',
                           f'{d}/rc.d': '', f'{d}/state': 'pending\n'})
            (self.tmp / 'online').write_text('')
            (self.tmp / 'missing').write_text('ppa-thing\n')
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.rec('done'), 'htop\n')
            self.assertIn('ppa-thing E: Unable to locate package ppa-thing', self.rec('failed'))
            # offline must fail the update: apt-get returns 0 without --error-on=any
            self.assertIn('apt-get update -q --error-on=any -o Acquire::http::Timeout=10', (self.tmp / 'pm.log').read_text())
            self.assertIn('apt-get install -y -q -o Dpkg::Options::=--force-confold htop ppa-thing',
                          (self.tmp / 'pm.log').read_text())
            self.assertIn('could not be installed again', (self.tmp / 'motd/61-mu300-packages').read_text())
            self.run_upk(shell, 'dismiss')
            self.assertFalse((self.tmp / 'motd/61-mu300-packages').exists())

    def ubuntu_record(self, wanted='htop\n', units='', sources=''):
        shutil.rmtree(self.R, ignore_errors=True)
        for f in ('online', 'missing', 'pm.log', 'systemctl.log'):
            (self.tmp / f).unlink(missing_ok=True)
        d = 'etc/mu300/user-packages'
        write(self.R, {f'{d}/wanted': wanted, f'{d}/done': '', f'{d}/failed': '', f'{d}/files': '', f'{d}/rc.d': '',
                       f'{d}/state': 'pending\n', f'{d}/units': units, f'{d}/apt-sources': sources})

    def test_ubuntu_units_as_they_were(self):
        # systemctl: logs; is-enabled says enabled (apt enabled every unit it installed)
        self.stub('systemctl', 'echo "$*" >> "$STUBLOG/systemctl.log"; [ "$1" = is-enabled ] && echo enabled; exit 0')
        for shell in self.each_shell():
            self.ubuntu_record('htop\nsmartmontools\n', 'smartmontools smartd.service disabled\n'
                               'smartmontools smartd-check.timer masked\nhtop htopd.service enabled\n'
                               'other x.service disabled\n')
            (self.tmp / 'online').write_text('')
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            log = (self.tmp / 'systemctl.log').read_text()
            self.assertIn('enable htopd.service\n', log)
            self.assertIn('disable smartd.service\nstop smartd.service\n', log)
            self.assertIn('mask smartd-check.timer\nstop smartd-check.timer\n', log)
            self.assertNotIn('x.service', log)
            self.assertIn('smartd.service left disabled, as it was', self.rec('log'))

    def test_ubuntu_kept_source_that_fails_is_disabled(self):
        for shell in self.each_shell():
            self.ubuntu_record(sources='etc/apt/sources.list.d/gone.list\netc/apt/sources.list.d/ok.list\n')
            write(self.R, {'etc/mu300/user-packages/apt-prefs': 'etc/apt/preferences.d/gone-pin\n',
                           'etc/apt/preferences.d/gone-pin': 'Package: *\n',
                           'etc/apt/sources.list.d/gone.list': 'deb x noble main\n',
                           'etc/apt/sources.list.d/ok.list': 'deb y resolute main\n'})
            (self.tmp / 'online').write_text('')
            r = self.run_upk(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            src = self.R / 'etc/apt/sources.list.d'
            self.assertFalse((src / 'gone.list').exists())
            self.assertTrue((src / 'gone.list.disabled').exists())
            self.assertTrue((src / 'ok.list').exists())
            self.assertEqual(self.rec('state'), 'done\n')
            self.assertIn('/etc/apt/sources.list.d/gone.list was disabled', self.rec('note'))
            st = self.run_upk(shell, 'status').stdout
            self.assertIn('gone.list was disabled', st)
            # its pin stays (harmless without the source), and status says so
            self.assertTrue((self.R / 'etc/apt/preferences.d/gone-pin').exists())
            self.assertIn('/etc/apt/preferences.d/gone-pin', st)
            self.assertIn('a pin of a source that was turned off does nothing', st)
            # offline: nothing is disabled, they all stay as they were
            self.ubuntu_record(sources='etc/apt/sources.list.d/gone.list\n')
            write(self.R, {'etc/apt/sources.list.d/gone.list': 'deb x noble main\n'})
            self.run_upk(shell, 'run')
            self.assertEqual(self.rec('state'), 'offline\n')
            self.assertTrue((src / 'gone.list').exists())
            self.assertFalse((src / 'gone.list.disabled').exists())


class Wiring(ShellTest):
    def test_the_images_list_their_packages_and_run_the_service(self):
        b = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertRegex(b, r'for s in [^\n]*\bmu300-user-packages\b[^\n]*; do')   # in the service loop, whatever else is there
        self.assertIn('upk_manual $R openwrt) > $R/etc/mu300/image-packages', b)
        # no stray config file in the image itself (an update would add it to every device): removed from $R, checked
        self.assertIn('rm -f $R/etc/config/attendedsysupgrade $R/etc/config/*.apk-new', b)
        self.assertLess(b.index('rm -f $R/etc/config/attendedsysupgrade'), b.index('cd $R && tar -czf'))
        a = (TOP / 'rootfs/assemble.sh').read_text()
        self.assertIn('mu300-user-packages.service:multi-user.target', a)
        self.assertIn('upk_manual $R ubuntu) > $R/etc/mu300/image-packages', a)
        self.assertTrue((TOP / 'rootfs/overlay/etc/systemd/system/mu300-user-packages.service').exists())
        i = TOP / 'openwrt/overlay/etc/init.d/mu300-user-packages'
        self.assertTrue(i.stat().st_mode & 0o111)
        self.assertIn('START=99', i.read_text())
        self.assertIn('/etc/mu300/user-packages/note', (TOP / 'openwrt/overlay/etc/profile.d/mu300-update.sh').read_text())

    def test_build_shell_can_source_the_updater(self):
        # the OpenWrt build sources mu300-update under sh -eu, assemble.sh under bash -e
        r = self.tmp / 'r'
        write(r, {'etc/apk/world': 'a\nb=1\nc><Q1x=\n'})
        for shell in self.each_shell():
            out = self.sh(shell, f'set -eu; (MU300_LIB=1; . "{UPD}"; upk_manual "{r}" openwrt)')
            self.assertEqual((out.returncode, out.stdout), (0, 'a\nb\nc\n'), out.stderr)
