"""Reject release images whose embedded config or packaged module set is incomplete."""
import gzip
import importlib.util
from pathlib import Path
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_audit', TOP / 'upstream/audit-bundle.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class BundleAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.fragment = self.out / 'fragment'
        self.fragment.write_text('CONFIG_KVM=y\nCONFIG_VHOST_NET=m\nCONFIG_KEXEC=n\n')
        self.config = 'CONFIG_KVM=y\nCONFIG_VHOST_NET=m\n# CONFIG_KEXEC is not set\n'
        self.image(self.config)
        (self.out / 'modules').mkdir()
        (self.out / 'modules/vhost_net.ko').write_bytes(b'module')
        (self.out / 'modules.in-tree').write_text('vhost_net.ko\n')

    def image(self, config):
        (self.out / 'Image').write_bytes(b'headerIKCFG_ST' + gzip.compress(config.encode()) + b'IKCFG_EDtail')

    def test_complete_build(self):
        audit.audit(self.out, self.fragment)
        self.assertEqual((self.out / 'kernel.config').read_text(), self.config)

    def test_stale_image_without_kvm(self):
        self.image(self.config.replace('CONFIG_KVM=y', '# CONFIG_KVM is not set'))
        with self.assertRaisesRegex(ValueError, 'CONFIG_KVM'):
            audit.audit(self.out, self.fragment)

    def test_missing_module(self):
        (self.out / 'modules/vhost_net.ko').unlink()
        with self.assertRaisesRegex(ValueError, 'missing in-tree module'):
            audit.audit(self.out, self.fragment)

    def test_missing_embedded_config(self):
        (self.out / 'Image').write_bytes(b'old kernel')
        with self.assertRaisesRegex(ValueError, 'no embedded'):
            audit.audit(self.out, self.fragment)

    def test_disabled_option_is_rejected_when_enabled(self):
        self.image(self.config.replace('# CONFIG_KEXEC is not set', 'CONFIG_KEXEC=y'))
        with self.assertRaisesRegex(ValueError, 'CONFIG_KEXEC'):
            audit.audit(self.out, self.fragment)

    def test_option_a_kernel_does_not_have(self):
        # config-ignored.txt (7.3 folded PROC_SYSCTL into SYSCTL, FINDINGS 206): passes only where the kernel has
        # no such symbol at all; a kernel that knows it and has it off still fails
        self.fragment.write_text('CONFIG_KVM=y\nCONFIG_PROC_SYSCTL=y\n')
        with self.assertRaisesRegex(ValueError, 'CONFIG_PROC_SYSCTL'):
            audit.audit(self.out, self.fragment)
        (self.out / 'config-ignored.txt').write_text('# comment\nCONFIG_PROC_SYSCTL\n')
        audit.audit(self.out, self.fragment)
        self.image(self.config + '# CONFIG_PROC_SYSCTL is not set\n')
        with self.assertRaisesRegex(ValueError, 'CONFIG_PROC_SYSCTL'):
            audit.audit(self.out, self.fragment)

    def test_listed_options_of_the_repository(self):
        # what config-ignored.txt may list: options of the fragment, each with a reason above it
        lines = (TOP / 'upstream/config-ignored.txt').read_text().splitlines()
        names = [l for l in lines if l.startswith('CONFIG_')]
        fragment = {l.split('=', 1)[0] for l in (TOP / 'upstream/mu300-mainline.config').read_text().splitlines()}
        self.assertEqual(names, ['CONFIG_PROC_SYSCTL'])
        self.assertTrue(set(names) <= fragment)
        self.assertIn('CONFIG_SYSCTL', fragment)
        self.assertTrue(all(l.startswith('#') or l.startswith('CONFIG_') for l in lines if l))

    def test_missing_manifest(self):
        (self.out / 'modules.in-tree').unlink()
        with self.assertRaises(FileNotFoundError):
            audit.audit(self.out, self.fragment)
