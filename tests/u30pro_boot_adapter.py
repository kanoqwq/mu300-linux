"""Exercise native three-image commit/rollback against regular files, not devices."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


fw=load('u30pro_firmware',TOP/'devices/u30pro/native/tools/firmware.py')
adapter=load('u30pro_adapter',TOP/'desktop/native_u30pro.py')
repair=load('u30pro_repair',TOP/'tools/u30pro-boot-repair.py')
cpio=load('u30pro_repair_cpio',TOP/'boot/build-boot-image.py')


def framed(body, size=1<<20):
    body+=bytes(-len(body)%4096)
    metadata=b'AVB0'+bytes(252)
    footer=struct.pack('>4sIIQQQ28s',b'AVBf',1,0,len(body),len(body),len(metadata),bytes(28))
    return body+metadata+bytes(size-len(body)-len(metadata)-64)+footer


def boot(kernel=b'ARM-KERNEL',ram=b'RAM',linux=False):
    header=bytearray(4096);header[:8]=b'ANDROID!'
    struct.pack_into('<II',header,8,len(kernel),len(ram))
    struct.pack_into('<I',header,20,1584);struct.pack_into('<I',header,40,4)
    if linux:header[44:55]=b'loglevel=5\0'
    return framed(bytes(header)+kernel+bytes(-len(kernel)%4096)+ram)


def vendor():
    header=bytearray(4096);header[:8]=b'VNDRBOOT'
    struct.pack_into('<II',header,8,4,4096);struct.pack_into('<I',header,24,3)
    struct.pack_into('<I',header,2100,3);struct.pack_into('<4I',header,2112,108,1,108,4)
    table=struct.pack('<III',3,0,1)+bytes(96)
    return framed(bytes(header)+b'RAM'+bytes(4093)+b'DTB'+bytes(4093)+table+bytes(3988)+b'CONF')


class AndroidFiles:
    def __init__(self, directory, slot='a', fail=False):
        self.root=directory;self.slot=slot;self.linux='b' if slot=='a' else 'a';self.serial='synthetic-only'
        self.p='/data/local/tmp/mu300-desktop-012345abcdef'
        self.bb=self.p+'/busybox'
        self.native_backup=directory/'backup';self.native_backup.mkdir()
        self.parts=directory/'parts';self.parts.mkdir()
        self.payload=directory/'payload';self.payload.mkdir()
        self.tx=directory/'tx';self.tx.mkdir()
        self.commands=0
        self.original={}
        templates={'boot':boot(),'vendor_boot':vendor(),'init_boot':boot(b'',b'ANDROID-INIT'),'dtbo':b'DTBO'+bytes(4092)}
        for name,data in templates.items():
            for side in ('a','b'):
                # Source and target differ, including DTBO, to expose rollback failures.
                raw=data if side==slot else data[:-1]+bytes([data[-1]^1])
                (self.parts/(name+'_'+side)).write_bytes(raw)
        misc=bytes(2048)+b'ORIGINAL-MISC-RECORD'.ljust(32,b'\0')+bytes(512)
        (self.parts/'misc').write_bytes(misc)
        self.original={p.name:p.read_bytes() for p in self.parts.iterdir()}
        (self.payload/'android-bootchain.sh').write_text('#!/bin/sh\nexit 0\n')
        commands=directory/'commands';commands.mkdir()
        (commands/'getprop').write_text('#!/bin/sh\necho _'+slot+'\n')
        dd=shutil.which('dd')
        (commands/'dd').write_text('#!/bin/sh\n'+(
            'case "$*" in *of='+str(self.parts/('vendor_boot_'+self.linux))+'*)\n'
            'if [ ! -e "'+str(directory/'failed-once')+'" ]; then\n'
            'touch "'+str(directory/'failed-once')+'"\nprintf broken > "'+str(self.parts/('vendor_boot_'+self.linux))+'"\nexit 1\nfi;; esac\n' if fail else '')+
            'exec '+dd+' "$@"\n')
        for p in commands.iterdir():p.chmod(0o755)
        self.commands_dir=commands

    def shell(self,command):
        if command.startswith('for f in /proc/'): return ''
        if command.startswith('chmod '): return ''
        part=command.split('/dev/block/by-name/')[1].split()[0]
        path=self.parts/part
        return str(path.stat().st_size) if command.startswith('blockdev') else hashlib.sha256(path.read_bytes()).hexdigest()+'  '+part

    def download(self,command,path,expected_size):
        part=command.split('/dev/block/by-name/')[1].split()[0]
        data=(self.parts/part).read_bytes()
        assert len(data)==expected_size
        path.write_bytes(data)

    def push(self,path,name):shutil.copyfile(path,self.payload/name)
    def put(self,data,name):(self.payload/name).write_bytes(data.encode() if isinstance(data,str) else data)
    def run(self,command):
        self.commands+=1
        command=command.replace('/dev/block/by-name/',str(self.parts)+'/')
        command=command.replace('/data/local/tmp/openwrt-boot-write.lock',str(self.root/'boot-write.lock'))
        # blockdev/stat size checks target regular files in this fixture only.
        commands=self.commands_dir
        (commands/'blockdev').write_text('#!/bin/sh\nstat -c %s "$2"\n');(commands/'blockdev').chmod(0o755)
        result=subprocess.run(['sh','-c',command],env=dict(os.environ,P=str(self.payload),T=str(self.tx),
            BB=shutil.which('busybox') or '/bin/busybox',PATH=str(commands)+':'+os.environ['PATH']),capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)


class NativeBoot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'desktop').mkdir()
        shutil.copyfile(TOP/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/boot.py',self.root/'desktop/boot-runtime.py')
    def tearDown(self):self.temp.cleanup()

    def test_preserves_actual_device_dtb_and_bootconfig(self):
        templates={'boot':boot(),'vendor_boot':vendor(),'init_boot':boot(b'',b'ANDROID')}
        result=adapter.images(self.root,boot(b'NEWKERNEL',b'NEWRAM',True),templates)
        self.assertEqual(fw.boot(result['boot']),(b'NEWKERNEL',b'NEWRAM'))
        self.assertEqual(fw.vendor_boot(result['vendor_boot']),(b'NEWRAM',b'DTB',b'CONF'))
        self.assertEqual(fw.cpio_read(fw.unlz4(fw.boot(result['init_boot'])[1])),{})
        self.assertTrue(all(len(data)==1<<20 for data in result.values()))

    @unittest.skipUnless(os.name=='posix' and shutil.which('busybox'),'POSIX regular-file flash fixture')
    def test_both_slot_directions_commit_misc_last(self):
        for slot in ('a','b'):
            with self.subTest(slot=slot):
                directory=self.root/slot;directory.mkdir();android=AndroidFiles(directory,slot)
                adapter.flash(android,boot(b'NEW',b'RAM',True),b'NEXT'.ljust(32,b'\0'),self.root,None)
                self.assertEqual((android.parts/'misc').read_bytes()[2048:2080],b'NEXT'.ljust(32,b'\0'))
                for name in ('boot','vendor_boot','init_boot','dtbo'):
                    self.assertEqual((android.parts/(name+'_'+slot)).read_bytes(),android.original[name+'_'+slot])
                sums=json.loads((android.native_backup/'checksums.json').read_text())
                self.assertIn('vendor_boot_'+slot+'.bin',sums)

    @unittest.skipUnless(os.name=='posix' and shutil.which('busybox'),'POSIX regular-file flash fixture')
    def test_partial_vendor_write_restores_all_and_retry_succeeds(self):
        android=AndroidFiles(self.root,fail=True)
        with self.assertRaises(RuntimeError):adapter.flash(android,boot(b'NEW',b'RAM',True),bytes(32),self.root,None)
        for name,data in android.original.items():self.assertEqual((android.parts/name).read_bytes(),data,name)
        adapter.flash(android,boot(b'NEW',b'RAM',True),b'OK'.ljust(32,b'\0'),self.root,None)
        self.assertEqual((android.parts/'misc').read_bytes()[2048:2080],b'OK'.ljust(32,b'\0'))

    @unittest.skipUnless(os.name=='posix' and shutil.which('busybox'),'POSIX ordinary-file write fixture')
    def test_uninstall_bcb_failure_restores_every_container_and_both_misc_regions(self):
        android=AndroidFiles(self.root)
        with self.assertRaises(RuntimeError):
            adapter.flash(android,boot(b'NEW',b'RAM',True),b'NEXT'.ljust(32,b'\0'),self.root,None,
                before_misc='printf changed | dd of=/dev/block/by-name/misc bs=1 conv=notrunc\nexit 8\n',preserve_bcb=True)
        for name,data in android.original.items():self.assertEqual((android.parts/name).read_bytes(),data,name)

    @unittest.skipUnless(os.name=='posix','POSIX native write receipt')
    def test_native_receipt_recovers_atomic_publication_gap_but_rejects_changed_plan(self):
        import sys
        from types import SimpleNamespace
        sys.path.insert(0,str(TOP/'installer'))
        from backend.native_boot import native_receipt
        android=AndroidFiles(self.root)
        canonical=self.root/'canonical.img';canonical.write_bytes(boot(b'NEW',b'RAM',True))
        adapter.flash(android,canonical.read_bytes(),b'NEXT'.ljust(32,b'\0'),self.root,None)
        shared=SimpleNamespace(ctx=SimpleNamespace(directory=self.root))
        device={'serial':android.serial,'slot':'_a'}
        path=android.native_backup/'checksums.json';checks=json.loads(path.read_text());checks['native-before.json']='0'*64;path.write_text(json.dumps(checks))
        self.assertTrue(native_receipt(shared,device,'.',canonical)['committed'])
        path=android.native_backup/'native-before.json';record=json.loads(path.read_text());record['planned']['boot']='f'*64;path.write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError,'plan differs'):native_receipt(shared,device,'.',canonical)

    @unittest.skipUnless(os.name=='posix' and shutil.which('busybox'),'POSIX ordinary-file write fixture')
    def test_uninstall_restores_android_companions_in_both_directions_and_retries_failed_write(self):
        import sys
        from types import SimpleNamespace
        sys.path.insert(0,str(TOP/'installer'))
        from backend.native_boot import restore_android_companions
        for slot in ('a','b'):
            directory=self.root/('restore-'+slot);directory.mkdir()
            android=AndroidFiles(directory,slot,fail=True)
            record={'effects':{}}
            context=SimpleNamespace(directory=directory/'context',record=record,store=SimpleNamespace(save=lambda r:None),
                                    effect=lambda key,fn,**kw:fn())
            shared=SimpleNamespace(ctx=context,root=self.root,legacy=SimpleNamespace(Android=lambda args:android,
                payload_file=lambda root,name:Path(shutil.which('busybox'))))
            device={'slot':'_'+slot,'serial':android.serial}
            with self.assertRaises(RuntimeError):restore_android_companions(shared,device)
            for name,data in android.original.items():self.assertEqual((android.parts/name).read_bytes(),data,name)
            restore_android_companions(shared,device)
            for name in ('vendor_boot','init_boot','dtbo'):
                self.assertEqual((android.parts/(name+'_'+android.linux)).read_bytes(),android.original[name+'_'+slot])
            self.assertEqual((android.parts/'misc').read_bytes(),android.original['misc'])
            self.assertEqual((android.parts/('boot_'+android.linux)).read_bytes(),android.original['boot_'+android.linux])


    @unittest.skipUnless(os.name=='posix','POSIX native SSH update functions')
    def test_ssh_companion_write_and_restore_run_actual_shell_functions(self):
        source=(TOP/'desktop/local-update.sh').read_text()
        row=source[source.index('native_row() {'):source.index('\nif [ "$native" = 1 ]; then',source.index('native_row() {'))]
        functions=source[source.index('native_write() {'):source.index('\ndd if="$dev"',source.index('native_write() {'))]
        row=row.replace('[ -b "$nv_dev" ]','[ -f "$nv_dev" ]')
        for slot in ('a','b'):
            for fault in (False,True):
                with tempfile.TemporaryDirectory() as temp:
                    folder=Path(temp);payload=folder/'payload';payload.mkdir();backup=folder/'backup';backup.mkdir()
                    parts=folder/'parts';parts.mkdir();commands=folder/'commands';commands.mkdir()
                    lines=['U30PRO-BOOT-V1 '+slot];before={};after={}
                    for name in ('vendor_boot','init_boot'):
                        before[name]=(b'OLD-'+name.encode()).ljust(8192,b'x');after[name]=(b'NEW-'+name.encode()).ljust(8192,b'y')
                        (parts/(name+'_'+slot)).write_bytes(before[name]);(backup/(name+'.img')).write_bytes(before[name])
                        (payload/('native-'+name+'.img')).write_bytes(after[name])
                        lines.append(name+' 8192 '+hashlib.sha256(before[name]).hexdigest()+' '+hashlib.sha256(after[name]).hexdigest())
                    (payload/'native-boot.tsv').write_text('\n'.join(lines)+'\n')
                    (commands/'blockdev').write_text('#!/bin/sh\nstat -c %s "$2"\n')
                    (commands/'dd').write_text('#!/bin/sh\n'+('case "$*" in *of='+str(parts/('vendor_boot_'+slot))+'*) if [ ! -e '+str(folder/'fault')+' ]; then touch '+str(folder/'fault')+'; printf partial | /bin/dd of='+str(parts/('vendor_boot_'+slot))+' conv=notrunc 2>/dev/null; exit 1; fi;; esac\n' if fault else '')+'exec /bin/dd "$@"\n')
                    for file in commands.iterdir():file.chmod(0o755)
                    script='set -eu\npayload='+str(payload)+'\nbackup='+str(backup)+'\nnative=1\nlinux='+slot+'\npart_dev() { echo '+str(parts)+'/$1; }\n'+row+'\n'+functions+'\ncommitted=0\nfinish() { rc=$?; trap - EXIT; [ "$committed" = 1 ] || native_restore; exit "$rc"; }\ntrap finish EXIT\nnative_write\ncommitted=1\n'
                    result=subprocess.run(['sh','-c',script],env=dict(os.environ,PATH=str(commands)+':'+os.environ['PATH']),capture_output=True,text=True)
                    self.assertEqual(result.returncode!=0,fault,result.stderr)
                    for name in before:
                        self.assertEqual((parts/(name+'_'+slot)).read_bytes(),before[name] if fault else after[name])

    def test_image_overflow_rejected_without_device_write(self):
        templates={'boot':boot(),'vendor_boot':vendor(),'init_boot':boot(b'',b'ANDROID')}
        with self.assertRaises(ValueError):adapter.images(self.root,boot(b'NEW',b'RAM',True),dict(templates,vendor_boot=vendor()[:4096]))

    def test_preflight_backs_up_without_upload_or_write(self):
        android=AndroidFiles(self.root)
        result=adapter.flash(android,boot(b'NEW',b'RAM',True),b'',self.root,None,dry_run=True)
        self.assertTrue(result['verified']);self.assertFalse(result['device_writes'])
        self.assertEqual(android.commands,0)
        self.assertFalse((android.payload/'native-boot.img').exists())
        for name,data in android.original.items():self.assertEqual((android.parts/name).read_bytes(),data)

    def test_repair_real_layered_ramdisk_preserves_kernel_dtb_and_android(self):
        for slot in ('a', 'b'):
            with self.subTest(slot=slot):
                tx=self.root/('tx-'+slot);(tx/'backup').mkdir(parents=True);(tx/'native-boot').mkdir()
                (self.root/'desktop/manifest.json').write_text(json.dumps({'platform':'u30pro'}))
                templates={'boot':boot(),'vendor_boot':vendor(),'init_boot':boot(b'',b'ANDROID')}
                ram=fw.lz4_legacy(fw.cpio_write({'init':(stat.S_IFREG|0o755,b'old init')}))
                ram+=fw.lz4_legacy(fw.cpio_write({'etc/mu300-android-slot':(stat.S_IFREG|0o644,(slot+'\n').encode())}))
                installed=adapter.images(self.root,boot(b'UNCHANGED-KERNEL',ram,True),templates)
                installed['dtbo']=b'UNCHANGED-DTBO'
                record={'android_slot':slot,'files':{},'planned':{}}
                for name,data in templates.items():
                    part=name+'_'+slot;(tx/'backup'/(part+'.bin')).write_bytes(data)
                    record['files'][part]=hashlib.sha256(data).hexdigest()
                for name,data in installed.items():
                    (tx/'native-boot'/(name+'.img')).write_bytes(data)
                    record['planned'][name]=hashlib.sha256(data).hexdigest()
                (tx/'backup/native-before.json').write_text(json.dumps(record))
                (tx/'transaction.json').write_text(json.dumps({'device':{'slot':'_'+slot},'package':{'path':str(self.root)}}))
                before={p:p.read_bytes() for p in tx.rglob('*.bin')}
                target=tx/'repair';repair.build(tx,target)
                result=fw.boot((target/'boot.img').read_bytes())
                self.assertEqual(result[0],b'UNCHANGED-KERNEL')
                entries=fw.cpio_read(fw.unlz4(result[1]))
                self.assertIn('etc/u30pro-boot-wait.sh',entries)
                self.assertEqual(entries['init'][1],(TOP/'devices/u30pro/init').read_bytes().replace(b'\r\n',b'\n'))
                self.assertEqual(fw.vendor_boot((target/'vendor_boot.img').read_bytes())[1:],(b'DTB',b'CONF'))
                self.assertFalse((target/'init_boot.img').exists())
                for path,data in before.items():self.assertEqual(path.read_bytes(),data)
                # A corrupt recovery input must stop before creating outputs.
                (tx/'backup'/('boot_'+slot+'.bin')).write_bytes(b'broken')
                with self.assertRaisesRegex(ValueError,'Invalid Android backup'):
                    repair.build(tx,tx/'bad-repair')
                self.assertFalse((tx/'bad-repair').exists())


if __name__=='__main__':unittest.main()
