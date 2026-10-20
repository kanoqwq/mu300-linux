"""Data refresh must preserve native binaries and reject compiled-input changes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

spec = importlib.util.spec_from_file_location('ota_refresh_test', Path(__file__).resolve().parents[1]/'refresh-ota-data.py')
refresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(refresh)


class DataRefreshTests(unittest.TestCase):
    def fixture(self, root):
        artifact = root/'artifact'; artifact.mkdir()
        installer = root/'installer'; installer.mkdir()
        key = '../openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        source = installer/key; source.parent.mkdir(parents=True); source.write_bytes(b'new-data\n')
        files = {}
        for name in ('OpenWrt-Installer.exe', 'tools/adb.exe', 'tools/AdbWinApi.dll',
                     'tools/AdbWinUsbApi.dll', 'tools/backend/openwrt-backend.exe', 'tools/backend/_internal/engine.py'):
            path = artifact/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'old-data\n' if name.endswith('engine.py') else b'UNCHANGED-NATIVE')
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        before = {key:hashlib.sha256(b'old-data\n').hexdigest(), 'backend/ota_runtime.py':'compiled-original'}
        after = dict(before); after[key] = hashlib.sha256(source.read_bytes()).hexdigest()
        metadata = dict(protocol=1, platform='windows-x64', source=before, files=files)
        (artifact/'build.json').write_text(json.dumps(metadata))
        return artifact, installer, before, after

    def test_only_plain_data_replaced_all_native_hashes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, installer, before, after = self.fixture(Path(directory))
            with patch.object(refresh, 'HERE', installer), patch.object(refresh, 'sources', return_value=after):
                result = refresh.refresh(artifact, 'windows-x64')
            self.assertEqual(result['source'], after)
            self.assertEqual((artifact/'tools/backend/_internal/engine.py').read_bytes(), b'new-data\n')
            self.assertEqual((artifact/'tools/backend/openwrt-backend.exe').read_bytes(), b'UNCHANGED-NATIVE')
            self.assertEqual(len(result['data_refreshes']), 1)

    def test_compiled_input_change_requires_rebuild_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, installer, before, after = self.fixture(Path(directory))
            after['backend/ota_runtime.py'] = 'compiled-changed'
            with patch.object(refresh, 'HERE', installer), patch.object(refresh, 'sources', return_value=after):
                with self.assertRaisesRegex(ValueError, 'native rebuild required'):
                    refresh.refresh(artifact, 'windows-x64')
            self.assertEqual((artifact/'tools/backend/_internal/engine.py').read_bytes(), b'old-data\n')
            self.assertEqual(json.loads((artifact/'build.json').read_text())['source'], before)
