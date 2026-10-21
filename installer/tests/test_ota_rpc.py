"""rpcd HTTP session metadata and post-commit plugin registration regressions."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.name == 'posix', 'rpcd uses Linux locks')
class RPCTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'
        sys.path.insert(0, str(directory)); self.addCleanup(lambda: sys.path.remove(str(directory)))
        spec = importlib.util.spec_from_file_location('ota_rpc_regression', directory/'rpc.py')
        self.rpc = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.rpc)

    def call(self, method, arguments):
        with patch.object(sys, 'argv', ['rpc.py','call',method]), patch.object(sys, 'stdin', io.StringIO(json.dumps(arguments))), redirect_stdout(io.StringIO()):
            self.rpc.main()

    def test_authenticated_metadata_is_accepted_for_every_method(self):
        for method in ('status','inspect','start','rollback_check','rollback','reboot'):
            args = {'ubus_rpc_session': 'a'*32}
            if method == 'start': args['id'] = 'b'*64
            with patch.object(self.rpc, 'dispatch', return_value={}) as dispatch:
                self.call(method, args)
                dispatch.assert_called_once_with(method, None, 'b'*64 if method == 'start' else None)

    def test_other_arguments_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected RPC'):
            self.call('status', {'ubus_rpc_session':'a'*32,'command':'not-allowed'})
        with self.assertRaisesRegex(ValueError, 'must be an object'):
            self.call('status', ['not-an-object'])

    def test_reload_uses_stock_service_and_health_checks_before_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); service=root/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch()
            self.rpc.ROOT = d
            result = Mock(returncode=0, stdout=json.dumps({'identity':{'version':'test'}}), stderr='')
            with patch.dict(os.environ, {'UNISOC_OTA_RAM':'/'}), patch.object(self.rpc,'mounted',return_value=True), patch.object(self.rpc.subprocess,'run',return_value=result) as run:
                self.assertEqual(self.rpc.activate_rpc(), {'status':'ready'})
                self.assertEqual(run.call_args_list[0].args[0], ['/ota/busybox','chroot',d,'/etc/init.d/rpcd','reload'])
                self.assertEqual(run.call_args_list[1].args[0][-1], 'ubus call unisoc-ota status')

    def test_ram_activation_binds_host_service_runtime_before_reload(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); service=root/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch(); self.rpc.ROOT=d
            missing=Mock(returncode=1); ok=Mock(returncode=0,stdout='{"identity":{}}',stderr='')
            with patch.dict(os.environ, {'UNISOC_OTA_RAM':'/'}), patch.object(self.rpc,'mounted',return_value=False), patch.object(self.rpc.subprocess,'run',side_effect=[ok,ok,ok,ok]) as run:
                self.assertEqual(self.rpc.activate_rpc(), {'status':'ready'})
                self.assertEqual(run.call_args_list[0].args[0], ['/ota/busybox','mount','--bind','/proc/1/root/tmp',d+'/tmp'])
                self.assertEqual(run.call_args_list[1].args[0], ['/ota/busybox','mount','--bind','/proc/1/root/proc',d+'/proc'])
                self.assertEqual(run.call_args_list[2].args[0][-2:], ['/etc/init.d/rpcd','reload'])

    def test_mount_table_decodes_paths_and_matches_exact_bind_mount(self):
        with tempfile.TemporaryDirectory() as d:
            table = Path(d)/'mountinfo'
            table.write_text('31 20 0:1 / /target rw - ext4 /dev/root rw\n'
                             '32 31 0:2 / /target/tmp rw - tmpfs tmpfs rw\n'
                             r'33 31 0:2 / /target/with\040space rw - tmpfs tmpfs rw'+'\n')
            self.assertTrue(self.rpc.mounted('/target/tmp', table))
            self.assertTrue(self.rpc.mounted('/target/with space', table))
            self.assertFalse(self.rpc.mounted('/target/tm', table))
            self.assertFalse(self.rpc.mounted('/target/tmp/child', table))

    def test_reload_failure_is_warning_not_filesystem_failure(self):
        with tempfile.TemporaryDirectory() as d:
            service=Path(d)/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch(); self.rpc.ROOT=d
            with patch.object(self.rpc.subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='reload failed')):
                self.assertEqual(self.rpc.activate_rpc()['status'], 'warning')

    def test_worker_publishes_complete_after_activation(self):
        events=[]
        result=Mock(returncode=0,stdout='{"status":"complete"}',stderr='')
        with patch.object(sys,'argv',['rpc.py','_worker','apply','']), patch.object(self.rpc.subprocess,'run',return_value=result), patch.object(self.rpc,'activate_rpc',side_effect=lambda:(events.append('reload') or {'status':'ready'})), patch.object(self.rpc,'atomic_json',side_effect=lambda path,data:events.append(data)):
            self.rpc.main()
        self.assertEqual(events[0], 'reload')
        self.assertEqual(events[1]['status'], 'complete')
        self.assertEqual(events[1]['result']['activation'], {'status':'ready'})

    def test_failed_worker_does_not_activate_partial_files(self):
        result=Mock(returncode=1,stdout='{"error":"write failed"}',stderr='')
        with patch.object(sys,'argv',['rpc.py','_worker','apply','']), patch.object(self.rpc.subprocess,'run',return_value=result), patch.object(self.rpc,'activate_rpc') as activate, patch.object(self.rpc,'atomic_json') as publish:
            self.rpc.main()
        activate.assert_not_called()
        self.assertEqual(publish.call_args.args[1]['status'], 'failed')

    def test_completion_waits_for_worker_lock_before_reboot_is_offered(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d); self.rpc.JOB=Path(d)/'job.json'
            self.rpc.JOB.write_text('{"status":"complete"}')
            with patch.object(self.rpc,'Engine') as engine:
                engine.return_value.status.return_value={'identity':{'version':'test'}}
                with (Path(d)/'job.lock').open('a') as held:
                    fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.assertEqual(self.rpc.dispatch('status')['job']['status'], 'running')
                self.assertEqual(self.rpc.dispatch('status')['job']['status'], 'complete')

    def test_reboot_ack_window_retains_job_lock_until_child_exits(self):
        import fcntl
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d)
            launch=subprocess.Popen
            children=[]
            def harmless(command, **kwargs):
                command=list(command); command[-1]='/bin/true'
                child=launch(command,**kwargs);children.append(child);return child
            try:
                with patch.dict(os.environ,{'UNISOC_OTA_RAM':''}), patch.object(self.rpc.subprocess,'Popen',side_effect=harmless):
                    self.assertEqual(self.rpc.dispatch('reboot')['status'],'rebooting')
                with (Path(d)/'job.lock').open('a') as held:
                    with self.assertRaises(BlockingIOError): fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    children[0].wait(timeout=10)
                    self.assertEqual(children[0].returncode,0)
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                for child in children:
                    if child.poll() is None: child.terminate();child.wait()
