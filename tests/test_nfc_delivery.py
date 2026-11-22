"""Real shared diff generator/applier, private roots and preserved user data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOP/'installer'))
sys.path.insert(0,str(TOP/'installer/tests'))
from test_ota import mu_release
from backend.ota_package import analyze
from backend.ota_engine import Engine,Patch

@unittest.skipUnless(os.name=='posix','actual filesystem update and rollback')
class Delivery(unittest.TestCase):
    def test_first_and_subsequent_update_and_rollback_preserve_settings(self):
        encoder='usr/share/ucode/mu300/nfc-wifi.uc'
        owner='opt/mu300/bin/mu300-nfc'
        current={encoder:(TOP/'rootfs/overlay'/encoder).read_bytes(),
                 owner:(TOP/'rootfs/overlay'/owner).read_bytes()}
        previous={owner:b'previous NFC owner\n'}
        engine=TOP/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        for profile in ('mu300','u30pro','f50pro'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);root=base/'device';root.mkdir()
                versions=('nfc-r1','nfc-r2','nfc-r3')
                for index,files in enumerate((previous,current,{**current,encoder:current[encoder]+b'\n// next release\n'})):
                    package=base/str(index);mu_release(package,versions[index],files)
                    meta=package/'desktop/release.json';data=json.loads(meta.read_text())
                    data['profile']=profile;meta.write_text(json.dumps(data))
                for path,data in previous.items():
                    p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o644)
                marker=root/'etc/mu300/release.json';marker.parent.mkdir(parents=True)
                marker.write_text(json.dumps(dict(profile=profile,image_version=versions[0],kernel_release='7.2.8')))
                users={'etc/config/wireless':b'user SSID/key/encryption',
                       'etc/config/u30pro_native_control':b'NFC enabled=0',
                       'var/user/nfc-notes':b'private user data'}
                for path,data in users.items():
                    p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                updater=Engine(root)
                for old,new in ((0,1),(1,2)):
                    output=base/f'{old}-{new}.ota'
                    result=analyze(base/str(old),base/str(new),output)
                    self.assertTrue(result['can_export'],result.get('blockers'))
                    with Patch(output) as patch:
                        self.assertTrue(updater.inspect(patch)['applicable'])
                        self.assertEqual(updater.apply(patch)['status'],'complete')
                    expected=current[encoder]+(b'\n// next release\n' if new==2 else b'')
                    self.assertEqual((root/encoder).read_bytes(),expected)
                    for path,data in users.items():self.assertEqual((root/path).read_bytes(),data)
                    if new==1:
                        undo=subprocess.run([sys.executable,str(engine),'rollback','--root',str(root)],capture_output=True,text=True)
                        self.assertEqual(undo.returncode,0,undo.stdout+undo.stderr)
                        self.assertFalse((root/encoder).exists())
                        self.assertEqual((root/owner).read_bytes(),previous[owner])
                        for path,data in users.items():self.assertEqual((root/path).read_bytes(),data)
                        with Patch(output) as patch:updater.apply(patch)
                rollback=subprocess.run([sys.executable,str(engine),'rollback','--root',str(root)],capture_output=True,text=True)
                self.assertEqual(rollback.returncode,0,rollback.stdout+rollback.stderr)
                self.assertEqual((root/encoder).read_bytes(),current[encoder])
                self.assertEqual(updater.identity()['version'],versions[1])
                for path,data in users.items():self.assertEqual((root/path).read_bytes(),data)
