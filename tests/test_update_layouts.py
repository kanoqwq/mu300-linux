"""Real updater/boot selection against temporary files; no devices or network."""
import hashlib
import io
import struct
import subprocess
import tarfile
import zlib
from pathlib import Path

from helpers import BIN, TOP, ShellTest


def bundle(path, features='', module=b'new module'):
    files={'./Image': b'new kernel', './ramdisk-generic.lz4': b'\x02\x21\x4c\x18generic',
           './devices': b'f50 u30air v50\n', './kernel.release': b'7.2.8\n',
           './modules/driver.ko': module, './modules.builtin': b'driver builtin\n'}
    if features: files['./features']=features.encode()
    with tarfile.open(path,'w:gz') as archive:
        directory=tarfile.TarInfo('./modules');directory.type=tarfile.DIRTYPE;directory.mode=0o755;archive.addfile(directory)
        for name,data in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))


class UpdateBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root';(self.root/'run/mu300').mkdir(parents=True)
        self.disk=self.tmp/'disk';(self.disk/'openwrt/etc/mu300').mkdir(parents=True)
        (self.disk/'.mu300-update').mkdir()
        (self.disk/'openwrt/etc/mu300/image-version').write_text('old\n')
        self.plain=self.tmp/'plain.tar.gz';bundle(self.plain)
        self.gpt=self.tmp/'gpt.tar.gz';bundle(self.gpt,'sdcard\ngpt-root\n')

    def up(self,shell,code,**env):
        return self.sh(shell,f'. "{BIN}/mu300-update"; '+code,MU300_LIB=1,MU300_DISK=self.disk,
                       MU300_SYSROOT=self.root,MU300_BIN=BIN,**env)


