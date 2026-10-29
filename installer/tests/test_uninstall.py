"""Uninstall proof, reboot/recovery and offline file-write fault matrix.

All transports are simulated. Shell GPT tests use temporary regular files.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import gpt
from backend.service import Service
from backend.state import Context, Store, RecoveryRequired, Cancelled, DeviceLock
from backend.uninstall import prepare, persist, restored_layout, Uninstaller, android_volume
from backend.uninstall_boot import build, archives, entries, overlay
from backend.uninstall_worker import WORKER

GIB = 1 << 30
SECTORS = (64*GIB)//512
IDENTITY = 'b'*32


def android_bc(slot='a'):
    data=bytearray(32);data[:4]=('_'+slot).encode()+b'\0\0'
    data[4:8]=b'BCAB';data[8]=1;data[9]=2
    data[12],data[14]=(0x9f,0x1e) if slot=='a' else (0x1e,0x9f)
    struct.pack_into('<I',data,28,zlib.crc32(data[:28]))
    return bytes(data)


def copies(table):
    def header(lba, alternate, table_at):
        data = bytearray(512)
        struct.pack_into('<8sIIIIQQQQ16sQIII',data,0,b'EFI PART',0x10000,92,0,0,
                         lba,alternate,34,SECTORS-34,b'G'*16,table_at,128,128,zlib.crc32(table))
        struct.pack_into('<I',data,16,zlib.crc32(data[:92]))
        return bytes(data)
    return (bytes(512)+header(1,SECTORS-1,2)+bytes(table),
            bytes(table)+header(SECTORS-1,1,SECTORS-33))


def layouts(family='e5', gap=False):
    table = bytearray(16384)
    def entry(n,start,size,name):
        at=(n-1)*128
        table[at:at+16]=uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
        table[at+16:at+32]=bytes([n])*16
        struct.pack_into('<QQ',table,at+32,start,start+size-1)
        text=name.encode('utf-16-le');table[at+56:at+56+len(text)]=text
    entry(1,2048,131072,'boot_a');entry(2,133120,131072,'boot_b')
    entry(3,264192,2048,'misc');entry(6,400000,4096,'nv-protected')
    data_start=GIB//512
    data_size=(28 if gap else 32)*GIB//512
    entry(4,data_start,data_size,'userdata')
    original=copies(table)
    if not gap:
        struct.pack_into('<Q',table,3*128+40,data_start+28*GIB//512-1)
    before=gpt.gpt_add_root(*copies(table),SECTORS,data_start+28*GIB//512,4*GIB//512,
                            'e5root' if family=='e5' else 'mu300root')
    return original,before


def superblock(size, kind='ext4'):
    data=bytearray(1024)
    if kind=='ext4':
        data[56:58]=b'\x53\xef';struct.pack_into('<I',data,24,2)
        struct.pack_into('<I',data,4,size//4096)
    else:
        data[:4]=bytes.fromhex('1020f5f2');struct.pack_into('<I',data,16,12)
        struct.pack_into('<Q',data,36,size//4096)
    at=104 if kind=='ext4' else 108;data[at:at+16]=b'U'*16
    return bytes(data)


def boot(family):
    init='#!/bin/busybox sh\npersist() {\n    echo log\n}\nload_board_modules\n'
    init+='tf_update_probe\nmount_userdata\n' if family=='e5' else 'ROOT_OFFSET=27762098176\nmount_userdata\n'
    ram=overlay({'init':init.encode()})
    header=bytearray(4096);header[:8]=b'ANDROID!'
    struct.pack_into('<II',header,8,4096,len(ram));struct.pack_into('<I',header,20,1584)
    struct.pack_into('<I',header,40,4)
    command=b'e5.part_v=2' if family=='e5' else b'loglevel=5'
    header[44:44+len(command)]=command
    body=(bytes(header)+b'K'*4096+ram)
    body=body.ljust((len(body)+4095)&~4095,b'\0')
    footer=bytearray(64);footer[:4]=b'AVBf'
    struct.pack_into('>QQQ',footer,12,len(body),len(body),128)
    return (body+b'V'*128).ljust((64<<20)-64,b'\0')+footer


class Proof(unittest.TestCase):
    def test_pending_boot_marker_blocks_new_operations_even_without_computer_record(self):
        header=bytearray(4096);header[44:44+len(b'openwrt.uninstall=1')]=b'openwrt.uninstall=1'
        for method in ('part','reinstall','boot','uninstall'):
            with self.subTest(method=method),tempfile.TemporaryDirectory() as directory:
                service=Service('.',Store(directory),io.StringIO());service.package={'adapter':'e5'}
                adapter=SimpleNamespace(probe=Mock(return_value={'slot':'_a','readiness':'ready','cid':IDENTITY}),
                                        adb=SimpleNamespace(read=Mock(return_value=bytes(header))))
                service.adapters=lambda *_:adapter
                with self.assertRaisesRegex(RecoveryRequired,'pending'):
                    service.dispatch({'protocol':1,'command':'plan','params':{'serial':'selected','choices':{'method':method}}})
                self.assertFalse(service.store.list())

    def test_shrink_and_factory_gap_reverse_only_own_entries(self):
        for family in ('e5','mu300'):
            for gap in (False,True):
                with self.subTest(family=family,gap=gap):
                    original,before=layouts(family,gap)
                    after,data,root,wipe=restored_layout(original,before,SECTORS,'e5root' if family=='e5' else 'mu300root')
                    self.assertEqual(after,list(original))
                    self.assertEqual(wipe,not gap)
                    self.assertEqual((data['end']-data['start']+1)*512,(28 if gap else 32)*GIB)
                    self.assertEqual(root['name'],'e5root' if family=='e5' else 'mu300root')

    def test_changed_other_partition_or_userdata_identity_is_refused(self):
        original,before=layouts()
        for offset in (5*128+16,3*128+16,3*128+32):
            with self.subTest(offset=offset):
                table=bytearray(before[0][1024:]);table[offset]^=1
                with self.assertRaises((ValueError,RecoveryRequired)):
                    restored_layout(original,copies(table),SECTORS,'e5root')

    def test_corrupt_gpt_copies_are_refused(self):
        original,before=layouts();changed=bytearray(before[1]);changed[0]^=1
        with self.assertRaises(ValueError):restored_layout(original,(before[0],bytes(changed)),SECTORS,'e5root')

    def test_an_old_larger_userdata_backup_cannot_reclaim_extra_space(self):
        original,before=layouts()
        changed=bytearray(original[0][1024:])
        end=struct.unpack_from('<Q',changed,3*128+40)[0]
        struct.pack_into('<Q',changed,3*128+40,end+4*GIB//512)
        with self.assertRaises(RecoveryRequired):
            restored_layout(copies(changed),before,SECTORS,'e5root')

    def test_ext4_and_f2fs_are_measured_from_real_superblock_fields(self):
        for kind in ('ext4','f2fs'):
            adb=SimpleNamespace(shell=Mock(side_effect=['/dev/block/dm-0',str(28*GIB//512)]),
                                read=Mock(return_value=superblock(28*GIB,kind)))
            result=android_volume(adb,{'userdata_bytes':28*GIB,'storage':{'SOURCE':'/dev/block/dm-0'}})
            self.assertEqual(result['filesystem_bytes'],28*GIB)
            self.assertEqual(result['filesystem'],kind)


class Journal(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.configure()

    def configure(self, family='e5', slot='a', gap=False):
        self.original,self.before=layouts(family,gap)
        self.package={'path':self.temp.name,'adapter':family,'profile':'e5' if family=='e5' else 'mu300-family','protocol':1,'payloads':{}}
        self.device={'serial':'selected','cid':IDENTITY,'profile':'e5' if family=='e5' else 'f50','model':'E5' if family=='e5' else 'F50','slot':'_'+slot,'root':True,
                     'readiness':'ready','boot_id':'before','userdata_bytes':28*GIB,'existing':True,
                     'storage':{'NODE':'mmcblk0p4','ROOT':'mmcblk0p5','START':GIB//512,
                                'SIZE':28*GIB//512,'DISK_SIZE':SECTORS,'SOURCE':'/dev/block/mmcblk0p4'}}
        source_plan={'method':'part','openwrt_bytes':4*GIB}
        if gap:source_plan['gap_start']=29*GIB//512
        self.source=self.store.create(self.package,{**self.device,'userdata_bytes':(28 if gap else 32)*GIB},
                                      {'method':'part'},source_plan)
        backup=self.store.directory(self.source['id'])/'backup';backup.mkdir()
        (backup/'gpt-first.bin').write_bytes(self.original[0]);(backup/'gpt-last.bin').write_bytes(self.original[1])
        (backup/'identity.json').write_text(json.dumps(self.device))
        from backend.state import sha
        (backup/'checksums.json').write_text(json.dumps({p.name:sha(p) for p in backup.iterdir()}))
        self.adb=SimpleNamespace(shell=Mock(side_effect=['/dev/block/mmcblk0p4',str(28*GIB//512)]),
                                 read=Mock(return_value=superblock(28*GIB)))
        self.adapter=SimpleNamespace(kind=family,adb=self.adb,read_gpt=Mock(return_value={**gpt.gpt_space(*self.before,SECTORS),
                                                                                  'first':self.before[0],'last':self.before[1]}))

    def tearDown(self):self.temp.cleanup()

    def plan(self):
        plan,metadata,files=prepare(self.adapter,self.store,self.device,authorize_reset=lambda _:True)
        record=self.store.create(self.package,self.device,{'method':'uninstall'},plan)
        context=Context(self.store,record,lambda *_:None);persist(context,metadata,files)
        return context

    def test_plan_is_read_only_and_damaged_backups_need_device_evidence(self):
        context=self.plan()
        self.assertTrue(context.record['plan']['wipe'])
        self.assertEqual(context.record['plan']['reclaimed_bytes'],4*GIB)
        self.assertEqual(context.record['plan']['android_bytes'],32*GIB)
        self.assertEqual(context.record['plan']['source_transaction'],self.source['id'])
        commands=' '.join(str(call) for call in self.adb.shell.call_args_list)
        self.assertNotIn('dd ',commands);self.assertNotIn('of=',commands)
        (self.store.directory(self.source['id'])/'backup/gpt-first.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RecoveryRequired,'original'):prepare(self.adapter,self.store,self.device)

    def test_factory_gap_plan_does_not_reset_or_enlarge_android(self):
        self.configure('mu300','b',gap=True)
        context=self.plan()
        self.assertFalse(context.record['plan']['wipe'])
        self.assertEqual(context.record['plan']['android_bytes'],28*GIB)
        self.assertEqual(context.record['plan']['reclaimed_bytes'],0)
        self.assertEqual(context.record['plan']['openwrt_bytes'],4*GIB)

    def test_missing_original_record_and_missing_device_evidence_stop_before_volume_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RecoveryRequired,'original'):
                prepare(self.adapter,Store(directory),self.device)
        self.adb.shell.assert_not_called()

    def test_explicit_resume_connection_change_checks_hardware_and_masks_history(self):
        context=self.plan();record=context.record
        service=Service('.',self.store,io.StringIO())
        original_serial=record['device']['serial']
        selected='192.168.0.1:5555'
        for change in ({'cid':'0'*32},{'profile':'another'},{'slot':'_b'},{'readiness':'root_permission'}):
            probe=Mock(return_value={**self.device,**change})
            adapter=SimpleNamespace(probe=probe,execute=Mock())
            service.adapters=lambda *_:adapter
            with self.subTest(change=change),self.assertRaises((ValueError,RecoveryRequired)):
                service.rebind_uninstall(record,selected,self.package)
            self.assertEqual(record['device']['serial'],original_serial)
            adapter.execute.assert_not_called()
        adapter=SimpleNamespace(probe=Mock(return_value=self.device.copy()),execute=Mock())
        service.adapters=lambda *_:adapter
        with DeviceLock(self.store.base,IDENTITY),self.assertRaises(RuntimeError):
            service.rebind_uninstall(record,selected,self.package)
        service.rebind_uninstall(record,selected,self.package)
        self.assertEqual(record['device']['serial'],selected)
        self.assertEqual(record['transport_history'][0]['serial'],original_serial)
        self.assertEqual(record['device']['cid'],IDENTITY)
        serialized=json.dumps(context.snapshot())
        self.assertNotIn(original_serial,serialized)
        self.assertNotIn(selected,serialized)
        adapter.execute.assert_not_called()

    def test_damaged_uninstall_snapshot_is_refused_after_restart(self):
        context=self.plan();(context.directory/'uninstall/after-first').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RecoveryRequired,'snapshot'):
            Uninstaller(None,Context(self.store,self.store.load(context.record['id']),lambda *_:None),self.adapter)

    def test_rehashed_import_cannot_modify_nv_or_hide_the_required_reset(self):
        context=self.plan();folder=context.directory/'uninstall'
        after=[(folder/'after-first').read_bytes(),(folder/'after-last').read_bytes()]
        changed=bytearray(after[0][1024:]);changed[5*128+16]^=1
        altered=copies(changed)
        for name,data in zip(('after-first','after-last'),altered):
            (folder/name).write_bytes(data)
            context.record['uninstall']['checksums'][name]=hashlib.sha256(data).hexdigest()
        with self.assertRaises(RecoveryRequired):Uninstaller(None,context,self.adapter)
        for name,data in zip(('after-first','after-last'),after):
            (folder/name).write_bytes(data)
            context.record['uninstall']['checksums'][name]=hashlib.sha256(data).hexdigest()
        context.record['plan']['wipe']=False
        with self.assertRaises(RecoveryRequired):Uninstaller(None,context,self.adapter)

    def test_expected_disconnect_unauthorized_su_and_restart_never_rearm(self):
        context=self.plan();record=context.record
        record['effects']['uninstall-arm']={'state':'done'};record['stage']='uninstall_offline'
        self.store.save(record)
        after=(context.directory/'uninstall/after-first').read_bytes(),(context.directory/'uninstall/after-last').read_bytes()
        ready={**self.device,'boot_id':'after','existing':False,'userdata_bytes':32*GIB}
        devices=[ConnectionError('expected reboot'),'android_setup','debug_authorization','root_permission',ready]
        def probe(*_):
            item=devices.pop(0)
            if isinstance(item,Exception):raise item
            return item if isinstance(item,dict) else {'readiness':item}
        def read(command):
            if 'count=34 ' in command:return after[0]
            if 'skip='+str(SECTORS-33) in command:return after[1]
            if 'by-name/misc' in command:return android_bc() if 'skip=2048' in command else bytes(2048)
            return superblock(32*GIB)
        record['uninstall_android_sha256']='c'*64;self.store.save(record)
        adapter=SimpleNamespace(probe=Mock(side_effect=probe),password='',adb=SimpleNamespace(
            read=read,shell=Mock(side_effect=['/dev/block/mmcblk0p4',str(32*GIB//512),
                                            'c'*64+'  boot_a\n'+'c'*64+'  boot_b\n',''])))
        output=io.StringIO();service=Service('.',self.store,output)
        restored=Context(self.store,self.store.load(record['id']),service.emit)
        with patch.object(service.stop,'wait',return_value=False),patch.object(Uninstaller,'arm',side_effect=AssertionError('replayed')):
            service.run(restored,adapter)
        result=self.store.load(record['id'])
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertTrue(result['result']['uninstalled'])
        self.assertEqual(result['result']['filesystem_bytes'],32*GIB)
        stages=[json.loads(line)['data'].get('stage') for line in output.getvalue().splitlines() if json.loads(line)['type']=='transaction']
        for stage in ('uninstall_offline','android_setup','debug_authorization','root_permission','verify_android'):
            self.assertIn(stage,stages)

    def test_uncertain_arm_never_replays_and_capacity_shortfall_cannot_complete(self):
        context=self.plan();context.record['effects']['uninstall-arm']={'state':'started'}
        with self.assertRaisesRegex(RecoveryRequired,'interrupted'):
            Uninstaller(None,context,self.adapter).run()
        context.record['effects']['uninstall-arm']={'state':'done'}
        device={**self.device,'existing':False,'userdata_bytes':32*GIB}
        self.adb.shell=Mock(side_effect=['/dev/block/mmcblk0p4',str(32*GIB//512)])
        self.adb.read=Mock(return_value=superblock(28*GIB))
        with self.assertRaises(RecoveryRequired):Uninstaller(None,context,self.adapter).finish(device)
        self.assertNotEqual(context.record['status'],'completed')

    def test_finished_layout_never_rearms_a_reset_even_if_arm_checkpoint_was_lost(self):
        context=self.plan();record=context.record
        record.update(uninstall_boot_sha256='d'*64,uninstall_android_sha256='c'*64)
        (context.directory/'backup').mkdir()
        (context.directory/'backup/misc.bin').write_bytes(bytes(2048)+android_bc()+bytes(2016))
        (context.directory/'uninstall/trial.bin').write_bytes(b't'*32)
        record['effects']['uninstall-arm']={'state':'started'};record['unsafe']=True
        self.adapter.probe=Mock(return_value=self.device)
        self.adb.shell.reset_mock()
        self.adb.read=Mock(side_effect=[(context.directory/'uninstall/after-first').read_bytes(),
                                      (context.directory/'uninstall/after-last').read_bytes()])
        result=Uninstaller(None,context,self.adapter).recover_arm()
        self.assertEqual(result['state'],'done')
        self.assertFalse(record['unsafe'])
        self.adb.shell.assert_not_called()

    def test_real_boot_staging_honors_cancel_after_verified_inactive_write(self):
        for family,slot,command in (('e5','a',b''),('mu300','a',b''),('mu300','b',b''),('e5','a',b'bootonce-bootloader'),('mu300','a',b'bootonce-bootloader'),('mu300','b',b'bootonce-bootloader')):
            with self.subTest(family=family,slot=slot):
                self.configure(family,slot)
                context=self.plan();base=boot(family)
                root=Path(self.temp.name)/('package-'+family+'-'+slot+('-bootloader' if command else ''));(root/'files').mkdir(parents=True)
                (root/'files/boot.img').write_bytes(base)
                (root/'desktop').mkdir()
                (root/'desktop/storage-lib.sh').write_bytes((Path(__file__).resolve().parents[2]/'rootfs/overlay/opt/mu300/lib/storage.sh').read_bytes())
                self.adapter.root=root
                self.adapter.legacy=SimpleNamespace(payload_file=lambda root,name:root/'files/boot.img')
                other='b' if slot=='a' else 'a'
                def backup(_device):
                    folder=context.directory/'backup';folder.mkdir()
                    android=bytearray(base);android[44:1580]=bytes(1536)
                    # Synthetic boot bodies are zero padding. Keep them sparse
                    # instead of allocating hundreds of MiB in the WSL VHDX.
                    for name,data in (('boot_'+slot+'.bin',android),('boot_'+other+'.bin',base)):
                        assert not data[16384:-512].strip(b'\0')
                        with (folder/name).open('wb') as stream:
                            stream.truncate(len(data));stream.write(data[:16384])
                            stream.seek(len(data)-512);stream.write(data[-512:])
                    (folder/'misc.bin').write_bytes(command.ljust(32,b'\0')+bytes(2016)+android_bc(slot)+bytes(2016))
                self.adapter.backup=Mock(side_effect=backup)
                def read(command):
                    if 'by-name/boot_'+other in command:return base[:4096] if 'count=8' in command else base[-512:]
                    if 'count=34 ' in command:return self.before[0]
                    if 'skip='+str(SECTORS-33) in command:return self.before[1]
                    raise AssertionError(command)
                self.adb.read=Mock(side_effect=read)
                commands=[]
                def shell(command,**_):
                    commands.append(command)
                    if command.startswith('blockdev '):return str(64<<20)
                    if command.startswith('set -eu;'):
                        context.cancel=True
                    return ''
                self.adb.shell=Mock(side_effect=shell);self.adb.call=Mock()
                with patch('backend.uninstall.push_chunks'),self.assertRaises(Cancelled):
                    Uninstaller(None,context,self.adapter).arm(self.device)
                self.assertEqual(context.record['effects']['uninstall-arm']['state'],'done')
                self.assertFalse(context.record['unsafe'])
                self.assertTrue(context.record['uninstall_armed'])
                self.adb.call.assert_not_called()
                critical=commands[-1]
                self.assertIsNone(self.adb.shell.call_args.kwargs['timeout'])
                self.assertIn('of=/dev/block/by-name/boot_'+other,critical)
                self.assertNotIn('of=/dev/block/by-name/boot_'+slot,critical)
                self.assertNotIn('of=/dev/block/mmcblk0 ',critical)
                self.assertNotIn('wipe_data',critical)
                generated=(context.directory/'uninstall/boot-uninstall.img').read_bytes()
                size=struct.unpack_from('<I',generated,12)[0]
                files={n:b for archive in archives(generated[8192:8192+size]) for n,b,_ in entries(archive)}
                self.assertIn(b'--wipe_data',files['openwrt-uninstall/recovery-bcb'])
                self.assertEqual(files['openwrt-uninstall/before-bcb'],bytes(2048))
                saved=(context.directory/'backup/misc.bin').read_bytes()
                self.assertEqual(saved[:32],command.ljust(32,b'\0'))
                if command:
                    self.assertIn('of=/dev/block/by-name/misc bs=1 count=32',critical)
                helper=files['openwrt-uninstall/storage.sh']
                self.assertIn(b'mu300_storage_identity()',helper)
                sums=files['openwrt-uninstall/SHA256SUMS'].decode().splitlines()
                for row in sums:
                    digest,name=row.split('  ',1)
                    self.assertEqual(digest,hashlib.sha256(files['openwrt-uninstall/'+name]).hexdigest())
                self.assertTrue(any(row.endswith('  storage.sh') for row in sums))

    def test_other_device_is_refused_before_layout_or_boot_writes(self):
        context=self.plan();self.adapter.probe=Mock(return_value={**self.device,'cid':'0'*32})
        with self.assertRaisesRegex(RecoveryRequired,'slot changed'):
            Uninstaller(None,context,self.adapter).run()

    def test_final_boot_completion_does_not_arm_recovery_or_change_active_slot(self):
        context=self.plan();record=context.record
        record['uninstall_android_sha256']='c'*64
        self.adb.read=Mock(return_value=android_bc())
        self.adb.shell=Mock(side_effect=['c'*64+'  boot_a\n'+'d'*64+'  boot_b\n','',''])
        self.adb.call=Mock()
        with patch('backend.uninstall.push_chunks'):
            Uninstaller(None,context,self.adapter).finish_boot(self.device)
        command=self.adb.shell.call_args_list[-1].args[0]
        self.assertIn('if=/dev/block/by-name/boot_a of=/dev/block/by-name/boot_b',command)
        self.assertNotIn('of=/dev/block/by-name/boot_a',command)
        self.assertNotIn('wipe_data',command)
        self.assertNotIn('of=/dev/block/mmcblk0 ',command)
        self.assertIn('seek=2048 count=32',command)
        self.assertFalse(record['unsafe'])


    def test_final_boot_clears_only_a_superseded_bootloader_command(self):
        context=self.plan();record=context.record;record['uninstall_android_sha256']='c'*64
        self.adb.read=Mock(return_value=android_bc())
        self.adb.call=Mock()
        self.adb.shell=Mock(side_effect=['c'*64+'  boot_a\n'+'c'*64+'  boot_b\n','',''])
        raw=b'bootonce-bootloader'.ljust(32,b'\0')+bytes(2016)
        with patch('backend.uninstall.push_chunks'):
            Uninstaller(None,context,self.adapter).finish_boot(self.device,bcb=raw)
        command=self.adb.shell.call_args_list[-1].args[0]
        self.assertIn('of=/dev/block/by-name/misc bs=1 count=32',command)
        self.assertNotIn('wipe_data',command)
        self.assertNotIn('of=/dev/block/by-name/boot_a',command)
        self.assertEqual(record['effects']['uninstall-final-boot']['state'],'done')


class BootRequest(unittest.TestCase):
    def test_both_boot_families_keep_kernel_and_stop_before_root_mount(self):
        for family in ('e5','mu300'):
            with self.subTest(family=family):
                base=boot(family)
                result=build(base,family,{'openwrt-uninstall/request':b'test\n'})
                self.assertEqual(result[4096:8192],base[4096:8192])
                size=struct.unpack_from('<I',result,12)[0]
                files={name:body for archive in archives(result[8192:8192+size]) for name,body,_ in entries(archive)}
                init=files['init'].decode()
                self.assertIn('load_board_modules',init)
                self.assertIn('exec /bin/busybox sh /openwrt-uninstall/worker.sh',init)
                self.assertNotIn('mount_userdata',init)
                self.assertNotIn('echo log',init)
                self.assertEqual(files['openwrt-uninstall/worker.sh'],WORKER.encode())


@unittest.skipUnless(shutil.which('busybox'),'requires Linux BusyBox')
class OfflineGpt(unittest.TestCase):
    def test_full_worker_reset_gap_resume_and_both_android_slots_on_regular_files(self):
        for slot,wipe,finished,grow in (('a',True,False,''),('b',True,False,''),('a',False,False,''),('b',False,False,''),('a',True,True,''),('a',False,False,'ok'),('b',False,False,'ok'),('a',False,False,'check-failed'),('a',False,False,'reread-failed'),('b',False,False,'stale-node'),('a',False,False,'android'),('b',False,False,'android'),('a',False,False,'android-bcb-failed')):
            with self.subTest(slot=slot,wipe=wipe,finished=finished,grow=grow),tempfile.TemporaryDirectory() as directory:
                folder=Path(directory)
                for name in ('run','dev','sys/class/block/mmcblk0/device','proc','work'):
                    (folder/name).mkdir(parents=True)
                (folder/'proc/mountinfo').write_text('')
                original,before=layouts(gap=not wipe and not grow)
                after,*_=restored_layout(original,before,SECTORS,'e5root')
                for name,data in zip(('before-first','before-last','after-first','after-last'),(*before,*after)):
                    (folder/'work'/name).write_bytes(data)
                with (folder/'dev/mmcblk0').open('wb') as stream:
                    stream.truncate(SECTORS*512);stream.write(after[0] if finished else before[0])
                    stream.seek((SECTORS-33)*512);stream.write(after[1] if finished else before[1])
                    stream.seek(GIB);stream.write(b'KEEP ANDROID DATA')
                    stream.seek(400000*512);stream.write(b'KEEP NV DATA')
                    stream.seek(29*GIB);stream.write(b'OPENWRT SIGNATURE')
                for node,name in (('mmcblk0p4','userdata'),('mmcblk0p5','e5root'),('mmcblk0p3','misc'),
                                  ('mmcblk0p1','boot_a'),('mmcblk0p2','boot_b')):
                    path=folder/'sys/class/block'/node
                    (path/'holders').mkdir(parents=True)
                    (path/'uevent').write_text('PARTNAME='+name+'\n')
                    (path/'dev').write_text('179:'+node.split('p')[-1])
                    (path/'size').write_text('8')
                    (path/'start').write_text(str(GIB//512))
                (folder/'sys/class/block/mmcblk0/device/cid').write_text(IDENTITY)
                (folder/'sys/class/block/mmcblk0/device/type').write_text('MMC\n')
                (folder/'sys/class/block/mmcblk0/size').write_text(str(SECTORS))
                android_node,linux_node=('mmcblk0p1','mmcblk0p2') if slot=='a' else ('mmcblk0p2','mmcblk0p1')
                android=b'A'*4096;boot_sha=hashlib.sha256(android).hexdigest()
                (folder/'dev'/android_node).write_bytes(android);(folder/'dev'/linux_node).write_bytes(b'L'*4096)
                (folder/'dev/zero').write_bytes(bytes(4096))
                misc=bytearray(4096)
                if finished:misc[128:143]=b'KEEP AFTER WIPE'
                misc[2048:2080]=android_bc(slot)
                (folder/'dev/mmcblk0p3').write_bytes(misc)
                bcb=bytearray(2048);bcb[:13]=b'boot-recovery';message=b'recovery\n--wipe_data\n'
                bcb[64:64+len(message)]=message
                (folder/'work/recovery-bcb').write_bytes(bcb)
                (folder/'work/before-bcb').write_bytes(bytes(2048))
                (folder/'work/android-bc').write_bytes(android_bc(slot))
                request=[IDENTITY,'mmcblk0','mmcblk0p4','mmcblk0p5','mmcblk0p3',android_node,linux_node,
                         SECTORS,34,SECTORS-33,29*GIB//512,4*GIB//512,int(wipe),4096,boot_sha]
                if grow:
                    request+=[('grow-android-f2fs' if grow.startswith('android') else 'grow-ext4'),GIB//512,32*GIB//512,28*GIB,'55'*16]
                    (folder/'dev/mmcblk0p4').write_bytes(bytes(1024)+superblock(28*GIB))
                if grow=='android-bcb-failed':
                    (folder/'work/before-bcb').write_bytes(b'UNEXPECTED RECOVERY'.ljust(2048,b'\0'))
                (folder/'work/request').write_text(':'.join(map(str,request))+'\n')
                sums=''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n'
                             for path in (folder/'work').iterdir())
                (folder/'work/SHA256SUMS').write_text(sums)
                storage_lib=(Path(__file__).resolve().parents[2]/'rootfs/overlay/opt/mu300/lib/storage.sh').read_text()
                worker=WORKER.replace('. /openwrt-uninstall/storage.sh',storage_lib).replace('/openwrt-uninstall',str(folder/'work'))
                worker=worker.replace('/sys/',str(folder/'sys')+'/').replace('/dev/',str(folder/'dev')+'/')
                worker=worker.replace('/proc/self/mountinfo',str(folder/'proc/mountinfo')).replace('/run/',str(folder/'run')+'/')
                worker=worker.replace('while :; do sleep 10; done','exit "$rc"',1)
                # Only reboot/network services are simulated. GPT, readback,
                # boot copy, BCB writes and hashes use real BusyBox file I/O.
                prefix='httpd() { :; }\nreboot() { echo reboot > '+str(folder/'rebooted')+'; exit 0; }\n'
                if grow:
                    # GPT/BCB writes are real file I/O. Tool routing is mocked
                    # here; real populated filesystem growth is tested separately.
                    prefix+='fs_tool() { '+('case "$*" in *" -pf "*) return 4;; esac; ' if grow=='check-failed' else '')+':; }\n'
                    prefix+='fs_grow() { echo PRESERVING_GROW; }\n'
                    if grow.startswith('android'):
                        prefix+='fs_tool() { echo FORBIDDEN_RAW_CHECK; return 99; }\nfs_grow() { echo FORBIDDEN_RAW_GROW; return 99; }\n'
                    prefix+='kernel_node_matches() { '+('return 1' if grow=='stale-node' else ':')+'; }\n'
                    prefix+='blockdev() { '+('return 16' if grow=='reread-failed' else '[ "$1" = --rereadpt ] && echo '+str(32*GIB//512)+' > '+str(folder/'sys/class/block/mmcblk0p4/size'))+'; }\n'
                    worker=worker.replace('# BEGIN DEVICE WORKER',prefix+'\n# BEGIN DEVICE WORKER',1)
                result=subprocess.run(['busybox','ash','-c',prefix+worker],capture_output=True,text=True,timeout=10)
                debug=list((folder/'run').rglob('debug.log'))
                detail=debug[0].read_text() if debug else result.stderr
                if grow in ('check-failed','reread-failed','stale-node','android-bcb-failed'):
                    self.assertNotEqual(result.returncode,0,detail)
                    self.assertFalse((folder/'rebooted').exists())
                    with (folder/'dev/mmcblk0').open('rb') as stream:self.assertEqual(stream.read(34*512),before[0] if grow in ('check-failed','android-bcb-failed') else after[0])
                    self.assertEqual((folder/'dev/mmcblk0p3').read_bytes(),bytes(misc))
                    self.assertNotIn('PRESERVING_GROW',detail)
                    continue
                self.assertNotIn('FORBIDDEN_RAW_',detail)
                self.assertEqual(result.returncode,0,detail)
                self.assertTrue((folder/'rebooted').exists(),detail)
                self.assertEqual((folder/'dev'/linux_node).read_bytes(),android)
                self.assertEqual((folder/'dev'/android_node).read_bytes(),android)
                with (folder/'dev/mmcblk0').open('rb') as stream:
                    self.assertEqual(stream.read(34*512),after[0]);stream.seek((SECTORS-33)*512)
                    self.assertEqual(stream.read(),after[1]);stream.seek(GIB)
                    self.assertEqual(stream.read(17),b'KEEP ANDROID DATA');stream.seek(400000*512)
                    self.assertEqual(stream.read(12),b'KEEP NV DATA')
                resulting=(folder/'dev/mmcblk0p3').read_bytes()
                self.assertEqual(resulting[:2048],bytes(misc[:2048]) if finished or not wipe else bytes(bcb))
                self.assertNotIn(IDENTITY,detail)

    def test_backup_first_commit_and_mixed_copy_recovery_on_test_files(self):
        original,before=layouts();after,*_=restored_layout(original,before,SECTORS,'e5root')
        functions=WORKER.split('# BEGIN DEVICE WORKER')[0]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name,data in zip(('before-first','before-last','after-first','after-last'),(*before,*after)):
                (folder/name).write_bytes(data)
            for mixed in (False,True):
                disk=folder/'disk.bin'
                with disk.open('wb') as stream:
                    stream.truncate(SECTORS*512);stream.write(before[0]);stream.seek((SECTORS-33)*512)
                    stream.write(after[1] if mixed else before[1])
                script=functions+'\ndisk=./disk.bin; total='+str(SECTORS)+'; front=34; tail='+str(SECTORS-33)+'\nsnapshot_commit\n'
                result=subprocess.run(['busybox','ash','-ec',script],cwd=folder,capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                with disk.open('rb') as stream:
                    self.assertEqual(stream.read(34*512),after[0]);stream.seek((SECTORS-33)*512)
                    self.assertEqual(stream.read(),after[1])

    def test_failed_backup_write_never_updates_primary_and_unknown_layout_is_read_only(self):
        original,before=layouts();after,*_=restored_layout(original,before,SECTORS,'e5root')
        functions=WORKER.split('# BEGIN DEVICE WORKER')[0]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name,data in zip(('before-first','before-last','after-first','after-last'),(*before,*after)):
                (folder/name).write_bytes(data)
            disk=folder/'disk.bin'
            with disk.open('wb') as stream:
                stream.truncate(SECTORS*512);stream.write(before[0]);stream.seek((SECTORS-33)*512);stream.write(before[1])
            prefix=functions+'\ndisk=./disk.bin; total='+str(SECTORS)+'; front=34; tail='+str(SECTORS-33)+'\n'
            fail='dd() { case "$*" in *if=after-last*) return 7;; esac; command dd "$@"; }\n'
            result=subprocess.run(['busybox','ash','-ec',prefix+fail+'snapshot_commit'],cwd=folder,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            with disk.open('rb') as stream:self.assertEqual(stream.read(34*512),before[0])
            corrupt=bytearray(before[0]);corrupt[100]^=1
            with disk.open('r+b') as stream:stream.write(corrupt)
            result=subprocess.run(['busybox','ash','-ec',prefix+'snapshot_commit'],cwd=folder,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            with disk.open('rb') as stream:
                self.assertEqual(stream.read(34*512),bytes(corrupt));stream.seek((SECTORS-33)*512)
                self.assertEqual(stream.read(),before[1])


if __name__=='__main__':unittest.main()
