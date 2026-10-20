"""Both A/B layouts must preserve Android and arm only the opposite slot."""
import os
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from helpers import TOP


def misc_head():
    data = bytearray(4096)
    block = bytearray(32)
    block[:4] = b'_a\0\0'
    block[4:8] = b'BCAB'
    block[12] = 0x9f
    block[14] = 0x1e
    block[28:] = struct.pack('<I', zlib.crc32(block[:28]))
    data[2048:2080] = block
    return bytes(data)


class BootBlocks(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Requires POSIX shell')
    def test_init_detects_both_slots_and_refuses_unknown(self):
        source=(TOP/'boot/init').read_text()
        function=source[source.index('detect_linux_slot() {'):source.index('\nfind_partitions() {')]
        for prefix,args,wanted in [(b'_a',b'','a'),(b'_b',b'','b'),
                                   (b'??',b'androidboot.slot_suffix=_a\0','a'),
                                   (b'??',b'androidboot.slot_suffix=_b','b'),(b'??',b'','')]:
            with self.subTest(prefix=prefix,args=args),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);misc=root/'misc';misc.write_bytes(b'\0'*2048+prefix)
                dt=root/'bootargs';dt.write_bytes(args);cmd=root/'cmdline';cmd.write_bytes(b'')
                text=function.replace('/proc/device-tree/chosen/bootargs',str(dt)).replace('/proc/cmdline',str(cmd))
                script='log(){ :; }; misc='+str(misc)+'; linux_slot=;\n'+text+'\ndetect_linux_slot; r=$?; printf "%s:%s" "$r" "$linux_slot"'
                result=subprocess.run(['sh','-c',script],capture_output=True,text=True)
                self.assertEqual(result.stdout,('0:' if wanted else '1:')+wanted)

    def test_cpio_slot_metadata_has_its_parent_directory(self):
        build=runpy.run_path(str(TOP/'boot/build-boot-image.py'))
        raw=build['cpio_archive']({'dev'}, {'etc/mu300/linux-slot': (b'a\n', 0o100644)})
        offset=0;seen=set()
        while offset<len(raw):
            self.assertEqual(raw[offset:offset+6],b'070701')
            fields=[int(raw[offset+6+i*8:offset+14+i*8],16) for i in range(13)]
            size,namesize=fields[6],fields[11]
            name=raw[offset+110:offset+110+namesize-1].decode()
            start=(offset+110+namesize+3)&~3
            if name=='TRAILER!!!':break
            if '/' in name:self.assertIn(name.rsplit('/',1)[0],seen)
            seen.add(name);offset=(start+size+3)&~3
        self.assertIn('etc/mu300/linux-slot',seen)

    def test_both_android_and_linux_blocks_have_valid_crc(self):
        build = runpy.run_path(str(TOP / 'boot/build-boot-image.py'))
        android_a, linux_b, android_b, linux_a = build['bootloader_control'](misc_head())
        for block, suffix, a_meta, b_meta in (
            (android_a, b'_a', 0x9f, 0x1e),
            (linux_b, b'_b', 0x9e, 0x2f),
            (android_b, b'_b', 0x1e, 0x9f),
            (linux_a, b'_a', 0x2f, 0x9e),
        ):
            with self.subTest(suffix=suffix, a=a_meta, b=b_meta):
                self.assertEqual(block[:2], suffix)
                self.assertEqual((block[12], block[14]), (a_meta, b_meta))
                self.assertEqual(struct.unpack('<I', block[28:])[0], zlib.crc32(block[:28]))


@unittest.skipUnless(shutil.which('busybox') and shutil.which('sh'), 'BusyBox and sh are needed')
class SwitchSlots(unittest.TestCase):
    def test_switch_arms_opposite_slot_without_touching_boot_images(self):
        switch = TOP / 'android/magisk/mu300-linux-switch/switch.sh'
        for android_slot, linux_slot, language in (('_a', 'b', 'en'), ('_b', 'a', 'en'), ('_a', 'b', 'zh')):
            with self.subTest(android=android_slot, language=language), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                by_name = root / 'by-name'
                by_name.mkdir()
                boot_a = b'ANDROID!' + b'A' * 65528
                boot_b = b'ANDROID!' + b'B' * 65528
                (by_name / 'boot_a').write_bytes(boot_a)
                (by_name / 'boot_b').write_bytes(boot_b)
                (by_name / 'misc').write_bytes(misc_head())
                fake_bin = root / 'bin'
                fake_bin.mkdir()
                (fake_bin / 'id').write_text('#!/bin/sh\n[ "$1" = -u ] && echo 0 || /usr/bin/id "$@"\n')
                (fake_bin / 'id').chmod(0o755)
                env = dict(os.environ, MU300_BY_NAME=str(by_name), MU300_SLOT_SUFFIX=android_slot,
                           MU300_INSTALL_LANG=language, MU300_BUSYBOX=shutil.which('busybox'), TMPDIR=tmp,
                           PATH=str(fake_bin) + os.pathsep + os.environ['PATH'])
                result = subprocess.run(['sh', str(switch), '--no-reboot'], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'已设置下次从 {linux_slot} 槽位启动一次' if language == 'zh'
                              else f'slot {linux_slot} armed for one boot', result.stdout)
                block = (by_name / 'misc').read_bytes()[2048:2080]
                self.assertEqual(block[:2], ('_' + linux_slot).encode())
                self.assertEqual((block[12], block[14]),
                                 (0x2f, 0x9e) if linux_slot == 'a' else (0x9e, 0x2f))
                self.assertEqual(struct.unpack('<I', block[28:])[0], zlib.crc32(block[:28]))
                self.assertEqual((by_name / 'boot_a').read_bytes(), boot_a)
                self.assertEqual((by_name / 'boot_b').read_bytes(), boot_b)


if __name__ == '__main__':
    unittest.main()
