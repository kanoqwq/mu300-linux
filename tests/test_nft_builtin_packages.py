"""Built-in nft declarations: config guards, package ownership, and no-apply live checks (#470)."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

from helpers import TOP, ShellTest

SOURCE = TOP / 'openwrt/nft-builtins'
spec = importlib.util.spec_from_file_location('nft_builtin_build', SOURCE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Packages(ShellTest):
    def setUp(self):
        super().setUp()
        self.output = self.tmp / 'packages'
        self.config = self.tmp / 'kernel.config'
        self.config.write_text('CONFIG_NFT_SOCKET=y\nCONFIG_NFT_TPROXY=y\nCONFIG_INET=y\nCONFIG_IPV6=y\n')
        self.fake = self.tmp / 'host apk'
        self.fake.write_text('#!' + sys.executable + '\n' + r'''
import json, os, pathlib, sys
a=sys.argv[1:]
stage=pathlib.Path(a[a.index('--files')+1])
guard=pathlib.Path(a[a.index('--script')+1].split(':',1)[1])
data={'argv':a,'epoch':os.environ.get('SOURCE_DATE_EPOCH'),
      'files':{str(p.relative_to(stage)):p.read_text() for p in stage.rglob('*') if p.is_file()},
      'guard':guard.read_text()}
pathlib.Path(a[a.index('--output')+1]).write_text(json.dumps(data))
''')
        self.fake.chmod(0o755)

    def test_package_payloads_have_owned_markers_and_lists_but_no_modules(self):
        builder.build(self.fake, [TOP / 'kernel/linux.config', TOP / 'upstream/mu300-mainline.config'],
                      self.output, '2026.11.02-r1')
        for feature, symbols in builder.FEATURES.items():
            name = 'kmod-nft-' + feature
            data = json.loads((self.output / (name + '-2026.11.02-r1.apk')).read_text())
            marker = 'usr/share/mu300/nft-builtins/' + name
            listing = 'lib/apk/packages/' + name + '.list'
            self.assertEqual(set(data['files']), {marker, listing})
            self.assertEqual(data['files'][listing], '/' + marker + '\n')
            self.assertTrue(all(s + '=y' in data['files'][marker] for s in symbols))
            self.assertEqual(data['epoch'], '0')
            self.assertIn('name:' + name, data['argv'])
            self.assertNotIn('@FEATURE@', data['guard'])

    def test_any_unsupported_kernel_config_stops_before_building_packages(self):
        for key in ('CONFIG_NFT_SOCKET', 'CONFIG_NFT_TPROXY', 'CONFIG_INET', 'CONFIG_IPV6'):
            text = self.config.read_text()
            for value in ('m', 'n'):
                self.config.write_text(text.replace(key + '=y', key + '=' + value))
                with self.assertRaisesRegex(ValueError, key):
                    builder.build(self.fake, [self.config], self.output, '1-r0')
                self.assertFalse(self.output.exists())
            self.config.write_text(text)

    def test_resolved_config_requires_every_family_helper(self):
        text = self.config.read_text() + ''.join(k + '=y\n' for keys in builder.FEATURES.values() for k in keys[1:])
        for keys in builder.FEATURES.values():
            for key in keys:
                self.config.write_text(text.replace(key + '=y', key + '=m'))
                with self.assertRaisesRegex(ValueError, key):
                    builder.build(self.fake, [], self.output, '1-r0', resolved=[self.config])
                self.assertFalse(self.output.exists())

    def live(self, shell, feature, config, nft_rc=0, gzip_rc=0):
        guard = self.tmp / 'pre-install'
        guard.write_text((SOURCE / 'pre-install.sh.in').read_text().replace('@FEATURE@', feature)
                         .replace('@SYMBOLS@', ' '.join(builder.FEATURES[feature])))
        self.stub('gzip', 'printf "%s\\n" "$TEST_CONFIG"; exit "$TEST_GZIP_RC"')
        self.stub('nft', 'printf "%s\\n" "$*" > "$STUBLOG/nft-args"\n'
                        '[ "$*" = "--check -f -" ] || exit 90\n'
                        'cat > "$STUBLOG/nft-input"\nexit "$TEST_NFT_RC"')
        for name in ('nft-args', 'nft-input'):
            (self.tmp / name).unlink(missing_ok=True)
        return self.script(shell, guard, TEST_CONFIG=config, TEST_NFT_RC=str(nft_rc), TEST_GZIP_RC=str(gzip_rc))

    def test_live_install_checks_socket_and_both_tproxy_protocols_without_applying(self):
        for shell in self.each_shell():
            for feature, keys in builder.FEATURES.items():
                config = '\n'.join(k + '=y' for k in keys)
                result = self.live(shell, feature, config)
                self.assertEqual(result.returncode, 0, result.stderr)
                rules = (self.tmp / 'nft-input').read_text()
                if feature == 'socket':
                    self.assertIn('meta l4proto tcp socket transparent 1', rules)
                else:
                    self.assertIn('meta l4proto tcp tproxy to :1041', rules)
                    self.assertIn('meta l4proto udp tproxy to :1041', rules)
                self.assertNotIn('flush', rules)
                self.assertNotIn('delete', rules)

    def test_missing_config_helpers_or_runtime_support_refuse_install(self):
        for shell in self.each_shell():
            for feature, keys in builder.FEATURES.items():
                config = '\n'.join(k + '=y' for k in keys)
                for key in keys:
                    result = self.live(shell, feature, config.replace(key + '=y', key + '=m'))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.tmp / 'nft-args').exists())
                self.assertNotEqual(self.live(shell, feature, config, gzip_rc=1).returncode, 0)
                self.assertNotEqual(self.live(shell, feature, config, nft_rc=1).returncode, 0)


if __name__ == '__main__':
    unittest.main()
