"""No-original-record removal: real metadata parsing, bounded GPT and recovery."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_uninstall as fx
from backend import gpt
from backend.install_evidence import Ext4Evidence, inspect_installation
from backend.state import Store, Context, RecoveryRequired
from backend.uninstall import prepare, persist, Uninstaller, device_layout
from backend.uninstall_growth import PreservationUnavailable
from backend.uninstall_boot import overlay, archives, entries


def marker_files(family):
    release = b"DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='test'\n"
    return {'etc/e5/part-root':b'e5root /dev/mmcblk0\n','etc/openwrt_release':release} if family == 'e5' else {
        'openwrt/etc/mu300/desktop-mode':b'part\n','.mu300/boot-os':b'android\n','openwrt/etc/openwrt_release':release}


def image(files, block_size=4096, extents=True):
    """Small synthetic, nonmountable metadata image; never a block device."""
    data = bytearray(1 << 20); sb = memoryview(data)[1024:2048]
    struct.pack_into('<I',sb,0,128);struct.pack_into('<I',sb,4,len(data)//block_size)
    struct.pack_into('<I',sb,24,{1024:0,2048:1,4096:2}[block_size])
    struct.pack_into('<I',sb,40,128);sb[56:58]=b'\x53\xef';struct.pack_into('<H',sb,58,1)
    struct.pack_into('<H',sb,88,256);struct.pack_into('<I',sb,96,0x42 if extents else 2)
    sb[104:120]=b'U'*16
    descriptor=2 if block_size==1024 else 1
    struct.pack_into('<I',data,descriptor*block_size+8,8)
    dirs = {'':{}};nodes={'':2};allfiles={}
    for path,content in files.items():
        parts=path.split('/')
        for i,name in enumerate(parts):
            parent='/'.join(parts[:i]);full='/'.join(parts[:i+1])
            if full not in nodes:
                nodes[full]=len(nodes)+2;dirs[parent][name]=nodes[full]
            if i<len(parts)-1:dirs.setdefault(full,{})
        allfiles[path]=content
    block=64
    for path,number in nodes.items():
        directory=path in dirs
        if directory:
            content=bytearray(block_size);at=0
            values=list(dirs[path].items())
            for i,(name,child) in enumerate(values):
                name=name.encode();length=(8+len(name)+3)&~3
                if i==len(values)-1:length=block_size-at
                struct.pack_into('<IHBB',content,at,child,length,len(name),0)
                content[at+8:at+8+len(name)]=name;at+=length
        else:content=allfiles[path]
        offset=8*block_size+(number-1)*256
        struct.pack_into('<H',data,offset,(stat.S_IFDIR if directory else stat.S_IFREG)|0o755)
        struct.pack_into('<I',data,offset+4,len(content));struct.pack_into('<H',data,offset+26,1)
        if extents:
            struct.pack_into('<I',data,offset+32,0x80000)
            struct.pack_into('<HHHHI',data,offset+40,0xf30a,1,4,0,0)
            struct.pack_into('<IHHI',data,offset+52,0,1,0,block)
        else:struct.pack_into('<I',data,offset+40,block)
        data[block*block_size:block*block_size+len(content)]=content;block+=1
    return bytes(data)


def linux_boot(family, mode=b'part\n'):
    base=fx.boot(family)
    init=b'#!/bin/busybox sh\n# /etc/e5/part-root\n' if family=='e5' else b'#!/bin/busybox sh\n. /etc/mu300-desktop-storage.sh\n'
    files={'init':init}
    if family=='mu300':files['etc/mu300-desktop-root-mode']=mode
    ram=overlay(files);header=bytearray(base[:4096]);struct.pack_into('<I',header,12,len(ram))
    body=bytes(header)+base[4096:8192]+ram;body=body.ljust((len(body)+4095)&~4095,b'\0')
    footer=bytearray(base[-64:]);struct.pack_into('>QQQ',footer,12,len(body),len(body),128)
    return (body+b'V'*128).ljust(len(base)-64,b'\0')+footer


class NoOriginalRecord(unittest.TestCase):
    def setUp(self):
        self.fixture=fx.Journal();self.fixture.setUp();self.configure()
    def tearDown(self):self.fixture.tearDown()

    def configure(self,family='e5',slot='a',adjacent=True):
        f=self.fixture;f.configure(family,slot)
        self.store=Store(Path(f.temp.name)/('fresh-'+family+'-'+slot))
        self.before=f.before
        if not adjacent:
            table=bytearray(f.before[0][1024:])
            start,end=struct.unpack_from('<QQ',table,4*128+32)
            struct.pack_into('<QQ',table,4*128+32,start+2*fx.GIB//512,end+2*fx.GIB//512)
            self.before=fx.copies(table)
        space=gpt.gpt_space(*self.before,fx.SECTORS);root=next(p for p in space['parts'] if p['name'].endswith('root'))
        f.adapter.read_gpt=Mock(return_value={**space,'first':self.before[0],'last':self.before[1]})
        f.device['storage'].update(ROOT_START=root['start'],ROOT_SIZE=root['end']-root['start']+1)
        f.device.update(encrypted=False,encryption_state='unencrypted',encryption_type='')
        self.fs=image(marker_files(family));self.boot=linux_boot(family)
        self.idle='IDLE';self.root=root
        self.other='b' if slot=='a' else 'a'
        def shell(command,**_):
            if 'echo IDLE' in command:return self.idle
            if command.startswith('blockdev '):return str(64 << 20)
            if command.startswith('readlink '):return '/dev/block/mmcblk0p4'
            if command.startswith('cat /sys/class/block/mmcblk0p4/size'):return str(28*fx.GIB//512)
            raise AssertionError(command)
        def read(command):
            match=re.fullmatch(r'dd if=(\S+) bs=(\d+)(?: skip=(\d+))? count=(\d+) 2>/dev/null',command)
            if not match:raise AssertionError(command)
            path,bs,skip,count=match[1],int(match[2]),int(match[3] or 0),int(match[4])
            if path.endswith('boot_'+self.other):source=self.boot
            elif path.endswith('mmcblk0p5'):source=self.fs
            elif path.endswith('mmcblk0p4'):source=bytes(1024)+fx.superblock(28*fx.GIB)
            else:raise AssertionError(command)
            return source[bs*skip:bs*(skip+count)]
        f.adb.shell=Mock(side_effect=shell);f.adb.read=Mock(side_effect=read)

    def plan(self,reset=None):
        f=self.fixture
        with patch('backend.uninstall.growth_tools',return_value={'openwrt-uninstall/tools/fixture':b'tool'}),patch('backend.uninstall.base_boot'),patch('backend.uninstall.build'):
            plan,meta,files=prepare(f.adapter,self.store,f.device,reset)
        r=self.store.create(f.package,f.device,{'method':'uninstall'},plan)
        context=Context(self.store,r,lambda *_:None);persist(context,meta,files)
        return context

    def test_empty_computer_store_checks_real_markers_and_grows_only_adjacent_root(self):
        for family,slot in (('e5','a'),('mu300','a'),('mu300','b')):
            self.configure(family,slot);ctx=self.plan();r=ctx.record
            self.assertEqual(r['uninstall']['version'],3)
            self.assertEqual(r['plan']['layout_source'],'device');self.assertIsNone(r['plan']['source_transaction'])
            self.assertEqual(r['plan']['reclaimed_bytes'],4*fx.GIB)
            self.assertEqual(r['plan']['android_bytes'],32*fx.GIB)
            self.assertFalse(r['plan']['wipe']);self.assertEqual(r['plan']['data_action'],'grow-ext4')
            Uninstaller(None,ctx,self.fixture.adapter)
            # Sparse synthetic disk leaves >30 GiB after the root. It is not reclaimed.
            after=gpt.gpt_space((ctx.directory/'uninstall/after-first').read_bytes(),(ctx.directory/'uninstall/after-last').read_bytes(),fx.SECTORS)
            self.assertEqual(next(p for p in after['parts'] if p['name']=='userdata')['end'],self.root['end'])
            for call in self.fixture.adb.read.call_args_list:self.assertNotIn('of=',call.args[0])
            for call in self.fixture.adb.shell.call_args_list:
                self.assertNotIn('of=',call.args[0]);self.assertNotIn('mount ',call.args[0])

    def test_nonadjacent_root_is_released_without_growing_or_resetting_userdata(self):
        self.configure('mu300',adjacent=False);ctx=self.plan()
        self.assertEqual(ctx.record['plan']['data_action'],'gap')
        self.assertEqual(ctx.record['plan']['reclaimed_bytes'],0)
        self.assertEqual(ctx.record['plan']['released_unallocated_bytes'],4*fx.GIB)
        self.assertEqual(ctx.record['plan']['android_bytes'],28*fx.GIB)
        Uninstaller(None,ctx,self.fixture.adapter)

    def test_matching_original_backup_is_preferred(self):
        self.fixture.device.update(encrypted=True,encryption_state='encrypted')
        with patch('backend.uninstall.inspect_installation',side_effect=AssertionError('unnecessary evidence scan')):
            ctx=self.fixture.plan()
        self.assertEqual(ctx.record['plan']['layout_source'],'original_record')

    def test_conflicting_valid_originals_cannot_be_bypassed_by_device_fallback(self):
        f=self.fixture
        source=f.store.list()[0];folder=f.store.directory(source['id'])/'backup'
        changed=bytearray(f.original[0][1024:]);end=struct.unpack_from('<Q',changed,3*128+40)[0]
        struct.pack_into('<Q',changed,3*128+40,end+1)
        for name,data in zip(('gpt-first.bin','gpt-last.bin'),fx.copies(changed)):(folder/name).write_bytes(data)
        sums=json.loads((folder/'checksums.json').read_text())
        for name in ('gpt-first.bin','gpt-last.bin'):sums[name]=hashlib.sha256((folder/name).read_bytes()).hexdigest()
        (folder/'checksums.json').write_text(json.dumps(sums))
        with patch('backend.uninstall.inspect_installation',side_effect=AssertionError('conflict bypass')),self.assertRaisesRegex(RecoveryRequired,'disagree'):
            prepare(f.adapter,f.store,f.device)

    def test_damaged_original_backup_can_use_fresh_verified_device_evidence(self):
        f=self.fixture
        for source in f.store.list():
            (f.store.directory(source['id'])/'backup/gpt-first.bin').write_bytes(b'damaged')
        with patch('backend.uninstall.growth_tools',return_value={'tool':b'fixture'}),patch('backend.uninstall.base_boot'),patch('backend.uninstall.build'):
            plan,meta,_=prepare(f.adapter,f.store,f.device)
        self.assertEqual(plan['layout_source'],'device');self.assertEqual(meta['version'],3)

    def test_missing_foreign_symlinked_or_unclean_markers_and_mounted_roots_are_refused(self):
        for failure in ('missing','foreign','symlink','unclean','mounted','wrong-node','wrong-start','wrong-boot','pending'):
            self.configure()
            files=marker_files('e5')
            if failure=='missing':del files['etc/e5/part-root']
            if failure=='foreign':files['etc/e5/part-root']=b'e5root /dev/mmcblk1\n'
            self.fs=image(files)
            if failure=='symlink':
                raw=bytearray(self.fs);struct.pack_into('<H',raw,8*4096+(5-1)*256,stat.S_IFLNK|0o777);self.fs=bytes(raw)
            if failure=='unclean':
                raw=bytearray(self.fs);struct.pack_into('<H',raw,1024+58,2);self.fs=bytes(raw)
            if failure=='mounted':self.idle=''
            if failure=='wrong-node':self.fixture.device['storage']['ROOT']='mmcblk1p5'
            if failure=='wrong-start':self.fixture.device['storage']['ROOT_START']+=1
            if failure in ('wrong-boot','pending'):
                raw=bytearray(self.boot);value=b'androidboot.test=1' if failure=='wrong-boot' else b'e5.part_v=2 e5.part_prepare=request'
                raw[44:1580]=value.ljust(1536,b'\0');self.boot=bytes(raw)
            with self.subTest(failure=failure),self.assertRaises(RecoveryRequired):self.plan(reset=lambda _:True)
            self.assertFalse(self.store.list())

    def test_encrypted_data_asks_before_reset_but_consent_cannot_override_bad_root_evidence(self):
        self.fixture.device.update(encrypted=True,encryption_state='encrypted',encryption_type='file')
        with self.assertRaises(PreservationUnavailable):self.plan()
        self.assertFalse(self.store.list())
        ctx=self.plan(reset=lambda _:True)
        self.assertEqual(ctx.record['plan']['data_action'],'reset');self.assertTrue(ctx.record['plan']['wipe'])
        self.fs=image({'etc/openwrt_release':b"DISTRIB_ID='OpenWrt'\n"})
        with self.assertRaises(RecoveryRequired):self.plan(reset=lambda _:True)

    def test_mu300_preparation_tf_and_legacy_boot_routes_cannot_authorize_partition_removal(self):
        for mode in (b'prepare\n',b'sd\n',b'legacy\n',b'data-image\n'):
            self.configure('mu300');self.boot=linux_boot('mu300',mode)
            with self.subTest(mode=mode),self.assertRaises(RecoveryRequired):self.plan(reset=lambda _:True)
            self.assertFalse(self.store.list())

    def test_forged_target_cannot_swallow_unallocated_space_or_an_unrelated_partition(self):
        for foreign in (False,True):
            self.configure();ctx=self.plan();folder=ctx.directory/'uninstall'
            table=bytearray((folder/'after-first').read_bytes()[1024:])
            if foreign:table[5*128+16]^=1
            else:
                end=struct.unpack_from('<Q',table,3*128+40)[0]
                struct.pack_into('<Q',table,3*128+40,end+2*fx.GIB//512)
            for name,raw in zip(('after-first','after-last'),fx.copies(table)):
                (folder/name).write_bytes(raw);ctx.record['uninstall']['checksums'][name]=hashlib.sha256(raw).hexdigest()
            with self.assertRaises(RecoveryRequired):Uninstaller(None,ctx,self.fixture.adapter)
        self.configure();ctx=self.plan()
        ctx.record['device']['storage']['NODE']='mmcblk0;invalid;p4'
        with self.assertRaisesRegex(RecoveryRequired,'disk node'):Uninstaller(None,ctx,self.fixture.adapter)

    def test_changed_installation_stops_before_backup_or_boot_writes(self):
        ctx=self.plan();self.fixture.adapter.backup=Mock()
        self.fs=image({**marker_files('e5'),'etc/e5/part-root':b'e5root /dev/mmcblk1\n'})
        with self.assertRaises(RecoveryRequired):Uninstaller(None,ctx,self.fixture.adapter).arm(self.fixture.device)
        self.fixture.adapter.backup.assert_not_called();self.assertFalse(ctx.record['effects'])

    def test_restart_after_armed_effect_never_requires_original_record_or_repeats_writes(self):
        ctx=self.plan();r=ctx.record;r['effects']['uninstall-arm']={'state':'done'};self.store.save(r)
        adapter=self.fixture.adapter
        adapter.probe=Mock(return_value={**self.fixture.device,'boot_id':'after','existing':False,'userdata_bytes':32*fx.GIB})
        resumed=Context(self.store,self.store.load(r['id']),lambda *_:None)
        worker=Uninstaller(SimpleNamespace(stop=Mock()),resumed,adapter)
        with patch.object(worker,'layout_state',return_value='after'),patch.object(worker,'finish') as finish,patch.object(worker,'arm',side_effect=AssertionError('replayed')),patch('backend.uninstall.inspect_installation',side_effect=AssertionError('root no longer exists')):
            worker.run()
        finish.assert_called_once()

    def test_interrupted_boot_retry_uses_verified_original_boot_backup_for_evidence(self):
        proof=inspect_installation(self.fixture.adapter,self.fixture.device,self.root)
        saved=self.boot;self.boot=bytes(len(saved))
        self.assertEqual(inspect_installation(self.fixture.adapter,self.fixture.device,self.root,boot_backup=saved),proof)

    def test_wrong_type_attributes_duplicate_guid_and_alignment_are_rejected_before_reads(self):
        for case in ('type','attributes','guid','alignment'):
            table=bytearray(self.before[0][1024:]);at=4*128
            if case=='type':table[at:at+16]=b'X'*16
            if case=='attributes':struct.pack_into('<Q',table,at+48,1)
            if case=='guid':table[at+16:at+32]=table[3*128+16:3*128+32]
            if case=='alignment':
                start,end=struct.unpack_from('<QQ',table,at+32);struct.pack_into('<QQ',table,at+32,start+1,end)
            with self.subTest(case=case),self.assertRaises(RecoveryRequired):device_layout(fx.copies(table),fx.SECTORS,'e5root')

    def test_schema3_arm_carries_the_preserving_action_and_creates_backups_before_any_boot_write(self):
        f=self.fixture;ctx=self.plan()
        # Use the real worker image builder on a tiny synthetic boot, no release
        # build or device commands. Only the transport and tools are fixtures.
        base=fx.boot('e5');package=Path(f.temp.name)/'package';(package/'files').mkdir(parents=True)
        (package/'files/boot.img').write_bytes(base);f.adapter.root=package
        def backup(_):
            folder=ctx.directory/'backup';folder.mkdir()
            android=bytearray(base);android[44:1580]=bytes(1536)
            (folder/'boot_a.bin').write_bytes(android);(folder/'boot_b.bin').write_bytes(self.boot)
            (folder/'misc.bin').write_bytes(bytes(2048)+fx.android_bc()+bytes(2016))
        f.adapter.backup=Mock(side_effect=backup)
        original_read=f.adb.read.side_effect
        f.adb.read=Mock(side_effect=lambda command:self.before[0] if 'count=34 ' in command else self.before[1] if 'skip='+str(fx.SECTORS-33) in command else original_read(command))
        original_shell=f.adb.shell.side_effect;critical=[]
        def shell(command,**kwargs):
            if command.startswith('set -eu;'):
                self.assertTrue((ctx.directory/'backup/boot_a.bin').is_file())
                self.assertTrue((ctx.directory/'uninstall/after-first').is_file());critical.append(command)
                self.assertIsNone(kwargs['timeout']);return ''
            if command.startswith('umask '):return ''
            return original_shell(command,**kwargs)
        f.adb.shell=Mock(side_effect=shell);f.adb.call=Mock()
        with patch('backend.uninstall.growth_tools',return_value={'openwrt-uninstall/tools/fixture':b'tool'}),patch('backend.uninstall.push_chunks'):
            Uninstaller(None,ctx,f.adapter).arm(f.device)
        self.assertEqual(len(critical),1);self.assertEqual(ctx.record['effects']['uninstall-arm']['state'],'done')
        generated=(ctx.directory/'uninstall/boot-uninstall.img').read_bytes();size=struct.unpack_from('<I',generated,12)[0]
        files={name:body for archive in archives(generated[8192:8192+size]) for name,body,_ in entries(archive)}
        self.assertIn(b':grow-ext4:',files['openwrt-uninstall/request'])
        self.assertNotIn(b'--wipe_data',files['openwrt-uninstall/recovery-bcb'])
        self.assertNotIn('of=/dev/block/mmcblk',critical[0]);f.adb.call.assert_called_once_with('reboot')


class MetadataReader(unittest.TestCase):
    def test_block_sizes_extents_and_old_direct_pointers(self):
        for blocksize in (1024,2048,4096):
            for extents in (False,True):
                files=marker_files('mu300');raw=image(files,blocksize,extents)
                reader=Ext4Evidence(lambda at,n:raw[at:at+n],len(raw))
                for name,value in files.items():self.assertEqual(reader.file(name),value)

    def test_out_of_range_extents_and_truncated_reads_stop(self):
        raw=bytearray(image(marker_files('e5')))
        struct.pack_into('<I',raw,8*4096+(2-1)*256+60,99999999)
        reader=Ext4Evidence(lambda at,n:bytes(raw[at:at+n]),len(raw))
        with self.assertRaises(ValueError):reader.file('etc/e5/part-root')
        with self.assertRaises(ValueError):Ext4Evidence(lambda at,n:b'bad',len(raw))

    @unittest.skipUnless(sys.platform!='win32' and shutil.which('sh'),'requires a POSIX shell; regular files only')
    def test_idle_check_rejects_mounts_holders_and_unreadable_metadata(self):
        f=NoOriginalRecord();f.setUp()
        try:
            inspect_installation(f.fixture.adapter,f.fixture.device,f.root)
            command=next(call.args[0] for call in f.fixture.adb.shell.call_args_list if 'echo IDLE' in call.args[0])
        finally:f.tearDown()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);node=root/'sys/mmcblk0p5';(node/'holders').mkdir(parents=True)
            (node/'dev').write_text('179:5\n');mounts=root/'mountinfo';mounts.write_text('')
            command=command.replace('/sys/class/block',str(root/'sys')).replace('/proc/self/mountinfo',str(mounts))
            def run():return subprocess.run(['sh','-c',command],capture_output=True,text=True)
            self.assertEqual(run().stdout.strip(),'IDLE')
            mounts.write_text('1 2 179:5 / /mounted rw - ext4 /dev/mmcblk0p5 rw\n')
            self.assertEqual(run().stdout.strip(),'BUSY')
            mounts.write_text('');(node/'holders/dm-0').write_text('')
            self.assertEqual(run().stdout.strip(),'BUSY')
            mounts.unlink();self.assertNotEqual(run().returncode,0)


@unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'),'requires Linux filesystem tools, regular files only')
class RealMetadata(unittest.TestCase):
    def test_populated_ext4_with_real_directory_indexes_64bit_and_metadata_checksums(self):
        for blocksize in (1024,4096):
            with self.subTest(blocksize=blocksize),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);tree=root/'tree';tree.mkdir()
                files=marker_files('mu300')
                for name,content in files.items():
                    p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
                # More than one directory block, and a real symlink rejection.
                for n in range(400):(tree/'openwrt/etc'/f'other-{n:04d}').write_text('other')
                (tree/'alias').symlink_to('openwrt/etc/openwrt_release')
                path=root/'root.ext4'
                subprocess.run(['mke2fs','-q','-t','ext4','-F','-b',str(blocksize),'-d',str(tree),str(path),'64M'],check=True,capture_output=True)
                result=subprocess.run(['e2fsck','-fyD',str(path)],capture_output=True)
                self.assertIn(result.returncode,(0,1),result.stderr)
                before=hashlib.sha256(path.read_bytes()).hexdigest()
                with path.open('rb') as stream:
                    def read(at,n):stream.seek(at);return stream.read(n)
                    reader=Ext4Evidence(read,path.stat().st_size)
                    for name,value in files.items():self.assertEqual(reader.file(name),value)
                    with self.assertRaises(ValueError):reader.file('alias')
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
