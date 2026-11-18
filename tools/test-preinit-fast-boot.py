#!/usr/bin/env python3
"""Run real OpenWrt preinit functions and real update delivery in private roots."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

TOP = Path(__file__).resolve().parents[1]
REL = 'lib/preinit/31_unisoc_fast_boot'
SOURCE = TOP / 'openwrt/overlay' / REL
E5 = (TOP / 'openwrt/device-install-image.sh').exists()
sys.path.insert(0, str(TOP / 'installer'))
from backend.ota_engine import Engine, Patch, canonical, descriptor
from backend.ota_package import compare


def put(root, name, data):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    p.chmod(0o644)


class Preinit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = os.environ.get('UNISOC_PREINIT_IMAGE')
        if not image:
            raise unittest.SkipTest('select an existing OpenWrt base image with UNISOC_PREINIT_IMAGE')
        cls.framework = subprocess.check_output([
            'docker', 'run', '--rm', '--network', 'none', '--platform', 'linux/arm64',
            '--entrypoint', '/bin/cat', image, '/lib/preinit/10_indicate_preinit',
            '/lib/preinit/30_failsafe_wait', '/lib/preinit/99_10_run_init'], text=True)

    def run_boot(self, cmdline, fast):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'cmdline').write_text(cmdline)
            (root / 'netdev').write_text('usb0: preserved early USB network\n')
            timer = root / 'ip'
            timer.write_text('#!/bin/sh\necho ip-operation\n')
            timer.chmod(0o755)
            # Only the command line, marker files and network syscall are fixtures.
            # No real interface or global /tmp/failsafe can be modified by this test.
            source = self.framework.replace('/tmp/', str(root) + '/')
            source = source.replace('/proc/cmdline', str(root/'cmdline'))
            source = source.replace('/proc/net/dev', str(root/'netdev'))
            source = source.replace('ip ', str(timer) + ' ')
            hook = SOURCE.read_text().replace('/proc/cmdline', str(root/'cmdline'))
            code = 'boot_hook_add() { :; };\n' + source + '''
fs_wait_for_key() { echo key-countdown; return 1; }
preinit_ip_config() { echo rescue-network; }
preinit_net_echo() { :; }
pi_ifname=usb0
fs_failsafe_wait_timeout=4
'''
            if fast:
                code += hook
            code += '\npreinit_ip\nfailsafe_wait\nrun_init\nprintf "RESULT|%s|%s\\n" "$FAILSAFE" "$pi_preinit_no_failsafe"\n'
            commands = [['sh'], ['bash']]
            if shutil.which('busybox'):
                commands.append(['busybox', 'sh'])
            results = []
            for command in commands:
                p = subprocess.run(command + ['-c', code], capture_output=True, text=True, timeout=5)
                self.assertEqual(p.returncode, 0, p.stderr)
                results.append(p.stdout.splitlines())
            return results

    def test_normal_boot_skips_countdown_and_network_without_flushing_early_usb(self):
        for lines in self.run_boot('console=ttyS1 root=test', False):
            self.assertIn('key-countdown', lines)
            self.assertIn('rescue-network', lines)
            self.assertIn('ip-operation', lines)
        for lines in self.run_boot('console=ttyS1 root=test', True):
            self.assertEqual(lines, ['RESULT||y'])

    def test_explicit_failsafe_retains_original_rescue_functions(self):
        for args in ('failsafe=1 console=ttyS1', 'root=test failsafe='):
            for lines in self.run_boot(args, True):
                self.assertIn('rescue-network', lines)
                self.assertIn('ip-operation', lines)
                self.assertNotIn('key-countdown', lines)
                self.assertIn('RESULT|true|', lines)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'real OTA ownership requires POSIX root')
class Delivery(unittest.TestCase):
    def test_first_and_subsequent_real_diff_keep_user_configuration(self):
        family = 'e5' if E5 else 'mu300'
        profile = 'e5' if E5 else ('u30pro' if (TOP/'tools/u30pro-package.py').exists() else 'mu300')
        metadata = 'etc/e5/release.json' if E5 else 'etc/mu300/release.json'
        boot = b'unchanged boot; bootloader rollback is outside this patch'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old, new, third, live = [root / n for n in ('old', 'new', 'third', 'live')]
            for tree in (old, new, third):
                put(tree, 'etc/config/wireless', b'user Wi-Fi settings')
                put(tree, 'etc/config/network', b'user LAN and WAN settings')
                put(tree, 'etc/config/unisoc_modem', b'user radio permissions')
            if E5:
                put(old, 'lib/preinit/01_e5_boot_wait', b'fs_failsafe_wait_timeout=1\n')
            put(new, REL, SOURCE.read_bytes())
            put(third, REL, SOURCE.read_bytes() + b'\n# subsequent delivery\n')

            def release(tree, version):
                meta = dict(profile=profile, image_version=profile+'-r'+str(version),
                            kernel_release='test-unchanged-kernel', openwrt='25.12.5')
                put(tree, metadata, json.dumps(meta).encode())
                return SimpleNamespace(family=family, source=str(tree), meta=meta, packages={},
                    entries={p.relative_to(tree).as_posix():descriptor(p) for p in tree.rglob('*')},
                    boot_name='boot.img', boot_digest=hashlib.sha256(boot).hexdigest(), open=lambda n:io.BytesIO(boot))

            versions = [release(tree, n) for n, tree in enumerate((old, new, third), 1)]
            shutil.copytree(old, live)
            runtime = importlib.import_module(compare.__module__.rsplit('.', 1)[0]+'.ota_runtime')
            for index, target in enumerate((new, third)):
                with patch.object(runtime, 'build', return_value=({}, None, {})):
                    plan, _ = compare(versions[index], versions[index+1])
                self.assertTrue(plan['can_export'], plan['blockers'])
                archive = root / ('patch-%d.ota' % index)
                with zipfile.ZipFile(archive, 'w') as stream:
                    stream.writestr('manifest.json', canonical(plan['manifest']))
                    added = set()
                    for op in plan['manifest']['operations']:
                        desc = op['after']
                        if desc and desc['kind']=='file' and desc['sha256'] not in added:
                            stream.write(target/op['path'], 'blobs/'+desc['sha256'])
                            added.add(desc['sha256'])
                with Patch(archive) as delta:
                    self.assertEqual(Engine(live).apply(delta)['status'], 'complete')
                self.assertEqual((live/REL).read_bytes(), (target/REL).read_bytes())
                for name in ('wireless', 'network', 'unisoc_modem'):
                    self.assertEqual((live/'etc/config'/name).read_bytes(), (old/'etc/config'/name).read_bytes())
            if E5:
                self.assertFalse((live/'lib/preinit/01_e5_boot_wait').exists())

    def test_ordinary_update_delivers_hook_without_overwriting_settings(self):
        if not E5:
            sys.path.insert(0, str(TOP/'tests'))
            from test_update import Update
            import tarfile
            fixture = Update()
            fixture.setUp()
            try:
                for shell in fixture.each_shell():
                    old, user = fixture.vendor_update_fixture()
                    archive = fixture.disk/'.mu300-update/mu300-openwrt-rootfs.tar.gz'
                    files = {'sbin/init':b'#!/bin/sh\n', 'etc/mu300/image-version':b'test-new\n', REL:SOURCE.read_bytes()}
                    with tarfile.open(archive, 'w:gz') as stream:
                        for name, data in files.items():
                            item=tarfile.TarInfo(name);item.mode=0o755 if name=='sbin/init' else 0o644;item.size=len(data)
                            stream.addfile(item, io.BytesIO(data))
                    p=fixture.up(shell, 'sync() { :; }; is_root() { false; }; apply_one openwrt test-new')
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual((old/REL).read_bytes(), SOURCE.read_bytes())
                    self.assertEqual((old/'etc/config/wireless').read_bytes(), user['etc/config/wireless'])
                    shutil.rmtree(old);shutil.rmtree(fixture.disk/'openwrt.old')
            finally:
                fixture.tearDown()
            return
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);old=root/'old';new=root/'new'
            for tree in (old, new):
                put(tree, 'etc/e5/release.json', b'{"profile":"e5"}')
            put(old, 'etc/config/wireless', b'user Wi-Fi settings')
            put(new, REL, SOURCE.read_bytes())
            source=(TOP/'openwrt/device-install-image.sh').read_text()
            start=source.index('keep_config() {')
            function=source[start:source.index('\n}\n', start)+3]
            function=function.replace('/tmp/e5-img.', str(root/'migration.'))
            p=subprocess.run(['sh','-ec', function+'\nkeep_config "$1" "$2"', 'test', str(old), str(new)],
                capture_output=True, text=True, env={**os.environ, 'E5_NV_POLICY':'preserve'})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((new/REL).read_bytes(), SOURCE.read_bytes())
            self.assertEqual((new/'etc/config/wireless').read_bytes(), b'user Wi-Fi settings')


if __name__ == '__main__':
    unittest.main(verbosity=2)
