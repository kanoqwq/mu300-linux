"""PTY tests of the AT broker u30pro-atd (built with -DATD_TEST) and its client u30pro-atc.
Run under Linux: python3 tests/test_atd.py /path/to/test-atd /path/to/u30pro-atc"""
import json, os, select, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
try:
    import pty
except ImportError:
    pty = None

ATD = os.environ.get('U30_ATD_TEST_BINARY')
ATC = os.environ.get('U30_ATC_BINARY')
if __name__ == '__main__' and len(sys.argv) > 2 and not sys.argv[1].startswith('-'):
    ATD, ATC = sys.argv[1], sys.argv[2]
    del sys.argv[1:3]


class Modem:
    """minimal AT modem on a pty"""
    def __init__(self):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.commands = []
        self.running = True
        self.silent = False
        self.sms_pdu = None
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def say(self, text):
        os.write(self.master, ('\r\n' + text + '\r\n').encode())

    def loop(self):
        buf = b''
        while self.running:
            if not select.select([self.master], [], [], .05)[0]:
                continue
            try:
                b = os.read(self.master, 4096)
            except OSError:
                break
            buf += b
            if self.sms_pdu is not None:
                if b'\x1a' in buf:
                    pdu, _, buf = buf.partition(b'\x1a')
                    self.sms_pdu = pdu.decode()
                    self.say('+CMGS: 7\r\n\r\nOK')
                    self.sms_pdu = pdu.decode()
                    self.sms_done = True
                    continue
                else:
                    continue
            while b'\r' in buf:
                cmd, buf = buf.split(b'\r', 1)
                cmd = cmd.strip().decode(errors='replace')
                if not cmd:
                    continue
                self.commands.append(cmd)
                if self.silent:
                    continue
                if cmd.startswith('AT+CMGS='):
                    self.sms_pdu = ''
                    os.write(self.master, b'\r\n> ')
                    continue
                if cmd == 'ATE0':
                    self.say('OK')
                elif cmd == 'AT':
                    self.say('OK')
                elif cmd == 'AT+CSQ':
                    self.say('+CSQ: 31,13\r\n\r\nOK')
                elif cmd == 'AT+CMEERR':
                    self.say('+CME ERROR: 3')
                else:
                    self.say('ERROR')

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        os.close(self.master)
        os.close(self.slave)


@unittest.skipUnless(ATD and ATC and pty, 'needs the test binaries')
class TestAtd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sock = str(Path(self.tmp.name) / 'atd.sock')
        self.m = Modem()
        self.m.sms_done = False
        self.p = subprocess.Popen([ATD, '-p', self.m.port, '-s', self.sock], stderr=subprocess.PIPE)
        for _ in range(100):
            if os.path.exists(self.sock):
                break
            time.sleep(.05)
        self.assertTrue(os.path.exists(self.sock), 'broker socket')

    def tearDown(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.p.stderr.close()
        self.m.close()
        self.tmp.cleanup()

    def atc(self, cmd, t=None):
        args = [ATC, '-s', self.sock] + (['-t', str(t)] if t else []) + [cmd]
        q = subprocess.run(args, capture_output=True, timeout=30)
        return json.loads(q.stdout) if q.stdout.strip().startswith(b'{') else {'raw': q.stdout, 'rc': q.returncode}

    def test_basic_command_and_lines(self):
        r = self.atc('AT+CSQ')
        self.assertTrue(r['ok'])
        self.assertEqual(r['status'], 'OK')
        self.assertEqual(r['lines'], ['+CSQ: 31,13'])

    def test_error_status_is_reported(self):
        r = self.atc('AT+CMEERR')
        self.assertFalse(r['ok'])
        self.assertEqual(r['status'], 'CME ERROR: 3')

    def test_every_command_is_sent_as_typed(self):
        # no filter: identity reads, IMEI changes, the commands known to hang the modem and text that is no AT command all reach it
        cmds = ['AT+CIMI', 'AT+CGSN', 'AT+SPIMEI?', 'AT+EGMR=1,7,"860000000000000"', 'at+spimei="860000000000000"',
                'AT^SYSCONFIG?', 'AT+SPREF="AUTODLOADER"', 'AT+SPLOGLEVEL=1,5', 'AT+SFUN=4', 'AT+CSQ;+CIMI', 'rm -rf /']
        for cmd in cmds:
            r = self.atc(cmd)
            self.assertNotEqual(r.get('status'), 'invalid', cmd)
        self.assertEqual([c for c in self.m.commands if c not in ('ATE0', 'AT')], cmds)

    def test_empty_request_sends_nothing(self):
        r = self.atc('')
        self.assertEqual(r['status'], 'invalid')
        self.assertEqual([c for c in self.m.commands if c not in ('ATE0', 'AT')], [])

    def test_timeout(self):
        self.m.silent = True
        r = self.atc('AT+CSQ', t=700)
        self.assertEqual(r['status'], 'timeout')
        self.assertFalse(r['ok'])

    def test_urc_ring_collects_unsolicited_lines(self):
        self.m.say('+CEREG: 1,"9D6E","01877901",13')
        time.sleep(.4)
        q = subprocess.run([ATC, '-s', self.sock, '!urc'], capture_output=True, timeout=5)
        urc = json.loads(q.stdout)
        self.assertTrue(any('+CEREG' in x['line'] for x in urc['urc']))

    def test_status_reports_counters_and_wedge(self):
        self.atc('AT+CSQ')
        q = subprocess.run([ATC, '-s', self.sock, '!status'], capture_output=True, timeout=5)
        st = json.loads(q.stdout)
        self.assertFalse(st['wedged'])
        self.assertGreaterEqual(st['served'], 1)
        self.m.silent = True
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            q = subprocess.run([ATC, '-s', self.sock, '!status'], capture_output=True, timeout=5)
            if json.loads(q.stdout)['wedged']:
                break
            time.sleep(.3)
        self.assertTrue(json.loads(q.stdout)['wedged'], 'three missed idle pings mark the modem wedged')

    def test_sms_send_waits_for_prompt_and_ends_with_ctrl_z(self):
        pdu = '0001000B915121551532F40000' + '04D4F29C0E'
        q = subprocess.run([ATC, '-s', self.sock, '!sms T20000 %d %s' % (len(pdu) // 2 - 1, pdu)], capture_output=True, timeout=30)
        r = json.loads(q.stdout)
        self.assertTrue(r['ok'], r)
        self.assertIn('+CMGS: 7', r['lines'])
        self.assertEqual(self.m.sms_pdu, pdu)
        self.assertIn('AT+CMGS=%d' % (len(pdu) // 2 - 1), self.m.commands)

    def test_sms_request_validation(self):
        for bad in ['!sms T20000 5 ZZZZ', '!sms T20000 999 00010000', '!sms T20000 x 0001000B915121551532F4', '!sms']:
            q = subprocess.run([ATC, '-s', self.sock, bad], capture_output=True, timeout=5)
            self.assertIn(b'"ok":false', q.stdout, bad)
        self.assertFalse(any(c.startswith('AT+CMGS') for c in self.m.commands))


if __name__ == '__main__':
    unittest.main(verbosity=2)
