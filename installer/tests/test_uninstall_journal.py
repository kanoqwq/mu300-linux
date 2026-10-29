"""Automatic journal-only recovery is bound to verified installation evidence."""
import struct
import sys
from pathlib import Path
import unittest
import shutil
import subprocess
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_uninstall_evidence as fx
from backend.state import RecoveryRequired
from backend.install_evidence import inspect_installation


class JournalRecovery(unittest.TestCase):
    def setUp(self):
        self.case=fx.NoOriginalRecord();self.case.setUp()
        self.shell=self.case.fixture.adb.shell.side_effect
        self.calls=[];self.recover=True
        self.pending()
        self.case.fixture.adb.shell.side_effect=self.device_shell

    def tearDown(self):self.case.tearDown()

    def pending(self):
        raw=bytearray(self.case.fs)
        struct.pack_into('<I',raw,1024+92,4)
        struct.pack_into('<I',raw,1024+96,struct.unpack_from('<I',raw,1024+96)[0]|4)
        struct.pack_into('<I',raw,1024+224,8)
        self.case.fs=bytes(raw)

    def device_shell(self,command,**kw):
        if '-E journal_only' in command:
            self.calls.append(command)
            if self.recover:
                raw=bytearray(self.case.fs)
                struct.pack_into('<I',raw,1024+96,struct.unpack_from('<I',raw,1024+96)[0]&~4)
                self.case.fs=bytes(raw)
            return 'Journal recovered'
        return self.shell(command,**kw)

    def inspect(self,repair=True):
        return inspect_installation(self.case.fixture.adapter,self.case.fixture.device,self.case.root,
                                    recovery_store=self.case.store if repair else None)

    def test_verified_unmounted_root_is_recovered_once_and_reverified(self):
        proof=self.inspect()
        self.assertEqual(len(self.calls),1)
        self.assertIn('/system/bin/e2fsck -p -E journal_only',self.calls[0])
        self.assertNotIn('-y',self.calls[0]);self.assertNotIn('of=',self.calls[0])
        self.assertEqual(proof,self.inspect(False));self.assertEqual(len(self.calls),1)

    def test_read_only_revalidation_cannot_silently_repair(self):
        with self.assertRaisesRegex(RecoveryRequired,'journal needs recovery'):self.inspect(False)
        self.assertFalse(self.calls)

    def test_no_general_repair_for_errors_unknown_features_or_wrong_markers(self):
        for error in ('state','features','marker','boot','mounted'):
            with self.subTest(error=error):
                saved=self.case.fs;boot=self.case.boot;idle=self.case.idle
                if error=='marker':
                    self.case.fs=fx.image({**fx.marker_files('e5'),'etc/e5/part-root':b'e5root /dev/mmcblk1\n'});self.pending()
                elif error=='boot':self.case.boot=fx.linux_boot('mu300')
                elif error=='mounted':self.case.idle='BUSY'
                else:
                    raw=bytearray(self.case.fs)
                    if error=='state':struct.pack_into('<H',raw,1024+58,2)
                    else:struct.pack_into('<I',raw,1024+96,0x10046)
                    self.case.fs=bytes(raw)
                with self.assertRaises(RecoveryRequired):self.inspect()
                self.assertFalse(self.calls)
                self.case.fs=saved;self.case.boot=boot;self.case.idle=idle

    def test_recovery_must_clear_pending_flag_before_plan_is_accepted(self):
        self.recover=False
        with self.assertRaisesRegex(RecoveryRequired,'journal needs recovery'):self.inspect()
        self.assertEqual(len(self.calls),1)

    def test_gpt_change_stops_before_recovery(self):
        self.case.fixture.adapter.read_gpt.return_value={'parts':[{**self.case.root,'start':self.case.root['start']+2048}]}
        with self.assertRaisesRegex(RecoveryRequired,'geometry changed'):self.inspect()
        self.assertFalse(self.calls)

    @unittest.skipUnless(sys.platform!='win32' and all(shutil.which(x) for x in ('mke2fs','debugfs','e2fsck')),
                         'requires Linux ext4 tools; temporary regular file only')
    def test_real_ext4_markers_survive_real_journal_only_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'openwrt.img';marker=Path(folder)/'marker'
            with image.open('wb') as f:f.truncate(32<<20)
            def run(*args):
                p=subprocess.run(args,capture_output=True,text=True,timeout=30)
                self.assertIn(p.returncode,(0,1),p.stdout+p.stderr)
                return p
            run('mke2fs','-q','-F','-t','ext4',str(image))
            for directory in ('etc','etc/e5'):
                run('debugfs','-w','-R','mkdir '+directory,str(image))
            for name,value in fx.marker_files('e5').items():
                marker.write_bytes(value)
                run('debugfs','-w','-R','write '+str(marker)+' '+name,str(image))
            run('debugfs','-w','-R','feature needs_recovery',str(image))
            self.case.fs=image.read_bytes()
            self.assertTrue(struct.unpack_from('<I',self.case.fs,1024+96)[0]&4)
            original=self.device_shell
            def shell(command,**kwargs):
                if '-E journal_only' in command:
                    self.calls.append(command)
                    result=run('e2fsck','-p','-E','journal_only',str(image))
                    self.case.fs=image.read_bytes()
                    return result.stdout
                return original(command,**kwargs)
            self.case.fixture.adb.shell.side_effect=shell
            self.inspect()
            self.assertEqual(len(self.calls),1)
            self.assertFalse(struct.unpack_from('<I',self.case.fs,1024+96)[0]&4)


if __name__=='__main__':unittest.main()
