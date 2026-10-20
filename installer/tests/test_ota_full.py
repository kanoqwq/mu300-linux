"""Protocol 2 migration, ownership preservation and kernel transaction tests.

All block-device writes use regular files under a temporary root. These are not
claims of device power-cut or modem validation.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import mmap
import tracemalloc
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_ota import file, manifest, package
from backend.ota_engine import Engine, Patch, descriptor, canonical
from boot import bind, layout, archives, entries, overlay, target, Binder


def boot_image(slot='a', kernel=b'kernel-before', family='e5', payload=0):
    init = b'#!/bin/sh\n    log "stage=switch-root root=$rootdir init=$init"\n    exec switch_root /newroot $init\n'
    files = {'init': (init, stat.S_IFREG|0o755),
             'etc/e5/linux-slot': (slot.encode()+b'\n', stat.S_IFREG|0o644),
             'etc/misc-bc-slot-b-trial.bin': (b'_'+slot.encode()+b'\0'*30, stat.S_IFREG|0o644)}
    if payload: files['linux-modules/large.ko'] = (b'x'*payload, stat.S_IFREG|0o644)
    ram = overlay(files)
    header = bytearray(4096); header[:8] = b'ANDROID!'
    struct.pack_into('<II', header, 8, len(kernel), len(ram)); struct.pack_into('<I', header, 40, 4)
    command = b'root=installed-device custom=keep'
    header[44:44+len(command)] = command
    body = bytes(header)+kernel.ljust((len(kernel)+4095)&~4095, b'\0')+ram
    body = body.ljust((len(body)+4095)&~4095, b'\0'); avb = len(body)
    body += b'test-avb'
    footer = bytearray(64); footer[:4] = b'AVBf'
    struct.pack_into('>QQQ', footer, 12, avb, avb, 8)
    return body.ljust((64 << 20)-64, b'\0')+footer


class BootTests(unittest.TestCase):
    def test_file_binding_matches_byte_api_without_loading_full_image(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory)/'old'; new = Path(directory)/'new'
            old.write_bytes(boot_image('a')); new.write_bytes(boot_image('b', b'new-kernel'))
            expected = bind(new.read_bytes(), old.read_bytes(), 'e5')
            with old.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as current:
                Binder(current,'e5').prepare_file(new)
            actual = new.read_bytes(); *_,limit = layout(actual,'e5')
            self.assertEqual(actual[:limit],expected[:limit]); self.assertEqual(actual[-64:],expected[-64:])

    def test_large_module_payload_is_skipped_with_bounded_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'boot'
            path.write_bytes(boot_image(payload=48 << 20))
            with path.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
                tracemalloc.start()
                try:
                    binder = Binder(image,'e5')
                    _,peak = tracemalloc.get_traced_memory()
                    self.assertIn('etc/e5/linux-slot',binder.roles)
                    self.assertLess(peak,48 << 20,'A whole decoded ramdisk was retained')
                finally: tracemalloc.stop()

    def test_light_python_without_ctypes_can_decode(self):
        import boot
        import builtins
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'ctypes': raise ImportError('ctypes omitted by python3-light')
            return original(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory)/'lz4raw-aarch64.so'; helper.touch()
            with patch.object(boot, '_native_decode', None), patch.object(boot.Path, 'with_name', return_value=helper), patch.object(builtins, '__import__', side_effect=importing):
                self.assertEqual(boot.block_decode(b'\x50hello'), b'hello')

    def test_kernel_and_slot_bindings_and_diagnostic_bytes(self):
        current = bytearray(boot_image('a')); current[56 << 20:(56 << 20)+8] = b'LIVE-LOG'
        incoming = boot_image('b', b'new-kernel')
        result = bind(incoming, bytes(current), 'e5')
        self.assertEqual(result[44:1580], current[44:1580])
        self.assertEqual(result[4096:4106], b'new-kernel')
        self.assertEqual(result[56 << 20:(56 << 20)+8], b'LIVE-LOG')
        start, size, *_ = layout(result, 'e5')
        contents = {}
        for archive in archives(result[start:start+size]):
            contents.update({n:c for n,c,_ in entries(archive)})
        self.assertEqual(contents['etc/e5/linux-slot'], b'a\n')
        self.assertEqual(contents['etc/misc-bc-slot-b-trial.bin'][:2], b'_a')
        self.assertIn(b'unisoc-ota-recovery-v2', contents['init'])
        self.assertLess(contents['init'].index(b'unisoc-ota-recovery-v2'), contents['init'].index(b'exec switch_root'))
        self.assertEqual(bind(result, result, 'e5'), result)

    def test_malformed_boot_refused(self):
        with self.assertRaises(ValueError): bind(b'bad', boot_image(), 'e5')


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem transaction tests')
class FullUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('etc/e5', 'usr/lib', 'lib/apk/db', 'bin', 'lib/modules/new', 'dev/block/by-name'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        (self.root/'etc/e5/release.json').write_text(json.dumps(dict(profile='e5', image_version='e5-r1', kernel_release='old-kernel')))
        (self.root/'etc/e5/linux-slot').write_text('a\n')
        (self.root/'dev/block/by-name/boot_a').write_bytes(boot_image('a'))
        (self.root/'dev/block/by-name/boot_b').write_bytes(b'ANDROID-DO-NOT-WRITE')
        self.engine = Engine(self.root)

    def update(self, updates, boot=False, base='e5-r1', target_version='e5-r2'):
        m = manifest(); m['operations'] = []; blobs = {}
        m.update(base_version=base, target_version=target_version)
        for name, value, merge in updates:
            before = descriptor(self.root/name)
            desc = file(value) if value is not None else None
            op = dict(path=name, before=before, after=desc)
            if merge: op['merge'] = merge
            m['operations'].append(op)
            if value is not None: blobs[desc['sha256']] = value
        if boot:
            content = boot_image('b', b'new-kernel')
            h = hashlib.sha256(content).hexdigest(); blobs[h] = content
            m['boot'] = dict(sha256=h, size=len(content))
            m['kernel_release'] = 'new-kernel'
        path = self.root/'patch.ota'; package(path, m, blobs)
        return path

    def test_packages_plugins_configs_data_self_update_and_rollback(self):
        old = b'P:base\nV:1\nA:aarch64\nF:bin\nR:busybox\n\nP:user-plugin\nV:99\nF:usr/lib\nR:user-plugin\n\n'
        new = b'P:base\nV:2\nA:aarch64\nF:bin\nR:busybox\n\nP:new-dependency\nV:1\n\nP:user-plugin\nV:2\nF:usr/lib\nR:user-plugin\nR:new-plugin-helper\n\n'
        (self.root/'lib/apk/db/installed').write_bytes(old)
        (self.root/'usr/lib/user-plugin').write_bytes(b'user-binary')
        (self.root/'bin/busybox').write_bytes(b'old-busybox')
        (self.root/'etc/shadow').write_bytes(b'root:my-password:0::::::\n')
        (self.root/'etc/config').mkdir(); (self.root/'etc/config/wireless').write_bytes(b'my-ssid-and-password')
        (self.root/'root').mkdir(); (self.root/'root/data.db').write_bytes(b'my-data')
        p = self.update([('lib/apk/db/installed', new, 'apk-db'),
                         ('bin/busybox', b'new-busybox', None),
                         ('usr/lib/user-plugin', b'package-collision', None),
                         ('usr/lib/new-plugin-helper', b'belongs-to-other-plugin-version', None),
                         ('usr/lib/engine.py', b'new-updater', None),
                         ('etc/shadow', b'root:default:0::::::\nnewuser:!:0::::::\n', 'accounts')])
        # Record the base package universe: the extra user plugin is not owned
        # by the full release and must keep both its database row and its files.
        import zipfile
        with Patch(p) as patch:
            patch.manifest['base_packages'] = {'base': {'V':'1'}}
            patch.manifest['target_packages'] = {'base': {'V':'2'}, 'new-dependency': {'V':'1'}}
            result = self.engine.apply(patch)
        self.assertEqual(result['status'], 'complete')
        db = (self.root/'lib/apk/db/installed').read_bytes()
        self.assertIn(b'P:base\nV:2', db); self.assertIn(b'P:user-plugin\nV:99', db)
        self.assertIn(b'P:new-dependency', db)
        self.assertEqual((self.root/'usr/lib/user-plugin').read_bytes(), b'user-binary')
        self.assertFalse((self.root/'usr/lib/new-plugin-helper').exists())
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'new-busybox')
        self.assertTrue((self.root/'etc/shadow').read_bytes().startswith(b'root:my-password:'))
        self.assertIn(b'newuser:!', (self.root/'etc/shadow').read_bytes())
        self.assertEqual((self.root/'etc/config/wireless').read_bytes(), b'my-ssid-and-password')
        self.assertEqual((self.root/'root/data.db').read_bytes(), b'my-data')
        journal = self.engine.state/'journal.json'; data = json.loads(journal.read_text())
        data['status'] = 'rolling_back'; journal.write_bytes(canonical(data))
        self.engine.recover()
        self.assertEqual((self.root/'lib/apk/db/installed').read_bytes(), old)
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'old-busybox')
        self.assertFalse((self.root/'usr/lib/engine.py').exists())

    def test_linux_a_updated_android_b_untouched_with_modules(self):
        (self.root/'lib/modules/new/wifi.ko').write_bytes(b'old-module')
        p = self.update([('lib/modules/new/wifi.ko', b'new-module', None)], boot=True)
        original = (self.root/'dev/block/by-name/boot_a').read_bytes()
        with Patch(p) as patch: self.engine.apply(patch)
        self.assertEqual((self.root/'dev/block/by-name/boot_b').read_bytes(), b'ANDROID-DO-NOT-WRITE')
        self.assertEqual(self.engine.identity()['kernel_release'], 'new-kernel')
        self.assertIn(b'new-kernel', (self.root/'dev/block/by-name/boot_a').read_bytes()[:8192])
        journal = self.engine.state/'journal.json'; data = json.loads(journal.read_text())
        data['status'] = 'rolling_back'; journal.write_bytes(canonical(data)); self.engine.recover()
        self.assertEqual((self.root/'dev/block/by-name/boot_a').read_bytes(), original)
        self.assertEqual((self.root/'lib/modules/new/wifi.ko').read_bytes(), b'old-module')

    def test_partial_module_write_restores_kernel_and_root(self):
        (self.root/'usr/lib/test').write_bytes(b'old-file')
        p = self.update([('usr/lib/test', b'new-file', None)], boot=True)
        original = (self.root/'dev/block/by-name/boot_a').read_bytes()
        def fail(*args): raise RuntimeError('simulated interruption after root write')
        with Patch(p) as patch:
            with self.assertRaises(RuntimeError): self.engine.apply(patch, fail)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(), b'old-file')
        self.assertEqual((self.root/'dev/block/by-name/boot_a').read_bytes(), original)
        self.assertEqual((self.root/'dev/block/by-name/boot_b').read_bytes(), b'ANDROID-DO-NOT-WRITE')

    def test_file_only_update_installs_guard_once_then_does_not_reflash(self):
        import boot
        (self.root/'usr/lib/test').write_bytes(b'old')
        inspect=self.engine.inspect
        def live_inspect(patch):
            result=inspect(patch);result['boot_work']=True;return result
        first=self.update([('usr/lib/test',b'new',None)])
        with patch.object(self.engine,'inspect',side_effect=live_inspect), patch.object(boot,'write_file',wraps=boot.write_file) as writes:
            with Patch(first) as package:self.engine.apply(package)
            self.assertEqual(writes.call_count,1)
        second=self.update([('usr/lib/test',b'newer',None)],base='e5-r2',target_version='e5-r3')
        with patch.object(self.engine,'inspect',side_effect=live_inspect), patch.object(boot,'write_file',wraps=boot.write_file) as writes:
            with Patch(second) as package:self.engine.apply(package)
            self.assertEqual(writes.call_count,0)

    def test_manual_rollback_rejects_subsequently_modified_boot(self):
        (self.root/'usr/lib/test').write_bytes(b'old')
        p=self.update([('usr/lib/test',b'new',None)],boot=True)
        with Patch(p) as patch:self.engine.apply(patch)
        with (self.root/'dev/block/by-name/boot_a').open('r+b') as image:
            image.seek(4096);image.write(b'changed')
        import subprocess
        engine=Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        result=subprocess.run([sys.executable,str(engine),'rollback','--root',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('boot changed',result.stdout)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')
