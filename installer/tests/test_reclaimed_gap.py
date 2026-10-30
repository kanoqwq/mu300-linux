"""Real GPT and shell writes against sparse regular files, never a device.

Replays uninstall/grow -> shrink -> stale root header -> install handoff,
including transport loss on either side of the bounded cleanup write.
"""
from copy import deepcopy
from contextlib import contextmanager
import io
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_online_shrink as online_tests
from test_online_shrink import sb
from backend import gpt, storage
from backend.adapter import Adapter
from backend.online_shrink import OnlineShrink
from backend.service import Service
from backend.state import Context, RecoveryRequired


@unittest.skipUnless(sys.platform.startswith('linux'),'requires Linux dd/fsync and sha256sum for the actual Android shell recipe')
class ReclaimedGapTests(unittest.TestCase):
    @contextmanager
    def fixture(self,block=4096,kind='e5',fs='ext4',interior=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job,r,a,old,new=online_tests.OnlineTests().job(temp,block,kind)
            j=r['online_shrink'];j['state']='gpt-committed'
            disk=root/'disk';boot=root/'boot';size=root/'size'
            boot.write_text('after\n');size.write_text(str(j['new_sectors'])+'\n')
            with disk.open('wb') as out:
                out.truncate(j['sectors']*512)
                out.seek(0);out.write(new[0]);out.seek(j['tail']*512);out.write(new[1])
            start=j['gap_start'];offsets=[start]+([start+4096] if interior else [])
            original=bytearray(b'\xa7'*8192)
            if fs=='ext4':original[1080:1082]=b'\x53\xef'
            else:original[:]=sb()
            original=bytes(original)
            def write(offset,data):
                with disk.open('r+b') as out:out.seek(offset*512);out.write(data)
            def read_at(offset,count):
                with disk.open('rb') as src:src.seek(offset*512);return src.read(count)
            for offset in offsets:write(offset,original)
            actual=deepcopy(r['device']);actual['boot_id']='after'
            actual['storage']['SIZE']=j['new_sectors'];actual['userdata_bytes']=j['new_sectors']*512
            mutations=[]
            def pair():return read_at(0,len(new[0])),read_at(j['tail'],len(new[1]))
            def probe(*_):
                result=deepcopy(actual);space=gpt.gpt_space(*pair(),j['sectors'])
                signatures=[];safe=[]
                for at,n in space['gaps']:
                    found=[]
                    for offset in set([at]+[i for i in offsets if at<=i<at+n]):
                        raw=read_at(offset,2048)
                        fs='ext4' if raw[1080:1082]==b'\x53\xef' else 'f2fs' if raw[1024:1028]==bytes.fromhex('1020f5f2') else None
                        if fs:found.append(dict(offset=offset,kind=fs))
                    signatures.extend(found)
                    if not found:safe.append((at,n))
                result['gpt']=dict(space,safe_gaps=safe,gap_signatures=signatures)
                return result
            def read(command):
                m=re.fullmatch(r'dd if=/dev/block/\w+ bs=512 skip=(\d+) count=16 2>/dev/null',command)
                if not m:raise AssertionError(command)
                return read_at(int(m[1]),8192)
            rawdisk='/dev/block/'+storage.disk_node(actual['storage']['NODE'])
            def shell(command,**kwargs):
                self.assertTrue(command.startswith('set -eu; set -o pipefail;'))
                script=command.replace(rawdisk,shlex.quote(str(disk)))
                script=script.replace('/proc/sys/kernel/random/boot_id',shlex.quote(str(boot)))
                script=script.replace('/sys/class/block/'+actual['storage']['NODE']+'/size',shlex.quote(str(size)))
                self.assertNotIn('/dev/block/',script);self.assertNotIn('/sys/',script);self.assertNotIn('/proc/',script)
                mutations.append(command)
                result=subprocess.run(['bash','-c',script],capture_output=True,text=True)
                if result.returncode:raise ConnectionError(result.stderr or 'guard refused write')
                return result.stdout
            a.probe.side_effect=probe;a.adb.read.side_effect=read;a.adb.shell.side_effect=shell
            info=dict(available=True,uuid=j['uuid'],blocks=j['target_blocks'],volume=dict(mapped_bytes=j['new_sectors']*512))
            with patch.object(OnlineShrink,'pair',side_effect=pair),patch('backend.online_shrink.inspect',return_value=info),patch('backend.service.inspect_online',return_value=info):
                yield dict(job=job,r=r,a=a,j=j,start=start,offsets=offsets,original=original,actual=actual,
                           probe=probe,read=read_at,write=write,shell=shell,mutations=mutations,new=new,pair=pair,
                           info=info,boot=boot,size=size)

    def test_emmc_ufs_both_repositories_install_handoff_with_old_headers(self):
        for block in (512,4096):
            for kind in ('e5','mu300'):
                for fs in ('ext4','f2fs'):
                    with self.subTest(block=block,kind=kind,fs=fs),self.fixture(block,kind,fs,True) as f:
                        r,a,job=f['r'],f['a'],f['job'];a.password='';a.execute=Mock()
                        service=Service('.',job.ctx.store,io.StringIO())
                        service.openwrt_wait_stage=Mock();service.wait_openwrt=Mock()
                        service.run(job.ctx,a)
                        self.assertIsNone(r.get('error'),r.get('error'));self.assertEqual(f['j']['state'],'verified')
                        self.assertFalse(r['unsafe']);self.assertEqual(r['plan']['target'],'GPT unallocated space')
                        a.execute.assert_called_once();a.adb.call.assert_not_called()
                        self.assertEqual(f['pair'](),f['new'])
                        self.assertEqual(len(f['mutations']),2)
                        for offset in f['offsets']:
                            self.assertEqual(f['read'](offset,8192),bytes(8192))
                            self.assertEqual((job.directory/'reclaimed-headers'/f'{offset}.bin').read_bytes(),f['original'])
                        # Retry is a no-op: no resize, no extra writes or backup growth.
                        job.run();self.assertEqual(len(f['mutations']),2)

    def test_disconnect_before_after_and_halfway_through_write_is_resumable(self):
        for phase in ('before','after','half'):
            with self.subTest(phase=phase),self.fixture(fs='f2fs',interior=True) as f:
                def interrupted(command,**kw):
                    if phase=='after':f['shell'](command,**kw)
                    elif phase=='half':f['write'](f['start'],bytes(4096))
                    raise ConnectionError('fixture disconnected')
                f['a'].adb.shell.side_effect=interrupted
                with self.assertRaises(ConnectionError):f['job'].run()
                self.assertTrue(f['r']['unsafe'])
                saved=f['job'].ctx.store.load(f['r']['id'])
                f['a'].ctx=Context(f['job'].ctx.store,saved,lambda *_:None)
                f['a'].adb.shell.side_effect=f['shell']
                OnlineShrink(f['a']).run()
                self.assertEqual(saved['online_shrink']['state'],'verified');self.assertFalse(saved['unsafe'])
                self.assertEqual(f['pair'](),f['new'])
                for offset in f['offsets']:self.assertEqual(f['read'](offset,8192),bytes(8192))

    def test_no_write_on_changed_device_gpt_geometry_or_filesystem(self):
        for bad in ('cid','slot','profile','NODE','START','DISK_SIZE','uuid','blocks','mapping','gpt'):
            with self.subTest(bad=bad),self.fixture() as f:
                if bad in ('cid','slot','profile'):f['actual'][bad]='wrong'
                elif bad in ('NODE','START','DISK_SIZE'):f['actual']['storage'][bad]='wrong'
                elif bad=='mapping':f['info']['volume']['mapped_bytes']-=4096
                elif bad=='gpt':f['write'](0,b'corrupt')
                else:f['info'][bad]='wrong'
                with self.assertRaises(RecoveryRequired):f['job'].run()
                self.assertEqual(f['mutations'],[])
                self.assertEqual(f['read'](f['start'],8192),f['original'])

    def test_unrelated_or_cross_boundary_signature_is_never_cleared(self):
        for position in ('old-gap','overlap'):
            with self.subTest(position=position),self.fixture() as f:
                end=f['start']+f['r']['plan']['openwrt_bytes']//512
                offset=end+2048 if position=='old-gap' else end-8
                f['offsets'].append(offset);f['write'](offset,f['original'])
                with self.assertRaisesRegex(RecoveryRequired,'outside'):f['job'].run()
                self.assertEqual(f['mutations'],[])
                self.assertEqual(f['read'](f['start'],8192),f['original'])

    def test_changed_header_since_probe_never_cleared(self):
        with self.fixture() as f:
            def changed(command):
                f['write'](f['start'],b'changed'.ljust(8192,b'!'))
                return f['read'](f['start'],8192)
            f['a'].adb.read.side_effect=changed
            with self.assertRaisesRegex(RecoveryRequired,'after probing'):f['job'].run()
            self.assertEqual(f['mutations'],[])

    def test_device_side_guards_prevent_write_after_host_read(self):
        for bad in ('boot','size','gpt','header'):
            with self.subTest(bad=bad),self.fixture() as f:
                def changed(command,**kw):
                    if bad=='boot':f['boot'].write_text('new-boot\n')
                    elif bad=='size':f['size'].write_text('123\n')
                    elif bad=='gpt':f['write'](0,b'changed')
                    else:f['write'](f['start'],b'changed')
                    return f['shell'](command,**kw)
                f['a'].adb.shell.side_effect=changed
                with self.assertRaises(ConnectionError):f['job'].run()
                self.assertNotEqual(f['read'](f['start'],8192),bytes(8192))

    def test_tampered_backup_or_unexplained_partial_write_rejected_on_resume(self):
        for bad in ('backup','header'):
            with self.subTest(bad=bad),self.fixture() as f:
                f['a'].adb.shell.side_effect=ConnectionError('before write')
                with self.assertRaises(ConnectionError):f['job'].run()
                if bad=='backup':(f['job'].directory/'reclaimed-headers'/f"{f['start']}.bin").write_bytes(b'tampered')
                else:f['write'](f['start']+8,b'unknown'.ljust(4096,b'!'))
                f['a'].adb.shell.side_effect=f['shell']
                with self.assertRaises(RecoveryRequired):f['job'].run()
                self.assertEqual(f['mutations'],[])

    def test_guarded_cleanup_does_not_repeat_successful_resize(self):
        with self.fixture() as f,patch.object(OnlineShrink,'stage') as stage:
            f['job'].run();stage.assert_not_called();f['a'].adb.call.assert_not_called()
            self.assertTrue(all('of=/dev/block/' in c and 'count=2 conv=notrunc,fsync' in c for c in f['mutations']))

    def test_actual_adapter_gap_scanner_protects_then_accepts_reclaimed_header(self):
        for block in (512,4096):
            for kind in ('e5','mu300'):
                with self.subTest(block=block,kind=kind),self.fixture(block,kind) as f:
                    scanner=Adapter.__new__(Adapter);scanner.kind=kind;scanner.adb=Mock()
                    def read(command):
                        m=re.fullmatch(r'dd if=/dev/block/\w+ bs=(\d+)(?: skip=(\d+))? count=(\d+) 2>/dev/null',command)
                        self.assertIsNotNone(m,command)
                        return f['read'](int(m[1])*int(m[2] or '0')//512,int(m[1])*int(m[3]))
                    scanner.adb.read.side_effect=read
                    s=dict(f['actual']['storage'],SECTOR_SIZE=block)
                    space=scanner.read_gpt(s)
                    self.assertIn({'offset':f['start'],'kind':'ext4'},space['gap_signatures'])
                    self.assertFalse(any(a<=f['start']<a+n for a,n in space['safe_gaps']))
                    f['job'].run()
                    clean=scanner.read_gpt(s)
                    self.assertTrue(any(a<=f['start']<a+n for a,n in clean['safe_gaps']))
                    # The generic scanner still rejects a new unrelated raw root.
                    f['write'](f['start'],f['original'])
                    protected=scanner.read_gpt(s)
                    self.assertFalse(any(a<=f['start']<a+n for a,n in protected['safe_gaps']))


if __name__=='__main__':unittest.main()
