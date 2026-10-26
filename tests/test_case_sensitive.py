"""Kernel modules whose names differ only in case (FINDINGS 212): xt_HL.ko and xt_hl.ko, xt_TCPMSS.ko and
xt_tcpmss.ko, xt_DSCP.ko and xt_dscp.ko, xt_RATEEST.ko and xt_rateest.ko. A tree on a file system that ignores case
keeps one file of each pair; the release scripts refuse such a directory, the audits find a bundle that lost one,
and the installers take a mainline kernel's modules from its tarball, never from a tree unpacked on the host."""
import importlib.util
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
PAIRS = ('xt_HL.ko', 'xt_hl.ko', 'xt_TCPMSS.ko', 'xt_tcpmss.ko', 'xt_DSCP.ko', 'xt_dscp.ko', 'xt_RATEEST.ko',
         'xt_rateest.ko')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, TOP / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def folds(d):
    """whether d ignores case, asked the same way case_sensitive does"""
    probe = Path(tempfile.mkdtemp(dir=d))
    try:
        (probe / 'mu300-case').write_text('')
        (probe / 'MU300-CASE').write_text('')
        return len(os.listdir(probe)) != 2
    finally:
        for p in probe.iterdir():
            p.unlink()
        probe.rmdir()


def add(t, name, data=b''):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    t.addfile(info, io.BytesIO(data))


@unittest.skipIf(os.name == 'nt', 'the release scripts run on a Unix host')
class CaseSensitive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def run_check(self, path=None):
        env = dict(os.environ)
        if path:
            env['PATH'] = path + os.pathsep + env['PATH']
        return subprocess.run(['sh', '-c', '. "$1" && case_sensitive "$2"', 'sh', str(TOP / 'upstream/case-sensitive.sh'),
                               str(self.d)], capture_output=True, text=True, env=env)

    def test_the_host_directory(self):
        # whatever this host's temporary directory is, the check says the same as Python does, and leaves nothing
        r = self.run_check()
        self.assertEqual(r.returncode, 1 if folds(self.d) else 0, r.stderr)
        self.assertEqual(os.listdir(self.d), [])

    def test_a_directory_that_ignores_case(self):
        # a file system that keeps one of the two names, made here with an ls that shows one
        bin_ = self.d / 'bin'
        bin_.mkdir()
        (bin_ / 'ls').write_text('#!/bin/sh\necho mu300-case\n')
        (bin_ / 'ls').chmod(0o755)
        self.d = self.d / 'stage'
        self.d.mkdir()
        r = self.run_check(str(bin_))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f'{self.d} is on a file system that ignores case', r.stderr)
        self.assertIn("-fs 'Case-sensitive APFS'", r.stderr)
        self.assertIn('FINDINGS.md 212', r.stderr)
        self.assertEqual(os.listdir(self.d), [])

    def test_a_directory_it_cannot_write(self):
        self.d = self.d / 'missing'
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn('cannot make a directory in', r.stderr)


class Scripts(unittest.TestCase):
    def test_the_release_scripts_check_and_honour_tmpdir(self):
        bundle = (TOP / 'upstream/make-bundle.sh').read_text()
        self.assertIn('W=$(mktemp -d "${TMPDIR:-/tmp}/mu300-bundle.XXXXXX")', bundle)
        self.assertIn('case_sensitive "$UO/modules" || exit 1', bundle)
        self.assertIn('case_sensitive "$W" || exit 1', bundle)
        self.assertLess(bundle.index('case_sensitive "$W"'), bundle.index('tar -xzf "$REF"'))
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('case_sensitive "$D" || exit 1', rel)
        self.assertIn('case_sensitive "${TMPDIR:-/tmp}" || exit 1', rel)
        self.assertLess(rel.index('case_sensitive "$D"'), rel.index('echo "==> helper binaries"'))
        mods = (TOP / 'upstream/build-modules.sh').read_text()
        self.assertLess(mods.index('case_sensitive $OUT/modules || exit 1'), mods.index('modules_install'))
        for f in ('tools/make-release.sh', 'tools/make-magisk-zips.sh', 'tools/make-extra.sh', 'tools/sign-release.sh',
                  'upstream/make-bundle.sh'):
            src = (TOP / f).read_text()
            self.assertNotRegex(src, r'mktemp( -d)?\)', f)

    def test_the_installers_take_the_tarball(self):
        sh = (TOP / 'install.sh').read_text()
        self.assertIn('echo --kernel-bundle "$ASSETS/mu300-kernel-$KERNEL.tar.gz"', sh)
        self.assertNotIn('--kernel-bundle "$KMAIN"', sh)
        ps = (TOP / 'install.ps1').read_text()
        self.assertIn("$argv += @('--kernel-bundle', \"$ASSETS\\mu300-kernel-$KERNEL.tar.gz\")", ps)
        self.assertNotIn("'--kernel-bundle', $KMAIN", ps)


