"""Exercise real archive output, including Windows-lost macOS file modes."""
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import json
import io
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release
from release import archive


class ReleaseArchiveTest(unittest.TestCase):
    def test_dotted_firmware_version_is_preserved_in_default_zip_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source'
            (source/'files').mkdir(parents=True)
            (source/'files/openwrt.ext4.gz').write_bytes(b'fixture')
            target = root/'e5-openwrt-25.12.5-tf-fixture'
            with patch.object(release, 'inspect_package', return_value={'payloads': {}}), patch.object(release, 'refresh'), patch.object(sys, 'argv', ['release.py', str(source), str(target)]), redirect_stdout(io.StringIO()):
                release.main()
            self.assertTrue((root/(target.name+'.zip')).is_file())
            self.assertFalse((root/'e5-openwrt-25.12.zip').exists())

    def test_refresh_updates_existing_mu300_mac_launcher_and_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            top, package = root/'source', root/'package'
            for name in ('desktop/installer.py', 'desktop/boot_image.py',
                         'desktop/android-write-part.sh', 'desktop/android-write-free.sh',
                         'installer/backend/binary_io.py', 'installer/backend/boot_image.py',
                         'installer/backend/mu300_update.py', 'installer/backend/windows_mtp.py'):
                path = top/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('source fixture')
            (package/'desktop').mkdir(parents=True)
            (package/'desktop/manifest.json').write_text(json.dumps({'magisk': {}, 'desktop': {}}))
            (package/'launchers').mkdir()
            launcher = package/'launchers/start-installer.sh'
            launcher.write_text('#!/bin/sh\nexec python3 OpenWrt-Installer.py --resume "$@"\n')
            with patch.object(release, 'TOP', top), patch.object(release, 'attach'):
                release.refresh(package)
            self.assertIn('openwrt-backend --cli --package-root "$PWD" --resume "$@"', launcher.read_text())
            manifest = json.loads((package/'desktop/manifest.json').read_text())
            self.assertEqual(manifest['desktop']['launchers/start-installer.sh'], release.digest(launcher))
            self.assertEqual(manifest['desktop']['desktop/windows_mtp.py'], release.digest(package/'desktop/windows_mtp.py'))

    def test_mac_executables_survive_windows_modes_and_share_one_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root/'package'
            executables = (
                'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer',
                'tools/macos/adb', 'tools/macos/backend/openwrt-backend',
                'tools/macos/backend/_internal/native.so',
                'launchers/start-installer.sh',
            )
            names = (*executables, 'OpenWrt-Installer.app/Contents/Info.plist',
                     'OpenWrt-Installer.exe', 'files/openwrt.ext4.gz', 'module.prop')
            for name in names:
                path = package/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'\xcf\xfa\xed\xfe fixture' if name.endswith('.so') else b'fixture')
                path.chmod(0o666)
            target = root/'release.zip'
            archive(package, target)
            with zipfile.ZipFile(target) as result:
                self.assertIsNone(result.testzip())
                self.assertEqual(set(result.namelist()), set(names))
                for name in executables:
                    info = result.getinfo(name)
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(stat.S_IMODE(info.external_attr >> 16), 0o755, name)
                self.assertFalse(result.getinfo('OpenWrt-Installer.app/Contents/Info.plist').external_attr >> 16 & 0o111)
                for name in names:
                    self.assertEqual(result.read(name), (package/name).read_bytes())


if __name__ == '__main__':
    unittest.main()
