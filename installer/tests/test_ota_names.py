"""Export names identify hardware, OpenWrt/kernel version and update direction."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.ota_package import patch_filename


class NameTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(profile='t551m', openwrt='25.12.5', kernel_release='6.18.54-t551m-otg10',
                        image_version='t551m-r20261019.163549-g85eabf34-s858382c8')
        self.new = dict(self.old, image_version='t551m-r20261021.074310-g99570375-sa38c8179')

    def name(self, path='renamed.zip'):
        return patch_filename(self.old, self.new, path)

    def test_model_versions_and_direction_come_from_metadata(self):
        self.assertEqual(self.name(), 'OpenWrt-patch-T551M-25.12.5-k6.18.54-'
                         '20261019.163549-g85eabf34-to-20261021.074310-g99570375.zip')

    def test_every_model_has_a_distinct_readable_label(self):
        for profile, label in [('e5','E5'), ('t551m','T551M'), ('m8','M8'),
                               ('t512n','T512N'), ('yuanhang41s','Yuanhang41S'), ('k1','K1'),
                               ('f50','F50'), ('v50','V50'), ('u30air','U30Air'),
                               ('mu300','MU300-F50-V50-U30Air')]:
            with self.subTest(profile=profile):
                self.new['profile'] = profile
                self.assertTrue(self.name().startswith('OpenWrt-patch-'+label+'-25.12.5-'))

    def test_op_version_transition_is_explicit(self):
        self.new['openwrt'] = '25.12.6'
        self.assertIn('-25.12.5-to-25.12.6-k6.18.54-',self.name())

    def test_mu300_kernel_variants_have_different_names(self):
        self.new['profile'] = 'mu300'
        a = self.name()
        self.new['kernel_release'] = '7.2.8'
        b = self.name()
        self.assertNotEqual(a, b)
        self.assertIn('-k7.2.8-', b)

    def test_unknown_model_uses_its_profile(self):
        self.new['profile'] = 'future-model'
        self.assertIn('-future-model-25.12.5-', self.name())

    def test_legacy_metadata_falls_back_to_new_release_leaf(self):
        for key in ('image_version',):
            for meta in (self.old, self.new):
                before = meta.pop(key)
                for path in (r'D:\release\E5-openwrt-25.12.5-custom.zip',
                             '/Users/test/E5-openwrt-25.12.5-custom.ZIP'):
                    self.assertEqual(self.name(path),'OpenWrt-patch-E5-openwrt-25.12.5-custom.zip')
                meta[key] = before

    def test_empty_or_invalid_fallback_never_blocks_export(self):
        self.old['image_version'] = 'legacy'
        self.assertEqual(self.name('.zip'), 'OpenWrt-patch.zip')
        self.assertEqual(self.name('/release/new:bad?.zip'), 'OpenWrt-patch-new_bad_.zip')

    def test_long_names_fit_a_filesystem_component(self):
        self.old['image_version'] = 'r20261019-g'+'a'*64
        self.new['image_version'] = 'r20261021-g'+'b'*64
        self.assertLessEqual(len(self.name().encode()),255)
        self.old['image_version'] = 'legacy'
        self.assertLessEqual(len(self.name('新'*300+'.zip').encode()),255)
