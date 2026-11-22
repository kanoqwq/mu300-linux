#!/usr/bin/env python3
"""Real APK solver and OTA transaction tests; no device or kernel loading."""
import hashlib
import base64
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

TOP = Path(__file__).resolve().parents[1]
M = runpy.run_path(str(TOP/'tools/stage-kernel-packages.py'))
sys.path.insert(0, str(TOP/'installer'))
from backend.ota_engine import Engine, Patch, canonical, descriptor, package_versions, package_replacements
from backend.ota_package import compare
IMAGE = os.environ.get('UNISOC_APK_TEST_IMAGE', 'u30pro-rootfs:25.12.5')
KREL = '6.12.94-native-test'


def put(root, name, value):
    p = root/name; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(value if isinstance(value, bytes) else value.encode()); p.chmod(0o644)
    return p


def record(name, deps='', files=''):
    checksum = base64.b64encode(hashlib.sha1(name.encode()).digest()).decode()
    return (f'C:Q1{checksum}\nP:{name}\nV:1.0-r1\nA:aarch64_generic\n'
            f'S:0\nI:0\nT:Test {name}\n' + (f'D:{deps}\n' if deps else '') + files)


def fixture(root):
    rows = [record('ubox'), record('kernel'), record('kmod-tun', 'kernel=1.0-r1',
        'F:lib/modules/official\nR:tun.ko\n'), record('kmod-unsupported', 'kernel=1.0-r1'),
        record('consumer', 'kmod-tun ubox')]
    put(root, 'lib/apk/db/installed', '\n\n'.join(rows)+'\n\n')
    put(root, 'etc/apk/world', 'ubox\nkernel\nkmod-tun\nconsumer\n')
    put(root, 'sbin/kmodloader', b'loader fixture')
    put(root, f'lib/modules/{KREL}/tun.ko', b'ELF fixture\0vermagic='+KREL.encode()+b' SMP\0')
    put(root, f'lib/modules/{KREL}/modules.builtin', '')
    put(root, 'etc/config/wireless', 'user password')
    put(root, 'etc/e5/device-profile', 'e5\n')


def release(root, version):
    meta = dict(profile='e5', image_version=version, kernel_release=KREL, openwrt='25.12.5')
    put(root, 'etc/e5/release.json', json.dumps(meta))
    return SimpleNamespace(family='e5', source=str(root), meta=meta,
        packages=package_versions((root/'lib/apk/db/installed').read_bytes()),
        entries={p.relative_to(root).as_posix(): descriptor(p) for p in root.rglob('*')},
        boot_name='boot.img', boot_digest=hashlib.sha256(b'unchanged boot').hexdigest(),
        open=lambda name: io.BytesIO(b'unchanged boot'))


