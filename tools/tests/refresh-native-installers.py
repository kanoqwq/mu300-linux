"""Native refresh replaces both hosts, drops only obsolete owned files, preserves firmware."""
import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest
import zipfile

TOP=Path(__file__).resolve().parents[2]
native=runpy.run_path(str(TOP/'tools/refresh-native-installers.py'))


def sha(raw):return hashlib.sha256(raw).hexdigest()


def tar(files):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as z:
        for n,v in files.items():
            info=tarfile.TarInfo(n);info.size=len(v);z.addfile(info,io.BytesIO(v))
    return out.getvalue()


def studio(root):
    groups={
        'windows-x64':{'OpenWrt-Installer.exe':b'new Windows GUI',
            'tools/backend/openwrt-backend.exe':b'new Windows backend','tools/backend/new.dll':b'new dependency'},
        'macos-arm64':{'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer':b'new Mac GUI',
            'tools/macos/backend/openwrt-backend':b'new Mac backend'},
    }
    diff={'OpenWrt-Diff-Studio.exe':groups['windows-x64']['OpenWrt-Installer.exe'],
          'OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer':groups['macos-arm64']['OpenWrt-Installer.app/Contents/MacOS/openwrt-installer'],
          'OpenWrt-Diff-Studio.py':b'new diff entry','backend/ota_engine.py':b'new support',
          'tools/backend/openwrt-backend.exe':groups['windows-x64']['tools/backend/openwrt-backend.exe'],
          'tools/macos/backend/openwrt-backend':groups['macos-arm64']['tools/macos/backend/openwrt-backend']}
    files={**groups['windows-x64'],**groups['macos-arm64'],**diff}
    for label,platform in [('docs/GUI-BUILD.json','windows-x64'),('docs/GUI-BUILD-macos.json','macos-arm64')]:
        files[label]=json.dumps({'protocol':1,'platform':platform,'source':{'revision':'new'},
            'files':{n:sha(v) for n,v in groups[platform].items()}}).encode()
    files['diff-tool.json']=json.dumps({'protocol':1,'source':{'revision':'new'},'files':{n:sha(v) for n,v in diff.items()}}).encode()
    files['OpenWrt-Tools.apk']=b'new signed ARM64 APK fixture'
    files['docs/GUI-BUILD-android.json']=json.dumps({'protocol':1,'platform':'android-aarch64','debug':False,
        'source':{'revision':'new'},'signing':{'alias':'fixture'},'files':{'OpenWrt-Tools.apk':sha(files['OpenWrt-Tools.apk'])}}).encode()
    for n in ('START-HERE.md','docs/GUI-GUIDE.md','docs/INSTALLER-PROTOCOL.md','docs/GUI-BUILD.md','docs/GUI-VERIFICATION.md','docs/DIFF-STUDIO.md','docs/ANDROID-INSTALLER.md'):files[n]=b'new guide'
    for n,v in files.items():
        p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
    return files


class RefreshNative(unittest.TestCase):
    def test_actual_archive_refresh_and_checksums_without_firmware_or_duplicate_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);fresh=studio(root/'studio');old=root/'old.zip';new=root/'new.zip'
            files={n:v.replace(b'new ',b'old ') for n,v in fresh.items()}
            obsolete='tools/backend/obsolete.dll';files[obsolete]=b'old dependency'
            metadata=json.loads(files['docs/GUI-BUILD.json'])
            metadata['files'][obsolete]=sha(files[obsolete])
            files['docs/GUI-BUILD.json']=json.dumps(metadata).encode()
            files['tools/keep-unowned.txt']=b'not owned by a GUI inventory'
            mu=(TOP/'desktop/installer.py').is_file()
            if mu:
                payload={'payload/boot-linux.img':b'unchanged boot','payload/mu300-openwrt.tar.gz':b'unchanged root'}
                files.update(payload)
                files['desktop/manifest.json']=json.dumps({'format':2,'kernel':'7.2',
                    'magisk_layout':{'boot-linux.img':'payload/boot-linux.img','mu300-openwrt.tar.gz':'payload/mu300-openwrt.tar.gz'},
                    'magisk':{n:sha(v) for n,v in payload.items()},
                    'desktop':{n:sha(v) for n,v in files.items() if n not in payload}}).encode()
            else:
                payload={'files/boot.img':b'unchanged boot','files/openwrt.ext4.gz':b'unchanged root','files/ext4-tools.tar':tar({'lib/base.so':b'ABI'})}
                files.update(payload)
                files['files/SHA256SUMS']=''.join(sha(v)+'  '+n+'\n' for n,v in files.items()).encode()
            with zipfile.ZipFile(old,'w') as z:
                for n,v in files.items():z.writestr(n,v)
            revision={'source_commit':'a'*40,'source_fingerprint':'b'*64}
            if mu:
                runpy.run_path(str(TOP/'tools/repack-with-diff.py'))['repack'](old,new,root/'studio',revision,refresh_native=True)
            else:
                tools=root/'tools.tar';tools.write_bytes(tar({'lib/base.so':b'ABI','usr/bin/flock':b'lock'}))
                runpy.run_path(str(TOP/'tools/refresh-release-installers.py'))['refresh'](old,new,tools,revision,native=root/'studio')
            with zipfile.ZipFile(new) as z:
                self.assertIsNone(z.testzip());self.assertEqual(len(z.namelist()),len(set(z.namelist())))
                self.assertNotIn(obsolete,z.namelist());self.assertEqual(z.read('tools/keep-unowned.txt'),files['tools/keep-unowned.txt'])
                for n,v in payload.items():self.assertEqual(z.read(n),v)
                for n in ('OpenWrt-Installer.exe','tools/backend/openwrt-backend.exe','tools/macos/backend/openwrt-backend','OpenWrt-Diff-Studio.exe','OpenWrt-Tools.apk'):
                    self.assertEqual(z.read(n),fresh[n])
                if mu:sums=json.loads(z.read('desktop/manifest.json'))['desktop']
                else:sums={n:h for line in z.read('files/SHA256SUMS').decode().splitlines() for h,n in [line.split(None,1)]}
                self.assertNotIn(obsolete,sums)
                for n,h in sums.items():self.assertEqual(sha(z.read(n)),h,n)
                for name in ('docs/GUI-BUILD.json','docs/GUI-BUILD-macos.json','diff-tool.json'):
                    for n,h in json.loads(z.read(name))['files'].items():self.assertEqual(sha(z.read(n)),h)
                self.assertTrue(z.getinfo('tools/backend/new.dll').external_attr>>16&0o444)

    def test_inventory_cannot_replace_firmware(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);files=studio(root/'studio');old=root/'old.zip'
            info=json.loads(files['docs/GUI-BUILD.json']);info['files']['files/boot.img']='0'*64
            files['docs/GUI-BUILD.json']=json.dumps(info).encode()
            with zipfile.ZipFile(old,'w') as z:
                for n,v in files.items():z.writestr(n,v)
            with zipfile.ZipFile(old) as z,self.assertRaisesRegex(ValueError,'non-installer'):
                native['replacements'](z,root/'studio')


if __name__=='__main__':unittest.main()
