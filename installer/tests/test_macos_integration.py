"""Native build/attachment contracts using disposable text fixtures, no compilers or devices."""
import importlib.util
import io
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build
import package_gui
from backend.state import app_directory
from backend.state import Store
from backend.service import Service
from backend.transport import Adb

TOP = Path(__file__).resolve().parents[2]


class MacIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = {'fixture.py': 'fixture-source'}
        self.installer = self.root/'installer'
        self.installer.mkdir()
        for name in ('GUIDE.md', 'PROTOCOL.md', 'README.md', 'VERIFICATION.md'):
            (self.installer/name).write_text('fixture guide', encoding='utf-8')
        self.package = self.root/'package'
        (self.package/'files').mkdir(parents=True)
        (self.package/'files/openwrt.ext4.gz').write_bytes(b'unchanged-firmware')

    def artifact(self, platform):
        root = self.root/platform
        names = ('OpenWrt-Installer.exe', 'tools/adb.exe', 'tools/AdbWinApi.dll',
                 'tools/AdbWinUsbApi.dll', 'tools/backend/openwrt-backend.exe') if platform == 'windows-x64' else (
                 'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer',
                 'OpenWrt-Installer.app/Contents/Info.plist', 'OpenWrt-Installer.app/Contents/Resources/icon.icns',
                 'tools/macos/adb', 'tools/macos/backend/openwrt-backend')
        for name in names:
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('fixture '+name).encode())
            target.chmod(0o755)
        metadata = {'protocol': 1, 'platform': platform, 'source': self.source,
                    'files': {name: package_gui.digest(root/name) for name in names}}
        (root/'build.json').write_text(json.dumps(metadata), encoding='utf-8')
        return root

    def attach(self, windows, mac=None):
        env = {'OPENWRT_GUI_MACOS_ARTIFACT': str(mac)} if mac else {}
        with patch.object(package_gui, 'HERE', self.installer), patch.object(package_gui, 'sources', return_value=self.source), patch.dict(os.environ, env, clear=True):
            return package_gui.attach(self.package, windows)

    def test_both_runtimes_share_payload_and_report_sizes(self):
        windows, mac = self.artifact('windows-x64'), self.artifact('macos-arm64')
        before = package_gui.digest(self.package/'files/openwrt.ext4.gz')
        self.assertTrue(self.attach(windows, mac))
        self.assertEqual(package_gui.digest(self.package/'files/openwrt.ext4.gz'), before)
        self.assertTrue((self.package/'tools/backend/openwrt-backend.exe').is_file())
        self.assertTrue((self.package/'tools/macos/backend/openwrt-backend').is_file())
        self.assertTrue((self.package/'docs/GUI-BUILD-macos.json').is_file())
        report = package_gui.size_report(self.package)
        self.assertEqual(report['gui_bytes'], report['gui_windows_bytes']+report['gui_macos_bytes'])
        self.assertGreater(report['gui_macos_bytes'], 0)
        self.assertEqual(report['firmware_payload_bytes'], len(b'unchanged-firmware'))
        if os.name != 'nt':
            for name in ('OpenWrt-Installer.app/Contents/MacOS/openwrt-installer', 'tools/macos/adb', 'tools/macos/backend/openwrt-backend'):
                self.assertTrue(os.access(self.package/name, os.X_OK))

    def test_invalid_mac_rejected_before_windows_copy(self):
        windows, mac = self.artifact('windows-x64'), self.artifact('macos-arm64')
        for field, value, expected in [('source', {}, 'stale'), ('platform', 'macos-x64', 'platform/protocol'), ('protocol', 2, 'platform/protocol')]:
            with self.subTest(field=field):
                path = mac/'build.json'
                original = path.read_text()
                metadata = json.loads(original)
                metadata[field] = value
                path.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, expected): self.attach(windows, mac)
                self.assertFalse((self.package/'OpenWrt-Installer.exe').exists())
                path.write_text(original)

    def test_hash_missing_file_extra_file_and_escape_rejected(self):
        mac = self.artifact('macos-arm64')
        metadata_path = mac/'build.json'
        original = metadata_path.read_text()
        adb = mac/'tools/macos/adb'
        contents = adb.read_bytes()
        adb.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum/path'): package_gui.validate_artifact(mac, 'macos-arm64', self.source)
        adb.unlink()
        with self.assertRaisesRegex(ValueError, 'checksum/path'): package_gui.validate_artifact(mac, 'macos-arm64', self.source)
        adb.write_bytes(contents)
        extra = mac/'tools/macos/unrecorded'
        extra.write_text('extra')
        with self.assertRaisesRegex(ValueError, 'unrecorded'): package_gui.validate_artifact(mac, 'macos-arm64', self.source)
        extra.unlink()
        outside = self.root/'outside'
        outside.write_text('outside')
        metadata = json.loads(original)
        metadata['files']['tools/macos/../../../outside'] = package_gui.digest(outside)
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, 'checksum/path'): package_gui.validate_artifact(mac, 'macos-arm64', self.source)

    def test_auto_detect_current_mac_artifact(self):
        windows, mac = self.artifact('windows-x64'), self.artifact('macos-arm64')
        target = self.installer/'artifacts/macos-arm64'
        target.parent.mkdir()
        mac.rename(target)
        self.assertTrue(self.attach(windows))
        self.assertTrue((self.package/'OpenWrt-Installer.app').is_dir())

    def test_missing_mac_refuses_release_before_any_copy(self):
        windows = self.artifact('windows-x64')
        with self.assertRaisesRegex(ValueError, 'Apple Silicon GUI build missing'):
            self.attach(windows)
        self.assertFalse((self.package/'OpenWrt-Installer.exe').exists())
        (self.package/'OpenWrt-Installer.app').mkdir()
        with self.assertRaisesRegex(ValueError, 'Apple Silicon GUI build missing'):
            self.attach(windows)

    def test_refresh_removes_old_runtime_dependencies(self):
        windows, mac = self.artifact('windows-x64'), self.artifact('macos-arm64')
        for name in ('tools/backend/removed.dll', 'tools/macos/backend/removed.dylib', 'OpenWrt-Installer.app/Contents/removed'):
            target = self.package/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('obsolete')
        self.attach(windows, mac)
        self.assertEqual(list(self.package.rglob('removed*')), [])

    def test_native_platform_guard(self):
        self.assertEqual(build.native_platform('win32', 'AMD64'), 'windows-x64')
        self.assertEqual(build.native_platform('darwin', 'arm64'), 'macos-arm64')
        for system, machine in [('darwin', 'x86_64'), ('linux', 'aarch64'), ('win32', 'ARM64')]:
            with self.assertRaises(ValueError): build.native_platform(system, machine)

    def test_mocked_native_build_uses_mac_tools_and_signature(self):
        # Every external command is intercepted; all outputs below are plain text fixtures.
        for name in ('ui/src-tauri/target/release/openwrt-installer', 'ui/src-tauri/icons/icon.icns'):
            target = self.installer/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture')
        (self.installer/'ui/src-tauri/tauri.conf.json').write_text(json.dumps({'identifier':'org.test.fixture','version':'2.3.4'}))
        adb = self.root/'adb-source'
        adb.mkdir()
        for name in ('adb', 'NOTICE.txt'): (adb/name).write_text('fixture')
        backend = self.root/'work/installer-backend-macos/dist/openwrt-backend'
        backend.mkdir(parents=True)
        (backend/'openwrt-backend').write_text('fixture')
        with patch.object(build, 'HERE', self.installer), patch.object(build, 'native_platform', return_value='macos-arm64'), patch.object(build, 'sources', return_value=self.source), patch.object(build, 'run') as commands, patch.object(build.subprocess, 'run') as child, patch.object(build.subprocess, 'check_output', return_value='arm64\n'), patch.object(sys, 'argv', ['build.py', '--adb-dir', str(adb)]), patch.dict(os.environ):
            with redirect_stdout(io.StringIO()):
                build.main()
        args = [call.args for call in commands.call_args_list]
        self.assertEqual(args[0][0], 'npm')
        self.assertIn(('codesign','--verify','--deep','--strict',self.installer/'artifacts/macos-arm64/OpenWrt-Installer.app'), args)
        self.assertEqual(child.call_args.args[0][-1], 'version')
        app = self.installer/'artifacts/macos-arm64/OpenWrt-Installer.app'
        info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleShortVersionString'], '2.3.4')
        self.assertEqual(info['CFBundleIdentifier'], 'org.test.fixture')
        package_gui.validate_artifact(app.parent, 'macos-arm64', self.source)

    def test_fingerprints_normalize_text_but_not_icons(self):
        names = package_gui.sources().keys()
        for name in names:
            path = self.installer/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture\n')
        with patch.object(package_gui, 'HERE', self.installer):
            before = package_gui.sources()
            for name in names:
                if Path(name).suffix not in ('.png', '.ico', '.icns', '.so'):
                    (self.installer/name).write_bytes(b'fixture\r\n')
            self.assertEqual(package_gui.sources(), before)
            (self.installer/'ui/src-tauri/icons/icon.icns').write_bytes(b'fixture\r\n')
            self.assertNotEqual(package_gui.sources(), before)

    def test_mac_adb_and_application_data_do_not_use_windows_paths(self):
        bundled = self.package/'tools/macos/adb'
        bundled.parent.mkdir(parents=True)
        bundled.write_text('fixture')
        with patch.object(sys, 'platform', 'darwin'), patch('backend.transport.shutil.which', return_value='external-adb'):
            self.assertEqual(Adb(self.package).exe, str(bundled))
            self.assertEqual(app_directory(), Path.home()/'Library/Application Support/OpenWrt-Installer')
        # Test the actual compatible CLI's lookup as well, without executing it.
        spec = importlib.util.spec_from_file_location('mac_cli_fixture', TOP/'openwrt/bundle/flash.py')
        if not spec.origin or not Path(spec.origin).is_file(): self.skipTest('E5 compatible CLI source is not in this mirror')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.B = str(self.package)
        with patch.object(sys, 'platform', 'darwin'), patch.object(module.shutil, 'which', return_value='external-adb'):
            module.find_adb()
        self.assertEqual(Path(module.ADB), bundled)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink aliases, executed separately in Linux/WSL')
    def test_recovery_import_resolves_macos_directory_aliases(self):
        saved = self.root/'real-transaction'
        saved.mkdir()
        alias = self.root/'alias-transaction'
        alias.symlink_to(saved, target_is_directory=True)
        legacy = saved/'legacy/transaction.json'
        legacy.parent.mkdir()
        legacy.write_text('{}')
        record = {'version':1, 'id':'abcdef0123456789abcdef0123456789', 'data_directory':str(alias),
                  'legacy':str(legacy), 'status':'failed', 'stage':'prepare', 'device':{}, 'package':{}}
        (saved/'transaction.json').write_text(json.dumps(record))
        store = Store(self.root/'app-data')
        service = Service(self.package, store, io.StringIO())
        service.dispatch({'protocol':1, 'id':1, 'command':'import', 'params':{'path':str(alias)}})
        loaded = store.load(record['id'])
        self.assertEqual(Path(loaded['legacy']), store.directory(record['id'])/'legacy/transaction.json')
        self.assertEqual(Path(loaded['legacy']).read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
