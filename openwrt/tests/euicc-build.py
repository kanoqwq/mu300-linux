"""Dependency receipts, target inventory and shipped source consistency fixtures."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

TOP=Path(__file__).resolve().parents[2]
state=runpy.run_path(str(TOP/'openwrt/lpac-state.py'))
stage=runpy.run_path(str(TOP/'openwrt/stage-lpac.py'))


class Artifacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='euicc-build-fixture-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'root';self.root.mkdir()
        self.art=Path(self.temp.name)/'apks';self.art.mkdir()
        self.apk=self.art/'lpac-2.3.0-r1001.apk';self.apk.write_bytes(b'SYNTHETIC TEST ARTIFACT, NOT AN APK')
        (self.art/'build-id').write_text(state['fingerprint']())
        (self.art/'SHA256SUMS').write_text(hashlib.sha256(self.apk.read_bytes()).hexdigest()+'  ./'+self.apk.name+'\n')

    def target(self):
        binary=self.root/'usr/lib/lpac';binary.parent.mkdir(parents=True);binary.write_bytes(b'SYNTHETIC TEST EXECUTABLE')
        db=self.root/'lib/apk/db/installed';db.parent.mkdir(parents=True);db.write_text('P:lpac\nV:2.3.0-r1001\n\n')

    def test_stale_dependency_source_is_rejected(self):
        (self.art/'build-id').write_text('old')
        with self.assertRaisesRegex(ValueError,'source changed'):state['inspect'](self.art)

    def test_changed_artifact_cannot_be_reused(self):
        self.apk.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'):state['inspect'](self.art)

    def test_inventory_checks_the_actual_lpac_driver_schema(self):
        self.target()
        output=json.dumps({'type':'driver','payload':{'LPAC_APDU':['stdio'],'LPAC_HTTP':['curl','stdio']}})
        with patch('subprocess.run') as run,patch('subprocess.check_output',return_value=output):
            result=stage['install'](self.root,self.art,'isolated-test-image')
        self.assertEqual(result['source_fingerprint'],state['fingerprint']())
        self.assertIn('--no-network',run.call_args.args[0][-1])
        self.assertIn('--force-reinstall',run.call_args.args[0][-1])
        self.assertEqual(stage['inspect'](self.root),result)
        (self.root/'usr/lib/lpac').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'binary differs'):stage['inspect'](self.root)

    def test_missing_stdio_transport_does_not_publish_receipt(self):
        self.target()
        with patch('subprocess.run'),patch('subprocess.check_output',return_value='{"type":"driver","payload":{"LPAC_APDU":["pcsc"],"LPAC_HTTP":["curl"]}}'):
            with self.assertRaisesRegex(ValueError,'stdio transports missing'):stage['install'](self.root,self.art,'test-image')
        self.assertFalse((self.root/stage['RECEIPT']).exists())

    def test_archive_must_be_the_reviewed_build(self):
        (self.art/'SHA256SUMS').write_text('0'*64+'  lpac-2.3.0-r1.apk\n')
        with self.assertRaisesRegex(ValueError,'Unexpected'):state['inspect'](self.art)


if __name__=='__main__':unittest.main(verbosity=2)
