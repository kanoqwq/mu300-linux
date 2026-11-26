"""Real ordinary updater delivery without resetting radio, routes or user data."""
import io
import shutil
import tarfile
import unittest
from helpers import TOP
from test_update import Update


class Delivery(Update):
    def test_first_and_followup_preserve_single_rf_and_button_settings(self):
        names=('opt/mu300/bin/mu300-v50-sim','opt/mu300/bin/mu300-v50-card-pair',
               'opt/mu300/bin/mu300-data-sim','opt/mu300/bin/mu300-sim-radio','opt/mu300/lib/v50-card.sh')
        for shell in self.each_shell():
            old,_=self.vendor_update_fixture()
            saved={'etc/config/unisoc_modem':b"config modem 'main'\n option v50_card '0'\n option v50_button_double 'data-switch'\n",
                   'etc/mu300/sim-radio':b'1 0\n','root/my-plugin/config':b'USER DATA'}
            for name,raw in saved.items():
                path=old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            for iteration in (0,1):
                content={name:(TOP/'rootfs/overlay'/name).read_bytes()+b'\n'*iteration for name in names}
                for name in ('mu300/common.js','mu300/roaming.js','view/mu300/home.js','view/mu300/sim.js','view/mu300/traffic.js'):
                    content['www/luci-static/resources/'+name]=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources'/name).read_bytes()+b'\n'*iteration
                content['usr/libexec/rpcd/mu300dash']=(TOP/'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash').read_bytes()+b'\n'*iteration
                content['usr/libexec/unisoc-modem/buttons.py']=(TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/buttons.py').read_bytes()+b'\n'*iteration
                content['sbin/init']=b'#!/bin/sh\n'
                with tarfile.open(self.disk/'.mu300-update/mu300-openwrt-rootfs.tar.gz','w:gz') as archive:
                    for name,raw in content.items():
                        entry=tarfile.TarInfo(name);entry.size=len(raw);entry.mode=0o755
                        archive.addfile(entry,io.BytesIO(raw))
                result=self.up(shell,'sync() { :; }; is_root() { false; }; apply_one openwrt v50-new')
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                for name,raw in content.items():self.assertEqual((old/name).read_bytes(),raw)
                for name,raw in saved.items():self.assertEqual((old/name).read_bytes(),raw)
                shutil.rmtree(self.disk/'openwrt.old')
            shutil.rmtree(old)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([Delivery('test_first_and_followup_preserve_single_rf_and_button_settings')])

if __name__=='__main__':unittest.main(defaultTest='Delivery.test_first_and_followup_preserve_single_rf_and_button_settings')