class Layouts(UpdateBase):
    def test_actual_trial_updates_only_misc_and_does_not_change_saved_default(self):
        run=self.tmp/'trial-run';(run/'mu300').mkdir(parents=True)
        area=self.tmp/'trial-disk';(area/'.mu300').mkdir(parents=True)
        conf=self.tmp/'default-boot';misc=self.tmp/'misc';script=self.tmp/'next-boot'
        source=(BIN/'mu300-next-boot').read_text().replace('/run/',str(run)+'/')
        source=source.replace('/mnt/mu300-disk',str(area)).replace('CONF=/etc/mu300/default-boot','CONF='+str(conf))
        script.write_text(source)
        (run/'mu300/misc-dev').write_text(str(misc)+'\n')
        (area/'.mu300/boot-attempts').write_text('3\n')
        for slot in ('a','b'):
            bc=bytearray(32);bc[:4]=('_'+slot).encode()+b'\0\0';bc[4:10]=b'BCAB\x01\x02'
            bc[12],bc[14]=(0x2f,0x9e) if slot=='a' else (0x9e,0x2f)
            struct.pack_into('<I',bc,28,zlib.crc32(bc[:28]))
            (run/'mu300/linux-slot').write_text(slot+'\n')
            (run/'mu300/misc-bc-linux-trial.bin').write_bytes(bc)
            android=bytearray(bc)
            android[:4]=b'_b\0\0' if slot=='a' else b'_a\0\0'
            android[12],android[14]=(0x1e,0x9f) if slot=='a' else (0x9f,0x1e)
            struct.pack_into('<I',android,28,zlib.crc32(android[:28]))
            (run/'mu300/misc-bc-android.bin').write_bytes(android)
            for default in ('android','linux',None):
                conf.unlink(missing_ok=True)
                if default:conf.write_text(default+'\n')
                (area/'.mu300/boot-tries').write_text('8\n')
                before=b'P'*4096;misc.write_bytes(before)
                result=subprocess.run(['bash',str(script),'--trial'],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(conf.read_text() if conf.exists() else None,default+'\n' if default else None)
                after=misc.read_bytes();self.assertEqual(after[:2048],before[:2048]);self.assertEqual(after[2080:],before[2080:])
                block=after[2048:2080];self.assertEqual(block[12 if slot=='a' else 14],0x4f)
                self.assertEqual(struct.unpack_from('<I',block,28)[0],zlib.crc32(block[:28]))
                self.assertEqual((area/'.mu300/boot-tries').read_text(),'0\n')
            # No role file is needed when the supplied Linux trial block
            # identifies its slot. Unknown/conflicting roles may not write misc.
            role=run/'mu300/linux-slot'
            role.unlink()
            result=subprocess.run(['bash',str(script),'--trial'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            original=misc.read_bytes()
            role.write_text(('b' if slot=='a' else 'a')+'\n')
            result=subprocess.run(['bash',str(script),'--trial'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(misc.read_bytes(),original)
            role.write_text(slot+'\n')
            (run/'mu300/misc-bc-android.bin').write_bytes(bc)
            result=subprocess.run(['bash',str(script),'android'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(misc.read_bytes(),original)
            (run/'mu300/misc-bc-android.bin').write_bytes(android)

    def test_root_device_detection_does_not_mount_userdata(self):
        sys=self.root/'sys/class/block'
        for name,back in [('loop0','/dev/mmcblk0'),('loop1','/mu300-data/mu300linux/openwrt.ext4')]:
            p=sys/name/'loop';p.mkdir(parents=True);(p/'backing_file').write_text(back+'\n')
        p=sys/'mmcblk0p76';p.mkdir();(p/'uevent').write_text('PARTNAME=mu300root\n')
        p=sys/'mmcblk1/device';p.mkdir(parents=True);(p/'type').write_text('SD\n')
        for shell in self.each_shell():
            for dev,want in [('/dev/mmcblk0p76','part'),('/dev/loop0','region'),('/dev/loop1','image'),
                             ('/dev/mmcblk1p1','sd'),('/dev/loop9','unknown'),('/dev/mmcblk0p3','unknown')]:
                (self.root/'run/mu300/disk-dev').write_text(dev+'\n')
                result=self.up(shell,'mount(){ echo unexpected >&2; return 1; }; root_layout')
                self.assertEqual((result.returncode,result.stdout.strip(),result.stderr),(0,want,''))

    def test_apply_refuses_before_system_or_module_changes(self):
        for shell in self.each_shell():
            for layout,archive,ok in [('part',self.plain,False),('part',self.gpt,True),
                                      ('image',self.gpt,False),('sd',self.plain,True),('region',self.plain,True),
                                      ('part',self.tmp/'missing.tar.gz',False)]:
                events=self.tmp/'events';events.unlink(missing_ok=True)
                (self.disk/'.mu300-update').mkdir(exist_ok=True)
                code='''root_layout(){ echo "$LAYOUT"; }
fetch_verified(){ return 0; }
df(){ printf 'Filesystem K Used Avail\nfake 999999999 0 999999999\n'; }
is_root(){ return 1; }
pause_data(){ echo pause >> "$STUBLOG/events"; }
resume_data(){ :; }
apply_one(){ echo root >> "$STUBLOG/events"; }
boot_update(){ echo boot >> "$STUBLOG/events"; }
apply openwrt
'''
                result=self.up(shell,code,MU300_SELF_UPDATED=1,MU300_RELEASE='new',MU300_KERNEL_BUNDLE=archive,LAYOUT=layout)
                self.assertEqual(result.returncode,0 if ok else 1,result.stdout+result.stderr)
                self.assertEqual(events.read_text().splitlines() if events.exists() else [],['pause','root','boot'] if ok else [])

    def test_trial_arming_uses_installed_system_tool(self):
        tool=self.disk/'openwrt/opt/mu300/bin/mu300-next-boot';tool.parent.mkdir(parents=True)
        tool.write_text('#!/bin/sh\n[ "$1" = --trial ] || exit 1\necho new-tool\n');tool.chmod(0o755)
        for shell in self.each_shell():
            result=self.up(shell,'running_os(){ echo openwrt; }; unlock_for_trial')
            self.assertEqual((result.returncode,result.stdout),(0,'new-tool\n'),result.stderr)


class BootUpdate(UpdateBase):
    def fixture(self):
        dev=self.tmp/'boot.bin';raw=bytearray(2<<20);raw[:8]=b'ANDROID!'
        struct.pack_into('<II',raw,8,4096,4096);struct.pack_into('<I',raw,40,4)
        raw[4096:8192]=b'K'*4096;raw[8192:8196]=bytes.fromhex('02214c18')
        raw[12288:16384]=b'V'*4096;raw[-64:-60]=b'AVBf'
        struct.pack_into('>QQQ',raw,len(raw)-52,12288,12288,4096);dev.write_bytes(raw)
        module=self.disk/'openwrt/lib/modules/7.2.8/driver.ko';module.parent.mkdir(parents=True,exist_ok=True);module.write_bytes(b'old module')
        return dev,module

    def run_boot(self,shell,archive,dev):
        code='''part_dev(){ echo "$FAKE_BOOT"; }
this_device(){ echo f50; }
root_layout(){ echo "$LAYOUT"; }
running_os(){ echo openwrt; }
cat(){ case "$1" in /sys/class/block/boot.bin/size) echo 4096;; *) command cat "$@";; esac; }
sync(){ :; }
unlock_for_trial(){ echo trial >> "$STUBLOG/events"; }
write_boot(){
    echo write >> "$STUBLOG/events"
    command cat "$3" "$4" > "$1"
    dd if="$5" of="$1" bs=64 seek=$(($2/64-1)) conv=notrunc 2>/dev/null
}
boot_update test
'''
        return self.up(shell,code,MU300_KERNEL_BUNDLE=archive,FAKE_BOOT=dev,LAYOUT='part')

    def test_changed_modules_in_local_bundle_cannot_be_silently_skipped(self):
        for shell in self.each_shell():
            dev,module=self.fixture();events=self.tmp/'events';events.unlink(missing_ok=True)
            bundle(self.gpt,'gpt-root\n',b'module A')
            result=self.run_boot(shell,self.gpt,dev);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(module.read_bytes(),b'module A')
            expected=hashlib.sha256(self.gpt.read_bytes()).hexdigest()
            self.assertEqual((self.disk/'boot/installed.id').read_text().strip(),expected)
            count=events.read_text().count('write')
            result=self.run_boot(shell,self.gpt,dev);self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('already up to date',result.stdout)
            self.assertEqual(events.read_text().count('write'),count)
            bundle(self.gpt,'gpt-root\n',b'module B')
            result=self.run_boot(shell,self.gpt,dev);self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('already up to date',result.stdout)
            self.assertEqual(module.read_bytes(),b'module B')
            self.assertEqual(events.read_text().count('write'),count+1)

    def test_incompatible_layout_and_invalid_boot_leave_modules_untouched(self):
        for shell in self.each_shell():
            for bad in ('layout','header'):
                dev,module=self.fixture();events=self.tmp/'events';events.unlink(missing_ok=True)
                if bad=='header':
                    with dev.open('r+b') as stream:stream.write(b'bad boot')
                before=dev.read_bytes()
                result=self.run_boot(shell,self.plain if bad=='layout' else self.gpt,dev)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(module.read_bytes(),b'old module')
                self.assertEqual(dev.read_bytes(),before)
                self.assertFalse(events.exists())
