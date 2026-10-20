#!/usr/bin/env python3
"""Build orchestration boundaries: no compilation, containers or device access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_release', TOP / 'tools/build-release.py')
build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)


class Layout(unittest.TestCase):
    def test_installer_defaults_match_native_builder_directories(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build.installer_artifact('windows-x64'), TOP / 'installer/artifacts/windows')
            self.assertEqual(build.installer_artifact('macos-arm64'), TOP / 'installer/artifacts/macos-arm64')
        with patch.dict(os.environ, {'OPENWRT_GUI_ARTIFACT': '/verified/windows',
                                    'OPENWRT_GUI_MACOS_ARTIFACT': '/verified/mac'}):
            self.assertEqual(build.installer_artifact('windows-x64'), Path('/verified/windows'))
            self.assertEqual(build.installer_artifact('macos-arm64'), Path('/verified/mac'))

    def test_registry_covers_every_model_without_copying_or_creating(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / 'not-created'
            common, rows = build.layout(TOP / 'tools/build-layout.json', workspace, build.PROFILES)
            self.assertEqual(set(rows), set(build.PROFILES))
            self.assertEqual(rows['mu300-7.2']['kernel'], workspace / 'kernels/mu300-7.2.8')
            self.assertEqual(common['inputs'], TOP.parent / 'mu300-linux/work')
            self.assertFalse(workspace.exists())

    def test_missing_inputs_stop_before_any_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); missing = root / 'missing'
            with self.assertRaisesRegex(ValueError, 'Missing registered inputs'):
                build.input_check({'base': missing}, {})
            self.assertFalse(missing.exists())

    def test_unknown_profile_and_unsafe_workspace(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            build.layout(TOP / 'tools/build-layout.json', Path('/tmp/e5-build'), ['other'])
        with self.assertRaisesRegex(ValueError, 'dedicated'):
            build.layout(TOP / 'tools/build-layout.json', Path('/'), ['mu300-7.2'])
        with self.assertRaisesRegex(ValueError, 'C:'):
            build.layout(TOP / 'tools/build-layout.json', Path('/mnt/c/build'), ['mu300-7.2'])


@unittest.skipIf(os.name == 'nt', 'Linux workspace lock and mount checks')
class Ownership(unittest.TestCase):
    def test_second_owner_is_blocked_and_lock_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with build.workspace_lock(workspace):
                    with self.assertRaisesRegex(ValueError, 'Another build'):
                        with build.workspace_lock(workspace):self.fail('lock bypass')
                    raise RuntimeError('fixture')
            with build.workspace_lock(workspace):pass

    def test_only_owned_staging_deleted_caches_and_backups_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['sources/kernel', 'objects/e5', 'cache/input', 'backups/boot.img']:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('keep')
            stage = build.stage_path(root, 'mu300-7.2'); stage.mkdir(parents=True)
            (stage / 'root').mkdir()
            with self.assertRaises(FileNotFoundError):build.clean_stage(root, 'mu300-7.2')
            build.write_json(stage / '.build-owner.json', {'owner': build.OWNER, 'profile': 'invalid'})
            with self.assertRaisesRegex(ValueError, 'unowned'):build.clean_stage(root, 'mu300-7.2')
            build.write_json(stage / '.build-owner.json', {'owner': build.OWNER, 'profile': 'mu300-7.2'})
            build.clean_stage(root, 'mu300-7.2')
            self.assertFalse(stage.exists())
            self.assertTrue(all((root / n).exists() for n in
                ['sources/kernel', 'objects/e5', 'cache/input', 'backups/boot.img']))

    def test_symlink_escape_and_live_mount_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); outside = root / 'outside'; outside.mkdir()
            workspace = root / 'workspace'; parent = workspace / 'package-work/mu300-release'
            parent.mkdir(parents=True)
            target = parent / 'mu300-7.2'; target.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlinks'):build.clean_stage(workspace, 'mu300-7.2')
            target.unlink(); target.mkdir()
            build.write_json(target / '.build-owner.json', {'owner': build.OWNER, 'profile': 'mu300-7.2'})
            read_text = Path.read_text
            def read(path, *args, **kwargs):
                if str(path) == '/proc/self/mountinfo':
                    return '1 2 3 / ' + str(target / 'assembly/root') + ' rw - ext4 /dev/loop0 rw\n'
                return read_text(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                with self.assertRaisesRegex(ValueError, 'Unmount'):build.clean_stage(workspace, 'mu300-7.2')
            self.assertTrue(target.exists()); self.assertTrue(outside.exists())


class Archives(unittest.TestCase):
    def fixture(self, path, *, corrupt=False, extra=None):
        files = {'payload/boot-linux.img': b'boot', 'payload/mu300-openwrt.tar.gz': b'root', 'OpenWrt-Installer.py': b'cli'}
        if extra:files[extra] = b'duplicate root'
        manifest = {'magisk_layout': {'boot-linux.img':'payload/boot-linux.img', 'mu300-openwrt.tar.gz':'payload/mu300-openwrt.tar.gz'},
                    'magisk': {k:hashlib.sha256(v).hexdigest() for k,v in files.items() if k.startswith('payload/')},
                    'desktop': {'OpenWrt-Installer.py':hashlib.sha256(files['OpenWrt-Installer.py']).hexdigest()}}
        if corrupt:files['payload/boot-linux.img'] = b'wrong'
        with zipfile.ZipFile(path, 'w') as archive:
            for k,v in files.items():archive.writestr(k,v)
            archive.writestr('desktop/manifest.json', json.dumps(manifest))

    def test_full_archive_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.zip'; self.fixture(path); build.verify_archive(path)
            self.fixture(path, corrupt=True)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):build.verify_archive(path)

    def test_duplicate_payloads_and_missing_checksum_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.zip'
            for extra in ['other/mu300-openwrt.tar.gz', 'other/openwrt.ext4.gz', 'rootfs.tar.gz']:
                self.fixture(path, extra=extra)
                with self.assertRaisesRegex(ValueError, 'one boot'):build.verify_archive(path)
            self.fixture(path)
            with zipfile.ZipFile(path, 'a') as archive:archive.writestr('unrecorded', b'extra')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):build.verify_archive(path)


class TaskState(unittest.TestCase):
    def fake_assembly(self, task, model, args, extra_env=None):
        current = model.removesuffix('-package')
        active = [p.name for p in (task.workspace / 'package-work/mu300-release').iterdir()]
        self.assertEqual(active, [current])
        stage = Path(extra_env['MU300_PACKAGE_WORK'])
        (stage / 'temporary-image').write_bytes(b'temporary root')
        Archives().fixture(Path(args[-1]))
        series = extra_env['MU300_KERNEL']
        (stage / ('release-' + series + '.json')).write_text(json.dumps({'package_name':current+'-release'}))

    def test_serial_success_cleans_only_staging_and_publishes_at_end(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'TOP', Path(folder)), \
             patch.object(build.os, 'geteuid', return_value=0), patch.object(build, 'input_check'), \
             patch.object(build.runpy, 'run_path', return_value={
                'identify': lambda *a: {'private_version':'r-test','source_fingerprint':'same'}}):
            task = build.Task(Path(folder) / 'native', 'fixture')
            with patch.object(task, 'run', side_effect=lambda model,args,**kw:self.fake_assembly(task,model,args,**kw)):
                build.package(task, {'inputs':Path('/input'),'desktop_tools':Path('/tools')}, {'mu300-7.2': {'kernel':Path('/kernel')}, 'mu300-6.18': {'kernel':Path('/kernel2')}})
            destination = Path(folder) / 'release' / task.id
            self.assertTrue(destination.is_dir())
            self.assertFalse(destination.with_name('.building-' + task.id).exists())
            self.assertEqual(len(list(destination.glob('*.zip'))), 2)
            self.assertFalse(build.stage_path(task.workspace, 'mu300-7.2').exists())
            self.assertFalse(build.stage_path(task.workspace, 'mu300-6.18').exists())
            self.assertEqual(task.data['state'], 'complete')

    def test_changed_sources_block_publication_after_assembly(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'TOP', Path(folder)), \
             patch.object(build.os, 'geteuid', return_value=0), patch.object(build, 'input_check'):
            identify = unittest.mock.Mock(side_effect=[
                {'private_version':'r-test','source_fingerprint':'before'},
                {'private_version':'r-test','source_fingerprint':'after'}])
            with patch.object(build.runpy, 'run_path', return_value={'identify': identify}):
                task = build.Task(Path(folder) / 'native', 'fixture')
                with patch.object(task, 'run', side_effect=lambda model,args,**kw:self.fake_assembly(task,model,args,**kw)):
                    with self.assertRaisesRegex(ValueError, 'sources changed'):
                        build.package(task, {'inputs':Path('/input'),'desktop_tools':Path('/tools')}, {'mu300-7.2': {'kernel':Path('/kernel')}})
                self.assertFalse((Path(folder) / 'release' / task.id).exists())
                self.assertTrue((Path(folder) / 'release' / ('.building-' + task.id)).exists())

    def test_real_child_logs_and_failure_status(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'TOP', Path(folder)):
            workspace = Path(folder) / 'native'
            task = build.Task(workspace, 'fixture')
            task.run('ok', [sys.executable, '-c', 'print("test-log-output")'])
            self.assertIn('test-log-output', (task.logs / 'ok.log').read_text())
            with self.assertRaises(subprocess.CalledProcessError):
                task.run('error', [sys.executable, '-c', 'raise SystemExit(3)'])
            status = json.loads((task.logs / 'status.json').read_text())
            self.assertEqual(status['state'], 'failed')
            self.assertEqual([x['state'] for x in status['steps']], ['complete', 'failed'])
            self.assertFalse((Path(folder) / 'release').exists())
            self.assertEqual(list((workspace / 'tmp').iterdir()), [])

    def test_failure_keeps_owned_staging_and_unpublished_archive(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'TOP', Path(folder)):
            root = Path(folder); workspace = root / 'native'
            with patch.object(build.runpy, 'run_path', return_value={
                'identify': lambda *a: {'private_version':'r-test','source_fingerprint':'same'}}), \
                 patch.object(build, 'input_check'), patch.object(build.os, 'geteuid', return_value=0):
                task = build.Task(workspace, 'fixture')
                def fail(*args, **kwargs):raise RuntimeError('assembler failed')
                with patch.object(task, 'run', side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, 'assembler failed'):
                        build.package(task, {'inputs':Path('/input'),'desktop_tools':Path('/tools')}, {'mu300-7.2': {'kernel':Path('/kernel')}})
                stage = build.stage_path(workspace, 'mu300-7.2')
                self.assertTrue((stage / '.build-owner.json').exists())
                self.assertEqual([p.name for p in (root / 'release').iterdir()], ['.building-' + task.id])

    def test_preflight_failure_creates_no_root_staging_or_release(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'TOP', Path(folder)), \
             patch.object(build.os, 'geteuid', return_value=0), \
             patch.object(build, 'input_check', side_effect=ValueError('stale GUI')):
            task = build.Task(Path(folder) / 'native', 'fixture')
            with self.assertRaisesRegex(ValueError, 'stale GUI'):build.package(task, {'inputs':Path('/input'),'desktop_tools':Path('/tools')}, {'mu300-7.2': {'kernel':Path('/kernel')}})
            self.assertFalse((Path(folder) / 'native/package-work').exists())
            self.assertFalse((Path(folder) / 'release').exists())


if __name__ == '__main__':unittest.main()
