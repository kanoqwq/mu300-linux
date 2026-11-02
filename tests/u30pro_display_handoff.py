"""Offline service ownership/start regressions; no DRM, AT or live device."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package', TOP/'tools/u30pro-package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
SERVICE = TOP/'devices/u30pro/overlay/etc/init.d/u30pro-panel'


class Startup(unittest.TestCase):
    def test_refresh_retires_only_owned_old_service_link_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init = root/'etc/init.d'; init.mkdir(parents=True)
            rc = root/'etc/rc.d'; rc.mkdir()
            (init/'u30pro-panel').write_bytes(SERVICE.read_bytes())
            (rc/'S95u30pro-panel').symlink_to('../init.d/u30pro-panel')
            (rc/'S96u30pro-panel').symlink_to('../init.d/unrelated')
            package.enable_service(root, 'u30pro-panel')
            package.enable_service(root, 'u30pro-panel')
            self.assertFalse((rc/'S95u30pro-panel').is_symlink())
            self.assertEqual(os.readlink(rc/'S14u30pro-panel'), '../init.d/u30pro-panel')
            self.assertEqual(os.readlink(rc/'S96u30pro-panel'), '../init.d/unrelated')

    def test_conflicting_entry_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'etc/init.d').mkdir(parents=True)
            (root/'etc/rc.d').mkdir()
            (root/'etc/init.d/u30pro-panel').write_bytes(SERVICE.read_bytes())
            conflict=root/'etc/rc.d/S14u30pro-panel'
            conflict.write_text('user entry')
            with self.assertRaises(ValueError): package.enable_service(root, 'u30pro-panel')
            self.assertEqual(conflict.read_text(), 'user entry')

    @unittest.skipUnless(os.name=='posix', 'POSIX service runner')
    def test_procd_launches_display_and_feed_without_waiting_for_modem(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            panel=folder/'panel'
            panel.write_text('#!/bin/sh\n[ "$1" = probe ]\n')
            panel.chmod(0o755)
            script=SERVICE.read_text().replace('/opt/u30pro/bin/u30pro-panel', str(panel))
            script=script.replace('mkdir -p /run/u30pro-ui', ':')
            script+='\nprocd_open_instance() { printf \'instance %s\\n\' "$1"; }\nprocd_set_param() { printf \'%s\\n\' "$*"; }\nprocd_close_instance() { :; }\nstart_service\n'
            result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=3,check=True)
            self.assertIn('instance display', result.stdout)
            self.assertIn('instance feed', result.stdout)
            self.assertLess(result.stdout.index('instance display'),result.stdout.index('instance feed'))
            self.assertIn('command /usr/sbin/u30pro-ui-feed',result.stdout)


if __name__=='__main__': unittest.main(verbosity=2)
