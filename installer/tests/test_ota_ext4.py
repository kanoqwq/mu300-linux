"""Use a real small ext4 image; no device, root mounts or full rootfs copies."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.ota_package import analyze
from backend.ota_engine import Patch


class Ext4Tests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt', 'Windows userspace ext4 reader integration with WSL-created fixture')
    def test_e5_ext4_and_symlink_reader(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='ota-test-',dir=repo/'work') as d:
            d=Path(d)
            for i in (1,2):
                release=d/str(i);source=release/'root';(source/'usr/lib').mkdir(parents=True)
                (source/'etc/config').mkdir(parents=True)
                (source/'usr/lib/example').write_bytes(('version '+str(i)).encode())
                (source/'etc/config/network').write_bytes(('user '+str(i)).encode())
                image=release/'root.ext4'
                linux=lambda p:'/mnt/'+p.drive[0].lower()+p.as_posix()[2:]
                subprocess.run(['wsl','-d','Ubuntu-22.04','--','ln','-s','example',linux(source/'usr/lib/link')],check=True,capture_output=True)
                subprocess.run(['wsl','-d','Ubuntu-22.04','--','mke2fs','-q','-t','ext4','-F','-d',linux(source),linux(image),'16M'],check=True,capture_output=True)
                files=release/'files';files.mkdir()
                with image.open('rb') as src,gzip.open(files/'openwrt.ext4.gz','wb') as dest:shutil.copyfileobj(src,dest)
                image.unlink()
                (files/'boot.img').write_bytes(b'same boot')
                (files/'device-profile.json').write_text(json.dumps({'profile':'e5'}))
                (files/'RELEASE.json').write_text(json.dumps(dict(profile='e5',image_version='e5-r'+str(i),kernel_release='6.18.54-e5-test',openwrt='25.12.5')))
                (files/'SHA256SUMS').write_text(''.join(hashlib.sha256((files/n).read_bytes()).hexdigest()+'  files/'+n+'\n' for n in ('boot.img','openwrt.ext4.gz')))
            out=d/'patch.ota';result=analyze(d/'1',d/'2',out)
            self.assertTrue(result['can_export'],result['blockers'])
            self.assertEqual([o['path'] for o in result['manifest']['operations']],['usr/lib/example'])
            with Patch(out) as patch:
                h=result['manifest']['operations'][0]['after']['sha256']
                self.assertEqual(patch.archive.read('blobs/'+h),b'version 2')
            self.assertFalse(any(p.name.startswith('tmp') for p in d.rglob('*')))


if __name__=='__main__':unittest.main()