class VendorOverlay(unittest.TestCase):
    def test_both_modules_of_a_pair_from_the_tarball(self):
        vo = load('vendor_overlay', 'tools/vendor-overlay.py')
        with tempfile.TemporaryDirectory() as d:
            bundle = Path(d) / 'mu300-kernel-6.18.tar.gz'
            with tarfile.open(bundle, 'w:gz') as t:
                add(t, './kernel.release', b'6.18.57-mu300\n')
                add(t, './modules.builtin', b'kernel/x.ko\n')
                add(t, './Image', b'kernel')
                for m in PAIRS + ('vhost_net.ko',):
                    add(t, f'./modules/{m}', m.encode())
            for os_name, sub in (('ubuntu', 'usr/lib/modules/6.18.57-mu300/extra'), ('openwrt', 'lib/modules/6.18.57-mu300')):
                out = io.BytesIO()
                with tarfile.open(fileobj=out, mode='w') as t:
                    krel, n = vo.add_kernel_bundle(t, bundle, os_name, set())
                self.assertEqual((krel, n), ('6.18.57-mu300', len(PAIRS) + 1))
                out.seek(0)
                with tarfile.open(fileobj=out) as t:
                    for m in PAIRS:
                        self.assertEqual(t.extractfile(f'{sub}/{m}').read(), m.encode(), m)
                    names = t.getnames()
                self.assertNotIn(f'{sub}/Image', names)
                self.assertEqual(os_name == 'ubuntu', 'usr/lib/modules/6.18.57-mu300/modules.builtin' in names)


class BundleAudit(unittest.TestCase):
    def test_a_build_that_lost_one_of_a_pair(self):
        # the tree as a file system that ignores case leaves it: modules.in-tree names both, the directory has one
        # (is_file() of the other is true there, so the audit asks the listing)
        audit = load('bundle_audit_case', 'upstream/audit-bundle.py')
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'Image').write_bytes(b'IKCFG_ST' + __import__('gzip').compress(b'CONFIG_KVM=y\n') + b'IKCFG_ED')
            (out / 'fragment').write_text('CONFIG_KVM=y\n')
            (out / 'modules').mkdir()
            (out / 'modules/xt_HL.ko').write_bytes(b'module')
            (out / 'modules.in-tree').write_text('xt_HL.ko\nxt_hl.ko\n')
            with self.assertRaisesRegex(ValueError, r'missing in-tree module: xt_hl\.ko \(only xt_HL\.ko'):
                audit.audit(out, out / 'fragment')


@unittest.skipIf(os.name == 'nt', 'make-release.sh runs on a Unix host')
class ReleaseAudit(unittest.TestCase):
    """the audit's own lines of make-release.sh, run against bundles made here (in a tarball: a host that ignores
    case could not hold both names of a pair in a tree)"""

    def setUp(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        start = rel.index('# every module a mainline build made')
        self.audit = rel[start:rel.index("# Call audio is not supported:", start)]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def bundle(self, name, mods, in_tree=None):
        with tarfile.open(self.d / f'{name}.tar.gz', 'w:gz') as t:
            add(t, './Image')
            if in_tree is not False:
                add(t, './modules.in-tree', ''.join(f'{m}\n' for m in (in_tree or mods)).encode())
            for m in mods:
                add(t, f'./modules/{m}')

    def run_audit(self):
        r = subprocess.run(['sh', '-c', 'fail=0\n' + self.audit + 'echo "fail=$fail"'],
                           env={'D': str(self.d), 'PATH': os.environ['PATH']}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_pairs(self):
        full = PAIRS + ('vhost_net.ko', 'sprd_wlan_combo.ko')
        for v in ('6.18', '7.2', '7.3'):
            self.bundle(f'mu300-kernel-{v}', full)
        self.assertIn('fail=0', self.run_audit())
        self.assertEqual(sorted(os.listdir(self.d)), [f'mu300-kernel-{v}.tar.gz' for v in ('6.18', '7.2', '7.3')])
        # a bundle like those before v2026.10.23.1: modules.in-tree names both, one file of each pair is there
        self.bundle('mu300-kernel-7.2', ('xt_DSCP.ko', 'xt_hl.ko', 'xt_TCPMSS.ko', 'xt_RATEEST.ko', 'vhost_net.ko'), full)
        out = self.run_audit()
        self.assertIn('fail=1', out)
        for m in ('xt_HL.ko', 'xt_dscp.ko', 'xt_tcpmss.ko', 'xt_rateest.ko', 'sprd_wlan_combo.ko'):
            self.assertIn(f'mu300-kernel-7.2 lacks modules/{m}\n', out)
        self.assertNotIn('lacks modules/xt_hl.ko', out)
        self.assertNotIn('mu300-kernel-6.18 lacks', out)
        # a modules.in-tree that lost the pair as well: the TTL and MSS pairs are named on their own
        self.bundle('mu300-kernel-7.2', ('xt_hl.ko', 'xt_TCPMSS.ko'))
        out = self.run_audit()
        self.assertIn('mu300-kernel-7.2 lacks modules/xt_HL.ko\n', out)
        self.assertIn('mu300-kernel-7.2 lacks modules/xt_tcpmss.ko\n', out)
        # no modules.in-tree at all
        self.bundle('mu300-kernel-7.2', full, in_tree=False)
        self.assertIn('mu300-kernel-7.2 has no modules.in-tree', self.run_audit())


if __name__ == '__main__':
    unittest.main()
