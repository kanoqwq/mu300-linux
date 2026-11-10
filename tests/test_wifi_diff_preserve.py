"""Run the pinned OTA engine against user-modified wireless configuration."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

TOP = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOP/'installer'))
from backend.ota_engine import Engine, Patch, FORMAT, canonical, descriptor


@unittest.skipUnless(sys.platform=='linux','OTA filesystem semantics require Linux')
class WirelessDiff(unittest.TestCase):
    def test_existing_wireless_survives_diffs_and_new_config_defaults(self):
        for family,profile in (('e5','t551m'),('mu300','mu300'),('mu300','u30pro'),('mu300','f50pro')):
            for default in (False,True):
                with self.subTest(profile=profile,new_default=default),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder)
                    (root/f'etc/{family}').mkdir(parents=True)
                    (root/f'etc/{family}/release.json').write_text(json.dumps(dict(profile=profile,
                        image_version=profile+'-r1',kernel_release='fixture-kernel')))
                    (root/'etc/config').mkdir()
                    saved=b"config wifi-iface 'user_ap'\n option ssid 'My AP'\n option encryption 'sae-mixed'\n option key 'user!key\\with-specials'\n"
                    wireless=root/'etc/config/wireless';wireless.write_bytes(saved)
                    (root/'usr/lib').mkdir(parents=True)
                    code=root/'usr/lib/test';code.write_bytes(b'old')
                    before=descriptor(code);after=dict(before,sha256=hashlib.sha256(b'new').hexdigest(),size=3)
                    ops=[dict(path='usr/lib/test',before=before,after=after)]
                    blobs={after['sha256']:b'new'}
                    if default:
                        raw=b'factory WiFi password'
                        value=dict(descriptor(wireless),sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
                        ops.append(dict(path='etc/config/wireless',before=None,after=value,merge='default'))
                        blobs[value['sha256']]=raw
                    # The user also modified a system file. Keep the existing
                    # permissive update/backup behavior; do not demand stock bytes.
                    code.write_bytes(b'local hotfix')
                    manifest=dict(format=FORMAT,family=family,profile=profile,base_version=profile+'-r1',
                        target_version=profile+'-r2',kernel_release='fixture-kernel',operations=ops)
                    archive=root/'patch.ota'
                    with zipfile.ZipFile(archive,'w') as z:
                        z.writestr('manifest.json',canonical(manifest))
                        for name,content in blobs.items():z.writestr('blobs/'+name,content)
                    engine=Engine(root)
                    with Patch(archive) as patch:
                        self.assertTrue(engine.inspect(patch)['applicable'])
                        self.assertEqual(engine.apply(patch)['status'],'complete')
                    self.assertEqual(wireless.read_bytes(),saved)
                    self.assertEqual(code.read_bytes(),b'new')
                    journal=json.loads((engine.state/'journal.json').read_text())
                    self.assertIn(b'local hotfix',[p.read_bytes() for p in
                        (engine.state/journal['backup']).iterdir() if p.is_file()])


if __name__=='__main__':unittest.main()
