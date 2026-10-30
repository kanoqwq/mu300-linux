"""Pre-mount worker uses private regular-file fixtures, never real userdata."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import android_growth_boot as growth
from backend.state import RecoveryRequired
import test_uninstall as fx


class ImageBounds(unittest.TestCase):
    def test_only_ramdisk_can_change(self):
        original = bytearray(64 << 20)
        original[:8] = b'ANDROID!'
        struct.pack_into('<III', original, 8, 4096, 4096, 0)
        struct.pack_into('<I', original, 40, 4)
        updated = bytearray(original); updated[8192] = 1
        growth.verify_image(original, updated)
        for offset in (8, 16, 40, 44, 4096):
            bad = bytearray(updated); bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                growth.verify_image(original, bad)
        with self.assertRaises(ValueError):growth.verify_image(original, original)


class PreMount(unittest.TestCase):
    def setUp(self):
        self.fx = fx.Journal(); self.fx.setUp()
        ctx = self.fx.plan()
        self.record = ctx.record
        self.record['uninstall']['volume'].update(uuid='55'*16,filesystem_bytes=28*fx.GIB)
        self.adapter = self.fx.adapter
        self.adapter.root = Path(self.fx.temp.name)
        self.adapter.adb.shell = Mock(return_value='a'*64+'  /system/bin/fsck.f2fs')
        self.adapter.adb.call = Mock()
        self.volume = {'android_growth':{'resizer_sha256':'b'*64}}
        # A successful Android A control, with an inactive low-priority slot.
        raw = bytearray(32); raw[:8]=b'_a\0\0BCAB'; raw[8]=1;raw[9]=2;raw[12]=0x9f;raw[14]=0x1e
        import zlib
        struct.pack_into('<I', raw, 28, zlib.crc32(raw[:28]))
        self.control = bytes(raw)

    def tearDown(self):self.fx.tearDown()

    def rendered(self):
        return growth.scripts(self.record, self.adapter, self.volume, 'c'*64)

    def test_restore_requires_original_backup_and_exact_maintenance_image(self):
        directory=Path(self.fx.temp.name)/'restore-fixture'
        source=directory/'uninstall/android-growth/android.img'
        source.parent.mkdir(parents=True)
        with source.open('wb') as stream:stream.truncate(64 << 20)
        expected=growth.sha(source)
        device=self.record['device']
        self.record.update(uninstall_android_growth_attempted=True,
                           uninstall_android_growth_source_sha256=expected,
                           uninstall_android_growth_sha256='f'*64)
        ctx=SimpleNamespace(directory=directory,store=SimpleNamespace(save=Mock()),effect=Mock())
        worker=SimpleNamespace(r=self.record,adapter=self.adapter,ctx=ctx,layout_state=lambda:'after')
        self.adapter.adb.shell=Mock(return_value=expected+'  boot_a')
        growth.restore(worker,device)
        ctx.effect.assert_not_called()
        self.adapter.adb.shell=Mock(return_value='f'*64+'  boot_a')
        with patch('backend.android_growth_boot.push_chunks') as push:
            growth.restore(worker,device)
        push.assert_called_once()
        ctx.effect.assert_called_once()
        # Executing only the mocked shell callback proves target selection.
        ctx.effect.call_args.args[1]()
        script=self.adapter.adb.shell.call_args.args[0]
        self.assertIn('of=/dev/block/by-name/boot_a',script)
        self.assertNotIn('misc',script)
        self.assertNotIn('userdata',script)
        self.assertNotIn('of=/dev/block/by-name/boot_b',script)
        self.adapter.adb.shell=Mock(return_value='e'*64+'  boot_a')
        with self.assertRaisesRegex(RecoveryRequired,'neither'):
            growth.restore(worker,device)
        source.write_bytes(b'broken')
        with self.assertRaisesRegex(RecoveryRequired,'backup'):
            growth.restore(worker,device)

    def test_templates_keep_keys_gpt_and_raw_userdata_out_of_resize(self):
        files = self.rendered()
        joined = b'\n'.join(files.values()).decode()
        self.assertNotIn('@TARGET@',joined)
        self.assertNotIn('dmsetup',joined)
        self.assertNotIn('setenforce',joined)
        self.assertNotIn('mkfs',joined)
        wrapper = next(v.decode() for k,v in files.items() if k.endswith('-wrapper.sh'))
        self.assertIn('mapper-mounted',wrapper)
        self.assertIn('filesystem-identity',wrapper)
        self.assertIn('"$base/resize.f2fs" -t',wrapper)
        self.assertNotIn('of=/dev/',wrapper)

    def test_e5_ufs_embeds_its_identity_tool_and_preserves_actual_slot(self):
        self.record['device']['slot']='_b'
        self.record['device']['storage']['NODE']='sda75'
        folder=self.adapter.root/'files';folder.mkdir(exist_ok=True)
        binary=bytearray(32);binary[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',binary,18,183)
        (folder/'e5-ufs-id').write_bytes(binary)
        files=self.rendered()
        self.assertEqual(next(value for name,value in files.items() if name.endswith('-ufs-id')),binary)
        setup=next(value.decode() for name,value in files.items() if name.endswith('-setup.sh'))
        self.assertIn('export E5_UFS_ID_HELPER="$2"',setup)
        self.assertIn('= _b',setup)
        rc=next(value.decode() for name,value in files.items() if name.endswith('.rc'))
        self.assertIn('-ufs-id',rc)
        (folder/'e5-ufs-id').write_bytes(b'bad')
        with self.assertRaises(ValueError):self.rendered()

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux/Android command semantics')
    def test_real_wrapper_success_refusal_and_failure_receipts(self):
        for fault in ('none','4k','mounted','uuid','mapping','fsck','resizer','no-growth'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);base=root/'base';base.mkdir();sysroot=root/'sys';node=sysroot/'dm-4'
                (node/'dm').mkdir(parents=True);(node/'slaves').mkdir()
                slave=self.record['device']['storage']['NODE']
                (node/'slaves'/('wrong' if fault=='mapping' else slave)).touch()
                (node/'dev').write_text('254:4\n')
                mounts=root/'mountinfo';mounts.write_text('1 0 254:4 / /data rw - f2fs /dev/block/dm-4 rw\n' if fault=='mounted' else '')
                device=root/'dm-4';raw=bytearray(8192)
                raw[1024:1028]=bytes.fromhex('1020f5f2');struct.pack_into('<I',raw,1040,12)
                struct.pack_into('<Q',raw,1060,28*fx.GIB//4096)
                raw[1132:1148]=bytes.fromhex(('66' if fault=='uuid' else '55')*16)
                device.write_bytes(raw)
                marker=root/'calls'
                (base/'fsck.f2fs').write_text('#!/bin/sh\necho fsck >> '+str(marker)+'\nexit '+('1' if fault=='fsck' else '0')+'\n')
                # Fixture resizer updates only the synthetic superblock count.
                (base/'resize.f2fs').write_text('#!/bin/sh\necho resize >> '+str(marker)+'\n'+
                    '[ "$1" = -t ] && [ "$2" = '+str(32*fx.GIB//(4096 if fault=='4k' else 512))+' ] || exit 9\n'+
                    ('exit 1\n' if fault=='resizer' else '' if fault=='no-growth' else
                     "printf '\\000\\000\\200\\000\\000\\000\\000\\000' | dd of="+str(device)+' bs=1 seek=1060 conv=notrunc 2>/dev/null\n'))
                for file in (base/'fsck.f2fs',base/'resize.f2fs'):file.chmod(0o755)
                files=self.rendered()
                source=next(v.decode() for k,v in files.items() if k.endswith('-wrapper.sh'))
                source=source.replace('/dev/openwrt-grow-'+self.record['id'],str(base)).replace('/sys/class/block',str(sysroot))
                source=source.replace('/proc/self/mountinfo',str(mounts)).replace('/proc/1/mountinfo',str(mounts))
                sector=4096 if fault=='4k' else 512
                source='blockdev() { if [ "$1" = --getss ]; then echo '+str(sector)+'; else echo '+str(32*fx.GIB)+'; fi; }\n'+source.partition('\n')[2]
                wrapper=root/'fsck.f2fs';wrapper.write_text(source)
                result=subprocess.run(['sh',str(wrapper),'-a',str(device)],capture_output=True,text=True)
                if fault in ('none','4k'):
                    self.assertEqual(result.returncode,0,result.stderr+(base/'worker.log').read_text())
                    self.assertEqual((base/'result').read_text().strip(),'GROWTH_VERIFIED')
                    self.assertEqual(marker.read_text().split(),['fsck','resize'])
                elif fault=='mapping':
                    # Other devices get the unchanged stock invocation only.
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(marker.read_text().split(),['fsck'])
                    self.assertFalse((base/'result').exists())
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('FAILED:',(base/'result').read_text())
                    if fault in ('mounted','uuid'):self.assertFalse(marker.exists())

    @unittest.skipIf(sys.platform=='win32','POSIX shell syntax')
    def test_rendered_scripts_parse_in_shell(self):
        for name,data in self.rendered().items():
            if name.endswith('.sh'):
                result=subprocess.run(['sh','-n'],input=data,capture_output=True)
                self.assertEqual(result.returncode,0,(name,result.stderr))

    def test_staging_only_writes_the_actual_android_slot_and_restores_on_write_failure(self):
        for slot in ('a','b'):
            self.fx.configure('e5',slot)
            context=self.fx.plan();device={**context.record['device'],'userdata_bytes':32*fx.GIB}
            self.fx.adapter.root=Path(self.fx.temp.name)
            self.fx.adapter.adb.call=Mock()
            self.fx.adapter.adb.shell=Mock(return_value='a'*64+'  fsck.f2fs')
            worker=SimpleNamespace(adapter=self.fx.adapter,ctx=context,r=context.record,layout_state=lambda:'after')
            native=bytearray(64 << 20);native[:8]=b'ANDROID!'
            struct.pack_into('<III',native,8,4096,4096,0);struct.pack_into('<I',native,40,4)
            patched=bytearray(native);patched[8192]=1
            def pull(_call,_shell,_cmd,dest,**_):
                with Path(dest).open('wb') as stream:
                    stream.write(native[:4096]);stream.truncate(len(native))
            with patch('backend.android_growth_boot.capability',return_value='a'*64),\
                 patch('backend.android_growth_boot.pull_binary',side_effect=pull),\
                 patch('backend.android_growth_boot.prepare_image',return_value=bytes(patched)),\
                 patch('backend.android_growth_boot.push_chunks'):
                growth.stage(worker,device,self.volume)
            scripts=[c.args[0] for c in self.fx.adapter.adb.shell.call_args_list]
            commit=next(s for s in scripts if 'ANDROID_GROWTH_BOOT_RESTORED' in s)
            self.assertIn('of=/dev/block/by-name/boot_'+slot,commit)
            self.assertNotIn('of=/dev/block/by-name/boot_'+('b' if slot=='a' else 'a'),commit)
            self.assertNotIn('misc',commit)
            self.assertNotIn('userdata',commit)
            self.fx.adapter.adb.call.assert_called_once_with('reboot')
            self.assertTrue(context.record['uninstall_growth_pending'])


if __name__=='__main__':unittest.main()
