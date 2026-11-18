"""Real MU300 diff generation/application with file-backed boot partitions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from helpers import TOP

sys.path.insert(0,str(TOP/'installer/tests'))
from test_ota import mu_release, analyze, Engine, Patch
from test_ota_full import boot_image


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'POSIX root required for real OTA ownership changes')
class AudioMemoryDelivery(unittest.TestCase):
    def test_changed_image_with_same_kernel_label_reaches_linux_slot_and_keeps_plugin(self):
        for slot in ('a','b'):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);live=root/'live'
                for name in ('etc/mu300','etc/config','dev/block/by-name','lib/modules/7.2.8','usr/lib/e5-sip/mu300-audio'):
                    (live/name).mkdir(parents=True,exist_ok=True)
                (live/'etc/mu300/release.json').write_text(json.dumps({
                    'profile':'mu300','image_version':'mu300-r1','kernel_release':'7.2.8'}))
                (live/'etc/mu300/linux-slot').write_text(slot+'\n')
                old=boot_image(slot,kernel=b'old Image',family='mu300')
                new_kernel=b'new Image containing early AUDCP memory reservation'
                new=boot_image('b' if slot=='a' else 'a',kernel=new_kernel,family='mu300')
                boot=live/('dev/block/by-name/boot_'+slot);boot.write_bytes(old)
                android=live/('dev/block/by-name/boot_'+('b' if slot=='a' else 'a'))
                android.write_bytes(b'Android must stay unchanged')
                module='lib/modules/7.2.8/driver.ko'
                (live/module).write_bytes(b'old module')
                (live/'etc/config/wireless').write_bytes(b'user wireless config')
                dsp=live/'usr/lib/e5-sip/mu300-audio/agdsp.bin';dsp.write_bytes(b'plugin DSP')
                mu_release(root/'old','mu300-r1',{module:b'old module'},boot=old)
                mu_release(root/'new','mu300-r2',{module:b'matching new module'},boot=new)
                output=root/'memory.ota';result=analyze(root/'old',root/'new',output)
                self.assertTrue(result['can_export'],result['blockers'])
                with Patch(output) as patch:
                    self.assertIn('boot',patch.manifest)
                    Engine(live).apply(patch)
                self.assertEqual(boot.read_bytes()[4096:4096+len(new_kernel)],new_kernel)
                self.assertEqual((live/module).read_bytes(),b'matching new module')
                self.assertEqual(android.read_bytes(),b'Android must stay unchanged')
                self.assertEqual((live/'etc/config/wireless').read_bytes(),b'user wireless config')
                self.assertEqual(dsp.read_bytes(),b'plugin DSP')


if __name__=='__main__':unittest.main()
