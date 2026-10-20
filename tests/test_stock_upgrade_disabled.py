#!/usr/bin/env python3
"""Stock flashing policy; no device writes, downloads or image builds."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP = next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir() and (p/'rootfs').is_dir())
E5 = (TOP/'openwrt/luci-app-t551m').exists()
PLUGIN = TOP/'openwrt'/('luci-app-t551m' if E5 else 'luci-app-mu300')
POLICY = PLUGIN/'root/usr/libexec/unisoc-modem/disable-stock-upgrade'
GUARD = TOP/'openwrt/overlay/usr/libexec'/('e5-sysupgrade' if E5 else 'mu300-sysupgrade')
IMAGE = 'mu300-openwrt-base:25.12.5'


class Guard(unittest.TestCase):
    def test_backup_restore_and_list_remain_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); old = root/'original'; receipt = root/'called'
            old.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RECEIPT"\n'); old.chmod(0o755)
            script = root/'guard'; script.write_text(GUARD.read_text().replace('/sbin/sysupgrade.openwrt', str(old)))
            for args in [['-b','backup.tgz'], ['--restore-backup','backup.tgz'], ['-r','backup.tgz'],
                         ['-k','-b','-'], ['--list-backup'], ['-v','-l']]:
                with self.subTest(args=args):
                    subprocess.run(['sh',str(script),*args],env=dict(os.environ,RECEIPT=str(receipt)),check=True)
                    self.assertEqual(receipt.read_text().splitlines(),args)

    def test_images_and_mixed_modes_never_reach_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); old = root/'original'; receipt = root/'called'
            old.write_text('#!/bin/sh\ntouch "$RECEIPT"\n'); old.chmod(0o755)
            script = root/'guard'; script.write_text(GUARD.read_text().replace('/sbin/sysupgrade.openwrt', str(old)))
            for args in [[],['firmware.bin'],['-n','firmware.bin'],['--force','firmware.bin'],
                         ['--test','firmware.bin'],['-b'],['-l','firmware.bin'],
                         ['-b','backup.tgz','--force','firmware.bin'],['-b','one','-r','two']]:
                with self.subTest(args=args):
                    r=subprocess.run(['sh',str(script),*args],env=dict(os.environ,RECEIPT=str(receipt)),capture_output=True)
                    self.assertNotEqual(r.returncode,0); self.assertFalse(receipt.exists())


class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('docker') or subprocess.run(['docker','image','inspect',IMAGE],capture_output=True).returncode:
            raise unittest.SkipTest('existing OpenWrt ucode image required; never download/build during tests')

    def fixture(self, root):
        files = {
            'www/luci-static/resources/view/system/flash.js': """return {render(){var m,s,o,ss;
m=new form.JSONMap({},_('Flash operations'));
o=s.option(form.Button,'restore',_('Restore backup'));
o=s.option(form.SectionValue,'actions',form.NamedSection,'actions','actions',_('Flash new firmware image'),'description');
ss=o.subsection;o=ss.option(form.Button,'sysupgrade',_('Image'));
s=m.section(form.NamedSection,'config','config',_('Configuration'));return m.render();}};""",
            'usr/share/luci/menu.d/system.json': json.dumps({
                'admin/system/flash':{'title':'Backup / Flash Firmware','action':{'path':'system/flash'}},
                'admin/system/reboot':{'title':'Reboot'},
                'admin/system/attendedsysupgrade':{'action':{'path':'system/attendedsysupgrade'}},
                'admin/system/ota':{'action':{'path':'unisoc/ota'}},
                'admin/system/upgrade':{'action':{'path':'unisoc/ota'}}}),
            'usr/share/rpcd/acl.d/system.json': json.dumps({
                'luci-mod-system-flash':{
                    'read':{'file':{'/sbin/sysupgrade --list-backup':['exec'],'/etc/sysupgrade.conf':['read']}},
                    'write':{'file':{'/sbin/sysupgrade --restore-backup /tmp/backup.tar.gz':['exec'],
                        '/sbin/firstboot -r -y':['exec'],'/sbin/reboot':['exec'],
                        '/sbin/sysupgrade --force /tmp/firmware.bin':['exec'],'/tmp/firmware.bin':['write']},
                        'ubus':{'system':['validate_firmware_image']}}},
                'luci-app-attendedsysupgrade':{}, 'my-ota':{'write':{'file':{'/usr/bin/ota':['exec']}}}})}
        for name, text in files.items():
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

    def run_policy(self, root):
        return subprocess.run(['docker','run','--rm','--pull=never','--network','none','--platform','linux/arm64',
             '-v',str(POLICY)+':/policy:ro','-v',str(root)+':/target',IMAGE,'ucode','/policy','/target'],capture_output=True,text=True)

    def test_backup_controls_and_custom_ota_survive_and_second_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root);r=self.run_policy(root);self.assertEqual(r.returncode,0,r.stderr)
            view=root/'www/luci-static/resources/view/system/flash.js'
            self.assertNotIn('Flash new firmware image',view.read_text())
            self.assertIn('Restore backup',view.read_text());self.assertIn("'Configuration'",view.read_text())
            if shutil.which('node'):
                subprocess.run(['node','-e',"new Function(require('fs').readFileSync(process.argv[1],'utf8'))",str(view)],check=True)
            menu=json.loads((root/'usr/share/luci/menu.d/system.json').read_text())
            self.assertEqual(menu['admin/system/flash']['title'],'Backup')
            self.assertIn('admin/system/reboot',menu);self.assertIn('admin/system/ota',menu)
            self.assertNotIn('admin/system/attendedsysupgrade',menu)
            self.assertIn('admin/system/upgrade',menu)
            acl=json.loads((root/'usr/share/rpcd/acl.d/system.json').read_text())
            self.assertIn('my-ota',acl);self.assertNotIn('luci-app-attendedsysupgrade',acl)
            write=acl['luci-mod-system-flash']['write']['file']
            self.assertIn('/sbin/sysupgrade --restore-backup /tmp/backup.tar.gz',write)
            self.assertIn('/sbin/firstboot -r -y',write);self.assertIn('/sbin/reboot',write)
            self.assertNotIn('/tmp/firmware.bin',write)
            self.assertNotIn('/sbin/sysupgrade --force /tmp/firmware.bin',write)
            before={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            r=self.run_policy(root);self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(before,{str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})

    def test_unknown_luci_layout_fails_without_changing_menu_or_acl(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root)
            (root/'www/luci-static/resources/view/system/flash.js').write_text('new upstream view')
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            r=self.run_policy(root);self.assertNotEqual(r.returncode,0)
            self.assertEqual(before,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
