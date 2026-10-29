"""Explicit ADB reboot after installation; no device writes or real transports."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.service import Service
from backend.state import Context,Store


class DeviceReboot(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        store=Store(temporary.name);self.service=Service('.',store,io.StringIO())
        self.device=dict(serial='chosen',cid='a'*32,profile='f50',slot='_b',system='Android',readiness='ready')
        self.record=store.create({'adapter':'mu300'},dict(self.device),{'method':'part'}, {})
        self.ctx=Context(store,self.record,self.service.emit)
        self.service.active=self.ctx;self.service.worker=Mock()
        self.service.worker.is_alive.return_value=True
        self.service.openwrt_wait_stage(self.ctx)
        self.adapter=Mock();self.adapter.probe.return_value=dict(self.device)

    def request(self, transaction=None):
        return self.service.dispatch({'protocol':1,'command':'reboot_device',
            'params':{'transaction':transaction or self.record['id']}})

    def test_queue_uses_device_owner_and_repeated_clicks_send_only_one_reboot(self):
        self.assertTrue(self.request()['accepted']);self.request()
        self.adapter.adb.call.assert_not_called()
        self.service.process_device_reboot(self.ctx,self.adapter)
        self.service.process_device_reboot(self.ctx,self.adapter)
        self.adapter.probe.assert_called_once_with('chosen','boot')
        self.adapter.adb.call.assert_called_once_with('reboot',timeout=15)
        self.assertFalse(self.record['details']['reboot_manually'])
        self.assertTrue(self.record['adb_reboot_requested'])
        with self.assertRaises(ValueError):self.request()

    def test_different_device_or_unavailable_adb_does_not_reboot(self):
        for change in ({'serial':'other'},{'cid':'b'*32},{'profile':'v50'},{'slot':'_a'},
                       {'readiness':'android_boot'},{'system':'OpenWrt'}):
            with self.subTest(change=change):
                self.adapter.probe.return_value={**self.device,**change}
                self.request();self.service.process_device_reboot(self.ctx,self.adapter)
                self.adapter.adb.call.assert_not_called()
                self.assertTrue(self.record['details']['reboot_manually'])
                self.assertIn('原 Android',self.record['details']['verification_error'])

    def test_writes_completion_confirmation_and_other_transactions_are_rejected(self):
        original=dict(self.record)
        for change in ({'unsafe':True},{'stage':'transfer_install'},{'status':'running'},
                       {'openwrt_confirmed':True},{'cancel_requested':True},
                       {'choices':{'method':'update'}},{'choices':{'method':'uninstall'}}):
            with self.subTest(change=change):
                self.record.clear();self.record.update(original);self.record.update(change)
                with self.assertRaises(ValueError):self.request()
        self.record.clear();self.record.update(original)
        with self.assertRaises(ValueError):self.request('another-transaction')
        self.adapter.probe.assert_not_called();self.adapter.adb.call.assert_not_called()

    def test_boundary_is_rechecked_before_processing(self):
        self.request();self.record['unsafe']=True
        self.service.process_device_reboot(self.ctx,self.adapter)
        self.adapter.probe.assert_not_called();self.adapter.adb.call.assert_not_called()

    def test_disconnect_is_expected_and_not_automatically_retried(self):
        self.adapter.adb.call.side_effect=ConnectionError('ADB: device offline')
        self.request();self.service.process_device_reboot(self.ctx,self.adapter)
        self.assertTrue(self.record['adb_reboot_requested'])
        self.assertFalse(self.record['details'].get('verification_error'))
        self.assertFalse(self.record['details']['reboot_manually'])
        self.service.process_device_reboot(self.ctx,self.adapter)
        self.adapter.adb.call.assert_called_once()

    def test_rejected_command_restores_the_control(self):
        self.adapter.adb.call.side_effect=ConnectionError('ADB: permission denied')
        self.request();self.service.process_device_reboot(self.ctx,self.adapter)
        self.assertFalse(self.record['adb_reboot_requested'])
        self.assertTrue(self.record['details']['reboot_manually'])
        self.assertIn('permission denied',self.record['details']['verification_error'])
        self.assertTrue(self.request()['accepted'])


if __name__=='__main__':unittest.main()
