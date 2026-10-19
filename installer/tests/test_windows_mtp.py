"""Device-tree matching and privilege failures; never disable real hardware."""
import base64
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import windows_mtp as mtp
from backend.transport import Adb

ROOT = r'USB\VID_19D2&PID_1405\MU123'
MTP = r'USB\VID_19D2&PID_1405&MI_00\7&123&0&0000'
ADB = r'USB\VID_19D2&PID_1405&MI_01\7&123&0&0001'


def tree(problem=0):
    return [dict(id=ROOT, parent='USBROOT', class_guid='USB'),
            dict(id=MTP, parent=ROOT, class_guid=mtp.WPD, problem=problem),
            dict(id=ADB, parent=ROOT, class_guid='Android', problem=0),
            dict(id=r'USB\VID_19D2&PID_1405&MI_02\7&123&0&0002', parent=ROOT, class_guid='Net'),
            dict(id=r'USB\VID_19D2&PID_1405&MI_03\7&123&0&0003', parent=ROOT, class_guid='Ports'),
            dict(id=r'USB\VID_1234&PID_1234\PHONE', parent='USBROOT'),
            dict(id=r'USB\VID_1234&PID_1234&MI_00\OTHER', parent=r'USB\VID_1234&PID_1234\PHONE', class_guid=mtp.WPD)]


