"""Encrypted uninstall planning/retry: no device partition writes in these tests."""
import hashlib
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_uninstall as fx
from backend.state import Context, RecoveryRequired
from backend.uninstall import prepare, persist, Uninstaller
from backend.uninstall_growth import inspect_android_growth, android_eligible, PreservationUnavailable


def elf(extra=b''):
    data=bytearray(32);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,183)
    return bytes(data)+extra


class AndroidGrowth(unittest.TestCase):
    def setUp(self):
        self.fx=fx.Journal();self.fx.setUp();self.fx.configure('mu300','a')
        self.device=self.fx.device
        self.device.update(encrypted=True,encryption_state='encrypted',encryption_type='file')
        self.volume={'source':'/dev/block/dm-39','mapped_bytes':28*fx.GIB,
                     'filesystem_bytes':28*fx.GIB,'filesystem':'f2fs','uuid':'55'*16,'mapping_overhead':0}
        self.manager=elf(b'/system/bin/resize.f2fs\0');self.resizer=elf(b'resizer')
        self.slaves=[self.device['storage']['NODE']]
        self.fx.adb.shell=Mock(side_effect=self.shell)
        self.fx.adb.read=Mock(side_effect=self.read)
        self.fx.adb.call=Mock()

    def tearDown(self):self.fx.tearDown()

    def shell(self,command,**_):
        if '/slaves/' in command:return '\n'.join(self.slaves)
        if command.startswith('if [ -x /system/bin/resize.f2fs ]'):return '/system/lib64/libfs_mgr.so\n'
        if command.startswith('rm -f /data/local/tmp/openwrt-uninstall-'):return ''
        raise AssertionError('Unexpected command: '+command)

    def read(self,command):
        if command=='cat /system/lib64/libfs_mgr.so':return self.manager
        if command=='cat /system/bin/resize.f2fs':return self.resizer
        if 'by-name/misc' in command:return bytes(2048)
        raise AssertionError('Unexpected read: '+command)

    def plan(self):
        with patch('backend.uninstall.android_growth_boot.capability',return_value='a'*64),patch('backend.uninstall.android_volume',return_value=self.volume),patch('backend.uninstall.base_boot'),patch('backend.uninstall.build'),patch('backend.uninstall.growth_tools',side_effect=AssertionError('raw encrypted resize')):
            plan,meta,files=prepare(self.fx.adapter,self.fx.store,self.device)
        record=self.fx.store.create(self.fx.package,self.device,{'method':'uninstall'},plan)
        context=Context(self.fx.store,record,lambda *_:None);persist(context,meta,files)
        return context

    def test_fbe_f2fs_preserving_plan_for_both_families_and_android_slots(self):
        for family,slot in (('e5','a'),('e5','b'),('mu300','a'),('mu300','b')):
            self.fx.configure(family,slot);self.device=self.fx.device
            self.device.update(encrypted=True,encryption_state='encrypted',encryption_type='file')
            self.fx.adb.shell=Mock(side_effect=self.shell);self.fx.adb.read=Mock(side_effect=self.read)
            context=self.plan()
            self.assertEqual(context.record['plan']['data_action'],'grow-android-f2fs')
            self.assertFalse(context.record['plan']['wipe'])
            self.assertEqual(context.record['uninstall']['tool_hashes'],{})
            Uninstaller(None,context,self.fx.adapter)
            self.assertFalse(any('of=' in c.args[0] for c in self.fx.adb.shell.call_args_list))

    def test_wrong_or_layered_mapping_is_refused_without_mutation(self):
        for slaves in ([],['mmcblk1p4'],['mmcblk0p4','dm-1']):
            self.slaves=slaves
            with self.subTest(slaves=slaves),self.assertRaises(PreservationUnavailable):self.plan()

    def test_unknown_fde_partial_mapping_and_non_f2fs_still_need_explicit_reset(self):
        for values in ({'encryption_type':'block'},{'encryption_state':'unknown'}):
            saved=dict(self.device);self.device.update(values)
            with self.subTest(values=values),self.assertRaises(PreservationUnavailable):self.plan()
            self.device.clear();self.device.update(saved)
        for values in ({'mapping_overhead':4096},{'mapped_bytes':27*fx.GIB},{'filesystem':'ext4'}):
            saved=dict(self.volume);self.volume.update(values)
            with self.subTest(values=values),self.assertRaises(PreservationUnavailable):self.plan()
            self.volume=saved

    def test_native_resize_capability_is_fresh_binary_evidence(self):
        self.manager=elf(b'no automatic resize entry')
        with self.assertRaises(PreservationUnavailable):self.plan()
        self.manager=elf(b'/system/bin/resize.f2fs\0');self.resizer=b'#!/bin/sh\n'
        with self.assertRaises(PreservationUnavailable):self.plan()

    def test_saved_evidence_cannot_claim_a_foreign_mapper_or_manager(self):
        volume=inspect_android_growth(self.fx.adapter,self.device,self.volume)
        for key,value in (('slave','sda75'),('manager','/data/evil'),('resizer_sha256','invalid')):
            changed={**volume,'android_growth':{**volume['android_growth'],key:value}}
            with self.subTest(key=key),self.assertRaises(ValueError):android_eligible(self.device,changed)

    def test_adb_loss_does_not_offer_erase_data_fallback(self):
        consent=Mock(return_value=True)
        with patch('backend.uninstall.android_volume',return_value=self.volume),\
             patch('backend.uninstall.android_growth_boot.capability',side_effect=ConnectionError('offline')):
            with self.assertRaises(ConnectionError):
                prepare(self.fx.adapter,self.fx.store,self.device,consent)
        consent.assert_not_called()

    def test_final_capacity_and_uuid_must_match_before_boot_cleanup(self):
        context=self.plan();worker=Uninstaller(None,context,self.fx.adapter)
        device={**self.device,'existing':False,'userdata_bytes':32*fx.GIB}
        grown={**self.volume,'mapped_bytes':32*fx.GIB,'filesystem_bytes':32*fx.GIB}
        with patch('backend.uninstall.android_volume',return_value=grown),patch.object(worker,'finish_boot') as finish:
            worker.finish(device)
        self.assertEqual(context.record['status'],'completed');finish.assert_called_once()
        self.assertEqual(context.record['result']['filesystem_bytes'],32*fx.GIB)

    def test_shortfall_stays_resumable_without_format_or_false_success(self):
        context=self.plan();worker=Uninstaller(None,context,self.fx.adapter)
        device={**self.device,'existing':False,'userdata_bytes':32*fx.GIB,'boot_id':'new-android'}
        short={**self.volume,'mapped_bytes':32*fx.GIB}
        with patch('backend.uninstall.android_volume',return_value=short),patch.object(worker,'finish_boot',side_effect=AssertionError('early cleanup')):
            with self.assertRaises(RecoveryRequired):worker.finish(device)
        self.assertTrue(context.record['uninstall_growth_pending'])
        with patch('backend.uninstall.android_volume',return_value=short),patch.object(worker,'layout_state',return_value='after'),patch('backend.uninstall.android_growth_boot.stage') as stage:
            self.assertTrue(worker.rearm_growth(device))
        stage.assert_called_once()
        self.fx.adb.call.assert_not_called()
        self.assertFalse(any('of=' in c.args[0] for c in self.fx.adb.shell.call_args_list))

    def test_changed_filesystem_uuid_cannot_finish_or_restart_growth(self):
        context=self.plan();worker=Uninstaller(None,context,self.fx.adapter)
        device={**self.device,'existing':False,'userdata_bytes':32*fx.GIB}
        foreign={**self.volume,'mapped_bytes':32*fx.GIB,'filesystem_bytes':32*fx.GIB,'uuid':'66'*16}
        with patch('backend.uninstall.android_volume',return_value=foreign),patch.object(worker,'finish_boot',side_effect=AssertionError('foreign cleanup')):
            with self.assertRaisesRegex(RecoveryRequired,'identity'):worker.finish(device)
        self.fx.adb.call.assert_not_called()


if __name__=='__main__':unittest.main()
