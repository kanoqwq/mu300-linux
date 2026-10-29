"""Private release versions identify source content."""
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path

IDENTIFY = runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/release-identity.py'))['identify']

class ReleaseIdentityTest(unittest.TestCase):
    def test_source_changes_have_distinct_clear_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'user.name', 'fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'commit.gpgsign', 'false')
            source = repo/'source.py'
            source.write_text('before')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            one = IDENTIFY(repo, 't551m', '6.18.54', epoch=1792324800)
            self.assertRegex(one['private_version'], r'^r20261018\.\d{6}-g[0-9a-f]{8}-s[0-9a-f]{8}$')
            self.assertIn('t551m-openwrt-25.12.5-', one['package_name'])
            self.assertTrue(one['package_name'].endswith('-k6.18.54'))
            self.assertFalse(one['source_dirty'])
            source.write_text('after')
            two = IDENTIFY(repo, 't551m', '6.18.54', epoch=1792324800)
            self.assertTrue(two['source_dirty'])
            self.assertNotEqual(one['private_version'], two['private_version'])
            with self.assertRaisesRegex(ValueError, 'Invalid release'):
                IDENTIFY(repo, 'e5', '6.18', version='../unsafe')

if __name__ == '__main__':
    unittest.main()