class WindowsMtpTest(unittest.TestCase):
    def setUp(self):
        mtp._inventory = (0, [])
        mtp._announced.clear()
        mtp._failed.clear()
        self.device = dict(serial='MU123', label='V50', wireless=False)

    def test_only_mtp_sibling_of_exact_serial_is_selected(self):
        self.assertEqual([n['id'] for n in mtp.matching_mtp(tree(), 'mu123')], [MTP])
        self.assertEqual(mtp.matching_mtp(tree(), 'MU'), [])
        self.assertEqual(mtp.matching_mtp(tree(), 'OTHER'), [])
        duplicate = tree() + [dict(id=r'USB\VID_1782&PID_1234\MU123')]
        self.assertEqual(mtp.matching_mtp(duplicate, 'MU123'), [])

    def test_guid_braces_and_mtp_compatible_id_are_supported(self):
        rows = tree()
        rows[1]['class_guid'] = mtp.WPD.strip('{}').lower()
        self.assertEqual(len(mtp.matching_mtp(rows, 'MU123')), 1)
        rows[1].update(class_guid='Unknown', compatible=['USB\\MS_COMP_MTP'])
        self.assertEqual(len(mtp.matching_mtp(rows, 'MU123')), 1)
        rows[1]['compatible'] = ['USB\\Class_FF&SubClass_42&Prot_01']
        self.assertEqual(mtp.matching_mtp(rows, 'MU123'), [])

    def test_non_windows_wireless_and_other_models_never_touch_pnp(self):
        for platform, device in [('darwin', self.device), ('linux', self.device),
                                 ('win32', dict(self.device, wireless=True)),
                                 ('win32', dict(self.device, serial='192.0.2.1:5555')),
                                 ('win32', dict(self.device, serial='adb-MU123._adb-tls-connect._tcp')),
                                 ('win32', dict(self.device, label='E5')),
                                 ('win32', dict(self.device, label='Pixel_9'))]:
            with self.subTest(platform=platform, device=device), patch.object(mtp.sys, 'platform', platform), patch.object(mtp, 'powershell') as run:
                mtp.prepare_devices([device])
                run.assert_not_called()

    def test_disable_is_verified_and_toast_emitted_once(self):
        notice = Mock()
        with patch.object(mtp.sys, 'platform', 'win32'), patch.object(mtp, 'powershell', side_effect=[json.dumps(tree()), '', json.dumps(tree(22))]) as run:
            mtp.prepare_devices([self.device], notice)
            mtp.prepare_devices([self.device], notice)
        self.assertEqual(run.call_count, 3)
        notice.assert_called_once_with(dict(code='mtp_disabled', label='V50'))
        self.assertNotIn('Enable-PnpDevice', run.call_args_list[1].args[0])

    def test_already_disabled_does_not_request_privileges_or_enable(self):
        with patch.object(mtp.sys, 'platform', 'win32'), patch.object(mtp, 'powershell', return_value=json.dumps(tree(22))) as run:
            notice = Mock()
            mtp.prepare_devices([self.device], notice)
            self.assertEqual(run.call_count, 1)
            notice.assert_called_once_with(dict(code='mtp_disabled', label='V50'))

    def test_access_denied_reports_warning_and_does_not_retry_uac(self):
        notice = Mock()
        with patch.object(mtp.sys, 'platform', 'win32'), patch.object(mtp, 'powershell', side_effect=[json.dumps(tree()), RuntimeError('denied')]) as run:
            mtp.prepare_devices([self.device], notice)
            mtp.prepare_devices([self.device], notice)
        self.assertEqual(run.call_count, 2)
        notice.assert_called_once_with(dict(code='mtp_disable_failed', label='V50'))

    def test_unconfirmed_disable_cannot_be_reported_as_success(self):
        notice = Mock()
        with patch.object(mtp.sys, 'platform', 'win32'), patch.object(mtp, 'powershell', side_effect=[json.dumps(tree()), '', json.dumps(tree())]):
            mtp.prepare_devices([self.device], notice)
        self.assertEqual(notice.call_args.args[0]['code'], 'mtp_disable_failed')

    def test_privileged_script_contains_only_encoded_data_and_rechecks_target(self):
        value = "MU123'; Stop-Computer; #"
        script = mtp.disable_script(value, MTP)
        self.assertNotIn(value, script)
        encoded = re.search(r"-EncodedCommand '([A-Za-z0-9+/=]+)'", script)[1]
        child = base64.b64decode(encoded).decode('utf-16le')
        self.assertIn('DEVPKEY_Device_Parent', child)
        self.assertIn('DEVPKEY_Device_ClassGuid', child)
        self.assertIn('Disable-PnpDevice -InstanceId $node.InstanceId -Confirm:$false', child)
        self.assertNotIn('Enable-PnpDevice', child)
        data = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", child)[1]
        self.assertEqual(json.loads(base64.b64decode(data)), [value, MTP])

    def test_unbound_listing_and_selected_wireless_cannot_disable_another_usb_device(self):
        adb = Adb.__new__(Adb)
        adb.exe, adb.serial, adb.context = 'adb', None, None
        adb.mtp_workaround = True
        result = 'List of devices attached\nMU123 device model:V50\n192.0.2.1:5555 device model:V50\n'
        with patch.object(adb, 'call', return_value=result), patch('backend.transport.prepare_devices') as prepare:
            adb.devices()
            self.assertEqual(prepare.call_args.args[0], [])
            adb.serial = '192.0.2.1:5555'
            adb.devices()
            self.assertEqual([d['serial'] for d in prepare.call_args.args[0]], ['192.0.2.1:5555'])
            self.assertTrue(prepare.call_args.args[0][0]['wireless'])
            adb.serial = 'MU123'
            adb.devices()
            self.assertEqual([d['serial'] for d in prepare.call_args.args[0]], ['MU123'])

    def test_e5_transport_is_unchanged_even_with_a_mu300_serial(self):
        adb = Adb.__new__(Adb)
        adb.exe, adb.serial, adb.context = 'adb', 'MU123', None
        with patch.object(adb, 'call', return_value='List of devices attached\nMU123 device model:V50\n'), patch('backend.transport.prepare_devices') as prepare:
            adb.devices()
            prepare.assert_not_called()

    def test_pnp_failure_preserves_adb_enumeration_and_further_commands(self):
        adb = Adb.__new__(Adb)
        adb.exe, adb.serial, adb.context = 'adb', 'MU123', None
        adb.mtp_workaround, adb.notice = True, Mock()
        listing = 'List of devices attached\nMU123 device model:V50\n'
        with patch.object(adb, 'call', side_effect=[listing, '1']), patch.object(mtp.sys, 'platform', 'win32'), patch.object(mtp, 'powershell', side_effect=RuntimeError('denied')):
            self.assertEqual(adb.devices()[0]['state'], 'device')
            self.assertEqual(adb.shell('getprop sys.boot_completed'), '1')
        adb.notice.assert_called_once_with(dict(code='mtp_disable_failed', label='V50'))


if __name__ == '__main__':
    unittest.main()
