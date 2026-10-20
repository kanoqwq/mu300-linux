"""Exercise the SSH desktop flow against a reduced shell with no stat/Python."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import ota_desktop as desktop
from test_ota import file, manifest, package


class Channel:
    def __init__(self, shell):
        self.shell = shell
        self.content = bytearray()

    def exec_command(self, command):
        self.destination = shlex.split(command.split('> ', 1)[1])[0]

    def sendall(self, data):
        self.content.extend(data)

    def shutdown_write(self):
        self.shell.files[self.destination] = bytes(self.content)
        self.shell.uploads += 1

    def recv_exit_status(self):
        return 0


class ReducedShell:
    """Transport fixture intentionally refuses old-image dependencies."""
    def __init__(self):
        self.files = {}
        self.directories = set()
        self.commands = []
        self.uploads = 0
        self.key = b'device-one'
        self.ready = True
        self.inspect_error = False
        self.corrupt_checksum = False
        self.reloads = self.reboots = 0
        self.status_result = {'job': {'status': 'complete', 'result': {'activation': {'status': 'ready'}}},
                              'transaction': {'id': 'test-patch', 'status': 'complete'}, 'identity': {'version': 'e5-r2'}}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get_transport(self): return self
    def get_remote_server_key(self): return self
    def asbytes(self): return self.key
    def open_session(self, **kwargs): return Channel(self)

    def execute(self, client, command, **kwargs):
        self.commands.append(command)
        if re.search(r'\bstat\b', command):
            raise RuntimeError('ash: stat: not found')
        if command.startswith('python3 '):
            raise RuntimeError('ash: python3: not found')
        if command.startswith('df '): return '99999999\n'
        if command == '/etc/init.d/rpcd reload': self.reloads += 1; return ''
        if command == 'ubus call unisoc-ota status': return json.dumps(self.status_result)
        if command.startswith('sha256sum '):
            name = shlex.split(command)[1]
            h = '0'*64 if self.corrupt_checksum else hashlib.sha256(self.files[name]).hexdigest()
            return h+'  '+name+'\n'
        if command.startswith('mv '):
            _, old, new = shlex.split(command)
            self.files[new] = self.files.pop(old)
        if command.startswith('umask 077; mkdir /tmp/'):
            name = shlex.split(command)[-1]
            if name in self.directories: raise RuntimeError('mkdir: File exists')
            self.directories.add(name)
        if command.startswith('if test -x '): return 'ready\n' if self.ready else ''
        if '/bootstrap.sh ' in command:
            mode = shlex.split(command)[-2]
            if mode == 'inspect':
                if self.inspect_error: raise RuntimeError('Device/base mismatch: version')
                return json.dumps({'applicable': True, 'id': 'test-patch'})
            if mode == 'start': return json.dumps({'status': 'running'})
            self.ready = True
            return json.dumps(self.status_result)
        if ' /ota/rpc.py status' in command:
            return json.dumps(self.status_result)
        if ' /ota/rpc.py reboot' in command:
            self.reboots += 1
            return json.dumps({'status': 'rebooting'})
        return ''


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root/'update.ota'
        m = manifest()
        contents = (b'new', b'python-runtime', b'static-busybox', b'bootstrap-shell')
        m['runtime'] = {'usr/bin/python3': file(contents[1], 0o755)}
        m['busybox'] = {k:v for k,v in file(contents[2]).items() if k in ('sha256', 'size')}
        m['bootstrap'] = {k:v for k,v in file(contents[3]).items() if k in ('sha256', 'size')}
        package(self.archive, m, {hashlib.sha256(b).hexdigest(): b for b in contents})
        self.shell = ReducedShell()
        for name, value in (('connect', lambda *args: self.shell), ('execute', self.shell.execute)):
            mock = patch.object(desktop, name, value)
            mock.start(); self.addCleanup(mock.stop)
        self.app = desktop.Desktop(self.root, lambda *_: None)
        self.params = {'host': '192.168.9.1', 'password': 'test'}

    def status(self):
        result = self.app.dispatch('ota_status', self.params)
        self.assertEqual(result['job']['status'], 'complete')
        return result

    def test_probe_without_stat_or_installed_python(self):
        result = self.app.dispatch('ota_probe', self.params)
        self.assertTrue(result['check']['applicable'])
        self.assertRegex(result['runtime'], r'^/tmp/unisoc-ota-[a-f0-9]{64}$')
        self.assertEqual(self.app.runtime_session['runtime'], result['runtime'])

    def test_status_before_probe_without_python_and_after_completed_update(self):
        self.shell.inspect_error = True
        self.status()
        self.assertFalse(any('/bootstrap.sh ' in c and ' inspect ' in c for c in self.shell.commands))

    def test_status_after_probe_reuses_prepared_runtime_without_upload(self):
        self.app.dispatch('ota_probe', self.params)
        uploads = self.shell.uploads
        self.status()
        self.assertEqual(self.shell.uploads, uploads)
        self.assertIn('/ota/rpc.py status', self.shell.commands[-1])

    def test_status_after_apply_uses_same_runtime(self):
        result = self.app.dispatch('ota_probe', self.params)
        self.app.dispatch('ota_apply', dict(self.params, token=result['token']))
        uploads = self.shell.uploads
        self.status()
        self.assertEqual(self.shell.uploads, uploads)

    def test_failed_probe_does_not_force_status_through_missing_python(self):
        self.shell.inspect_error = True
        with self.assertRaisesRegex(RuntimeError, 'Device/base'):
            self.app.dispatch('ota_probe', self.params)
        self.assertIsNone(self.app.review)
        self.status()

    def test_reboot_rebuilds_runtime_and_can_read_completed_transaction(self):
        self.app.dispatch('ota_probe', self.params)
        old = self.app.runtime_session['runtime']
        self.shell.ready = False
        self.shell.inspect_error = True
        self.status()
        self.assertNotEqual(self.app.runtime_session['runtime'], old)

    def test_reopened_app_can_read_status_without_inspecting_old_base(self):
        self.app.dispatch('ota_probe', self.params)
        self.app = desktop.Desktop(self.root, lambda *_: None)
        self.shell.inspect_error = True
        self.status()

    def test_selected_external_patch_is_used_when_status_button_sends_no_path(self):
        external = self.root/'selected.ota'
        self.archive.rename(external)
        self.app.dispatch('ota_package', {'path': str(external)})
        self.shell.inspect_error = True
        self.status()

    def test_failed_probe_remembers_external_patch_for_status_retry(self):
        external = self.root/'selected.ota'
        self.archive.rename(external)
        self.shell.inspect_error = True
        with self.assertRaisesRegex(RuntimeError, 'Device/base'):
            self.app.dispatch('ota_probe', dict(self.params, path=str(external)))
        self.status()

    def test_old_device_status_without_selected_patch_explains_required_input(self):
        self.archive.unlink()
        with self.assertRaisesRegex(ValueError, 'Select the OTA patch'):
            self.status()

    def test_changed_device_key_does_not_reuse_previous_runtime(self):
        self.app.dispatch('ota_probe', self.params)
        old = self.app.runtime_session['runtime']
        self.shell.key = b'device-two'
        self.status()
        self.assertNotEqual(self.app.runtime_session['runtime'], old)

    def test_changed_address_does_not_reuse_previous_runtime(self):
        self.app.dispatch('ota_probe', self.params)
        old = self.app.runtime_session['runtime']
        self.params['host'] = '192.168.77.1'
        self.status()
        self.assertNotEqual(self.app.runtime_session['runtime'], old)

    def test_existing_runtime_directory_is_never_reused(self):
        with patch.object(desktop.secrets, 'token_hex', return_value='a'*64):
            self.app.dispatch('ota_probe', self.params)
            with self.assertRaisesRegex(RuntimeError, 'File exists'):
                self.app.dispatch('ota_probe', self.params)
        self.assertIsNone(self.app.review)

    def test_upload_checksum_failure_stops_before_running_helpers(self):
        self.shell.corrupt_checksum = True
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.app.dispatch('ota_probe', self.params)
        self.assertFalse(any('/bootstrap.sh ' in c for c in self.shell.commands))

    def test_legacy_completion_registers_rpc_only_once(self):
        self.shell.status_result['job']['result'] = {}
        self.assertEqual(self.status()['activation']['status'], 'ready')
        self.assertEqual(self.status()['activation']['status'], 'ready')
        self.assertEqual(self.shell.reloads, 1)

    def test_modern_worker_already_registered_rpc(self):
        self.status()
        self.assertEqual(self.shell.reloads, 0)

    def test_reboot_requires_checking_same_device(self):
        with self.assertRaisesRegex(ValueError, 'Check this device'):
            self.app.dispatch('ota_reboot', self.params)
        self.app.dispatch('ota_probe', self.params)
        self.shell.key = b'changed-device'
        with self.assertRaisesRegex(ValueError, 'Check this device'):
            self.app.dispatch('ota_reboot', self.params)
        self.assertEqual(self.shell.reboots, 0)

    def test_manual_reboot_after_completed_update(self):
        self.status()
        self.assertEqual(self.app.dispatch('ota_reboot', self.params)['status'], 'rebooting')
        self.assertEqual(self.shell.reboots, 1)

    def test_running_job_or_pending_recovery_blocks_reboot(self):
        self.app.dispatch('ota_probe', self.params)
        for job, transaction in (('running','complete'), ('complete','applying'), ('complete','rolling_back')):
            self.shell.status_result['job']['status'] = job
            self.shell.status_result['transaction']['status'] = transaction
            with self.assertRaisesRegex(ValueError, 'still updating'):
                self.app.dispatch('ota_reboot', self.params)
        self.assertEqual(self.shell.reboots, 0)

    def test_auto_reboot_is_once_and_only_after_commit(self):
        review = self.app.dispatch('ota_probe', self.params)
        self.app.dispatch('ota_apply', dict(self.params, token=review['token'], auto_reboot=True))
        self.shell.status_result['job']['status'] = 'running'
        self.app.dispatch('ota_status', self.params)
        self.assertEqual(self.shell.reboots, 0)
        self.shell.status_result['job']['status'] = 'complete'
        self.status(); self.status()
        self.assertEqual(self.shell.reboots, 1)

    def test_auto_reboot_disabled_stays_off(self):
        review = self.app.dispatch('ota_probe', self.params)
        self.app.dispatch('ota_apply', dict(self.params, token=review['token'], auto_reboot=False))
        self.status()
        self.assertEqual(self.shell.reboots, 0)

    def test_unrelated_completed_transaction_never_triggers_auto_reboot(self):
        review = self.app.dispatch('ota_probe', self.params)
        self.app.dispatch('ota_apply', dict(self.params, token=review['token'], auto_reboot=True))
        self.shell.status_result['transaction']['id'] = 'different-patch'
        self.status()
        self.assertEqual(self.shell.reboots, 0)
