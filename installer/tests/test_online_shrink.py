"""Preservation/erase boundaries, real GPT bytes, and restart checkpoints.

No device writes or hardware success claims. Native ioctl is exercised only by
the separately authorized Changxing60 experiment documented in docs/.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import gpt
from backend.online_shrink import (OnlineShrink, OnlineDecision, allocation, candidate,
    fallback_plan, helper_files, inspect, superblocks)
from backend.service import plan_install, Service
from backend.state import Context, Pending, RecoveryRequired, Store, sha
from backend.uninstall import partition, restored_layout, table
import uuid
import zlib


def snapshot(block, sectors=64*2097152):
    factor=block//512;total=sectors//factor;count=128;rows=(count*128+block-1)//block
    table=bytearray(count*128)
    for number,name,start,end in ((1,'misc',2048,4095),(2,'userdata',4096,16*2097152-1)):
        e=bytearray(128);e[:16]=uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
        e[16:32]=uuid.UUID(int=number).bytes_le
        struct.pack_into('<QQ',e,32,start//factor,(end+1)//factor-1)
        text=name.encode('utf-16-le');e[56:56+len(text)]=text;table[(number-1)*128:number*128]=e
    def header(at,other,entries):
        h=bytearray(block);h[:8]=b'EFI PART';struct.pack_into('<II',h,8,0x10000,92)
        struct.pack_into('<QQQQ',h,24,at,other,2+rows,total-2-rows)
        h[56:72]=uuid.UUID(int=999).bytes_le
        struct.pack_into('<QIII',h,72,entries,count,128,zlib.crc32(table))
        struct.pack_into('<I',h,16,zlib.crc32(h[:92]));return h
    first=bytes(block)+header(1,total-1,2)+table
    last=table+header(total-1,1,total-1-rows)
    return first,last,sectors



def sb(blocks=3932160):
    raw=bytearray(8192);s=bytearray(3072)
    struct.pack_into('<I',s,0,0xf2f52010)
    struct.pack_into('<II',s,16,12,9);struct.pack_into('<I',s,24,1)
    struct.pack_into('<Q',s,36,blocks);s[108:124]=bytes(range(1,17))
    raw[1024:4096]=s;raw[5120:8192]=s
    return bytes(raw)


def device(block=4096):
    first,last,total=snapshot(block)
    data=partition(gpt.gpt_space(first,last,total),'userdata')
    size=data['end']-data['start']+1
    d=dict(serial='fixture',cid='a'*32,profile='fixture',model='fixture',slot='_b',boot_id='before',
        readiness='ready',encrypted=True,encryption_type='file',existing=False,free_bytes=12<<30,
        userdata_bytes=size*512,storage=dict(NODE='sdb'+str(data['n']),CID='a'*32,START=data['start'],
        SIZE=size,DISK_SIZE=total,SOURCE='/dev/block/dm-7'),gpt=dict(safe_gaps=[],empty=[3]))
    d['online_shrink']=dict(available=True,blocks=3932160,section_blocks=512,uuid=bytes(range(1,17)).hex(),
        volume={'source':'/dev/block/dm-7','mapped_bytes':size*512})
    return d,(first,last),total


class OnlineTests(unittest.TestCase):
    def test_two_superblocks_and_format_rejection(self):
        self.assertEqual(superblocks(sb())['blocks'],3932160)
        for offset in (1024,5120,1060,5228):
            raw=bytearray(sb());raw[offset]^=1
            with self.subTest(offset=offset),self.assertRaises(ValueError):superblocks(raw)
        with self.assertRaises(ValueError):superblocks(sb()[:-1])

    def test_prefer_online_but_keep_explicit_format_and_existing_free_space(self):
        for kind in ('e5','mu300'):
            for slot in ('_a','_b'):
                d,_,_=device();d['slot']=slot;p={'adapter':kind}
                plan=plan_install(p,d,dict(method='part',size_gib=4))
                self.assertEqual(plan['shrink_strategy'],'online-f2fs');self.assertFalse(plan['wipe'])
                self.assertTrue(plan['two_stage']);self.assertEqual(plan['new_sectors']%2048,0)
                original=plan_install(p,d,dict(method='part',size_gib=4,shrink_strategy='format',wipe=True))
                self.assertTrue(original['wipe']);self.assertTrue(original['offline'])
                self.assertNotIn('shrink_strategy',original)
                d['gpt']['safe_gaps']=[(100000000,100000000)]
                gap=plan_install(p,d,dict(method='part',size_gib=4))
                self.assertEqual(gap['target'],'GPT unallocated space');self.assertFalse(gap['wipe'])

    def test_insufficient_online_space_offers_clearly_destructive_plan(self):
        d,_,_=device();d['free_bytes']=1<<30
        plan=plan_install({'adapter':'e5'},d,dict(method='part',size_gib=4))
        self.assertTrue(plan['wipe']);self.assertIn('online_unavailable',plan)
        d['gpt']['empty']=[]
        with self.assertRaisesRegex(ValueError,'GPT'):plan_install({'adapter':'e5'},d,dict(method='part',size_gib=4))

    def test_real_512_4kn_emmc_ufs_gpt_and_uninstall_origin(self):
        for block,nodekind in ((512,'mmcblk1p'),(4096,'sdb')):
            d,pair,total=device(block);data=partition(gpt.gpt_space(*pair,total),'userdata')
            node=nodekind+str(data['n']);new=(data['end']-data['start']+1-8388608)//2048*2048
            resized=candidate(pair,total,node,d['storage']['SIZE'],new)
            old,newtable=table(pair[0]),table(resized[0]);at=(data['n']-1)*128
            self.assertEqual(old[:at+40]+old[at+48:],newtable[:at+40]+newtable[at+48:])
            for name in ('e5root','mu300root'):
                root=gpt.gpt_add_root(*resized,total,data['start']+new,8388608,name)
                restored,prior,_,grew=restored_layout(pair,root,total,name)
                self.assertEqual(prior['end'],data['end']);self.assertTrue(grew)
                self.assertEqual(tuple(restored),pair)
            with self.assertRaises(RecoveryRequired):candidate(pair,total,node,d['storage']['SIZE']+1,new)

    def test_mapping_and_encryption_eligibility_do_not_mutate(self):
        d,_,_=device();a=SimpleNamespace(adb=Mock())
        volume=dict(source='/dev/block/dm-7',mapping_overhead=0,filesystem='f2fs',uuid=d['online_shrink']['uuid'])
        a.adb.shell.return_value=d['storage']['NODE'];a.adb.read.return_value=sb()
        with patch('backend.online_shrink.android_volume',return_value=volume):
            self.assertTrue(inspect(a,d)['available'])
            a.adb.shell.return_value='sdc4'
            self.assertFalse(inspect(a,d)['available'])
            volume['mapping_overhead']=4096
            self.assertFalse(inspect(a,d)['available'])
            volume['mapping_overhead']=0;d['encryption_type']='block'
            self.assertFalse(inspect(a,d)['available'])
        self.assertFalse(any(' of=' in str(c) for c in a.adb.mock_calls))

    def job(self,temp,block=4096,kind='e5'):
        d,pair,total=device(block);store=Store(Path(temp));plan=plan_install({'adapter':kind},d,dict(method='part',size_gib=4))
        if block==512:d['storage']['NODE']='mmcblk0p2'
        r=store.create({'adapter':kind},d,dict(method='part',size_gib=4),plan)
        ctx=Context(store,r,lambda *_:None);a=SimpleNamespace(ctx=ctx,kind=kind,bind=Mock(),adb=Mock(),probe=Mock(return_value=deepcopy(d)))
        a.adb.shell.return_value='busy=0'
        job=OnlineShrink(a);job.directory.mkdir()
        new=tuple(candidate(pair,total,d['storage']['NODE'],d['storage']['SIZE'],plan['new_sectors']))
        files=dict(zip(('old-first','old-last','new-first','new-last'),(*pair,*new)))
        for n,raw in files.items():(job.directory/n).write_bytes(raw)
        r['online_shrink']=dict(version=1,state='started',front=len(pair[0])//512,tail=total-len(pair[1])//512,
            sectors=total,uuid=d['online_shrink']['uuid'],old_blocks=d['online_shrink']['blocks'],boot_id='before',
            **{k:plan[k] for k in ('target_blocks','new_sectors','gap_start')},files={n:sha(job.directory/n) for n in files})
        return job,r,a,pair,new

    def test_new_gpt_old_boot_only_reboots_never_repeats_resize(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,old,new=self.job(temp);info=dict(r['device']['online_shrink'],blocks=r['online_shrink']['target_blocks'])
            with patch.object(job,'pair',return_value=new),patch('backend.online_shrink.inspect',return_value=info),patch.object(job,'stage') as stage:
                with self.assertRaises(Pending):job.run()
                stage.assert_not_called();a.adb.call.assert_called_once_with('reboot')
                self.assertEqual(r['online_shrink']['state'],'gpt-committed')

    def test_commit_resumed_in_later_android_boot_waits_for_that_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,_,new=self.job(temp)
            a.probe.return_value=deepcopy(r['device']);a.probe.return_value['boot_id']='intermediate'
            info=dict(r['device']['online_shrink'],blocks=r['online_shrink']['target_blocks'])
            with patch.object(job,'pair',return_value=new),patch('backend.online_shrink.inspect',return_value=info):
                with self.assertRaises(Pending):job.run()
            self.assertEqual(r['device']['boot_id'],'intermediate')
            self.assertEqual(r['online_shrink']['commit_boot_id'],'intermediate')
            a.adb.call.assert_called_once_with('reboot')

    def test_reboot_verifies_uuid_capacity_mapping_and_gap_before_install(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,old,new=self.job(temp);actual=deepcopy(r['device']);actual['boot_id']='after'
            actual['storage']['SIZE']=r['online_shrink']['new_sectors']
            actual['gpt']['safe_gaps']=[(r['plan']['gap_start'],r['plan']['openwrt_bytes']//512)]
            a.probe.return_value=actual
            info=dict(r['device']['online_shrink'],blocks=r['online_shrink']['target_blocks'],volume={'mapped_bytes':actual['storage']['SIZE']*512})
            with patch.object(job,'pair',return_value=new),patch('backend.online_shrink.inspect',return_value=info):
                job.run()
            self.assertEqual(r['online_shrink']['state'],'verified');self.assertFalse(r['unsafe'])
            self.assertEqual(r['device']['boot_id'],'after');self.assertEqual(r['plan']['target'],'GPT unallocated space')
            a.adb.call.assert_not_called()

    def test_erasing_decision_requires_unchanged_verified_state(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,old,new=self.job(temp)
            with patch.object(job,'unchanged',return_value=False):
                with self.assertRaises(RecoveryRequired):job.decision('ioctl error')
                self.assertNotIn('online_fallback',r)
            with patch.object(job,'unchanged',return_value=True):
                with self.assertRaises(OnlineDecision):job.decision('unsupported')
                self.assertFalse(r['unsafe']);self.assertFalse(r['plan']['wipe'])
                self.assertEqual(r['online_fallback']['state'],'awaiting')

    def test_prepare_backs_up_before_launch_and_rejects_changed_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,pair,_=self.job(temp);r.pop('online_shrink')
            space=gpt.gpt_space(*pair,r['device']['storage']['DISK_SIZE'])
            a.read_gpt=Mock(return_value=dict(space,first=pair[0],last=pair[1]))
            a.backup=Mock();a.adb.shell.return_value='sdb3'
            def metadata(name,path,context):
                a.backup.assert_called_once();self.assertEqual(name,'metadata')
                path.write_bytes(b'private metadata fixture')
            a.adb.backup_partition.side_effect=metadata
            with patch('backend.online_shrink.inspect',return_value=r['device']['online_shrink']):
                job.prepare()
            self.assertEqual(r['online_shrink']['state'],'prepared');self.assertIn('metadata.bin',r['online_shrink']['files'])
            self.assertEqual(set(job.files()),{'old-first','old-last','new-first','new-last'})
            a.adb.call.assert_not_called()
            r.pop('online_shrink');a.backup.reset_mock();a.probe.return_value['existing']=True
            with self.assertRaises(RecoveryRequired):job.prepare()
            a.backup.assert_not_called()

    def test_new_format_plan_requires_nonce_yes_and_unchanged_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            job,r,a,_,_=self.job(temp);r['online_fallback']=dict(state='awaiting',nonce='one',reason='unsupported')
            service=SimpleNamespace(active=None,adapter=Mock(return_value=a),store=job.ctx.store,emit=lambda *_:None,public=lambda r:r)
            for params in ({},{'nonce':'bad','confirmation':'yes'},{'nonce':'one','confirmation':'no'}):
                with self.assertRaises(ValueError):fallback_plan(service,r,params)
            with patch.object(OnlineShrink,'unchanged',return_value=False):
                with self.assertRaises(RecoveryRequired):fallback_plan(service,r,dict(nonce='one',confirmation='yes'))
            with patch.object(OnlineShrink,'unchanged',return_value=True):
                fallback_plan(service,r,dict(nonce='one',confirmation='yes'))
            self.assertTrue(r['plan']['wipe']);self.assertEqual(r['status'],'ready')
            self.assertEqual(r['choices']['shrink_strategy'],'format');a.adb.call.assert_not_called()

    def test_helper_hash_and_frozen_resource_layout(self):
        binary,metadata=helper_files();info=json.loads(metadata.read_text())
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);(directory/'online-helper').mkdir()
            (directory/'f2fs-online-arm64').write_bytes(binary.read_bytes())
            (directory/'online-helper/build.json').write_text(json.dumps(info))
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(directory),create=True):
                helper_files()
                (directory/'f2fs-online-arm64').write_bytes(b'corrupt')
                with self.assertRaises(ValueError):helper_files()

    def simulate(self, temp, *, interrupt=False, reject=False, cancel=False, mixed=False, launch_reject=False):
        job,r,a,old,new=self.job(temp)
        r['online_shrink']['state']='prepared'
        service=Service('.',job.ctx.store,io.StringIO())
        service.openwrt_wait_stage=Mock();service.wait_openwrt=Mock()
        ctx=job.ctx;a.password='';a.execute=Mock()
        machine=dict(pair=old,blocks=r['online_shrink']['old_blocks'],boot='before',launches=0)
        if mixed:
            machine.update(pair=(old[0],new[1]),blocks=r['online_shrink']['target_blocks'])
            r['online_shrink']['state']='started'
        def current(*_):
            d=deepcopy(r['device']);d['boot_id']=machine['boot']
            if machine['boot']=='after':
                d['storage']['SIZE']=r['online_shrink']['new_sectors']
                d['userdata_bytes']=d['storage']['SIZE']*512
                d['gpt']['safe_gaps']=[(r['plan']['gap_start'],r['plan']['openwrt_bytes']//512)]
            return d
        def info(*_):
            d=current();return dict(available=True,blocks=machine['blocks'],section_blocks=512,
                uuid=r['online_shrink']['uuid'],volume=dict(source='/dev/block/dm-7',mapped_bytes=d['userdata_bytes']))
        def shell(command,**_):
            if 'lock-status' in command:
                if command.startswith('cat '):return 'phase=complete\nbusy=0'
                return 'busy=0'
            if ' probe /data ' in command:
                if reject:raise ConnectionError('errno=25')
                return 'same_size_probe=OK'
            if ' start /data ' in command:
                machine['launches']+=1
                if launch_reject:raise ConnectionError('permission denied before fork')
                machine['pair']=new;machine['blocks']=r['online_shrink']['target_blocks']
                if cancel:ctx.cancel=True
                if interrupt and machine['launches']==1:raise ConnectionError('ADB disconnected after fork')
                return 'worker_started=42'
            raise AssertionError(command)
        def call(command):
            self.assertEqual(command,'reboot');machine['boot']='after'
        a.adb.shell.side_effect=shell;a.adb.call.side_effect=call;a.probe.side_effect=current
        with patch.object(OnlineShrink,'pair',side_effect=lambda:machine['pair']),patch.object(OnlineShrink,'stage'),patch('backend.online_shrink.inspect',side_effect=info),patch('backend.service.inspect_online',side_effect=info):
            service.run(ctx,a)
            if interrupt:
                self.assertEqual(r['status'],'failed');self.assertTrue(r['error']['retryable'])
                a.execute.assert_not_called()
                # A new coordinator loads the durable recipe; never repeats ioctl.
                r=job.ctx.store.load(r['id']);ctx=Context(job.ctx.store,r,lambda *_:None);a.ctx=ctx
                service.run(ctx,a)
        return r,a,machine

    def test_coordinator_first_install_and_post_launch_adb_loss(self):
        for interrupt,mixed in ((False,False),(True,False),(False,True)):
            with self.subTest(interrupt=interrupt,mixed=mixed),tempfile.TemporaryDirectory() as temp:
                r,a,m=self.simulate(temp,interrupt=interrupt,mixed=mixed)
                self.assertEqual(r['online_shrink']['state'],'verified',r.get('error'))
                self.assertFalse(r['plan']['wipe']);self.assertEqual(r['device']['boot_id'],'after')
                self.assertEqual(m['launches'],1);a.execute.assert_called_once()

    def test_coordinator_rejected_ioctl_never_calls_installer_or_wipes(self):
        with tempfile.TemporaryDirectory() as temp:
            r,a,m=self.simulate(temp,reject=True)
            self.assertEqual(r['online_fallback']['state'],'awaiting');self.assertFalse(r['plan']['wipe'])
            self.assertEqual(m['launches'],0);a.execute.assert_not_called();a.adb.call.assert_not_called()

    def test_rejected_worker_launch_offers_fallback_only_after_unchanged_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            r,a,m=self.simulate(temp,launch_reject=True)
            self.assertEqual(r['online_fallback']['state'],'awaiting');self.assertFalse(r['unsafe'])
            self.assertFalse(r['plan']['wipe']);a.execute.assert_not_called();a.adb.call.assert_not_called()

    def test_cancel_during_worker_is_deferred_until_gpt_is_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            r,a,m=self.simulate(temp,cancel=True)
            self.assertEqual(r['status'],'cancelled');self.assertFalse(r['unsafe'])
            self.assertEqual(r['online_shrink']['state'],'gpt-committed')
            self.assertEqual(m['launches'],1);a.execute.assert_not_called()


if __name__=='__main__':unittest.main()
