"""Exercise the MU300 FIFO client, without opening a modem or sending an SMS."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest

from helpers import BIN


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux FIFO tests')
class ATClientBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mu300-at-client-')
        self.root = Path(self.temp.name)
        os.mkfifo(self.root / 'cmd', 0o600)
        self.env = dict(os.environ, MU300_AT_DIR=str(self.root),
                        MU300_AT_DEV=str(self.root / 'never-a-modem'), MU300_AT_LOCK_WAIT='0')
        self.captured = []
        self.errors = []

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args, limit=5):
        return subprocess.run(['bash', str(BIN / 'mu300-at'), *args], env=self.env,
                              capture_output=True, timeout=limit)

    def exchange(self, command, reply):
        # RDWR avoids blocking test cleanup if validation rejects a request.
        ready = threading.Event()
        def responder():
            fd = os.open(self.root / 'cmd', os.O_RDWR | os.O_NONBLOCK)
            ready.set()
            try:
                request = b''
                end = time.monotonic() + 4
                while b'\n' not in request and time.monotonic() < end:
                    try:
                        request += os.read(fd, 4096)
                    except BlockingIOError:
                        time.sleep(.005)
                if not request:
                    raise AssertionError('No FIFO request')
                self.captured.append(request)
                _, target, _ = request.split(b' ', 2)
                answer = Path(os.fsdecode(target))
                # Same atomic publication contract as the real daemon.
                partial = Path(str(answer) + '.part')
                partial.write_bytes(reply)
                partial.replace(answer)
            except BaseException as error:
                self.errors.append(error)
            finally:
                os.close(fd)
        worker = threading.Thread(target=responder)
        worker.start(); ready.wait(1)
        try:
            result = self.invoke('-t', '2', command)
        finally:
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])
        return result

    def test_terminal_prefix_or_final_before_truncated_tail_is_not_success(self):
        for reply in (b'OKAY\n', b'ERRORish\n', b'+CME ERRORgarbage\n',
                      b'CONNECTING\n', b'OK\n+NEXT: truncated', b'+VALUE: 1\nO', b'OK', b'OK\r'):
            with self.subTest(reply=reply):
                result = self.exchange('AT', reply)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')

    def test_complete_error_remains_a_complete_transport_response(self):
        for reply in (b'OK\n', b'ERROR\n', b'+CME ERROR: 10\n', b'+CMS ERROR: 302\n',
                      b'CONNECT 150000000\n', b'NO CARRIER\n', b'BUSY\n', b'NO ANSWER\n',
                      b'\r\n+VALUE: 1\r\nOK\r\n\r\n'):
            with self.subTest(reply=reply):
                result = self.exchange('AT', reply)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, reply)

    def test_sms_submit_and_compound_operator_query_are_unchanged(self):
        for command in ('AT+CMGS=5\r001122334455\x1a', 'AT+CSCS?;+COPS?'):
            result = self.exchange(command, b'OK\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.captured[-1].split(b' ', 2)[2], command.encode() + b'\n')

    def test_invalid_arguments_do_not_publish_a_request(self):
        fd = os.open(self.root / 'cmd', os.O_RDWR | os.O_NONBLOCK)
        try:
            for args in (['-t'], ['-t', '0', 'AT'], ['-t', 'abc', 'AT'], ['-t','100','AT'],
                         ['AT\nAT+CFUN=0'], ['AT' + 'X'*4096]):
                with self.subTest(args=str(args)[:60]):
                    result = self.invoke(*args, limit=1)
                    self.assertNotEqual(result.returncode, 0)
                    with self.assertRaises(BlockingIOError):
                        os.read(fd, 4096)
        finally:
            os.close(fd)

    def test_missing_daemon_answer_is_bounded_by_wall_time(self):
        started = time.monotonic()
        result = self.invoke('-t', '1', 'AT', limit=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertFalse((self.root / 'lock').exists())
        self.assertIn(b'no answer from the daemon', result.stderr)

    def test_incomplete_daemon_answer_has_a_diagnostic(self):
        result = self.exchange('AT', b'')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'modem returned no final response', result.stderr)

    def test_dead_daemon_lock_with_deadline_is_fully_reclaimed(self):
        child=subprocess.Popen(['sh','-c','exit 0']);child.wait()
        lock=self.root/'lock';lock.mkdir()
        (lock/'pid').write_text(str(child.pid)+'\n')
        (lock/'until').write_text('9999999999\n')
        result=self.exchange('AT',b'OK\n')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(lock.exists())

    def test_zero_lock_wait_never_steals_a_live_owner(self):
        lock = self.root / 'lock'; lock.mkdir()
        (lock / 'pid').write_text(str(os.getpid()))
        result = self.invoke('-t', '1', 'AT', limit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((lock / 'pid').read_text(), str(os.getpid()))

    def test_lock_wait_uses_elapsed_time_and_accepts_leading_zero(self):
        lock = self.root / 'lock'; lock.mkdir()
        (lock / 'pid').write_text(str(os.getpid()))
        self.env['MU300_AT_LOCK_WAIT'] = '001'
        started = time.monotonic()
        result = self.invoke('-t', '1', 'AT', limit=3)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(elapsed, .9)
        self.assertLess(elapsed, 2)
        self.assertEqual((lock / 'pid').read_text(), str(os.getpid()))

    def test_client_is_required_by_both_build_inventories(self):
        top = Path(__file__).resolve().parents[1]
        for name in ('tools/build-openwrt-tf-magisk.sh', 'tools/make-release.sh'):
            self.assertIn('./opt/mu300/bin/mu300-at ', (top / name).read_text())

    def test_termination_exits_and_releases_own_lock(self):
        process = subprocess.Popen(['bash', str(BIN / 'mu300-at'), '-t', '3', 'AT'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            until = time.monotonic() + 2
            while not (self.root / 'lock' / 'pid').exists() and time.monotonic() < until:
                time.sleep(.005)
            self.assertTrue((self.root / 'lock' / 'pid').exists())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=2)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((self.root / 'lock').exists())
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()


if __name__ == '__main__':
    unittest.main()
