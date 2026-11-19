"""Desktop boot and archive regressions; these tests never connect to a device."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import shlex
import subprocess
import sys
import tarfile
import tempfile
import copy
import unittest
from unittest.mock import patch, Mock
import zlib

TOP=Path(__file__).resolve().parents[1]

def load(name):
    spec=importlib.util.spec_from_file_location(name,TOP/'desktop'/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

installer=load('installer');boot=load('boot_image');package=load('package')
repair=load('prepare-tools')

def live(slot='a'):
    x=bytearray(32);x[:4]=('_'+slot).encode()+b'\0\0';x[4:8]=b'BCAB';x[8]=1;x[9]=2
    x[12],x[14]=(0x9f,0x1e) if slot=='a' else (0x1e,0x9f)
    x[16:28]=b'RESERVE-DATA';x[28:]=struct.pack('<I',zlib.crc32(x[:28]));return bytes(x)

def frame(data):
    import lz4.block
    return bytes.fromhex('02214c18')+struct.pack('<I',len(data:=lz4.block.compress(data,store_size=False)))+data

def unpack_frames(data):
    import lz4.block
    pos=0;streams=[]
    while pos<len(data):
        if data[pos:pos+4]!=bytes.fromhex('02214c18'):raise ValueError('bad LZ4 frame')
        pos+=4;stream=b''
        while pos<len(data) and data[pos:pos+4]!=bytes.fromhex('02214c18'):
            size=struct.unpack_from('<I',data,pos)[0];pos+=4
            if not size:break
            stream+=lz4.block.decompress(data[pos:pos+size],uncompressed_size=8<<20);pos+=size
        streams.append(stream)
    return streams

def cpio_files(stream):
    pos=0;found={}
    while pos+110<=len(stream):
        if stream[pos:pos+6]!=b'070701':raise ValueError('bad CPIO record')
        size=int(stream[pos+54:pos+62],16);ns=int(stream[pos+94:pos+102],16)
        name=stream[pos+110:pos+110+ns-1].decode();pos=(pos+110+ns+3)&~3
        data=stream[pos:pos+size];pos=(pos+size+3)&~3
        if name=='TRAILER!!!':break
        found[name]=data
    return found

class Desktop(unittest.TestCase):
    def test_external_entries_resolve_dependencies_under_a_frozen_host(self):
        with tempfile.TemporaryDirectory(prefix='installer package ') as temp:
            root=Path(temp)/'desktop';root.mkdir();support=root/'desktop';support.mkdir()
            (root/'tools/backend').mkdir(parents=True)
            for name in ('gpt.py','storage.py'):
                (support/name).write_bytes((TOP/'installer/backend'/name).read_bytes())
            (support/'storage-lib.sh').write_bytes((TOP/'rootfs/overlay/opt/mu300/lib/storage.sh').read_bytes())
            for entry in (root/'OpenWrt-Installer.py',support/'installer.py'):
                entry.write_bytes((TOP/'desktop/installer.py').read_bytes())
                script='''import importlib.util,sys
from pathlib import Path
sys.frozen=True
sys.executable=sys.argv[2]
spec=importlib.util.spec_from_file_location('external_entry',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert Path(m._gpt.__file__).resolve()==Path(sys.argv[3]).resolve()
assert m._storage.part_node('sda',75)=='sda75'
if Path(sys.argv[1]).name=='OpenWrt-Installer.py':
    assert m.package_root()==Path(sys.argv[1]).parent
print('external entry loaded')
'''
                result=subprocess.run([sys.executable,'-c',script,str(entry),
                    str(root/'tools/backend/openwrt-backend.exe'),str(support/'gpt.py')],
                    cwd=temp,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('external entry loaded',result.stdout)

    @unittest.skipUnless(importlib.util.find_spec('paramiko'), 'SSH tests need Paramiko')
    def test_update_ignores_old_host_key_cache_and_never_saves_keys(self):
        import paramiko
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);cache=home/'.mu300-desktop-known-hosts'
            cache.write_text('stale key for another device at the same address\n')
            c=Mock()
            with patch.object(paramiko,'SSHClient',return_value=c), \
                 patch.object(installer.Path,'home',return_value=home), \
                 patch.dict(installer.os.environ,{'MU300_SSH_PASSWORD':'test-only'}), \
                 patch.object(installer,'ssh_exec',return_value='unsupported-runtime'), \
                 patch.object(installer,'confirm',side_effect=AssertionError('unexpected key prompt')):
                with self.assertRaisesRegex(ValueError,'supported MU300 runtime'):
                    installer.update(Mock(host='192.168.77.1'),home)
            self.assertIsInstance(c.set_missing_host_key_policy.call_args.args[0],paramiko.AutoAddPolicy)
            c.load_host_keys.assert_not_called();c.load_system_host_keys.assert_not_called()
            c.save_host_keys.assert_not_called();c.close.assert_called_once()
            self.assertEqual(cache.read_text(),'stale key for another device at the same address\n')

    def test_ssh_upload_streams_binary_without_sftp_and_closes_channel(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'payload.bin';data=b'\x00\xff\n\r'*140000;path.write_bytes(data)
            c=Mock();channel=c.get_transport.return_value.open_session.return_value
            channel.makefile_stderr.return_value=io.BytesIO(b'')
            channel.recv_exit_status.return_value=0
            with patch('sys.stdout',io.StringIO()):installer.ssh_upload(c,path,"/tmp/update dir/file'1")
            self.assertEqual(b''.join(x.args[0] for x in channel.sendall.call_args_list),data)
            self.assertGreater(channel.sendall.call_count,1)
            channel.shutdown_write.assert_called_once()
            channel.close.assert_called_once()
            c.open_sftp.assert_not_called()
            self.assertEqual(shlex.split(channel.exec_command.call_args.args[0].split('> ',1)[1]),["/tmp/update dir/file'1"])

    def test_ssh_upload_remote_failure_or_disconnect_never_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'payload.bin';path.write_bytes(b'payload')
            for disconnected in (False,True):
                with self.subTest(disconnected=disconnected):
                    c=Mock();channel=c.get_transport.return_value.open_session.return_value
                    channel.makefile_stderr.return_value=io.BytesIO(b'No space left on device')
                    channel.recv_exit_status.return_value=1
                    if disconnected:channel.sendall.side_effect=OSError('connection lost')
                    with patch('sys.stdout',io.StringIO()),self.assertRaises((RuntimeError,OSError)):
                        installer.ssh_upload(c,path,'/tmp/test')
                    channel.close.assert_called_once()

    def test_ssh_boot_control_is_binary_and_diagnostics_stay_separate(self):
        c=Mock();out=Mock();out.read.return_value=live('b')
        out.channel.recv_exit_status.return_value=0
        c.exec_command.return_value=(None,out,io.BytesIO(b'32+0 records in\n32+0 records out\n'))
        command='dd if=/dev/mmcblk0p3 bs=1 skip=2048 count=32'
        raw=installer.ssh_read_exact(c,command,32)
        self.assertEqual(raw,live('b'))
        c.exec_command.assert_called_once_with(command,timeout=30)
        self.assertEqual(len(boot.boot_control(raw,'a')),32)

    def test_ssh_boot_control_short_reads_and_remote_errors_stop_update(self):
        for rc,data,stderr,exception in [(0,b'',b'',ValueError),
                (0,live()[:-1],b'',ValueError),
                (1,live(),b'dd: read error',RuntimeError)]:
            with self.subTest(rc=rc,size=len(data)):
                c=Mock();out=Mock();out.read.return_value=data
                out.channel.recv_exit_status.return_value=rc
                c.exec_command.return_value=(None,out,io.BytesIO(stderr))
                with self.assertRaises(exception):installer.ssh_read_exact(c,'dd',32)

    def test_interactive_exit_shows_success_next_step_and_waits_once(self):
        output=io.StringIO();stdin=Mock();stdin.isatty.return_value=True
        with patch.object(installer,'main',return_value=installer.Outcome('success','Installed','Reboot manually')), \
             patch.object(installer.sys,'stdin',stdin), patch.object(installer.sys,'stdout',output), \
             patch('builtins.input',return_value='') as pause:
            self.assertEqual(installer.run_cli([]),0)
            pause.assert_called_once()
        self.assertIn('[SUCCESS / 成功] Installed',output.getvalue())
        self.assertIn('Reboot manually',output.getvalue())

    def test_pending_stage_is_not_reported_as_completed_installation(self):
        output=io.StringIO();stdin=Mock();stdin.isatty.return_value=True
        with patch.object(installer,'main',return_value=installer.Outcome('pending','Preparing Android','Choose 5')), \
             patch.object(installer.sys,'stdin',stdin), patch.object(installer.sys,'stdout',output), \
             patch('builtins.input',side_effect=AssertionError('automation paused')):
            self.assertEqual(installer.run_cli(['--no-pause']),0)
        self.assertIn('[NEXT STEP REQUIRED / 仍需下一步]',output.getvalue())
        self.assertIn('Choose 5',output.getvalue())
        self.assertNotIn('[SUCCESS',output.getvalue())

    def test_failure_stays_visible_and_preserves_failure_status(self):
        output=io.StringIO();stdin=Mock();stdin.isatty.return_value=True
        with patch.object(installer,'main',side_effect=RuntimeError('readback mismatch')), \
             patch.object(installer.sys,'stdin',stdin), patch.object(installer.sys,'stdout',output), \
             patch('builtins.input',return_value='') as pause:
            self.assertEqual(installer.run_cli([]),1)
            pause.assert_called_once()
        self.assertIn('[FAILED / 失败] readback mismatch',output.getvalue())

    def test_cancel_and_redirected_input_never_claim_success_or_block(self):
        output=io.StringIO();stdin=Mock();stdin.isatty.return_value=False
        with patch.object(installer,'main',side_effect=installer.InstallerCancelled()), \
             patch.object(installer.sys,'stdin',stdin), patch.object(installer.sys,'stdout',output), \
             patch('builtins.input',side_effect=AssertionError('redirected input paused')):
            self.assertEqual(installer.run_cli([]),130)
        self.assertIn('[CANCELLED / 已取消]',output.getvalue())
        self.assertNotIn('[SUCCESS',output.getvalue())

    def payload_manifest(self,relocated=False):
        names=('boot-linux.img','mu300-openwrt.tar.gz','rootfs-critical.sha256','rootfs.requirements','busybox')
        layout={n:'payload/'+n for n in names} if relocated else {}
        return {'kernel':'7.2','magisk_layout':layout,
                'magisk':{layout.get(n,n):hashlib.sha256(n.encode()).hexdigest() for n in names},
                'desktop':{'desktop/files/boot/repair/usr/sbin/resize2fs':'a'*64,'OpenWrt-Installer.exe':'b'*64}}

    def test_resume_identity_allows_installer_repairs_and_relocation_but_not_payload_changes(self):
        old=self.payload_manifest();new=self.payload_manifest(relocated=True)
        new['desktop']['OpenWrt-Installer.exe']='c'*64
        new['desktop']['docs/DESKTOP-INSTALL.md']='d'*64
        new['desktop']['desktop/files/boot/ext4-geometry.sh']='e'*64
        self.assertEqual(installer.resume_payload_id(old),installer.resume_payload_id(new))
        for field in ('boot-linux.img','mu300-openwrt.tar.gz','rootfs.requirements','busybox'):
            changed=copy.deepcopy(new);changed['magisk']['payload/'+field]='e'*64
            self.assertNotEqual(installer.resume_payload_id(old),installer.resume_payload_id(changed))
        changed=copy.deepcopy(new);changed['desktop']['desktop/files/boot/repair/usr/sbin/resize2fs']='f'*64
        self.assertNotEqual(installer.resume_payload_id(old),installer.resume_payload_id(changed))

    def test_old_resume_record_requires_exact_old_metadata_then_compares_payload(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);old=base/'old';new=base/'new';backup=base/'backups'
            for path in (old/'desktop',new/'desktop',backup):path.mkdir(parents=True)
            before=self.payload_manifest();after=self.payload_manifest(relocated=True)
            oldmeta=old/'desktop/manifest.json';oldmeta.write_text(json.dumps(before))
            (new/'desktop/manifest.json').write_text(json.dumps(after))
            record=backup/'transaction.json';r={'package':installer.digest(oldmeta)}
            expected=installer.resume_payload_id(after)
            self.assertEqual(installer.validate_resume_package(new,after,r,record),expected)
            changed=copy.deepcopy(after);changed['magisk']['payload/boot-linux.img']='f'*64
            with self.assertRaisesRegex(ValueError,'differ'):installer.validate_resume_package(new,changed,r,record)
            oldmeta.unlink()
            with self.assertRaisesRegex(ValueError,'metadata is missing'):installer.validate_resume_package(new,after,r,record)
            r['payload_identity']=expected
            self.assertEqual(installer.validate_resume_package(new,after,r,record),expected)
            with self.assertRaisesRegex(ValueError,'differ'):installer.validate_resume_package(new,changed,r,record)
            installer.bind_resume_package(new,after,r,record)
            self.assertEqual((backup/'package-manifest.json').read_bytes(),(new/'desktop/manifest.json').read_bytes())

    def test_hotspot_password_uses_visible_input_and_preserves_spaces(self):
        with patch('builtins.input',side_effect=['V50_wifi','1234 5678','']), patch.object(installer.getpass,'getpass',side_effect=AssertionError('hidden Wi-Fi input')):
            text=installer.settings(None,2048,'part','7.2')
        self.assertIn("WIFI_KEY='1234 5678'",text)

    def test_reboot_uses_adb_service_and_does_not_hide_failures(self):
        android=object.__new__(installer.Android)
        with patch.object(android,'call',return_value=b'') as call, patch.object(android,'shell',side_effect=AssertionError('root shell reboot')):
            android.reboot()
            call.assert_called_once_with('reboot',timeout=30)
        with patch.object(android,'call',side_effect=RuntimeError('device offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):android.reboot()

    def test_empty_adb_error_has_action_and_status(self):
        android=object.__new__(installer.Android);android.adb='adb';android.serial='TEST'
        proc=Mock(returncode=1);proc.communicate.return_value=(b'',b'')
        with patch.object(installer.subprocess,'Popen',return_value=proc):
            with self.assertRaisesRegex(RuntimeError,'ADB reboot failed, exit 1'):android.call('reboot')

    def test_loop_selection_accepts_android_block_nodes_and_linux_nodes(self):
        import shutil
        bash=Path('C:/Program Files/Git/bin/bash.exe') if sys.platform=='win32' else shutil.which('bash')
        if not bash or not Path(bash).exists():self.skipTest('Bash is needed for loop selection fixtures')
        helper=(TOP/'desktop/android-loop.sh').read_text()
        for candidate,present,want in [('/dev/loop31','/dev/block/loop31','/dev/block/loop31'),
                                       ('/dev/block/loop31','/dev/block/loop31','/dev/block/loop31'),
                                       ('/dev/loop31','/dev/loop31','/dev/loop31')]:
            script=helper+f'''\nlosetup() {{ echo {candidate}; }}
desktop_loop_is_block() {{ [ "$1" = {present} ]; }}
desktop_free_loop
'''
            p=subprocess.run([str(bash),'-c',script],capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),want)
        script=helper+'''\nlosetup() { echo /dev/loop31oops; }
mknod() { echo UNEXPECTED-NODE-WRITE; exit 99; }
desktop_free_loop
'''
        p=subprocess.run([str(bash),'-c',script],capture_output=True,text=True,timeout=15)
        self.assertNotEqual(p.returncode,0);self.assertNotIn('UNEXPECTED',p.stdout)

    def test_v50_requires_the_exact_product_and_platform(self):
        self.assertEqual(installer.android_profile('V50\r\nMU3351\r\n','ums9620'), 'v50')
        for names, platform in [('V50\nMU3351','ums9230'), ('V50\nblade','ums9620'),
                                ('F50\nMU3351','ums9620'), ('Blade V50\nblade','ums9620')]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                installer.android_profile(names,platform)
        self.assertEqual(installer.android_profile('F50\nmu300','ums9620'), 'f50')
        self.assertEqual(installer.android_profile('U30 Air\nu30air','ums9620'), 'u30air')

    def test_android_binary_reads_survive_su_crlf_translation(self):
        android=object.__new__(installer.Android);android.su=True
        data=bytes(range(256))*4
        checksum=hashlib.sha256(data).hexdigest()
        def shell(script,**kwargs):
            if script.startswith('df '):return '1048576'
            if script.startswith('set -e;'):return f'{len(data)}\r\n{checksum}  data.bin\r\n{checksum}  -\r\n'
            return ''
        def pull(*args,**kwargs):
            self.assertEqual(args[0],'pull');Path(args[2]).write_bytes(data);return b'1 file pulled'
        with patch.object(android,'shell',side_effect=shell),patch.object(android,'call',side_effect=pull) as call:
            self.assertEqual(android.read('cat /sys/firmware/fdt'),data)
            self.assertEqual(call.call_args.args[0],'pull')
            self.assertTrue(call.call_args.args[1].startswith('/data/local/tmp/openwrt-read-'))
        def corrupt(*args,**kwargs):Path(args[2]).write_bytes(b'corrupt!');return b'1 file pulled'
        with patch.object(android,'shell',side_effect=shell),patch.object(android,'call',side_effect=corrupt):
            with self.assertRaises(ValueError):android.read('cat /sys/firmware/fdt')

    def test_large_gpt_backup_covers_all_entries_and_rejects_bad_crc(self):
        sectors=20971520;count=512;rows=count*128//512
        def header(at,other,table):
            raw=bytearray(512);raw[:8]=b'EFI PART';struct.pack_into('<I',raw,12,92)
            struct.pack_into('<QQ',raw,24,at,other);struct.pack_into('<QII',raw,72,table,count,128)
            struct.pack_into('<I',raw,16,zlib.crc32(raw[:92]));return bytes(raw)
        first=header(1,sectors-1,2);last=header(sectors-1,1,sectors-1-rows)
        self.assertEqual(installer.gpt_backup_layout(first,last,sectors),(2+rows,sectors-1-rows))
        with self.assertRaises(ValueError):installer.gpt_backup_layout(first[:-1],last,sectors)
        bad=bytearray(last);bad[80]^=1
        with self.assertRaises(ValueError):installer.gpt_backup_layout(first,bytes(bad),sectors)

    def native_fixture(self,failure=False):
        import shutil
        bash=Path('C:/Program Files/Git/bin/bash.exe') if sys.platform=='win32' else shutil.which('bash')
        if not bash or not Path(bash).exists():self.skipTest('Bash is needed for the native updater fixture')
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);disk=base/'disk';old=disk/'openwrt';stage=base/'stage';stage.mkdir()
            files={'etc/config/wireless':b'user settings','etc/mu300/desktop-mode':b'part\n',
                   'lib/firmware/wcnmodem.bin':b'own firmware','opt/mu300/android/vendor/bin/modem_control':b'own modem'}
            for n,data in files.items():p=old/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            (old/'etc/rc.d').mkdir(parents=True)
            (old/'etc/rc.d/S99demo').symlink_to('../init.d/demo')
            with tarfile.open(base/'mu300-openwrt.tar.gz','w:gz') as a:
                for n,data in (('sbin/init',b'#!/bin/sh\n'),('etc/config/wireless',b'image settings'),
                               ('etc/config/new-service',b'new settings'),('etc/init.d/demo',b'#!/bin/sh\n'),
                               ('etc/mu300/image-version',b'new\n'),
                               ('lib/firmware/regulatory.db',b'image database'),
                               ('lib/firmware/wcnmodem.bin',b'DONOR firmware'),
                               ('opt/mu300/android/vendor/bin/modem_control',b'DONOR modem')):
                    m=tarfile.TarInfo(n);m.size=len(data);m.mode=0o755;a.addfile(m,io.BytesIO(data))
            with installer.payload_rootfs(base) as archive:
                shutil.copyfile(archive,stage/'mu300-openwrt-rootfs.tar.gz')
            library=(TOP/'rootfs/overlay/opt/mu300/bin/mu300-update').as_posix()
            pre='cp() { case "$*" in *wcnmodem.bin*) return 1;; esac; command cp "$@"; };' if failure else ''
            script=f'export MU300_LIB=1 MU300_DISK="{disk.as_posix()}"; . "{library}"; STAGE=.; sync() {{ :; }}; {pre} if apply_one openwrt local; then exit 0; else exit 1; fi'
            result=subprocess.run([str(bash),'-c',script],cwd=stage,capture_output=True,text=True,timeout=30)
            if failure:
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertFalse((disk/'openwrt.old').exists())
                for n,data in files.items():self.assertEqual((old/n).read_bytes(),data)
            else:
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual((old/'etc/config/wireless').read_bytes(),b'user settings')
                self.assertEqual((old/'etc/config/new-service').read_bytes(),b'new settings')
                self.assertEqual((old/'lib/firmware/wcnmodem.bin').read_bytes(),b'own firmware')
                self.assertEqual((old/'opt/mu300/android/vendor/bin/modem_control').read_bytes(),b'own modem')
                self.assertEqual((disk/'openwrt.old/etc/config/wireless').read_bytes(),b'user settings')
                self.assertTrue((old/'etc/rc.d/S99demo').is_symlink())
                self.assertEqual(os.readlink(old/'etc/rc.d/S99demo'),'../init.d/demo')

    def test_real_native_update_preserves_configuration_and_device_firmware(self):
        self.native_fixture()

    def test_real_native_vendor_copy_failure_keeps_old_root(self):
        self.native_fixture(failure=True)

    def test_local_update_creates_boot_state_directory_without_removing_user_files(self):
        if os.name!='posix':self.skipTest('POSIX update fixture')
        script=(TOP/'desktop/local-update.sh').read_text()
        prepare='mkdir -p "$DISK/.mu300"'
        self.assertLess(script.index(prepare),script.index('if ! apply_one openwrt'))
        start=script.index('echo linux > "$DISK/openwrt/etc/mu300/default-boot"')
        commands=prepare+'\n'+script[start:script.index('\nsync',start)]
        for collision in (False,True):
            with self.subTest(collision=collision),tempfile.TemporaryDirectory() as t:
                disk=Path(t);(disk/'openwrt/etc/mu300').mkdir(parents=True)
                if collision:(disk/'.mu300').write_bytes(b'USER FILE')
                result=subprocess.run(['sh','-ec',commands],env={**os.environ,'DISK':str(disk)},capture_output=True,text=True)
                if collision:
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual((disk/'.mu300').read_bytes(),b'USER FILE')
                else:
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual((disk/'.mu300/boot-tries').read_text(),'0\n')
                    self.assertEqual((disk/'.mu300/boot-os').read_text(),'openwrt\n')
                    (disk/'.mu300/user-data').write_bytes(b'KEEP')
                    again=subprocess.run(['sh','-ec',commands],env={**os.environ,'DISK':str(disk)},capture_output=True,text=True)
                    self.assertEqual(again.returncode,0,again.stderr)
                    self.assertEqual((disk/'.mu300/user-data').read_bytes(),b'KEEP')

    def test_boot_control_both_slots_preserve_reserved_bytes(self):
        for slot in ('a','b'):
            source=live(slot);result=boot.boot_control(source,slot)
            self.assertEqual(result[:2],b'_b' if slot=='a' else b'_a')
            self.assertEqual(result[16:28],source[16:28])
            self.assertEqual(zlib.crc32(result[:28]),struct.unpack_from('<I',result,28)[0])
            android=12 if slot=='a' else 14
            self.assertTrue(result[android]&128)

    def test_corrupt_boot_control_is_rejected(self):
        with self.assertRaises(ValueError):boot.boot_control(bytes(32),'a')
        with self.assertRaises(ValueError):boot.boot_control(live()[:-1]+b'x','a')

    def test_fbe_with_plain_superblock_still_requires_format(self):
        self.assertTrue(installer.encrypted({'state':'encrypted','type':'file'},'/dev/block/mmcblk0p40','1020f5f2'))
        self.assertTrue(installer.encrypted({'state':'unencrypted'},'/dev/block/dm-0','53ef'))
        self.assertFalse(installer.encrypted({'state':'unencrypted','type':''},'/dev/block/mmcblk0p40','53ef'))
        with self.assertRaises(ValueError):installer.encrypted({'state':''},'/dev/block/mmcblk0p40','1020f5f2')

    def test_fractional_size_and_bounds(self):
        self.assertEqual(installer.image_size('6.5',10240),6656)
        for value in ('nan','inf','1.9','11','hello'):
            with self.subTest(value=value),self.assertRaises(ValueError):installer.image_size(value,10240)
        target,need=installer.allocation(32*2097152-34,10240)
        self.assertEqual(need,10*2097152);self.assertEqual(target%2048,0)
        self.assertLessEqual(target+need,32*2097152-34)

    def test_resume_wrong_device_geometry_or_marker_never_pass(self):
        s={'CID':'a'*32,'NODE':'mmcblk0p40','START':65536,'SIZE':20971520}
        r={'storage':s.copy(),'android_slot':'a','serial':'SERIAL','target':s['SIZE'],'need':8388608,'token':'b'*32}
        marker=('MU300-PART-PREPARED-V2 '+r['token']+'\n').encode()
        geom=(s['START']+s['SIZE'],r['need'])
        installer.validate_resume(s,r,'a','SERIAL',geom,marker)
        for args in (({**s,'CID':'c'*32},r,'a','SERIAL',geom,marker),(s,r,'b','SERIAL',geom,marker),
                     (s,r,'a','WRONG',geom,marker),(s,r,'a','SERIAL',(geom[0],geom[1]-2048),marker),
                     (s,r,'a','SERIAL',geom,b'WRONG')):
            with self.assertRaises(ValueError):installer.validate_resume(*args)

    def test_environment_quotes_user_strings(self):
        value="my Wi-Fi ' $(touch /tmp/not-run)"
        text=installer.env({'SSID':value})
        self.assertNotIn('SSID="',text)
        import shlex
        self.assertEqual(shlex.split(text.split('=',1)[1])[0],value)

    def test_public_update_archive_has_no_donor_runtime(self):
        with tempfile.TemporaryDirectory() as t:
            src=Path(t)/'original.tar.gz';dst=Path(t)/'public.tar.gz'
            names=('bin/busybox','lib/firmware/wcnmodem.bin','lib/firmware/regulatory.db',
                   'lib/firmware/rtl_nic/rtl.bin','opt/mu300/android/vendor/bin/modem_control')
            with tarfile.open(src,'w:gz') as f:
                for n in names:
                    m=tarfile.TarInfo('./'+n);m.size=4;f.addfile(m,io.BytesIO(b'data'))
            original=src.read_bytes();installer.public_rootfs(src,dst)
            self.assertEqual(src.read_bytes(),original)
            with tarfile.open(dst) as f:self.assertEqual(set(m.name for m in f),{'./bin/busybox','./lib/firmware/regulatory.db','./lib/firmware/rtl_nic/rtl.bin'})

    def test_shared_rootfs_tf_is_unchanged_and_temp_is_cleaned_on_failure(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'mu300-openwrt.tar.gz'
            with tarfile.open(source,'w:gz') as tar:
                for name in ('bin/busybox','lib/firmware/wcnmodem.bin'):
                    member=tarfile.TarInfo(name);member.size=1;tar.addfile(member,io.BytesIO(b'B'))
            original=source.read_bytes()
            with installer.payload_rootfs(root,tf=True) as archive:
                self.assertEqual(archive,source)
                self.assertEqual(archive.read_bytes(),original)
            for fail in (False,True):
                try:
                    with installer.payload_rootfs(root) as archive:
                        self.assertNotEqual(archive.parent,root)
                        with tarfile.open(archive) as tar:self.assertEqual(tar.getnames(),['bin/busybox'])
                        if fail:raise RuntimeError('upload failed')
                except RuntimeError as exc:
                    self.assertTrue(fail);self.assertEqual(str(exc),'upload failed')
                self.assertFalse(archive.exists())
                self.assertFalse(archive.parent.exists())
                self.assertEqual(source.read_bytes(),original)
            self.assertEqual(list(root.iterdir()),[source])

    def test_android_payload_uses_shared_tf_and_filtered_internal_rootfs(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'mu300-openwrt.tar.gz'
            with tarfile.open(source,'w:gz') as tar:
                for name in ('bin/busybox','lib/firmware/wcnmodem.bin'):
                    member=tarfile.TarInfo(name);member.size=1;tar.addfile(member,io.BytesIO(b'B'))
            for tf in (False,True):
                android=object.__new__(installer.Android);android.bb='/tmp/busybox';android.p='/tmp/payload'
                uploads=[]
                def upload(path,name):
                    if name=='mu300-openwrt.tar.gz':
                        with tarfile.open(path) as tar:uploads.append((Path(path),tar.getnames()))
                with patch.object(android,'push',side_effect=upload), patch.object(android,'shell'), patch.object(android,'run'):
                    android.prepare_payload(root,tf=tf)
                self.assertEqual(len(uploads),1)
                path,names=uploads[0]
                self.assertEqual(names,['bin/busybox','lib/firmware/wcnmodem.bin'] if tf else ['bin/busybox'])
                self.assertEqual(path.exists(),tf)
                self.assertEqual(path==source,tf)

    def test_boot_append_leaves_kernel_original_frame_and_log_tail_intact(self):
        with tempfile.TemporaryDirectory() as t:
            assets=Path(t);(assets/'repair').mkdir()
            (assets/'storage-lib.sh').write_bytes((TOP/'rootfs/overlay/opt/mu300/lib/storage.sh').read_bytes())
            for n in ('storage.sh','install-part.sh','part-prepare.sh','part-progress.sh','ext4-geometry.sh','mu300-gpt'):
                (assets/n).write_bytes((TOP/'desktop/boot'/n).read_bytes())
            for n,source in (('original-init',TOP/'boot/init'),('build-boot-image.py',TOP/'boot/build-boot-image.py')):(assets/n).write_bytes(source.read_bytes())
            (assets/'internal-ext4.sh').write_bytes((TOP/'boot/internal-ext4.sh').read_bytes())
            (assets/'repair/manifest.json').write_text('{"sha256":{}}')
            ram=frame(b'original ramdisk');kernel=b'K'*4096
            base=bytearray(64<<20);base[:8]=b'ANDROID!';struct.pack_into('<II',base,8,len(kernel),len(ram));struct.pack_into('<I',base,40,4)
            base[44:55]=b'loglevel=5\0';base[4096:8192]=kernel;base[8192:8192+len(ram)]=ram
            off=(8192+len(ram)+4095)&~4095;base[off:off+64]=b'V'*64;base[-64:-60]=b'AVBf';struct.pack_into('>IIQQQ',base,len(base)-60,1,0,off,off,64)
            before=bytes(base)
            for slot in ('a','b'):
                for mode in ('prepare','legacy'):
                    out=boot.build(before,assets,mode,live(slot),slot,prepare='request' if mode=='prepare' else None)
                    self.assertEqual(out[4096:8192],kernel);self.assertEqual(out[44:55],b'loglevel=5\0')
                    rs=struct.unpack_from('<I',out,12)[0]
                    streams=unpack_frames(out[8192:8192+rs]);self.assertEqual(streams[0],b'original ramdisk')
                    contents=cpio_files(streams[-1]);self.assertEqual(contents['etc/mu300-desktop-root-mode'],(mode+'\n').encode())
                    self.assertEqual(contents['etc/ext4-geometry.sh'],(assets/'ext4-geometry.sh').read_bytes())
                    self.assertEqual(contents['etc/mu300-internal-ext4.sh'],(assets/'internal-ext4.sh').read_bytes())
                    if mode=='prepare':self.assertEqual(contents['etc/mu300-desktop-prepare'],b'request\n')
                    else:
                        self.assertNotIn('etc/mu300-desktop-prepare',contents)
                        self.assertIn(b'find_root_offset',contents['init'])
                        self.assertIn(b'root_target=auto',contents['init'])
                    self.assertNotIn(b'e5root',contents['init'])
                    self.assertEqual(len(out),len(before));self.assertEqual(out[48<<20:56<<20],before[48<<20:56<<20])
            self.assertEqual(bytes(base),before)

    def test_tool_architecture_guard(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'source';dest=Path(t)/'dest';source.mkdir();dest.mkdir()
            p=source/'sbin/fsck.f2fs';p.parent.mkdir();p.write_bytes(b'not ARM64')
            with self.assertRaises(ValueError):repair.prepare(source,dest)

    def test_all_original_members_survive_augmentation_and_checks_detect_tampering(self):
        with tempfile.TemporaryDirectory() as t:
            stage=Path(t)/'stage';tools=Path(t)/'tools';stage.mkdir();tools.mkdir()
            for n in ('busybox','boot-linux.img','payload.sha256','customize.sh','module.prop'):(stage/n).write_bytes(('original '+n).encode())
            with tarfile.open(stage/'mu300-openwrt.tar.gz','w:gz') as f:
                m=tarfile.TarInfo('bin/busybox');m.size=1;f.addfile(m,io.BytesIO(b'B'))
            for n in repair.FILES:
                p=tools/n;p.parent.mkdir(parents=True,exist_ok=True)
                elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',elf,18,183);p.write_bytes(elf)
            (stage/'customize.sh').write_text('ORIGINAL_PAYLOAD=$MODPATH\n[ -f "$MODPATH/boot-linux.img" ]\n')
            original=package.original_files(stage)
            # This tiny Magisk fixture deliberately exercises the CLI-only layout.
            # Portable GUI artifacts are verified by the shared release tests.
            with patch.dict(os.environ, {'OPENWRT_GUI_OPTIONAL':'1'}):
                manifest=package.assemble(stage,tools,'7.2')
            self.assertEqual({n:package.sha(stage/manifest['magisk_layout'][n]) for n in original},original)
            self.assertEqual([p.relative_to(stage).as_posix() for p in stage.rglob('*.tar.gz')],['payload/mu300-openwrt.tar.gz'])
            self.assertEqual({p.name for p in stage.iterdir() if p.is_file()}, {'OpenWrt-Installer.py','START-HERE.md','customize.sh','module.prop'})
            guide=(stage/'START-HERE.md').read_text(encoding='utf-8')
            self.assertIn('START-HERE.md',manifest['desktop'])
            self.assertIn('OpenWrt-Installer.exe',guide)
            self.assertIn('OpenWrt-Installer.py',guide)
            self.assertIn('EXPERIMENTAL',guide)
            self.assertTrue((stage/'launchers/start-installer.cmd').is_file())
            self.assertTrue((stage/'docs/DESKTOP-INSTALL.md').is_file())
            self.assertEqual(installer.payload_file(stage,'boot-linux.img'),stage/'payload/boot-linux.img')
            import shutil, shlex
            bash=Path('C:/Program Files/Git/bin/bash.exe') if sys.platform=='win32' else shutil.which('bash')
            if bash and Path(bash).exists():
                p=shlex.quote(stage.as_posix());wrapper=shlex.quote((stage/'customize.sh').as_posix())
                result=subprocess.run([str(bash),'-c',f'MODPATH={p}; . {wrapper}; printf "%s\\n%s\\n" "$ORIGINAL_PAYLOAD" "$MODPATH"'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.splitlines(),[(stage/'payload').as_posix(),stage.as_posix()])
            installer.verify(stage)
            result=subprocess.run([sys.executable,'-X','utf8',str(stage/'OpenWrt-Installer.py'),'--check'],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            (stage/'desktop/configure.sh').write_text('tampered')
            with self.assertRaises(ValueError):installer.verify(stage)

    def test_switch_boot_does_not_require_userdata_or_root_image_detection(self):
        sys.path.insert(0,str(TOP/'installer/tests'))
        from test_mu300_boot_switch import AndroidFixture
        for slot in ('a','b'):
            android=AndroidFixture(slot,'V50')
            android.storage=Mock(side_effect=AssertionError('Switching must not depend on userdata'))
            android.flash=Mock(side_effect=AssertionError('Switching must not rewrite boot'))
            with patch.object(installer,'Android',return_value=android), patch.object(installer,'module'):
                result=installer.install(Mock(boot_openwrt=True),TOP,{})
            self.assertEqual(result.state,'success')
            android.storage.assert_not_called();android.flash.assert_not_called()
            self.assertIn('only misc changed',result.message)
            self.assertIn('default-boot settings are unchanged',result.next_step)
            self.assertEqual(android.calls[-1],('reboot',))

    def test_menu_warns_about_userdata_image_f2fs_risk(self):
        with patch.object(installer,'package_root',return_value=Path('/test/package')), \
             patch.object(installer,'verify',return_value={'kernel':'7.2'}), \
             patch('builtins.input',return_value='2'), \
             patch.object(installer,'install',return_value=installer.Outcome('checked','test')) as install, \
             patch.object(installer.sys,'stdout',io.StringIO()) as output:
            installer.main([])
        self.assertTrue(install.call_args.args[0].data)
        self.assertIn('EXPERIMENTAL',output.getvalue())
        self.assertIn('Android F2FS',output.getvalue())

if __name__=='__main__':unittest.main()
