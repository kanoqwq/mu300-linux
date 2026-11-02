"""Offline port-boundary regressions; never open a device or live NV file."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boot = module('boot_contract', TOP / 'devices/u30pro/boot_contract.py')
build = module('u30pro_build', TOP / 'tools/u30pro-build.py')
imports = module('u30pro_import', TOP / 'tools/import-u30pro-reference.py')


class BootLayout(unittest.TestCase):
    def setUp(self):
        names = ['misc', 'boot_a', 'vendor_boot_a', 'init_boot_a',
                 'boot_b', 'vendor_boot_b', 'init_boot_b', 'userdata', 'mu300root']
        self.parts = {name: {'first_lba': 1000 + i * 20000, 'sectors': 10000}
                      for i, name in enumerate(names)}

    def test_android_b_kept_and_a_targeted(self):
        result = boot.plan('_b', 512, self.parts)
        self.assertEqual((result['android_slot'], result['linux_slot']), ('b', 'a'))
        self.assertEqual([i['target'] for i in result['images']], ['boot_a', 'vendor_boot_a', 'init_boot_a'])
        self.assertEqual(result['firmware_slot'], 'b')
        self.assertEqual(result['nv_write_windows'], [])

    def test_android_a_kept_and_b_targeted(self):
        result = boot.plan('a', 512, self.parts)
        self.assertTrue(all(i['target'].endswith('_b') for i in result['images']))

    def test_4k_gpt_units_converted_to_linux_sectors(self):
        result = boot.plan('b', 4096, self.parts)
        self.assertIn((self.parts['boot_a']['first_lba'] * 8, 10000 * 8), result['write_windows_512'])
        self.assertIn((self.parts['misc']['first_lba'] * 8 + 4, 1), result['write_windows_512'])

    def test_android_boot_and_userdata_never_in_windows(self):
        result = boot.plan('b', 512, self.parts)
        for name in ('boot_b', 'vendor_boot_b', 'init_boot_b', 'userdata'):
            first = self.parts[name]['first_lba']
            for start, count in result['write_windows_512']:
                self.assertFalse(start <= first < start + count, name)

    def test_ram_trial_does_not_require_overlay(self):
        del self.parts['mu300root']
        self.assertIsNone(boot.plan('a', 512, self.parts)['root_partition'])

    def test_unknown_slot_never_defaults_to_a(self):
        for value in (None, '', 'slot_a', 'c'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                boot.plan(value, 512, self.parts)

    def test_overlap_missing_and_bad_geometry_rejected(self):
        for change in ('overlap', 'missing', 'negative', 'boolean', 'non-record'):
            parts = copy.deepcopy(self.parts)
            if change == 'overlap':
                parts['boot_a']['first_lba'] = parts['misc']['first_lba']
            elif change == 'missing':
                del parts['init_boot_b']
            elif change == 'negative':
                parts['boot_b']['sectors'] = -1
            elif change == 'boolean':
                parts['boot_b']['sectors'] = True
            else:
                parts['boot_b'] = None
            with self.subTest(change=change), self.assertRaises(ValueError):
                boot.plan('a', 512, parts)


class PortBoundaries(unittest.TestCase):
    def test_sources_match_reviewed_origin_and_local_adaptations(self):
        result = build.check_sources()
        self.assertGreater(result['files'], 400)
        self.assertFalse(result['hardware_verified'])

    def test_do_not_import_old_flashers_upgrade_or_credentials(self):
        for path in ('tools/spd_slot.py', 'tools/ab_update.py', 'tools/make_flash_kit.py',
                     'ui/native-control/overlay/usr/sbin/u30pro-slot-update',
                     'ui/native-control/overlay/usr/sbin/u30pro-overlay',
                     'ui/native-control/overlay/usr/sbin/u30pro-cfg-restore',
                     'ui/native-control/overlay/etc/u30pro/ssh_authorized_keys',
                     'ui/native-control/overlay/www/luci-static/resources/u30pro/modem.js'):
            self.assertFalse(imports.selected(path), path)

    def test_corrupt_import_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'native').mkdir()
            (root / 'native/sample.c').write_text('wrong', encoding='utf8')
            (root / 'reference.json').write_text(json.dumps({'format': 1, 'revision': build.PIN,
                'files': {'sample.c': {'sha256': '0' * 64}}}), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'differs'):
                build.check_sources(root)

    def test_layout_and_kernel_recipe_cannot_select_mu300(self):
        workspace, paths = build.load_layout(TOP / 'tools/build-layout.json')
        self.assertEqual(str(workspace), '/home/kano/openwrt-build')
        command = build.kernel_command(paths, 2)
        self.assertIn('hardware-native-kexec-modem', command)
        self.assertIn('CONFIG_UNISOC_AIC8800=m', command)
        self.assertNotIn('7.2', ' '.join(command))
        with self.assertRaises(ValueError):
            build.kernel_command(paths, 0)


if __name__ == '__main__':
    unittest.main()
