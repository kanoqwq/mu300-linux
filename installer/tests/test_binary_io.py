"""Binary staging, hash checks and legacy GUI delegation without real ADB."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.binary_io import pull_binary
from backend.adapter import Adapter
from backend.state import Context, RecoveryRequired, Store, sha
from backend.transport import Adb


class BinaryIO(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.local=self.root/'binary.bin'
        self.data=bytes(range(256))*4096+b'\x00\r\n\xfftail'
        self.corrupt=False;self.short=False;self.source_changed=False;self.free=1024*1024
        self.scripts=[];self.calls=[];self.failed=False

    def shell(self,script,**kwargs):
        self.scripts.append((script,kwargs))
        if script.startswith('df '):return str(self.free)
        if script.startswith('umask 077; mkdir '):return ''
        if script.startswith('set -e; set -o pipefail;'):
            if self.failed:raise ConnectionError('fixture staging failed')
            h=hashlib.sha256(self.data).hexdigest()
            return str(len(self.data))+'\r\n'+h+'  data.bin\r\n'+('0'*64 if self.source_changed else h)+'  -\r\n'
        if script.startswith('rm -f '):return ''
        self.fail(script)

    def pull(self,*args,**kwargs):
        self.calls.append(args)
        self.assertEqual(args[0],'pull')
        self.assertTrue(args[1].startswith('/data/local/tmp/openwrt-read-'))
        data=self.data[:-1] if self.short else b'X'+self.data[1:] if self.corrupt else self.data
        Path(args[2]).write_bytes(data)
        return b'1 file pulled'

    def test_raw_bytes_survive_crlf_and_all_byte_values(self):
        self.assertEqual(pull_binary(self.pull,self.shell,'cat /sys/firmware/fdt',self.local),len(self.data))
        self.assertEqual(self.local.read_bytes(),self.data)
        self.assertTrue(self.scripts[-1][0].startswith('rm -f '))
        self.assertTrue(self.scripts[1][1]['root'] is False)
        self.assertFalse(any('exec-out' in c for c in self.calls))

    def test_corrupt_short_or_changed_source_never_replace_verified_file(self):
        for flag in ('corrupt','short','source_changed','failed'):
            with self.subTest(flag=flag):
                self.local.write_bytes(b'previous verified data')
                setattr(self,flag,True)
                with self.assertRaises((ValueError,ConnectionError)):pull_binary(self.pull,self.shell,'dd if=fixture bs=4096 count=1',self.local)
                self.assertEqual(self.local.read_bytes(),b'previous verified data')
                self.assertFalse(self.local.with_name('binary.bin.partial').exists())
                self.assertTrue(self.scripts[-1][0].startswith('rm -f '))
                setattr(self,flag,False)

    def test_space_size_and_metadata_limits_stop_before_download(self):
        self.free=1
        with self.assertRaises(ValueError):pull_binary(self.pull,self.shell,'cat fixture',self.local)
        self.assertEqual(len(self.scripts),1)
        self.free=1024*1024
        with self.assertRaises(ValueError):pull_binary(self.pull,self.shell,'cat fixture',self.local,expected_size=len(self.data)+1)
        with self.assertRaises(ValueError):pull_binary(self.pull,self.shell,'cat fixture',self.local,limit=10)
        self.assertFalse(self.calls)

    def test_shared_read_uses_actual_serial_bound_pull(self):
        adb=Adb.__new__(Adb);adb.exe='fixture-adb';adb.serial='chosen';adb.su='su';adb.context=None
        def run(argv,**kwargs):
            self.assertEqual(argv[:4],['fixture-adb','-s','chosen','pull'])
            self.pull(*argv[3:])
            return subprocess.CompletedProcess(argv,0,b'1 file pulled',b'')
        with patch.object(adb,'shell',side_effect=self.shell),patch('backend.transport.subprocess.run',side_effect=run):
            self.assertEqual(adb.read('cat /sys/firmware/fdt'),self.data)


class LegacyGUI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'OpenWrt-Installer.py').write_text('class Android:\n def call(self,*a,**k): raise AssertionError("legacy call")\n def read(self,*a): raise AssertionError("old binary transport")\n def backup(self,*a): raise AssertionError("duplicate device backup")\n')
        self.storage={'CID':'a'*32,'NODE':'mmcblk0p30','START':2048,'SIZE':40000000,'DISK_SIZE':60000000}
        device={'serial':'selected','cid':'a'*32,'slot':'_a','storage':self.storage}
        self.store=Store(self.root/'data');self.record=self.store.create({'path':str(self.root),'adapter':'mu300'},device,{}, {})
        self.record['completed']=['backup'];self.ctx=Context(self.store,self.record,lambda *_:None)
        self.adapter=Adapter(self.record['package'],self.ctx)
        self.adapter.adb=SimpleNamespace(serial='selected',read=Mock(return_value=b'raw fixture'))
        self.android=self.adapter.legacy.Android();self.android.serial='selected';self.android.slot='a'
        backup=self.ctx.directory/'backup';backup.mkdir()
        names=['misc.bin','gpt-first.bin','gpt-last.bin']+[f'{n}_{s}.bin' for n in ('boot','uboot','sml','teecfg','trustos','vbmeta') for s in ('a','b')]
        for name in names:(backup/name).write_bytes((name+'\n').encode()*20)
        (backup/'checksums.json').write_text(json.dumps({n:sha(backup/n) for n in names}))

    def test_old_package_read_and_backup_are_replaced_by_shared_backend(self):
        self.assertEqual(self.android.read('bounded read'),b'raw fixture')
        target=self.ctx.directory/'legacy-backups/fixture'
        self.android.backup(target,self.storage)
        self.assertEqual((target/'boot_a.img').read_bytes(),(self.ctx.directory/'backup/boot_a.bin').read_bytes())
        self.assertTrue((target/'SHA256SUMS').is_file())
        self.adapter.adb.read.assert_called_once_with('bounded read')


    def online_backup_fixture(self, block=512, slot='a'):
        from test_online_shrink import device
        from backend.online_shrink import candidate
        from backend.service import plan_install
        d, original, total = device(block)
        d.update(serial='selected', slot='_'+slot)
        d['storage']['NODE'] = 'mmcblk0p2' if block == 512 else 'sdb2'
        plan = plan_install({'adapter':'mu300'}, d, {'method':'part','size_gib':4})
        updated = tuple(candidate(original,total,d['storage']['NODE'],d['storage']['SIZE'],plan['new_sectors']))
        source = self.ctx.directory/'backup'
        (source/'identity.json').write_text(json.dumps(d))
        for name,raw in zip(('gpt-first.bin','gpt-last.bin'),original):
            (source/name).write_bytes(raw)
        sums = {p.name:sha(p) for p in source.iterdir() if p.name != 'checksums.json'}
        (source/'checksums.json').write_text(json.dumps(sums))
        folder = self.ctx.directory/'online-shrink'
        folder.mkdir(exist_ok=True)
        files = dict(zip(('old-first','old-last','new-first','new-last'),(*original,*updated)))
        for name,raw in files.items():(folder/name).write_bytes(raw)
        self.record['online_shrink'] = dict(state='verified',sectors=total,new_sectors=plan['new_sectors'],
            files={name:sha(folder/name) for name in files})
        d['storage']['SIZE'] = plan['new_sectors']
        d['userdata_bytes'] = plan['android_bytes']
        plan.update(target='GPT unallocated space',two_stage=False,offline=False)
        self.record.update(device=d,plan=plan)
        self.storage = d['storage']
        self.android.slot = slot
        return original, updated

    def test_online_resize_handoff_preserves_originals_and_uses_verified_new_gpt(self):
        for block in (512,4096):
            for slot in ('a','b'):
                with self.subTest(block=block,slot=slot):
                    original, updated = self.online_backup_fixture(block,slot)
                    source = self.ctx.directory/'backup'
                    before = {p.name:p.read_bytes() for p in source.iterdir()}
                    target = self.ctx.directory/f'legacy-backups/online-{block}-{slot}'
                    self.android.backup(target,self.storage)
                    self.assertEqual(tuple((target/n).read_bytes() for n in ('gpt-first.bin','gpt-last.bin')),updated)
                    self.assertNotEqual(original,updated)
                    self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()},before)
                    for line in (target/'SHA256SUMS').read_text().splitlines():
                        digest,name = line.split()
                        self.assertEqual(sha(target/name),digest)
                    self.assertEqual((target/'boot_a.img').read_bytes(),before['boot_a.bin'])
        self.adapter.adb.read.assert_not_called()

    def test_online_handoff_refuses_pending_corrupt_or_unrelated_gpt_changes(self):
        from backend.uninstall import table,with_table
        for failure in ('pending','corrupt','unrelated','wrong-size','wrong-original'):
            with self.subTest(failure=failure):
                self.online_backup_fixture()
                folder = self.ctx.directory/'online-shrink'
                j = self.record['online_shrink']
                if failure == 'pending':j['state']='gpt-committed'
                elif failure == 'corrupt':(folder/'new-first').write_bytes(b'bad')
                elif failure == 'wrong-size':j['new_sectors']-=2048
                else:
                    names = ('new-first','new-last') if failure == 'unrelated' else ('old-first','old-last')
                    pair = tuple((folder/n).read_bytes() for n in names)
                    rows = bytearray(table(pair[0]));rows[48] ^= 1
                    changed = with_table(pair,rows,self.storage['DISK_SIZE'])
                    if failure == 'unrelated':
                        for name,raw in zip(names,changed):
                            (folder/name).write_bytes(raw);j['files'][name]=sha(folder/name)
                    else:
                        source = self.ctx.directory/'backup'
                        for name,raw in zip(('gpt-first.bin','gpt-last.bin'),changed):(source/name).write_bytes(raw)
                        sums = json.loads((source/'checksums.json').read_text())
                        for name in ('gpt-first.bin','gpt-last.bin'):sums[name]=sha(source/name)
                        (source/'checksums.json').write_text(json.dumps(sums))
                target = self.ctx.directory/('legacy-backups/reject-'+failure)
                with self.assertRaises(RecoveryRequired):self.android.backup(target,self.storage)
                self.assertFalse(target.exists())
        self.adapter.adb.read.assert_not_called()

    def test_actual_desktop_preflight_after_online_resize(self):
        import importlib.util
        from backend import gpt
        from backend.online_shrink import OnlineShrink
        original,updated = self.online_backup_fixture(slot='b')
        entry = Path(__file__).resolve().parents[2]/'desktop/installer.py'
        spec = importlib.util.spec_from_file_location('handoff_desktop',entry)
        desktop = importlib.util.module_from_spec(spec);spec.loader.exec_module(desktop)
        space = gpt.gpt_space(*updated,self.storage['DISK_SIZE'])
        space.update(first=updated[0],last=updated[1])
        android = SimpleNamespace(model='F50',slot='b',linux='a',serial='selected',
            storage=lambda:self.storage,gpt=lambda _:space,
            backup=lambda path,storage:self.adapter.backup_legacy(android,path,storage),prepare_payload=Mock())
        args = SimpleNamespace(boot_openwrt=False,tf=False,resume=False,reinstall_part=False,
            data=False,size='4',format_userdata=False,backup_dir=str(self.ctx.directory/'legacy-backups/reproduce'))
        with patch.object(desktop,'Android',return_value=android),patch.object(desktop,'module'),\
                patch.object(desktop,'GUI',self.adapter),patch.object(desktop,'free_write',return_value='verified') as writer:
            # Reproduce the reported error through the real comparison, rather
            # than a fake installer which never reads its backup GPT.
            with patch.object(OnlineShrink,'installation_gpt',return_value=original):
                with self.assertRaisesRegex(ValueError,'GPT changed during preflight'):
                    desktop.install(args,self.root,{'kernel':'7.2'})
            android.prepare_payload.assert_not_called();writer.assert_not_called()
            args.backup_dir=str(self.ctx.directory/'legacy-backups/fixed')
            self.assertEqual(desktop.install(args,self.root,{'kernel':'7.2'}),'verified')
            android.prepare_payload.assert_called_once();writer.assert_called_once()
            self.assertEqual(tuple((self.ctx.directory/'backup'/n).read_bytes()
                for n in ('gpt-first.bin','gpt-last.bin')),original)
            # A real on-disk GPT change must still stop before payload/write.
            android.prepare_payload.reset_mock();writer.reset_mock()
            space['first']=original[0];space['last']=original[1]
            args.backup_dir=str(self.ctx.directory/'legacy-backups/changed')
            with self.assertRaisesRegex(ValueError,'GPT changed during preflight'):
                desktop.install(args,self.root,{'kernel':'7.2'})
            android.prepare_payload.assert_not_called();writer.assert_not_called()

    def test_wrong_device_layout_or_corrupted_cache_stops(self):
        target=self.ctx.directory/'legacy-backups/fixture'
        with self.assertRaises(ValueError):self.android.backup(target,{**self.storage,'START':4096})
        self.assertFalse(target.exists())
        (self.ctx.directory/'backup/boot_a.bin').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.android.backup(target,self.storage)
        self.assertFalse(target.exists())
        self.android.serial='other'
        with self.assertRaises(ValueError):self.android.read('bounded read')


if __name__=='__main__':unittest.main()
