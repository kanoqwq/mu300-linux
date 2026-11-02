"""Verify the real signed feed and add-ons in disposable package databases.

Inputs are the separately downloaded, checksum-checked upstream fixtures.
No target device, prepared root writes, package scripts or network access.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
ROOT=Path('/home/kano/openwrt-build/package-work/u30pro-release/root')
FIXTURES=Path(os.environ.get('U30PRO_FEED_FIXTURES',TOP/'work/u30pro-runtime/feed-verification'))
AVAILABLE=os.name=='posix' and ROOT.exists() and (FIXTURES/'selected.json').exists()


@unittest.skipUnless(AVAILABLE,'prepared root and separately downloaded upstream feed fixtures')
class Feed(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='u30pro-feed-',dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)
        metadata=json.loads((FIXTURES/'selected.json').read_text())
        for row in metadata['packages']:
            data=(FIXTURES/row['file']).read_bytes()
            self.assertEqual(len(data),row['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(),row['sha256'])
            (self.repo/row['file']).write_bytes(data)
        shutil.copyfile(FIXTURES/'packages.adb',self.repo/'packages.adb')
        (self.repo/'empty-keys').mkdir()

    def apk(self,command):
        return subprocess.run(['docker','run','--rm','--platform','linux/arm64',
            '-v',str(ROOT)+':/target:ro','-v',str(self.repo)+':/fixtures:ro',
            '--entrypoint','/bin/sh','mu300-openwrt-base:25.12.5','-ec',command],
            capture_output=True,text=True)

    def install(self,keys='/target/etc/apk/keys'):
        return self.apk('apk --root /sandbox --initdb --arch aarch64_generic --keys-dir '+keys+
            ' --repositories-file /dev/null --repository /fixtures/packages.adb --no-network '
            '--scripts=no --commit-hooks=no add u30pro-screen-data u30pro-screen-clients u30pro-screen-thermal')

    def test_signed_index_installs_real_addons_without_allow_untrusted(self):
        result=self.apk('apk --keys-dir /target/etc/apk/keys verify /fixtures/packages.adb')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=self.install()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('3 packages',result.stdout)

    def test_modified_package_is_rejected_against_authenticated_index(self):
        path=self.repo/'u30pro-screen-data-1.0-r1.apk'
        data=bytearray(path.read_bytes());data[-5]^=1;path.write_bytes(data)
        result=self.install()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)

    def test_missing_trusted_key_rejects_the_repository(self):
        result=self.install('/fixtures/empty-keys')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertRegex(result.stdout+result.stderr,r'(?i)trust|signature')


if __name__=='__main__':unittest.main()
