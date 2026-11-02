"""Apply actual apk alternative manifests to a private synthetic root."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('package_u30pro', TOP/'tools/u30pro-package.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)

class Alternatives(unittest.TestCase):
    def test_priority_multiple_records_and_target_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('lib/apk/packages','usr/libexec','bin','sbin'):(root/name).mkdir(parents=True,exist_ok=True)
            (root/'bin/busybox').write_text('busybox')
            (root/'usr/libexec/ip-full').write_text('iproute2')
            (root/'usr/libexec/iw-full').write_text('nl80211')
            (root/'sbin/ip').symlink_to('../bin/busybox')
            (root/'lib/apk/packages/busybox.alternatives').write_text('100:/sbin/ip:/bin/busybox\n')
            (root/'lib/apk/packages/full.alternatives').write_text('300:/sbin/ip:/usr/libexec/ip-full 300:/sbin/iw:/usr/libexec/iw-full\n')
            package.apply_alternatives(root)
            self.assertEqual((root/'sbin/ip').read_text(),'iproute2')
            self.assertEqual((root/'sbin/iw').read_text(),'nl80211')
            package.apply_alternatives(root)
            self.assertEqual((root/'sbin/ip').read_text(),'iproute2')
            (root/'lib/apk/packages/bad.alternatives').write_text('900:/sbin/missing:/usr/libexec/missing\n')
            with self.assertRaises(ValueError):package.apply_alternatives(root)
            (root/'lib/apk/packages/bad.alternatives').write_text('900:/../../escape:/bin/busybox\n')
            with self.assertRaises(ValueError):package.apply_alternatives(root)

if __name__=='__main__':unittest.main()
