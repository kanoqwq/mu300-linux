"""mu300-modemd (FINDINGS 206, docs/MODEMD.md): the modem daemon in ucode, against a stand-in for mu300-atd-io.

The stand-in speaks the bridge's side of the protocol (HELLO, STATE, RX, REQ...) on the unix socket and plays a modem:
each TX is answered with the lines a test gave for that command. What is checked is what mu300-atd did, kept: one
command at a time, the drain before it (and its log), the collect after it, the answer file (whole, owner only), the
refusals, the URC logs, +CMT to the spooler, +ECIND to the native passive SIM cache, the reopen rule; and what is new: the ubus
object mu300.modem (at, status, cell), the URC events, and the panel's collection inside the daemon, which must write
the same sig.json and cell.json as the collector program for the same modem answers.

Needs a ucode with the uloop, socket and ubus modules (MU300_TEST_UCODE, e.g. /root/ucbuild/full/bin/ucode-run) and,
for the ubus tests, ubusd and the ubus CLI (MU300_TEST_UBUSD, MU300_TEST_UBUS)."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import TOP, ucode, ucode_paths

MODEMD = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-modemd'
CELL = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'cell'
UBUSD = os.environ.get('MU300_TEST_UBUSD', '/root/ucbuild/full/bin/ubusd-run')
UBUS = os.environ.get('MU300_TEST_UBUS', '/root/ucbuild/full/bin/ubus-run')


def has_modules(u):
    code = "import * as a from 'uloop'; import * as b from 'socket'; import * as c from 'ubus'; print('ok');"
    try:
        return subprocess.run(u + ['-e', code], capture_output=True, text=True, timeout=10).stdout == 'ok'
    except (OSError, subprocess.TimeoutExpired):
        return False


def wait_for(pred, timeout=8.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


# The modem: a command (without its CR) -> the lines it answers. A list of lines, or a function of the command.
DEFAULT_MODEM = {
    'AT+CSQ': ['+CSQ: 20,99', 'OK'],
    'AT+CFUN?': ['+CFUN: 1', 'OK'],
    'AT+CEREG?': ['+CEREG: 2,1,"1A2B","01234567",7', 'OK'],
    'AT+C5GREG?': ['+C5GREG: 2,1,"1A2B","0123456789",11', 'OK'],
    'AT+CESQ': ['+CESQ: 99,99,255,255,20,50', 'OK'],
    'AT+COPS?': ['+COPS: 0,2,"46001",7', 'OK'],
    'AT+SPENGMD=0,6,0': ['1-300-211--11747--654-0-3-0-0-0-123456-7-0-0-0-0-0-0-0-0-0-0-0-0-0-0-0-6699', 'OK'],
    'AT+SPENGMD=0,0,6': ['0-0-1500-0,0,3,9-0,0,0,2-0-0', 'OK'],
    'AT+SPENGMD=0,14,1': ['41-504990-747--8792--581-0-0-100-12642267-243118171-0-0-0-0-0-1617-20-3-0-10', 'OK'],
    'AT+SPENGMD=0,6,6': ['1650-211--10000--900-0-0-0-0-0-0-0-0', 'OK'],
    'AT+SPQ5GNCELLEX': ['+SPQ5GNCELLEX: 504990,747,-8792,-581,1617,0,0,0,1,504990,315,-9000,-700,1200,0,0,0', 'OK'],
    'AT+CGMI': ['Spreadtrum', 'OK'],
    'AT+CGMM': ['V1.0', 'OK'],
    'AT+CGMR': ['FW-1', 'OK'],
    'AT+CGSN': ['867530012345678', 'OK'],
    'AT+CIMI': ['286016012345678', 'OK'],
    'AT+CCID': ['+CCID: 8990011234567890123', 'OK'],
    'AT+CNUM': ['+CNUM: "","+905551234567",145', 'OK'],
}


def modem_answer(table, cmd):
    """The lines for CMD; a concatenation (AT+A?;+B?) answers each part's lines and one final OK."""
    if cmd in table:
        v = table[cmd]
        return v(cmd) if callable(v) else list(v)
    if ';' in cmd and cmd.startswith('AT') and '\r' not in cmd:
        out = []
        for part in cmd[2:].split(';'):
            lines = modem_answer(table, 'AT' + part)
            if lines is None or not lines or lines[-1] != 'OK':
                return ['ERROR']
            out += lines[:-1]
        return out + ['OK']
    return table.get('*', ['OK'])