class NativePackages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='kernel-package-tests-')
        cls.path = Path(cls.temp.name)
        cls.config = put(cls.path, 'kernel.config', 'CONFIG_TUN=m\n')
        cls.image = put(cls.path, 'Image', 'Linux version '+KREL+' fixture\0')
        cls.old = cls.path/'old'; fixture(cls.old)
        cls.new = cls.path/'new'; shutil.copytree(cls.old, cls.new)
        cls.archive = cls.path/'native.apk'
        cls.receipt = M['stage'](cls.new, cls.config, cls.image, KREL, 'e5', IMAGE, cls.archive)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def apk(self, root, *args, success=True):
        r = subprocess.run(['docker', 'run', '--rm', '--ulimit', 'core=0', '--network', 'none', '--platform', 'linux/arm64',
            '-v', f'{root}:{root}', '--entrypoint', '/usr/bin/apk', IMAGE,
            '--root', str(root), '--arch', 'aarch64_generic', '--no-network', *args],
            capture_output=True, text=True)
        self.assertEqual(r.returncode == 0, success, r.stdout+r.stderr)
        return r

    def test_01_real_apk_solver_native_provider_and_ownership(self):
        root = self.path/'solver'; shutil.copytree(self.new, root)
        self.apk(root, 'add', '--scripts=no', '--commit-hooks=no', 'kmod-tun')
        r = self.apk(root, 'info', '--who-owns', '/lib/modules/'+KREL+'/tun.ko')
        self.assertIn('kernel-', r.stdout)
        raw = (root/'lib/apk/db/installed').read_text()
        self.assertIn('P:kmod-tun\n', raw)
        self.assertIn('o:unisoc-native-kernel', raw)
        self.assertIn('p:kernel-any', raw)
        self.assertIn('D:kernel='+self.receipt['package_version'], raw)
        self.assertNotIn('P:kmod-unsupported\n', raw)

    def test_02_foreign_kernel_dependency_is_rejected(self):
        root = self.path/'foreign'; shutil.copytree(self.new, root)
        payload = self.path/'foreign-files'; payload.mkdir()
        archive = root/'foreign.apk'
        M['apk_file'](payload, set(), archive, 'pkgname = foreign-kmod\npkgver = 1-r1\n'
            'pkgdesc = Foreign module\narch = aarch64_generic\nlicense = GPL-2.0-only\n'
            'size = 0\nbuilddate = 0\ndepend = kernel=1.0-r1\n')
        before = (root/'lib/apk/db/installed').read_bytes()
        self.apk(root, 'add', '--allow-untrusted', '--scripts=no', '--commit-hooks=no', str(archive), success=False)
        self.assertEqual((root/'lib/apk/db/installed').read_bytes(), before)

    def test_03_idempotence_and_missing_features_do_not_claim_support(self):
        root = self.path/'repeat'; shutil.copytree(self.new, root)
        before = (root/'lib/apk/db/installed').read_bytes()
        M['stage'](root, self.config, self.image, KREL, 'e5', IMAGE)
        self.assertEqual((root/'lib/apk/db/installed').read_bytes(), before)
        self.assertEqual((root/'etc/apk/world').read_text().count('kernel='), 1)
        self.assertNotIn('kmod-fs-btrfs', self.receipt['provides'])
        bad = put(self.path, 'bad.config', 'CONFIG_TUN=n\n')
        with self.assertRaisesRegex(ValueError, 'kmod-tun'):
            M['stage'](root, bad, self.image, KREL, 'e5', IMAGE)
        self.assertEqual((root/'lib/apk/db/installed').read_bytes(), before)

    def test_04_wrong_module_release_fails_before_metadata_change(self):
        root = self.path/'wrong'; shutil.copytree(self.old, root)
        before = (root/'lib/apk/db/installed').read_bytes()
        put(root, f'lib/modules/{KREL}/tun.ko', b'\0vermagic=other SMP\0')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            M['stage'](root, self.config, self.image, KREL, 'e5', IMAGE)
        self.assertEqual((root/'lib/apk/db/installed').read_bytes(), before)

    def make_diff(self, old, new, number):
        runtime = importlib.import_module(compare.__module__.rsplit('.',1)[0]+'.ota_runtime')
        with patch.object(runtime, 'build', return_value=({}, None, {})):
            plan, extra = compare(release(old, 'e5-r'+str(number)), release(new, 'e5-r'+str(number+1)))
        self.assertTrue(plan['can_export'], plan['blockers'])
        archive = self.path/f'patch-{number}.ota'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', canonical(plan['manifest']))
            seen = set()
            for op in plan['manifest']['operations']:
                desc = op['after']
                if desc and desc['kind']=='file' and desc['sha256'] not in seen:
                    z.write(new/op['path'], 'blobs/'+desc['sha256']); seen.add(desc['sha256'])
        return archive

    def test_05_real_diff_migration_preserves_extra_packages_and_rollback(self):
        old = self.path/'diff-old'; new = self.path/'diff-new'; live = self.path/'live'
        shutil.copytree(self.old, old); shutil.copytree(self.new, new)
        archive = self.make_diff(old, new, 1)
        shutil.copytree(old, live)
        db = live/'lib/apk/db/installed'
        db.write_text(db.read_text()+record('user-plugin')+'\n\n')
        put(live, 'etc/config/wireless', 'my retained password')
        before = db.read_bytes()
        with Patch(archive) as delta:
            self.assertEqual(Engine(live).apply(delta)['status'], 'complete')
        migrated = M['records'](db.read_text())
        self.assertIn('kmod-tun', migrated)
        self.assertNotIn('kmod-unsupported', migrated)
        self.assertIn('user-plugin', migrated)
        self.assertEqual((live/'etc/config/wireless').read_text(), 'my retained password')
        self.assertIn('kernel='+self.receipt['package_version'], (live/'etc/apk/world').read_text())
        engine = TOP/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        subprocess.run([sys.executable, str(engine), 'rollback', '--root', str(live)], check=True)
        self.assertEqual(db.read_bytes(), before)

    def test_06_second_diff_and_non_native_update_preserves_boundaries(self):
        old = self.path/'next-old'; new = self.path/'next-new'; live = self.path/'next-live'
        shutil.copytree(self.new, old); shutil.copytree(self.new, new)
        # A different native payload must get a different exact package revision.
        put(new, f'lib/modules/{KREL}/tun.ko', b'ELF changed\0vermagic='+KREL.encode()+b' SMP\0')
        receipt = M['stage'](new, self.config, self.image, KREL, 'e5', IMAGE)
        self.assertNotEqual(receipt['package_version'], self.receipt['package_version'])
        archive = self.make_diff(old, new, 2); shutil.copytree(old, live)
        with Patch(archive) as delta:
            self.assertEqual(Engine(live).apply(delta)['status'], 'complete')
        self.assertIn('V:'+receipt['package_version'], (live/'lib/apk/db/installed').read_text())
        # Ordinary unrelated images cannot use the migration to remove kmods.
        oldrows = M['records']((self.old/'lib/apk/db/installed').read_text())
        self.assertEqual(package_replacements(oldrows, oldrows, {'kmod-tun'}), set())
        foreign = record('kmod-unsupported', 'kernel=other-version')
        self.assertEqual(package_replacements({'kernel':oldrows['kernel'], 'kmod-unsupported':foreign},
            M['records']((self.new/'lib/apk/db/installed').read_text()), {'kmod-unsupported'}), set())

    @unittest.skipUnless((TOP/'openwrt/device-install-image.sh').is_file(), 'E5 ordinary-update adapter')
    def test_07_ordinary_update_keeps_native_db_and_user_configuration(self):
        old = self.path/'ordinary-old'; new = self.path/'ordinary-new'
        shutil.copytree(self.old, old); shutil.copytree(self.new, new)
        native_db = (new/'lib/apk/db/installed').read_bytes()
        native_world = (new/'etc/apk/world').read_bytes()
        source = (TOP/'openwrt/device-install-image.sh').read_text()
        start = source.index('keep_config() {')
        function = source[start:source.index('\n}\n',start)+3]
        function = function.replace('/tmp/e5-img.', str(self.path/'ordinary.'))
        subprocess.run(['sh','-ec', function+'\nkeep_config "$1" "$2"',
                        'test',str(old),str(new)],check=True)
        self.assertEqual((new/'lib/apk/db/installed').read_bytes(), native_db)
        self.assertEqual((new/'etc/apk/world').read_bytes(), native_world)
        self.assertEqual((new/'etc/config/wireless').read_text(), 'user password')

    def test_08_sustained_solver_operations_do_not_replace_kernel_or_user_data(self):
        root = self.path/'sustained'; shutil.copytree(self.new, root)
        before = package_versions((root/'lib/apk/db/installed').read_bytes())
        for attempt in range(12):
            self.apk(root, 'add', '--scripts=no', '--commit-hooks=no', 'kmod-tun')
            self.assertEqual(package_versions((root/'lib/apk/db/installed').read_bytes()), before)
            self.assertEqual((root/'etc/config/wireless').read_text(), 'user password')
            self.assertIn('kernel='+self.receipt['package_version'], (root/'etc/apk/world').read_text())

    def test_09_new_native_capability_replaces_a_user_installed_stale_kmod(self):
        old = self.path/'takeover-old'; new = self.path/'takeover-new'; live = self.path/'takeover-live'
        shutil.copytree(self.old, old); shutil.copytree(self.new, new)
        rows = M['records']((old/'lib/apk/db/installed').read_text()); rows.pop('kmod-tun')
        rows['consumer'] = record('consumer', 'ubox')
        put(old, 'lib/apk/db/installed', '\n\n'.join(rows.values())+'\n\n')
        put(old, 'etc/apk/world', 'kernel\nubox\nconsumer\n')
        put(old, f'lib/modules/{KREL}/tun.ko', b'previous native module build')
        archive = self.make_diff(old, new, 3); shutil.copytree(old, live)
        db = live/'lib/apk/db/installed'
        db.write_text(db.read_text()+record('kmod-tun', 'kernel=1.0-r1',
                      'F:lib/modules/'+KREL+'\nR:tun.ko\n')+'\n\n')
        with (live/'etc/apk/world').open('a') as stream: stream.write('kmod-tun=1.0-r1\n')
        put(live, f'lib/modules/{KREL}/tun.ko', b'old user-added module')
        with Patch(archive) as delta:
            self.assertEqual(Engine(live).apply(delta)['status'], 'complete')
        row = M['records'](db.read_text())['kmod-tun']
        self.assertEqual(M['field'](row,'D'), 'kernel='+self.receipt['package_version'])
        self.assertIn('kmod-tun='+self.receipt['package_version'], (live/'etc/apk/world').read_text())
        self.assertEqual((live/f'lib/modules/{KREL}/tun.ko').read_bytes(),
                         (new/f'lib/modules/{KREL}/tun.ko').read_bytes())

    def test_10_docker_capabilities_require_real_modules_and_builtin_evidence(self):
        # A mix of built-ins and modules is normal on the native Pro kernel.
        config = {
            'CONFIG_NETFILTER_XT_MATCH_ADDRTYPE':'y', 'CONFIG_NETFILTER_XT_MATCH_OWNER':'y',
            'CONFIG_NETFILTER_XT_MATCH_PKTTYPE':'y', 'CONFIG_NETFILTER_XT_MATCH_QUOTA':'m',
        }
        builtin = {'xt_addrtype', 'xt_owner', 'xt_pkttype'}
        self.assertIn('kmod-ipt-extra', M['capabilities'](config, {'xt_quota':None}, builtin))
        self.assertNotIn('kmod-ipt-extra', M['capabilities'](config, {}, builtin))
        self.assertNotIn('kmod-ipt-extra', M['capabilities'](config, {'xt_quota':None}, builtin-{'xt_owner'}))
        self.assertNotIn('kmod-ipt-extra', M['capabilities'](dict(config, CONFIG_NETFILTER_XT_MATCH_OWNER='n'),
                                                                  {'xt_quota':None}, builtin))

    def test_11_docker_dependencies_solve_without_a_foreign_kernel_alias(self):
        root = self.path/'docker'; shutil.copytree(self.old, root)
        names = ['ipt-extra', 'ipt-conntrack', 'ipt-nat', 'ipt-nat6', 'ipt-physdev', 'nf-ipvs']
        config = {'TUN':'m'}; builtin = set()
        for name in names:
            flags, modules = M['FEATURES'][name]
            config.update({key:'y' for key in flags.split()})
            builtin.update(modules.split())
        put(root, f'lib/modules/{KREL}/modules.builtin',
            ''.join('kernel/net/'+name+'.ko\n' for name in sorted(builtin)))
        config_path = put(self.path, 'docker.config', ''.join('CONFIG_'+key+'='+value+'\n'
                                                            for key,value in sorted(config.items())))
        receipt = M['stage'](root, config_path, self.image, KREL, 'u30pro', IMAGE)
        self.assertTrue({'kmod-'+name for name in names} <= set(receipt['provides']))
        files = self.path/'docker-files'; files.mkdir()
        archive = root/'docker-dependencies.apk'
        M['apk_file'](files, set(), archive, 'pkgname = docker-kernel-dependencies\npkgver = 1-r1\n'
            'pkgdesc = Docker kernel dependency regression\narch = aarch64_generic\n'
            'license = GPL-2.0-only\nsize = 0\nbuilddate = 0\n'+
            ''.join('depend = kmod-'+name+'\n' for name in names))
        self.apk(root, 'add', '--allow-untrusted', '--scripts=no', '--commit-hooks=no', str(archive))
        rows = M['records']((root/'lib/apk/db/installed').read_text())
        self.assertEqual(M['field'](rows['kernel'],'V'), receipt['package_version'])
        for name in names:
            self.assertEqual(M['field'](rows['kmod-'+name],'D'), 'kernel='+receipt['package_version'])
        self.assertNotIn('u30pro-kernel-', (root/'lib/apk/db/installed').read_text())

    def test_12_proxy_capabilities_do_not_claim_disabled_or_missing_components(self):
        cases = {
            'dummy': ({'CONFIG_DUMMY':'y'}, {'dummy'}),
            'inet-diag': ({'CONFIG_INET_DIAG':'y', 'CONFIG_INET_TCP_DIAG':'y', 'CONFIG_INET_UDP_DIAG':'y'},
                          {'inet_diag', 'tcp_diag', 'udp_diag'}),
            'nf-socket': ({'CONFIG_NF_SOCKET_IPV4':'y', 'CONFIG_NF_SOCKET_IPV6':'y'},
                          {'nf_socket_ipv4', 'nf_socket_ipv6'}),
            'nf-tproxy': ({'CONFIG_NF_TPROXY_IPV4':'y', 'CONFIG_NF_TPROXY_IPV6':'y'},
                          {'nf_tproxy_ipv4', 'nf_tproxy_ipv6'}),
            'nft-socket': ({'CONFIG_NFT_SOCKET':'y'}, {'nft_socket'}),
            'nft-tproxy': ({'CONFIG_NFT_TPROXY':'y'}, {'nft_tproxy'}),
        }
        for name, (config, builtin) in cases.items():
            with self.subTest(capability=name):
                self.assertIn('kmod-'+name, M['capabilities'](config, {}, builtin))
                self.assertNotIn('kmod-'+name, M['capabilities'](config, {}, set()))
                self.assertNotIn('kmod-'+name, M['capabilities']({}, {}, builtin))


if __name__ == '__main__':
    unittest.main(verbosity=2)