class FakeBridge:
    """mu300-atd-io's side of the socket."""

    def __init__(self, path, modem):
        self.path = str(path)
        self.modem = modem
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(4)
        self.conn = None
        self.records = []
        self.tx = []
        self.lock = threading.Lock()
        self.stop = False
        self.connections = 0
        self.state_delay = 0.0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, rec):
        with self.lock:
            c = self.conn
        if c:
            try:
                c.sendall((rec + '\n').encode())
            except OSError:
                pass

    def rx(self, name, line):
        self.send(f'RX {name} {line.encode("latin-1").hex().upper()}')

    def req(self, name, line):
        self.send(f'REQ {name} {line.encode("latin-1").hex().upper()}')

    def run(self):
        self.srv.settimeout(0.2)
        while not self.stop:
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                old, self.conn = self.conn, c
                self.connections += 1
            if old:
                old.close()
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def serve(self, c):
        self.send(f'HELLO 1 {os.getpid()}')
        buf = b''
        c.settimeout(0.2)
        while not self.stop:
            try:
                d = c.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not d:
                return
            buf += d
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                self.handle(line.decode())

    def handle(self, rec):
        parts = rec.split(' ', 2)
        with self.lock:
            self.records.append(rec)
        kind = parts[0]
        if kind in ('OPEN', 'FIFO'):
            name, rest = parts[1], parts[2]
            dev = rest.split(' ')[0]
            if self.state_delay:
                time.sleep(self.state_delay)
            self.send(f'STATE {name} {dev} open')
        elif kind == 'TX':
            name, data = parts[1], bytes.fromhex(parts[2]).decode('latin-1')
            with self.lock:
                self.tx.append((name, data))
            cmd = data[:-1] if data.endswith('\r') else data
            answer = self.modem(name, cmd)
            if answer:
                def later(lines=answer, name=name):
                    for l in lines:
                        if isinstance(l, float):
                            time.sleep(l)
                        else:
                            self.rx(name, l)
                threading.Thread(target=later, daemon=True).start()
        elif kind == 'REOPEN':
            name = parts[1]
            self.send(f'STATE {name} /dev/{name} pending')
            self.send(f'STATE {name} /dev/{name} open')

    def close(self):
        self.stop = True
        try:
            self.srv.close()
        except OSError:
            pass
        with self.lock:
            if self.conn:
                self.conn.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-modemd-'))
        self.run_root = self.tmp / 'run'
        self.run_root.mkdir()
        self.table = dict(DEFAULT_MODEM)
        self.bridge = FakeBridge(self.tmp / 'io.sock', lambda name, cmd: modem_answer(self.table, cmd))
        self.procs = []
        self.spooled = self.tmp / 'spooled'
        spool = self.tmp / 'spool'
        spool.write_text(f'#!/bin/sh\ncat >> "{self.spooled}"\n')
        spool.chmod(0o755)
        config = self.tmp / 'config'
        config.mkdir()
        (config/'unisoc_modem').write_text("config main 'main'\n option sim_slots '1'\n")
        self.env = dict(os.environ, MU300_CMT_SPOOL_BIN=str(spool), MU300_MODEMD_CONFIG_DIR=str(config),
                        MU300_REGISTRATION_DIR=str(self.run_root / 'mu300-registration'),
                        MU300_MODEMD_RUN=str(self.run_root), MU300_DASH_DIR=str(self.tmp / 'dash'),
                        MU300_DASH_POOL_DIR=str(self.tmp / 'pool'), MU300_DASH_IDENT_DIR=str(self.tmp / 'ident'))
        self.ubus_sock = None

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
            if os.environ.get('MU300_TEST_VERBOSE'):
                print('\n--- modemd stderr:\n' + (p.stderr.read() if p.stderr else ''))
            if p.stdout: p.stdout.close()
            if p.stderr: p.stderr.close()
        self.bridge.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, *brokers, **env):
        e = dict(self.env)
        e.update({k: str(v) for k, v in env.items()})
        if self.ubus_sock:
            e['MU300_UBUS_SOCKET'] = str(self.ubus_sock)
        args = list(brokers) or [f'name=atd,dev=/dev/stty_nr1,dir={self.run_root}/mu300-at,urc=0']
        # the tree's modules, the interpreter's own ubus/uloop/socket (no stand-ins: the daemon publishes on ubus)
        p = subprocess.Popen(ucode() + ucode_paths()[2:] + [str(MODEMD), '-s', str(self.tmp / 'io.sock')] + args,
                             env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def ready(self, chan='stty_nr1'):
        """The daemon took its channels and finished the first drain (it then serves)."""
        self.assertTrue(wait_for(lambda: any(r.startswith(f'OPEN {chan} ') for r in self.bridge.records)),
                        self.bridge.records)
        time.sleep(1.3)   # the slow first drain: quiet for 1 s

    def ask(self, line, chan='stty_nr1'):
        self.bridge.req(chan, line)

    def answer(self, path, timeout=8):
        p = Path(path)
        self.assertTrue(wait_for(lambda: p.exists(), timeout), f'no answer file {p}')
        return p.read_text()

    def at_dir(self):
        return self.run_root / 'mu300-at'


@unittest.skipUnless(ucode() and has_modules(ucode()), 'a ucode with uloop, socket and ubus is needed (MU300_TEST_UCODE)')
class Modemd(Base):
    """The broker: what mu300-atd did."""

    def test_hello_opens_the_channels_and_the_fifo(self):
        self.start()
        self.ready()
        recs = self.bridge.records
        self.assertIn(f'FIFO stty_nr1 {self.at_dir()}/cmd', recs)
        self.assertIn('OPEN stty_nr1 /dev/stty_nr1', recs)
        self.assertIn('OPEN stty_nr0 /dev/stty_nr0', recs)
        self.assertEqual((self.at_dir() / 'owner' / 'pid').read_text().strip(), str(os.getpid()))
        self.assertEqual(oct((self.at_dir() / 'urc').stat().st_mode & 0o777), '0o700')

    def test_a_request_is_answered_in_its_file(self):
        self.start()
        self.ready()
        ans = self.at_dir() / 'answer.123'
        self.ask(f'2 {ans} AT+CSQ')
        self.assertEqual(self.answer(ans), '+CSQ: 20,99\nOK\n')
        self.assertEqual(oct(ans.stat().st_mode & 0o777), '0o600')
        self.assertIn(('stty_nr1', 'AT+CSQ\r'), self.bridge.tx)

    def test_one_command_at_a_time_in_order(self):
        self.table['AT+SLOW'] = [0.4, 'OK']
        self.start()
        self.ready()
        a1, a2 = self.at_dir() / 'answer.1', self.at_dir() / 'answer.2'
        self.ask(f'2 {a1} AT+SLOW')
        self.ask(f'2 {a2} AT+CSQ')
        self.assertEqual(self.answer(a1), 'OK\n')
        self.assertEqual(self.answer(a2), '+CSQ: 20,99\nOK\n')
        cmds = [d for c, d in self.bridge.tx]
        self.assertEqual(cmds.index('AT+SLOW\r') + 1, cmds.index('AT+CSQ\r'))

    def test_clean_request_uses_50ms_quiet_window(self):
        self.start()
        self.ready()
        observed = []
        def reply(cmd):
            observed.append(time.monotonic())
            return ['OK']
        self.table['AT'] = reply
        for i in range(4):
            ans = self.at_dir() / f'answer.{800+i}'
            before = time.monotonic()
            self.ask(f'2 {ans} AT')
            self.assertEqual(self.answer(ans), 'OK\n')
            # Measure dispatch, not the test client's answer-file polling.
            delta = observed[-1] - before
            self.assertGreaterEqual(delta, 0.045)
            self.assertLess(delta, 0.095, f'clean dispatch delayed {delta:.3f}s')

    def test_no_answer_is_an_empty_answer_after_its_time(self):
        self.table['AT+DEAF'] = []
        self.start()
        self.ready()
        ans = self.at_dir() / 'answer.7'
        t0 = time.time()
        self.ask(f'1 {ans} AT+DEAF')
        self.assertEqual(self.answer(ans), '\n')
        self.assertGreaterEqual(time.time() - t0, 1.9)

    def test_requests_that_are_not_one_command_are_refused_or_dropped(self):
        self.start()
        self.ready()
        refused = self.at_dir() / 'answer.9'
        self.ask(f'2 {refused} AT+CSQ\rAT+CFUN=0')
        self.assertEqual(self.answer(refused), 'mu300-atd: refused\n')
        for bad in (f'120 {self.at_dir()}/answer.10 AT', f'2 /tmp/elsewhere AT', f'2 {self.at_dir()}/answer.x AT',
                    f'2 {self.at_dir()}/answer.11', 'garbage'):
            self.ask(bad)
        time.sleep(1.5)
        self.assertFalse((self.at_dir() / 'answer.10').exists())
        self.assertFalse((self.at_dir() / 'answer.11').exists())
        self.assertFalse(any('CFUN=0' in d for _, d in self.bridge.tx))

    def test_a_pdu_send_goes_out_as_one_write(self):
        self.table['AT+CMGS=12\r0011000B811234\x1a'] = ['+CMGS: 5', 'OK']
        self.start()
        self.ready()
        ans = self.at_dir() / 'answer.12'
        self.ask(f'9 {ans} AT+CMGS=12\r0011000B811234\x1a')
        self.assertEqual(self.answer(ans), '+CMGS: 5\nOK\n')
        self.assertIn(('stty_nr1', 'AT+CMGS=12\r0011000B811234\x1a\r'), self.bridge.tx)

    def test_lines_between_commands_go_to_the_channel_log_but_not_the_echo(self):
        self.start()
        self.ready()
        self.bridge.rx('stty_nr1', '+CREG: 1')
        self.bridge.rx('stty_nr1', 'AT+CPIN="1234"')
        log = self.at_dir() / 'urc' / 'stty_nr1.log'
        self.assertTrue(wait_for(lambda: log.exists() and '+CREG: 1' in log.read_text()))
        self.assertNotIn('1234', log.read_text())

    def test_urc_lines_are_logged_capped_and_announced(self):
        self.start(MU300_AT_URC_MAX=200)
        self.ready()
        log = self.at_dir() / 'urc' / 'stty_nr0.log'
        self.bridge.rx('stty_nr0', '+CEREG: 1,\"0001\",\"1234\",7')
        cache = self.run_root / 'mu300-registration' / 'slot0.json'
        self.assertTrue(wait_for(cache.exists))
        self.assertEqual(json.loads(cache.read_text()), {'slot': 0, 'stat': 1, 'rat': 7})
        for i in range(20):
            self.bridge.rx('stty_nr0', f'+SIND: {i:04d} padding padding')
        self.assertTrue(wait_for(lambda: '0019' in log.read_text()))
        self.assertLessEqual(len(log.read_text()), 200)

    def test_cmt_frames_go_to_the_spooler_never_to_a_log_or_a_reply(self):
        self.table['AT+CMGL=4'] = [0.1, '+CMGL: 1,0,,23', 0.3, 'OK']
        self.start()
        self.ready()
        self.bridge.rx('stty_nr0', '+CMT: ,23')
        self.bridge.rx('stty_nr0', '')
        self.bridge.rx('stty_nr0', '0791448720003023')
        self.assertTrue(wait_for(lambda: self.spooled.exists() and '0791448720003023' in self.spooled.read_text()))
        self.assertEqual(self.spooled.read_text(), '+CMT: ,23\n0791448720003023\n')
        log = (self.at_dir() / 'urc' / 'stty_nr0.log').read_text()
        self.assertIn('+CMT (PDU spooled)', log)
        self.assertNotIn('0791', log)
        # inside a reply on the command channel: spooled, and kept out of the reply
        ans = self.at_dir() / 'answer.13'
        self.ask(f'3 {ans} AT+CMGL=4')
        time.sleep(0.2)
        self.bridge.rx('stty_nr1', '+CMT: ,24')
        self.bridge.rx('stty_nr1', '0791448720003024')
        self.assertEqual(self.answer(ans), '+CMGL: 1,0,,23\nOK\n')
        self.assertTrue(wait_for(lambda: '0791448720003024' in self.spooled.read_text()))

    def test_sms_pools_follow_physical_slot_not_channel_number_division(self):
        pool = self.tmp / "custom sms pool"
        spool = self.tmp / 'spool'
        spool.write_text(f'#!/bin/sh\nprintf "%s\\n" "$MU300_SMS_POOL" >> "{self.spooled}"\ncat >/dev/null\n')
        self.start(*[f'name=atd{n},dev=/dev/stty_nr{n},dir={self.run_root}/at{n}' for n in (1,4,6,7)],
                   MU300_SMS_POOL=pool)
        self.ready()
        for n in (1,4,6,7):
            self.bridge.rx(f'stty_nr{n}', '+CMT: ,23')
            self.bridge.rx(f'stty_nr{n}', '0791448720003023')
        self.assertTrue(wait_for(lambda: self.spooled.exists() and len(self.spooled.read_text().splitlines()) == 4))
        self.assertCountEqual(self.spooled.read_text().splitlines(), [str(pool)]*3 + [str(pool / 'sim1')])

    def test_sim_cache_survives_daemon_restart_with_same_bridge(self):
        p = self.start()
        self.ready()
        marker = self.at_dir() / 'urc' / 'stty_nr0.ready'
        self.assertEqual(marker.read_text().strip(), str(os.getpid()))
        cache = self.run_root / 'mu300-registration' / 'slot0.json'
        self.bridge.rx('stty_nr0', '+CEREG: 1')
        self.assertTrue(wait_for(cache.exists))
        p.terminate()
        p.wait(5)
        self.start()
        self.ready()
        self.assertEqual(json.loads(cache.read_text())['stat'], 1)
        ans = self.at_dir() / 'answer.301'
        self.ask(f'2 {ans} AT+SPACTCARD=0;+CPIN?')
        self.answer(ans)

    def test_scoped_sim_replies_update_existing_recovery_cache(self):
        self.table['AT+SPACTCARD=1;+CPIN?'] = ['+CPIN: READY', 'OK']
        self.start()
        self.ready()
        ans = self.at_dir() / 'answer.302'
        self.ask(f'2 {ans} AT+SPACTCARD=1;+CPIN?')
        self.answer(ans)
        cache = self.run_root / 'mu300-registration' / 'sim1.json'
        self.assertTrue(wait_for(cache.exists))
        self.assertEqual(json.loads(cache.read_text()), {'slot': 1, 'state': 'ready'})

    def test_the_reopen_rule(self):
        self.table['AT+DEAF'] = []
        self.start(MU300_AT_URC_QUIET=1)
        self.ready()
        log = self.at_dir() / 'urc' / 'stty_nr0.log'
        old = time.time() - 600
        os.utime(log, (old, old))
        for i in range(5):
            ans = self.at_dir() / f'answer.{100 + i}'
            self.ask(f'1 {ans} AT+DEAF')
            self.answer(ans)
        self.assertTrue(wait_for(lambda: 'REOPEN stty_nr1' in self.bridge.records), self.bridge.records)
        # never where the layout says no (the dual-SIM brokers)
        self.bridge.records.clear()

    def test_no_reopen_when_the_modem_still_talks_or_reopen_is_off(self):
        self.table['AT+DEAF'] = []
        self.start(f'name=atd,dev=/dev/stty_nr1,dir={self.run_root}/mu300-at,urc=0,reopen=0', MU300_AT_URC_QUIET=1)
        self.ready()
        log = self.at_dir() / 'urc' / 'stty_nr0.log'
        os.utime(log, (time.time() - 600, time.time() - 600))
        for i in range(6):
            ans = self.at_dir() / f'answer.{200 + i}'
            self.ask(f'1 {ans} AT+DEAF')
            self.answer(ans)
        self.assertNotIn('REOPEN stty_nr1', self.bridge.records)

    def test_a_new_daemon_takes_over_the_bridge(self):
        p = self.start()
        self.ready()
        p.terminate()
        p.wait(5)
        self.assertEqual((self.at_dir() / 'owner' / 'pid').read_text().strip(), str(os.getpid()))
        self.bridge.records.clear()
        self.start()
        self.ready()
        ans = self.at_dir() / 'answer.300'
        self.ask(f'2 {ans} AT+CSQ')
        self.assertEqual(self.answer(ans), '+CSQ: 20,99\nOK\n')
        self.assertGreaterEqual(self.bridge.connections, 2)


@unittest.skipUnless(ucode() and has_modules(ucode()) and os.access(UBUSD, os.X_OK) and os.access(UBUS, os.X_OK),
                     'ubusd and the ubus CLI are needed (MU300_TEST_UBUSD, MU300_TEST_UBUS)')
class ModemdUbus(Base):
    maxDiff = None
    """The ubus object, the events and the panel's collection, with a private ubusd."""

    def setUp(self):
        super().setUp()
        self.ubus_sock = self.tmp / 'ubus.sock'
        self.ubusd = subprocess.Popen([UBUSD, '-s', str(self.ubus_sock)], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self.assertTrue(wait_for(lambda: self.ubus_sock.exists()))

    def tearDown(self):
        super().tearDown()
        self.ubusd.terminate()
        self.ubusd.wait(5)

    def ubus(self, *args, timeout=15):
        r = subprocess.run([UBUS, '-s', str(self.ubus_sock), '-t', '10'] + list(args), capture_output=True, text=True,
                           timeout=timeout)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def wait_object(self):
        self.assertTrue(wait_for(lambda: subprocess.run([UBUS, '-s', str(self.ubus_sock), 'list', 'mu300.modem'],
                                                        capture_output=True).returncode == 0))

    def test_at_status_and_events(self):
        self.start()
        self.ready()
        self.wait_object()
        r = self.ubus('call', 'mu300.modem', 'at', json.dumps({'cmd': 'AT+CSQ', 'timeout': 2}))
        self.assertEqual(r, {'ok': 1, 'reply': '+CSQ: 20,99\nOK'})
        self.assertEqual(self.ubus('call', 'mu300.modem', 'at', json.dumps({'cmd': 'AT+CSQ;+X\rY'}))['ok'], 0)
        st = self.ubus('call', 'mu300.modem', 'status')
        self.assertEqual(st['brokers'][0]['dir'], str(self.at_dir()))
        self.assertEqual(st['urc'], {'stty_nr0': 'open'})
        listen = subprocess.Popen([UBUS, '-s', str(self.ubus_sock), 'listen', 'mu300.modem.urc'], stdout=subprocess.PIPE,
                                  text=True)
        try:
            time.sleep(0.5)
            self.bridge.rx('stty_nr0', '+CEREG: 1,"1A2B","01234567",7')
            # a direct delivery: mu300-smsd hears that it was spooled, in the log's fixed words only
            self.bridge.rx('stty_nr0', '+CMT: ,23')
            self.bridge.rx('stty_nr0', '0791448720003023')
            self.assertTrue(wait_for(lambda: self.spooled.exists()))
            # between commands on the command channel: a new message's index is announced, a late reply is not
            self.bridge.rx('stty_nr1', '+CMTI: "SM",3')
            self.bridge.rx('stty_nr1', '+CPIN: READY')
            time.sleep(0.5)
        finally:
            listen.terminate()
        out = listen.communicate(timeout=5)[0]
        events = [json.loads(l)['mu300.modem.urc'] for l in out.splitlines() if l.strip()]
        self.assertIn({'channel': 'stty_nr0', 'line': '+CEREG: 1,"1A2B","01234567",7'}, events)
        self.assertIn({'channel': 'stty_nr0', 'line': '+CMT (PDU spooled)'}, events)
        self.assertNotIn('0791', out)
        self.assertNotIn('+CMT: ,23', out)
        self.assertIn({'channel': 'stty_nr1', 'line': '+CMTI: "SM",3'}, events)
        self.assertNotIn('+CPIN', out)

    def collector_program(self, mode, tag):
        """The collector program on the same modem answers (MU300_AT, a stand-in adapter that asks the table)."""
        table = json.dumps(self.table)
        adapter = self.tmp / f'fake-at-{tag}'
        adapter.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                           f'table = json.loads({table!r})\n'
                           'args = sys.argv[1:]\n'
                           'if args[:1] == ["-t"]: args = args[2:]\n'
                           'cmd = args[0]\n'
                           'def ans(c):\n'
                           '    if c in table: return [l for l in table[c] if isinstance(l, str)]\n'
                           '    if ";" in c and "\\r" not in c:\n'
                           '        out = []\n'
                           '        for p in c[2:].split(";"):\n'
                           '            l = ans("AT" + p)\n'
                           '            if not l or l[-1] != "OK": return ["ERROR"]\n'
                           '            out += l[:-1]\n'
                           '        return out + ["OK"]\n'
                           '    return ["OK"]\n'
                           'print("\\n".join(ans(cmd)))\n')
        adapter.chmod(0o755)
        d = self.tmp / f'prog-{tag}'
        env = dict(self.env, MU300_AT=str(adapter), MU300_DASH_DIR=str(d), MU300_DASH_POOL_DIR=str(self.tmp / f'ppool-{tag}'),
                   MU300_DASH_IDENT_DIR=str(self.tmp / f'pident-{tag}'))
        from helpers import ucode_argv, is_ucode
        command = ucode_argv(CELL) if is_ucode(CELL) else ['/bin/sh', str(CELL)]
        r = subprocess.run(command + [mode], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return d

    @staticmethod
    def without_ts(obj):
        if isinstance(obj, dict):
            return {k: ModemdUbus.without_ts(v) for k, v in obj.items() if k != 'ts'}
        if isinstance(obj, list):
            return [ModemdUbus.without_ts(v) for v in obj]
        return obj

    def test_the_collection_writes_what_the_collector_program_writes(self):
        self.start(f'name=atd,dev=/dev/stty_nr1,dir={self.run_root}/mu300-at,urc=0')
        self.ready()
        self.wait_object()
        dash = self.tmp / 'dash'
        for mode in ('full', 'fast'):
            with self.subTest(mode=mode):
                target = dash / ('cell.json' if mode == 'full' else 'sig.json')
                before = target.stat().st_mtime_ns if target.exists() else 0
                r = self.ubus('call', 'mu300.modem', 'cell', json.dumps({'mode': mode}))
                self.assertEqual(r, {'started': 1})
                self.assertTrue(wait_for(lambda: target.exists() and target.stat().st_mtime_ns != before
                                         and not (dash / 'collect.lck').exists(), 15))
                prog = self.collector_program(mode, mode)
                for name in (('cell.json', 'sig.json') if mode == 'full' else ('sig.json',)):
                    mine = json.loads((dash / name).read_text())
                    theirs = json.loads((prog / name).read_text())
                    self.assertEqual(self.without_ts(mine), self.without_ts(theirs), name)
        # a second request while one runs: not started
        self.table['AT+CFUN?'] = [1.0, '+CFUN: 1', 'OK']
        self.assertEqual(self.ubus('call', 'mu300.modem', 'cell', json.dumps({'mode': 'fast'})), {'started': 1})
        self.assertEqual(self.ubus('call', 'mu300.modem', 'cell', json.dumps({'mode': 'fast'}))['started'], 0)

    def test_full_round_publishes_serving_and_neighbors_while_identity_is_pending(self):
        self.table['AT+CIMI']=[0.8,'286016012345678','OK']
        self.start();self.ready();self.wait_object()
        self.ubus('call','mu300.modem','collect','{"slot":"0","mode":"full"}')
        target=self.tmp/'dash'/'cell.json'
        def partial():
            if not target.exists(): return False
            data=json.loads(target.read_text())
            return data['partial']==1 and data['neigh_pending']==0
        self.assertTrue(wait_for(partial,10))
        sig=json.loads((target.parent/'sig.json').read_text())
        self.assertEqual(sig['sig_src'],'nr')
        self.assertTrue(wait_for(lambda: not (target.parent/'collect.lck').exists(),10))
        self.assertEqual(json.loads(target.read_text())['partial'],0)

    def test_compatibility_collector_does_not_steal_resident_collector_lock(self):
        self.table['AT+CIMI']=[2.0,'286016012345678','OK']
        self.start();self.ready();self.wait_object()
        self.ubus('call','mu300.modem','collect','{"slot":"0","mode":"full"}')
        lock=self.tmp/'dash'/'collect.lck'/'pid'
        self.assertTrue(wait_for(lock.exists))
        owner=lock.read_text()
        p=subprocess.run(['/bin/sh',str(CELL),'fast'],env=dict(self.env,MU300_AT=str(self.tmp/'never-run')),capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(lock.read_text(),owner)

    def test_unanswered_core_publishes_error_without_identity_or_number_reads(self):
        for cmd in ['AT+CFUN?','AT+CEREG?','AT+C5GREG?','AT+CESQ']: self.table[cmd]=['ERROR']
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        self.ubus('call','mu300.modem','collect','{"slot":"0","mode":"full"}')
        target=self.tmp/'dash'/'cell.json'
        self.assertTrue(wait_for(lambda: target.exists() and not (target.parent/'collect.lck').exists(),10))
        self.assertIn('error',json.loads(target.read_text()))
        self.assertFalse(any('CGSN' in cmd or 'CNUM' in cmd for _,cmd in self.bridge.tx))

    def test_scoped_collect_writes_only_the_requested_sim_cache_and_channel(self):
        config=self.tmp/'config'/'unisoc_modem'
        config.write_text("config main 'main'\n option sim_slots '2'\n")
        self.start(f'name=atd,dev=/dev/stty_nr1,dir={self.run_root}/mu300-at,urc=0',
                   f'name=atd4,dev=/dev/stty_nr4,dir={self.run_root}/mu300-at4,urc=3')
        self.ready();self.wait_object();self.bridge.tx.clear()
        self.assertEqual(self.ubus('call','mu300.modem','collect','{"slot":"1","mode":"full"}'),{'started':1})
        target=self.tmp/'dash'/'sim1'/'cell.json'
        self.assertTrue(wait_for(lambda: target.exists() and not (target.parent/'collect.lck').exists(),15))
        self.assertTrue(self.bridge.tx)
        self.assertTrue(all(c=='stty_nr4' and d.startswith('AT+SPACTCARD=1;') for c,d in self.bridge.tx))
        self.assertFalse((self.tmp/'dash'/'cell.json').exists())
        self.assertTrue((self.tmp/'ident'/'sim1'/'identity'/'sim.json').exists())
        self.assertEqual(self.ubus('call','mu300.modem','collect','{"slot":"2","mode":"fast"}')['started'],0)

    def test_unscoped_collect_follows_shared_data_selection_not_old_u30_pin(self):
        config=self.tmp/'config'/'unisoc_modem'
        config.write_text("config main 'main'\n option sim_slots '2'\n option data_sim '1'\n")
        old=self.run_root/'mu300'/'sim-slot';old.parent.mkdir(parents=True);old.write_text('0\n')
        self.start(f'name=atd,dev=/dev/stty_nr1,dir={self.run_root}/mu300-at,urc=0',
                   f'name=atd4,dev=/dev/stty_nr4,dir={self.run_root}/mu300-at4,urc=3')
        self.ready();self.wait_object();self.bridge.tx.clear()
        self.assertEqual(self.ubus('call','mu300.modem','cell','{"mode":"fast"}'),{'started':1})
        target=self.tmp/'dash'/'sig.json'
        self.assertTrue(wait_for(lambda: target.exists() and not (target.parent/'collect.lck').exists(),15))
        self.assertTrue(self.bridge.tx)
        self.assertTrue(all(c=='stty_nr4' and d.startswith('AT+SPACTCARD=1;') for c,d in self.bridge.tx))

    def test_collect_rejects_unavailable_slot_custom_backend_and_switching(self):
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        result=self.ubus('call','mu300.modem','collect','{"slot":"1","mode":"fast"}')
        self.assertEqual(result['started'],0);self.assertIn('not available',result['error'])
        (self.tmp/'config'/'unisoc_modem').write_text("config main 'main'\n option at_backend 'custom'\n")
        result=self.ubus('call','mu300.modem','collect','{"slot":"0","mode":"fast"}')
        self.assertEqual(result['started'],0);self.assertIn('unsupported',result['error'])
        (self.run_root/'unisoc-data-sim-switch').mkdir()
        result=self.ubus('call','mu300.modem','collect','{"slot":"0","mode":"fast"}')
        self.assertEqual(result['busy'],1)
        self.assertEqual(self.bridge.tx,[])

    def test_number_refresh_is_live_and_static_identity_keeps_its_boot_cache(self):
        self.start()
        self.ready()
        self.wait_object()
        target = self.tmp / 'dash' / 'cell.json'
        for number in ('+905551234567', '+905559876543'):
            before = target.stat().st_mtime_ns if target.exists() else 0
            self.table['AT+CNUM'] = [f'+CNUM: "","{number}",145', 'OK']
            self.assertEqual(self.ubus('call','mu300.modem','cell','{"mode":"full"}'), {'started':1})
            self.assertTrue(wait_for(lambda: target.exists() and target.stat().st_mtime_ns != before
                                     and not (target.parent/'collect.lck').exists(), 15))
            self.assertEqual(json.loads(target.read_text())['ident']['msisdn'], number)
        cmds = [d for _,d in self.bridge.tx]
        self.assertEqual(cmds.count('AT+SPACTCARD=0;+CNUM\r'),2)
        self.assertEqual(cmds.count('AT+SPACTCARD=0;+CGSN\r'),1)
        self.assertNotIn('msisdn',json.loads((self.tmp/'ident'/'sim.json').read_text()))
        self.assertFalse(any('COPS=3' in c for c in cmds))

    def test_card_mux_change_blocks_all_stale_publications(self):
        epoch = self.tmp/'epoch'
        epoch.write_text('1')
        self.table['AT+CFUN?'] = [0.5, '+CFUN: 1', 'OK']
        self.start(MU300_CARD_EPOCH_PATH=epoch)
        self.ready()
        self.wait_object()
        self.bridge.tx.clear()
        self.assertEqual(self.ubus('call','mu300.modem','cell','{"mode":"full"}'), {'started':1})
        self.assertTrue(wait_for(lambda: len(self.bridge.tx)>0))
        epoch.write_text('2')
        dash=self.tmp/'dash'
        self.assertTrue(wait_for(lambda: not (dash/'collect.lck').exists(), 10))
        self.assertFalse((dash/'cell.json').exists())
        self.assertFalse((dash/'sig.json').exists())
        self.assertFalse((self.tmp/'ident'/'sim.json').exists())

    def test_the_collection_on_sa_asks_no_lte_reads(self):
        self.table['AT+CEREG?'] = ['+CEREG: 2,1,"141080","0E7DB05B",11', 'OK']
        self.start()
        self.ready()
        self.wait_object()
        self.bridge.tx.clear()
        self.ubus('call', 'mu300.modem', 'cell', json.dumps({'mode': 'fast'}))
        sig = self.tmp / 'dash' / 'sig.json'
        self.assertTrue(wait_for(lambda: sig.exists() and not (self.tmp / 'dash' / 'collect.lck').exists(), 15))
        cmds = [d for _, d in self.bridge.tx]
        self.assertEqual(cmds, ['AT+SPACTCARD=0;+CFUN?;+CEREG?;+C5GREG?;+CESQ\r', 'AT+SPACTCARD=0;+SPENGMD=0,14,1\r'])
        self.assertEqual(json.loads(sig.read_text())['sig_src'], 'nr')

    def test_live_signal_is_fresh_and_never_overwrites_background_cache(self):
        self.start();self.ready();self.wait_object()
        dash=self.tmp/'dash';dash.mkdir(exist_ok=True)
        (dash/'sig.json').write_text('{"ts":1,"sentinel":"cached"}')
        result=self.ubus('call','mu300.modem','signal','{"slot":"0"}')
        self.assertEqual(result['sig_src'],'nr');self.assertNotIn('sentinel',result)
        self.assertEqual(json.loads((dash/'sig.json').read_text()),{'ts':1,'sentinel':'cached'})
        self.assertFalse(list(dash.glob('live.*')))
        self.assertFalse((dash/'collect.lck').exists())

    def test_live_signal_switch_gate_and_invalid_slot_do_not_send_at(self):
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        (self.run_root/'unisoc-data-sim-switch').mkdir()
        self.assertEqual(self.ubus('call','mu300.modem','signal','{"slot":"0"}')['busy'],1)
        self.assertIn('error',self.ubus('call','mu300.modem','signal','{"slot":"1"}'))
        self.assertEqual(self.bridge.tx,[])

    def test_live_signal_accepts_http_session_metadata_but_not_business_extras(self):
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        args={'slot':'0','ubus_rpc_session':'a'*32}
        result=self.ubus('call','mu300.modem','signal',json.dumps(args))
        self.assertEqual(result['sig_src'],'nr')
        self.assertTrue(result['ts'])
        self.assertNotIn('ubus_rpc_session',result)
        self.bridge.tx.clear()
        args['slot']='2'
        self.assertEqual(self.ubus('call','mu300.modem','signal',json.dumps(args))['error'],'invalid SIM slot')
        args.update(slot='0',unexpected='not-allowed')
        rejected=subprocess.run([UBUS,'-s',str(self.ubus_sock),'call','mu300.modem','signal',json.dumps(args)],capture_output=True,text=True)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(self.bridge.tx,[])

    def test_live_signal_mux_change_returns_error_and_cleans_locks(self):
        epoch=self.tmp/'epoch';epoch.write_text('1')
        self.table['AT+CFUN?']=[0.5,'+CFUN: 1','OK']
        self.start(MU300_CARD_EPOCH_PATH=epoch);self.ready();self.wait_object();self.bridge.tx.clear()
        proc=subprocess.Popen([UBUS,'-s',str(self.ubus_sock),'-t','20','call','mu300.modem','signal','{"slot":"0"}'],stdout=subprocess.PIPE,text=True)
        try:
            self.assertTrue(wait_for(lambda:len(self.bridge.tx)>0));epoch.write_text('2')
            out=proc.communicate(timeout=15)[0]
            self.assertIn('SIM changed',json.loads(out)['error'])
            self.assertFalse(list((self.tmp/'dash').glob('live.*')))
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(5)


    def test_live_signal_deadline_keeps_pending_channel_owned(self):
        from unittest.mock import patch
        # Exercise the production deadline callback in the real event loop,
        # compressing only the 12-second constant to keep the test deterministic.
        source=MODEMD.read_text()
        self.assertEqual(source.count('uloop.timer(12000,'),1)
        script=self.tmp/'mu300-modemd'
        script.write_text(source.replace('uloop.timer(12000,','uloop.timer(200,',1))
        self.table['AT+CFUN?']=[0.7,'+CFUN: 1','OK']
        with patch('test_modemd.MODEMD',script):self.start()
        self.ready();self.wait_object();self.bridge.tx.clear()
        result=self.ubus('call','mu300.modem','signal','{"slot":"0"}')
        self.assertEqual(result['error'],'live signal read timed out')
        self.assertTrue(list((self.tmp/'dash').glob('live.*/collect.lck')),'an in-flight AT read retains its pool')
        self.assertTrue(wait_for(lambda:not list((self.tmp/'dash').glob('live.*')),4))
        self.assertEqual(len(self.bridge.tx),1,'no later AT steps after the deadline')

    def test_live_signal_shares_concurrent_same_sim_observation(self):
        self.table['AT+CFUN?']=[0.4,'+CFUN: 1','OK']
        self.start(*[f'name=atd{n},dev=/dev/stty_nr{n},dir={self.run_root}/mu300-at{n}' for n in (6,7)])
        self.ready('stty_nr6');self.ready('stty_nr7');self.wait_object()
        requests=[]
        try:
            for _ in range(3):
                requests.append(subprocess.Popen([UBUS,'-s',str(self.ubus_sock),'-t','20','call','mu300.modem','signal','{"slot":"0"}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            replies=[json.loads(p.communicate(timeout=20)[0]) for p in requests]
            self.assertEqual(sum('sig' in r for r in replies),3,replies)
            self.assertEqual(sum(r.get('busy')==1 for r in replies),0,replies)
            self.assertFalse(list((self.tmp/'dash').glob('live.*')))
        finally:
            for p in requests:
                if p.poll() is None:p.terminate();p.wait(5)


    def test_live_pool_respects_foreign_process_arguments_after_nul(self):
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        owner=subprocess.Popen(['python3','-c','import time;time.sleep(30)','/usr/libexec/unisoc-modem/cell'])
        try:
            for n in (6,7):
                path=self.tmp/'pool'/f'nr{n}';path.mkdir(parents=True)
                (path/'pid').write_text(str(owner.pid)+'\n')
            result=self.ubus('call','mu300.modem','signal','{"slot":"0"}')
            self.assertEqual(result.get('busy'),1,result)
            self.assertEqual(self.bridge.tx,[])
        finally:owner.terminate();owner.wait(5)

    def test_expired_native_client_lock_cannot_kill_its_owner(self):
        self.table['AT+SLOW']=[0.8,'OK']
        daemon=self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        request=subprocess.Popen([UBUS,'-s',str(self.ubus_sock),'-t','10','call','mu300.modem','at','{"cmd":"AT+SLOW","timeout":2}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertTrue(wait_for(lambda:any('AT+SLOW' in text for _,text in self.bridge.tx)))
            (self.at_dir()/'lock/until').write_text('0\n')
            result=self.ubus('call','mu300.modem','at','{"cmd":"AT+CSQ","timeout":2}')
            self.assertEqual(result,{'ok':0,'busy':1})
            out,err=request.communicate(timeout=5)
            self.assertEqual(request.returncode,0,err);self.assertEqual(json.loads(out)['ok'],1)
            self.assertIsNone(daemon.poll())
        finally:
            if request.poll() is None:request.terminate();request.wait(5)

    def test_expired_foreign_native_lock_is_not_treated_as_a_cli(self):
        self.start();self.ready();self.wait_object();self.bridge.tx.clear()
        owner=subprocess.Popen(['python3','-c','import time;time.sleep(30)','/opt/mu300/bin/mu300-modemd','dir=/run/mu300-at'])
        try:
            lock=self.at_dir()/'lock';lock.mkdir()
            (lock/'pid').write_text(str(owner.pid)+'\n');(lock/'until').write_text('0\n')
            result=self.ubus('call','mu300.modem','at','{"cmd":"AT+CSQ","timeout":2}')
            self.assertEqual(result,{'ok':0,'busy':1})
            self.assertIsNone(owner.poll());self.assertEqual(self.bridge.tx,[])
        finally:owner.terminate();owner.wait(5)


if __name__ == '__main__':
    unittest.main()
